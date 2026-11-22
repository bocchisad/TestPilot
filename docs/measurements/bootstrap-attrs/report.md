# TestPilot training report

- status: **data_insufficient**
- objective: `lambdarank`
- useful_on_val (useful_on_real_val): null (data_insufficient)
- useful_on_synthetic_val (useful_on_synthetic_val): True
- useful_on_synthetic_test (useful_on_synthetic_test): False
- ranker: `cov_lines`
- bootstrap.verdict: **use_baseline** (insufficient_test_C)
- features_version: 2
- schema_sha256: `9e5e9243326d8870bd33dcf8c59b49d34a76d60e921435f501638db23e2c5269`
- note: useful_on_real_val=null: no real new_fail changes on val (data_insufficient for the real gate)
- note: synthetic data present: test needs ≥20 mutant changes with ≥1 new_fail and ≥10 distinct base_change; metrics shown but no conclusions
- note: bootstrap verdict=use_baseline ranker=cov_lines reason=insufficient_test_C (accept on synth TEST cluster CI only; val is report-only)

## Diagnostics

- n_changes: 277
- fail_per_change_mean: 3.5812274368231045
- new_fail_per_change_mean: 3.0866425992779782
- n_always_broken_tests: 38
- n_label1: 992
- n_persistent_among_label1: 95
- persistent_share_among_label1: 0.09576612903225806
- n_changes_with_new_fail: 61
- n_rows_parent_missing: 1329
- n_persistent: 95
- n_flaky: 0
- n_orphan_vs_parent: 38
- n_high_fail_rate_changes: 0
- exclude_high_fail_rate: True
- mutant_test_new_fail_changes: 16
- mutant_test_distinct_base_change: 5

### Failures per change after HF filter (`label=1`, flaky=0)

- min/p50/p90/max: 0/0.0/5.0/199

- [0,1): 194
- [1,2): 12
- [2,5): 41
- [5,10): 20
- [10,20): 1
- [20,50): 6
- [50,100): 0
- 100+: 3

### new_fail per change after HF filter

- min/p50/p90/max: 0/0.0/4.400000000000006/199

- [0,1): 216
- [1,2): 12
- [2,5): 21
- [5,10): 18
- [10,20): 1
- [20,50): 6
- [50,100): 1
- 100+: 2

### Always-broken tests (≥90% fails, n=38)

