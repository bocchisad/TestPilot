# TestPilot — Week 6 Bootstrap (cold-start model)

**Date:** 2026-11-14  
**Base:** `main` @ `6ee74eb` (PR #7 Week 5 merged)  
**Scope:** One-command offline bootstrap for a new repo with little/no historical failures: real backfill + mutation labels + train + quality gate + explicit `ranker=model|cov_lines`. Week-5 tails (build.rs SHA, `non_py_source` denylist).  
**Non-goals:** Online learning; pushing mutant refs upstream; GPU; changing the 29 features; replacing Week-5 safe mode; PyPI/crates.io releases (W8); multi-repo federated train in the CLI (LORO remains a measurement).

**Amendments R1–R11** (approved 2026-11-14) are incorporated below.

## Goal

A developer clones a green-history pytest project, runs **`testpilot bootstrap`**, and gets either:

1. a trained model that **beats `cov_lines` on the synthetic TEST slice** (cluster-paired CI over C), usable by `select`, **or**
2. an honest **`verdict=use_baseline`** so `select` ranks by **`cov_lines`** (never a silent weak model).

Must work **offline**, on a **weak machine**, and **resume** after Ctrl-C.

---

## Locked decisions (brainstorm)

| Topic | Decision |
|-------|----------|
| Orchestrator | **Rust** `testpilot bootstrap` shells out to existing Python (`backfill.py`, `mutate_bootstrap.py`, `testpilot-train`) |
| Steps | Fixed pipeline §2; each step idempotent + resumable via state file |
| Default scale | **N=200, K=4** (equal mutant budget to 160×5, more C for cluster CI — §3); overridable |
| Quality gate (R1) | On **synthetic TEST** only (C held out from early stopping / HP). Val metrics → report only. Accept ⇔ `ci_low ≥ 0` ∧ `data_sufficient` on that test slice |
| Paired CI (R2) | **Cluster bootstrap by C** (`base_change`); report `n_C` and `n_mutants` for val and test |
| Select fallback | Explicit JSON `ranker: "model" \| "cov_lines"`; plugin/Action print it |
| Third measure repo | **attrs** (§3.3); report timeout-mutant share (R11) |
| Parallel mutants | `--jobs J`: up to J worktrees/pytest; **serialize all SQLite writes** (single DB + queue) |
| Dry-run | Print plan + time estimate from calibrated priors (§3.2); no DB/git/model writes |
| Determinism (R3) | Two-level — see §5.2; **no** promise that two full bootstraps share `model_sha256` |
| Resume fingerprint (R4) | `state.json` stores input fingerprint; mismatch → refuse resume, hint `--force` |
| Clean tree (R5) | `init` appends `.testpilot/` to `.git/info/exclude` only |
| Backfill set (R6) | Default `B = sampled_C ∪ last 20 first-parent commits`; reuse existing real run for C |
| Dump done (R8) | Fingerprint DB (`max(run_id)`, counts real/synthetic), not mtime |
| Startup cleanup (R9) | `git worktree prune` + delete stale `.testpilot/bootstrap/wt-*` |
| Selection model hash (R10) | When `ranker=cov_lines`, `test_run_selection.model_sha256 = sha256(model.json)` |
| Verdict storage | Fields inside `model.json` under `bootstrap` (+ `report.json`) |
| Baseline-only model dir | `model.json` with `ranker=cov_lines` and **no** `model.txt` is valid for select |
| Time-budget mid-mutate | Finish current mutant, then stop; `interrupted` + partial pairs |
| Action model-dir | Stays explicit (W5); README shows bootstrap → Action wiring |
| LORO | Measurement-only in W6 (T6) |

---

## 1. Architecture

```text
testpilot bootstrap
  │
  ├─ state: .testpilot/bootstrap/state.json   (+ report.md / report.json)
  │
  ├─ 0 cleanup            → worktree prune + remove stale wt-*
  ├─ 1 init               → DB v7+; .git/info/exclude += .testpilot/
  ├─ 2 collect_git        → Store commits/file_changes
  ├─ 3 backfill           → real pytest --testpilot (dirty=0); reuse C runs
  ├─ 4 mutate             → refs/testpilot/mutants/** + synthetic=1 junit
  ├─ 5 features_dump      → pairs.parquet (+ --include-mutants)
  ├─ 6 train              → model dir + train report.json
  └─ 7 gate + install     → verdict on synth TEST; ranker in model.json;
                            select reads ranker
```

Python stays the owner of mutate/train math; Rust owns CLI, state machine, timeouts, job fan-out, cleanup, and select ranking mode.

---

## 2. CLI `testpilot bootstrap`

### 2.1 Surface

```text
testpilot bootstrap
  --repo PATH                    # default: cwd git toplevel
  --db PATH                      # default: <repo>/.testpilot/db.sqlite
  --out DIR                      # model + reports; default <repo>/.testpilot/model
  --max-commits N                # mutate sample size of C (default 200)
  --mutants-per-commit K         # default 4
  --backfill-commits B           # override default backfill set size/policy (R6)
  --time-budget SECONDS          # wall clock for whole bootstrap; soft stop between steps
  --jobs J                       # mutant pytest concurrency (default 1; max 4)
  --seed S                       # default 42
  --resume                       # continue from state.json (default on if state exists)
  --no-resume                    # ignore state; refuse if out dir dirty unless --force
  --force                        # allow restart over existing artifacts / fingerprint mismatch
  --dry-run                      # plan + estimates only
  [--python PATH]                # forwarded to backfill/mutate
  [--plugin PATH]                # pytest-testpilot path
  [--train-threads T]            # default 1; >1 warns non-determinism for level-(a) hash
```

Help text: bootstrap does not disable select `--safe`.

### 2.2 Startup (R9)

Before any step:

1. `git -C repo worktree prune`  
2. Delete any leftover `.testpilot/bootstrap/wt-*` directories (stale from crash)

### 2.3 `state.json` fingerprint (R4)

On first start, write:

```json
{
  "fingerprint": {
    "head_sha": "...",
    "N": 200,
    "K": 4,
    "seed": 42,
    "B_policy": "sampled_C_union_last_20_fp",
    "B_override": null,
    "bin_version": "0.1.0 (<gitsha>)",
    "schema_version": 7,
    "schema_sql_sha256": "<hash of schema.sql or SCHEMA_SQL bytes>"
  },
  "steps": { "...": "..." }
}
```

**Resume:** if state exists and fingerprint ≠ current inputs → **refuse** with message to use `--force` (or change flags to match).  
**Test:** change N or HEAD between runs → resume errors; `--force` restarts.

### 2.4 Pipeline steps (idempotency)

State file: `<repo>/.testpilot/bootstrap/state.json`.

| Step id | Done when | Artifacts / checks |
|---------|-----------|-------------------|
| `cleanup` | prune + stale wt removed | always re-run (cheap) |
| `init` | DB SCHEMA_VERSION≥7; `.testpilot/` in `.git/info/exclude` (R5) | `testpilot init` if missing; **never** write project `.gitignore` |
| `collect_git` | `commits` count ≥1 and HEAD indexed | `testpilot collect git` (incremental) |
| `backfill` | every SHA in planned backfill set has a clean real run **or** skipped with reason / time-budget | markers `state.backfill.done_shas[]`; **reuse** existing `synthetic=0 partial=0` run for C (R6) — do not pytest twice |
| `mutate` | `completed_pairs` covers all planned `(C,i)` attempts (incl. skip/timeout) **or** time-budget | pair key `(C_sha, i)`; timeout count in report |
| `features_dump` | parquet exists and **DB fingerprint** matches state (R8) | fingerprint = `(max_run_id, n_real_runs, n_synthetic_runs)` (and optionally row count); **not** mtime |
| `train` | `model.txt` and/or baseline-only `model.json` exist; train metrics recorded | `testpilot-train train …` |
| `gate` | `verdict` ∈ {`use_model`,`use_baseline`} written from **synth TEST** (R1) | cluster-paired CI by C (R2) |
| `finalize` | `report.md` + `report.json` written | install/pointer under `--out` |

**Resume:** load state if fingerprint matches; skip `done` steps; continue mid-step markers.  
**Ctrl-C:** flush `status=interrupted`; remove `wt-*`; exit nonzero.  
**Time-budget mid-mutate:** finish current mutant, then stop.  
**Mutant idempotency:** finished `(C,i)` not re-committed; existing ref with same tree/SHA OK.

### 2.5 Backfill set (R6)

Default planned SHA set:

```text
B_set = sample_commits(N, seed)   ∪   last 20 first-parent commits from HEAD
```

- If C already has a clean real pytest/junit run (`synthetic=0`, `partial=0`, `finished_at` set) → **reuse**; do not re-run.  
- `--backfill-commits B` **overrides** the default policy (exact semantics: attempt at most B first-parent SHAs **plus** still ensure sampled C are covered if missing — document in CLI help as “override cap/policy; sampled C always satisfied via reuse-or-run”).  
  **Locked interpretation:** `--backfill-commits B` replaces the “last 20” limb with “last B first-parent”, and still unions sampled C.

### 2.6 `--dry-run`

Prints without writing:

- fingerprint that would be stored  
- `|B_set|`, N×K, jobs  
- time estimate using §3.2 priors (and `.testpilot/bootstrap/priors.json` if present from a prior run)  
- whether resume would be allowed

### 2.7 Resources (weak machine)

| Knob | Default | Notes |
|------|---------|--------|
| `--jobs` | 1 | Cap recommended 2 for ≤8 GB RAM; hard max 4 |
| Mutant timeout | M5 `max(60, 3×C_duration)` | unchanged |
| Backfill | sequential | one pytest at a time |
| Train threads | 1 | `OMP_NUM_THREADS` / `--train-threads` |
| Disk | `.testpilot/bootstrap/wt-<id>`; deleted on success and signal | |

Single SQLite DB + write queue for all mutant ingests.

### 2.8 Clean-tree vs `.testpilot/` (R5)

`testpilot init` continues to append `.testpilot/` to **`.git/info/exclude`** only (not `.gitignore`).  
Backfill/mutate “clean tree” checks use `git status` and therefore **must not** see `.testpilot/` as dirty.  
**Test:** after init, create DB under `.testpilot/`; `git status --porcelain` empty; backfill dirty probe passes.

---

## 3. Mutation scale, time, third repo

### 3.1 Evidence and CI width via \(n_C\) (R2)

**Source (80×5 click+jinja mutant train):** in-repo snapshot [`docs/measurements/mut2-80x5-click-jinja.json`](../measurements/mut2-80x5-click-jinja.json) (copied from the Week 4/5 `/tmp/tp-model-mut2/report.json` retrain; do not depend on `/tmp` at runtime).

| Field | Value |
|-------|-------|
| `diagnostics.mutant_test_new_fail_changes` | 40 (= \(n_{\text{mutants}}\) in test with new_fail) |
| `diagnostics.mutant_test_distinct_base_change` | **17** (= \(n_C\) in test) |
| `paired_delta_ci_test` vs cov_lines | mean ≈ 0.090, CI95 **[−0.003, 0.239]** (width ≈ 0.24), `n=40` |

Cluster bootstrap **by C** is the correct uncertainty for gate/reporting; width scales ≈ \(1/\sqrt{n_C}\), not \(1/\sqrt{n_{\text{mutants}}}\).

Empirical fraction: \(n_C^{\text{test}} / N \approx 17/80 \approx 0.21\) (order-of-magnitude prior for presets).

| Preset | N×K | Exp. \(n_C^{\text{test}}\) | Width vs today (\(n_C=17\)) |
|--------|-----|----------------------------|------------------------------|
| current 80×5 | 400 | ~17 | 1.00× |
| 110×5 | 550 | ~23 | ~0.86× |
| 160×5 | 800 | ~34 | ~0.71× |
| **200×4** | **800** | **~42** | **~0.64×** |
| 200×5 | 1000 | ~42 | ~0.64× (more mutants/C, same \(n_C\)) |

**Equal mutant budget 800:** **200×4 vs 160×5** — prefer **200×4** (more C → tighter cluster CI; slightly fewer mutants per C).  
**Default: N=200, K=4.** Document 160×5 as alternate “more kills per C” preset; 110×5 as faster/weaker.

Report **must** print for val and test: `n_C`, `n_mutants` (changes with label), and cluster-paired CI.

### 3.2 Time priors (R7) — calibrated from 80×5

**Sources (in-repo only):**

1. Scale/outcomes: [`docs/measurements/mut2-80x5-click-jinja.json`](../measurements/mut2-80x5-click-jinja.json).  
2. Per-mutant wall prior: Week-3 design [`2026-11-11-testpilot-mutation-bootstrap-design.md`](2026-11-11-testpilot-mutation-bootstrap-design.md) §5.1 — click ~**10–13 s/mutant** without coverage. Use **12 s** click, **8 s** jinja as mutate hats until a bootstrap run writes `.testpilot/bootstrap/priors.json`.  
3. Default dry-run hats also live in [`docs/measurements/bootstrap_time_priors.json`](../measurements/bootstrap_time_priors.json) (embedded/loaded by bootstrap; never `/tmp`).

**Backfill is likely the dominant wall cost** (C runs need coverage for features; ~200 C can exceed mutate). The **1.5–3 min/commit** figure is an **unmeasured placeholder** — mark it as such in dry-run and replace after T6 measurements.

Linear scale from **80×5 = 400** mutants:

| Step | Prior (click) | Prior (jinja) | Derivation |
|------|---------------|---------------|------------|
| collect git | 1–3 min | 1–2 min | order-of-magnitude |
| backfill (\|B_set\|≈N+20) | **unmeasured** placeholder ~1.5–3 min/commit × SHAs needing run | **unmeasured** ~0.8–2 min/commit | dry-run prints **backfill on its own line** with `unmeasured=true` until T6 fills real `t_backfill_s_per_commit` |
| mutate 400 @ jobs=1 | 400×12 s ≈ **1.3 h** | 400×8 s ≈ **0.9 h** | §5.1 × 80×5 |
| mutate 800 @ jobs=1 | **~2.7 h** | **~1.8 h** | ×2 vs 400 |
| mutate 800 @ jobs=2 | **~1.4 h** | **~1.0 h** | /jobs (DB queue overhead ignored in hat) |
| dump + train | 10–30 min | 5–15 min | order-of-magnitude |

After first successful bootstrap on a repo, write `.testpilot/bootstrap/priors.json` (`t_mutant_s`, `t_backfill_s_per_commit`, …) for subsequent dry-runs. **T6:** measure real backfill seconds/commit on click/jinja/attrs.

### 3.3 Third measurement repo (R11)

| Repo | Why | Risk |
|------|-----|------|
| **attrs** | Pure Python, pytest, size between jinja and click | **hypothesis** / slow tests → elevated **timeout** mutants |
| httpx | Modern large suite | network extras |
| Flask | pallets-adjacent | heavier plugin surface |

**Choose: attrs.** In measurement report, always include **`n_timeout` / `timeout_rate`** for mutate. Not in CI.

---

## 4. Quality gate and `ranker`

### 4.1 Gate (R1, R2)

**Eval slice:** synthetic **TEST** only (changes whose C / `base_change` are in the test partition).  
Val (incl. synthetic val) is **report-only** — used for early stopping / HP during train, **not** for accept/reject.

```text
accept  ⇔  cluster_paired_delta_ci(
              model, cov_lines, recall@10%,
              cluster_id = C / base_change
            ).ci_low ≥ 0
         ∧  data_sufficient on that TEST slice
            (M3: ≥20 kill mutants ∧ ≥10 distinct C)
```

If after filters **eligible C < N**, report **`n_eligible`** and **actual N used**; still sample/use what exists.  
If test-slice **\(n_C < 10\)** → **`use_baseline`** with reason `insufficient_test_C` (honest; do not claim a win).

Cold start does **not** require real `new_fail`.

| Verdict | `model.json` / report | `select` behavior |
|---------|----------------------|-------------------|
| `use_model` | `bootstrap.verdict=use_model`, `ranker=model` | LightGBM raw scores |
| `use_baseline` | `bootstrap.verdict=use_baseline`, `ranker=cov_lines`, reason | Rank by `cov_lines` then `cov_func` then `test_id`; safe force/fallback still apply |

**Never silent:** stderr + report + select JSON show `ranker` and `bootstrap.verdict` when present.

### 4.2 Select JSON (additive)

```json
{
  "ranker": "model",
  "bootstrap": {
    "verdict": "use_model",
    "gate": "synthetic_test_vs_cov_lines_cluster_C"
  }
}
```

Legacy model without fields → `ranker=model`.

### 4.3 `cov_lines` ranker + selection hash (R10)

- Ordering lock: `cov_lines` ↓, `cov_func` ↓, `test_id` ↑.  
- Booster optional: baseline-only dir (`model.json`, no `model.txt`) is valid.  
- When recording `test_run_selection` with `ranker=cov_lines`:  
  **`model_sha256 = sha256(contents of model.json)`** (canonical file bytes).  
  When `ranker=model`: keep existing booster/model artifact hash behavior.

### 4.4 Report metrics block

For **val** and **test** (synthetic and real slices as available):

- `n_C`, `n_mutants`  
- recall@10% mean + CI  
- paired Δ vs cov_lines mean + **cluster** CI  

Gate decision cites **test** numbers only.

---

## 5. Determinism and reporting

### 5.1 Seeds

- `--seed S` (default 42).  
- Derived: `seed_mutate = S ^ 0x4D55`, `seed_train = S ^ 0x5452`.  
- Mutant `(C,i)` as Week-3.

### 5.2 Two-level determinism (R3)

**(a) Fixed DB → identical dump + model (CI):**  
Given an already-populated DB (real+synthetic frozen), two runs of `features dump` + `train` with `--train-threads 1` produce **byte-identical parquet** (existing sort) and **identical `model_sha256`**.  
**CI test** on fixture DB.

**(b) Two full bootstraps (measurement, not CI hash lock):**  
Same seed/flags/repo tip → **identical mutant commit SHAs** and **identical kill/survive labels** for all non-timeout mutants, **excluding tests marked flaky** (e.g. click `less` suite flakiness must not fail the equality check).  
**Timeouts may differ** (machine load); report `n_timeout` and list timeout `(C,i)`.  
Metrics (recall, CI) compared within documented tolerance — **not** required equal.  
**Do not promise** identical `model_sha256` across two full bootstraps (timeouts / flaky env / wall clock).

### 5.3 `report.md` / `report.json`

Must include:

- fingerprint; step timings; resume count; `n_timeout`  
- backfill: planned set size, reused vs newly run; dry-run had shown backfill estimate on its **own line**  
- mutate: **`n_eligible`**, actual N, attempted, invalid, timeout, killed, kill rate by operator  
- train + gate: val/test `n_C`, `n_mutants`, recalls, cluster-paired Δ; **verdict from test** (incl. `insufficient_test_C` when \(n_C<10\))  
- `model_sha256` (of booster and/or `model.json` per ranker)  
- machine: jobs, seed, train threads  

Golden: **JSON shape** only.

---

## 6. Week-5 tails (in scope)

### 6.1 `build.rs` git SHA (worktree-safe)

1. `git rev-parse --git-dir` and `--git-common-dir`.  
2. `cargo:rerun-if-changed` on `$GIT_DIR/HEAD`, resolved branch ref file, `$GIT_COMMON_DIR/packed-refs`.  
3. Embed short HEAD; outside git → **`unknown`**, build succeeds.  
4. Test: SHA updates after commit / worktree layout.

### 6.2 `non_py_source` denylist

Top-level prefixes **never** source packages:

```text
examples/, example/, docs_src/, benchmarks/, benchmark/,
tutorial/, tutorials/, demo/, demos/, samples/, sample/
```

(plus `docs/`, `tests/`, `.github/`, dotdirs, root files).

**Tests:** `examples/.../index.html` → no `non_py_source`; `src/pkg/templates/x.html` → yes; `.pre-commit-config.yaml` → no fallback.

---

## 7. Tests

### 7.1 Unit / integration (CI)

| Area | Tests |
|------|-------|
| Fingerprint (R4) | mismatch refuses resume; `--force` OK |
| Exclude (R5) | `.testpilot/` not dirty after init |
| Dump done (R8) | fingerprint change invalidates done; mtime-only change does not |
| Cleanup (R9) | stale `wt-*` removed on start |
| State machine | resume skip; interrupt |
| Dry-run | no writes; uses priors |
| Mutate resume | interrupt + resume → same non-timeout SHAs |
| Gate | no signal → `use_baseline`; planted test-slice win → `use_model` |
| Select | `ranker=cov_lines` order; selection `model_sha256 = sha256(model.json)` (R10) |
| Determinism (a) | fixed DB → same dump hash + model sha @ threads=1 |
| build.rs / non_py | as §6 |

### 7.2 e2e (CI ≤2 min)

1. Full bootstrap tiny N/K → report + verdict.  
2. Interrupt mutate + `--resume` → same non-timeout mutant set.  
3. Engineered `use_baseline` → select `ranker=cov_lines`.  
4. Fingerprint mismatch → error without `--force`.  
5. Golden `report.json` schema.

### 7.3 Measurements (not CI)

click, jinja, **attrs**: step times; val/test `n_C`/`n_mutants`; cluster-paired Δ; LORO×3; level-(b) mutant SHA equality; **attrs timeout rate** (R11).  
Level-(a) covered in CI. Do **not** claim two full bootstraps share model sha.

---

## 8. Acceptance checklist (recheckable)

```bash
cargo build -p testpilot-cli --release
BIN=./target/release/testpilot

# Per repo R ∈ {click, jinja, attrs}:
/usr/bin/time -p $BIN bootstrap --repo .cache/repos/$R --out /tmp/tp-boot-$R \
  --max-commits 200 --mutants-per-commit 4 --jobs 2 --seed 42

# report.json: timings; test n_C/n_mutants; cluster CI; verdict; n_timeout
# Fixed-DB retrain (CI-like): identical model_sha256 @ train-threads=1
# Two full bootstraps: identical mutant SHAs except timeouts; metrics within tolerance
# LORO across 3 repos (scripted)
```

- [ ] Defaults N×K = **200×4**; §3.1 \(n_C\) widths cited from `docs/measurements/mut2-80x5-click-jinja.json`.  
- [ ] Gate on synth **TEST**; val report-only.  
- [ ] attrs measured with timeout share.  
- [ ] Fingerprint / exclude / dump fingerprint / wt cleanup tested.  
- [ ] `ranker=cov_lines` → selection hash = `sha256(model.json)`.  
- [ ] build.rs + examples denylist.  
- [ ] CI ≤2 min e2e; no click/jinja/attrs download.

---

## File map

| Path | Role |
|------|------|
| `crates/testpilot-cli/src/main.rs` | `bootstrap` subcommand |
| `crates/testpilot-core/src/bootstrap/` | state, fingerprint, cleanup, estimates |
| `crates/testpilot-cli/build.rs` | worktree-safe SHA |
| `crates/testpilot-core/src/select.rs` | `ranker=cov_lines`; denylist; selection hash (R10) |
| `scripts/mutate_bootstrap.py` / `mutate/*` | pair resume; jobs; timeout accounting |
| `scripts/backfill.py` | B_set; reuse C runs; exclude-aware dirty |
| `python/testpilot-train` | cluster CI by C; expose test-slice gate fields |
| `.testpilot/bootstrap/` | state, wt-*, priors, logs (via info/exclude) |
| `tests/fixtures/bootstrap_e2e/` | mini repo |
| `tests/e2e/test_bootstrap_e2e.sh` | CI ≤2 min |
| `README.md` | cold-start |

---

## Implementation plan

> No code until this spec is approved. One logical commit per task.

### T1 — Week-5 tails

- [ ] build.rs worktree-safe SHA.  
- [ ] `non_py_source` denylist + tests.  
- [ ] Commit: `Fix bootstrap prerequisites: worktree git SHA and non_py denylist.`

### T2 — State machine + fingerprint + cleanup

- [ ] `state.json` + fingerprint (R4); refuse mismatch.  
- [ ] Startup prune + stale wt (R9); init exclude (R5).  
- [ ] Dry-run priors (R7).  
- [ ] Commit: `Add testpilot bootstrap state machine, fingerprint, and dry-run.`

### T3 — collect + backfill (R6) + mutate + dump (R8)

- [ ] B_set = sampled C ∪ last 20 FP; reuse real C runs.  
- [ ] Mutate resume; jobs + DB queue; dump done via DB fingerprint.  
- [ ] Commit: `Wire bootstrap backfill, mutate, and fingerprint dump.`

### T4 — Train + TEST gate + ranker (R1/R2/R10)

- [ ] Gate on synth TEST cluster CI; val report-only.  
- [ ] `ranker=cov_lines`; selection `model_sha256=sha256(model.json)`.  
- [ ] Determinism (a) CI test.  
- [ ] Commit: `Gate bootstrap on synth test and add cov_lines ranker.`

### T5 — e2e + CI

- [ ] Fixture: full / resume / use_baseline / fingerprint refuse; ≤2 min.  
- [ ] Commit: `Add bootstrap e2e fixture and CI job.`

### T6 — Docs + measurements

- [ ] README; measure click/jinja/attrs (timeouts + **backfill s/commit**); LORO; level-(b) mutant SHAs + kill/survive **excluding flaky**.  
- [ ] Commit: `Document Week 6 bootstrap and record measurements.`

---

## Open questions

| # | Status |
|---|--------|
| 1 Backfill vs N | **Superseded by R6** |
| 2 Gate ε | **Closed:** `ci_low ≥ 0` only, on synth **TEST** with cluster-by-C (R1/R2) |
| 3 Verdict location | **Accepted:** `model.json` `bootstrap` + report |
| 4 Baseline-only dir | **Accepted:** no `model.txt` OK |
| 5 Parallel DB | **Accepted:** single DB + queue |
| 6 Third repo | **Accepted:** attrs (+ R11 timeout reporting) |
| 7 Default N×K | **Closed:** default **200×4**; 160×5 alternate at same NK |
| 8 Time-budget | **Accepted:** finish current mutant |
| 9 Action model-dir | **Accepted:** explicit |
| 10 LORO in CLI | **Accepted:** measurements only |

---

## Self-review

- Gate is TEST-only; val cannot accept a model.  
- CI width story is \(n_C\)-based; 200×4 preferred over 160×5 at equal NK.  
- Determinism promises match R3 (no full-bootstrap sha lock).  
- Fingerprint, exclude, dump fingerprint, wt cleanup, selection hash locked.  
- Priors cite `docs/measurements/mut2-80x5-click-jinja.json` + `bootstrap_time_priors.json` + W3 §5.1; backfill marked unmeasured until T6.
