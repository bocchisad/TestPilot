# TestPilot — Week 4 `select` Design

**Date:** 2026-11-13  
**Base:** `main` after PR #2–#5 (`fa12d97`)  
**Amendments:** A1–A10 (approved 2026-11-13)  
**Scope:** Rust `testpilot select` — pure-Rust LightGBM `model.txt` inference + change feature vectors + budgeted ranking. No Python at runtime.  
**Non-goals (week 5+):** safe mode, pytest-plugin integration, GitHub Action, SHAP, embeddings, GPU, online learning.

## Goal

Given a trained artifact from `testpilot-train` (`model.txt` + `model.json` + optional reference preds), a local SQLite DB with `feature_history_snapshot` rows, and a change `base..head` (or worktree), emit a **deterministic ranked subset** of tests under a budget — using the **same 29 features** and **same raw scores** as LightGBM `predict(raw_score=True)` (parity ≤ `1e-6`).

## Context (locked)

| Item | Value |
|------|--------|
| `FEATURES_VERSION` | `2` |
| Model features | exactly **29** names, order locked in embedded `features_schema.json` |
| Service columns (never scored) | `repo, change, parent, commit_idx, time, test, label, flaky, synthetic, base_change` |
| Objectives | `binary` **or** `lambdarank` — both emit a single raw margin; `num_class=1`, `num_tree_per_iteration=1` |
| Snapshot | zstd + postcard HistoryState/CoChangeState, schema **v6** |
| Synthetic runs | excluded from all real history / coverage / candidate-base queries (as dump) |
| Import graph (Phase B / change) | **seeded** at the **head** (or worktree overlay): seeds = changed sources + candidate test files + hunk `.py` paths |
| Schema check | **embedded** in the binary (`include_bytes!` of repo-root `features_schema.json`); no CLI `--features-schema` |

**Honest limitations (document in README):** production models may be trained on mutation bootstrap. Features `t_fail_rate`, `t_fail_cnt`, `t_changed`, `t_flaky_rate`, `cov_lag` were near-constant on that training set → near-zero gain. Real useful-gate remains `data_insufficient` when val has no real `new_fail`. If `model.json` has `useful_on_val: null` / `useful_on_real_val` null (A10), emit one stderr warning.

---

## 1. Architecture

```text
                    model.json ──► validate vs embedded schema / objective / best_iteration
                    model.txt  ──► Booster (pure Rust)
                                         │
base, head|worktree, db ──► compute_change_rows()  ◄── SAME CODE as features show/dump leaf
                                         │              (snapshot@base, hunks base..head,
                                         │               graph seeded @ head)
                                         ▼
                              Vec<(test_id, FeatureRow[29])>
                                         │
                                         ▼
                              predict_raw → score per test
                                         │
                                         ▼
                              sort (score↓, test_id↑) → apply budget + force-include
                                         │
                                         ▼
                              json | table | lines
```

**Crate layout:**

| Path | Role |
|------|------|
| `crates/testpilot-core/src/lgbm/` | parse `model.txt`, validate vs `model.json` + embedded schema, `predict_raw` |
| `crates/testpilot-core/src/features/change_rows.rs` (or refactor `dump.rs`) | **one** `compute_change_rows` used by `show`, dump leaf helpers, and `select` |
| `crates/testpilot-core/src/select.rs` | candidates, scoring, budgets, output structs |
| `crates/testpilot-cli` | `testpilot select …` |
| `scripts/bench_select.py` | warm-up + 10 timed runs → median/p90 |
| `scripts/gen_lgbm_parity_csv.py` | regenerate exact-f64 parity CSV from LightGBM |
| `tests/` / crate tests | parser parity, feature parity vs dump, budgets, determinism, synthetic isolation, dump regression |

No new runtime Python deps. No `lightgbm-sys` / FFI.

---

## 2. LightGBM parser (pure Rust)

### 2.1 Load contract

Directory `--model DIR` must contain:

```text
DIR/model.txt
DIR/model.json
```

**`model.json` gates (hard error, non-zero exit):**

