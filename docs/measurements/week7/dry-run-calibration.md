# Week 6 dry-run calibration

In-sample reconstruction from the existing Week 6 click and jinja reports. The same reports supply the fitted step rates and the actual step totals, so this is not independent validation.

## Formula

`estimate_s = collect_git_s + B_hat × backfill_s_per_commit + attempted_hat × mutate_s_per_attempt + eligible_hat × features_dump_s_per_eligible + train_s`

- `attempted_hat = min(N, B_hat) × attempted_per_commit × K / 4`.
- `eligible_hat = attempted_hat × observed_eligible / observed_attempted`.
- `features_dump_hat = eligible_hat × observed_features_dump_s / observed_eligible`.
- `actual_s` is the sum of the five listed `step_timings_s` values. It excludes `init`, `gate`, `cleanup`, `finalize`, and uninstrumented wall overhead.
- The reconstruction uses `N=200`, `K=4`, and dry-run `B_hat=200`. Jinja observed 199 new backfill commits; the fitted per-commit rate is applied to the 200 predicted commits.

## Results

| Suite | Old estimate | New estimate | Actual five-step sum | Absolute error |
| --- | ---: | ---: | ---: | ---: |
| Click | 1103.36 s | 1608.31 s | 1718.03 s | 6.39% |
| Jinja | 1944.20 s | 2776.40 s | 2414.99 s | 14.97% |

Click: 1,608.31 s estimated vs 1,718.03 s actual (6.39% absolute error). Jinja: 2,776.40 s vs 2,414.99 s (14.97%). The target of at most 15% is met in-sample; jinja is close to the boundary.

## Calibration inputs and limits

- Step timings and counts come from `docs/measurements/bootstrap-click/bootstrap_report.json` and `docs/measurements/bootstrap-jinja/bootstrap_report.json`; existing estimates come from `docs/measurements/dry-run-click.txt`, `docs/measurements/dry-run-jinja.txt`, and `docs/measurements/dry-run-estimates.json`.
- Coefficients are stored by step and suite in `docs/measurements/bootstrap_time_priors.json`. The existing attempted-per-commit density is retained; per-suite observed eligibility ratios are used for the estimate.
- Attrs is excluded from fitting because its Week 6 run resumed. Its feature-dump and eligibility values are labeled conservative click/jinja proxies; they are not an attrs measurement.
- No measurement run was started for this calibration.

For full inputs and per-step arithmetic, see `dry-run-calibration.json`.
