# TestPilot training report

## Week 6 interpretation

- LORO macro recall@10%: model 0.710 vs cov_lines 0.716 (Δ -0.006).
- attrs: model 0.854, cov_lines 0.850 (Δ +0.005); gate `use_baseline` (insufficient_test_C, n_C=5).
- click: model 0.407, cov_lines 0.428 (Δ -0.021); gate `use_baseline` (insufficient_test_C, n_C=5).
- jinja: model 0.868, cov_lines 0.870 (Δ -0.002); gate `use_model` (synth_test_ci_low_ge_0, n_C=11).
- Pooled TEST vs cov_lines: Δ +0.027, 95% CI [0.005, 0.054], n=48, n_C=21.
- Aggregate bootstrap verdict is `use_model` on the pooled synthetic TEST gate; that result is distinct from per-repository gates and does not change the global default.
- Conclusion: the model is approximately tied with cov_lines; cov_lines has the strongest feature gain. Keep cov_lines as the default.
- Limitation: the dry-run estimate underpredicted measured wall time by 32–42%. Click and attrs each had TEST n_C=5 (<10); widening TEST is deferred to Week 7.
- `env.date_utc` is the environment fingerprint captured before the measurement runs, not the run start time.

## Provenance

- Click classifier: commit `6c3fc2c4ffd7fc7599f2313a0dfcb86093b3dbac`, blob `55bacb570a74e4625eddab73ed155698c326db81`.
- Jinja classifier: exact runtime source is unconfirmed. The binary identifies candidate commit `b4d5bfaf9e18316895970058c87e105b0a404293` / blob `dfa03ce9d32c487bf0ccda0f5f95f3a4a37a245b`, but the saved ledger records `M4c044f` with `pytest_rc=4` and `collect_note`; the candidate source handles missing-JUnit collection errors for rc=2 only, while rc=4 handling appears in later source. The binary version alone does not prove which Python classifier was loaded for that row.
- Attrs labels: attribution corrected by Git history, but exact per-pair versions are unrecorded because the pair-level ledger was not committed. The 36 pairs are from the last run reported at `978b50b`; `mutate_bootstrap.py` is blob `548fcc2e98285d7d6bd2dea29d8e4c4b144b4620` at `092d6cc`, `9e863f1`, and `978b50b`, while `run_mutant.py` differs at those commits (`b47ec90…`, `7cdb093…`, `3a6f035…`). The other 76 of 112 pairs are an estimate across varying classifier versions before `978b50b`; the pair-level ledger is absent.
- Mutation ledger: `scripts/mutate_bootstrap.py`, commit `d7c73f1e1a8fdf207c8f044ba0f160dbb9288912`, blob `2f09ab29a91b597369cb7d76dfbade9dc397ecca`.
- Aggregator: `scripts/aggregate_bootstrap_w6.py`, commit `092d6cc30a09f409aea1b1413ab62976d60430fd`, blob `0a7c3bb9ad7ebde626c75ae2266abd30ae254302`.
- Measurement runner: `scripts/measure_bootstrap_w6.sh`, commit `d7c73f1e1a8fdf207c8f044ba0f160dbb9288912`, blob `f408b62d49636c259dd6980f725a5fada0311bf7`.

- status: **ok**
- objective: `lambdarank`
- useful_on_val (useful_on_real_val): True
- useful_on_synthetic_val (useful_on_synthetic_val): True
- useful_on_synthetic_test (useful_on_synthetic_test): True
- ranker: `model`
- bootstrap.verdict: **use_model** (synth_test_ci_low_ge_0)
- features_version: 2
- schema_sha256: `9e5e9243326d8870bd33dcf8c59b49d34a76d60e921435f501638db23e2c5269`
- note: bootstrap verdict=use_model ranker=model reason=synth_test_ci_low_ge_0 (accept on synth TEST cluster CI only; val is report-only)

## Diagnostics

