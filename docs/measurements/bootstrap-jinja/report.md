# TestPilot training report

- status: **ok**
- objective: `lambdarank`
- useful_on_val (useful_on_real_val): null (data_insufficient)
- useful_on_synthetic_val (useful_on_synthetic_val): False
- useful_on_synthetic_test (useful_on_synthetic_test): True
- ranker: `model`
- bootstrap.verdict: **use_model** (synth_test_ci_low_ge_0)
- features_version: 2
- schema_sha256: `9e5e9243326d8870bd33dcf8c59b49d34a76d60e921435f501638db23e2c5269`
- note: useful_on_real_val=null: no real new_fail changes on val (data_insufficient for the real gate)
- note: bootstrap verdict=use_model ranker=model reason=synth_test_ci_low_ge_0 (accept on synth TEST cluster CI only; val is report-only)

## Diagnostics

- n_changes: 356
- fail_per_change_mean: 36.502808988764045
- new_fail_per_change_mean: 9.794943820224718
- n_always_broken_tests: 41
- n_label1: 12995
- n_persistent_among_label1: 9373
- persistent_share_among_label1: 0.72127741439015
- n_changes_with_new_fail: 114
- n_rows_parent_missing: 1664
- n_persistent: 10031
- n_flaky: 0
- n_orphan_vs_parent: 44
- n_high_fail_rate_changes: 11
- exclude_high_fail_rate: True
- fail_per_change_mean_including_high_fail: 57.119891008174385
- new_fail_per_change_mean_including_high_fail: 29.419618528610354
- mutant_test_new_fail_changes: 22
- mutant_test_distinct_base_change: 11

### Failures per change after HF filter (`label=1`, flaky=0)

- min/p50/p90/max: 0/2.0/94.0/344

- [0,1): 56
- [1,2): 103
- [2,5): 42
- [5,10): 16
- [10,20): 7
- [20,50): 20
- [50,100): 90
- 100+: 22

### new_fail per change after HF filter

- min/p50/p90/max: 0/0.0/24.0/344

- [0,1): 242
- [1,2): 24
- [2,5): 21
- [5,10): 13
- [10,20): 11
- [20,50): 25
- [50,100): 9
- 100+: 11

### Failures per change including high-fail-rate mutants

- min/p50/p90/max: 0/2.0/95.0/790

- [0,1): 56
- [1,2): 103
- [2,5): 42
- [5,10): 16
- [10,20): 7
- [20,50): 20
- [50,100): 90
- 100+: 33

### new_fail per change including high-fail-rate mutants

- min/p50/p90/max: 0/0.0/42.0/790

- [0,1): 242
- [1,2): 24
- [2,5): 21
- [5,10): 13
- [10,20): 11
- [20,50): 25
- [50,100): 9
- 100+: 22

