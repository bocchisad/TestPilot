# TestPilot training report

- status: **data_insufficient**
- objective: `lambdarank`
- useful_on_val (useful_on_real_val): True
- useful_on_synthetic_val (useful_on_synthetic_val): True
- useful_on_synthetic_test (useful_on_synthetic_test): False
- ranker: `cov_lines`
- bootstrap.verdict: **use_baseline** (insufficient_test_C)
- features_version: 2
- schema_sha256: `9e5e9243326d8870bd33dcf8c59b49d34a76d60e921435f501638db23e2c5269`
- note: synthetic data present: test needs ≥20 mutant changes with ≥1 new_fail and ≥10 distinct base_change; metrics shown but no conclusions
- note: bootstrap verdict=use_baseline ranker=cov_lines reason=insufficient_test_C (accept on synth TEST cluster CI only; val is report-only)

## Diagnostics

- n_changes: 330
- fail_per_change_mean: 15.327272727272728
- new_fail_per_change_mean: 12.575757575757576
- n_always_broken_tests: 0
- n_label1: 5058
- n_persistent_among_label1: 904
- persistent_share_among_label1: 0.17872676947410043
- n_changes_with_new_fail: 111
- n_rows_parent_missing: 506
- n_persistent: 907
- n_flaky: 76
- n_orphan_vs_parent: 0
- n_high_fail_rate_changes: 2
- exclude_high_fail_rate: True
- fail_per_change_mean_including_high_fail: 17.16867469879518
- new_fail_per_change_mean_including_high_fail: 14.424698795180722
- mutant_test_new_fail_changes: 10
- mutant_test_distinct_base_change: 5

### Failures per change after HF filter (`label=1`, flaky=0)

- min/p50/p90/max: 0/4.0/25.200000000000045/279

- [0,1): 51
- [1,2): 1
- [2,5): 188
- [5,10): 36
- [10,20): 15
- [20,50): 14
- [50,100): 14
- 100+: 11

### new_fail per change after HF filter

- min/p50/p90/max: 0/0.0/22.200000000000045/275

- [0,1): 219
- [1,2): 18
- [2,5): 27
- [5,10): 13
- [10,20): 18
- [20,50): 12
- [50,100): 12
- 100+: 11

### Failures per change including high-fail-rate mutants

- min/p50/p90/max: 0/4.0/27.900000000000034/338

- [0,1): 51
- [1,2): 1
- [2,5): 188
- [5,10): 36
- [10,20): 15
- [20,50): 14
- [50,100): 14
- 100+: 13

### new_fail per change including high-fail-rate mutants

- min/p50/p90/max: 0/0.0/24.900000000000034/338

- [0,1): 219
- [1,2): 18
- [2,5): 27
- [5,10): 13
- [10,20): 18
- [20,50): 12
- [50,100): 12
- 100+: 13

### Always-broken tests (≥90% fails, n=0)


## Val metrics (new_fail)

- recall@5%: {'mean': 0.9066193853427895, 'ci95': [0.7198581560283688, 1.0], 'n_changes': 9}
- recall@10%: {'mean': 0.9249408983451536, 'ci95': [0.7748226950354611, 1.0], 'n_changes': 9}
- recall@20%: {'mean': 0.9580378250591016, 'ci95': [0.874113475177305, 1.0], 'n_changes': 9}
- apfd: {'mean': 0.9766739392182602, 'ci95': [0.9413572832394341, 0.9979631697916056], 'n_changes': 9}
- first_fail_frac: {'mean': 0.0016342937654251076, 'ci95': [0.0016307338525361253, 0.001638002736712189], 'n_changes': 9}
- all_fail_frac: {'mean': 0.047895274693028164, 'ci95': [0.0040736604167888385, 0.12047111911967412], 'n_changes': 9}

## Val metrics including high-fail-rate mutants

- recall@5%: {'mean': 0.9066193853427895, 'ci95': [0.747872340425532, 1.0], 'n_changes': 9}
- recall@10%: {'mean': 0.9249408983451536, 'ci95': [0.7973404255319149, 1.0], 'n_changes': 9}
- recall@20%: {'mean': 0.9580378250591016, 'ci95': [0.8867021276595745, 1.0], 'n_changes': 9}
- apfd: {'mean': 0.9766739392182602, 'ci95': [0.9427348910766205, 0.9979591648789128], 'n_changes': 9}
- first_fail_frac: {'mean': 0.0016342937654251076, 'ci95': [0.0016307338525361251, 0.001638002736712189], 'n_changes': 9}
- all_fail_frac: {'mean': 0.047895274693028164, 'ci95': [0.004081670242174646, 0.11772359915950906], 'n_changes': 9}

## Val metrics excluding high-fail-rate mutants

