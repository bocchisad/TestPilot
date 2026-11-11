# TestPilot — Mutation Bootstrap Design

**Date:** 2026-11-11  
**Base:** `main` after merge of PR #3 (week-3 training)  
**Pulls forward:** week-6 idea — generate failures via mutations when real `new_fail` is absent  
**Non-goals:** replacing user data; GPU; mutating tests; mutating non-Python; parallel SQLite writers; changing the 29 model features; Rust `select`  
**Amendments:** M1–M8 (approved 2026-11-11)

## Goal

When clean backfills of click/jinja yield **`new_fail = 0`** (only green commits land on `main`), bootstrap ranking labels by committing **small AST mutations** of historical changes as real git commits, running tests **without coverage**, and training on the resulting synthetic `new_fail` — without leaking mutant outcomes into history features of real changes.

## Motivation (measured)

- Click / Jinja `--last 250` after env fixes → green suites → `label=1 = 0` → `data_insufficient`.
- Linux click `--last 10`: **24 failures every commit** — all parametrizations of `test_echo_via_pager` for **`less`** when `less` is missing. These are **persistent** (fail on parent and child) → **do not create `new_fail`**. Document in README that **`less` must be installed** for click pager tests; do not exclude the tests.

---

## 1. Mutants as real git commits

### 1.1 Placement

For a sample of clean commits `C` in the backfill window (C has a clean pytest/junit run with results):

1. **Eligible C (M6):** commits that change at least one **source** `.py` file in hunks (exclude tests / docs / non-Python-only). Report `n_eligible_C`.
2. Restrict candidate edit sites to **lines touched by C’s hunks** (old→new mapping as in week-2 features).
3. Apply **one** AST mutation → tree `M` with **`parent = C`**.
4. Commit `M` under:

```text
refs/testpilot/mutants/<C_sha>/<i>
```

**Timestamps (M1 — P0):**  
- `author_date(M) = author_date(C) + i` seconds  
- `committer_date(M) = committer_date(C) + i` seconds  

(Deterministic; identity `testpilot-mutate@localhost`.) Do **not** use fixed fixture epoch (`2020-01-01`) — that makes `M` years older than `C` and shifts `t_age_days` / history decay. Features use **author** time, so author must track `author(C)+i` (not committer).

**Snapshot after C (locked):** Phase B loads the history snapshot **after** processing `C`. Therefore `t_obs(M) = t_obs(C)+1` for tests observed on C — C is in M’s past. That is correct; do not freeze at pre-C state.

**Time-feature test (M1):** on a C with `author ≠ committer`, `t_age_days` for M matches a real child commit with the **same author time** as M (not merely “equal to C’s row”).

**DB indexing (locked):** Mutants are **not** on `HEAD`’s first-parent walk, so plain `testpilot collect git` will not see them. After each successful commit of `M`, the mutate tool must **index that SHA explicitly** (extend CLI/`collect_git` with a single-SHA / ref-list path that reuses the same `file_changes` / parents insert as normal collect). Dump Phase B and junit collect both require `M` present in the store.

Existing hunk / import / name / `file_changes` code paths then treat `M` as a normal commit. No parallel “fake change” feature path.

### 1.2 Operators (AST)

Mutate **only the changed AST node**; `ast.unparse` that node; **byte-preserve** the rest of the file (do not reformat the whole module).

| ID | Operator |
|----|----------|
| `cmp_flip` | `<` ↔ `<=`, `>` ↔ `>=` |
| `eq_flip` | `==` ↔ `!=` |
| `arith_flip` | `+` ↔ `-` (binary) |
| `bool_flip` | `and` ↔ `or` |
| `not_wrap` | invert condition (`not x` / unwrap) |
| `const_pm1` | numeric constant `±1` |
| `return_none` | `return X` → `return None` |
| `drop_call` | delete a statement that is a bare call inside a changed function |

- Mutant that **does not compile** (`ast.parse` / `compile` fail) → **skip**, count in report (`n_invalid`).
- Prefer sites on hunk-touched lines; if none available for an operator, try next deterministic candidate.
- **Dedup (M6):** for the `K` mutants of one `C`, choose distinct `(operator, node)`; drop duplicates by resulting **diff**. Report **kill rate by operator**.

### 1.3 Sampling parameters