### Always-broken tests (≥90% fails, n=41)

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
- `jinja` `tests/test_asyncfilters.py::test_groupby[<lambda>0]`: 55/55 (100%)
- `jinja` `tests/test_asyncfilters.py::test_groupby[<lambda>1]`: 55/55 (100%)
- `jinja` `tests/test_asyncfilters.py::test_groupby_multidot[<lambda>0]`: 55/55 (100%)
- `jinja` `tests/test_asyncfilters.py::test_groupby_multidot[<lambda>1]`: 55/55 (100%)
- `jinja` `tests/test_asyncfilters.py::test_groupby_tuple_index[<lambda>0]`: 55/55 (100%)
- `jinja` `tests/test_asyncfilters.py::test_groupby_tuple_index[<lambda>1]`: 55/55 (100%)
- `jinja` `tests/test_asyncfilters.py::test_join_attribute[<lambda>0]`: 55/55 (100%)
- `jinja` `tests/test_asyncfilters.py::test_join_attribute[<lambda>1]`: 55/55 (100%)
- `jinja` `tests/test_asyncfilters.py::test_join_env_int[<lambda>0]`: 55/55 (100%)
- `jinja` `tests/test_asyncfilters.py::test_join_env_int[<lambda>1]`: 55/55 (100%)
- `jinja` `tests/test_asyncfilters.py::test_join_string_list[<lambda>0]`: 55/55 (100%)
- `jinja` `tests/test_asyncfilters.py::test_join_string_list[<lambda>1]`: 55/55 (100%)
- `jinja` `tests/test_asyncfilters.py::test_map_sum`: 55/55 (100%)
- `jinja` `tests/test_asyncfilters.py::test_simple_map[<lambda>0]`: 55/55 (100%)
- `jinja` `tests/test_asyncfilters.py::test_simple_map[<lambda>1]`: 55/55 (100%)
- `jinja` `tests/test_asyncfilters.py::test_simple_reject[<lambda>0]`: 55/55 (100%)
- `jinja` `tests/test_asyncfilters.py::test_simple_reject[<lambda>1]`: 55/55 (100%)
- `jinja` `tests/test_asyncfilters.py::test_simple_select[<lambda>0]`: 55/55 (100%)
- `jinja` `tests/test_asyncfilters.py::test_simple_select[<lambda>1]`: 55/55 (100%)
- `jinja` `tests/test_asyncfilters.py::test_simple_select_attr[<lambda>0]`: 55/55 (100%)
- `jinja` `tests/test_asyncfilters.py::test_simple_select_attr[<lambda>1]`: 55/55 (100%)
- `jinja` `tests/test_asyncfilters.py::test_slice[<lambda>0]`: 55/55 (100%)
- `jinja` `tests/test_asyncfilters.py::test_slice[<lambda>1]`: 55/55 (100%)
- `jinja` `tests/test_asyncfilters.py::test_sum[<lambda>0]`: 55/55 (100%)
- `jinja` `tests/test_asyncfilters.py::test_sum[<lambda>1]`: 55/55 (100%)
- `jinja` `tests/test_asyncfilters.py::test_sum_attributes[<lambda>0]`: 55/55 (100%)
- `jinja` `tests/test_asyncfilters.py::test_sum_attributes[<lambda>1]`: 55/55 (100%)
- `jinja` `tests/test_asyncfilters.py::test_sum_attributes_nested`: 55/55 (100%)
- `jinja` `tests/test_asyncfilters.py::test_sum_attributes_tuple`: 55/55 (100%)

## Val metrics (new_fail)

- recall@5%: {'mean': 0.8278967230712747, 'ci95': [0.498074782536127, 0.991600790513834], 'n_changes': 9}
- recall@10%: {'mean': 0.9006209316445687, 'ci95': [0.6090903254331342, 1.0], 'n_changes': 9}
- recall@20%: {'mean': 0.9153742357492836, 'ci95': [0.6604391209439998, 1.0], 'n_changes': 9}
- apfd: {'mean': 0.9381094974909172, 'ci95': [0.7955963995960296, 0.9913329708263432], 'n_changes': 9}
- first_fail_frac: {'mean': 0.0018283248172352206, 'ci95': [0.0011493186550394787, 0.002362496374114048], 'n_changes': 9}
- all_fail_frac: {'mean': 0.12498980989231508, 'ci95': [0.01649282791247234, 0.4147642622807494], 'n_changes': 9}

## Val metrics including high-fail-rate mutants

- recall@5%: {'mean': 0.8278967230712747, 'ci95': [0.48078742462945667, 0.9932806324110672], 'n_changes': 9}
- recall@10%: {'mean': 0.9006209316445687, 'ci95': [0.5644220993773034, 1.0], 'n_changes': 9}
- recall@20%: {'mean': 0.9153742357492836, 'ci95': [0.6191840608717754, 1.0], 'n_changes': 9}
- apfd: {'mean': 0.9381094974909172, 'ci95': [0.7757796504978393, 0.9908910243031609], 'n_changes': 9}
- first_fail_frac: {'mean': 0.0018283248172352206, 'ci95': [0.0011493186550394787, 0.0023631335829892423], 'n_changes': 9}
- all_fail_frac: {'mean': 0.12498980989231508, 'ci95': [0.017376720958836783, 0.4563987749677055], 'n_changes': 9}

## Val metrics excluding high-fail-rate mutants

- recall@5%: {'mean': 0.8278967230712747, 'ci95': [0.498074782536127, 0.991600790513834], 'n_changes': 9}
- recall@10%: {'mean': 0.9006209316445687, 'ci95': [0.6090903254331342, 1.0], 'n_changes': 9}
- recall@20%: {'mean': 0.9153742357492836, 'ci95': [0.6604391209439998, 1.0], 'n_changes': 9}
- apfd: {'mean': 0.9381094974909172, 'ci95': [0.7955963995960296, 0.9913329708263432], 'n_changes': 9}
- first_fail_frac: {'mean': 0.0018283248172352206, 'ci95': [0.0011493186550394787, 0.002362496374114048], 'n_changes': 9}
- all_fail_frac: {'mean': 0.12498980989231508, 'ci95': [0.01649282791247234, 0.4147642622807494], 'n_changes': 9}

## Test metrics including high-fail-rate mutants

