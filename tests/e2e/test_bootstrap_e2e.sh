#!/usr/bin/env bash
# Week-6 bootstrap e2e (spec §7.2): full / resume / use_baseline select / fingerprint / schema.
# Budget: ≤2 min wall. No click/jinja/attrs download.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
FIX="$ROOT/tests/fixtures/bootstrap_e2e"
START_WALL=$(date +%s)
SEED=11
N=2
K=1

BIN="${CARGO_TARGET_DIR:-$ROOT/target}/debug/testpilot"
# Always rebuild so local runs cannot pick a stale binary (finalize/mutate asserts).
(cd "$ROOT" && cargo build -p testpilot-cli --locked)
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
export PYTHONPATH="$ROOT/python/testpilot-train/src:$ROOT/scripts${PYTHONPATH:+:$PYTHONPATH}"

seed_repo() {
  local dest="$1"
  local ncommits="${2:-6}"
  rm -rf "$dest"
  mkdir -p "$dest"
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
  # Body-level edits on EVERY commit after root (find_sites looks at function bodies).
  local i
  for i in $(seq 1 $((ncommits - 1))); do
    cat > pkg/calc.py <<PY
def add(a: int, b: int) -> int:
    s = a + b
    return s + $((i % 3))


def mul(a: int, b: int) -> int:
    return a * b


def clamp(x: int, lo: int = 0, hi: int = 100) -> int:
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x
PY
    # Keep tests green: add(2,3) must equal 5 + (i%3) — update expectations.
    cat > tests/test_calc.py <<PY
from pkg.calc import add, mul, clamp


def test_add():
    assert add(2, 3) == $((5 + i % 3))


def test_mul():
    assert mul(2, 4) == 8


def test_clamp():
    assert clamp(-1) == 0
    assert clamp(50) == 50
    assert clamp(50, lo=10, hi=40) == 40
PY
    git add -A
    git commit -q -m "c$i body edit"
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
steps = rep["steps"]
required = (
    "init", "cleanup", "collect_git", "backfill", "mutate",
    "features_dump", "train", "gate", "finalize",
)
for step in required:
    assert steps.get(step) == "done", (step, steps)
print("report schema ok", mj["bootstrap"]["verdict"], mj["ranker"])
PY
}

assert_mutate_happened() {
  local repo="$1"
  local boot_log="$2"
  "$PYTHON" - <<PY
import json, re, sqlite3
from pathlib import Path
repo = Path("$repo")
log = Path("$boot_log").read_text(encoding="utf-8", errors="replace")
# Prefer JSON blob from mutate stdout embedded in bootstrap log.
n_attempted = None
for m in re.finditer(r"\{[^{}]*\"n_mutants_attempted\"[^{}]*\}", log):
    try:
        n_attempted = json.loads(m.group(0)).get("n_mutants_attempted")
    except Exception:
        pass
if n_attempted is None:
    # Fall back: count synthetic runs in DB.
    db = repo / ".testpilot" / "db.sqlite"
    con = sqlite3.connect(db)
    n_syn = con.execute(
        "SELECT COUNT(*) FROM test_runs WHERE IFNULL(synthetic,0)=1"
    ).fetchone()[0]
    con.close()
    assert n_syn > 0, f"mutate no-op: no synthetic runs and no n_mutants_attempted in log"
    print("mutate ok via n_synthetic_runs", n_syn)
else:
    assert n_attempted > 0, f"n_mutants_attempted={n_attempted} (mutate no-op)"
    print("mutate ok n_mutants_attempted", n_attempted)
# dump fingerprint
st = json.loads((repo / ".testpilot" / "bootstrap" / "state.json").read_text())
fp = st.get("dump_fingerprint") or {}
n_syn = fp.get("n_synthetic_runs", 0)
assert n_syn > 0, f"dump_fingerprint.n_synthetic_runs={n_syn}"
print("dump_fingerprint ok", fp)
PY
}

collect_mutant_gold() {
  # Prints JSON: {m_shas: [...], labels: {m_sha: kill|survive}, pairs: [...]}
  local repo="$1"
  "$PYTHON" - <<PY
import json, sqlite3
from pathlib import Path
repo = Path("$repo")
pairs_path = repo / ".testpilot" / "bootstrap" / "mutate_pairs.jsonl"
pairs = []
m_shas = []
if pairs_path.is_file():
    for line in pairs_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        pairs.append(rec["pair"])
        m_shas.append(rec["m_sha"])
db = repo / ".testpilot" / "db.sqlite"
con = sqlite3.connect(db)
labels = {}
for m in m_shas:
    row = con.execute(
        """
        SELECT COUNT(*) FROM test_runs r
        JOIN test_results tr ON tr.run_id = r.id
        WHERE r.sha = ? AND IFNULL(r.synthetic,0)=1 AND tr.outcome IN ('fail','error')
        """,
        (m,),
    ).fetchone()
    labels[m] = "kill" if row and row[0] > 0 else "survive"
# uniqueness of (C,i) via completed pairs / synthetic mapping
pair_counts = {}
for p in pairs:
    pair_counts[p] = pair_counts.get(p, 0) + 1
dups = {k: v for k, v in pair_counts.items() if v > 1}
assert not dups, f"duplicate pair keys in pairs jsonl: {dups}"
# Also ensure no duplicate synthetic runs per mutant sha
for m in m_shas:
    n = con.execute(
        "SELECT COUNT(*) FROM test_runs WHERE sha=? AND IFNULL(synthetic,0)=1",
        (m,),
    ).fetchone()[0]
    assert n == 1, f"duplicate synthetic runs for {m}: {n}"
con.close()
print(json.dumps({"m_shas": sorted(m_shas), "labels": labels, "pairs": sorted(pairs)}))
PY
}

