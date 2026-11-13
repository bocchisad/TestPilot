# TestPilot — Week 4 `select` Design

**Date:** 2026-11-13  
**Base:** `main` after PR #2–#5 (`fa12d97`)  
**Scope:** Rust `testpilot select` — pure-Rust LightGBM `model.txt` inference + change feature vectors + budgeted ranking. No Python at runtime.  
**Non-goals (week 5+):** safe mode, pytest-plugin integration, GitHub Action, SHAP, embeddings, GPU, online learning.

## Goal

Given a trained artifact from `testpilot-train` (`model.txt` + `model.json` + optional `reference_predictions.parquet`), a local SQLite DB with `feature_history_snapshot` rows, and a change `base..head` (or worktree), emit a **deterministic ranked subset** of tests under a budget — using the **same 29 features** and **same raw scores** as LightGBM `predict(raw_score=True)` (parity ≤ `1e-6`).

## Context (locked)

| Item | Value |
|------|--------|
| `FEATURES_VERSION` | `2` |
| Model features | exactly **29** names, order locked in `features_schema.json` |
| Service columns (never scored) | `repo, change, parent, commit_idx, time, test, label, flaky, synthetic, base_change` |
| Objectives | `binary` **or** `lambdarank` — both emit a single raw margin; `num_class=1`, `num_tree_per_iteration=1` |
| Snapshot | zstd + postcard HistoryState/CoChangeState, schema **v6** |
| Synthetic runs | excluded from all real history / coverage / candidate-base queries (as dump) |
| Import graph (Phase B / change) | **seeded** at the **head** (or worktree overlay): seeds = changed sources + candidate test files + hunk `.py` paths |

**Honest limitations (document in README):** production models may be trained on mutation bootstrap. Features `t_fail_rate`, `t_fail_cnt`, `t_changed`, `t_flaky_rate`, `cov_lag` were near-constant on that training set → near-zero gain. Real useful-gate remains `data_insufficient` when val has no real `new_fail`.

---

## 1. Architecture

