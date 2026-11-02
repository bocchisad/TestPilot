# TestPilot — Data Collection Week Design

**Date:** 2026-11-02  
**Scope:** Offline predictive test selection — data collection only (no ML, ranking, or prediction)

## Goal

Build a monorepo that records, for a given code change and test history:

- Git commit graph + per-file diffs (including renames, merges)
- Test outcomes from JUnit XML and live pytest runs
- Per-test line coverage keyed to a test run

All data lands in a local SQLite DB under `<repo>/.testpilot/db.sqlite`. No network calls at runtime.

## Non-goals (later weeks)

- Ranking / predictive models
- Training-set builders (will default-exclude `dirty=1` runs)
- PyO3 shared writer (deferred; Python writes SQLite directly this week)

---

## 1. Repository layout

```
TestPilot/
├── Cargo.toml                      # workspace
├── rust-toolchain.toml
├── crates/
│   ├── testpilot-core/
│   │   ├── schema.sql              # source of truth
│   │   └── src/
│   │       ├── lib.rs
│   │       ├── error.rs
│   │       ├── store.rs            # open/migrate/CRUD, schema_version
│   │       ├── git.rs              # GitRepo trait + GixRepo
│   │       ├── junit.rs            # streaming quick-xml parser
│   │       ├── codec.rs            # coverage lines blob
│   │       └── stats.rs
│   └── testpilot-cli/
│       └── src/main.rs             # clap binary `testpilot`
├── python/pytest-testpilot/
│   ├── pyproject.toml              # Python >=3.10, coverage>=7.4
│   ├── src/pytest_testpilot/
│   │   ├── schema.sql              # synced copy (committed)
│   │   ├── plugin.py
│   │   ├── store.py
│   │   ├── codec.py
│   │   └── gitinfo.py
│   └── tests/
├── scripts/sync_schema.py          # copy + --check
├── tests/
│   ├── fixtures/
│   │   ├── junit/
│   │   ├── coverage_vectors.json
│   │   └── sample_project/
│   └── e2e/
├── .github/workflows/ci.yml
└── README.md
```

### Tooling constraints

| Area | Choice |
|------|--------|
| Rust edition | 2024 + `rust-version` in workspace/`Cargo.toml` |
| Toolchain | `rust-toolchain.toml` |
| gix | no default features; enable only what walk/diff/rename need |
| SQLite | `rusqlite` with `bundled` |
| Errors | `thiserror` in core, `anyhow` in CLI |
| Logging | `tracing` |
| Python | `>=3.10`, `coverage>=7.4`, stdlib `sqlite3` |
| Lint | `cargo fmt` / `clippy -D warnings`, `ruff` for Python |

**Architecture:** thin CLI, fat core. Core modules: `store` / `git` / `junit` / `codec` / `stats`. Git behind a `GitRepo` trait for tests. PyO3 is out of scope this week.

---

## 2. Database schema and migrations

**Source of truth:** `crates/testpilot-core/schema.sql`  
**Distribution:** `scripts/sync_schema.py` copies into `python/pytest-testpilot/src/pytest_testpilot/schema.sql` (committed). CI runs `sync_schema.py --check`. Both Rust and Python verify `schema_version` on open.

**Version:** `schema_version = 1` (single-row table).

### Connection pragmas (both sides)

On every open:

- `PRAGMA journal_mode=WAL;`
- `PRAGMA foreign_keys=ON;`
- `PRAGMA busy_timeout=5000;`

### Tables (v1)

