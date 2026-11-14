#!/usr/bin/env bash
# Local-only Week-6 bootstrap measurements (NOT CI). Spec §7.3 / §8 / T6.
# Pins tip SHAs, dry-runs first; aborts if estimated wall > 4h.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PINS_CLICK_TAG=8.1.8
PINS_CLICK_SHA=934813e4d421071a1b3db3973c02fe2721359a6e
PINS_JINJA_TAG=3.1.6
PINS_JINJA_SHA=15206881c006c79667fe5154fe80c01c65410679
PINS_ATTRS_TAG=24.3.0
PINS_ATTRS_SHA=598494a618410490cfbe0c896b7a544f6d23e0d9

URL_CLICK=https://github.com/pallets/click.git
URL_JINJA=https://github.com/pallets/jinja.git
URL_ATTRS=https://github.com/python-attrs/attrs.git

N=200
K=4
JOBS=2
SEED=42
MAX_EST_S=14400  # 4h for 3×full + 2nd click level-b

# Portable wall timing (bash builtin; avoid GNU/BSD /usr/bin/time differences).
TIMEFORMAT='%R'

ensure_repo() {
  local name="$1" url="$2" sha="$3"
  local dest=".cache/repos/$name"
  mkdir -p .cache/repos
  if [[ ! -d "$dest/.git" ]]; then
    # Full clone — blob:none partial clones break historical pytest checkouts.
    git clone "$url" "$dest"
  fi
  git -C "$dest" fetch --tags --force origin
  git -C "$dest" checkout --force "$sha"
  local got
  got="$(git -C "$dest" rev-parse HEAD)"
  if [[ "$got" != "$sha" ]]; then
    echo "pin mismatch $name: want $sha got $got" >&2
    exit 1
  fi
}

parse_dry_total() {
  local log="$1"
  if grep -E '^total_s=' "$log" >/dev/null 2>&1; then
    grep -E '^total_s=' "$log" | tail -1 | cut -d= -f2
  else
    python3 - "$log" <<'PY'
import re, sys
text = open(sys.argv[1], encoding="utf-8", errors="replace").read()
m = re.search(r"total_s=([0-9.]+)", text)
if m:
    print(m.group(1))
else:
    m = re.search(r"total[^0-9]*([0-9]+(?:\.[0-9]+)?)\s*s", text, re.I)
    print(m.group(1) if m else "0")
PY
  fi
}

echo "=== env fingerprint ==="
{
  echo "date_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "nproc=$( (command -v nproc >/dev/null && nproc) || sysctl -n hw.ncpu 2>/dev/null || echo unknown )"
  if [[ "$(uname)" == Darwin ]]; then
    echo "cpu=$(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo unknown)"
  else
    echo "cpu=$(grep -m1 'model name' /proc/cpuinfo 2>/dev/null | cut -d: -f2- | xargs || echo unknown)"
  fi
  if ! command -v less >/dev/null 2>&1; then
    echo "WARN: less not on PATH — click pager tests fail every commit (persistent, not new_fail)" >&2
  else
    echo "less=$(command -v less)"
  fi
} | tee docs/measurements/env-host.txt
python3 -m pip freeze > docs/measurements/env-pip-freeze.txt

echo "=== ensure pinned clones ==="
ensure_repo click "$URL_CLICK" "$PINS_CLICK_SHA"
ensure_repo jinja "$URL_JINJA" "$PINS_JINJA_SHA"
ensure_repo attrs "$URL_ATTRS" "$PINS_ATTRS_SHA"

echo "=== build release ==="
cargo build -p testpilot-cli --release --locked
BIN="$ROOT/target/release/testpilot"

