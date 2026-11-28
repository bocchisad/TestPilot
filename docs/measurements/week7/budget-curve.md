# Week 7 budget curve

Population: 24 primary real positive changes after excluding positives in test functions modified by the PR head and removing 680 rows from interrupted pytest heads. Average is over positive changes; ties at the budget boundary use expected contribution under uniform tie selection. Durations are milliseconds from feature data.

| Selector | Recall @10 / @20 / @30 | Time saved @10 / @20 / @30 |
| --- | ---: | ---: |
| Real-only model | 0.104 / 0.185 / 0.327 | 0.777 / 0.638 / 0.453 |
| cov_lines | 0.191 / 0.291 / 0.388 | 0.902 / 0.786 / 0.669 |
| Run all | 1.000 / 1.000 / 1.000 | 0 / 0 / 0 |

Duration coverage: 14,361 known, 0 unknown; all 24 changes have complete duration coverage. `cov_lines` has boundary ties in all 24 changes at 10/20/30%. Model ties occur in 22/24, 21/24, and 24/24 changes.

Recall is not interchangeable with time saved. LORO headline recall is equal-repository macro; this curve is change-weighted. Full curves for each training arm/ablation and the separate synthetic diagnostic are in `budget-curve.json`. The real-data decision criterion is not met; keep `cov_lines`.