```text
                    model.json ──► validate schema / objective / best_iteration
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
| `crates/testpilot-core/src/lgbm/` | parse `model.txt`, validate vs `model.json`, `predict_raw` |
| `crates/testpilot-core/src/features/change_rows.rs` (or refactor `dump.rs`) | **one** `compute_change_rows` used by `show`, dump leaf emit helper, and `select` |
| `crates/testpilot-core/src/select.rs` | candidates, scoring, budgets, output structs |
| `crates/testpilot-cli` | `testpilot select …` |
| `scripts/bench_select.py` | warm-up + 10 timed runs → median/p90 |
| `tests/` / crate tests | parser parity, feature parity vs dump, budgets, determinism, synthetic isolation |

No new runtime Python deps. No `lightgbm-sys` / FFI.

---

## 2. LightGBM parser (pure Rust)

### 2.1 Load contract

Directory `--model DIR` must contain:

```text
DIR/model.txt
DIR/model.json
```

(`reference_predictions.parquet` required only for the parity CI test, not for `select` itself.)

**`model.json` gates (hard error, non-zero exit):**

1. `features_version == 2` (this is the “version 2” gate; no separate `version` field required).
2. `schema_sha256` == SHA-256 of the **exact bytes** of `--features-schema` (default: `./features_schema.json` if it exists in cwd, else error asking for `--features-schema`). Must match `model.json`.
3. `feature_names` length **29**, identical order to schema / `features_schema.json`.
4. `objective` ∈ {`binary`, `lambdarank`} (string may be `binary` or start with `binary `; lambdarank similarly). Reject `multiclass`, `xentropy`, etc.
5. `constraints.categorical == false` (or absent + model has `num_cat=0` everywhere).
6. `constraints.linear_tree == false` (and every tree `is_linear=0`).
7. `predict_raw_score == true` (if field present; if absent, still predict raw margins and warn once on stderr).
8. `best_iteration` (positive int): use **only the first `best_iteration` trees** from `model.txt` even if the file contains more.

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

Numeric split: go **left** if `fval ≤ threshold` (LightGBM `<=` convention for the trees we emit). Missing handling follows `missing_type` + `default_left` exactly as LightGBM’s C++ predictor for `raw_score`.

`use_missing=true`, `zero_as_missing=false` (train constraints): zeros are real values; only true missing/NaN use the missing path. Our feature matrix has **no NaN** (dump rejects NaN); sentinels are ordinary numbers (`-1`, `99`). Parser must still implement missing paths for fidelity / future-proofing.

### 2.3 Prediction

```text
score(x) = sum_{t=0..best_iteration-1} tree_t(x)
```

Same as LightGBM `predict(..., raw_score=True)` for binary and lambdarank (both single-score).

### 2.4 Parity test

Fixture: `tests/fixtures/reference_model/` (`model.txt`, `model.json`, `reference_predictions.parquet`, `pairs.parquet`).

`reference_predictions.parquet` columns: `repo, change, test, raw_score` (no feature matrix). Features come from `pairs.parquet` joined on `(repo, change, test)` (same order as train: sort by those keys).

1. Load booster in Rust; for every joined row, take the 29 feature columns in schema order.
2. Compare to `raw_score`.
3. Assert `max_i |rust_i − ref_i| ≤ 1e-6` over **all** rows.
4. Test prints `max_abs_err=…` and `n=…`.

Additional unit tests: reject categorical / `linear_tree` / wrong `schema_sha256` / wrong feature order / multiclass header.

---

## 3. Feature vectors — one code path with dump/show

### 3.1 Shared API

Public (or crate-visible) function:

```rust
pub fn compute_change_rows<R: GitRepo>(
    repo: &R,
    store: &Store,
    repo_name: &str,
    base: &Sha,
    head: &Sha,                 // HEAD sha when --worktree; still used for metadata
    hunks: &[DiffHunk],         // base..head OR base..worktree
    opts: ChangeRowsOpts,       // see §3.3 candidates
) -> Result<Vec<FeatureRow>>
```

**Refactor:** today’s `compute_show_rows` becomes a thin wrapper around `compute_change_rows` with default opts (candidates = last clean run ≤ base only). Dump’s per-commit emit stays on the walk, but **import-graph seeding and `emit_row` inputs** for a single change must call the same helpers as `compute_change_rows` (no duplicated seed/graph logic).

### 3.2 Snapshot + history

- Load `feature_history_snapshot` at `base` (exact). If missing: hard error  
  `no feature snapshot for {base}; run: testpilot features dump`  
  (mention `features backfill` / dump in the message — same spirit as show today). **No silent first-parent rebuild.**
- Coverage / history queries: **non-synthetic only**.
- Coverage source + lag rules: identical to `features show` / dump.

### 3.3 Import graph (locked)

For change `base..head` (or worktree):

1. Resolve **file bytes for scanning** from **head** when `--head <rev>`; from **worktree filesystem** when `--worktree` (dirty overlay). Module **index** = all `.py` paths at that same view.
2. Seeds = `srcf` ∪ test-file paths of every candidate `test_id` ∪ hunk paths ending in `.py`.
3. `build_import_graph_seeded(repo, store, head_view, seeds, index)` — same helper as Phase B mutant dump (seeds + BFS), not a full walk of every file as root.

**Parity note:** historical dump rows for commit `H` already use files at `H`. After this refactor, `features show --base P --head H` must match dump rows for change `H` on the 29 features for tests that appear in both (see §6.2). If today’s show still graphs at `base`, **change it** to head as part of this work — that is intentional alignment, not a silent behavior drift without tests.

### 3.4 Worktree

| Flag | Diff | File bytes for graph / name / changed-funcs |
|------|------|-----------------------------------------------|
| `--head REV` | `diff_hunks(base, REV)` | at `REV` |
| `--worktree` | `diff_worktree(base)` | worktree working tree |
| `--head WORKTREE` | **alias** of `--worktree` | same |

`change` / `time` service fields: for worktree, `change` may be the literal `WORKTREE` (or `HEAD` sha + dirty marker) — **locked for select JSON:** `head: "WORKTREE"` when worktree mode; feature `commit_idx` / history still keyed off `base`. Ranking does not use service fields.

### 3.5 Candidate tests

**Base set:** test_ids from the last **clean, non-synthetic** results run at `base` or earlier (same ancestor walk as show/dump).

**Diff-discovered set:**

1. For each hunk path with `is_test_path(path)` and ends with `.py`:
2. Read file bytes at head/worktree; `scan_python` → defs.
3. Keep defs whose leaf name starts with `test_` (functions / methods). Map to nodeid:
   - file-level: `{path}::{func}`
   - method: `{path}::{Class}.{method}` → normalize to pytest-style `{path}::{Class}::{method}` if that is what the plugin stores (match existing `test_id` strings in DB; unit-test against plugin nodeids for a tiny fixture).
4. **No parametrize expansion** in week 4 (document limitation). Parametrized ids only appear if already in the base run.

**Union** = base set ∪ diff-discovered. Dedup by `test_id`.

**Force-include (beyond budget):** any candidate that is **new** (not in base set) **or** whose test file path appears in hunks (`t_changed`-equivalent) gets `reason = "new_or_changed"` and is **always** present in the selected output even if over budget. Remaining slots filled by ranked `reason = "ranked"`.

New/changed tests without history use the **same sentinels as dump** (`dur=-1`, `pf_max/pq_max=-1`, `imp_dist=99` when no path, zeros where dump zeros, etc.).

### 3.6 Empty diff

If `hunks` is empty: select **0** tests, print a **warning** on stderr (`empty diff; selecting 0 tests`), exit code **0**. Skip scoring; JSON has `n_candidates=0`, `n_selected=0`, `tests=[]`.

---

## 4. CLI

```text
testpilot select
  --repo PATH                 # default: cwd git root
  --db PATH                   # default: <repo>/.testpilot/db.sqlite
  --model DIR                 # model.txt + model.json
  [--features-schema PATH]    # default: ./features_schema.json then DIR/../… — see §2.1
  --base <rev>
  --head <rev|WORKTREE>       # XOR with --worktree; WORKTREE ⇒ worktree mode
  [--worktree]                # XOR with --head rev
  --format json|table|lines   # default: lines
  exactly one of:
    --budget-frac F           # 0 < F ≤ 1; k = max(1, ceil(F * n_candidates))
    --max-tests N             # N ≥ 1; error if N == 0
    --time-budget SECONDS     # SECONDS > 0