- n_changes: 963
- fail_per_change_mean: 19.77673935617861
- new_fail_per_change_mean: 8.818276220145378
- n_always_broken_tests: 79
- n_label1: 19045
- n_persistent_among_label1: 10372
- persistent_share_among_label1: 0.5446048831714361
- n_changes_with_new_fail: 286
- n_rows_parent_missing: 3499
- n_persistent: 11033
- n_flaky: 76
- n_orphan_vs_parent: 82
- n_high_fail_rate_changes: 13
- exclude_high_fail_rate: True
- fail_per_change_mean_including_high_fail: 28.335040983606557
- new_fail_per_change_mean_including_high_fail: 16.8452868852459
- mutant_test_new_fail_changes: 48
- mutant_test_distinct_base_change: 21

### Failures per change after HF filter (`label=1`, flaky=0)

- min/p50/p90/max: 0/3.0/94.0/344

- [0,1): 301
- [1,2): 116
- [2,5): 271
- [5,10): 72
- [10,20): 23
- [20,50): 40
- [50,100): 104
- 100+: 36

### new_fail per change after HF filter

- min/p50/p90/max: 0/0.0/16.0/344

- [0,1): 677
- [1,2): 54
- [2,5): 69
- [5,10): 44
- [10,20): 30
- [20,50): 43
- [50,100): 22
- 100+: 24

### Failures per change including high-fail-rate mutants

- min/p50/p90/max: 0/3.0/94.0/790

- [0,1): 301
- [1,2): 116
- [2,5): 271
- [5,10): 72
- [10,20): 23
- [20,50): 40
- [50,100): 104
- 100+: 49

### new_fail per change including high-fail-rate mutants

- min/p50/p90/max: 0/0.0/21.0/790

- [0,1): 677
- [1,2): 54
- [2,5): 69
- [5,10): 44
- [10,20): 30
- [20,50): 43
- [50,100): 22
- 100+: 37

### Always-broken tests (≥90% fails, n=79)

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
- `jinja` `tests/test_asyncfilters.py::test_attribute_map[<lambda>0]`: 55/55 (100%)
- `jinja` `tests/test_asyncfilters.py::test_attribute_map[<lambda>1]`: 55/55 (100%)
- `jinja` `tests/test_asyncfilters.py::test_bool_reject[<lambda>0]`: 55/55 (100%)
- `jinja` `tests/test_asyncfilters.py::test_bool_reject[<lambda>1]`: 55/55 (100%)
- `jinja` `tests/test_asyncfilters.py::test_bool_select[<lambda>0]`: 55/55 (100%)
- `jinja` `tests/test_asyncfilters.py::test_bool_select[<lambda>1]`: 55/55 (100%)
- `jinja` `tests/test_asyncfilters.py::test_custom_async_filter`: 55/55 (100%)
- `jinja` `tests/test_asyncfilters.py::test_custom_async_iteratable_filter[<lambda>0]`: 55/55 (100%)
- `jinja` `tests/test_asyncfilters.py::test_custom_async_iteratable_filter[<lambda>1]`: 55/55 (100%)
- `jinja` `tests/test_asyncfilters.py::test_empty_map`: 55/55 (100%)
- `jinja` `tests/test_asyncfilters.py::test_first[<lambda>0]`: 55/55 (100%)
- `jinja` `tests/test_asyncfilters.py::test_first[<lambda>1]`: 55/55 (100%)
- … +29 more

## Val metrics (new_fail)

- recall@5%: {'mean': 0.8903913925109422, 'ci95': [0.7365037509390779, 0.99277950310559], 'n_changes': 22}
- recall@10%: {'mean': 0.9304944273574436, 'ci95': [0.8142283423335007, 1.0], 'n_changes': 22}
- recall@20%: {'mean': 0.949842602350643, 'ci95': [0.8604157830871091, 1.0], 'n_changes': 22}
- apfd: {'mean': 0.9658806700727783, 'ci95': [0.9133142587279888, 0.9934415900117615], 'n_changes': 22}
- first_fail_frac: {'mean': 0.0021044119487192313, 'ci95': [0.0013195982957498029, 0.0031985869264961847], 'n_changes': 22}
- all_fail_frac: {'mean': 0.07718174152972314, 'ci95': [0.01298115522753352, 0.19305986450309803], 'n_changes': 22}

