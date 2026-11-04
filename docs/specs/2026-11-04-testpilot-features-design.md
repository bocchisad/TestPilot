# TestPilot — Features Week Design

**Date:** 2026-11-04  
**Scope:** offline feature vectors for (change, test) pairs — dump / show / schema only (no ML, select, SHAP, or PyO3)

## Goal

Compute a leak-free feature vector for each (change, test) pair using **only data from changes already processed earlier in topological dump order**, and export:

1. `testpilot features dump --out pairs.parquet` — training dataset over history.
2. `testpilot features show --base <rev> [--head <rev>|--worktree] --format table|json|parquet` — features for one change (input to week-4 select). Target: &lt; 300 ms on click (~1300 tests) with a warm DB (measure; do not invent numbers).
3. `testpilot features schema [--check PATH]` — print or verify column lockfile.

`FEATURES_VERSION = 1` is written into Parquet key-value metadata. Column names and order live in one place (`schema.rs`) and are mirrored by committed `features_schema.json`.

## Non-goals (later weeks)

- Training / models / `select` / SHAP
- Graph embeddings (`emb_cc`, `emb_imp`) — rejected
- PyO3
- Implementing `--tests-from FILE` (document only; week 4)

---

## 1. Layout

```
crates/testpilot-core/src/features/
  mod.rs
  schema.rs      # FEATURES_VERSION, ordered columns, dtypes
  change.rs      # srcf/tf, hunks → lines + function qualnames
  history.rs     # incremental test / file / func stats
  cochange.rs    # decaying co-change
  imports.rs     # import graph + BFS + blob cache
  coverage.rs    # last clean coverage ≤ parent lineage
  names.rs       # name tokens
  writer.rs      # arrow + parquet
  python_scan.rs # imports / def / class scanner
scripts/
  features_reference.py
  generate_features_fixture.sh   # fixed GIT_*_DATE; does not commit .git
  backfill.py
  sync_features_schema.py        # optional; or `testpilot features schema --check`
features_schema.json             # committed lockfile
```

**Architecture:** thin CLI, fat core (same as week 1). Git behind `GitRepo`; tests use a fake repo.

---

## 2. Dump semantics

### 2.1 Ordering and “history”

- Walk commits **topo old → new** (author-time tie-break).
- Feature **history** for a change = state accumulated from changes **already emitted earlier in this walk**, not a wall-clock filter `t < t0`.
- Time decay for co-change: `exp(-max(0, t0 - t) / TAU)` with `TAU = 180` days (`t`, `t0` = author times in seconds). The Python reference uses the same formula and the same topo definition of “earlier”.

### 2.2 One change per commit

- For each commit `C` with first parent `P` (skip root / no parent):
  - **Primary run** = last clean (`dirty=0`) run on `C` that has `test_results` (source `pytest` **or** `junit`).
  - That run defines the change’s test rows and labels.
  - Other clean runs on the same `C` are **not** separate changes; they update **flaky statistics only** (when applying post-emit updates).
- **Change** = `diff(P → C)` (first parent only for merges).
- **Label** = 1 iff outcome ∈ `{fail, error}`; skip → 0; `flaky` column from the primary run’s row (flaky does not force label 1).
- JUnit-only primary runs: labels still set; coverage features → `cov_known=0` (and related zeros / defaults).

### 2.3 Emit then update

```
for C in topo_old_to_new:
  if no parent P: continue
  primary = last_clean_run_with_results(C)
  if primary is None: continue
  change = diff(P, C)
  for test in primary.results:
      emit row(features from current counters, change, test, label)
  update counters with primary (+ other clean runs on C for flaky only)
```

A test that updates counters **before** emit must fail the leakage regression.

### 2.4 Coverage

