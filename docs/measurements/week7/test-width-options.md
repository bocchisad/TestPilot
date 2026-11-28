# Week 7 synthetic TEST width: dry-run options and measured runs

Date: 2026-11-27
Branch: `feat/week7`, code head `3409041717c2a09c16213d53c2927ed61a762773`
Purpose: choose larger synthetic samples after N=400 did not meet `n_C >= 10` for click or attrs. The user approved the click N=500 and attrs N=800 runs.

## Setup and estimates

The runs used Python 3.13.18, `K=4`, `jobs=2`, and `seed=42`, with pinned trees:

- click: `934813e4d421071a1b3db3973c02fe2721359a6e`
- attrs: `598494a618410490cfbe0c896b7a544f6d23e0d9`
- binary: `testpilot 0.1.0 (3409041717c2)`, SHA256 `70102d12c5c4fbd1b4623b41784947618b3b8ef4cc3fbeaa63ac73abf972371b`

Dry-run command pattern:

```sh
python3 scripts/run_week7_test_width.py --mode dry-run --n N --k 4 --jobs 2 --seed 42
  --suites click attrs
  --repos-root .cache/measurements/week7/test-width/repos-estimate
  --run-dir .cache/measurements/week7/test-width/run-nN
```

Full-run commands used the same options with `--mode full`, suite-specific output directories, and N=500 for click / N=800 for attrs. The attrs command first used `--no-resume`; after runner fixes it was resumed three times with `testpilot bootstrap ... --resume`.

## Compared dry-run options

These are the estimates recorded before the full runs; combined times assume click and attrs run sequentially.

| Option | click estimate | attrs estimate | Combined estimate |
| --- | ---: | ---: | ---: |
| N=500 both | 66.7 min | 220.1 min | 286.8 min (4 h 47 min) |
| N=600 both | 80.1 min | 264.1 min | 344.2 min (5 h 44 min) |
| click N=500 + attrs N=800 | 66.7 min | 352.1 min | 418.8 min (6 h 59 min) |
| N=800 both | 106.7 min | 352.1 min | 458.8 min (7 h 39 min) |

The earlier N=400 measurements had `n_C=9/18` for click and `6/20` for attrs, below the threshold. The selected click N=500 / attrs N=800 combination reached `n_C >= 10` for both in the actual runs. The intermediate N=600 option was not run.

## Estimate versus measured runtime

| Suite / N | Dry estimate | Measured CLI time | Difference | Width result |
| --- | ---: | ---: | ---: | --- |
| click / 500 | 66.74 min | 71.49 min wrapper wall | +7.1% | `n_C=15`, target met |
| attrs / 800 | 352.13 min | 206.97 min cumulative step timings | −41.2% | `n_C=13`, target met |
| Combined | 418.88 min | 278.46 min | −33.5% | both targets met |

Click was a single successful wrapper invocation. The attrs figure is the resume-safe sum of stage timings accumulated in bootstrap state across the initial attempt and three resume invocations. It is 206.97 minutes; the first wrapper invocation stopped after 166.72 minutes, so that time is already represented in the cumulative stage timings and must not be added again. Time paused between invocations for diagnosis and repair is excluded. On this comparable stage-time basis, attrs was 41.2% below the 352.13-minute estimate, and click plus attrs totaled 278.46 minutes versus 418.88 minutes estimated (−33.5%). This gap shows that the attrs dry-run substantially overestimated this run; it does not include wall-clock interruptions.

Dry-run JSONs: `run-n400-final/dry-run.json`, `run-n500/dry-run.json`, `run-n600/dry-run.json`, `run-n800/dry-run.json`. The attrs estimate was especially uncertain because feature and eligibility rates were cross-suite proxies.

## Results and interpretation

- **click N=500:** `n_C=15`; synthetic-test versus `cov_lines` paired delta mean −0.0134, CI95 [−0.0350, 0.0032], `n=33`. Gate: `use_baseline` (`synth_test_ci_low_lt_0`).
- **attrs N=800:** `n_C=13`; synthetic-test versus `cov_lines` paired delta mean +0.0990, CI95 [+0.0235, +0.1968], `n=36`. Synthetic gate: `use_model` (`synth_test_ci_low_ge_0`).

These are synthetic-mutant results only. They do not answer the preregistered real-PR-intermediate comparison and do not justify changing the general default or claiming the model is better. In particular, the attrs report flags 124 always-broken test IDs; this is a substantial data-quality diagnostic and should be examined before interpreting the synthetic result as useful ranking signal. Week 7's real-case criterion remains open.

## Run caveats and artifacts

The attrs initial wrapper stopped after 10003.324 seconds because a mutant-induced runtime failure before JUnit XML was mistaken for an infrastructure failure. That initial attempt is included in cumulative step timings in the final report. During the resumed attrs run, a broad changed-source fallback was temporarily present. The full suite later showed it could misclassify unrelated exceptions; I removed it. The saved attrs outcome ledger has zero final `collection_error` rows carrying that fallback note, so none of the published pair outcomes depends on it. The final runner now uses only whitelisted mutant signatures: generic attrs/TypeError signatures require a successful parent control, and pytest11-plugin failures require a changed-source frame. A later resume hit a transient `collect_git_sha` issue; the affected mutant was indexed with the pinned binary and the final resume completed. The attrs outcome log has 248 rows and 246 unique pairs: 184 final `complete` outcomes (173 killed, 11 survived), 41 `setup_failure`, and 21 `collection_error`. Two earlier `infrastructure_failure` rows were later reclassified for the same pairs. Setup and collection errors do not count as killed mutants. Backfill reports 87 unavailable historical revisions: 86 need `distutils` on Python 3.13, and one has invalid historical `extras_require` metadata.

The attrs run spans script revisions: initial run used clean `run_mutant.py`; resumed stages used the patched worktree version, and `backfill.py` was modified during the run. The post-run classifier audit and script hashes are in `test-width/run-attrs-n800/resume-execution-provenance.json`; keep this provenance caveat with any reuse of the attrs metric.

- committed, sanitized click artifacts: `test-width/run-click-n500/full-run.json`, `bootstrap_report.json`, `dry-run.json`, `measurement-provenance.json`, and `click-full-run.txt`
- committed, sanitized attrs artifacts: `test-width/run-attrs-n800/full-run.json`, `bootstrap_report.json`, `dry-run.json`, `measurement-provenance.json`, `resume-execution-provenance.json`, and `attrs-full-run.txt`
- original local artifacts remain in `.cache/measurements/week7/test-width/` and are the direct inputs to the copied reports.
- attrs backfill and mutation state remain local: `.cache/measurements/week7/test-width/repos-retry2/attrs/.testpilot/bootstrap/state.json`, `mutate_outcomes.jsonl`
- final local verification: `PYTHONPATH=scripts .cache/week7-train-env/bin/python -m pytest scripts/mutate -q --durations=20` → `267 passed`; `.cache/week7-train-env/bin/python -m pytest python/testpilot-train -q --durations=10` → `55 passed`; `ruff check --select E4,E7,E9,F scripts/` → all checks passed; targeted `ruff format --check` on the three newly authored/refactored files → all formatted; `git diff --check` → clean.

No full-model claim is made here; real PR intermediate pairs must be evaluated separately.
