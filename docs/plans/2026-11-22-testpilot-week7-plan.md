# TestPilot Week 7 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish whether the model beats `cov_lines` on real bug-fix regressions, while closing Week 6 measurement and provenance gaps.

**Architecture:** Keep measurement corrections, test-width selection, real-pair collection, model experiments, and conditional explain work as separately reviewable commits. Generate immutable per-run provenance and real/synthetic artifacts; never use synthetic metrics to approve a real-data conclusion.

**Tech Stack:** Rust CLI/core, Python feature/training scripts, Bash measurement runner, pytest, SQLite/Parquet/JSON, Git worktrees.

**Spec:** `docs/specs/2026-11-22-testpilot-week7-spec.md`

## Global Constraints

- Base is `origin/main` at `b0fb10f7f5070d062128efd2b6150d814963b5b8`; work on the `feat/week7` worktree.
- Do not modify or reset the original checkout’s dirty dry-run/env/fixture files.
- Do not merge or push to `main`.
- Do not start a measurement expected to exceed 30 minutes before the user’s explicit OK.
- The user runs long measurements; provide exact commands, inputs, and calibrated estimates first.
- `partial=1` records never enter training or evaluation; no partial/full mixing.
- Treat missing real metrics as unavailable; never substitute zero or synthetic metrics.
- Change the default only if the real-only criterion in the spec passes; otherwise keep `cov_lines`.
- Each implementation stage/0.x substage gets a distinct commit, PR in `feat/week7`, green CI, and an evidence report.
- Every reported number names its input file or command output. Distinguish observed values from planning estimates and in-sample dry-run reconstruction.

## Review Focus

- A failing head test missing from base must increment the separate missing-base counter and never become `new_fail`; test in both aggregator and site builder.
- Base/head test ID intersections must use outcomes from the same test IDs, including skip/error rows; absent tests must not silently compare as pass.
- Real TEST folds with no positive events must serialize as unavailable with `n=0`, not as recall `0` or NaN interpreted as a score.
- A real pair is base=fixed commit, head=fixed commit with production fix changes reverted while the regression test/fixtures remain; this `base → head` diff is the `select` change.
- A bug/fix pair, duplicate test, or related commit must not leak between train/validation/TEST; group by source bug/change pair.
- Unknown duration (`dur=-1`) and partial runs must not fabricate time savings or enter the model comparison.

---

## Task 0.1: Correct shared-test Level-B labels

**Files:**
- Modify: `scripts/aggregate_bootstrap_w6.py` (`run_details`, `collect_mutants`, `compare_level_b`)
- Test: `scripts/mutate/test_measurement_aggregation.py`
- Modify: `site/scripts/build-demo-data` (`caught`, case serialization)
- Test: `site/scripts/test_build_demo_data.py`
- Create: `docs/measurements/week7/week6-level-b-delta.json` and `.md` from the original Week 6 artifacts

**Interfaces:**
- Aggregator run details return failed, flaky, and present test ID sets for the selected base/head run.
- Level-B output includes new-fail and missing-from-base counts, changed labels by mutant SHA/test ID, and old/new label deltas.
- Demo case adds `missing_from_base_test_count` and test IDs; `new_fail_n` counts only failing non-flaky tests in `head_ids ∩ base_ids` absent from base failures.

**Estimate:** 4–6 engineering hours, plus local artifact re-aggregation. The aggregation itself is expected to be short; measure before reporting if runtime is material.

- [x] Add aggregator test where a shared test fails in head but passes in base; assert it is a `new_fail`.
- [x] Add aggregator test where a head failure's test ID is absent from base; assert it is excluded from `new_fail` and counted separately.
- [x] Add tests for flaky shared tests, collection errors, and missing labels on common mutants.
- [x] Implement shared-test intersection and counters in `scripts/aggregate_bootstrap_w6.py`.
- [x] Add demo tests for missing-base failures and ordinary shared-test new failures; assert shared-test labels and separate missing-base count/test IDs.
- [x] Implement the matching semantics in `site/scripts/build-demo-data` while preserving the existing JSON readers.
- [x] Run focused tests:

  ```bash
  .venv-train/bin/python -m pytest -q scripts/mutate/test_measurement_aggregation.py
  python3 site/scripts/test_build_demo_data.py
  ```