## Val metrics including high-fail-rate mutants

- recall@5%: {'mean': 0.8903913925109422, 'ci95': [0.7472254370038913, 0.9790697674418604], 'n_changes': 22}
- recall@10%: {'mean': 0.9304944273574436, 'ci95': [0.814180607062956, 0.9991055456171735], 'n_changes': 22}
- recall@20%: {'mean': 0.949842602350643, 'ci95': [0.8601027401338739, 1.0], 'n_changes': 22}
- apfd: {'mean': 0.9658806700727783, 'ci95': [0.9136479681461942, 0.9925993064299851], 'n_changes': 22}
- first_fail_frac: {'mean': 0.0021044119487192313, 'ci95': [0.0013182244575210029, 0.0031940540249435585], 'n_changes': 22}
- all_fail_frac: {'mean': 0.07718174152972314, 'ci95': [0.014719298478988322, 0.18803900024949555], 'n_changes': 22}

## Val metrics excluding high-fail-rate mutants

- recall@5%: {'mean': 0.8903913925109422, 'ci95': [0.7365037509390779, 0.99277950310559], 'n_changes': 22}
- recall@10%: {'mean': 0.9304944273574436, 'ci95': [0.8142283423335007, 1.0], 'n_changes': 22}
- recall@20%: {'mean': 0.949842602350643, 'ci95': [0.8604157830871091, 1.0], 'n_changes': 22}
- apfd: {'mean': 0.9658806700727783, 'ci95': [0.9133142587279888, 0.9934415900117615], 'n_changes': 22}
- first_fail_frac: {'mean': 0.0021044119487192313, 'ci95': [0.0013195982957498029, 0.0031985869264961847], 'n_changes': 22}
- all_fail_frac: {'mean': 0.07718174152972314, 'ci95': [0.01298115522753352, 0.19305986450309803], 'n_changes': 22}

## Test metrics including high-fail-rate mutants

- recall@5%: {'mean': 0.7095377301466236, 'ci95': [0.5153429297694824, 0.8627762107268513], 'n_changes': 50}
- recall@10%: {'mean': 0.7712218680852281, 'ci95': [0.6044307886186184, 0.9023663988023611], 'n_changes': 50}
- recall@20%: {'mean': 0.8736238537237611, 'ci95': [0.7716116194197671, 0.9510710758555427], 'n_changes': 50}
- apfd: {'mean': 0.9276072221343997, 'ci95': [0.8774219590096896, 0.9662448526291786], 'n_changes': 50}
- first_fail_frac: {'mean': 0.00723495501231568, 'ci95': [0.002069592135830427, 0.015904051325277817], 'n_changes': 50}
- all_fail_frac: {'mean': 0.15179661398974367, 'ci95': [0.06835150736652487, 0.26297930720231716], 'n_changes': 50}

## Test metrics excluding high-fail-rate mutants

- recall@5%: {'mean': 0.7363125292727425, 'ci95': [0.5443454218297814, 0.9133598042801624], 'n_changes': 48}
- recall@10%: {'mean': 0.7975310183386609, 'ci95': [0.6442153211268875, 0.9453238195797053], 'n_changes': 48}
- recall@20%: {'mean': 0.8984099249015317, 'ci95': [0.8170567669265872, 0.9751773049645389], 'n_changes': 48}
- apfd: {'mean': 0.9398101842102635, 'ci95': [0.8971747027095623, 0.9810165803957376], 'n_changes': 48}
- first_fail_frac: {'mean': 0.007377745230196352, 'ci95': [0.0017300262051347004, 0.01620668068192364], 'n_changes': 48}
- all_fail_frac: {'mean': 0.1241805357388821, 'ci95': [0.040395275642409224, 0.21650408304785895], 'n_changes': 48}

