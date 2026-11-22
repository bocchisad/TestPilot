# Real-pair pilot: click

## Pair

- Fixed base: `c53d3732b98c764932e146c007146ad402acb229`.
- Head: `c74942ad32c5a7ee926b65c5a80030c9bf8e96af`; only `src/click/core.py` was restored from the fixed commit’s first parent. The added tests and `CHANGES.md` are unchanged.
- Added regression tests: `tests/test_options.py::test_show_envvar_empty` and `test_missing_envvar_sequence`.
- Runtime: Python 3.13.18, pytest 9.0.2 from the commit’s `uv.lock`; both tree installs/builds passed.

## Results

| Run | Result | Time |
| --- | --- | ---: |
| base, first | 2110 passed, 25 skipped, 1 xfailed; exit 0 | 6.47 s |
| head, first | 2101 passed, 9 failed, 25 skipped, 1 xfailed; exit 1 | 3.10 s |
| base, repeat | 2110 passed, 25 skipped, 1 xfailed; exit 0 | 2.46 s |
| head, repeat | 2101 passed, 9 failed, 25 skipped, 1 xfailed; exit 1 | 3.28 s |

- Shared test IDs: 2136. No test IDs were absent from base among head failures.
- Positive pair: **yes**; 9 shared tests failed on head and passed on base. Outcomes repeated consistently; no flaky IDs observed.
- Failing tests outside the newly added regression tests: **0**. Positive pairs with another failure: **0/1 (0%)**.

## Setup and runtime

| Step | Elapsed |
| --- | ---: |
| Create Python 3.13 venv | 1.522 s |
| Install pytest from `uv.lock` | 0.993 s |
| Build/install base | 0.716 s |
| Build/install head | 0.719 s |
| Base tests, first/repeat | 6.47 s / 2.46 s |
| Head tests, first/repeat | 3.10 s / 3.28 s |

Setup/package timing used the local pip cache. Raw step logs and timing JSON are retained in the local artifact directory.

- The only nine failures are parametrized cases of the added regression tests; this is direct evidence for the spec’s `cov_lines` bias caveat.
- Approximate end-to-end pilot time, including worktrees, dependency setup, installs and four suite runs: **about 25 s**, vs the 5–10 min planning estimate.

## Artifacts and limits

Raw logs and JUnit XML are retained under a local temporary directory; its path is in the work-session output, and filenames are listed in `real-pair-pilot.json`. They are not copied into Git. This is one pair and not a model comparison; use it only as the process/time pilot. The pair must still pass the full Task 2A provenance and screening audit before entering the final dataset.

An initial install command referenced `requirements/tests.txt`, absent at this historical commit. This was corrected by installing pytest 9.0.2 from the commit’s `uv.lock`; it was not classified as `env_incompatible`.
