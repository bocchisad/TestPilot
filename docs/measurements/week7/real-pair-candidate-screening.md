# Week 7 real-pair candidate screening

## Scope and result

Screened the complete locally available Git history (`git log --all`) for click, Jinja, and attrs. No network fetches or test runs were performed. The repositories were not shallow. A match is a screening lead only; it does not establish a real bug/fix pair or a positive label.

| Repo | Local commits | Message-pattern matches | CHANGES/CHANGELOG/NEWS matches | Union candidates | Source and test changed | Added test-code heuristic | Test diff unavailable |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| click | 3,547 | 760 | 813 | 1,388 | 247 | 217 | 0 |
| jinja | 2,949 | 694 | 523 | 1,100 | 81 | 66 | 0 |
| attrs | 1,864 | 474 | 164 | 600 | 183 | 138 | 20 |

The test-code heuristic looks for added `assert`, `raises`, or `test_*` lines in a changed Python test file. It is only a shortlist signal; each remaining candidate still needs manual confirmation that the commit fixes a real regression and that its added test exercises the fixed behavior. The 20 attrs test diffs are unknown because required Git objects are missing from the local cache; they were not counted as test additions.

The pilot is one positive click pair, but it is not yet eligible for the final data inventory until it passes the full screening/provenance audit. Its outside-regression-test count is 0/1.

## Runtime planning

The click pilot took about 25 s end-to-end for one pair, including cached setup and four suite passes. If every candidate with added test-code heuristic were run, straight-line scaling gives about 10,525 s (175 min, 2 h 55 min) across 421 candidates. This is a coarse planning proxy across three repos, not a guaranteed upper bound; actual runtimes and dependency setup can differ.

The theoretical floor to reach 10 positives per repo is 29 additional pairs (9 click after the pilot, 10 Jinja, 10 attrs), or about 12 min at the click pilot rate, assuming every selected candidate is positive and ignoring any cross-repo timing difference. The real count and duration are unknown until candidates are confirmed and run. Any batch projected above 30 minutes remains unstarted pending explicit user OK.

## Local CI artifact check

Checked filenames under the local Week 6 measurement cache for CI/workflow/action logs. No matching local CI run logs were found; the scan therefore used the offline commit-history fallback. This was a filename-based local check only; no remote CI history was queried.

## Reproduction

Command (run from the TestPilot checkout):

```sh
W6_REPOS='/Users/gothscum/Documents/проекты/TestPilot/.cache/measurements/w6.fDq7oz/repos'
python3 scripts/collect_real_bugfix_pairs.py \
  --repo "click=$W6_REPOS/click" \
  --repo "jinja=$W6_REPOS/jinja" \
  --repo "attrs=$W6_REPOS/attrs" \
  --out-dir .cache/measurements/week7/candidates \
  > .cache/measurements/week7/candidate-scan.stdout.txt \
  2> .cache/measurements/week7/candidate-scan.stderr.txt
```

Stdout:

```text
click: 3547 local commits; 1388 candidates; 247 changed both source and tests
jinja: 2949 local commits; 1100 candidates; 81 changed both source and tests
attrs: 1864 local commits; 600 candidates; 183 changed both source and tests
wrote .cache/measurements/week7/candidates/candidate-scan-summary.json
```

Stderr was empty. Candidate records and exact local history pins are in the ignored local artifact `.cache/measurements/week7/candidates/`; this report intentionally does not treat heuristic matches as confirmed pairs.