- [x] Re-run Level-B comparison on Week 6 saved ledgers/DBs, compare old/new labels by mutant SHA and test ID, and write exact changed counts plus label/metric deltas (including explicit zero deltas) to the two Week 7 artifacts.
- [x] Verify no W6 metric was silently overwritten; attach both old and recalculated values and source paths.
- [x] Commit: `fix: compare only shared tests in level-b labels`.

**Acceptance:** Both test suites prove common-ID semantics; missing-base test failures have a separate counter; Week 6 recalculation lists exactly which labels changed and the deltas, or explicitly records no changes.

## Task 0.1b: Pilot one real pair

**Position:** Immediately after 0.1 and before 0.2/0.3. The pilot checks the procedure and environment; it is not part of final metrics unless re-collected under Task 2A's full audit rules.

**Artifacts:** `docs/measurements/week7/real-pair-pilot.json` and `.md`.

- [x] Choose one plausible pair from the existing offline shortlist; document buggy/fixed SHAs and the code-only revert while retaining the regression test/fixtures.
- [x] Prepare commands for Python 3.13 install/build on both trees and shared-test runs on base/head. Classify install/build incompatibility as `env_incompatible`.
- [x] Run the pilot on the user's behalf after the user explicitly asked the agent to run it.
- [x] Report setup/install/build/test wall times; record shared-test failures, missing-from-base failures, repeat consistency, and whether any failure other than the new regression test appeared.
- [x] Use measured pilot timing to estimate Task 2A test-running cost.

**Acceptance:** A reproducible pilot report exists before Task 0.2 begins; no pilot result is presented as model evidence.

## Task 0.2: Snapshot run provenance and correct attrs history

**Files:**
- Create: `scripts/measurement_provenance.py`
- Test: `scripts/mutate/test_measurement_provenance.py`
- Modify: `scripts/measure_bootstrap_w6.sh`
- Modify: `scripts/aggregate_bootstrap_w6.py`
- Modify: `scripts/features_reference.py` (canonical `repo_name` at export boundary)
- Modify: Week 6 provenance in `docs/measurements/bootstrap-{click,jinja,attrs}/` and `docs/measurements/loro-w6-click-jinja-attrs/`

**Interfaces:**
- `measurement_provenance.py --root ROOT --bin BIN --out JSON` writes `head_sha`, `bin_version`, binary SHA-256, capture time, and per-script `{path, commit_blob_sha1, worktree_blob_sha1, dirty}`.
- Script inventory includes `scripts/mutate_bootstrap.py`, `scripts/mutate/run_mutant.py`, each `scripts/measure_*.sh`, `scripts/aggregate_bootstrap_w6.py`, and the exporter itself.
- The measurement runner captures one snapshot before the full run; aggregator consumes that snapshot instead of embedding revisions.

**Estimate:** 3–5 engineering hours.

- [x] Add helper tests for a clean script, an edited script, a missing optional script, and failed/malformed `bin --version`.
- [x] Record commit-tree blob and worktree blob separately, with a dirty flag for each script.
- [x] Add runner invocation before measurement starts, retain the snapshot with measurement outputs, and pass it to the aggregator.
- [x] Replace hardcoded current-run revisions with the captured snapshot; fail closed on missing required scripts or malformed version output.
- [x] Make the canonical repository label an explicit `--repo-name` export input; test that an `attrs-rerun12` checkout exports under `attrs` when requested.
- [x] Remove `normalize_attrs_run_name()` and its one-off key/path rewrites from `aggregate_bootstrap_w6.py`; no report-only normalization replaces it.
- [x] Trace attrs provenance from Git history. `mutate_bootstrap.py` at `092d6cc`, `9e863f1`, and `978b50b` has blob `548fcc2`; `run_mutant.py` differs at those commits. The 36 pair attribution is corrected by Git history, but exact per-pair versions are unrecorded because the ledger is absent.
- [x] Preserve the 76 early attrs pairs as an estimate across varying classifier versions before `978b50b`; mark the pair-level ledger as absent.
- [x] Add provenance tests for identical snapshots in report outputs and absence of obsolete hardcoded revisions.
- [x] Commit: `fix: capture measurement provenance at run time`.

**Acceptance:** Clean/dirty fixture runs yield auditable commit/blob pairs and `bin_version`; the runner captures a single snapshot before measurement and the aggregator reuses it; historical Week 6 provenance cites Git evidence and uncertainty; no `rerun12` normalization remains in the aggregator. Full Week 6 measurement was not rerun.

## Task 0.3: Calibrate dry-run by observed steps