```text
testpilot-mutate  # or scripts/mutate_bootstrap.py
  --repo PATH
  --db PATH                 # DB with clean runs for candidate C
  --commits N               # how many C to sample (from eligible)
  --mutants-per-commit K
  --seed S
  [--timeout-sec T]         # override; default from §2.1 M5
```

`seed` → deterministic choice of `C`, files, nodes, operators, and commit metadata. Re-running with the same seed must recreate the **same** `M` SHAs (same trees + same timestamps).

---

## 2. Run and label

### 2.1 Pytest on `M`

- Worktree at `M`; shared venv as in `backfill.py` (project test deps, `--python`, locale, warning filters).
- **`pytest` + junit XML** with **no coverage** (`--testpilot` coverage off): ingest via **`testpilot collect junit --sha M`** with **`synthetic=1`** on the run (M2).
- **Coverage features for `M`** use the week-2 rule unchanged: last **non-synthetic** clean run with coverage on **≤ parent** lineage → for parent `C`, that is C’s (or ancestor’s) coverage map. Do **not** require a coverage run on `M`.
- **Timeout (M5 — P2):** default  
  `timeout_sec = max(60, 3 × duration_sec of C’s last non-synthetic run without requiring coverage)`.  
  On timeout: kill the **entire process group**; discard mutant (delete ref; no junit retain); count `n_timeout`.
- **Flaky rerun (M4 — P1):** tests that **failed** on `M` are re-run **once** on `M` (failed only). Pass on rerun → `flaky=1`. Report `n_flaky_rerun`.

### 2.2 `new_fail` on mutants

Same derivation as week 3 (Python, parquet):

```text
new_fail = 1  iff  label==1 ∧ flaky==0 ∧ test did not fail on parent C
```

- Persistent env fails (`less`, etc.) fail on both `C` and `M` → **`new_fail = 0`**, auto-filtered from ranking positives.
- Mutants with **zero** failing tests (equivalent / uncovered) stay in the dataset as changes **without** ranking positives; counted in report (`n_equivalent`); excluded from per-change ranking macros (need ≥1 `new_fail`).

---

## 3. No leakage (hard requirement)

### 3.1 Leaf semantics

A mutant `M` is a **leaf**:

- Feature rows for `M` are emitted using **HistoryState + CoChangeState (+ cov source) as of `C`** (snapshot after processing `C` in the real walk).
- Processing `M` **must not** call `history.update_*`, **must not** `cochange.record`, **must not** advance cov tracker / write a history snapshot that includes `M`’s outcomes.
- Subsequent **real** commits must see the same history they would without any mutants.

### 3.2 DB: `test_runs.synthetic` (M2 — P0)

Additive DB migration: column **`test_runs.synthetic INTEGER NOT NULL DEFAULT 0`** (`0` real, `1` mutant).

**Exclude `synthetic=1` runs from every real query path**, including:

- last clean run for coverage / `cov_lag`
- `dur`, `t_obs` / `t_fail_*` history updates
- test set for `features show` (“last clean run ≤ base”)
- Phase A dump snapshots
- future `select`

Mutant junit ingest sets `synthetic=1`.

### 3.3 Dump integration

1. **Phase A — real walk:** today’s `features dump` over first-parent / HEAD history only (ignore `refs/testpilot/mutants/**`). Snapshots as today; only non-synthetic runs feed history.
2. **Phase B — mutant leaves:** for each `refs/testpilot/mutants/<C>/<i>` tip `M`:
   - Load snapshot at `C` (exact; hard error if missing → run dump).
   - Diff `C..M`, build import graph / names as usual.
   - Emit one row per test in M’s **synthetic** results run, with coverage from ≤`C` (non-synthetic).
   - **Do not** apply post-emit history/cochange updates.
   - Set service fields (§4).

CLI: `testpilot features dump --include-mutants` (default **off**). Training datasets that need mutants pass the flag.

### 3.4 Leakage tests (CI + PR) — M2 strengthened

| Test | Assert |
|------|--------|
| Flag on/off | Dump **without** `--include-mutants` on a DB **with** mutants vs dump on a DB **without** any mutant runs/refs → **byte-identical** real parquet (sort `(repo, change, test)`). Not only “same DB, flag on vs off”. |
| Snapshots | Phase A `feature_history_snapshot` payloads identical with vs without mutant runs in DB. |
| Show | `features show` for a real `base..head` identical with mutants in DB vs without. |
| Time features (M1) | Synthetic rows’ `t_age_days` (etc.) match C’s rows for unaffected tests. |
| Feature matrix (M7) | `synthetic` / `base_change` **never** appear in the 29-column model matrix. |

