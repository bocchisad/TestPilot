# Mutation Bootstrap — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans`. Checkbox steps for tracking.

**Goal:** Bootstrap `new_fail` labels via AST mutants as real git commits, dump them as leaf rows without history leakage (`FEATURES_VERSION=2` + `test_runs.synthetic`), and train with a clustered synthetic useful-gate.

**Architecture:** Mutate → commit under `refs/testpilot/mutants/<C>/<i>` with `author(C)+i` / `committer(C)+i` → junit-only pytest (`synthetic=1` run) → `features dump --include-mutants` emits rows from post-C snapshot without updating HistoryState/cochange → `testpilot-train` metrics/gate on `synthetic==1` with cluster bootstrap by `base_change`.

**Tech Stack:** Python 3.10+ (stdlib `ast`), existing TestPilot CLI/DB, polars/LightGBM train package, pytest+junit.

**Spec:** [`2026-11-11-testpilot-mutation-bootstrap-design.md`](2026-11-11-testpilot-mutation-bootstrap-design.md) (incl. **M1–M8**)

## Global Constraints

- Base: week-3 training branch / `main` after PR #3.
- Mutants never update HistoryState / CoChangeState / cov tracker.
- Mutant runs never feed real query paths (`test_runs.synthetic=1`).
- Model features stay 29; schema v2 adds service `synthetic`, `base_change` only.
- No parallel pytest writers to one SQLite.
- Every PR number cited with its command.
- README: require `less` for click pager tests (Linux).

---

## File map

| Path | Role |
|------|------|
| `scripts/mutate_bootstrap.py` (+ `scripts/mutate/`) | sample C, AST ops, git commit/ref, pytest+junit, stats |
| `crates/testpilot-core/schema.sql` + migrate | `test_runs.synthetic` |
| `crates/testpilot-core/src/store.rs` | exclude synthetic from all real run queries |
| `crates/testpilot-core/src/features/schema.rs` | `FEATURES_VERSION=2`, new columns |
| `features_schema.json` | mirror |
| `crates/testpilot-core/src/features/dump.rs` | `--include-mutants` leaf pass |
| `crates/testpilot-cli` | dump flag; single-SHA collect; junit synthetic |
| `python/testpilot-train/...` | load v2, unique-idx split, clusters, synthetic gate, M7 assert |
| `tests/fixtures/` | mutate CI fixture with ≥1 killable site |
| `README.md` | `less` note |
| `.github/workflows/ci.yml` | mutant tests in train-oracle budget |

---

### Task 1: Schema v2 + DB `test_runs.synthetic` (M2, M7)

**Files:** `schema.rs`, `features_schema.json`, `schema.sql`, migrate, `store.rs`, oracle, `synth.py`, train schema tests.

- [ ] Parquet: add `synthetic` (int64), `base_change` (utf8) after `flaky`; `FEATURES_VERSION=2`.
- [ ] Real dump rows: `synthetic=0`, `base_change=""`.
- [ ] DB: additive `test_runs.synthetic INTEGER NOT NULL DEFAULT 0`; bump schema_version.
- [ ] Update **all** “last clean run” / coverage / other_clean_runs / show / history queries to `AND synthetic=0` (or equivalent).
- [ ] Junit/pytest ingest: accept `synthetic` flag (default 0); mutate path sets 1.
- [ ] Train: assert model matrix is exactly 29 feature cols (never service `synthetic`/`base_change`).
- [ ] Reference predictions: after v2, same 29 cols → `raw_score` unchanged (rebuild pairs only if needed; verify scores).
- [ ] Commit: `Bump features v2 and exclude synthetic runs from real queries.`

### Task 2: AST operators + compile skip + dedup (M6)

**Files:** `scripts/mutate/ast_ops.py`, unit tests.

- [ ] Operators from spec §1.2; sites on hunk lines; `ast.unparse` edited node only; stitch bytes.
- [ ] Invalid compile → skip + `n_invalid`.
- [ ] Dedup by `(operator, node)` and by resulting diff within one C.
- [ ] Commit: `Add AST mutation operators with compile-skip and dedup.`

### Task 3: Git mutant commits + single-SHA collect (M1)

