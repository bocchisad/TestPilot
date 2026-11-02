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
testpilot collect git
testpilot stats
```

Optional JUnit ingest:

```bash
pytest --junitxml=junit.xml
testpilot collect junit junit.xml --sha "$(git rev-parse HEAD)"
```

Database path defaults to `.testpilot/db.sqlite` (also created by `testpilot init`, which adds `.testpilot/` to `.gitignore`).

## Database schema

Source of truth: [`crates/testpilot-core/schema.sql`](crates/testpilot-core/schema.sql). A synced copy ships inside the pytest plugin; `python scripts/sync_schema.py --check` enforces equality in CI.

| Table | Role |
|-------|------|
| `commits` | Commit metadata; `oversized=1` means the diff exceeded `--max-files-per-commit` and **no** `file_changes` were stored |
| `commit_parents` | Parent SHAs (`parent_idx=0` is first parent / merge base for diffs) |
| `file_changes` | Per-commit path changes (`A/M/D/R`), line counts, `is_binary` |
| `test_runs` | One row per pytest/JUnit ingest; `dirty` + `diff_hash` for unclean trees; `input_hash` for JUnit dedup |
| `test_results` | Outcomes (`pass/fail/error/skip`), duration, `flaky`, `attempts` |
| `test_coverage` | Per `(run_id, test_id, path)` coverage blob |

Coverage blob format: leading version byte `0x01`, then delta-encoded unsigned varints of sorted unique line numbers. Empty coverage is exactly `[0x01]`.

Dirty runs (`dirty=1`, including untracked files) are recorded now; **future training will exclude them by default**.

## Commands

| Command | Purpose |
|---------|---------|
| `testpilot init` | Create DB + gitignore entry |
| `testpilot collect git` | Incremental history via [gix](https://github.com/GitoxideLabs/gitoxide) |
| `testpilot collect junit <glob> --sha <sha>` | Stream-parse JUnit XML |
| `testpilot stats [--json]` | Summary + top slow / failing tests |
| `testpilot debug coverage --run-id/--sha --test-id` | Decode coverage blobs |

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