**Files:**
- Modify: `crates/testpilot-core/src/bootstrap/estimate.rs`
- Modify: `docs/measurements/bootstrap_time_priors.json`
- Test: create `crates/testpilot-core/tests/bootstrap_estimate.rs`
- Modify: `scripts/measure_bootstrap_w6.sh` and dry-run report output
- Create: `docs/measurements/week7/dry-run-calibration.json` and `.md`

**Interfaces:**
- Per-suite estimate models `collect_git`, `backfill`, `mutate`, `features_dump`, and `train` separately.
- `eligible_hat = attempted_hat × observed_eligible / observed_attempted`.
- `features_dump_hat = eligible_hat × observed_features_dump / observed_eligible`.
- No attrs samples feed coefficient fitting.

**Estimate:** 4–6 engineering hours.

- [x] Add estimator tests for backfill count changes, K/attempt scaling, eligibility ratio, zero eligible count, and legacy prior compatibility.
- [x] Fit click and jinja coefficients from their `bootstrap_report.json` step timings; retain the raw input counts and report `actual_s` definition.
- [x] Verify the same-run reconstruction from Week 6 raw inputs: click estimate `1608.31 s` vs `1718.03 s` actual (absolute error `6.39%`); jinja `2776.40 s` vs `2414.99 s` actual (`14.97%`).
- [x] Add calibration method and evidence paths to JSON/Markdown. Label as in-sample reconstruction, not independent validation; do not fit coefficients from attrs.
- [x] Run `cargo test -p testpilot-core --test bootstrap_estimate`, existing bootstrap estimator tests, and CLI dry-run tests.
- [x] Commit: `feat: calibrate bootstrap estimate by observed steps`.

**Acceptance:** Reproducible click/jinja same-run errors are each ≤15%; attrs W6 samples are excluded from fitting (the feature-dump/eligibility estimate uses a labeled click/jinja proxy); dry-run exposes all modeled steps and assumptions. Full measurement was not run.

## Task 2A: Build and validate real bug-fix pairs offline

**Files:**
- Create: `scripts/collect_real_bugfix_pairs.py`
- Test: `scripts/mutate/test_real_bugfix_pairs.py`
- Create: `docs/measurements/week7/real-bug-pairs.jsonl` and `real-pair-audit.md`

**Interfaces:**
- Manifest row records repo/pin, source bug/fix commits, fixed `base`, code-reverted `head`, reverted code paths, retained test/fixture paths, environment ID, all shared test outcomes, wall time, and inclusion/exclusion reason.
- A pair is positive when at least one non-flaky shared test fails on head and passes on base; include every such test ID, not only the newly added test.

**Pilot evidence:** `docs/measurements/week7/real-pair-pilot.md` records 9.57 s for the first full-suite base+head pass and 15.31 s across two passes per tree; the fresh-venv/dependency/base-install/head-install steps took 3.95 s with a warm pip cache. This is one click pair and does not predict installation cost for other repos.

**Primary Task 2A count:** track positive real pairs with at least one failure outside the added regression-test function(s), divided by all audited positive real pairs. Initial pilot value is 0/1 (0%); excluding both added click functions removes all 12 parametrized cases and leaves 0 positives. The 31,000 deselected cases per pytest run are Click stress tests selected out by its `addopts`.

**Estimate:** After patch-id dedupe the scan has 1,183/968/593 leads for click/jinja/attrs; source+test filters leave 452/334/210 leads. The ≤30 min calibration batch measured per-repo medians and p90 values; see `real-pair-candidate-screening.md`. Attrs has missing Git objects in the first 15 leads and no valid pair-time estimate. Forecasts replace the old ~175 min cross-repository proxy. The 1–2 engineering day estimate for tool, validation, and audit workflow excludes additional pair runs.