- `attrs` `tests/test_dunders.py::TestAddHash::test_hash_works[None0]`: 1/1 (100%)
- `attrs` `tests/test_dunders.py::TestAddHash::test_hash_works[None1]`: 1/1 (100%)
- `attrs` `tests/test_dunders.py::TestAddHash::test_hash_works[None2]`: 1/1 (100%)
- `attrs` `tests/test_dunders.py::TestAddHash::test_hash_works[None3]`: 1/1 (100%)
- `attrs` `tests/test_dunders.py::TestAddHash::test_hash_works[None4]`: 1/1 (100%)
- `attrs` `tests/test_dunders.py::TestAddRepr::test_repr_works[None0]`: 1/1 (100%)
- `attrs` `tests/test_dunders.py::TestAddRepr::test_repr_works[None1]`: 1/1 (100%)
- `attrs` `tests/test_dunders.py::TestEqOrder::test_equal[None0]`: 1/1 (100%)
- `attrs` `tests/test_dunders.py::TestEqOrder::test_equal[None1]`: 1/1 (100%)
- `attrs` `tests/test_dunders.py::TestEqOrder::test_ge[None0]`: 1/1 (100%)
- `attrs` `tests/test_dunders.py::TestEqOrder::test_ge[None1]`: 1/1 (100%)
- `attrs` `tests/test_dunders.py::TestEqOrder::test_ge_unordable[None0]`: 1/1 (100%)
- `attrs` `tests/test_dunders.py::TestEqOrder::test_ge_unordable[None1]`: 1/1 (100%)
- `attrs` `tests/test_dunders.py::TestEqOrder::test_gt[None0]`: 1/1 (100%)
- `attrs` `tests/test_dunders.py::TestEqOrder::test_gt[None1]`: 1/1 (100%)
- `attrs` `tests/test_dunders.py::TestEqOrder::test_gt_unordable[None0]`: 1/1 (100%)
- `attrs` `tests/test_dunders.py::TestEqOrder::test_gt_unordable[None1]`: 1/1 (100%)
- `attrs` `tests/test_dunders.py::TestEqOrder::test_le[None0]`: 1/1 (100%)
- `attrs` `tests/test_dunders.py::TestEqOrder::test_le[None1]`: 1/1 (100%)
- `attrs` `tests/test_dunders.py::TestEqOrder::test_le_unordable[None0]`: 1/1 (100%)
- `attrs` `tests/test_dunders.py::TestEqOrder::test_le_unordable[None1]`: 1/1 (100%)
- `attrs` `tests/test_dunders.py::TestEqOrder::test_lt[None0]`: 1/1 (100%)
- `attrs` `tests/test_dunders.py::TestEqOrder::test_lt[None1]`: 1/1 (100%)
- `attrs` `tests/test_dunders.py::TestEqOrder::test_lt_unordable[None0]`: 1/1 (100%)
- `attrs` `tests/test_dunders.py::TestEqOrder::test_lt_unordable[None1]`: 1/1 (100%)
- `attrs` `tests/test_dunders.py::TestEqOrder::test_unequal_different_class[None0]`: 1/1 (100%)
- `attrs` `tests/test_dunders.py::TestEqOrder::test_unequal_different_class[None1]`: 1/1 (100%)
- `attrs` `tests/test_dunders.py::TestEqOrder::test_unequal_same_class[None0]`: 1/1 (100%)
- `attrs` `tests/test_dunders.py::TestEqOrder::test_unequal_same_class[None1]`: 1/1 (100%)
- `attrs` `tests/test_functional.py::TestFunctional::test_frozen_instance[None]`: 1/1 (100%)
- `attrs` `tests/test_functional.py::TestFunctional::test_pickle_attributes[2-None]`: 1/1 (100%)
- `attrs` `tests/test_functional.py::TestFunctional::test_pickle_attributes[3-None]`: 1/1 (100%)
- `attrs` `tests/test_functional.py::TestFunctional::test_pickle_attributes[4-None]`: 1/1 (100%)
- `attrs` `tests/test_functional.py::TestFunctional::test_pickle_attributes[5-None]`: 1/1 (100%)
- `attrs` `tests/test_functional.py::TestFunctional::test_pickle_object[2-None]`: 1/1 (100%)
- `attrs` `tests/test_functional.py::TestFunctional::test_pickle_object[3-None]`: 1/1 (100%)
- `attrs` `tests/test_functional.py::TestFunctional::test_pickle_object[4-None]`: 1/1 (100%)
- `attrs` `tests/test_functional.py::TestFunctional::test_pickle_object[5-None]`: 1/1 (100%)

## Val metrics (new_fail)

- recall@5%: {'mean': 1.0, 'ci95': [1.0, 1.0], 'n_changes': 4}
- recall@10%: {'mean': 1.0, 'ci95': [1.0, 1.0], 'n_changes': 4}
- recall@20%: {'mean': 1.0, 'ci95': [1.0, 1.0], 'n_changes': 4}
- apfd: {'mean': 0.9947471910112359, 'ci95': [0.9947471910112359, 0.9947471910112359], 'n_changes': 4}
- first_fail_frac: {'mean': 0.001966292134831461, 'ci95': [0.001966292134831461, 0.001966292134831461], 'n_changes': 4}
- all_fail_frac: {'mean': 0.008614232209737827, 'ci95': [0.008614232209737827, 0.008614232209737827], 'n_changes': 4}

## Baselines (recall@10%)

- best for paired gate: `cov_lines`

### Val