```sql
CREATE TABLE IF NOT EXISTS schema_version (
  version INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS commits (
  sha       TEXT PRIMARY KEY,
  author_ts INTEGER NOT NULL,   -- unix seconds
  message   TEXT NOT NULL,
  oversized INTEGER NOT NULL DEFAULT 0 CHECK (oversized IN (0,1))
  -- oversized=1: commit recorded without file_changes (exceeded --max-files-per-commit)
);

CREATE TABLE IF NOT EXISTS commit_parents (
  sha        TEXT NOT NULL REFERENCES commits(sha),
  parent_sha TEXT NOT NULL,
  parent_idx INTEGER NOT NULL,  -- 0 = first parent (merge base for diffs)
  PRIMARY KEY (sha, parent_idx)
);

CREATE TABLE IF NOT EXISTS file_changes (
  sha           TEXT NOT NULL REFERENCES commits(sha),
  path          TEXT NOT NULL,
  change_type   TEXT NOT NULL CHECK (change_type IN ('A','M','D','R')),
  old_path      TEXT,            -- set only for R
  lines_added   INTEGER NOT NULL DEFAULT 0,
  lines_deleted INTEGER NOT NULL DEFAULT 0,
  is_binary     INTEGER NOT NULL DEFAULT 0 CHECK (is_binary IN (0,1)),
  PRIMARY KEY (sha, path)
);

CREATE TABLE IF NOT EXISTS test_runs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  sha         TEXT NOT NULL,
  source      TEXT NOT NULL CHECK (source IN ('junit','pytest')),
  started_at  INTEGER NOT NULL,  -- unix seconds
  finished_at INTEGER,
  dirty       INTEGER NOT NULL DEFAULT 0 CHECK (dirty IN (0,1)),
  diff_hash   TEXT,              -- sha256 of dirty tree fingerprint; NULL if clean
  input_hash  TEXT,              -- junit ingest dedup; NULL for pytest
  meta        TEXT               -- JSON object (optional extras; not used for dedup)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_test_runs_dedup
  ON test_runs(source, sha, input_hash)
  WHERE input_hash IS NOT NULL;

CREATE TABLE IF NOT EXISTS test_results (
  run_id      INTEGER NOT NULL REFERENCES test_runs(id),
  test_id     TEXT NOT NULL,
  outcome     TEXT NOT NULL CHECK (outcome IN ('pass','fail','error','skip')),
  duration_ms INTEGER NOT NULL,
  message     TEXT,
  flaky       INTEGER NOT NULL DEFAULT 0 CHECK (flaky IN (0,1)),
  attempts    INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (run_id, test_id)
);

CREATE TABLE IF NOT EXISTS test_coverage (
  run_id  INTEGER NOT NULL REFERENCES test_runs(id),
  test_id TEXT NOT NULL,
  path    TEXT NOT NULL,
  lines   BLOB NOT NULL,
  PRIMARY KEY (run_id, test_id, path)
);

CREATE INDEX IF NOT EXISTS idx_file_changes_path ON file_changes(path);
CREATE INDEX IF NOT EXISTS idx_test_results_test_id ON test_results(test_id);
CREATE INDEX IF NOT EXISTS idx_test_runs_sha ON test_runs(sha);
CREATE INDEX IF NOT EXISTS idx_test_coverage_path ON test_coverage(path);
```

### Migration policy

- Apply `schema.sql` inside a single transaction.
- All DDL uses `IF NOT EXISTS`.
- Empty DB → apply schema, `INSERT INTO schema_version(version) VALUES (1)`.
- Existing DB with `version != 1` → hard error: unsupported schema_version (no auto-upgrade this week).

### Semantics notes

- Coverage is keyed by `run_id`, not commit sha (a sha may have multiple runs).
- Dirty runs: `dirty=1` when the working tree is not clean (including untracked files). `diff_hash` is sha256 of a fingerprint that includes tracked diff and untracked content; `NULL` when clean. Future training defaults to `WHERE dirty = 0`.
- `meta`: JSON text for optional fields (`--meta` on junit collect, plugin extras). Dedup uses `input_hash`, not `meta`.
- `commits.oversized=1`: commit stored with parents but **no** `file_changes` rows because the diff exceeded `--max-files-per-commit`.

---

## 3. Coverage codec

Shared format (Rust `codec.rs`, Python `codec.py`), verified by `tests/fixtures/coverage_vectors.json`.

| Field | Encoding |
|-------|----------|
| Version byte | `0x01` (first byte always) |
| Line numbers | sorted unique `u32` → delta-encode → protobuf-style unsigned varint |
| Empty coverage | exactly `[0x01]` |

CLI: `testpilot debug coverage --run-id <id> --test-id <id> [--path <path>]`  
If `--sha` is passed instead of `--run-id`, resolve to the latest `test_runs.id` for that sha.

---

## 4. Git collector

### Trait

```rust
/// Opaque hex commit id — never expose gix::Oid on the trait boundary.
struct Sha(String);

trait GitRepo {
    fn rev_parse(&self, rev: &str) -> Result<Sha>;
    fn walk_topo_old_to_new(
        &self,
        since_sha: Option<&str>,
        since_date: Option<i64>,
        max: Option<usize>,
    ) -> Result<Vec<CommitMeta>>;
    fn diff_against_first_parent(&self, sha: &Sha) -> Result<Vec<FileChange>>;
}
```