```

Exit codes:

| Situation | Code |
|-----------|------|
| Success (incl. empty diff → 0 tests) | 0 |
| Usage / validation / missing snapshot / bad model / unknown rev | ≠0 |
| `N == 0` or `F` out of range or no budget flag / two budget flags | ≠0 |

### 4.1 Ranking order

1. Predict `raw_score` for every candidate.
2. Sort: `score` **descending**, tie-break `test_id` **ascending** (lexicographic UTF-8).
3. Partition: force-include set vs rankable set (rankable = not force-included, or force-included also keep their scores for JSON but budget applies only to filling remaining capacity — **locked algorithm:**

```text
force = { candidates with new_or_changed }
rankable = candidates sorted by (score↓, test_id↑)
selected = []
# 1) always add all force (stable order: score↓, test_id↑)
selected.extend(sorted(force, key=…))
# 2) fill budget from rankable \ selected
```

**Budget accounting:**

- `--max-tests N`: stop when `len(selected) >= N`, but if `|force| > N`, still include all force (selected may exceed N). Document: force can exceed N.
- `--budget-frac F`: let `n = |candidates|` (full union); `k = max(1, ceil(F * n))`; same fill rules as `--max-tests N=k` (force may still make `|selected| > k`).
- `--time-budget S`: walk rankable order (after placing force first, still ordered by score); add test if `sum(dur_sec) + dur_i <= S` **OR** selected is empty (always ≥1 test unless empty diff). `dur_i` from feature `dur`: if `dur == -1` → **repo median** of known `dur` among candidates with `dur >= 0` (milliseconds→seconds); if none known → **1.0 s**. Convert dump’s `dur` (ms integer) → seconds as `dur_ms / 1000.0`.

### 4.2 Output

**`lines`:** one `test_id` per line, selected order (force block then ranked fill — overall still score↓ within the printed list? **locked:** print final `selected` sorted by `(score↓, test_id↑)` for stable pytest feeding). No header.

**`table`:** human-readable columns `rank score dur reason test_id`.

**`json`:**

```json
{
  "model_sha256": "<sha256 of model.txt bytes>",
  "features_version": 2,
  "base": "<resolved sha>",
  "head": "<resolved sha|WORKTREE>",
  "n_candidates": 0,
  "n_selected": 0,
  "budget": {"kind": "frac|count|time", "value": 0.1},
  "tests": [
    {"test_id": "…", "score": 0.0, "rank": 1, "dur": -1, "reason": "ranked|new_or_changed"}
  ]
}
```

`rank` is 1-based in the printed selected order. `dur` is the feature value (ms sentinel `-1`), not the imputed seconds used for time budget.

Determinism: two runs with identical inputs → **byte-identical** stdout for `json` and `lines` (stable key order in JSON via explicit serialization).

---

## 5. Performance

**Target:** `testpilot select --base HEAD~1 --head HEAD` on click (~2.2k tests), release build, model load included, **median wall &lt; 300 ms** over 10 timed runs after 1 warm-up.

Script: `scripts/bench_select.py` (or `.sh`) prints `median_ms`, `p90_ms`, `n`, command. CI may run a smaller fixture smoke; the click number is reported in the PR (machine noted).

---

## 6. Tests (acceptance)

| ID | Assert |
|----|--------|
| T1 | Parser parity vs `reference_predictions.parquet`, max abs err ≤ 1e-6; print max err |
| T2 | Reject categorical / linear_tree / bad schema_sha256 / multiclass |
| T3 | Feature parity: 3 real click commits — for each test in dump ∩ select, 29 floats **exact** (bitwise or decimal equality) |
| T4 | Budgets: `F=1.0` selects all rankable (+force); `N=0` errors; `N > n` selects all; time-budget &lt; first test still selects 1; `dur=-1` uses median/1.0s |
| T5 | Determinism: two `select` invocations → identical `lines` and `json` bytes |
| T6 | Missing snapshot → non-zero exit + hint to dump |
| T7 | Synthetic isolation: DB with mutants vs without → identical select for a real `base..head` |
| T8 | Empty diff → 0 selected, exit 0, stderr warning |
| T9 | Unknown rev → non-zero exit |

Click for T3/bench: use `.cache/repos/click` when present; otherwise skip with clear message (CI uses fixture for T1/T2/T4–T9; T3 may be `#[ignore]` unless `TESTPILOT_CLICK_REPO` set — **locked:** T3 runs in CI on the generate_features_fixture / small repo if click unavailable; plus an optional ignored click test for local/PR numbers).