- Source run = last clean run **with at least one `test_coverage` row** on commit `P` or an **ancestor of `P` that was already processed** in the walk.
- The primary run on `C` itself is **never** used as coverage for features of `C` (leakage).
- Line/function overlap uses the **old** side of hunks (coverage was taken on old code).
- **`cov_lag`**: number of commits by which that coverage commit is older than base (`P` for dump). `0` = coverage recorded on base/`P`.

### 2.5 `dur`

- Last known `duration_ms` for the test from a run belonging to a **strictly earlier** processed change.
- If never seen: `-1`.
- **Must not** use the current primary run’s duration.

### 2.6 Output

- Sort rows by `(time, change, test)` for deterministic Parquet bytes.
- Service columns: `repo, change, parent, commit_idx, time, test, label, flaky`.

---

## 3. `show` semantics

- Change = `base..head` or `base` vs worktree (dirty diff).
- History/coverage as of `base` (same rules as dump: coverage on `base` or earlier processed ancestor of base; never “future”).
- **Test list** = tests from the last clean run at `base` or earlier (same selection rule as dump’s “last clean with results”, walking ancestors if needed).
- `--tests-from FILE`: reserved for week 4 (file produced by `pytest --collect-only -q`). Documented in CLI help / README; not implemented now.
- Formats: `table` | `json` | `parquet`.

---

## 4. Feature definitions

Abbreviations: **srcf** = changed `.py` paths that are not tests (basename/path does not mark them as tests: name lacks `test` and path lacks `/tests/`); **tf** = test file path; **t0** = author time of the change commit; **TAU** = 180 days.

| Column | Definition |
|--------|------------|
| `cc_max`, `cc_sum` | Co-change: for each `F` in srcf, over earlier commits that touch `F` (skip commits with &gt;50 files), weight `exp(-max(0,t0-t)/TAU)` for each other file `g`. `cc_max` = max over `F` of weight for `tf`; `cc_sum` = sum over `F`. |
| `imp_dist`, `imp_direct` | Shortest path in import graph from `tf` to any srcf (BFS, maxd=6; none → 99). `imp_direct = (imp_dist==1)`. Graph from sources at **base**/`P`. |
| `name_file`, `name_jac`, `name_func` | Tokens via regex `[A-Za-z][a-z0-9]+|[A-Z]+(?![a-z])|\d+`, `_`→space, lower, len≥3; stopwords `test/tests/src/py/init`. `name_file` = intersection of srcf name tokens with `test_id`+test filename tokens; `name_jac` = \|∩\|/\|srcf tokens\|; `name_func` = some changed function name (len≥3, not `<module>`) appears in test name. |
| `cov_known` … `cov_mod` | From coverage source run (above). `cov_known` = test has any coverage rows; `cov_file` = covers ≥1 srcf; `cov_lines` = count of **old-side** changed lines covered; `cov_func` = count of changed functions with ≥1 covered line; `cov_breadth` = total covered lines by test; `cov_mod` = changed non-executable-by-any-test regions (module/class body, import-time) and `imp_dist < 99`. |
| `cov_lag` | Commits between coverage commit and base (`P`); 0 if coverage on base. |
| `t_fail_rate`, `t_fail_cnt`, `t_obs` | From earlier primary updates: fail+error count as failures; flaky flag does not add a failure by itself. |
| `pf_max` | max over srcf `F` of `fails(F,test)/samples(F)`; no data → `-1`. |
| `pq_max` | Same over changed functions; no data → `-1`. |
| `t_age_days` | Days since first observation of the test (earlier changes). |
| `dur` | Last duration before this change; else `-1`. |
| `tfile_ntests` | Tests observed in the same file (from history). |
| `t_changed` | Test file path changed in this diff. |
| `t_flaky_rate` | Share of prior observations marked flaky (including multi-run-on-commit flaky updates). |
| `ch_nfiles`, `ch_nsrc`, `ch_add`, `ch_rm`, `ch_nfunc` | Change size. |

Changed functions: from hunks + scanner, qualname `path::Class.method` or `path::<module>` outside functions.

