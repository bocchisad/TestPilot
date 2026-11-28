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

**Accepted revert-fix result:** revert-fix pairs give no signal outside their regression tests: **0/25** positive pairs (click **0/13**, Jinja **0/12**). The 95% upper bound by the rule of three is approximately **12%** (3/25). This is a negative result about the revert-fix construction; it does not measure the PR-intermediate source and is not a model-vs-`cov_lines` evaluation. Attrs’ 15 candidates could not be paired because their Git diffs/commit trees are missing from the local cache; they are not zero-positive observations.

The run used 507.33 s (8 min 27 s). No pair hit the 5-minute timeout. An initial runner invocation was discarded before tests because it used a relative worktree path; no tests ran in that invocation. The corrected run above is the only calibration batch summarized here.

## PR-intermediate source and feasibility

The primary 2A design now uses PR-intermediate commits: `base=merge-base(PR, main)`, with each commit after base evaluated as a head. Only test IDs already present in base can be positive; they must pass in both base runs and fail in both head runs. Repeated outcomes are required, so a one-off failure cannot count as a positive. The feasibility batch was capped at 30 minutes including inventory, with a 5-minute maximum per pair. No remaining revert-fix leads were run.

### Feasibility result (2026-11-22)

Inventory against each clone's `refs/remotes/upstream/main` found **282 click, 162 Jinja, and 510 attrs PRs** with at least two commits. We sampled the 10 newest PRs per repo; their `merge-base..PR head` commit counts were 43, 184, and 37. Main SHAs were click `08b803c`, Jinja `5ef7011`, attrs `f830e60`. Sample details are in `.cache/measurements/week7/pr-intermediate/inventory.json`.

#### Recount using unique changes

I recalculated the earlier feasibility batch by unique `head` SHA and then by the exact set of stable failing, base-present test IDs within each connected PR group. PRs that share commits are one group. This removes the duplicate click appearances for PR #3872/#3873 (and #3917/#3919). The prior raw pair rows are preserved as `pairs-raw-before-dedup.jsonl`; `pairs.jsonl` now contains the SHA-deduplicated rows.

| Repo | Unique heads attempted | Completed | Positive heads | Unique positive failure sets | Positive PR groups | `test_modified_in_head` true / false |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| click | 20 | 20 | 4 | **2** | 2 | 0 / 4 |
| Jinja | 25 | 25 | 4 | **2** | 2 | 0 / 4 |
| attrs | 24 | 23 | 0 | **0** | 0 | n/a |

The unique click changes are (a) PR group #3872/#3873, whose three intermediate heads have the same failure set, and (b) PR #3833. Jinja has one unique failure set each in PR #2256 and #2242. Thus the feasibility count is **click 2, Jinja 2, attrs 0**, below the threshold of 10 unique positive changes per repo. Every positive head had `test_modified_in_head=false`: none of the failing tests' files was changed by that head commit. The per-pair flag and resolved test paths are recorded in the deduplicated JSONL.

The 30-minute batch finished at **1,799.87 s**, including **121.89 s** for PR inventory. There were 69 unique-head attempts, 68 completed pairs, 8 positive head outcomes, and one attrs `batch_deadline` before pytest; after failure-set deduplication these are four unique positive changes across click/Jinja. No pair was `env_incompatible`, `git_objects_missing`, or a 5-minute pair timeout. The attrs deadline row is not a negative outcome. Click deselected 31,000 stress cases via `addopts`. These are feasibility results, not manually audited real-bug labels or model-vs-`cov_lines` metrics.

#### CI prefilter inventory (2026-11-22; no pytest)

For the same newest 10 PRs per repo, the CI inventory queried `check-runs` once per unique head SHA. It used the latest completed run for each check name/app: any latest `failure`, `timed_out`, or `startup_failure` makes the commit CI-failure; all latest `success`, `neutral`, or `skipped` means success; otherwise the status is unavailable. If any commit status is unavailable within a PR, that PR falls back to its first commit only. Fully classifiable PRs contribute every CI-failure commit plus a seeded random 10% sample of success commits (seed `20261122`).