1. `features_version` == embedded schema `features_version` (**2**).
2. `schema_sha256` == SHA-256 of the **exact bytes** of the schema file **embedded in the binary** (`include_bytes!("…/features_schema.json")`). No `--features-schema` flag; no cwd lookup (A3).
3. `feature_names` length **29**, identical order to the embedded schema.
4. `objective` ∈ {`binary`, `lambdarank`} (string may be `binary` or start with `binary `; lambdarank similarly). Reject `multiclass`, `xentropy`, etc.
5. `constraints.categorical == false` (or absent + model has `num_cat=0` everywhere).
6. `constraints.linear_tree == false` (and every tree `is_linear=0`).
7. `predict_raw_score == true` (if field present; if absent, still predict raw margins and warn once on stderr).
8. `best_iteration` (positive int): use **only the first `best_iteration` trees**. If `best_iteration >` number of trees in `model.txt` → **hard error** (A6).
9. A10: if `useful_on_val` is JSON `null` **or** the payload uses label `useful_on_real_val` with null (check both `useful_on_val` and any explicit null real-gate field) → print **one** stderr line: `warning: model validated on synthetic only` (do not fail).

**`model.txt` gates:**

- Header: `num_class=1`, `num_tree_per_iteration=1`.
- `feature_names` line matches `model.json` names (space-separated).
- Every tree: `num_cat=0`; reject any categorical split (`decision_type` categorical bit).
- Reject `is_linear=1`.
- Support LightGBM text format **v4** (as emitted by LightGBM 4.5.x used in train).

### 2.2 `decision_type` semantics

Each internal node carries a LightGBM `decision_type` **bitmask** (u8). Supported:

| Field | Meaning |
|-------|---------|
| categorical bit | must be **0** (reject otherwise) |
| missing_type | `None` / `Zero` / `NaN` (bits as in LightGBM 4.x) |
| default_left | missing goes left if set; else right |

Numeric split: go **left** if `fval ≤ threshold` (LightGBM `<=` convention). Missing handling follows `missing_type` + `default_left` exactly as LightGBM’s C++ predictor for `raw_score`.

`use_missing=true`, `zero_as_missing=false` (train constraints): zeros are real values; only true missing/NaN use the missing path. Our feature matrix has **no NaN** (dump rejects NaN); sentinels are ordinary numbers (`-1`, `99`). Parser must still implement missing paths for fidelity / future-proofing.

### 2.3 Prediction

```text
score(x) = sum_{t=0..best_iteration-1} tree_t(x)
```

Same as LightGBM `predict(..., raw_score=True)` for binary and lambdarank (both single-score). T1 must use the **same** `num_iteration` / `best_iteration` that produced `reference_predictions` (recorded in `model.json`; fix the fixture if missing) (A6).

### 2.4 Parity test (A7)

Primary fixture for CI: **CSV with exact f64** (Python `repr` or hex float) generated by `scripts/gen_lgbm_parity_csv.py` from the reference model + `pairs.parquet`, committed under `tests/fixtures/reference_model/`.

Optional: keep reading parquet only behind a **dev-dependency** if useful for local checks; CI gate uses the CSV.

1. Load booster in Rust with `best_iteration` from `model.json`.
2. For every CSV row, take the 29 features in schema order; compare to `raw_score`.
3. Assert `max_i |rust_i − ref_i| ≤ 1e-6` over **all** rows; print `max_abs_err` and `n`.

Additional unit tests: reject categorical / `linear_tree` / wrong `schema_sha256` (vs embedded) / wrong feature order / multiclass / `best_iteration` too large.

---

## 3. Feature vectors — one code path with dump/show

### 3.1 Shared API

```rust
pub fn compute_change_rows<R: GitRepo>(
    repo: &R,
    store: &Store,
    repo_name: &str,
    base: &Sha,
    head: &Sha,                 // HEAD sha when --worktree (for metadata / time)
    hunks: &[DiffHunk],         // base..head OR base..worktree
    opts: ChangeRowsOpts,
) -> Result<Vec<FeatureRow>>
```

**Refactor:** today’s `compute_show_rows` becomes a thin wrapper around `compute_change_rows`. Dump’s per-commit emit stays on the walk, but **import-graph seeding and `emit_row` inputs** for a single change must call the same helpers (no duplicated seed/graph logic).

**A5 dump regression:** after the refactor, a fixture test asserts that a real-only dump (with and without `--include-mutants` on a DB that has mutants) is **byte-identical** to a golden parquet captured **before** the refactor (committed fixture). PR body also reports click dump row count + sha256 of the parquet (local command).