- [ ] Add tests for malformed code-only reverts, accidentally omitted regression tests/fixtures, unchanged/shared outcomes, and fixture-required cases.
- [x] Add a patch-id regression test covering one fix on a branch, its first-parent merge commit, and a stable-branch cherry-pick; assert they dedupe to one lead.
- [x] Test old click/Jinja package layouts, all-extension source paths, `bugfix` matching, quoted Revert exclusion, and patch-id dedupe across a cherry-pick and first-parent merge diff.
- [x] Explicitly scan merge commits against first parent while retaining internal commits; sort the deduplicated shortlist newest-first.
- [x] Expand CI Ruff coverage to `scripts/` (baseline rules E4/E7/E9/F); fix reported issues and format the four reviewed files.
- [x] Remove machine-specific absolute paths from the screening report; add optional SHA-256 for the measurement binary provenance.
- [x] Check existing local artifacts/caches for CI run logs without network or credentials. No matching CI/workflow/action log files were found; used the offline bug-fix-pair fallback and did not query remote CI or fetch history.
- [x] Scan the full locally available history, not a fixed recent-commit window. Record message/changelog matches, source+test changes, and test-code heuristic counts in `docs/measurements/week7/real-pair-candidate-screening.md`; details are also retained in the ignored local candidate JSONL artifacts.
- [ ] Triage potential candidates, confirm regression-test intent from commit diff, and record rejected candidates with reasons.
- [ ] Construct an isolated `head` by reverting only production-code fixes from fixed `base`, retaining the regression test and fixtures; persist the exact patch and tree identities.
- [ ] Classify Python 3.13 install/build failures as `env_incompatible`, separately from tests that run but do not establish a regression.
- [ ] Provide run commands and time estimates before the user executes pairs. Any run over 30 minutes requires the user's explicit OK.
- [x] Run the user-authorized calibration batch (first 15 source+test leads per repo after dedupe, ≤30 min). Persist pair logs and report statuses, outcome positives, outside-test counter, median/p90, and remaining-time forecast.
- [ ] Run the remaining accepted pairs after the user's next OK; preserve commit/test/environment identities and produce a data inventory with positives by repo and chronological fold.
- [ ] Commit collection logic and tests; keep raw clones/databases/parquets out of Git unless the user approves storing them.

**Acceptance:** No candidate enters the real dataset without both buggy-fail and fixed-pass evidence; pair grouping and failure reasons are auditable; incomplete attempts do not become positives.

**Real pair semantics:** `base` is the fixed commit; `head` is the fixed commit with only the production-code fix reverted, keeping the regression test and required fixtures. The `base → head` diff is the real, potentially multi-line change passed to `select`. Positives are all non-flaky shared test IDs failing on head and passing on base. Report metrics both with and without the added regression test, plus the fraction of positive pairs with at least one other failing test. The new test was written against the fixed lines and may favor `cov_lines`.

## Task 2B: Evaluate real/synthetic data, ablations, source weights, and reranker

**Files:**
- Modify: `python/testpilot-train/src/testpilot_train/train_lib.py`
- Modify: `python/testpilot-train/src/testpilot_train/metrics.py` and reporting code as needed
- Modify: `crates/testpilot-core/src/features/{schema.rs,change_rows.rs,coverage.rs,imports.rs,cochange.rs,history.rs}` only for approved new features
- Modify: `scripts/features_reference.py` to preserve feature parity
- Tests: `python/testpilot-train/tests/test_loro.py`, `tests/test_metrics.py`, new focused feature/weight/eval tests, and Rust feature tests
- Create: `docs/measurements/week7/summary.json`, `summary.md`, `budget-curve.json`, `budget-curve.md`

**Interfaces:**
- Training entry takes source arm (`mutant_only`, `real_only`, `mix`), normalized synthetic:real weight ratio, and feature ablation; the TEST data is always `synthetic=0` for the decision score.
- Evaluation returns separate real/synthetic blocks with metric or unavailable reason, counts, CI, baseline values, fold IDs, and duration coverage.
- LORO returns held-out repo rows and macro metrics for real/synthetic separately.

**Estimate:** 2–4 engineering days after real pair artifacts exist; training jobs should be measured from dry-run/local sample and may exceed 30 minutes. User OK required before those runs.

- [ ] Add unit tests for real-only, synthetic-only, and missing-source folds; assert missing positives produce unavailable, not zero.
- [ ] Add tests proving `partial=1` exclusion, pair grouping across splits, and no synthetic TEST contribution to real paired CI.
- [ ] Implement split-grouping by source bug/fix pair and chronological order; assert held-out source IDs do not occur in train/val.
- [ ] Implement the pre-registered ablations and source arms. Fit `4:1`, `1:1`, `1:4` weights using validation only.
- [ ] Evaluate five feature hypotheses from the spec and record real/synthetic sentinel coverage and conditional contribution; do not interpret synthetic-empty co-change/failure history as a real result.
- [ ] Implement and test the `cov_lines` reranker correction. Choose correction strength on validation only and compare on identical real TEST examples.
- [ ] Produce real/synthetic `recall@10/20/30%` for each LORO fold and aggregate; paired CI and per-repo Δ use the same held-out real cases.
- [ ] Before computing/reporting comparative metrics, require at least 10 confirmed real-positive changes per repo for numeric per-repo Δ; otherwise serialize that repo as `data_insufficient` without numeric Δ. Criterion 2.4 is not satisfied while any repo is underpowered.
- [ ] Report comparisons with and without the added regression test and the fraction of positive pairs with another failing test.
- [ ] Produce budget curves using real `dur` milliseconds; report `dur=-1` coverage and leave unknown savings unavailable.
- [ ] Apply the real-only success criterion verbatim. If it fails or is underpowered, say “real-data superiority not demonstrated” and keep `cov_lines` default.
- [ ] Report commands, stdout/stderr paths, artifact paths, counts, uncompleted analysis, and reason. Commit and open this stage’s PR after local checks; wait for green CI.