Also: dump with `--include-mutants` → filter `synthetic==0` matches the no-mutant dump for all 29 features + real service fields.

---

## 4. Schema — `FEATURES_VERSION = 2`

### 4.1 New service columns (not in the model)

Append after `flaky` (order locked in `features_schema.json` + Rust `schema.rs`):

| Column | dtype | Meaning |
|--------|-------|---------|
| `synthetic` | int64 | `0` real, `1` mutant |
| `base_change` | utf8 | mutants: full `C` sha; real: `""` |

**Model features remain 29** (unchanged names/order/dtypes).  
`FEATURES_VERSION` → **2**. Update: Rust schema, `features_schema.json`, sync check, features oracle, train schema gate, synth parquet generator.

**Feature gate (M7 — P2):** assert in train load that model matrix columns are exactly the 29 features — never `synthetic` / `base_change`. After v2, week-4 **reference** model still scores the same 29 columns → `reference_predictions` / `raw_score` must stay identical; if `pairs.parquet` is rebuilt, verify scores unchanged.

### 4.2 `commit_idx` / split inheritance

- Mutant rows set **`commit_idx` = `commit_idx` of `C`** (from the real walk).
- **Split:** partition **ordered unique `commit_idx` values** 70/15/15; every change with that idx is atomic → all mutants of `C` share C’s slice. Assert stays strict on unique idxs.
- **Regression (M8 — P3):** on data **without** mutants (unique idx == one change per idx), unique-idx split **equals** the old change-count split. Unit test locks this.
- `time` = mutant **author** ts (`author(C) + i` sec); history from post-C snapshot → `t_obs(M)=t_obs(C)+1`; ages match a real peer with the same author time (M1).

### 4.3 Train filters, clusters, gate

- Train on `new_fail` from **`synthetic==1`**, plus any real `new_fail` if present.
- Report metrics **separately**: synthetic val/test (primary) and real val/test.
- **Clusters (M3 — P1):** bootstrap CI for metrics and the paired useful-gate **resample by cluster**:
  - synthetic rows: cluster = `base_change` (`C`)
  - real rows: cluster = `change`
- **Useful-gate** on synthetic val; label `useful_on_synthetic_val`.
- **`data_insufficient` (synthetic test):** need **≥20 mutant changes with ≥1 `new_fail` AND ≥10 distinct `base_change` (C)** in test.

### 4.4 Baseline emphasis

Keep existing heuristics. **`cov_lines`** is the critical foil for mutation bootstrap — model must beat it on synthetic val under the paired (clustered) gate.

---

## 5. Volumes and timing

### 5.1 Measure first (every number with command)

```bash
/usr/bin/time -p testpilot-mutate --repo "$CLICK" --db ... \
  --commits 5 --mutants-per-commit 3 --seed 42
```

Expect ~10–13 s/run without coverage (verify; cite measured `real`). Default timeout from M5 unless overridden.

### 5.2 Target

Choose `N × K` so synthetic **test** has ≥20 mutant changes with ≥1 `new_fail` **and** ≥10 distinct C (M3). Finalize after §5.1.

Rough prior: if ~50% kill and test ≈15% of unique idxs,

\[
0.15 \times N \times K \times 0.5 \ge 20 \quad\text{and}\quad 0.15 \times N \ge 10
\]

→ e.g. `N ≥ 67`, `N K ≳ 270` — **replace with measurement**.

### 5.3 Constraints

- **Do not** parallelize mutant pytest against **one** SQLite DB.
- Mutant refs live only in the training repo clone (not pushed to upstream click/jinja).

---

## 6. Honesty limits (spec + report)

Mutations ≠ real regressions: single-site, hunk-local, Python-only. Bootstrap only until user-project failures exist.

Report must include:

- `n_eligible_C`, `n_mutants_attempted / committed / invalid / timeout / equivalent / with_new_fail`
- `n_flaky_rerun`; kill rate **by operator**
- useful-gate marked **on mutations** (`useful_on_synthetic_val`)
- real-slice metrics honest when insufficient