echo "=== case 1: full bootstrap tiny N/K (gold) ==="
WORKDIR="$(mktemp -d)"
MODEL1="$WORKDIR/model"
seed_repo "$WORKDIR/repo" 6
cd "$WORKDIR/repo"
"$BIN" bootstrap --repo "$WORKDIR/repo" --out "$MODEL1" \
  --max-commits "$N" --mutants-per-commit "$K" --jobs 1 --seed "$SEED" --force \
  --train-threads 1 2>&1 | tee "$WORKDIR/boot1.log"
test -f "$MODEL1/model.json"
grep -q "bootstrap complete" "$WORKDIR/boot1.log"
grep -q "gate: verdict=" "$WORKDIR/boot1.log"
assert_report_schema "$MODEL1"
assert_mutate_happened "$WORKDIR/repo" "$WORKDIR/boot1.log"
GOLD_JSON=$(collect_mutant_gold "$WORKDIR/repo")
echo "gold=$GOLD_JSON"
"$PYTHON" - <<PY
import json
g = json.loads('''$GOLD_JSON''')
assert len(g["m_shas"]) > 0, g
print("gold mutants", len(g["m_shas"]))
PY
# Select must load the artifact.
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

echo "=== case 2: interrupt mutate + --resume vs gold (same seed clone) ==="
WORKDIR2="$(mktemp -d)"
seed_repo "$WORKDIR2/repo" 6
# Identical commit SHAs → identical mutant SHAs (gold vs interrupt+resume).
cp -a "$WORKDIR2/repo" "$WORKDIR2/repo_gold"
MODEL_GOLD="$WORKDIR2/model_gold"
cd "$WORKDIR2/repo_gold"
"$BIN" bootstrap --repo "$WORKDIR2/repo_gold" --out "$MODEL_GOLD" \
  --max-commits "$N" --mutants-per-commit "$K" --jobs 1 --seed "$SEED" --force \
  --train-threads 1 2>&1 | tee "$WORKDIR2/boot2-gold.log"
assert_mutate_happened "$WORKDIR2/repo_gold" "$WORKDIR2/boot2-gold.log"
GOLD2_JSON=$(collect_mutant_gold "$WORKDIR2/repo_gold")
echo "gold2=$GOLD2_JSON"
"$PYTHON" - <<PY
import json
g = json.loads('''$GOLD2_JSON''')
assert len(g["m_shas"]) > 0, g
assert any(v == "kill" for v in g["labels"].values()) or any(
    v == "survive" for v in g["labels"].values()
), g
print("gold2 mutants", len(g["m_shas"]), "labels", g["labels"])
PY

MODEL2="$WORKDIR2/model"
cd "$WORKDIR2/repo"
MARKER="$WORKDIR2/repo/.testpilot/bootstrap/TESTPILOT_PAUSE_AFTER_PAIRS"
rm -f "$MARKER"
export TESTPILOT_TEST_PAUSE_AFTER_PAIRS=1
"$BIN" bootstrap --repo "$WORKDIR2/repo" --out "$MODEL2" \
  --max-commits "$N" --mutants-per-commit "$K" --jobs 1 --seed "$SEED" --force \
  --train-threads 1 >"$WORKDIR2/boot2.log" 2>&1 &
BPID=$!
# Wait for pause marker (mutate completed ≥1 pair and is sleeping).
for _ in $(seq 1 600); do
  if [[ -f "$MARKER" ]]; then
    break
  fi
  if ! kill -0 "$BPID" 2>/dev/null; then
    echo "bootstrap exited before pause marker" >&2
    cat "$WORKDIR2/boot2.log" >&2 || true
    exit 1
  fi
  sleep 0.25
done
if [[ ! -f "$MARKER" ]]; then
  echo "pause marker never appeared" >&2
  kill -INT "$BPID" 2>/dev/null || true
  wait "$BPID" || true
  exit 1