- recall@5%: {'mean': 0.9066193853427895, 'ci95': [0.7198581560283688, 1.0], 'n_changes': 9}
- recall@10%: {'mean': 0.9249408983451536, 'ci95': [0.7748226950354611, 1.0], 'n_changes': 9}
- recall@20%: {'mean': 0.9580378250591016, 'ci95': [0.874113475177305, 1.0], 'n_changes': 9}
- apfd: {'mean': 0.9766739392182602, 'ci95': [0.9413572832394341, 0.9979631697916056], 'n_changes': 9}
- first_fail_frac: {'mean': 0.0016342937654251076, 'ci95': [0.0016307338525361253, 0.001638002736712189], 'n_changes': 9}
- all_fail_frac: {'mean': 0.047895274693028164, 'ci95': [0.0040736604167888385, 0.12047111911967412], 'n_changes': 9}

## Test metrics including high-fail-rate mutants

- recall@5%: {'mean': 0.3999845555583127, 'ci95': [0.16091894271544796, 0.7958333333333334], 'n_changes': 11}
- recall@10%: {'mean': 0.46085198819394774, 'ci95': [0.23666558595498013, 0.8078461290362597], 'n_changes': 11}
- recall@20%: {'mean': 0.6391682974384927, 'ci95': [0.443880711294593, 0.9], 'n_changes': 11}
- apfd: {'mean': 0.8350377646109731, 'ci95': [0.7519226745695541, 0.9420716679311261], 'n_changes': 11}
- first_fail_frac: {'mean': 0.004125920423687961, 'ci95': [0.0015829009580499214, 0.00650973982983199], 'n_changes': 11}
- all_fail_frac: {'mean': 0.3598294981150308, 'ci95': [0.14180834723656685, 0.5195081718419289], 'n_changes': 11}

## Test metrics excluding high-fail-rate mutants

- recall@5%: {'mean': 0.4325865614100019, 'ci95': [0.17830983185025356, 0.8700808625336928], 'n_changes': 10}
- recall@10%: {'mean': 0.49421529352221816, 'ci95': [0.2585549161637276, 0.8776280323450134], 'n_changes': 10}
- recall@20%: {'mean': 0.6737951863539395, 'ci95': [0.5146586439232326, 0.9125], 'n_changes': 10}
- apfd: {'mean': 0.8512815522510693, 'ci95': [0.7797711999893131, 0.953858560034694], 'n_changes': 10}
- first_fail_frac: {'mean': 0.003724180218499755, 'ci95': [0.0015829009580499212, 0.005452715764942667], 'n_changes': 10}
- all_fail_frac: {'mean': 0.3321316661675762, 'ci95': [0.09469445043449967, 0.49633934431739685], 'n_changes': 10}

## Baselines (recall@10%)

- best for paired gate: `cov_lines`

### Val

- `random`: recall@10%=0.0997 ci95=[0.09952914180392668, 0.09983668986827503] n=9
- `t_fail_rate`: recall@10%=0.1937 ci95=[0.0921731470842734, 0.3953579316527535] n=9
- `cc_max`: recall@10%=0.2258 ci95=[0.07497541789577188, 0.40747910521140607] n=9
- `imp_dist`: recall@10%=0.0686 ci95=[0.013447971781305114, 0.14853328478952052] n=9
- `cov_lines`: recall@10%=0.8221 ci95=[0.5878263188470452, 1.0] n=9
- `fail_per_day`: recall@10%=0.1937 ci95=[0.0921731470842734, 0.3953579316527535] n=9

### Test

- `random`: recall@10%=0.0994 ci95=[0.0992354545695294, 0.09960965016816292] n=10
- `t_fail_rate`: recall@10%=0.0921 ci95=[0.09184850197851893, 0.09242834168123715] n=10
- `cc_max`: recall@10%=0.1632 ci95=[0.0431047588945166, 0.35802990982510635] n=10
- `imp_dist`: recall@10%=0.3803 ci95=[0.11990019141738402, 0.6465589729103951] n=10
- `cov_lines`: recall@10%=0.4279 ci95=[0.1847599291475139, 0.6745988612836438] n=10
- `fail_per_day`: recall@10%=0.0921 ci95=[0.09184850197851893, 0.09242834168123715] n=10

### Val synthetic

- `random`: recall@10%=0.0997 ci95=[0.09949074279455664, 0.09985699737785117] n=8
- `t_fail_rate`: recall@10%=0.0929 ci95=[0.09210582531194877, 0.09420117244481094] n=8
- `cc_max`: recall@10%=0.2541 ci95=[0.08434734513274336, 0.45703125] n=8
- `imp_dist`: recall@10%=0.0771 ci95=[0.015128968253968252, 0.16709994538821057] n=8
- `cov_lines`: recall@10%=0.9124 ci95=[0.7370853422073158, 1.0] n=8
- `fail_per_day`: recall@10%=0.0929 ci95=[0.09210582531194877, 0.09420117244481094] n=8

### Test synthetic

- `random`: recall@10%=0.0994 ci95=[0.0992354545695294, 0.09960965016816291] n=10
- `t_fail_rate`: recall@10%=0.0921 ci95=[0.09184850197851893, 0.09242834168123715] n=10
- `cc_max`: recall@10%=0.1632 ci95=[0.03836562916049669, 0.36891920242556] n=10
- `imp_dist`: recall@10%=0.3803 ci95=[0.12085387412479624, 0.6458656527959319] n=10
- `cov_lines`: recall@10%=0.4279 ci95=[0.18673489211550942, 0.6739249057507495] n=10
- `fail_per_day`: recall@10%=0.0921 ci95=[0.09184850197851893, 0.09242834168123715] n=10