---

## 7. Package layout (implementation sketch)

```text
scripts/mutate_bootstrap.py          # or python/testpilot-mutate/
  sample_commits.py                  # eligible C, seed sample
  ast_ops.py                         # operators + compile check + dedup
  git_mutant.py                      # worktree, commit (C_ts+i), ref update
  run_mutant.py                      # pytest no-cov, timeout pgid, flaky rerun
  report_stats.py

crates/testpilot-core/
  schema.sql / migrate               # test_runs.synthetic
  store.rs                           # exclude synthetic from real queries
  features/schema.rs                 # FEATURES_VERSION=2
  features/dump.rs                   # --include-mutants leaf pass

python/testpilot-train/
  data.py / split.py / train_lib.py / report.py / metrics.py / baselines.py
  # cluster bootstrap; synthetic gate; feature-matrix assert
```

README: **`less` required** for click pager tests on Linux.

---

## 8. CI

Within **train-oracle** budget (&lt;60s pytest wall for train package; mutant tests share job or thin sibling):

1. **Determinism:** two mutate runs → identical `M` SHAs / refs.
2. **Invalid skip:** broken compile → counted, no ref.
3. **Leakage (M2):** DB-with-mutants dump (no `--include-mutants`) == DB-without-mutants dump; snapshots; show.
4. **≥1 mutant with `new_fail`** on fixture.
5. **M1** time-feature parity; **M7** feature-matrix assert; **M8** split regression on non-mutant data.

No click/jinja download in CI.

---

## 9. Done when

- Spec + plan approved (incl. M1–M8); implementation complete.
- `FEATURES_VERSION=2` + `test_runs.synthetic` + oracle/schema gates.
- Mutant refs + leaf dump + M2 leakage tests green.
- Train: clustered synthetic useful-gate + `cov_lines`; real slice honest.
- Click timing + chosen `N×K` in PR with commands; README `less`.
- CI invariants including M1/M7/M8.

---

## 10. Locked decisions

| Topic | Choice |
|-------|--------|
| Mutant storage | Real commits under `refs/testpilot/mutants/<C>/<i>` |
| Parent | `C` (first-parent of M) |
| Timestamps (M1) | `author(C)+i` / `committer(C)+i`; snapshot after C |
| DB index | Explicit single-SHA collect (not HEAD walk) |
| Run flag (M2) | `test_runs.synthetic`; excluded from all real queries |
| Coverage on M | None; features from ≤C non-synthetic |
| History/cochange | Frozen at C; M never updates |
| Schema parquet | v2: `synthetic`, `base_change`; 29 features unchanged |
| Split | Unique `commit_idx` atoms; M8 regression vs week-3 |
| Clusters (M3) | Bootstrap by `base_change` (synth) / `change` (real) |
| data_insufficient | ≥20 kill mutants **and** ≥10 distinct C in test |
| Flaky (M4) | One rerun of failed tests on M |
| Timeout (M5) | `max(60, 3×C_duration)`; kill process group |
| Sampling (M6) | Eligible C with src `.py`; dedup diff; kill rate by op |
| Feature gate (M7) | 29-col matrix only; reference scores unchanged |
| Useful-gate | Synthetic val, clustered (`useful_on_synthetic_val`) |
| Key baseline | `cov_lines` |
| Parallelism | No concurrent writers to one DB |
| Env note | Install `less` for click pager tests |

---

## 11. Self-review + M1–M8 map

| ID | Topic | Where |
|----|-------|--------|
| M1 | Mutant time = C + i sec; age feature test | §1.1, §4.2, §3.4 |
| M2 | `test_runs.synthetic`; real-query exclusion; DB-with vs DB-without leak tests | §3.2–3.4 |
| M3 | Cluster bootstrap; ≥20 kills ∧ ≥10 C | §4.3, §5.2 |
| M4 | Fail-only flaky rerun; `n_flaky_rerun` | §2.1, §6 |
| M5 | Default timeout rule; process group kill | §2.1 |
| M6 | Eligible C; dedup; kill rate by op | §1.1–1.2, §6 |
| M7 | 29-col gate; reference score identity | §4.1, §3.4 |
| M8 | Unique-idx split ≡ change split without mutants | §4.2, §8 |