fi
# Require 1 ≤ completed_pairs < total before SIGINT.
"$PYTHON" - <<PY
from pathlib import Path
pairs_path = Path("$WORKDIR2/repo/.testpilot/bootstrap/mutate_pairs.jsonl")
n = 0
if pairs_path.is_file():
    n = sum(1 for line in pairs_path.read_text().splitlines() if line.strip())
total = $N * $K
assert n >= 1, f"completed_pairs before SIGINT={n}"
assert n < total, f"completed_pairs={n} not < total={total} (need room to resume)"
print("pre-SIGINT pairs", n, "total", total)
PY
kill -INT "$BPID" || true
wait "$BPID" || true
unset TESTPILOT_TEST_PAUSE_AFTER_PAIRS
rm -f "$MARKER"
"$PYTHON" - <<PY
import json
from pathlib import Path
st = json.loads(Path("$WORKDIR2/repo/.testpilot/bootstrap/state.json").read_text())
assert st.get("status") == "interrupted", st
assert st.get("steps", {}).get("mutate") == "interrupted", st.get("steps")
print("interrupted ok")
PY
"$BIN" bootstrap --repo "$WORKDIR2/repo" --out "$MODEL2" \
  --max-commits "$N" --mutants-per-commit "$K" --jobs 1 --seed "$SEED" --resume \
  --train-threads 1 2>&1 | tee "$WORKDIR2/boot2-resume.log"
grep -q "bootstrap complete" "$WORKDIR2/boot2-resume.log"
assert_report_schema "$MODEL2"
RESUME_JSON=$(collect_mutant_gold "$WORKDIR2/repo")
"$PYTHON" - <<PY
import json
gold = json.loads('''$GOLD2_JSON''')
got = json.loads('''$RESUME_JSON''')
assert set(got["m_shas"]) == set(gold["m_shas"]), (got["m_shas"], gold["m_shas"])
assert got["labels"] == gold["labels"], (got["labels"], gold["labels"])
assert set(got["pairs"]) == set(gold["pairs"]), (got["pairs"], gold["pairs"])
print("case2 gold match ok", len(got["m_shas"]))
PY
echo "case2 ok"

echo "=== case 3: engineered use_baseline → select ranker=cov_lines (≥1 test) ==="
WORKDIR3="$(mktemp -d)"
seed_repo "$WORKDIR3/repo" 3
cd "$WORKDIR3/repo"
"$BIN" init >/dev/null
# Clean coverage run on base (needed for cov_lines candidates).
export COVERAGE_CORE=ctrace
"$PYTHON" -m pip install -q -e .
# Re-assert plugin editable: fixture install can leave a stale pytest11 entrypoint.
"$PYTHON" -m pip install -q -e "$ROOT/python/pytest-testpilot"
"$PYTHON" -m pytest --testpilot -q >/dev/null
"$BIN" collect git >/dev/null
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
BASE=$(git rev-parse HEAD)
# Body-level edit of covered code so select has candidates.
cat > pkg/calc.py <<'PY'
def add(a: int, b: int) -> int:
    s = a + b
    return s + 1


def mul(a: int, b: int) -> int:
    return a * b


def clamp(x: int, lo: int = 0, hi: int = 100) -> int:
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x
PY
git add pkg/calc.py && git commit -q -m "head body edit"
"$BIN" collect git >/dev/null
"$BIN" features dump --out "$WORKDIR3/repo/.testpilot/pairs.parquet" >/dev/null
HEAD=$(git rev-parse HEAD)
SEL3=$("$BIN" select --repo "$WORKDIR3/repo" --db "$WORKDIR3/repo/.testpilot/db.sqlite" \
  --base "$BASE" --head "$HEAD" --model "$MODEL3" --max-tests 2 --format json \
  --no-safe --safe-min-candidates 1)
"$PYTHON" - <<PY
import json
data = json.loads('''$SEL3''')
assert data.get("ranker") == "cov_lines", data
assert data.get("bootstrap", {}).get("verdict") == "use_baseline", data
assert data.get("n_candidates", 0) >= 1, data
assert data.get("n_selected", 0) >= 1, data
print("case3 select ok", data["ranker"], data["n_selected"], data["n_candidates"])
PY
echo "case3 ok"

echo "=== case 4: fingerprint mismatch refuses without --force ==="
WORKDIR4="$(mktemp -d)"
seed_repo "$WORKDIR4/repo" 3
cd "$WORKDIR4/repo"
"$BIN" bootstrap --repo "$WORKDIR4/repo" --out "$WORKDIR4/model" \
  --max-commits "$N" --mutants-per-commit "$K" --jobs 1 --seed 44 --force \
  --train-threads 1 >/dev/null
set +e
OUT=$("$BIN" bootstrap --repo "$WORKDIR4/repo" --out "$WORKDIR4/model" \
  --max-commits 3 --mutants-per-commit "$K" --jobs 1 --seed 44 --resume \
  --train-threads 1 2>&1)
RC=$?
set -e
test "$RC" -ne 0
echo "$OUT" | grep -q -- "--force"
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
