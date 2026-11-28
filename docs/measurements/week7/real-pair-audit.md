# Real pair function-level audit

This audit uses the full-history PR-intermediate run in `.cache/measurements/week7/pr-intermediate/full-history-run/pairs.jsonl`. It maps changed test lines in each positive head to enclosing `test_*` functions and removes only positive IDs whose function changed in that head. A failing test ID with parametrization is compared by its function name before `[parameter]`.

## Result

- Raw stable positive failure sets from the full-history run: 28 (click 21, Jinja 2, attrs 5).
- Function-level audit removed 2 click changes whose positives were entirely inside changed test functions; 26 remained before semantic PR-context review.
- Semantic audit reviewed all 26 retained changes against first-parent/source diffs, commit history, and PR metadata. It excluded click PR group #3872/#3873 as non-substantive spam (84 failed test IDs) and withheld Jinja #2256 as uncertain (`[rejected AI]`, closed/unmerged).
- Primary confirmed-candidate manifest: 24 changes (click 18, Jinja 1, attrs 5). The uncertain Jinja row is preserved separately, not counted in primary metrics.
- Positive changes with `test_modified_in_head=true` at file level: 2/24; both remain valid because the failing functions themselves were not modified.
- Positive sets removed because every positive function was modified: 2 (click 2, Jinja 0, attrs 0).

## Unique failure-set decisions

| Repo | PR | Head | Decision | Removed changed-function positives | Retained positives |
| --- | --- | --- | --- | ---: | ---: |
| attrs | 1488 | `165d4051effa` | retained | 0 | 1 |
| attrs | 1511/1512 | `716d219cfe69` | retained | 0 | 9 |
| attrs | 1516 | `8bfb9f1f3aaa` | retained | 0 | 2 |
| attrs | 1533 | `605cb9b38362` | retained | 0 | 1 |
| attrs | 1554 | `11bb503cfacd` | retained | 0 | 1 |
| click | 1222 | `804cf9130268` | retained | 0 | 30 |
| click | 1280 | `8867e0be2bde` | retained | 0 | 11 |
| click | 1364 | `ae7e0e45d398` | retained | 0 | 1 |
| click | 1368 | `5062b6543e8a` | retained | 0 | 2 |
| click | 1392 | `f759138b68f8` | retained | 0 | 1 |
| click | 1489 | `e08af2564382` | retained | 0 | 2 |
| click | 1504 | `009c2b0d3c01` | retained | 0 | 13 |
| click | 2622 | `401ae6545131` | retained | 0 | 2 |
| click | 2767 | `e65efc32a0ba` | retained | 0 | 4 |
| click | 2796 | `78a8d86f84ab` | retained | 0 | 4 |
| click | 327 | `e637840751cc` | excluded: no positives after function audit | 1 | 0 |
| click | 352 | `023548942686` | retained | 0 | 1 |
| click | 3833 | `349162a0a820` | retained | 0 | 3 |
| click | 3872/3873 | `e05912a7c976` | excluded: spam, closed/unmerged | 0 | 84 |
| click | 422 | `b35fb3488a0b` | retained | 0 | 39 |
| click | 446 | `f674412d97c5` | retained | 1 | 1 |
| click | 51 | `41c396f63c34` | retained | 0 | 12 |
| click | 765 | `9b1e50c063e5` | retained | 0 | 6 |
| click | 767 | `79f29dcc4c2f` | excluded: no positives after function audit | 1 | 0 |
| click | 849 | `60eac2e4a2f5` | retained | 0 | 6 |
| click | 868 | `2b4ae3111f15` | retained | 0 | 125 |
| jinja | 2242 | `e80befd76ecc` | retained | 0 | 6 |
| jinja | 2256 | `a8db1e620a8c` | uncertain: rejected AI PR; withheld from primary | 0 | 4 |

The 26 here is the pre-semantic candidate count. After semantic review, 24 primary candidates are in `real-bug-pairs.jsonl` (click 18, attrs 5, Jinja 1); click #3872/#3873 spam and uncertain Jinja #2256 are in separate manifests. Exact IDs, changed functions, PR groups, and duplicate source heads are recorded in those manifests. The two function-only exclusions remain auditable in the raw `pairs.jsonl` plus head diffs. Click PR #767 loses `test_nargs_tup_composite`; click PR #327 loses `test_multiple_default_help`; both sets have no remaining positives. Click PR #446 loses changed `test_auto_shorthelp` but retains unchanged `test_basic_group`. Click PR #422 has a changed test file, but its changed function does not overlap its positive IDs, so its positives remain.

This is a function-level exclusion audit, not a new test run. The four-run stable outcomes are inherited from the full-history run; function mapping is checked against each head diff and AST.


## Semantic PR-context review

Independent review covered 26/26 function-audited rows using first-parent diffs, base-to-head diffs, commit messages, and PR metadata where available. The reviewer found 24 semantically plausible candidates, one uncertain source (Jinja #2256), and one clear exclusion (click #3872/#3873). The 24 primary rows are in `real-bug-pairs.jsonl`; exclusions and unresolved policy are retained in `real-bug-pairs-excluded.jsonl` and `real-bug-pairs-uncertain.jsonl`.

The excluded click stack consists of commits titled `spam`, with both PRs closed and unmerged. Its 84 failures came from the `IntParamType` change and do not qualify as substantive PR evidence. Jinja #2256 changes LRUCache behavior and the failures are related, but the PR is `[rejected AI]`, closed, and unmerged; it is withheld from primary metrics pending the inclusion policy.

Two pairs remain correlated even though the registered PR-group rule treats them as distinct changes: attrs #1533/#1554 share issue #1245 and the same failing test; click #765/#849 share six failing tests and return-value behavior. This means 24 is the preregistered unique-change count, not 24 independent bug mechanisms. Other valid candidates include unfinished or WIP intermediate commits; they represent real observed PR intermediate states, not merged releases.