### 3.2 Snapshot + history

- Load `feature_history_snapshot` at `base` (exact). If missing: hard error (A9):  
  `no feature snapshot for {base}; run: testpilot collect git && testpilot features dump`  
  **No silent first-parent rebuild.**
- Coverage / history queries: **non-synthetic only**.
- Coverage source + lag rules: identical to `features show` / dump.

### 3.3 Import graph (locked)

For change `base..head` (or worktree):

1. Resolve **file bytes for scanning** from **head** when `--head <rev>`; from **worktree filesystem** when `--worktree`. Module **index** = all `.py` paths at that same view.
2. Seeds = `srcf` ∪ test-file paths of every candidate `test_id` ∪ hunk paths ending in `.py`.
3. `build_import_graph_seeded(…)` — same helper as Phase B mutant dump.

**Parity note:** `features show --base P --head H` must match dump rows for change `H` on the 29 features for shared tests. If today’s show graphs at `base`, change it to **head**.

### 3.4 Worktree (A4)

| Flag | Diff | File bytes | Author/time for `time` / `t_age_days` |
|------|------|------------|----------------------------------------|
| `--head REV` | `diff_hunks(base, REV)` | at `REV` | author time of `REV` |
| `--worktree` / `--head WORKTREE` | `diff_worktree(base)` | worktree FS | author time of **`HEAD` commit**, **not** wall clock |

JSON: `head: "WORKTREE"` in worktree mode. Ranking does not use service fields. **T5 (determinism) must also run in `--worktree` mode.**

### 3.5 Candidate tests

**Base set:** test_ids from the last **clean, non-synthetic** results run at `base` or earlier (same ancestor walk as show/dump). Record that run’s commit SHA as `base_run_sha` (A8). If the first-parent distance from `base_run_sha` to `base` is **> 20** commits, print one stderr warning (still succeed).

**Present on head (A2):** drop any base-set test that no longer exists on head/worktree:

- If the test’s file path is **deleted** in the diff → drop all tests from that file.
- Else scan the file at head/worktree with `scan_python`; keep the test only if its def still exists (match nodeid ↔ def).  
  Test: delete one test in a commit → it must not appear in select output.

**Diff-discovered set (new tests):**

1. For each hunk path with `is_test_path` + `.py` that exists on head:
2. `scan_python` → defs with leaf name `test_*`; map to pytest-style nodeids (match plugin / DB strings).
3. Any such id **not** in the (filtered) base set is a **new** candidate.
4. No parametrize expansion in week 4.

**Union** = (base set minus deleted) ∪ new defs. Dedup by `test_id`.

### 3.5.1 Force-include (A1) — **not** whole-file

A candidate is force-included (`reason = "new_or_changed"`) iff **either**:

1. **New:** `test_id` absent from the base run set, **or**
2. **Touched def:** the def’s line span on **head** intersects at least one changed line from the hunks for that file (new-side and/or old-side mapping as in dump’s changed-func logic).

**Not** force-included merely because the test file path appears in hunks.  
**Test:** edit one function in a file with 100 tests → `n_forced == 1`.

Force-include is always present in the selected output even if over budget. Remaining slots filled with `reason = "ranked"`. JSON includes **`n_forced`** (A1).

New/touched tests without history use the **same sentinels as dump**.

### 3.6 Empty diff

If `hunks` is empty: select **0** tests, stderr warning (`empty diff; selecting 0 tests`), exit **0**. Skip scoring; JSON: `n_candidates=0`, `n_selected=0`, `n_forced=0`, `tests=[]`.

---

## 4. CLI

```text
testpilot select
  --repo PATH                 # default: cwd git root
  --db PATH                   # default: <repo>/.testpilot/db.sqlite
  --model DIR                 # model.txt + model.json
  --base <rev>
  --head <rev|WORKTREE>       # XOR with --worktree; WORKTREE ⇒ worktree mode
  [--worktree]                # XOR with --head rev
  --format json|table|lines   # default: lines
  exactly one of:
    --budget-frac F           # 0 < F ≤ 1; k = max(1, ceil(F * n_candidates))
    --max-tests N             # N ≥ 1; error if N == 0
    --time-budget SECONDS     # SECONDS > 0
```