- Production impl: `GixRepo` (gix, no default features). `gix` types stay inside `GixRepo` only.
- Tests may use a fake or real temp repos via the trait / integration harness.

### `testpilot collect git`

Flags:

- `--since-sha <sha>`
- `--since-date <RFC3339|YYYY-MM-DD>` (filter by `author_ts`)
- `--max N`
- `--max-files-per-commit 5000` (default 5000)
- Global `--db PATH`

Algorithm:

1. Open DB, check `schema_version`, set pragmas.
2. Full topological walk, oldest → newest.
3. Process in batches of 200 commits, each batch one transaction.
4. Per commit: `INSERT OR IGNORE` into `commits`. If the row was not newly inserted (already known), skip parents/file_changes for that sha. Only on a fresh insert: write `commit_parents` + (optionally) `file_changes`.
5. If a commit’s diff has more than `--max-files-per-commit` files: insert the commit + parents with `oversized=1`, write **no** `file_changes`, log a warning. Do not truncate the file list.
6. Idempotent: re-run and `--max` then full collect must not duplicate rows (integration-tested).
7. Merge commits: diff against first parent (`parent_idx = 0`).
8. Root commit: diff against empty tree.
9. Rename detection via gix; `change_type` in `A|M|D|R`; `old_path` only for `R`; line counts from hunks; `is_binary=1` for binary diffs (line counts 0).
10. No shell-out to `git` in Rust.

---

## 5. JUnit collector and CLI surface

### Parser (`junit.rs`, `quick-xml` streaming)

- Nested `testsuites` / `testsuite` / `testcase`.
- Outcomes: `failure`→fail, `error`→error, `skipped`/`skip`→skip, else pass.
- `time` attribute (seconds) → `duration_ms`; **missing `time` → `duration_ms = 0`**.
- Failure/error body → `message`, truncated to 4 KiB.
- `test_id` normalization:
  - pytest-style (path / `::` hints, or `file` attribute when present) → `path/to/test_file.py::Class::test_name[param]`
  - Java/Surefire/Gradle → `package.Class#method`
- Fixtures: pytest, surefire, gradle, nested, malformed (malformed → `Err`, no panic).

### `testpilot collect junit <glob> --sha <sha>`

Additional flags:

- `--started-at <unix|RFC3339>` (optional; default = `timestamp` attribute from the root/first `testsuite`/`testsuites` in XML if present, else now)
- `--meta <json>` (optional; stored on `test_runs.meta`)
- `--force` — bypass input dedup

**Dedup:** `input_hash` = sha256 of concatenated sorted matched file contents (plus fixed source marker). Stored in `test_runs.input_hash`. Unique index `(source, sha, input_hash)` WHERE `input_hash IS NOT NULL`. On conflict without `--force`, skip with a log line.

**Reruns in one XML / multiple results for same `test_id`:** keep last attempt’s outcome/duration/message; set `attempts` to count; set `flaky=1` if any two attempts disagree on outcome.

One `test_runs` row (`source=junit`, `dirty=0` unless later extended), all results in one transaction.

### Other CLI commands

| Command | Behavior |
|---------|----------|
| `init` | Create `.testpilot/`, migrate DB, ensure `.testpilot/` in `.gitignore` |
| `stats [--json]` | Counts: commits, runs, distinct tests, failures; top-10 slowest; top-10 most failing. `--json` for machine output |
| `debug coverage` | Decode blobs (see §3) |

Global: `--db`, `-v`/`-vv` (tracing). Exit 1 on error. No network.

---

## 6. pytest plugin (`pytest-testpilot`)

### Flags

| Flag | Meaning |
|------|---------|
| `--testpilot` | Enable collection |
| `--testpilot-db PATH` | Default `.testpilot/db.sqlite` |
| `--testpilot-strict` | Plugin/store errors fail the pytest run; otherwise warn and continue |

### Lifecycle (non-xdist)