## LORO (macro recall@10% new_fail on held-out test)

- macro: None

## Paired delta (model − best baseline)

- val: {'mean': 0.10285972281570228, 'ci95': [0.0, 0.38943783394050663], 'n': 9}
- test: {'mean': 0.06629340813651102, 'ci95': [-0.004100466215307992, 0.11941306204890384], 'n': 10}
- synthetic val vs cov_lines (report-only): {'mean': 0.003196729902525945, 'ci95': [0.0, 0.009590189707577836], 'n': 8}
- synthetic test vs cov_lines (gate): {'mean': 0.06629340813651102, 'ci95': [-0.00476402713811717, 0.1198643351884514], 'n': 10, 'n_C': 5, 'n_mutants': 10}

## Candidates

- {'objective': 'binary', 'num_boost_round': 500, 'early_stopping_rounds': 50, 'stopping_metric': 'auc', 'best_iteration': 65, 'val_recall@10%_new_fail': 0.9199301058690514}
- {'objective': 'lambdarank', 'num_boost_round': 500, 'early_stopping_rounds': 50, 'stopping_metric': 'ndcg', 'best_iteration': 111, 'val_recall@10%_new_fail': 0.9243498817966902}

## Feature gain (top)

- cov_lines: 9523.356109976768
- cov_breadth: 1179.4739721566439
- cc_max: 784.5168320983648
- t_age_days: 768.0649094581604
- t_obs: 666.1738957613707
- cov_mod: 630.3358818292618
- imp_dist: 574.2999207377434
- tfile_ntests: 506.2389463633299
- dur: 404.32909578084946
- cov_func: 402.91217398643494
- cov_file: 340.15060782432556
- ch_nfiles: 180.8319668918848
- cc_sum: 162.20510682463646
- pf_max: 98.47687196731567
- name_file: 53.90916082262993
- pq_max: 45.599597096443176
- ch_nsrc: 42.61596918106079
- name_jac: 37.499359995126724
- ch_nfunc: 32.40878850221634
- imp_direct: 22.584036499261856

## Feature value stats (synthetic)

| feature | n_unique | n_rows | sentinel | sentinel_share |
|---|---:|---:|---:|---:|
| `cov_lag` | 1 | 76992 | None | 0.0000 |
| `t_changed` | 1 | 76992 | None | 0.0000 |
| `t_flaky_rate` | 1 | 76992 | None | 0.0000 |
| `imp_direct` | 2 | 76992 | None | 0.0000 |
| `name_file` | 2 | 76992 | None | 0.0000 |
| `name_func` | 2 | 76992 | None | 0.0000 |
| `cov_known` | 2 | 76992 | None | 0.0000 |
| `cov_file` | 2 | 76992 | None | 0.0000 |
| `cov_func` | 2 | 76992 | None | 0.0000 |
| `cov_mod` | 2 | 76992 | None | 0.0000 |
| `ch_nfiles` | 2 | 76992 | None | 0.0000 |
| `ch_nsrc` | 2 | 76992 | None | 0.0000 |
| `ch_add` | 2 | 76992 | None | 0.0000 |
| `ch_nfunc` | 3 | 76992 | None | 0.0000 |
| `name_jac` | 4 | 76992 | None | 0.0000 |
| `cov_lines` | 4 | 76992 | None | 0.0000 |
| `imp_dist` | 6 | 76992 | 99 | 0.0336 |
| `ch_rm` | 6 | 76992 | None | 0.0000 |
| `pq_max` | 17 | 76992 | -1.0 | 0.0573 |
| `dur` | 28 | 76992 | -1 | 0.0000 |
| `pf_max` | 43 | 76992 | -1.0 | 0.0304 |
| `t_fail_cnt` | 45 | 76992 | None | 0.0000 |
| `tfile_ntests` | 46 | 76992 | None | 0.0000 |
| `t_fail_rate` | 55 | 76992 | None | 0.0000 |
| `t_obs` | 183 | 76992 | None | 0.0000 |
| `cc_max` | 691 | 76992 | None | 0.0000 |
| `cc_sum` | 691 | 76992 | None | 0.0000 |
| `cov_breadth` | 773 | 76992 | None | 0.0000 |
| `t_age_days` | 2834 | 76992 | None | 0.0000 |

## Provenance

- Mutation classifier (`scripts/mutate/run_mutant.py`): commit `6c3fc2c4ffd7fc7599f2313a0dfcb86093b3dbac`, blob `55bacb570a74e4625eddab73ed155698c326db81`.
- Mutation ledger (`scripts/mutate_bootstrap.py`): commit `d7c73f1e1a8fdf207c8f044ba0f160dbb9288912`, blob `548fcc2e98285d7d6bd2dea29d8e4c4b144b4620`.
- Aggregator (`scripts/aggregate_bootstrap_w6.py`): commit `092d6cc30a09f409aea1b1413ab62976d60430fd`, blob `0a7c3bb9ad7ebde626c75ae2266abd30ae254302`.