| Repo | PRs ≥2 commits overall | Sample PRs | Raw commit appearances | Unique heads | Shared-head duplicates removed | CI failure | CI success | CI unavailable | Fallback PRs | Heads selected | Forecast median / p90 pair time |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| click | 282 | 10 | 43 | 37 | 6 | 8 | 18 | 11 | 3 | 12 | 282.3 / 311.7 s |
| Jinja | 162 | 10 | 184 | 184 | 0 | 13 | 9 | 162 | 4 | 16 | 200.0 / 236.7 s |
| attrs | 510 | 10 | 37 | 37 | 0 | 13 | 15 | 9 | 6 | 16 | 449.3 / 516.2 s |

In the selected set, click has 8 CI-failure heads and 1 sampled success, Jinja 11 failures and 1 sampled success, attrs 9 failures and 1 sampled success; fallback first heads are deduplicated with those selections. Forecasts multiply selected heads by the deduplicated feasibility median/p90 (click 23.528/25.974 s, Jinja 12.501/14.791 s, attrs 28.081/32.265 s); they cover pair runs only. The sample's fallback PR numbers, selected heads, CI conclusions and seed are in `.cache/measurements/week7/pr-intermediate/ci-inventory.json`; raw API response cache is in `check-runs-cache.json`. **This CI inventory launched no pytest.** Attrs remains at the already measured 0/10 unique positive changes; no expanded attrs sample was run.

#### Full-history CI inventory (2026-11-22; no pytest)

Expanded the same inventory to **all 954 PRs with at least two commits** in the local PR refs, not only the newest 10. For every PR, commits are enumerated from `merge-base(main, PR head)` through the PR head; shared intermediate head SHAs are queried once. This produced **5,610 PR-to-commit appearances** and **5,283 unique head SHAs** after removing 327 shared-head duplicates.

| Repo | Eligible PRs | Commit appearances | Unique head SHAs | Shared-head duplicates | CI failure | CI success | CI unavailable | PRs using first-commit fallback | Unique candidate heads | Forecast median / p90 | Rough expected unique positives |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| click | 282 | 1,559 | 1,390 | 169 | 78 | 114 | 1,198 | 232 | 285 | 111.8 / 123.4 min | 56.4 |
| Jinja | 162 | 721 | 705 | 16 | 55 | 30 | 620 | 131 | 179 | 37.3 / 44.1 min | 32.4 |
| attrs | 510 | 3,330 | 3,188 | 142 | 122 | 158 | 2,908 | 475 | 508 | 237.8 / 273.2 min | 0.0 |
| **Total** | **954** | **5,610** | **5,283** | **327** | **255** | **302** | **4,726** | **838** | **972** | **386.8 / 440.7 min** | **88.8** |

Candidate heads are deduplicated after applying the per-PR rule: if any commit in a PR has unavailable CI, select only that PR's first commit; otherwise select all failure commits and `ceil(10%)` of success commits with seed `20261122`. Because PRs can share a head SHA, fallback, failure, and success selections overlap; therefore their raw counts do not sum directly to the unique candidate-head count. “Rough expected unique positives” linearly extends the feasibility rates requested (click `2/10`, Jinja `2/10`, attrs `0/10`) to all eligible PRs. This is only a rough extrapolation from 10 sampled PRs per repo and does not correct for connected PR groups or sampling uncertainty; it is not an observed count.

Status provenance: **1,700 SHAs** came from the REST check-runs endpoint. The remaining **3,583** were fetched in GraphQL batches from `statusCheckRollup.contexts` (which exposes the same check-run conclusions and app/check names); of these, **3,273** had no status-check rollup and were conservatively counted as unavailable. The resulting totals still reconcile exactly to all 5,283 unique SHAs. For available checks, classification uses the latest completed check per name/app: any `failure`, `timed_out`, or `startup_failure` means failure; all `success`, `neutral`, or `skipped` means success; pending, absent, or incomplete CI means unavailable. No check-run status was inferred from a pytest result.

Attrs' full-history forecast is above one hour. A newest-first cap of 100 PRs would still estimate **114 unique candidate heads** and **61.3 min p90**. A cap of **90 newest PRs** reduces that to **105 unique candidate heads**, with an estimated **49.1 min median / 56.5 min p90** at the attrs feasibility timings. This is a proposed cap only; no pytest was launched for this inventory.

