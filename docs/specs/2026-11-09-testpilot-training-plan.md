# TestPilot Week 3 Training — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans`. Checkbox steps for tracking.

**Goal:** `python/testpilot-train` trains LightGBM on `new_fail`, evaluates tie-aware vs heuristics with paired bootstrap, emits week-4 artifacts + committed reference fixtures.

**Spec:** [`2026-11-09-testpilot-training-design.md`](2026-11-09-testpilot-training-design.md) (includes A1–A9).

---

## File map

| Path | Role |
|------|------|
| `python/testpilot-train/pyproject.toml` | pins + `testpilot-train` script |
| `…/schema.py` | schema load, hash, 29 names |
| `…/data.py` | parquet load, NaN gate, `new_fail`, train filter |
| `…/split.py` | chrono 70/15/15, LORO, asserts |
| `…/metrics.py` | tie-aware recall/APFD + bootstrap + paired delta |
| `…/baselines.py` | random, heuristics, `fail_per_day` |
| `…/model.py` | binary/lambdarank, fixed hparams, reference trainer |
| `…/synth.py` | synthetic parquet for leakage |
| `…/leakage.py` | shuffled / future canary on synth |
| `…/artifact.py` | model.txt/json, fixture writer |
| `…/report.py` | md+json + diagnostics |
| `…/cli.py` | train / evaluate / baselines |
| `tests/fixtures/reference_model/` | frozen week-4 contract |
| `.github/workflows/ci.yml` | train-oracle (build CLI separate; pytest &lt;60s) |

---

### Task 1: Scaffold + schema

- [ ] `pyproject.toml` with lightgbm, polars, pyarrow, numpy pinned
- [ ] `schema.py` + `tests/test_schema.py` (29 features, sha256)
- [ ] Commit: `Add testpilot-train scaffold and schema loader.`

### Task 2: Data + new_fail

- [ ] `conftest.py`: week-2 fixture → features dump parquet
- [ ] `data.py`: schema gate, NaN/inf reject, `add_new_fail()`, train-row filter (drop flaky, persistent, optional parent-missing handling per spec)
- [ ] Tests for new_fail / persistent exclusion
- [ ] Commit: `Derive new_fail labels and filter persistent failures.`

### Task 3: Split

- [ ] Chrono + LORO + commit_idx assert + n&lt;10 error
- [ ] Commit: `Add chronological and LORO splits.`

### Task 4: Tie-aware metrics + paired bootstrap + baselines

- [ ] Unit test: all scores equal → recall@k = k/n
- [ ] Borderline tie group expectation
- [ ] Paired bootstrap delta CI
- [ ] `fail_per_day` baseline
- [ ] Commit: `Add tie-aware metrics and paired bootstrap useful-gate.`

### Task 5: Model + reference fixtures

- [ ] Fixed hparams train; lambdarank group contiguity assert + truncation
- [ ] Objective select by val recall@10% new_fail
- [ ] Build `tests/fixtures/reference_model/` (50 rounds, depth asserts, sentinels, raw_score)
- [ ] Determinism test on fixture train
- [ ] Commit: `Train LightGBM candidates and commit reference model fixtures.`

### Task 6: Synth leakage + report + artifact + CLI

- [ ] `synth.py` ~200×200; leakage tests on synth only
- [ ] report diagnostics (persistent share, always-broken)
- [ ] CLI train/evaluate/baselines with A8 slice rules
- [ ] Commit: `CLI, reports, and synthetic leakage canaries.`

### Task 7: CI + README

- [ ] Job: build CLI step; pytest train-oracle &lt;60s
- [ ] README train section + v5 dump note already present
- [ ] Commit: `CI train-oracle and README for training.`

### Task 8: Manual real-data (PR numbers)

- [ ] Measure `backfill.py --last 5` on click (and jinja if present) with `/usr/bin/time -p`
- [ ] Extrapolate; run 250/400 as needed; train; paste measured numbers only

---

## Out of scope

Rust parser, `select`, SHAP, GPU, GitHub Action for PR selection.
