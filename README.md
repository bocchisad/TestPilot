# TestPilot

Offline **predictive test selection** toolkit. This repository currently ships the **data-collection** layer only (no ML / ranking yet): git history, JUnit/pytest outcomes, and per-test line coverage in a local SQLite database.

## Quickstart

```bash
# Build CLI
cargo build -p testpilot-cli
export PATH="$PWD/target/debug:$PATH"

# Install pytest plugin
pip install -e python/pytest-testpilot

# In a git checkout of your project
testpilot init
pytest --testpilot
# Prefer equals form so pytest does not treat PATH as a rootdir arg:
#   pytest --testpilot --testpilot-db=/abs/path/to/db.sqlite
testpilot collect git
testpilot stats
```

Optional JUnit ingest:

```bash
pytest --junitxml=junit.xml
testpilot collect junit junit.xml --sha "$(git rev-parse HEAD)"
```

Database path defaults to `<git-toplevel>/.testpilot/db.sqlite`. `testpilot init` creates the DB and appends `.testpilot/` to `.git/info/exclude` (not the tracked `.gitignore`).

`collect git` walks commits reachable from **HEAD** only (not `--all`), in parent-before-child order.

## Features (week 2)

```bash
testpilot features dump --out pairs.parquet
testpilot features show --base HEAD~1 --head HEAD --format json
testpilot features schema --check features_schema.json
```

`features show` needs history snapshots written by `features dump`. Opening a schema **v4** DB upgrades in place to **v5** and **drops** `feature_history_snapshot` rows — run `features dump` again (otherwise show errors with that hint).

## Database schema

Source of truth: [`crates/testpilot-core/schema.sql`](crates/testpilot-core/schema.sql) (`SCHEMA_VERSION = 5`). A synced copy ships inside the pytest plugin; `python scripts/sync_schema.py --check` enforces equality in CI.

| Table | Role |
|-------|------|
| `commits` | Commit metadata; `oversized=1` means the diff exceeded `--max-files-per-commit` and **no** `file_changes` were stored |
| `commit_parents` | Parent SHAs (`parent_idx=0` is first parent / merge base for diffs) |
| `file_changes` | Per-commit path changes (`A/M/D/R`), line counts, `is_binary` |
| `test_runs` | One row per pytest/JUnit ingest; `dirty` + `diff_hash` for unclean trees; `input_hash` for JUnit dedup |
| `tests` | Dictionary of unique `test_id` strings (`id` referenced elsewhere) |
| `test_results` | Outcomes (`pass/fail/error/skip`), duration, `flaky`, `attempts` (via `test_pk`) |
| `test_coverage` | Per `(run_id, test_pk, path)` coverage blob (`WITHOUT ROWID`) |

Coverage blob format: leading version byte `0x01`, then delta-encoded unsigned varints of sorted unique line numbers. Empty coverage is exactly `[0x01]`.

Dirty runs (`dirty=1`, including untracked files) are recorded now; **future training will exclude them by default**.

## Commands

| Command | Purpose |
|---------|---------|
| `testpilot init` | Create DB + `.git/info/exclude` entry |
| `testpilot collect git` | Incremental HEAD history via [gix](https://github.com/GitoxideLabs/gitoxide) |
| `testpilot collect junit <glob> --sha <sha>` | Stream-parse JUnit XML |
| `testpilot stats [--json]` | Summary + top slow / failing tests |
| `testpilot debug coverage --run-id/--sha --test-id` | Decode coverage blobs |
| `testpilot features dump\|show\|schema` | Leak-free (change, test) feature vectors / Parquet / schema lockfile |

## Development

```bash
cargo fmt --check
cargo clippy -- -D warnings
cargo test
pip install -e 'python/pytest-testpilot[dev]'
ruff check python/pytest-testpilot
pytest python/pytest-testpilot
python scripts/sync_schema.py --check
bash tests/e2e/test_sample_project.sh
```

## License

MIT © bocchisad