No `--features-schema` (A3).

Exit codes: success (incl. empty diff) → 0; validation / missing snapshot / bad model / unknown rev / `N==0` / bad F / missing|duplicate budget → ≠0.

### 4.1 Ranking order

```text
force = { candidates matching A1 }
rankable = all candidates sorted by (score↓, test_id↑)
selected = sorted(force) then fill from rankable \ selected under budget
final print order = selected sorted by (score↓, test_id↑)
```

**Budget accounting:**

- `--max-tests N`: fill until `len(selected) >= N`, but if `|force| > N`, still include all force (may exceed N).
- `--budget-frac F`: `k = max(1, ceil(F * |candidates|))`; same as `N=k`.
- `--time-budget S`: after placing force, walk remaining by score; add if `sum(dur_sec)+dur_i ≤ S` **or** selected empty (always ≥1 when candidates exist). `dur == -1` → median known `dur_ms/1000` among candidates with `dur≥0`, else **1.0 s**.

### 4.2 Output

**`lines`:** one `test_id` per line, final sort `(score↓, test_id↑)`.  
**`table`:** `rank score dur reason test_id`.  
**`json`:**

```json
{
  "model_sha256": "<sha256 of model.txt bytes>",
  "features_version": 2,
  "base": "<resolved sha>",
  "head": "<resolved sha|WORKTREE>",
  "base_run_sha": "<sha of last clean run used for candidates>",
  "n_candidates": 0,
  "n_selected": 0,
  "n_forced": 0,
  "budget": {"kind": "frac|count|time", "value": 0.1},
  "tests": [
    {"test_id": "…", "score": 0.0, "rank": 1, "dur": -1, "reason": "ranked|new_or_changed"}
  ]
}
```

`dur` is the feature value (ms, sentinel `-1`). Determinism: two runs → byte-identical `json`/`lines` (also under `--worktree`, A4/T5).

---

## 5. Performance

**Target:** `select --base HEAD~1 --head HEAD` on click (~2.2k tests), release, model load included, **median &lt; 300 ms** over 10 timed runs after 1 warm-up.

`scripts/bench_select.py` prints `median_ms`, `p90_ms`, `n`. PR reports local click numbers.

---

## 6. Tests (acceptance)

| ID | Assert |
|----|--------|
| T1 | Parser parity vs exact-f64 CSV; `best_iteration` as in `model.json`; max abs err ≤ 1e-6 |
| T2 | Reject categorical / linear_tree / bad schema_sha256 vs embedded / multiclass / `best_iteration` too large |
| T3 | Feature parity: fixture (+ optional click) — 29 values exact vs dump for shared tests |
| T4 | Budgets: `F=1.0`; `N=0` error; `N>n`; time-budget &lt; first still selects 1; `dur=-1` |
| T5 | Determinism for `lines`/`json` **and** `--worktree` |
| T6 | Missing snapshot → ≠0 + A9 hint (`collect git` && `features dump`) |
| T7 | Synthetic isolation: DB with/without mutants → identical select for real change |
| T8 | Empty diff → 0 selected, exit 0, stderr warning |
| T9 | Unknown rev → ≠0 |
| T10 | A1: one function edit in 100-test file → `n_forced==1` |
| T11 | A2: deleted test absent from output |
| T12 | A5: dump golden byte-identical (real slice, ±`--include-mutants`) |
| T13 | A3: wrong `schema_sha256` in model.json → error |
| T14 | A10: null useful-on-real → one stderr warning |

---

## 7. Documentation

README **`select`**: examples, budgets, dump prerequisite, embedded schema, limitations (mutant-trained models, constant history features, no parametrize, synthetic-only validation warning), link this spec.

---

## 8. CI

- fmt, clippy `-D warnings`, cargo test, existing python/oracle jobs.
- Parity + select tests in **rust** job (CSV fixture; parquet only if optional dev-dep).
- No click download for merge gate; PR body: max abs err, click median/p90, click dump row count + sha256.

---

## 9. Out of scope

Safe mode, pytest plugin auto-select, GitHub Action, SHAP, `--tests-from`, multiclass, categorical LightGBM, GPU.

---

# Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: `superpowers:subagent-driven-development` or `superpowers:executing-plans`. Checkbox steps for tracking.