---

## 7. Documentation

README section **`select`**:

- Example commands (`lines` → `pytest $(cat …)` / `xargs`).
- Budget flags.
- Prerequisite: `features dump` for snapshots; model from `testpilot-train`.
- Limitations: mutant-trained models; constant history features; no parametrize discovery; real gate data_insufficient.
- Link this spec.

---

## 8. CI

- Existing jobs stay green: `fmt`, `clippy -D warnings`, `cargo test`, python jobs, oracles.
- Add parser parity + select unit/integration tests to the rust job (or train-oracle only if they need the fixture parquet — prefer **rust** job with committed reference model).
- No click download required for merge gate; PR body includes local click bench numbers + parity max err.

---

## 9. Out of scope

Safe mode, pytest plugin auto-select, GitHub Action, SHAP, `--tests-from` collect-only file (still reserved), multiclass, categorical LightGBM, GPU.

---

# Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: `superpowers:subagent-driven-development` or `superpowers:executing-plans`. Checkbox steps for tracking. **Do not start until the design above is approved.**

**Goal:** Ship `testpilot select` with pure-Rust LightGBM raw_score inference, shared change features, budgets, and parity/determinism tests.

**Architecture:** `lgbm` module loads/validates artifact → `compute_change_rows` (shared with show) builds 29-D rows → `select` ranks + budgets → CLI formats output.

**Tech Stack:** Rust (edition as workspace), existing `testpilot-core` git/store/features, clap CLI, no FFI.

## Global Constraints

- No Python at `select` runtime; no `lightgbm-sys`.
- Exactly 29 features; service columns never scored.
- Snapshot miss = hard error; synthetic runs never affect real select.
- Import graph: seeded at head/worktree (seeds = srcf + tests + hunks).
- Force-include `new_or_changed` may exceed `--max-tests` / frac.
- Empty diff → 0 tests, exit 0, stderr warning.
- CI: fmt, clippy `-D warnings`, cargo test green; PR quotes max abs err + click median/p90.

## File map