1. `pytest_configure`: open DB, verify schema, configure coverage.py with dynamic contexts; `switch_context` in `pytest_runtest_setup` so context = test nodeid. Compatible with pytest-cov (cooperate when both enabled — tested explicitly).
2. Record per-test outcome; `duration_ms` = setup + call only (exclude teardown).
3. `test_id`: prefer path from test item `path`/`fspath` made repo-relative, plus `::` parts from nodeid (not absolute paths).
4. Coverage read once at session end via `CoverageData.contexts_by_lineno()` / `lines(file, contexts=...)` — never `coverage json`. Keep only files under repo root; store relative paths; encode with shared codec.
5. `gitinfo` (shell `git` OK in Python plugin only):
   - sha = `git rev-parse HEAD`
   - dirty if `git status --porcelain` non-empty (untracked counts)
   - `diff_hash` = sha256 over tracked diff and untracked file contents fingerprint; `NULL` if clean
6. Write one `test_runs` + results + coverage in a single transaction; set `finished_at`.

### Reruns

Same `test_id` multiple times in a session → store last attempt; `attempts=N`; `flaky=1` if outcomes differed.

### xdist

- Primary: workers put results+coverage payload on `workeroutput`; master merges in `pytest_testnodedown` / session finish; one DB transaction.
- Fallback: JSONL under `.testpilot/tmp/`, merge, then cleanup.

### Failure policy

Plugin exceptions → warning + continue, unless `--testpilot-strict`.

### Overhead

Do not assert overhead thresholds inside unit tests. Measure in a dedicated e2e bench step and print timings (session wall, coverage collect time).

### Schema sync

Plugin loads its packaged `schema.sql` copy; CI ensures it matches core via `--check`.

---

## 7. Testing

### Rust

- JUnit fixtures: pytest, surefire, gradle, nested, malformed.
- Codec roundtrip + shared `coverage_vectors.json`.
- Integration: temp git repo with ≥5 commits (add, modify, rename, merge, delete) → `collect git` → table asserts; second collect no duplicates; `--max` then full collect increments correctly.
- Store: migration idempotency; wrong `schema_version` errors.

### Python

- pytester: 3 tests pass/fail/skip → outcomes, `duration_ms > 0`, per-test coverage isolation (own lines present, foreign lines absent).
- Codec vs shared vectors.
- pytest-cov compatibility smoke.
- untracked file → `dirty=1` and non-NULL `diff_hash`.
- rerun → `flaky` / `attempts` correct.
- xdist smoke (2 workers) when plugin available.
- `--testpilot-strict` vs default error swallowing.

### E2E

- `tests/fixtures/sample_project/`: `testpilot init && pytest --testpilot && testpilot collect git && testpilot stats` → non-zero stats.
- **test_id consistency:** on `sample_project`, run `pytest --junitxml=...` and `pytest --testpilot`, then `testpilot collect junit`; the set of `test_id` values from the junit ingest and the pytest ingest must be equal.
- Separate overhead bench output (no hard CI fail on ms).

### Perf target (documented; optional manual/local)

- Real repo (e.g. pallets/click): `collect git` for ~500 commits < 5s.
- CI uses a synthetic repo (~50–100 commits) and logs timing; full 500-commit gate is README/manual unless made a scheduled job later.

---

## 8. CI (`.github/workflows/ci.yml`)

Runner: `ubuntu-latest`.

1. Checkout; install from `rust-toolchain.toml`.
2. Rust cache + pip cache.
3. `cargo fmt --check`
4. `cargo clippy -- -D warnings`
5. `cargo test`
6. Python matrix: 3.10, 3.13 — install editable plugin + dev deps, `ruff check`, `pytest`
7. `python scripts/sync_schema.py --check`
8. E2E on `sample_project` (+ overhead bench print)

---

## 9. README

Must include:

- Quickstart: `init` → `pytest --testpilot` → `collect git` → `stats`
- DB schema summary (tables, codec, dirty/`diff_hash` semantics)
- Explicit note: no ML this week; dirty runs excluded from future training by default

---

## 10. Open TODOs (do not over-engineer)

- PyO3 shared writer — later week.
- Training-set filter `dirty=0` — later week.
- Schema v2+ migrations framework — only when needed.

---

## Acceptance checklist

- [ ] `cargo test`, `cargo clippy -- -D warnings`, `pytest`, `ruff` green on ubuntu-latest CI
- [ ] Schema sync `--check` green; both sides enforce `schema_version`
- [ ] JUnit + git integration tests + pytester coverage isolation + cov/untracked/rerun cases
- [ ] E2E sample project produces non-zero `stats`
- [ ] README Quickstart + schema docs
- [ ] No ML / prediction code
