#!/usr/bin/env bash
# Safe-mode + plugin select e2e (in-repo mini fixture).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
FIX="$ROOT/tests/fixtures/safe_e2e"
MODEL="$ROOT/tests/fixtures/reference_model"

BIN="${CARGO_TARGET_DIR:-$ROOT/target}/debug/testpilot"
# Always rebuild in CI; locally reuse when the linker succeeds.
if [[ "${CI:-}" == "true" ]] || [[ ! -x "$BIN" ]]; then
  (cd "$ROOT" && cargo build -p testpilot-cli --locked)
fi
export TESTPILOT_BIN="$BIN"

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON="$ROOT/.venv/bin/python"
elif [[ -n "${VIRTUAL_ENV:-}" && -x "${VIRTUAL_ENV}/bin/python" ]]; then
  PYTHON="${VIRTUAL_ENV}/bin/python"
else
  PYTHON="$(command -v python3 || command -v python)"
fi
"$PYTHON" -m pip install -q -e "$ROOT/python/pytest-testpilot"

build_indexed_repo() {
  local dest="$1"
  mkdir -p "$dest"
  rsync -a --exclude tests/test_regression.py "$FIX/" "$dest/"
  cd "$dest"
  git init -b main >/dev/null
  git config user.email "safe-e2e@example.com"
  git config user.name "Safe E2E"
  git add .
  git commit -q -m "baseline"
  "$BIN" init >/dev/null
  export COVERAGE_CORE=ctrace PYTHONPATH="$dest/src${PYTHONPATH:+:$PYTHONPATH}"
  # Clean tree required so the recorded run is dirty=0 (feeds base_run / select).
  git status --porcelain | grep -q . && {
    echo "safe_e2e: worktree not clean before pytest:" >&2
    git status --porcelain >&2
    exit 1
  }
  "$PYTHON" -m pytest --rootdir=. --testpilot -q >/dev/null
  "$BIN" collect git >/dev/null
  "$BIN" features dump --out .testpilot/pairs.parquet >/dev/null
  git rev-parse HEAD
}

echo "=== case 1: safe select includes injected regression (CLI) ==="
WORKDIR="$(mktemp -d)"
BASE_SHA="$(build_indexed_repo "$WORKDIR")"
cd "$WORKDIR"
# Inject regression: break add() and add failing test.
cat > src/calc.py <<'PY'
def add(a: int, b: int) -> int:
    return a + b + 1


def sub(a: int, b: int) -> int:
    return a - b


def mul(a: int, b: int) -> int:
    return a * b


def div(a: int, b: int) -> int:
    return a // b


def clamp(x: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, x))


def parity(n: int) -> int:
    return n % 2
PY
cat > tests/test_regression.py <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from calc import add
def test_regression_add():
    assert add(1, 1) == 2
PY
git add src/calc.py tests/test_regression.py
git commit -m "regression"

SEL="$("$BIN" select --base "$BASE_SHA" --head HEAD --model "$MODEL" \
  --budget-frac 0.2 --format json --safe-min-candidates 3)"
"$PYTHON" - <<'PY' "$SEL"
import json, sys
data = json.loads(sys.argv[1])
ids = {t["test_id"] for t in data["tests"]}
assert any("test_regression" in i for i in ids), ids
safe = data.get("safe") or {}
assert safe.get("enabled") is True, data
print("case1 ok", {"n_selected": len(ids), "safe": safe.get("enabled")})
PY

echo "=== case 2: shadow caught_fail == 1.0 ==="
# Full candidate budget so both new fails are in would-select; shadow still runs all.
OUT="$("$PYTHON" -m pytest --rootdir=. --testpilot --testpilot-select --testpilot-shadow \
  --testpilot-model="$MODEL" --testpilot-budget-frac=1.0 --testpilot-no-safe \
  --testpilot-base="$BASE_SHA" -q 2>&1 || true)"
echo "$OUT" | tail -20
echo "$OUT" | grep -q 'shadow_caught_fail=1.0000'

echo "=== case 3: --no-safe tiny budget (safe.enabled=false) ==="
NS="$("$BIN" select --base "$BASE_SHA" --head HEAD --model "$MODEL" \
  --no-safe --max-tests 1 --format json --safe-min-candidates 3)"
"$PYTHON" - <<'PY' "$NS"
import json, sys
data = json.loads(sys.argv[1])
safe = data.get("safe") or {}
assert safe.get("enabled") is False, data
print("case3 ok", {"n_selected": data.get("n_selected"), "safe.enabled": False})
PY

echo "=== case 4: cold cache fail-open (plugin_error / snapshot) ==="
COLD="$(mktemp -d)"
rsync -a "$FIX/" "$COLD/"
cd "$COLD"
git init -b main >/dev/null
git config user.email "cold@example.com"
git config user.name "Cold"
git add .
git commit -q -m "only"
COLD_OUT="$("$PYTHON" -m pytest --rootdir=. --testpilot --testpilot-select \
  --testpilot-model="$MODEL" --testpilot-budget-frac=0.5 -q 2>&1)" || true
echo "$COLD_OUT" | tail -15
echo "$COLD_OUT" | grep -qiE 'plugin_error|fail-open|feature snapshot|fallback=plugin_error'
# Session stays green when fail-open (pytest exits 0 unless tests fail).
RC=0
"$PYTHON" -m pytest --rootdir=. --testpilot --testpilot-select \
  --testpilot-model="$MODEL" --testpilot-budget-frac=0.5 -q >/dev/null 2>&1 || RC=$?
test "$RC" -eq 0

echo "safe_e2e ok"