## Baselines (recall@10%)

- best for paired gate: `cov_lines`

### Val

- `random`: recall@10%=0.0996 ci95=[0.09954594870989929, 0.09971487748673617] n=22
- `t_fail_rate`: recall@10%=0.2495 ci95=[0.13263493880998603, 0.38988750225872104] n=22
- `cc_max`: recall@10%=0.1879 ci95=[0.07726892270280541, 0.3105845783234922] n=22
- `imp_dist`: recall@10%=0.1686 ci95=[0.07630419483989755, 0.2845382768733253] n=22
- `cov_lines`: recall@10%=0.8291 ci95=[0.693198641195793, 0.9403787231409148] n=22
- `fail_per_day`: recall@10%=0.2495 ci95=[0.13263493880998603, 0.38988750225872104] n=22

### Test

- `random`: recall@10%=0.0995 ci95=[0.0994528832905931, 0.09963787892459709] n=48
- `t_fail_rate`: recall@10%=0.0818 ci95=[0.0623385443545403, 0.1039837751543274] n=48
- `cc_max`: recall@10%=0.4717 ci95=[0.35283315145898964, 0.5934734339939235] n=48
- `imp_dist`: recall@10%=0.4072 ci95=[0.30519337587823053, 0.5122803705389243] n=48
- `cov_lines`: recall@10%=0.7710 ci95=[0.6723072923530307, 0.8659969246555856] n=48
- `fail_per_day`: recall@10%=0.0818 ci95=[0.0623385443545403, 0.1039837751543274] n=48

### Val synthetic

- `random`: recall@10%=0.0996 ci95=[0.0995307109742763, 0.09970934060260318] n=21
- `t_fail_rate`: recall@10%=0.2138 ci95=[0.10771518557981104, 0.33490702776840897] n=21
- `cc_max`: recall@10%=0.1969 ci95=[0.08748404725511341, 0.31230903827858814] n=21
- `imp_dist`: recall@10%=0.1766 ci95=[0.07605645179861001, 0.28500109273931973] n=21
- `cov_lines`: recall@10%=0.8639 ci95=[0.7476587793699468, 0.9597144116521595] n=21
- `fail_per_day`: recall@10%=0.2138 ci95=[0.10771518557981104, 0.33490702776840897] n=21

### Test synthetic

- `random`: recall@10%=0.0995 ci95=[0.099454019470398, 0.09963873808650765] n=48
- `t_fail_rate`: recall@10%=0.0818 ci95=[0.06289287448575899, 0.10352046853759238] n=48
- `cc_max`: recall@10%=0.4717 ci95=[0.3519152587522905, 0.5863279310393722] n=48
- `imp_dist`: recall@10%=0.4072 ci95=[0.3017239635948434, 0.5137017958638755] n=48
- `cov_lines`: recall@10%=0.7710 ci95=[0.6634227038604498, 0.8652115584976456] n=48
- `fail_per_day`: recall@10%=0.0818 ci95=[0.06289287448575899, 0.10352046853759238] n=48


## LORO (macro recall@10% new_fail on held-out test)

- macro: 0.7098951204632825
- attrs: model=0.8542713567839196 n_changes=16
  - baselines: cov_lines=0.849752390987194, cc_max=0.3962356171873058, random=0.09978527279271918
- click: model=0.4073467590446098 n_changes=10
  - baselines: cov_lines=0.42792188538570725, cc_max=0.16316964067662662, random=0.09939805619554568
- jinja: model=0.8680672455613184 n_changes=22
  - baselines: cov_lines=0.8695896473600615, cc_max=0.6667402193601016, random=0.09943594771363616

## Paired delta (model − best baseline)

