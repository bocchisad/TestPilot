# TestPilot — Training Week Design

**Date:** 2026-11-09  
**Base:** `main` @ `b79f4ac` (weeks 1–2 merged)  
**Scope:** offline CPU LightGBM training + chronological evaluation + artifact for week-4 Rust `select`  
**Non-goals:** Rust inference, `select`, safe mode, GitHub Action runner, SHAP, embeddings, GPU, LLM

## Goal

Train an offline model that ranks tests for a change by probability of a **new** failure (failed on this change but not on parent); compare it honestly to heuristics on a chronological split; emit an artifact week 4 can load and score with the same raw scores (tolerance `1e-6` on a frozen reference model).

---

## 1. Package and CLI

```
python/testpilot-train/
  pyproject.toml
  src/testpilot_train/
    cli.py                # train | evaluate | baselines
    schema.py             # FEATURES_VERSION + schema hash + feature columns
    data.py               # load parquet(s), validate, new_fail, filters
    split.py              # chrono 70/15/15 per repo; leave-one-repo-out
    model.py              # LightGBM binary vs lambdarank; fixed hyperparams
    metrics.py            # ranking metrics with tie handling + bootstrap
    baselines.py          # random + heuristics
    report.py             # Markdown + JSON
    leakage.py            # shuffled labels / future-feature (synthetic data)
    synth.py              # synthetic parquet generator for leakage tests
    artifact.py           # model.txt, model.json, reference model + preds
  tests/
  tests/fixtures/         # frozen reference model for week-4 parity
```

**Pinned runtime deps** (`pyproject.toml` + lockfile): `lightgbm`, `pyarrow`, **polars**, `numpy`. No GPU. Python ≥ 3.10.

**Commands:**

```text
testpilot-train train --data A.parquet [B.parquet…] --out DIR
  [--features-schema PATH] [--seed N]

testpilot-train evaluate --model DIR --data …
  [--slice test|all]   # default: test ranges from model.json

testpilot-train baselines --data …
```

Default `--features-schema` = repo-root `features_schema.json`.

### 1.1 `evaluate` slice rules (A8)

- Default: evaluate only the **test** `commit_idx` ranges recorded in `model.json` for each **known** `repo`.
- Rows whose `repo` is **not** in `model.json` splits → include **all** their changes.
- If the requested evaluation set intersects the model’s **train** `commit_idx` ranges for a known repo → print an explicit **warning** (still runs).

---

## 2. Data

### 2.1 Input / schema gate

Parquet from `testpilot features dump` (one or more repos).

On every file:

1. Parquet kv `features_version` == `features_schema.json` `features_version` (currently `1`).
2. `schema_sha256` = SHA-256 of the exact bytes of `--features-schema`.
3. Column names/order match `features_schema.json` exactly.

Mismatch → hard error. Both values go into `model.json`.

Reject any feature column containing `NaN` or `±inf` (hard error before train).

### 2.2 Feature matrix (29 columns)

**Service (not in model):** `repo, change, parent, commit_idx, time, test, label, flaky`

**Features (fixed order):**

```
cc_max, cc_sum, imp_dist, imp_direct,
name_file, name_jac, name_func,
cov_known, cov_file, cov_func, cov_lines, cov_breadth, cov_mod, cov_lag,
t_fail_rate, t_fail_cnt, t_obs, pf_max, pq_max,
t_age_days, dur, tfile_ntests, t_changed, t_flaky_rate,
ch_nfiles, ch_nsrc, ch_add, ch_rm, ch_nfunc
```

Numeric only. **No categorical. No `linear_tree`.**

### 2.3 Sentinels (keep as-is; no imputation)

| Value | Columns | Meaning |
|-------|---------|---------|
| `-1` | `pf_max`, `pq_max` | no historical samples |
| `-1` | `dur` | never observed before |
| `99` | `imp_dist` | no import path within maxd=6 |

### 2.4 Derived label `new_fail` (A1) — primary

Computed in Python from parquet (no Rust change):

