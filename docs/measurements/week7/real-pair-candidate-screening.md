# Week 7 real-pair candidate screening

## Scope and result

Screened the complete locally available Git history (`git log --all`) for click, Jinja, and attrs. No network fetches or pair tests were run during screening. A match is a lead only; it does not establish a real bug/fix pair or a positive label. Merge commits use first-parent diffs, while internal commits are scanned. Quoted `Revert "…"` subjects are excluded. Equivalent patches are grouped by stable patch-id, retaining all commit SHAs for audit. Output order is newest-first within each repository.

| Repo | Local commits | Leads before dedupe | Quoted Revert subjects excluded | Duplicate patch groups collapsed | Leads after dedupe | Source and test changed | Added test-code heuristic | Test diff unavailable |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| click | 3,547 | 1,387 | 8 | 204 | 1,183 | 452 | 405 | 0 |
| jinja | 2,949 | 1,099 | 7 | 131 | 968 | 334 | 304 | 0 |
| attrs | 1,864 | 598 | 3 | 5 | 593 | 210 | 145 | 20 |

The test-code heuristic looks for added `assert`, `raises`, or `test_*` lines in changed test paths. It includes old package layouts such as `click/` and `jinja2/testsuite/`. It is only a shortlist signal; each remaining candidate still needs manual confirmation that the commit fixes a real regression and that its added test exercises the fixed behavior. Missing Git objects are recorded as incomplete, never silently treated as “no test change.”

Legacy-layout coverage requested in review: click history before 2020-03-06 contributes 347 commits in `click/`, 105 with test changes; Jinja history before 2020-01-09 contributes 443 commits in `jinja2/`, 193 with test changes, including `jinja2/testsuite/`. The updated package-root and test-path rules include these layouts; Python 3.13 install failures are classified only during pair runs.

The accepted click pilot (`c53d373`) is within the newest-first top 15 and was rerun as that lead; the calibration count includes that single run and does not double-count the earlier pilot artifact.

## Calibration batch

The bounded run records one row per lead in `.cache/measurements/week7/calibration-v2/calibration-pairs.jsonl` and summary JSON in `.cache/measurements/week7/calibration-v2/calibration-summary.json`. A pair is `started` once pytest begins. A positive here means a stable shared test fails in both head runs and passes in both base runs; it is an outcome label, not a completed human audit that the commit message describes a real bug. Pairs that lack Git objects, have an unclean source revert, or fail Python 3.13 setup are reported separately; these are not negative labels. “Outside new test” maps changed test line ranges back to the containing test function, including assertions added inside existing functions.

Calibration summary (first 15 source+test leads per repository after deduplication; round-robin execution, newest-first inside each repository; Python 3.13.18; 300 s pair cap; 1,800 s batch cap):

| Repo | Leads after dedupe | Source+test leads | Attempted | Pytest started | env_incompatible | revert_conflict | Git objects missing | Timeout | Positive | Positive with outside-test failure | Median pair (s) | p90 (s) | Forecast remaining at median |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| click | 1,183 | 452 | 15 | 15 | 0 | 0 | 0 | 0 | 13 | 0/13 (0%) | 23.436 | 26.304 | 10,241.5 s (170.7 min) |
| jinja | 968 | 334 | 15 | 15 | 0 | 0 | 0 | 0 | 12 | 0/12 (0%) | 9.832 | 10.473 | 3,136.4 s (52.3 min) |
| attrs | 593 | 210 | 15 | 0 | 0 | 0 | 15 | 0 | 0 | not applicable | not applicable | not applicable | not estimable: no pair reached pytest |

The click and Jinja “outside-test” counters are both 0 among the outcome-positive pairs. The earlier click pilot `c53d373` reproduced 9 shared positives across 2,136 shared test IDs and remains 0 outside its two changed test functions. This result is consistent with regression tests driving the labels; it is not a model-vs-`cov_lines` evaluation. Attrs’ 15 candidates could not be paired because their Git diffs/commit trees are missing from the local cache; they are not zero-positive observations.

The run used 507.33 s (8 min 27 s). No pair hit the 5-minute timeout. An initial runner invocation was discarded before tests because it used a relative worktree path; no tests ran in that invocation. The corrected run above is the only calibration batch summarized here.

Commands (run from the TestPilot checkout; `W6_REPOS` points to the local Week 6 clones):

```sh
python3 scripts/collect_real_bugfix_pairs.py \
  --repo "click=$W6_REPOS/click" \
  --repo "jinja=$W6_REPOS/jinja" \
  --repo "attrs=$W6_REPOS/attrs" \
  --out-dir .cache/measurements/week7/candidates-new

python3 scripts/run_real_pair_calibration.py \
  --repo "click=$W6_REPOS/click" \
  --repo "jinja=$W6_REPOS/jinja" \
  --repo "attrs=$W6_REPOS/attrs" \
  --candidate-dir .cache/measurements/week7/candidates-new \
  --out-dir .cache/measurements/week7/calibration-v2 \
  --python python3.13 --per-repo-limit 15 \
  --pair-timeout-s 300 --batch-timeout-s 1800
```

## Runtime planning

The click pilot took about 25 s end-to-end for one pair. The per-repository calibration medians and p90 values below replace the old cross-repository linear estimate.

Forecast for remaining source+test leads uses the observed per-repository median, and is only a runtime estimate; screened leads are not assumed to be positives.

## Local CI artifact check

Checked filenames under the local Week 6 measurement cache for CI/workflow/action logs. No matching local CI run logs were found; the scan therefore used the offline commit-history fallback. This was a filename-based local check only; no remote CI history was queried.

## Reproduction

Command (run from the TestPilot checkout):

```sh
W6_REPOS='<path-to-week6-repositories>'
python3 scripts/collect_real_bugfix_pairs.py \
  --repo "click=$W6_REPOS/click" \
  --repo "jinja=$W6_REPOS/jinja" \
  --repo "attrs=$W6_REPOS/attrs" \
  --out-dir .cache/measurements/week7/candidates-new \
  > .cache/measurements/week7/candidate-scan.stdout.txt \
  2> .cache/measurements/week7/candidate-scan.stderr.txt
```

Stdout:

```text
click: 3547 local commits; 1387 leads before patch-id dedupe; 1183 after; 8 Revert subjects excluded
jinja: 2949 local commits; 1099 leads before patch-id dedupe; 968 after; 7 Revert subjects excluded
attrs: 1864 local commits; 598 leads before patch-id dedupe; 593 after; 3 Revert subjects excluded
wrote .cache/measurements/week7/candidates-new/candidate-scan-summary.json
```

Stderr was empty. Candidate records and exact local history pins are in the ignored local artifact `.cache/measurements/week7/candidates-new/`; this report intentionally does not treat heuristic matches as confirmed pairs.
