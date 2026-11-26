# Week 7 budget curve

Population: 24 primary real positive changes after excluding positives whose test function changed in the PR head. Average over positive changes; ties at the budget boundary use expected contribution under uniform tie selection. Durations are milliseconds from feature data.

| Selector | Recall @10 / @20 / @30 | Time saved @10 / @20 / @30 |
| --- | ---: | ---: |
| real-only model | 0.140 / 0.230 / 0.397 | 0.675 / 0.547 / 0.384 |
| cov_lines | 0.191 / 0.291 / 0.388 | 0.902 / 0.786 / 0.669 |
| Run all | 1.000 / 1.000 / 1.000 | 0 / 0 / 0 |

Duration coverage: 14,361 known, 0 unknown; all 24 changes have complete duration coverage. `cov_lines` has boundary ties in all 24 changes at 10/20/30%. Model ties occur in 22/24, 21/24, and 23/24 changes.

Recall is not interchangeable with time saved. LORO headline recall is equal-repository macro; this curve is change-weighted. Complete curves for all training arms/ablations and separate synthetic diagnostic remain in `budget-curve.json`. The decision criterion is not met; keep `cov_lines`.