```
new_fail = 1  iff  label==1 AND flaky==0 AND the same (repo, test) did NOT fail
                  on the parent change (parent sha equals some row’s `change`
                  in the same repo with label==1 & flaky==0).
```

- If `parent` has **no** run/results in the parquet for that repo → row is **excluded from all `new_fail` metrics**; count these as `n_rows_parent_missing`.
- **Persistent fail:** `label==1 & flaky==0` and the test also failed on parent → `new_fail=0` and row is **excluded from the training matrix** (neither pos nor neg).
- **Training positives:** `new_fail==1`. Training negatives: remaining rows after excluding flaky, persistent, and parent-missing rows used only for metrics exclusion.

**Secondary metrics** (report only): ranking on raw `label` with flaky excluded (A) and included (B).  
**Primary metric, objective choice, useful-gate, data_insufficient:** all on **`new_fail`**.

### 2.5 Diagnostics (report)

- Histogram / summary of failures-per-change (`label=1&flaky=0` and `new_fail`).
- Count of tests that fail in ≥90% of changes where they appear (always-broken / env).
- Share of `label=1&flaky=0` rows that are persistent (`new_fail=0` with parent present).
- If most failures are persistent → call out likely backfill environment issues (deps, Python version) in the PR.

### 2.6 Changes in ranking metrics

Only changes with ≥1 `new_fail` enter primary ranking metrics. Count `n_changes_no_new_fail`.

---

## 3. Split (no leakage)

### 3.1 Chronological per repo

Sort distinct changes by `commit_idx` (tie-break `change` sha). Whole change → one slice:

| Slice | ≈ fraction | Use |
|-------|------------|-----|
| train | 70% | fit |
| val | 15% | early stopping, objective choice, useful-gate |
| test | 15% | final report only |

`i_train = int(0.70*n)`, `i_val = int(0.85*n)`. Refuse train if `n < 10` changes.

**Assert:** per repo, `max(commit_idx|train) < min(val) < min(test)` when slices non-empty.

### 3.2 Leave-one-repo-out

If ≥2 repos: for each held-out R, train on other repos (early stopping on those repos’ chrono val); evaluate primary metrics on R’s chrono **test** third. Macro-average recall@10% (`new_fail`) over held-out repos.

### 3.3 Early stopping

Only on val. Test once after selection.

---

## 4. Model

### 4.1 Fixed hyperparameters (A7) — no grid search

```
learning_rate = 0.05
num_leaves = 31
min_data_in_leaf = 50
feature_fraction = 0.8
bagging_fraction = 0.8
bagging_freq = 1
deterministic = true
force_col_wise = true
num_threads = 1
seed = bagging_seed = feature_fraction_seed = data_random_seed = <CLI --seed, default 42>
linear_tree = false
use_missing = true          # LightGBM default; record in model.json
zero_as_missing = false
```

Large `num_boost_round` + early stopping on val (native `auc` for binary / `ndcg` for lambdarank). Persist `best_iteration`. **All params → `model.json`.**

### 4.2 Candidates

1. **`binary`** + `scale_pos_weight = n_neg/n_pos` (train matrix after §2.4 filters).  
2. **`lambdarank`**, label = `new_fail`, `group` = contiguous rows per `change`.

**Lambdarank (A6):**

- Before building `group`, **assert** rows are sorted so each `change` forms one contiguous block.
- Set `lambdarank_truncation_level` and `ndcg_eval_at` to a **fixed integer** ≈ 10% of median train group size (compute once from train; store in `model.json`).

Choose objective by higher val **recall@10% on `new_fail`** (tie → **binary**).

### 4.3 Scores for metrics / week 4

`raw_score = booster.predict(X, raw_score=True)` — **margin**, no sigmoid. Documented in `model.json` as `predict_raw_score: true`.

### 4.4 Reference model for Rust parity (A4)

Separate from the production train path: a **frozen** booster for week-4 CI:

```
num_boost_round = 50
num_leaves = 15
min_data_in_leaf = 1
min_sum_hessian_in_leaf = 0
# NO early stopping
```