- recall@5%: {'mean': 0.7811730457281925, 'ci95': [0.5170864002804754, 1.0], 'n_changes': 23}
- recall@10%: {'mean': 0.8373835444614288, 'ci95': [0.6220057320779953, 1.0], 'n_changes': 23}
- recall@20%: {'mean': 0.8908571173943989, 'ci95': [0.7489520219788884, 1.0], 'n_changes': 23}
- apfd: {'mean': 0.9410312548873958, 'ci95': [0.8716855351937288, 0.9951327938941459], 'n_changes': 23}
- first_fail_frac: {'mean': 0.00556442958518649, 'ci95': [0.0012841468444168291, 0.011464340425058365], 'n_changes': 23}
- all_fail_frac: {'mean': 0.12356929091919008, 'ci95': [0.008474993836170232, 0.2757354130873204], 'n_changes': 23}

## Test metrics excluding high-fail-rate mutants

- recall@5%: {'mean': 0.8140917284741807, 'ci95': [0.5200976700459533, 1.0], 'n_changes': 22}
- recall@10%: {'mean': 0.8702680669081797, 'ci95': [0.6277257572147762, 1.0], 'n_changes': 22}
- recall@20%: {'mean': 0.9209363534554793, 'ci95': [0.7731217099157229, 1.0], 'n_changes': 22}
- apfd: {'mean': 0.9581576635243315, 'ci95': [0.8937942649407741, 0.9958640459032371], 'n_changes': 22}
- first_fail_frac: {'mean': 0.005767298130608462, 'ci95': [0.0014606884057213416, 0.012481483910931868], 'n_changes': 22}
- all_fail_frac: {'mean': 0.08417492062542596, 'ci95': [0.007495552643052442, 0.21595345816453426], 'n_changes': 22}

## Baselines (recall@10%)

- best for paired gate: `cov_lines`

### Val

- `random`: recall@10%=0.0996 ci95=[0.09945296738114905, 0.09968662690014564] n=9
- `t_fail_rate`: recall@10%=0.3732 ci95=[0.16702885907910042, 0.6043872073188421] n=9
- `cc_max`: recall@10%=0.2336 ci95=[0.05766326525876342, 0.4812054083720001] n=9
- `imp_dist`: recall@10%=0.3326 ci95=[0.14575716304484557, 0.5635310412279879] n=9
- `cov_lines`: recall@10%=0.8974 ci95=[0.6947388878660086, 1.0] n=9
- `fail_per_day`: recall@10%=0.3732 ci95=[0.16702885907910042, 0.6043872073188421] n=9

### Test

- `random`: recall@10%=0.0994 ci95=[0.09931731613421509, 0.09955563511179316] n=22
- `t_fail_rate`: recall@10%=0.0659 ci95=[0.025832859544731468, 0.1145199707444839] n=22
- `cc_max`: recall@10%=0.6667 ci95=[0.5265911399327334, 0.7996198600881125] n=22
- `imp_dist`: recall@10%=0.4955 ci95=[0.34140189035353874, 0.6501513113724563] n=22
- `cov_lines`: recall@10%=0.8696 ci95=[0.7468660235116906, 0.9735729386892178] n=22
- `fail_per_day`: recall@10%=0.0659 ci95=[0.025832859544731468, 0.1145199707444839] n=22

### Val synthetic

- `random`: recall@10%=0.0996 ci95=[0.09945296738114903, 0.09967862057282934] n=9
- `t_fail_rate`: recall@10%=0.3732 ci95=[0.13812283476028067, 0.6198214882525792] n=9
- `cc_max`: recall@10%=0.2336 ci95=[0.04325885392916857, 0.4603138571837987] n=9
- `imp_dist`: recall@10%=0.3326 ci95=[0.161068687630571, 0.5490060689059345] n=9
- `cov_lines`: recall@10%=0.8974 ci95=[0.6972293093219472, 1.0] n=9
- `fail_per_day`: recall@10%=0.3732 ci95=[0.13812283476028067, 0.6198214882525792] n=9

### Test synthetic

- `random`: recall@10%=0.0994 ci95=[0.09931669113756236, 0.09954671195251459] n=22
- `t_fail_rate`: recall@10%=0.0659 ci95=[0.027504906746960427, 0.10973200410515782] n=22
- `cc_max`: recall@10%=0.6667 ci95=[0.5310170883937778, 0.7941251326016155] n=22
- `imp_dist`: recall@10%=0.4955 ci95=[0.3411129724767353, 0.6403463298992919] n=22
- `cov_lines`: recall@10%=0.8696 ci95=[0.7547669534535965, 0.9735729386892178] n=22
- `fail_per_day`: recall@10%=0.0659 ci95=[0.027504906746960427, 0.10973200410515782] n=22


