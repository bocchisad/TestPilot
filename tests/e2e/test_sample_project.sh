#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SAMPLE="$ROOT/tests/fixtures/sample_project"
BIN="${CARGO_TARGET_DIR:-$ROOT/target}/debug/testpilot"
if [[ ! -x "$BIN" ]]; then
  BIN="$(ls "$ROOT"/target/*/debug/testpilot 2>/dev/null | head -1 || true)"
fi
if [[ -z "${BIN:-}" || ! -x "$BIN" ]]; then
  (cd "$ROOT" && RUSTUP_TOOLCHAIN="${RUSTUP_TOOLCHAIN:-1.98.1}" cargo build -p testpilot-cli)
  BIN="$ROOT/target/debug/testpilot"
  if [[ ! -x "$BIN" ]]; then
    BIN="$(find "$ROOT/target" -name testpilot -type f | head -1)"
  fi
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
python -m pip install -e "$ROOT/python/pytest-testpilot" -q
export PYTHONPATH="$WORKDIR${PYTHONPATH:+:$PYTHONPATH}"
export COVERAGE_CORE=ctrace

pytest --testpilot -q
SHA="$(git rev-parse HEAD)"
pytest --junitxml=junit.xml -q
"$BIN" collect junit junit.xml --sha "$SHA"
"$BIN" collect git
"$BIN" stats

# Non-zero checks via sqlite
python - <<'PY'
import sqlite3
c = sqlite3.connect(".testpilot/db.sqlite")
commits = c.execute("SELECT COUNT(*) FROM commits").fetchone()[0]
runs = c.execute("SELECT COUNT(*) FROM test_runs").fetchone()[0]
tests = c.execute("SELECT COUNT(DISTINCT test_id) FROM test_results").fetchone()[0]
assert commits > 0, commits
assert runs > 0, runs
assert tests > 0, tests

# test_id consistency between pytest and junit sources
pytest_ids = {r[0] for r in c.execute(
    "SELECT DISTINCT tr.test_id FROM test_results tr JOIN test_runs r ON r.id=tr.run_id WHERE r.source='pytest'"
)}
junit_ids = {r[0] for r in c.execute(
    "SELECT DISTINCT tr.test_id FROM test_results tr JOIN test_runs r ON r.id=tr.run_id WHERE r.source='junit'"
)}
assert pytest_ids == junit_ids, (pytest_ids, junit_ids)
print("e2e ok", {"commits": commits, "runs": runs, "tests": tests, "ids": sorted(pytest_ids)})
PY