| Path | Role |
|------|------|
| `crates/testpilot-core/src/lgbm/mod.rs` (+ `parse.rs`, `predict.rs`) | model.txt/json load, predict_raw |
| `crates/testpilot-core/src/features/change_rows.rs` | shared change feature computation |
| `crates/testpilot-core/src/features/dump.rs` | call shared helpers; thin show wrapper |
| `crates/testpilot-core/src/select.rs` | candidates, budget, JSON structs |
| `crates/testpilot-core/src/lib.rs` | module exports |
| `crates/testpilot-cli/src/main.rs` | `Select` subcommand |
| `crates/testpilot-core/tests/lgbm_parity.rs` | T1/T2 |
| `crates/testpilot-core/tests/select_*.rs` | T3–T9 |
| `scripts/bench_select.py` | latency median/p90 |
| `README.md` | `select` section |

---

### Task 1: LightGBM text parser + validation

**Files:** `lgbm/*`, unit tests with tiny hand-written trees + reference fixture header checks.

- [ ] Parse v4 `model.txt` header + trees (`split_feature`, `threshold`, `decision_type`, children, `leaf_value`, `shrinkage`, `is_linear`, `num_cat`).
- [ ] Load `model.json`; enforce §2.1 gates; truncate to `best_iteration`.
- [ ] Reject categorical / linear / multiclass / bad sha / name order mismatch.
- [ ] Commit: `Add pure-Rust LightGBM model.txt parser with schema gates.`

### Task 2: Predict raw_score + reference parity

**Files:** `lgbm/predict.rs`, `tests/lgbm_parity.rs`.

- [ ] Implement missing/default_left/≤ split semantics.
- [ ] Parity vs `tests/fixtures/reference_model/reference_predictions.parquet` ≤ 1e-6; print max abs err.
- [ ] Commit: `Match LightGBM raw_score within 1e-6 on reference_predictions.`

### Task 3: Shared `compute_change_rows` + head-seeded import graph

**Files:** `change_rows.rs`, refactor `dump.rs` / show CLI path.

- [ ] Extract shared computation; show becomes wrapper.
- [ ] Graph at head/worktree with Phase-B-style seeds.
- [ ] Missing snapshot → explicit error string with dump hint.
- [ ] Commit: `Share change feature rows between show and select.`

### Task 4: Candidates + force-include new/changed tests

**Files:** `select.rs`, python_scan usage, tests on fixture repo.

- [ ] Base set from last clean non-synthetic run ≤ base.
- [ ] Diff-discovered `test_*` defs → nodeids; union + force-include rules.
- [ ] Sentinels identical to dump for unseen tests.
- [ ] Commit: `Select candidates from base run plus new/changed tests.`

### Task 5: Budgets + CLI output

**Files:** `select.rs`, `testpilot-cli`.

- [ ] Exactly one of frac / max-tests / time-budget; validation errors.
- [ ] Time-budget dur imputation (median / 1.0s); always ≥1 test when candidates exist.
- [ ] Formats `json|table|lines`; `model_sha256`; deterministic JSON.
- [ ] Empty diff / unknown rev behavior per §3.6 / §4.
- [ ] Commit: `Add testpilot select CLI with frac, count, and time budgets.`

### Task 6: Isolation, determinism, feature parity tests

**Files:** integration tests, optional click ignored test.

- [ ] T5 determinism, T6 snapshot, T7 synthetic isolation, T8 empty diff.
- [ ] T3 feature parity on fixture (and optional click).
- [ ] Commit: `Cover select determinism, snapshot errors, and dump parity.`

### Task 7: Bench script + README + PR numbers

**Files:** `scripts/bench_select.py`, `README.md`.

- [ ] Warm-up + 10 runs; print median/p90.
- [ ] README `select` section + limitations.
- [ ] PR: commands, max abs err, click median/p90 (local).
- [ ] Commit: `Document select and add latency benchmark script.`

---

## Open points resolved in this doc

| Topic | Decision |
|-------|----------|
| Graph location | Seeded at **head**/worktree (align show with dump-at-child) |
| New tests | AST `test_*` defs in changed test files; no parametrize expand |
| Force-include vs N | Force may exceed `--max-tests` / frac |
| Schema path | `--features-schema` optional; must match `model.json` hash |
| `version 2` | Means `features_version == 2` |
| Empty diff | 0 selected, exit 0, stderr warning |
| Worktree head field | JSON `head: "WORKTREE"` |

## Spec self-review

- No TBD placeholders left for week-4 scope.
- Safe mode / plugin / GHA / SHAP explicitly out of scope.
- Force-include vs strict N documented (overflow allowed).
- Click-in-CI: fixture for gate; click numbers in PR from local bench.