- `random`: recall@10%=0.0996 ci95=[0.099625468164794, 0.099625468164794] n=4
- `t_fail_rate`: recall@10%=0.0969 ci95=[0.09691960931630353, 0.09691960931630354] n=4
- `cc_max`: recall@10%=0.0000 ci95=[0.0, 0.0] n=4
- `imp_dist`: recall@10%=0.0244 ci95=[0.0, 0.07334558823529412] n=4
- `cov_lines`: recall@10%=0.6914 ci95=[0.5884955752212389, 0.8971238938053098] n=4
- `fail_per_day`: recall@10%=0.0969 ci95=[0.09691960931630353, 0.09691960931630354] n=4

### Test

- `random`: recall@10%=0.0998 ci95=[0.09965448940554385, 0.09990666023695811] n=16
- `t_fail_rate`: recall@10%=0.0971 ci95=[0.09695808533170752, 0.09721176567123035] n=16
- `cc_max`: recall@10%=0.3962 ci95=[0.2038113288724869, 0.6200980392156863] n=16
- `imp_dist`: recall@10%=0.3024 ci95=[0.15381402754342247, 0.4766394855137164] n=16
- `cov_lines`: recall@10%=0.8498 ci95=[0.689268520019452, 0.9792713567839196] n=16
- `fail_per_day`: recall@10%=0.0971 ci95=[0.09695808533170752, 0.09721176567123035] n=16

### Val synthetic

- `random`: recall@10%=0.0996 ci95=[0.099625468164794, 0.099625468164794] n=4
- `t_fail_rate`: recall@10%=0.0969 ci95=[0.09691960931630353, 0.09691960931630354] n=4
- `cc_max`: recall@10%=0.0000 ci95=[0.0, 0.0] n=4
- `imp_dist`: recall@10%=0.0244 ci95=[0.0, 0.07334558823529412] n=4
- `cov_lines`: recall@10%=0.6914 ci95=[0.5884955752212389, 0.8971238938053098] n=4
- `fail_per_day`: recall@10%=0.0969 ci95=[0.09691960931630353, 0.09691960931630354] n=4

### Test synthetic

- `random`: recall@10%=0.0998 ci95=[0.09965923857708957, 0.0999111745586874] n=16
- `t_fail_rate`: recall@10%=0.0971 ci95=[0.09696265331195021, 0.09721490574974953] n=16
- `cc_max`: recall@10%=0.3962 ci95=[0.18744862726459405, 0.5992647058823529] n=16
- `imp_dist`: recall@10%=0.3024 ci95=[0.15318712297872805, 0.46935765759295167] n=16
- `cov_lines`: recall@10%=0.8498 ci95=[0.6946574910844545, 0.9792713567839196] n=16
- `fail_per_day`: recall@10%=0.0971 ci95=[0.09696265331195021, 0.09721490574974953] n=16


## LORO (macro recall@10% new_fail on held-out test)

- macro: None

## Paired delta (model − best baseline)

- val: {'mean': 0.3086283185840708, 'ci95': [0.3086283185840708, 0.3086283185840708], 'n': 4}
- test: {'mean': 0.004518965796725562, 'ci95': [-0.045768500948766604, 0.07106963388370423], 'n': 16}
- synthetic val vs cov_lines (report-only): {'mean': 0.3086283185840708, 'ci95': [0.3086283185840708, 0.3086283185840708], 'n': 4}
- synthetic test vs cov_lines (gate): {'mean': 0.004518965796725562, 'ci95': [-0.045840014231499054, 0.07106963388370423], 'n': 16, 'n_C': 5, 'n_mutants': 16}

## Candidates

- {'objective': 'binary', 'num_boost_round': 500, 'early_stopping_rounds': 50, 'stopping_metric': 'auc', 'best_iteration': 46, 'val_recall@10%_new_fail': 0.3}
- {'objective': 'lambdarank', 'num_boost_round': 500, 'early_stopping_rounds': 50, 'stopping_metric': 'ndcg', 'best_iteration': 3, 'val_recall@10%_new_fail': 1.0}