**Goal:** Ship `testpilot select` with pure-Rust LightGBM raw_score inference, shared change features, budgets, and parity/determinism tests (A1–A10).

**Architecture:** `lgbm` validates vs embedded schema → `compute_change_rows` → `select` ranks/budgets/force → CLI.

**Tech Stack:** Rust workspace, clap, no FFI; optional parquet **dev-dependency** only.

## Global Constraints

- No Python at `select` runtime; no `lightgbm-sys`.
- Embedded `features_schema.json` only (A3).
- Force-include = new **or** def∩hunk only (A1); deleted tests dropped (A2).
- Worktree time from `HEAD` author (A4).
- Dump byte-identical golden after refactor (A5).
- `best_iteration` must not exceed tree count (A6).
- Exact-f64 CSV parity fixture (A7).
- `base_run_sha` + lag>20 warning (A8); snapshot hint A9; synthetic-only warning A10.
- CI green; PR quotes max abs err + click median/p90 + dump sha.

## File map

| Path | Role |
|------|------|
| `crates/testpilot-core/src/lgbm/` | model.txt/json, predict_raw |
| `crates/testpilot-core/src/features/change_rows.rs` | shared change features |
| `crates/testpilot-core/src/features/dump.rs` | shared helpers; dump regression golden |
| `crates/testpilot-core/src/select.rs` | candidates, A1/A2, budgets, JSON |
| `crates/testpilot-cli/src/main.rs` | `Select` |
| `scripts/gen_lgbm_parity_csv.py` | A7 fixture generator |
| `scripts/bench_select.py` | latency |
| `tests/fixtures/…` | parity CSV, dump golden, select fixtures |
| `README.md` | select section |

---

### Task 1: LightGBM text parser + embedded schema gates

- [ ] Parse v4 `model.txt`; load `model.json`; gates §2.1 including A3/A6/A10.
- [ ] Commit: `Add pure-Rust LightGBM model.txt parser with embedded schema gates.`

### Task 2: Predict + exact-f64 CSV parity

- [ ] `scripts/gen_lgbm_parity_csv.py`; commit CSV; T1 ≤ 1e-6 with `best_iteration`.
- [ ] Commit: `Match LightGBM raw_score within 1e-6 on parity CSV.`

### Task 3: Shared `compute_change_rows` + dump golden (A5)

- [ ] Extract shared path; head-seeded graph; A9 snapshot message.
- [ ] Capture/commit dump golden; assert byte-identical ± mutants flag.
- [ ] Commit: `Share change feature rows and lock dump regression golden.`

### Task 4: Candidates — A1 force, A2 deleted, A8 base_run_sha

- [ ] Implement candidate union + force def∩hunk + deletions; T10/T11; lag warning.
- [ ] Commit: `Select candidates with def-touch force-include and deletions.`

### Task 5: Budgets + CLI output

- [ ] frac / max-tests / time-budget; json with `n_forced`, `base_run_sha`; worktree HEAD time (A4).
- [ ] Commit: `Add testpilot select CLI with frac, count, and time budgets.`

### Task 6: Isolation, determinism, worktree T5, feature parity

- [ ] T5 (incl worktree), T6–T9, T3, T13–T14.
- [ ] Commit: `Cover select determinism, snapshot errors, and dump parity.`

### Task 7: Bench + README + PR numbers

- [ ] Bench script; README; PR with max abs err, click median/p90, dump sha commands.
- [ ] Commit: `Document select and add latency benchmark script.`

---

## Open points resolved

| Topic | Decision |
|-------|----------|
| Force-include | New **or** def span ∩ hunk only (A1); `n_forced` in JSON |
| Deleted tests | Dropped via scan / deleted file (A2) |
| Schema | Embedded bytes only (A3) |
| Worktree time | `HEAD` author time (A4); T5 includes worktree |
| Dump refactor | Golden byte-identical (A5) |
| `best_iteration` | Error if &gt; tree count (A6) |
| Parity fixture | Exact-f64 CSV + generator (A7) |
| Stale base run | `base_run_sha` + warn if &gt;20 commits (A8) |
| Snapshot hint | `collect git` then `features dump` (A9) |
| Synthetic-only model | One stderr warning (A10) |