Trained once on the week-2 fixture parquet (or synthetic-with-sentinels), committed under:

```
python/testpilot-train/tests/fixtures/reference_model/
  model.txt
  model.json
  reference_predictions.parquet
```

Asserts: ≥50 trees; some tree depth ≥3; prediction rows include sentinel inputs (`pf_max/pq_max/dur == -1` and/or `imp_dist == 99`).  
`raw_score` = `predict(raw_score=True)`. Week 4 matches within `1e-6`.

Production `train` still writes its own `DIR/model.txt` + optional `DIR/reference_predictions.parquet` for the run; the **committed fixtures** are the week-4 contract.

### 4.5 Determinism

Two production `train` runs on the same data+seed → byte-identical `model.txt` (CI).

---

## 5. Metrics

Per-change, then macro-average over changes with ≥1 `new_fail` (primary). Secondary tables for `label` A/B.

### 5.1 Tie-aware ranking (A3)

Scores may have huge ties (`imp_dist=99`, zeros, …). **Do not** break ties by `test` id for metric expectation.

For recall@k with ties: sort by score descending into tie groups; greedily take whole groups while under budget; for the **borderline** group, take the expected fraction of positives if a random `k_remain`-subset of that group is chosen (hypergeometric / linearity of expectation).

**Unit test:** all scores equal → `recall@k = k/n` (when every row is positive? → if `n_pos` positives and all scores tied, expected recall@k = `min(k,n_pos)/n_pos` = hypergeometric mean). Spec requirement: all scores equal → `recall@k = k/n` when **all** labels are positive; more generally expected positives in top-k / n_pos under uniform ranking.

Clarify: if all scores equal and there are `n_pos` positives out of `n`, expected recall@k = `E[hits]/n_pos` where hits ~ Hypergeometric(n, n_pos, k) → `k/n`. Yes: `recall@k = k/n` independent of n_pos (as long as n_pos>0). Good.

Same code path for model and baselines. APFD / first_fail_frac / all_fail_frac: use expected ranks under random tie-breaking within groups (or document equivalent unbiased estimator).

Budgets: `k = max(1, floor(frac * n))` for `frac ∈ {0.05, 0.10, 0.20}`.

### 5.2 Bootstrap

1000 resamples over changes; 95% CI = 2.5/97.5 percentiles; RNG from `--seed`.

### 5.3 Baselines

| Name | Score |
|------|-------|
| `random` | analytical recall@k expectation |
| `t_fail_rate` | descending |
| `cc_max` | descending |
| `imp_dist` | ascending |
| `cov_lines` | descending |
| `fail_per_day` | `t_fail_cnt / max(t_age_days, ε)` descending (`ε=1/86400`) — renamed from misleading `recent_fail` (A9); no last-fail timestamp in features |

### 5.4 Useful-gate — paired bootstrap (A2)

On the **same** change resamples, compute `delta = model_recall@10% − best_baseline_recall@10%` where **best baseline is chosen on val** (point estimate). Model is **useful on val** iff the 95% CI of `delta` lies **entirely above 0**.

Compute the same paired test on **test** (still using the baseline that won on val). Report both; only val gates “useful”.

### 5.5 `data_insufficient`

If test has **&lt; 20** changes with ≥1 **`new_fail`** → `status: data_insufficient`; print metrics; **no conclusions**.

---

## 6. Leakage checks (A5) — synthetic data

Generator `synth.py`: numpy + seed; ~200 changes × 200 tests; same schema; signal planted in a pair of features correlated with `new_fail`.

| Check | Data | Expectation |
|-------|------|-------------|
| Shuffled labels | synthetic | recall@10% within 0.05 of random |
| Future feature (= label copy) | synthetic | canary fires (recall@10% ≫ random) |
| Schema / load / dump path | week-2 fixture parquet | gate + columns OK |

Feature **gain** importances in report. SHAP → week 8.

---

## 7. Artifact layout

```
DIR/
  model.txt
  model.json
  report.md
  report.json
```