## LORO (macro recall@10% new_fail on held-out test)

- macro: None

## Paired delta (model − best baseline)

- val: {'mean': 0.003226061828014994, 'ci95': [-8.910273545396456e-05, 0.013060406641000282], 'n': 9}
- test: {'mean': 0.0006784195481182157, 'ci95': [0.0, 0.0018656537573250934], 'n': 22}
- synthetic val vs cov_lines (report-only): {'mean': 0.003226061828014994, 'ci95': [-8.910273545396456e-05, 0.014606380961521436], 'n': 9}
- synthetic test vs cov_lines (gate): {'mean': 0.0006784195481182157, 'ci95': [0.0, 0.0018656537573250934], 'n': 22, 'n_C': 11, 'n_mutants': 22}

## Candidates

- {'objective': 'binary', 'num_boost_round': 500, 'early_stopping_rounds': 50, 'stopping_metric': 'auc', 'best_iteration': 3, 'val_recall@10%_new_fail': 0.8431283124221413}
- {'objective': 'lambdarank', 'num_boost_round': 500, 'early_stopping_rounds': 50, 'stopping_metric': 'ndcg', 'best_iteration': 6, 'val_recall@10%_new_fail': 0.9006209316445687}

## Feature gain (top)

- cov_lines: 2807.9636895656586
- cov_mod: 528.1078559160233
- cov_func: 429.7252731323242
- cov_file: 326.0385093688965
- t_obs: 197.41904002428055
- t_age_days: 167.53133976459503
- cc_max: 149.54973542690277
- cov_breadth: 109.26496505737305
- pq_max: 91.8265790939331
- name_file: 51.04244947433472
- tfile_ntests: 48.87105941772461
- ch_rm: 31.979249238967896
- imp_dist: 28.919149935245514
- t_fail_rate: 23.400140285491943
- ch_nsrc: 16.82309913635254
- dur: 12.87962019443512
- ch_nfunc: 7.065390110015869
- cc_sum: 3.0640299320220947
- imp_direct: 2.659130096435547
- name_jac: 2.4102299213409424

## Feature value stats (synthetic)

| feature | n_unique | n_rows | sentinel | sentinel_share |
|---|---:|---:|---:|---:|
| `cov_lag` | 1 | 141037 | None | 0.0000 |
| `t_changed` | 1 | 141037 | None | 0.0000 |
| `t_flaky_rate` | 1 | 141037 | None | 0.0000 |
| `ch_nfiles` | 1 | 141037 | None | 0.0000 |
| `ch_add` | 1 | 141037 | None | 0.0000 |
| `imp_direct` | 2 | 141037 | None | 0.0000 |
| `name_file` | 2 | 141037 | None | 0.0000 |
| `name_func` | 2 | 141037 | None | 0.0000 |
| `cov_known` | 2 | 141037 | None | 0.0000 |
| `cov_file` | 2 | 141037 | None | 0.0000 |
| `cov_func` | 2 | 141037 | None | 0.0000 |
| `cov_mod` | 2 | 141037 | None | 0.0000 |
| `ch_nsrc` | 2 | 141037 | None | 0.0000 |
| `name_jac` | 4 | 141037 | None | 0.0000 |
| `ch_nfunc` | 4 | 141037 | None | 0.0000 |
| `imp_dist` | 6 | 141037 | 99 | 0.1105 |
| `cov_lines` | 6 | 141037 | None | 0.0000 |
| `ch_rm` | 6 | 141037 | None | 0.0000 |
| `dur` | 33 | 141037 | -1 | 0.0000 |
| `pq_max` | 37 | 141037 | -1.0 | 0.1708 |
| `tfile_ntests` | 51 | 141037 | None | 0.0000 |
| `t_fail_cnt` | 59 | 141037 | None | 0.0000 |
| `pf_max` | 76 | 141037 | -1.0 | 0.0113 |
| `t_fail_rate` | 183 | 141037 | None | 0.0000 |
| `t_obs` | 197 | 141037 | None | 0.0000 |
| `cc_max` | 706 | 141037 | None | 0.0000 |
| `cc_sum` | 706 | 141037 | None | 0.0000 |
| `cov_breadth` | 867 | 141037 | None | 0.0000 |
| `t_age_days` | 3023 | 141037 | None | 0.0000 |
