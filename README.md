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

## Training (week 3)

```bash
pip install -e 'python/testpilot-train[dev]'
testpilot-train train --data pairs.parquet --out /tmp/tp-model --features-schema features_schema.json
```

Primary label is `new_fail` (see [`docs/specs/2026-11-09-testpilot-training-design.md`](docs/specs/2026-11-09-testpilot-training-design.md)). Package details: [`python/testpilot-train/README.md`](python/testpilot-train/README.md).

### Mutation bootstrap (when real `new_fail` is absent)

Historical green `main` histories (e.g. click/jinja) yield no ranking positives. Generate synthetic failures via AST mutants as real commits under `refs/testpilot/mutants/`, then dump with `--include-mutants`. Design: [`docs/specs/2026-11-11-testpilot-mutation-bootstrap-design.md`](docs/specs/2026-11-11-testpilot-mutation-bootstrap-design.md).

```bash
python scripts/mutate_bootstrap.py --repo "$REPO" --db "$DB" --commits 5 --mutants-per-commit 3 --seed 42
testpilot features dump --out pairs.parquet --include-mutants
```

**Click on Linux:** install **`less`** (e.g. `apt install less`). Without it, every commit fails all `test_echo_via_pager` parametrizations for `less` — persistent env failures, not `new_fail`. Do not exclude those tests; install the pager.

## Select (week 4)

Rank tests for a change with a trained artifact (pure Rust — no Python at runtime). Schema is **embedded** in the binary (must match `model.json` `schema_sha256`).

```bash
cargo build -p testpilot-cli --release
# Requires feature_history_snapshot rows from `features dump`:
testpilot select \
  --model /path/to/tp-model \
  --repo "$REPO" --db "$DB" \
  --base HEAD~1 --head HEAD \
  --budget-frac 0.1 \
  --format lines | xargs -r pytest
```

Exactly one budget: `--budget-frac F` (0&lt;F≤1), `--max-tests N` (N≥1), or `--time-budget SECONDS`. Formats: `lines` (default), `table`, `json`. Use `--worktree` or `--head WORKTREE` for dirty trees (time features use **HEAD** author time, not wall clock).

**Limitations:** models trained on mutation bootstrap may have near-constant history features (`t_fail_rate`, `t_fail_cnt`, `t_changed`, `t_flaky_rate`, `cov_lag`); if `useful_on_val` is null the CLI warns that the model was validated on synthetic data only. Candidates are pytest nodeids (as in dump/train); parametrized ids from the base run are kept, but newly discovered defs are emitted bare (no param expansion — one bare id may expand to N collected nodeids). Design: [`docs/specs/2026-11-13-testpilot-select-design.md`](docs/specs/2026-11-13-testpilot-select-design.md). Latency: `scripts/bench_select.py`.

## Database schema

Source of truth: [`crates/testpilot-core/schema.sql`](crates/testpilot-core/schema.sql) (`SCHEMA_VERSION = 6`). A synced copy ships inside the pytest plugin; `python scripts/sync_schema.py --check` enforces equality in CI.

| Table | Role |
|-------|------|
| `commits` | Commit metadata; `oversized=1` means the diff exceeded `--max-files-per-commit` and **no** `file_changes` were stored |
| `commit_parents` | Parent SHAs (`parent_idx=0` is first parent / merge base for diffs) |
| `file_changes` | Per-commit path changes (`A/M/D/R`), line counts, `is_binary` |
| `test_runs` | One row per pytest/JUnit ingest; `dirty` + `diff_hash` for unclean trees; `input_hash` for JUnit dedup; `synthetic=1` for mutant bootstrap (excluded from real history/coverage/show) |
| `tests` | Dictionary of unique `test_id` strings (`id` referenced elsewhere) |
| `test_results` | Outcomes (`pass/fail/error/skip`), duration, `flaky`, `attempts` (via `test_pk`) |
| `test_coverage` | Per `(run_id, test_pk, path)` coverage blob (`WITHOUT ROWID`) |

Coverage blob format: leading version byte `0x01`, then delta-encoded unsigned varints of sorted unique line numbers. Empty coverage is exactly `[0x01]`.

Dirty runs (`dirty=1`, including untracked files) are recorded now; training should exclude them by default when building datasets.

## Commands

| Command | Purpose |
|---------|---------|
| `testpilot init` | Create DB + `.git/info/exclude` entry |
| `testpilot collect git` | Incremental HEAD history via [gix](https://github.com/GitoxideLabs/gitoxide) |
| `testpilot collect git --sha <sha>` | Index a single commit (mutant leaves off HEAD) |
| `testpilot collect junit <glob> --sha <sha> [--synthetic]` | Stream-parse JUnit XML (`--synthetic` for mutants) |
| `testpilot stats [--json]` | Summary + top slow / failing tests |
| `testpilot debug coverage --run-id/--sha --test-id` | Decode coverage blobs |
| `testpilot features dump\|show\|schema` | Leak-free (change, test) feature vectors / Parquet / schema lockfile |
| `testpilot-train train\|evaluate\|baselines` | Offline LightGBM ranking on feature parquet |

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