---

## 5. Python scanner

Hand-rolled scanner (no tree-sitter). Requirements:

- `import` / `from … import` including parenthesized multi-line and `\` continuations
- Relative imports; package `__init__.py`; src-layout resolution when building the graph
- `def` / `async def` / `class`; decorators included in the function’s line span
- Nested `def`/`class` (stack by indentation)
- Do **not** treat `def`/`class`/`import` inside triple-quoted strings as code
- Imports inside `if TYPE_CHECKING:` and inside `try:` / `except` / `finally` **are counted** (static graph; TYPE_CHECKING and try blocks still declare dependencies)
- Broken / partial syntax: skip bad constructs, never panic

**Unit test per bullet** (plus happy path).

Cache parse results in SQLite:

```sql
CREATE TABLE IF NOT EXISTS python_blob_cache (
  blob_sha TEXT PRIMARY KEY,
  imports_json TEXT NOT NULL,
  defs_json TEXT NOT NULL
);
```

### Schema version 3

- `SCHEMA_VERSION = 3`.
- Migration **2 → 3**: `CREATE TABLE IF NOT EXISTS python_blob_cache …` only. **No** database wipe.
- User-facing “delete `.testpilot`” remains for **v1 → current** only (outdated incompatible layouts). Opening v2 DB upgrades in place to v3.

---

## 6. GitRepo extensions

```text
file_at_rev(sha, path) -> Option<Bytes>
diff_hunks(old_sha, new_sha) -> Vec<Hunk>
  # path, old_path, change_type, old_line_ranges, new_line_ranges, is_binary
```

Fake `GitRepo` for unit/oracle tests. Oversized/binary commits: no text hunks; features that need lines/funcs degrade safely.

---

## 7. CLI

```text
testpilot features dump --out pairs.parquet [--db PATH]
testpilot features show --base REV [--head REV | --worktree] [--format table|json|parquet] [--db PATH]
testpilot features schema [--check features_schema.json]
```

Help text mentions future `--tests-from FILE` (week 4).

---

## 8. Reference oracle and fixture

- Fixture **generated** by `scripts/generate_features_fixture.sh` (or `.py`) with fixed `GIT_AUTHOR_DATE` / `GIT_COMMITTER_DATE`. Generated `.git` is **gitignored / not committed**; CI regenerates into a temp dir.
- Content: 5–8 commits, 2 modules, 4–6 tests, known failures + coverage in DB.
- `scripts/features_reference.py`: naive implementation of §2–§4 over the same DB+git (subprocess git + sqlite3).
- Rust dump vs reference: floats within `1e-5`; row keys `(change, test)` match.
- Leakage test: dump → add future commit/run → dump again → earlier rows unchanged.
- Determinism: two dumps equal after sort (or byte-identical Parquet).
- Edges: root skip, merge (first parent), rename, delete, binary/oversized, no coverage, first-seen test, broken Python file.

---

## 9. backfill.py

```text
scripts/backfill.py --repo PATH --last N --db PATH/to/main/.testpilot/db.sqlite
```

- Create worktree per commit; **venv lives outside the worktree** (avoid dirty from venv files).
- Install project; run `pytest --testpilot --testpilot-db=<main db> --junitxml=...`.
- Recorded `sha` = worktree `HEAD`.
- Install/build failures: log and skip; always remove worktree.
- PR for click `--last 30`: report changes, pairs, **honest positive count** (may be near zero), dump time, `features show` time — measured only.

---

## 10. CI

- New job/step: generate fixture → collect into DB → `testpilot features dump` → compare to reference → `features schema --check`.
- Existing rust / python / wheel / e2e jobs stay green; MSRV 1.85 `--locked`.

---

## 11. Done when

- CI green including oracle + schema check.
- Reference test matches; leakage test fails if counters update before emit.
- Click N=30 backfill + dump/show timings and counts attached to the PR (honest positives).