**Acceptance:** Every comparison names source arm, fold, real/synthetic subset, and counts; the success gate is computed only on real held-out TEST; `partial=1` is absent; default remains `cov_lines` unless all three real-only conditions pass.

## Deferred Task 1: Reach sufficient synthetic TEST width (only after Task 2A if time remains)

**Files:**
- Modify: `scripts/measure_bootstrap_w6.sh` or add a Week 7 measurement command wrapper with `N`, repo subset, and pins as explicit arguments.
- Tests: shell argument/parser tests or a dry-run command test; no full data run in CI.
- Create: `docs/measurements/week7/test-width-options.md` after user-run measurements.

**Interfaces:**
- User command selects repository pins, `N`, `K`, jobs, and dry-run/full mode explicitly.
- Dry-run prints calibrated per-step estimate, formula version, binary version, and exact repository pins.

**Estimate:** 4–6 engineering hours for runner changes, plus user-owned measurement time. Formula proxy from the spec: click N=400 ≈53.4 min; attrs N=400 ≈94.5 min (cross-suite proxy only); a fourth repo N=200 ≈50.0 min before exact pin dry-run. These are planning estimates only; no run is approved by this plan.

- [ ] Add runner validation for positive N/K/jobs, known suite names, unique output directories, and pin/hash match.
- [ ] Add a dry-run-only test proving it never enters a full measurement path.
- [ ] Screen `pluggy`, `packaging`, and `iniconfig` at exact pins for Python 3.13, pytest default-suite command, no network/service use, and plausible history/test count; reject candidates that fail any condition.
- [ ] Only if time remains after Task 2A, present exact pins, commands, and calibrated dry-run estimates for variants A/B/C from the spec. Ask the user to choose only after this evidence is reviewable.
- [ ] No measurement run is authorized in advance. After the user chooses a variant, provide commands and estimates and wait for explicit OK before starting any run; runs expected to take more than 30 minutes always require explicit OK.
- [ ] User runs selected full measurements. After each finishes, import only complete (`partial=0`) outputs, calculate `n_C` per repo, and report whether it reached 10. If it did not, stop and provide a new estimate before suggesting another N.
- [ ] Commit runner/tests separately from generated measurement results; attach pinned outputs and command logs.

**Acceptance:** Each chosen repo has `n_C ≥ 10` in synthetic TEST, or the report explicitly says target not reached and leaves `cov_lines` default; no long measurement starts without user OK.

## Task 3: Conditional `select --explain`

**Files:**
- Modify: `crates/testpilot-cli/src/main.rs`, select JSON serializer/schema, and client documentation
- Modify: `crates/testpilot-core/src/lgbm/` only to expose `pred_contrib` safely
- Tests: `crates/testpilot-cli/tests/` select JSON compatibility and explain output tests; core contribution tests

**Estimate:** 1–2 engineering days, only after tasks 0–2 are complete and the user confirms time remains.

- [ ] Add a version+1 schema test that old JSON consumers still accept and new output includes at most three contributions.
- [ ] Add a model explanation test asserting top-3 contributions sum consistently with raw model score within LightGBM tolerance.
- [ ] Add `ranker=cov_lines` explanation test with exact phrase “covers N changed lines”.
- [ ] Assert no output labels a raw score as a probability.
- [ ] Implement only if condition is met; otherwise record `not done: stages 0–2 consumed available time` in the final report.

## Stage report and commit protocol

At each stage end, include:

1. commit/PR and changed paths;
2. exact commands and their concise output;
3. CI run and conclusion;
4. measurement inputs, pins, provenance, and artifact paths;
5. observed results versus estimates;
6. what remains undone and why;
7. whether `cov_lines` remains default.

Do not merge any Week 7 PR into `main`.