## Feature gain (top)

- cov_lines: 700.2875329554081
- cov_func: 196.345987085253
- cov_mod: 127.5199966430664
- tfile_ntests: 75.82354974746704
- cov_breadth: 74.84732583910227
- cc_max: 69.0185421705246
- t_obs: 61.204978719353676
- dur: 55.26114147901535
- t_age_days: 23.53788983821869
- imp_dist: 17.53300976753235
- imp_direct: 10.858199834823608
- ch_rm: 7.6171698570251465
- name_jac: 6.358439922332764
- name_file: 3.5890668965876102
- cc_sum: 0.0
- name_func: 0.0
- cov_known: 0.0
- cov_file: 0.0
- cov_lag: 0.0
- t_fail_rate: 0.0

## Feature value stats (synthetic)

| feature | n_unique | n_rows | sentinel | sentinel_share |
|---|---:|---:|---:|---:|
| `cov_lag` | 1 | 102317 | None | 0.0000 |
| `t_changed` | 1 | 102317 | None | 0.0000 |
| `t_flaky_rate` | 1 | 102317 | None | 0.0000 |
| `imp_direct` | 2 | 102317 | None | 0.0000 |
| `name_file` | 2 | 102317 | None | 0.0000 |
| `name_func` | 2 | 102317 | None | 0.0000 |
| `cov_known` | 2 | 102317 | None | 0.0000 |
| `cov_file` | 2 | 102317 | None | 0.0000 |
| `cov_func` | 2 | 102317 | None | 0.0000 |
| `cov_mod` | 2 | 102317 | None | 0.0000 |
| `ch_nfiles` | 2 | 102317 | None | 0.0000 |
| `ch_nsrc` | 2 | 102317 | None | 0.0000 |
| `ch_add` | 2 | 102317 | None | 0.0000 |
| `name_jac` | 3 | 102317 | None | 0.0000 |
| `ch_nfunc` | 3 | 102317 | None | 0.0000 |
| `t_fail_cnt` | 4 | 102317 | None | 0.0000 |
| `imp_dist` | 5 | 102317 | 99 | 0.3165 |
| `cov_lines` | 9 | 102317 | None | 0.0000 |
| `ch_rm` | 9 | 102317 | None | 0.0000 |
| `pq_max` | 11 | 102317 | -1.0 | 0.1695 |
| `pf_max` | 19 | 102317 | -1.0 | 0.0779 |
| `tfile_ntests` | 31 | 102317 | None | 0.0000 |
| `t_fail_rate` | 52 | 102317 | None | 0.0000 |
| `t_obs` | 102 | 102317 | None | 0.0000 |
| `cc_max` | 323 | 102317 | None | 0.0000 |
| `cc_sum` | 323 | 102317 | None | 0.0000 |
| `dur` | 329 | 102317 | -1 | 0.0004 |
| `t_age_days` | 498 | 102317 | None | 0.0000 |
| `cov_breadth` | 585 | 102317 | None | 0.0000 |

## Provenance

- Attrs pair split is corrected by Git history, but the pair-level ledger was not committed: 36 pairs are attributed to the last run reported at `978b50b`; exact per-pair script versions are unrecorded. `scripts/mutate_bootstrap.py` has blob `548fcc2e98285d7d6bd2dea29d8e4c4b144b4620` at `092d6cc`, `9e863f1`, and `978b50b`. `scripts/mutate/run_mutant.py` differs across those commits (`b47ec90…`, `7cdb093…`, and `3a6f035…` respectively), so do not infer one exact classifier version for all 36 pairs.
- The other 76 of 112 pairs are an estimate across varying classifier versions before `978b50b`; the pair-level ledger is absent.
- Aggregator (`scripts/aggregate_bootstrap_w6.py`): commit `092d6cc30a09f409aea1b1413ab62976d60430fd`, blob `0a7c3bb9ad7ebde626c75ae2266abd30ae254302`.