- val: {'mean': 0.10136846673118187, 'ci95': [0.0008611300894788722, 0.2282015554068978], 'n': 22}
- test: {'mean': 0.026567906847545734, 'ci95': [0.004897452006012426, 0.053819629480489704], 'n': 48}
- synthetic val vs cov_lines (report-only): {'mean': 0.0633306000935661, 'ci95': [0.0, 0.1656325881452237], 'n': 21}
- synthetic test vs cov_lines (gate): {'mean': 0.026567906847545734, 'ci95': [0.00486235355699223, 0.05086344155495714], 'n': 48, 'n_C': 21, 'n_mutants': 48}

## Candidates

- {'objective': 'binary', 'num_boost_round': 500, 'early_stopping_rounds': 50, 'stopping_metric': 'auc', 'best_iteration': 38, 'val_recall@10%_new_fail': 0.9050678488608356}
- {'objective': 'lambdarank', 'num_boost_round': 500, 'early_stopping_rounds': 50, 'stopping_metric': 'ndcg', 'best_iteration': 26, 'val_recall@10%_new_fail': 0.930252647860345}

## Feature gain (top)

- cov_lines: 22049.184993743896
- cov_breadth: 884.9757597446442
- tfile_ntests: 788.2666447162628
- cov_file: 708.50475025177
- cov_func: 628.1296616792679
- t_age_days: 610.446410536766
- t_obs: 496.09431862831116
- cov_mod: 431.0036063194275
- cc_max: 361.8449196815491
- ch_nsrc: 340.01341915130615
- name_file: 202.74793195724487
- ch_nfiles: 192.79509925842285
- dur: 175.94149947166443
- pq_max: 175.60547924041748
- name_jac: 164.4714789390564
- ch_nfunc: 153.96877002716064
- imp_dist: 133.53306078910828
- cc_sum: 117.36489939689636
- ch_rm: 101.1911404132843
- imp_direct: 28.680561065673828

## Feature value stats (synthetic)

| feature | n_unique | n_rows | sentinel | sentinel_share |
|---|---:|---:|---:|---:|
| `cov_lag` | 1 | 320346 | None | 0.0000 |
| `t_changed` | 1 | 320346 | None | 0.0000 |
| `t_flaky_rate` | 1 | 320346 | None | 0.0000 |
| `imp_direct` | 2 | 320346 | None | 0.0000 |
| `name_file` | 2 | 320346 | None | 0.0000 |
| `name_func` | 2 | 320346 | None | 0.0000 |
| `cov_known` | 2 | 320346 | None | 0.0000 |
| `cov_file` | 2 | 320346 | None | 0.0000 |
| `cov_func` | 2 | 320346 | None | 0.0000 |
| `cov_mod` | 2 | 320346 | None | 0.0000 |
| `ch_nfiles` | 2 | 320346 | None | 0.0000 |
| `ch_nsrc` | 2 | 320346 | None | 0.0000 |
| `ch_add` | 2 | 320346 | None | 0.0000 |
| `ch_nfunc` | 4 | 320346 | None | 0.0000 |
| `imp_dist` | 6 | 320346 | 99 | 0.1578 |
| `name_jac` | 6 | 320346 | None | 0.0000 |
| `cov_lines` | 9 | 320346 | None | 0.0000 |
| `ch_rm` | 9 | 320346 | None | 0.0000 |
| `pq_max` | 49 | 320346 | -1.0 | 0.1431 |
| `t_fail_cnt` | 87 | 320346 | None | 0.0000 |
| `tfile_ntests` | 93 | 320346 | None | 0.0000 |
| `pf_max` | 108 | 320346 | -1.0 | 0.0372 |
| `t_obs` | 199 | 320346 | None | 0.0000 |
| `t_fail_rate` | 279 | 320346 | None | 0.0000 |
| `dur` | 346 | 320346 | -1 | 0.0001 |
| `cov_breadth` | 1123 | 320346 | None | 0.0000 |
| `cc_max` | 1710 | 320346 | None | 0.0000 |
| `cc_sum` | 1710 | 320346 | None | 0.0000 |
| `t_age_days` | 6347 | 320346 | None | 0.0000 |