The complete per-PR commit lists and SHA-to-PR mapping are in `.cache/measurements/week7/pr-intermediate/full-history-inventory.json`; CI states, selection reasons, and selected SHA lists are in `.cache/measurements/week7/pr-intermediate/full-history-ci-inventory.json`. The full status cache is `check-runs-cache-full.json`. The one-time inventory runner is `scripts/inventory_full_pr_ci.py`; status retrieval and selection ran with no pytest.

### Authorized PR-intermediate run (2026-11-23)

After authorization, the selected CI-filtered candidates were run sequentially under Python 3.13.18, with a 300-second timeout per pair and 1,800-second maximum per resumable batch. The full-history policy selected all **285 click** and **179 Jinja** candidate heads. As forecast above, attrs was capped at the **90 newest PRs**, yielding **105** candidate heads; the remaining attrs history was not run. The full run therefore covered **569 unique head SHAs**, with no pending candidates at completion.

| Repo | Selected heads | Completed | Stable positive heads | Unique positive changes before function audit | `test_modified_in_head` true / false | Other terminal outcomes |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| click | 285 | 254 | 24 | **21** | 4 / 20 | 26 `test_collection_error`, 4 `head_source_invalid`, 1 `timeout` |
| Jinja | 179 | 95 | 3 | **2** | 0 / 3 | 54 `test_collection_error`, 29 `env_incompatible`, 1 `head_source_invalid` |
| attrs (newest 90 PRs) | 105 | 103 | 7 | **5** | 0 / 7 | 1 `test_collection_error`, 1 `env_incompatible` |
| **Total** | **569** | **452** | **34** | **28** | **4 / 30** | **81 `test_collection_error`, 30 `env_incompatible`, 5 `head_source_invalid`, 1 `timeout`** |

The pre-audit “unique positive changes” count deduplicates by PR group and the full stable failing test-ID set, so multiple intermediate heads in one PR change count once. The raw run found **28 unique stable positive changes**: click **21**, Jinja **2**, attrs **5**. For **all 28**, at least one base-existing shared test passed twice on base and failed twice on head. In **24/28 (85.7%)**, the failing test file was unchanged by the head; in **4/28 (14.3%)**, that file was changed. Across head outcomes, 30/34 positives had `test_modified_in_head=false`; repeated heads in the same PR group explain why this differs from the unique-change count. All positives had `unresolved_positive_test_ids=[]`. The function-level and semantic audited counts below supersede these raw counts for model evaluation. After the later PR-context audit, the primary manifest retained 24 candidates (click 18, Jinja 1, attrs 5): click PR group #3872/#3873 was excluded as spam, and Jinja #2256 was withheld as uncertain. See `real-pair-semantic-audit.md` and `real-pair-audit.md`.

This is evidence that PR-intermediate changes can produce stable failures in existing shared tests, unlike the revert-fix result above. It is **not** yet a model-vs-`cov_lines` evaluation: the selected observations are CI-filtered and attrs covers only its newest 90 PRs; no LORO or recall/budget-curve metrics were run. The CI prefilter also means this is not a census of all intermediate heads. `test_collection_error`, `env_incompatible`, `head_source_invalid`, and `timeout` are exclusions, never negative labels. The one timeout was click PR #1372 head `99352735ba` at 300 seconds. The 30 env-incompatible outcomes were 29 historical Jinja package-install failures and one attrs test-extra install failure. Of 81 collection errors, 29 logs show `AttributeError: __spec__` (old vendored import helper), and 26 Jinja logs show the removed `markupsafe.soft_unicode`; the remaining cases include other import/plugin/missing-dependency failures. Five source-invalid heads failed parsing or import with syntax/indentation errors.

#### Function-level positive audit

The manual audit maps changed test lines in each positive head to the enclosing `test_*` function, then removes a failing test ID only when that exact function was changed. This is stricter than the earlier `test_modified_in_head` file-level flag. Three of the four click changes with a modified test file overlapped positive functions: PR #767 (`test_nargs_tup_composite`) and PR #327 (`test_multiple_default_help`) have no remaining positive after exclusion; PR #446 keeps `test_basic_group` while excluding the changed `test_auto_shorthelp`. PR #422 changed a test file but none of its positive functions (`test_option_custom_separator`) changed, so its positives remain. The function-level audit initially leaves **26 candidates**: click **19**, Jinja **2**, attrs **5**. The later semantic PR-context review retains **24 primary candidates** (click **18**, Jinja **1**, attrs **5**); two primary rows have `test_modified_in_head=true` at file level, with no overlap in the changed failing function. The two excluded/uncertain candidates and exact test IDs are retained in the audit reports and manifests.

