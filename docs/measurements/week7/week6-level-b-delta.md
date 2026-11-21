# Week 6 Level-B label recalculation

Scope: click Level-B; saved gold vs labels recalculated using shared test IDs.

Inputs: `docs/measurements/bootstrap-click/level_b_gold.json` and the original local W6 click clone supplied via `--repos-root` (`.testpilot/db.sqlite` and `bootstrap/mutate_outcomes.jsonl`).

| Measure | Saved labels | Shared-test labels | Delta |
| --- | ---: | ---: | ---: |
| Kill mutants | 111 | 111 | +0 |
| Changed mutant labels | — | 0 | — |
| Changed test labels | — | 0 | — |
| Pairs with failures missing from base | — | 4 | — |

Compared mutants: 132. SHA sets match: True.

## Per-mutant test-label changes

No per-test labels changed (0).

## Failures missing from base

| Mutant SHA | Test IDs |
| --- | --- |
| `3332b6d1271b37177fe7b3996de5df6e2c54dcc3` | `tests.test_info_dict` |
| `4d4d4a303ab74e8f315f7eaa71e781aa8aea3157` | `tests.test_info_dict` |
| `53f01e33ddfea66b5cb6f243c3528a06bb13004d` | `tests.test_info_dict`, `tests.test_options` |
| `c416c54bbfc39e8edda4b4d9c0a89eb5338d1fe7` | `tests.test_info_dict`, `tests.test_options` |
