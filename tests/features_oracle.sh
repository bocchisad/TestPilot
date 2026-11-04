#!/usr/bin/env bash
# Generate fixture → Rust dump → Python reference → compare; schema --check.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

FIX=$(mktemp -d)
trap 'rm -rf "$FIX"' EXIT

python3 scripts/generate_features_fixture.py --out "$FIX/repo"
cargo build -p testpilot-cli --quiet
BIN="$ROOT/target/debug/testpilot"

"$BIN" features schema --check features_schema.json

(
  cd "$FIX/repo"
  "$BIN" --db "$FIX/repo/.testpilot/db.sqlite" features dump --out "$FIX/pairs.parquet"
  "$BIN" --db "$FIX/repo/.testpilot/db.sqlite" features dump --out "$FIX/pairs2.parquet"
)

python3 scripts/features_reference.py \
  --repo "$FIX/repo" \
  --db "$FIX/repo/.testpilot/db.sqlite" \
  --out "$FIX/ref.json"

VENV="$FIX/venv"
python3 -m venv "$VENV"
"$VENV/bin/pip" install -q pyarrow
"$VENV/bin/python" scripts/compare_features.py --parquet "$FIX/pairs.parquet" --reference "$FIX/ref.json"

# determinism: two dumps equal
"$VENV/bin/python" - <<PY
import pyarrow.parquet as pq
a = pq.read_table("$FIX/pairs.parquet").to_pylist()
b = pq.read_table("$FIX/pairs2.parquet").to_pylist()
assert a == b, "determinism failed"
print("determinism ok", len(a))
PY

# Leakage: re-dump after adding a future commit+run must leave earlier rows unchanged.
"$VENV/bin/python" - "$FIX" "$BIN" <<'PY'
import os, sqlite3, subprocess, sys
from pathlib import Path
import pyarrow.parquet as pq

fix, bin_ = Path(sys.argv[1]), sys.argv[2]
repo = fix / "repo"
db = repo / ".testpilot" / "db.sqlite"
before = {(r["change"], r["test"]): r for r in pq.read_table(fix / "pairs.parquet").to_pylist()}
env = os.environ.copy()
env.update({
    "GIT_AUTHOR_DATE": "2020-01-10T00:00:00+00:00",
    "GIT_COMMITTER_DATE": "2020-01-10T00:00:00+00:00",
    "GIT_AUTHOR_NAME": "Fix",
    "GIT_AUTHOR_EMAIL": "fix@example.com",
    "GIT_COMMITTER_NAME": "Fix",
    "GIT_COMMITTER_EMAIL": "fix@example.com",
})
subprocess.check_call(["git", "commit", "--allow-empty", "-m", "future"], cwd=repo, env=env)
fut = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
parent = subprocess.check_output(["git", "rev-parse", "HEAD~1"], cwd=repo, text=True).strip()
conn = sqlite3.connect(db)
conn.execute(
    "INSERT INTO commits(sha,author_ts,message,oversized) VALUES (?,?,?,0)",
    (fut, 1578614400, "future"),
)
conn.execute(
    "INSERT INTO commit_parents(sha,parent_sha,parent_idx) VALUES (?,?,0)",
    (fut, parent),
)
tid = conn.execute("SELECT test_id FROM tests LIMIT 1").fetchone()[0]
pk = conn.execute("SELECT id FROM tests WHERE test_id=?", (tid,)).fetchone()[0]
cur = conn.execute(
    "INSERT INTO test_runs(sha,source,started_at,finished_at,dirty) VALUES (?,?,?,?,0)",
    (fut, "pytest", 1, 2),
)
rid = cur.lastrowid
conn.execute(
    "INSERT INTO test_results(run_id,test_pk,outcome,duration_ms,flaky,attempts) VALUES (?,?,?,?,0,1)",
    (rid, pk, "pass", 1),
)
conn.commit()
subprocess.check_call(
    [bin_, "--db", str(db), "features", "dump", "--out", str(fix / "pairs3.parquet")],
    cwd=repo,
)
after = {(r["change"], r["test"]): r for r in pq.read_table(fix / "pairs3.parquet").to_pylist()}
for k, row in before.items():
    assert k in after, k
    for col, val in row.items():
        assert after[k][col] == val, (k, col, val, after[k][col])
print("leakage ok")
PY

echo "features oracle ok"