The summed per-pair `wall_seconds` across the 569 latest unique-head results is **9,427.3 s (2 h 37 min)**. The raw JSONL contains 570 rows because attrs PR #1429 was first misclassified when an optional test extra was installed without build isolation; the runner was corrected and the pair retried successfully. Counts above use the successful latest row per head. Batch summaries and per-pair logs are retained alongside the JSONL.

Reproduction/resume command (rerunning skips terminal head SHAs already in `pairs.jsonl`):

```sh
python3 scripts/run_full_history_ci_pairs.py \
  --inventory .cache/measurements/week7/pr-intermediate/full-history-inventory.json \
  --ci-inventory .cache/measurements/week7/pr-intermediate/full-history-ci-inventory.json \
  --out-dir .cache/measurements/week7/pr-intermediate/full-history-run \
  --attrs-newest-prs 90 --python python3.13 \
  --pair-timeout-s 300 --batch-timeout-s 1800 \
  --repo-paths "click=$W6_REPOS/click" \
  --repo-paths "jinja=$W6_REPOS/jinja" \
  --repo-paths "attrs=$W6_REPOS/attrs"
```

Primary artifacts: `.cache/measurements/week7/pr-intermediate/full-history-run/pairs.jsonl` (per-head result, reason, positive test IDs, changed paths, and log directory), `summary.json` (deduplicated final counts and positive test-ID sets), `progress.json` (last batch summary), and `work/pairs/` (per-pair logs and JUnit XML). The run did not change the selection policy or run the unselected attrs history.

Selected first intermediate commits (PR and commit counts):

| Repo | Newest-first sample |
| --- | --- |
| click | #3924 (17), #3919 (5), #3917 (3), #3873 (4), #3872 (3), #3856 (2), #3854 (2), #3835 (2), #3833 (3), #3825 (2) |
| Jinja | #2277 (2), #2275 (2), #2266 (2), #2256 (3), #2254 (2), #2253 (2), #2251 (158), #2249 (3), #2248 (3), #2242 (7) |
| attrs | #1646 (6), #1660 (2), #1659 (2), #1654 (3), #1656 (3), #1644 (4), #1652 (6), #1650 (4), #1631 (3), #1648 (4) |

Deduplicated pair rows, the preserved raw rows, JUnit/log artifacts, and summaries are in `.cache/measurements/week7/pr-intermediate/`; `run.stderr.txt` from the prior feasibility batch and `ci-inventory.stderr.txt` were empty.

Reproduction (from the TestPilot checkout; set `W6_REPOS` to the directory containing the three local clones):

```sh
# PR refs were fetched from upstream for click/Jinja because their local origin
# is a bare cache; attrs used its origin and refetch to repair the incomplete clone.
git -C "$W6_REPOS/click" fetch https://github.com/pallets/click.git '+refs/pull/*/head:refs/pr/*'
git -C "$W6_REPOS/jinja" fetch https://github.com/pallets/jinja.git '+refs/pull/*/head:refs/pr/*'
git -C "$W6_REPOS/attrs" fetch --refetch origin '+refs/pull/*/head:refs/pr/*'

python3.13 scripts/run_pr_intermediate_feasibility.py \
  --repo "click=$W6_REPOS/click" \
  --repo "jinja=$W6_REPOS/jinja" \
  --repo "attrs=$W6_REPOS/attrs" \
  --main-ref refs/remotes/upstream/main \
  --python python3.13 --sample-prs 10 \
  --pair-timeout-s 300 --batch-timeout-s 1800 \
  --inventory-only \
  --out-dir .cache/measurements/week7/pr-intermediate
```

This command inventories check-runs and calculates the fallback selection and forecast, then exits before pytest. It uses `gh api repos/<owner>/<repo>/commits/<sha>/check-runs`; `ci-inventory.json` records per-SHA conclusions and selection. Do not remove `--inventory-only` until the CI-filtered run is separately authorized.

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