echo "=== dry-run estimates ==="
mkdir -p docs/measurements
EST_CLICK=0 EST_JINJA=0 EST_ATTRS=0
for R in click jinja attrs; do
  suite_flag=(--suite "$R")
  "$BIN" bootstrap --repo ".cache/repos/$R" --out "docs/measurements/bootstrap-$R" \
    --max-commits "$N" --mutants-per-commit "$K" --jobs "$JOBS" --seed "$SEED" \
    "${suite_flag[@]}" --dry-run | tee "docs/measurements/dry-run-$R.txt"
  case "$R" in
    click) EST_CLICK="$(parse_dry_total docs/measurements/dry-run-click.txt)" ;;
    jinja) EST_JINJA="$(parse_dry_total docs/measurements/dry-run-jinja.txt)" ;;
    attrs) EST_ATTRS="$(parse_dry_total docs/measurements/dry-run-attrs.txt)" ;;
  esac
done

python3 - <<PY
click, jinja, attrs = float("$EST_CLICK"), float("$EST_JINJA"), float("$EST_ATTRS")
# 3 full runs + second click for level-b
total = click + jinja + attrs + click
print(f"dry_run_estimates_s click={click} jinja={jinja} attrs={attrs}")
print(f"estimated_total_s={total} (3×full + 2nd click level-b)")
open("docs/measurements/dry-run-estimates.json", "w").write(
    __import__("json").dumps({
        "click": click, "jinja": jinja, "attrs": attrs,
        "estimated_total_s": total,
        "max_est_s": $MAX_EST_S,
    }, indent=2) + "\\n"
)
if total > $MAX_EST_S:
    print("ABORT: estimated wall > 4h — send estimates to mell before full runs.", flush=True)
    raise SystemExit(42)
PY

run_one() {
  local R="$1" OUT="$2"
  rm -rf ".cache/repos/$R/.testpilot"
  mkdir -p "$OUT"
  # attrs: exclude mypy suite (matches mell N=20 calibration that produced 17.139 s/commit).
  local -a env_extra=()
  if [[ "$R" == attrs ]]; then
    env_extra+=(env PYTEST_ADDOPTS="--ignore=tests/test_mypy.yml")
  fi
  {
    "${env_extra[@]}" time "$BIN" bootstrap --repo ".cache/repos/$R" --out "$OUT" \
      --max-commits "$N" --mutants-per-commit "$K" --jobs "$JOBS" --seed "$SEED" \
      --suite "$R" --force --train-threads 1
  } 2>&1 | tee "$OUT/measure.log"
  python3 - <<PY
import json
from pathlib import Path
rep = json.loads(Path("$OUT/bootstrap_report.json").read_text())
assert rep.get("n_backfill_reused", 1) == 0, rep
print("ok n_backfill_reused=0 n_new=", rep.get("n_backfill_new"))
PY
}

echo "=== full bootstrap runs ==="
START=$(date +%s)
run_one click docs/measurements/bootstrap-click
# Snapshot level-b gold before second click overwrites the DB.
python3 - <<'PY'
import json, sys
from pathlib import Path
sys.path.insert(0, "scripts")
from aggregate_bootstrap_w6 import collect_mutants
gold = collect_mutants(Path(".cache/repos/click"))
Path("docs/measurements/bootstrap-click/level_b_gold.json").write_text(
    json.dumps(gold, indent=2, sort_keys=True) + "\n"
)
print("level_b gold mutants", len(gold.get("m_shas_non_timeout") or []))
PY
run_one jinja docs/measurements/bootstrap-jinja
run_one attrs docs/measurements/bootstrap-attrs

echo "=== level-b second click ==="
rm -rf /tmp/tp-boot-click-b
run_one click /tmp/tp-boot-click-b

echo "=== LORO ×3 + summary ==="
python3 "$ROOT/scripts/aggregate_bootstrap_w6.py" \
  --root "$ROOT" \
  --level-b-out /tmp/tp-boot-click-b \
  --pins-click "$PINS_CLICK_SHA:$PINS_CLICK_TAG" \
  --pins-jinja "$PINS_JINJA_SHA:$PINS_JINJA_TAG" \
  --pins-attrs "$PINS_ATTRS_SHA:$PINS_ATTRS_TAG"

END=$(date +%s)
echo "measure_bootstrap_w6 wall_s=$((END - START))"
