#!/usr/bin/env bash
# Week-6 bootstrap e2e (spec §7.2): full / resume / use_baseline select / fingerprint / schema.
# Budget: ≤2 min wall. No click/jinja/attrs download.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
FIX="$ROOT/tests/fixtures/bootstrap_e2e"
START_WALL=$(date +%s)

BIN="${CARGO_TARGET_DIR:-$ROOT/target}/debug/testpilot"
if [[ "${CI:-}" == "true" ]] || [[ ! -x "$BIN" ]]; then
  (cd "$ROOT" && cargo build -p testpilot-cli --locked)
fi
export TESTPILOT_BIN="$BIN"

if [[ -x "$ROOT/python/testpilot-train/.venv/bin/python" ]]; then
  PYTHON="$ROOT/python/testpilot-train/.venv/bin/python"
elif [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON="$ROOT/.venv/bin/python"
elif [[ -n "${VIRTUAL_ENV:-}" && -x "${VIRTUAL_ENV}/bin/python" ]]; then
  PYTHON="${VIRTUAL_ENV}/bin/python"
else
  PYTHON="$(command -v python3 || command -v python)"
fi

"$PYTHON" -m pip install -q -e "$ROOT/python/pytest-testpilot"
"$PYTHON" -m pip install -q -e "$ROOT/python/testpilot-train"
export PATH="$(dirname "$PYTHON"):$PATH"
export PYTHONPATH="$ROOT/python/testpilot-train/src${PYTHONPATH:+:$PYTHONPATH}"

seed_repo() {
  local dest="$1"
  local ncommits="${2:-6}"
  rm -rf "$dest"
  mkdir -p "$dest"
  # Prefer rsync; fall back to cp for minimal environments.
  if command -v rsync >/dev/null 2>&1; then
    rsync -a "$FIX/" "$dest/"
  else
    cp -R "$FIX/." "$dest/"
  fi
  cd "$dest"
  git init -b main >/dev/null
  git config user.email "bootstrap-e2e@example.com"
  git config user.name "Bootstrap E2E"
  git add .
  git commit -q -m "c0 init"
  # Real function-body edits so mutate can find binop sites (not comment-only).
  # Keep tests green: only change mul/clamp constants that tests still accept.
  local i
  for i in $(seq 1 $((ncommits - 1))); do
    cat > pkg/calc.py <<PY
def add(a: int, b: int) -> int:
    return a + b


def mul(a: int, b: int) -> int:
    return a * b


def clamp(x: int, lo: int = 0, hi: int = $((100 + i % 7)) ) -> int:
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x + 0
PY
    git add -A
    git commit -q -m "c$i edit"
  done
}

assert_report_schema() {
  local model_dir="$1"
  "$PYTHON" - <<PY
import json
from pathlib import Path
model = Path("$model_dir")
mj = json.loads((model / "model.json").read_text())
assert "ranker" in mj, mj.keys()
assert "bootstrap" in mj and "verdict" in mj["bootstrap"], mj.get("bootstrap")
assert mj["ranker"] in ("model", "cov_lines")
assert mj["bootstrap"]["verdict"] in ("use_model", "use_baseline")
br = model / "bootstrap_report.json"
assert br.is_file(), br
rep = json.loads(br.read_text())
for key in ("fingerprint", "steps", "ranker", "bootstrap"):
    assert key in rep, (key, sorted(rep))
print("report schema ok", mj["bootstrap"]["verdict"], mj["ranker"])
PY
}

echo "=== case 1: full bootstrap tiny N/K ==="
WORKDIR="$(mktemp -d)"
MODEL1="$WORKDIR/model"
seed_repo "$WORKDIR/repo" 6
cd "$WORKDIR/repo"
"$BIN" bootstrap --repo "$WORKDIR/repo" --out "$MODEL1" \
  --max-commits 2 --mutants-per-commit 1 --jobs 1 --seed 11 --force \
  --train-threads 1 2>&1 | tee "$WORKDIR/boot1.log"
test -f "$MODEL1/model.json"
rg -q "bootstrap complete" "$WORKDIR/boot1.log"
rg -q "gate: verdict=" "$WORKDIR/boot1.log"
assert_report_schema "$MODEL1"
# Select must load the artifact (cold or thin gate → usually cov_lines).
BASE=$(git rev-parse HEAD~1)
HEAD=$(git rev-parse HEAD)
SEL=$("$BIN" select --repo "$WORKDIR/repo" --db "$WORKDIR/repo/.testpilot/db.sqlite" \
  --base "$BASE" --head "$HEAD" --model "$MODEL1" --max-tests 3 --format json)
"$PYTHON" - <<PY
import json
data = json.loads('''$SEL''')
assert data.get("ranker") in ("model", "cov_lines"), data
assert data.get("n_selected", 0) >= 1, data
print("case1 select ok", data.get("ranker"), data.get("n_selected"))
PY
echo "case1 ok"

echo "=== case 2: interrupt + --resume ==="
WORKDIR2="$(mktemp -d)"
MODEL2="$WORKDIR2/model"
seed_repo "$WORKDIR2/repo" 6
cd "$WORKDIR2/repo"
"$BIN" bootstrap --repo "$WORKDIR2/repo" --out "$MODEL2" \
  --max-commits 2 --mutants-per-commit 1 --jobs 1 --seed 22 --force \
  --train-threads 1 >"$WORKDIR2/boot2.log" 2>&1 &
BPID=$!
# Interrupt once features_dump or train starts (covers train interrupt path).
for _ in $(seq 1 180); do
  if rg -q "features_dump:|train:|mutate" "$WORKDIR2/boot2.log" 2>/dev/null; then
    kill -INT "$BPID" || true
    break
  fi
  sleep 0.25
done
wait "$BPID" || true
STATUS=$("$PYTHON" - <<PY
import json
from pathlib import Path
st = json.loads(Path("$WORKDIR2/repo/.testpilot/bootstrap/state.json").read_text())
print(st.get("status") or "")
PY
)
if [[ "$STATUS" != "interrupted" ]]; then
  # Race: bootstrap finished before SIGINT — still exercise resume by
  # invalidating model.json so train re-runs even if step was Done.
  echo "case2: no interrupt race (status=$STATUS); forcing invalid artifact resume path"
  mkdir -p "$MODEL2"
  echo "{not json" >"$MODEL2/model.json"
  "$PYTHON" - <<PY
import json
from pathlib import Path
p = Path("$WORKDIR2/repo/.testpilot/bootstrap/state.json")
st = json.loads(p.read_text())
st["status"] = "interrupted"
st.setdefault("steps", {})["train"] = "interrupted"
p.write_text(json.dumps(st, indent=2) + "\n")
PY
fi
"$BIN" bootstrap --repo "$WORKDIR2/repo" --out "$MODEL2" \
  --max-commits 2 --mutants-per-commit 1 --jobs 1 --seed 22 --resume \
  --train-threads 1 2>&1 | tee "$WORKDIR2/boot2-resume.log"
rg -q "bootstrap complete" "$WORKDIR2/boot2-resume.log"
test -f "$MODEL2/model.json"
assert_report_schema "$MODEL2"
# Optional: if mutate wrote pairs before interrupt, resume must not drop them.
if [[ -f "$WORKDIR2/repo/.testpilot/bootstrap/mutate_pairs.jsonl" ]]; then
  echo "case2: mutate pairs present (resume kept progress)"
fi
echo "case2 ok"

echo "=== case 3: engineered use_baseline → select ranker=cov_lines ==="
WORKDIR3="$(mktemp -d)"
seed_repo "$WORKDIR3/repo" 3
cd "$WORKDIR3/repo"
"$BIN" init >/dev/null
"$BIN" collect git >/dev/null
# Minimal baseline-only model dir (no model.txt); schema_sha must match embedded.
MODEL3="$WORKDIR3/model"
mkdir -p "$MODEL3"
"$PYTHON" - <<PY
import json
from pathlib import Path
from testpilot_train.schema import load_features_schema
schema = load_features_schema(Path("$ROOT") / "features_schema.json")
payload = {
  "features_version": schema.features_version,
  "schema_sha256": schema.schema_sha256,
  "feature_names": list(schema.feature_names),
  "objective": "binary",
  "best_iteration": 1,
  "predict_raw_score": True,
  "ranker": "cov_lines",
  "bootstrap": {
    "verdict": "use_baseline",
    "reason": "insufficient_commit_idx",
    "gate": "synthetic_test_vs_cov_lines_cluster_C",
  },
  "constraints": {"categorical": False, "linear_tree": False},
}
Path("$MODEL3/model.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
assert not Path("$MODEL3/model.txt").exists()
PY
# Two-commit select: edit, commit, collect+dump so HEAD has a feature snapshot.
echo "# e2e" >> pkg/calc.py
git add pkg/calc.py && git commit -q -m "head edit"
"$BIN" collect git >/dev/null
"$BIN" features dump --out "$WORKDIR3/repo/.testpilot/pairs.parquet" >/dev/null
BASE=$(git rev-parse HEAD~1)
HEAD=$(git rev-parse HEAD)
SEL3=$("$BIN" select --repo "$WORKDIR3/repo" --db "$WORKDIR3/repo/.testpilot/db.sqlite" \
  --base "$BASE" --head "$HEAD" --model "$MODEL3" --max-tests 2 --format json \
  --no-safe --safe-min-candidates 1)
"$PYTHON" - <<PY
import json
data = json.loads('''$SEL3''')
assert data.get("ranker") == "cov_lines", data
# Engineered baseline: cov_lines ranker must be honored (selection may be empty
# if the tiny edit has no test graph — still assert ranker wiring).
assert data.get("bootstrap", {}).get("verdict") == "use_baseline", data
print("case3 select ok", data["ranker"], data.get("n_selected"), data.get("n_candidates"))
PY
echo "case3 ok"

echo "=== case 4: fingerprint mismatch refuses without --force ==="
WORKDIR4="$(mktemp -d)"
seed_repo "$WORKDIR4/repo" 3
cd "$WORKDIR4/repo"
"$BIN" bootstrap --repo "$WORKDIR4/repo" --out "$WORKDIR4/model" \
  --max-commits 2 --mutants-per-commit 1 --jobs 1 --seed 44 --force \
  --train-threads 1 >/dev/null
set +e
OUT=$("$BIN" bootstrap --repo "$WORKDIR4/repo" --out "$WORKDIR4/model" \
  --max-commits 3 --mutants-per-commit 1 --jobs 1 --seed 44 --resume \
  --train-threads 1 2>&1)
RC=$?
set -e
test "$RC" -ne 0
echo "$OUT" | rg -q -- "--force"
echo "case4 ok"

echo "=== case 5: golden report.json schema (from case1) ==="
assert_report_schema "$MODEL1"
echo "case5 ok"

END_WALL=$(date +%s)
ELAPSED=$((END_WALL - START_WALL))
echo "bootstrap_e2e elapsed=${ELAPSED}s"
if [[ "$ELAPSED" -ge 120 ]]; then
  echo "bootstrap_e2e exceeded 120s budget" >&2
  exit 1
fi
echo "bootstrap_e2e: all cases passed"
