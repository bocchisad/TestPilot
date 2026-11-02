#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SAMPLE="$ROOT/tests/fixtures/sample_project"

# Build with whatever cargo resolves (rust-toolchain.toml / CI's +1.85.0).
# Prefer an already-built binary so local SDK mismatches on MSRV don't block e2e.
candidates=(
  "${CARGO_TARGET_DIR:-}/debug/testpilot"
  "$ROOT/target/debug/testpilot"
)
BIN=""
for c in "${candidates[@]}"; do
  if [[ -n "$c" && -x "$c" ]]; then
    BIN="$c"
    break
  fi
done
if [[ -z "$BIN" ]]; then
  (cd "$ROOT" && cargo build -p testpilot-cli --locked)
  BIN="${CARGO_TARGET_DIR:-$ROOT/target}/debug/testpilot"
fi
if [[ ! -x "$BIN" ]]; then
  echo "testpilot binary not found at $BIN" >&2
  exit 1
fi

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT
cp -R "$SAMPLE/." "$WORKDIR/"
cd "$WORKDIR"
git init -b main
git config user.email "e2e@example.com"
git config user.name "E2E"
git add .
git commit -m "init"

"$BIN" init
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON="$ROOT/.venv/bin/python"
elif [[ -n "${VIRTUAL_ENV:-}" && -x "${VIRTUAL_ENV}/bin/python" ]]; then
  PYTHON="${VIRTUAL_ENV}/bin/python"
else
  PYTHON="$(command -v python3 || command -v python)"
fi
"$PYTHON" -m pip install -e "$ROOT/python/pytest-testpilot" -q
export PYTHONPATH="$WORKDIR${PYTHONPATH:+:$PYTHONPATH}"
export COVERAGE_CORE=ctrace
PYTEST=("$PYTHON" -m pytest)

# Bench: without testpilot
START=$(date +%s.%N)
"${PYTEST[@]}" -q
END=$(date +%s.%N)
"$PYTHON" - <<PY
start=float("$START"); end=float("$END")
print(f"bench: pytest without --testpilot: {end-start:.3f}s")
PY

# With testpilot
START=$(date +%s.%N)
"${PYTEST[@]}" --testpilot -q
END=$(date +%s.%N)
"$PYTHON" - <<PY
start=float("$START"); end=float("$END")
print(f"bench: pytest with --testpilot: {end-start:.3f}s")
PY

SHA="$(git rev-parse HEAD)"
"${PYTEST[@]}" --junitxml=junit.xml -q
"$BIN" collect junit junit.xml --sha "$SHA"
"$BIN" collect git
"$BIN" stats

"$PYTHON" - <<'PY'
import sqlite3
c = sqlite3.connect(".testpilot/db.sqlite")
commits = c.execute("SELECT COUNT(*) FROM commits").fetchone()[0]
runs = c.execute("SELECT COUNT(*) FROM test_runs").fetchone()[0]
tests = c.execute("SELECT COUNT(*) FROM tests").fetchone()[0]
dirty = c.execute("SELECT dirty FROM test_runs WHERE source='pytest' ORDER BY id LIMIT 1").fetchone()[0]
assert commits > 0, commits
assert runs > 0, runs
assert tests > 0, tests
assert dirty == 0, f"expected dirty=0 after clean commit, got {dirty}"

pytest_ids = {r[0] for r in c.execute(
    """SELECT DISTINCT t.test_id FROM test_results r
       JOIN test_runs run ON run.id=r.run_id
       JOIN tests t ON t.id=r.test_pk
       WHERE run.source='pytest'"""
)}
junit_ids = {r[0] for r in c.execute(
    """SELECT DISTINCT t.test_id FROM test_results r
       JOIN test_runs run ON run.id=r.run_id
       JOIN tests t ON t.id=r.test_pk
       WHERE run.source='junit'"""
)}
assert pytest_ids == junit_ids, (pytest_ids, junit_ids)
print("e2e ok", {"commits": commits, "runs": runs, "tests": tests, "ids": sorted(pytest_ids), "dirty": dirty})
PY
