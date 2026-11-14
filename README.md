# TestPilot

Offline **predictive test selection** toolkit: git/JUnit/pytest collection, change features, LightGBM ranking, safe-mode select, and a pytest plugin + GitHub Action for CI.

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

### Cold-start bootstrap (week 6)

One command: real backfill + mutation labels + train + quality gate. Prefer this over the low-level mutate script below. Design: [`docs/specs/2026-11-14-testpilot-bootstrap-design.md`](docs/specs/2026-11-14-testpilot-bootstrap-design.md).

```bash
cargo build -p testpilot-cli --release
./target/release/testpilot bootstrap --repo "$REPO" --out "$MODEL_DIR" \
  --max-commits 200 --mutants-per-commit 4 --jobs 2 --seed 42
# Defaults are N=200 × K=4. Use --resume after interrupt; --force to restart;
# --dry-run prints plan + time estimate (no writes).
```

`model.json` carries `ranker` (`model` or `cov_lines`) and `bootstrap.verdict` / `reason`. Sidecar `bootstrap_report.json` has step timings and mutate/backfill counters. `.testpilot/` is added to `.git/info/exclude` (not `.gitignore`) so the tree stays clean.

If the gate returns `use_baseline` (e.g. `insufficient_commit_idx` when synth TEST has too few distinct C), select uses the `cov_lines` ranker from the same artifact — no `model.txt` required. After more history or a larger N, re-run with `--force`.

**n_C widths** (why 200×4): see [`docs/measurements/mut2-80x5-click-jinja.json`](docs/measurements/mut2-80x5-click-jinja.json). **Measured step times / timeout rates / LORO:** [`docs/measurements/`](docs/measurements/) (do not invent numbers — open the JSON files). Dry-run hats: [`docs/measurements/bootstrap_time_priors.json`](docs/measurements/bootstrap_time_priors.json).

**Click on Linux:** install **`less`**. Without it, pager tests fail on every commit (persistent, not `new_fail`).

### Mutation bootstrap (low-level; when real `new_fail` is absent)

Historical green `main` histories yield no ranking positives. The CLI above wraps this path. Manual AST mutants: [`docs/specs/2026-11-11-testpilot-mutation-bootstrap-design.md`](docs/specs/2026-11-11-testpilot-mutation-bootstrap-design.md).

```bash
python scripts/mutate_bootstrap.py --repo "$REPO" --db "$DB" --commits 5 --mutants-per-commit 3 --seed 42
testpilot features dump --out pairs.parquet --include-mutants
```

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

**Limitations:** models trained on mutation bootstrap may have near-constant history features (`t_fail_rate`, `t_fail_cnt`, `t_changed`, `t_flaky_rate`, `cov_lag`); if `useful_on_val` is null the CLI warns that the model was validated on synthetic data only. Candidates are pytest nodeids (as in dump/train); parametrized ids from the base run are kept, but newly discovered defs are emitted bare (no param expansion — one bare id may expand to N collected nodeids). **Scanner change (PR #6):** `scan_python` now handles `#` comments, triple-quoted strings in paren continuations, and nested `@` correctly — change/import features that depend on def spans differ from earlier builds. **Retrain** models trained before this fix: re-run `features dump --include-mutants` + `testpilot-train` (no need to regenerate mutants). Design: [`docs/specs/2026-11-13-testpilot-select-design.md`](docs/specs/2026-11-13-testpilot-select-design.md). Latency: `scripts/bench_select.py` (pass **`--no-safe`** for Week-4 parity). Local scan vs collect check: `scripts/check_scan_collect_coverage.py`.

## Safe mode + plugin select + Action (week 5)

Safe guardrails live in Rust `select` (**on by default**). JSON always includes `safe.enabled`. Use `--no-safe` / `--testpilot-no-safe` for research / Week-4 parity. Design: [`docs/specs/2026-11-14-testpilot-safe-mode-ci-design.md`](docs/specs/2026-11-14-testpilot-safe-mode-ci-design.md).

```bash
# CLI (safe on by default)
testpilot select --model "$MODEL" --base HEAD~1 --head HEAD --budget-frac 0.1 --format json
testpilot select ... --no-safe   # Week-4 ranking only

# Pytest: select + record (fail-open if binary/snapshot/model missing)
pytest --testpilot --testpilot-select --testpilot-model "$MODEL" --testpilot-budget-frac 0.1
# Without a local index/snapshot, select always fail-opens unless --testpilot-auto-index
pytest ... --testpilot-auto-index          # default off
pytest ... --testpilot-select-timeout 10   # default 10s
pytest ... --testpilot-shadow              # run all; record would-select
```

Composite Action: [`.github/actions/testpilot/`](.github/actions/testpilot/) — example workflow [`docs/examples/testpilot-ci.yml`](docs/examples/testpilot-ci.yml). On **push to main**: full pytest → `collect git` → `features dump` → save DB cache under `testpilot-db-<repo>-${{ github.sha }}-${{ github.run_attempt }}`. On **PR**: restore cache + `--testpilot-select` (no save). Model is not part of the DB cache.

## Database schema

Source of truth: [`crates/testpilot-core/schema.sql`](crates/testpilot-core/schema.sql) (`SCHEMA_VERSION = 7`). A synced copy ships inside the pytest plugin; `python scripts/sync_schema.py --check` enforces equality in CI.

| Table | Role |
|-------|------|
| `commits` | Commit metadata; `oversized=1` means the diff exceeded `--max-files-per-commit` and **no** `file_changes` were stored |
| `commit_parents` | Parent SHAs (`parent_idx=0` is first parent / merge base for diffs) |
| `file_changes` | Per-commit path changes (`A/M/D/R`), line counts, `is_binary` |
| `test_runs` | One row per pytest/JUnit ingest; `dirty` + `diff_hash` for unclean trees; `input_hash` for JUnit dedup; `synthetic=1` for mutant bootstrap; `partial=1` for successful select subsets (excluded from base_run / dump / recent_fail) |
| `tests` | Dictionary of unique `test_id` strings (`id` referenced elsewhere) |
| `test_results` | Outcomes (`pass/fail/error/skip`), duration, `flaky`, `attempts` (via `test_pk`); deselected tests are **not** written |
| `test_coverage` | Per `(run_id, test_pk, path)` coverage blob (`WITHOUT ROWID`) |
| `test_run_selection` | Per-run select/shadow decisions (`selected`, `reason`, score/rank/budget/model/safe) |

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
| `testpilot select` | Budgeted ranking (`--safe` default on; `--no-safe` for Week-4) |
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
bash tests/e2e/test_safe_e2e.sh
```

## License

MIT © bocchisad