Committed week-4 fixtures (separate): `tests/fixtures/reference_model/{model.txt,model.json,reference_predictions.parquet}`.

### 7.1 `model.json` (required fields)

- `features_version`, `schema_sha256`, `feature_names` (29)
- `objective`, `lightgbm_version`, `seed`, `best_iteration`
- Full hyperparameter dict (incl. `use_missing`, `zero_as_missing`, lambdarank truncation / `ndcg_eval_at`)
- `predict_raw_score: true`
- `splits` with inclusive `commit_idx` ranges per repo/slice
- `metrics` (val/test, primary `new_fail` + secondary label A/B)
- `useful_on_val`, `paired_delta_ci_val`, `paired_delta_ci_test`
- `status`: `ok` | `data_insufficient`
- Diagnostics block (§2.5)
- `sentinel_notes`, `constraints`

### 7.2 CI (A9)

- Build CLI in a **separate** step/job as needed.
- `train-oracle` **test** step wall time **&lt; 60 s** (fixture dump may live in the same job but timing gate applies to pytest for `testpilot-train`).
- No click/jinja download in CI.

---

## 8. Real data (click + jinja)

### 8.1 Volume

`--last 30` is too few changes and is polluted by persistent failures. Use **`--last 250`** each; if test `new_fail` changes &lt; 20, bump to **`--last 400`**. Never invent metrics.

### 8.2 Commands

```bash
# backfill uses pytest --testpilot (WITH coverage via the plugin)
python "$TESTPILOT_ROOT/scripts/backfill.py" \
  --repo "$CLICK_REPO" --last 250 \
  --db "$CLICK_REPO/.testpilot/db.sqlite" \
  --plugin "$TESTPILOT_ROOT/python/pytest-testpilot" \
  --pytest-args "-q --tb=no"

testpilot --db "$CLICK_REPO/.testpilot/db.sqlite" \
  features dump --repo "$CLICK_REPO" --out /tmp/click_pairs.parquet
# same for jinja → /tmp/jinja_pairs.parquet

testpilot-train train \
  --data /tmp/click_pairs.parquet /tmp/jinja_pairs.parquet \
  --out /tmp/tp-model \
  --features-schema "$TESTPILOT_ROOT/features_schema.json" \
  --seed 42
```

### 8.3 Backfill time (A9)

Do **not** publish a guessed 4–12 h figure. Measure:

```bash
/usr/bin/time -p python .../backfill.py --repo "$CLICK_REPO" --last 5 ...
/usr/bin/time -p python .../backfill.py --repo "$JINJA_REPO" --last 5 ...
```

State clearly that backfill runs **pytest with TestPilot coverage** (plugin), not bare pytest. Extrapolate linearly to 250/400 in the PR from those measured `--last 5` wall times only.

---

## 9. Report contents

Schema gate; split sizes; `new_fail` / persistent / parent-missing / always-broken diagnostics; objective comparison; metrics+CI primary+secondary; paired deltas val+test; useful-gate; `data_insufficient`; gain table; leakage canaries; real-data commands when applicable.

---

## 10. Done when

- Package + CLI + CI test step &lt; 60 s; committed reference model fixtures.
- `new_fail` primary path; tie-aware metrics; paired bootstrap gate.
- Chrono assert; schema/NaN gates; deterministic train.
- Real click/jinja via documented commands; measured backfill timing; honest insufficient-data reporting.
- No Rust select / SHAP / Action / GPU.

---

## 11. Locked decisions

| Topic | Choice |
|-------|--------|
| Tabular library | polars |
| Primary label | **`new_fail`** |
| Objective tie-break | binary |
| Useful-gate | paired bootstrap CI(delta)>0 on val |
| Tie handling | expectation within borderline score group |
| Heuristic rename | `fail_per_day` |
| Reference model | fixed 50 rounds, committed fixtures, `raw_score=True` |
| Leakage tests | synthetic ~200×200 |
| Train volume | --last 250 → 400 if needed |