**Files:** `scripts/mutate/git_mutant.py`, collect single-SHA path, tests.

- [ ] Commit `M` parent=`C`; `author(C)+i` / `committer(C)+i`; ref `refs/testpilot/mutants/<C>/<i>`.
- [ ] Explicit single-SHA collect into DB.
- [ ] Determinism: same seed → same M SHAs twice.
- [ ] Commit: `Commit mutants with C+i timestamps and single-SHA collect.`

### Task 4: Mutant pytest — timeout, flaky, synthetic run (M4, M5)

**Files:** `scripts/mutate/run_mutant.py`, sample eligible C (M6).

- [ ] Eligible C = src `.py` in hunks; report `n_eligible_C`.
- [ ] Worktree pytest **without** coverage; ingest junit with `synthetic=1`.
- [ ] Default timeout `max(60, 3×C_duration_sec)`; kill process group; on timeout drop ref/results + `n_timeout`.
- [ ] Rerun failed tests once; pass → `flaky=1`; `n_flaky_rerun`.
- [ ] Kill rate by operator in report.
- [ ] CLI: `--commits N --mutants-per-commit K --seed [--timeout-sec]`.
- [ ] Commit: `Run mutant pytest with synthetic runs, timeout, and flaky rerun.`

### Task 5: Dump leaf pass + leakage (M1, M2)

**Files:** `dump.rs`, CLI `--include-mutants`.

- [ ] Phase B: load snapshot at C; emit; no history/cochange/cov/snapshot updates; `synthetic=1`, `base_change=C`, `commit_idx` of C.
- [ ] Leakage: DB-with-mutants dump (no flag) **byte-identical** to DB-without-mutants; Phase A snapshots identical; `show` identical.
- [ ] M1: time features on M match C for unaffected tests.
- [ ] Commit: `Emit mutant feature leaves and harden leakage tests.`

### Task 6: Train — clusters, gate, split (M3, M7, M8)

**Files:** `data.py`, `split.py`, `train_lib.py`, `report.py`, `metrics.py`, `baselines.py`.

- [ ] Unique-`commit_idx` chrono split; **M8** test: without mutants ≡ old change-count split.
- [ ] Cluster bootstrap: synth by `base_change`, real by `change`; paired gate uses clusters.
- [ ] `data_insufficient` if synthetic test has &lt;20 kill-mutants **or** &lt;10 distinct C.
- [ ] Metrics: synthetic val/test + real; `useful_on_synthetic_val`; highlight `cov_lines`.
- [ ] M7 feature-matrix assert in load/train.
- [ ] Commit: `Train on clustered synthetic new_fail with unique-idx split.`

### Task 7: Timing + N×K + README less

- [x] README: install **`less`** for click pager tests on Linux.
- [ ] Measure click `5×3` (needs click clone + backfill DB); cite `/usr/bin/time -p` + `--json` stats.
- [ ] Propose `N×K` for M3 (≥20 kill-mutants ∧ ≥10 distinct C in test); paste commands+counts in PR.

### Task 8: CI fixture coverage

- [x] Mutate unit tests (determinism, invalid skip, M2 dump leakage) in train-oracle job.
- [x] Train: M7 feature-matrix gate, M8 split regression, clustered metrics.
- [ ] Optional: fixture end-to-end ≥1 `new_fail` mutant (expand sample_project if needed).
- [x] Keep train-oracle budget (&lt;60s) with mutate tests included.

---

## Out of scope

Rust `select`, SHAP, mutating tests, multi-DB parallel runners, claiming usefulness on real click/jinja green history.

---

## Plan ↔ design cross-check

- [x] M1 timestamps C+i; time-feature test
- [x] M2 `test_runs.synthetic`; DB-with vs without leakage
- [x] M3 cluster bootstrap; ≥20 ∧ ≥10 C
- [x] M4 flaky rerun
- [x] M5 timeout rule + process group
- [x] M6 eligible C; dedup; kill rate by op
- [x] M7 29-col gate; reference scores
- [x] M8 split regression test
- [x] Leaf dump; single-SHA collect; no-cov; cov_lines; README less

## Approval

M1–M8 approved 2026-11-11 — execute Tasks 1–8.
