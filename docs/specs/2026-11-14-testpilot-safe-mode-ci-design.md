# TestPilot — Week 5 Safe Mode + Plugin Select + GitHub Action

**Date:** 2026-11-14  
**Base:** `main` @ `55e0cfe` (PR #2–#6 merged; select ready; mutant-trained model, `useful_on_real_val=null`)  
**Scope:** Rust `select --safe` (default on); pytest-plugin integration; composite GitHub Action; DB schema **v7** (`test_run_selection`); in-repo e2e fixture; PR measurements on click/jinja.  
**Non-goals (later weeks):** SHAP / explanations (W8); training in CI; PyPI / crates.io releases (W8 — then Action may download Releases with cargo fallback); bootstrap on new repos (W6); online learning.

## Goal

Ship a **fail-safe** path from “ranked subset” to “what CI actually runs”: safe-mode guardrails in Rust so a synth-trained model cannot silently miss regressions; a pytest plugin that calls `testpilot select`, deselects/orders items, and records selection for Week 7 evaluation; a composite Action that wires bin + DB cache + pytest for PRs while keeping full runs on `main`.

## Locked decisions (brainstorm)

| Topic | Decision |
|-------|----------|
| Safe default | **A:** CLI `--safe` **on by default**; `--no-safe` to disable. Plugin/Action same (`--testpilot-no-safe` → `--no-safe`). Not gated on `useful_on_val` (avoids silent behavior change after retrain). |
| Safe placement | **All safe logic in Rust `select`**. Plugin is thin orchestrator. |
| Visibility | JSON + plugin/Action summary always show `safe.enabled` (cannot hide `--no-safe`). |
| Week-4 parity | `scripts/bench_select.py` and dump/select parity tests pass **`--no-safe`**. Safe latency/size measured separately. |
| DB v7 | Table **`test_run_selection`** (not a column on `test_results`). |
| Action binary | **Cargo build + rust-cache** for W5; prefer input `testpilot-bin` / env `TESTPILOT_BIN` if present. Releases download = W8. |
| Snapshot / bad model | **Hard error** from select (nonzero exit). **Not** `fallback_all`. Plugin fail-opens to all tests. |
| Partial runs (P1) | `test_runs.partial`: select (not shadow, not fallback_all, not plugin_error) → `partial=1`. History / base_run / recent_fail use **only** `partial=0`. |
| Plugin timeout (P7) | `--testpilot-select-timeout` default **10s**; sleep-11 test uses default. |
| Auto-index (P6) | `--testpilot-auto-index` default **off**; without index, local select always fail-opens. |

**Amendments P1–P8** (approved 2026-11-14) are incorporated below.

---

## 1. Architecture

```text
                    ┌─────────────────────────────────────────┐
  pytest / Action  │  pytest-testpilot                       │
                    │  --testpilot-select → spawn bin         │
                    │  parse JSON → modifyitems / shadow      │
                    │  write test_runs + test_run_selection   │
                    └──────────────────┬──────────────────────┘
                                       │ testpilot select --format json
                                       ▼
                    ┌─────────────────────────────────────────┐
                    │  testpilot-core::select                 │
                    │  candidates → score → budget            │
                    │  + force (new/touched) + safe extras    │
                    │  or fallback_all (config/size gates)    │
                    └─────────────────────────────────────────┘
```

| Path | Role |
|------|------|
| `crates/testpilot-core/src/select.rs` (+ safe helpers) | Safe rules, JSON `safe` block, counters |
| `crates/testpilot-cli` | `--safe` / `--no-safe`, thresholds flags |
| `crates/testpilot-core/schema.sql` + store migrate | **SCHEMA_VERSION = 7**, `test_run_selection` |
| `python/pytest-testpilot/` | select/shadow/xdist/timeout/DB writes |
| `.github/actions/testpilot/` | composite Action |
| `docs/` example workflow | consumer template |
| `tests/fixtures/safe_e2e/` (or similar) | mini in-repo repo for CI e2e |
| `scripts/bench_select.py` | keep `--no-safe`; optional safe size script |

---

## 2. Safe mode (Rust `select`)

### 2.1 CLI

```text
testpilot select … [--safe | --no-safe]
  [--safe-recent-k K]              # default 5
  [--safe-include-covered]         # default off
  [--safe-src-frac-threshold X]    # default 0.30 (30% of .py src files touched)
  [--safe-min-candidates M]        # default 50
  [--safe-non-py-source | --no-safe-non-py-source]  # default on
  [--safe-non-py-allowlist GLOB[,GLOB…]]            # extend docs allowlist
```

- **Default: `--safe` on.** `--no-safe` disables all safe additions and `fallback_all` gates (Week-4 behavior + `safe.enabled=false`).
- Plugin: `--testpilot-no-safe` passes `--no-safe`. Action input `safe: false` same.

### 2.2 Selection order and budget

1. Build candidate set (Week 4: base run present ∪ new defs; A1/A2).
2. Score all candidates.
3. **Force ∪ safe-added** set (always included, even over budget):
   - A1 force: `new_or_changed` (unchanged semantics).
   - Safe (if enabled): `recent_fail`, `flaky`, and if `--safe-include-covered`: `covered` (`cov_lines>0` or `cov_func>0` on change features).
4. Fill remaining slots with **ranked** tests by (score↓, test_id↑) up to budget.
5. If `fallback_all`: selected = **all candidates**, every row `reason=fallback_all` (select still exits 0).

**`--time-budget`:** duration accounting applies **only to ranked fillers**. Force + safe-added are **not** charged against the time budget (stated explicitly in CLI help + README). Count/frac budgets: force+safe may exceed N / ceil(F·n); ranked fills until cap.

**JSON counters (always present):**

| Field | Meaning |
|-------|---------|
| `n_candidates` | candidate universe |
| `n_selected` | output size |
| `n_forced` | A1 `new_or_changed` count |
| `n_safe_added` | safe extras not already forced (0 if `--no-safe` or fallback_all) |
| `n_ranked` | selected with `reason=ranked` |

### 2.3 `recent_fail` / `flaky` (P2)

- Eligible runs: **`synthetic=0` AND `partial=0` AND finished** (`finished_at IS NOT NULL` — exclude aborted). **Runs that contain failures are included** (they are still “full” sessions).
- Restricted to commits **at or before `base`** (same ancestry / ordering as `base_run` / Week-4 leakage rules).
- Take the last **K** such runs (`--safe-recent-k`, default 5).
- Union: tests with `outcome ∈ {fail,error}` on those runs → reason `recent_fail`.
- Known flaky: `flaky=1` on any of those K runs → reason `flaky`.
- **Tests:** (1) failing run at `head` (after `base`) must **not** affect `recent_fail`; (2) a completed real run **with failures** on `base~1` **does** put those tests into `recent_fail`.

### 2.4 `covered` (optional)

- Requires change feature rows already computed for candidates.
- Include if `cov_lines > 0` OR `cov_func > 0`.
- Default **off** (`--safe-include-covered` to enable). PR measurements: |selected| with/without on click & jinja.

### 2.5 `fallback_all` reasons (select succeeds)

Only when select can score normally. Each reason string stable for tests/summary:

| Reason id | Condition |
|-----------|-----------|
| `config_surface` | Diff touches any path matching: `**/conftest.py`, `pytest.ini`, `pyproject.toml` (any hunk — whole file, not only `[tool.pytest]`), `setup.cfg`, `requirements*.txt`, `requirements*.in`, `poetry.lock`, `uv.lock`, `Pipfile.lock`, `pdm.lock`, or `.github/workflows/**`. (`Cargo.lock` is **not** included — Rust lockfile churn must not force full Python suite.) |
| `src_frac` | Changed non-test `.py` paths / all non-test `.py` paths at head > **X** (`--safe-src-frac-threshold`, default 0.30) |
| `few_candidates` | `n_candidates < M` (`--safe-min-candidates`, default 50) |
| `non_py_source` (P4) | Diff touches a file **inside a source package** (not a test path), extension **≠ `.py`**, and path is **not** on the docs allowlist. Default allowlist: `*.md`, `*.rst`, `docs/**`, `CHANGES*`, `LICENSE*`. Extensible via `--safe-non-py-allowlist`. Toggle: `--safe-non-py-source` (default **on**) / `--no-safe-non-py-source`. Measure impact on jinja (templates). |

**Not** `fallback_all` (hard errors, nonzero exit):

| Condition | Behavior |
|-----------|----------|
| Missing feature snapshot at base | Error (existing A9 message) |
| Model validation failure | Error |
| Empty/invalid model dir | Error |

Plugin maps these to fail-open all tests.

### 2.6 JSON shape (extends Week 4)

```json
{
  "model_sha256": "...",
  "features_version": 2,
  "base": "...",
  "head": "...",
  "base_run_sha": "...",
  "n_candidates": 0,
  "n_selected": 0,
  "n_forced": 0,
  "n_safe_added": 0,
  "n_ranked": 0,
  "budget": { "kind": "frac|count|time", "value": 0.0 },
  "safe": {
    "enabled": true,
    "fallback_all": false,
    "reasons": [],
    "thresholds": {
      "recent_k": 5,
      "src_frac": 0.30,
      "min_candidates": 50,
      "include_covered": false,
      "non_py_source": true
    }
  },
  "tests": [
    {
      "test_id": "...",
      "score": 0.0,
      "rank": 1,
      "dur": 0,
      "reason": "ranked|new_or_changed|recent_fail|flaky|covered|fallback_all"
    }
  ]
}
```

- **`--no-safe` golden (P8):** exact match on **all Week-4 JSON fields**; the **only** allowed additions are keys `safe`, `n_safe_added`, `n_ranked` (with `safe.enabled=false`, empty `reasons`, `fallback_all=false`, safe counters 0). No fuzzy “≈”.
- Per-test `reason`: if multiple apply, precedence `fallback_all` > `new_or_changed` > `recent_fail` > `flaky` > `covered` > `ranked` (document in code).

### 2.7 Determinism & synthetic / partial (P1)

- Same inputs → same JSON (sort ties by `test_id`).
- Synthetic **and** `partial=1` runs excluded from recent_fail/flaky/coverage history, `base_run` / last-clean-run queries, and dump snapshot history (same gates as “real full runs”).
- **`test_runs.partial`:** see §3.8 / §3.9.

---

## 3. Pytest plugin

### 3.1 Options

```text
pytest --testpilot --testpilot-select
  (--testpilot-budget-frac F | --testpilot-max-tests N | --testpilot-time-budget S)
  [--testpilot-base REV]
  [--testpilot-model DIR]
  [--testpilot-no-safe]
  [--testpilot-order]
  [--testpilot-shadow]
  [--testpilot-auto-index]              # default off (P6)
  [--testpilot-select-timeout SECONDS]  # default 10 (P7)
```

Exactly one budget flag when select is on (mirror CLI).

### 3.2 Base resolution

1. If `--testpilot-base` set → use it.
2. Else try `merge-base(HEAD, origin/<default-branch>)` (detect default via `origin/HEAD` or `main`/`master`).
3. Else `HEAD~1`.

Print in terminal summary: `base=<sha> (merge-base|explicit|fallback HEAD~1)`.

### 3.3 Binary resolution

`TESTPILOT_BIN` → `PATH` (`testpilot`) → binary next to installed package/wheel if present.  
Invoke: `testpilot select --format json …` with timeout `--testpilot-select-timeout` (default **10s**).

**`--testpilot-auto-index` (P6, default off):** before select, run `collect git` + `features dump` enough to ensure a snapshot at `base`. If off and snapshot missing → select errors → fail-open. README: without an index, local select **always** fail-opens.

### 3.4 Collection / deselect

- Hook: `pytest_collection_modifyitems`.
- Match select `test_id` to nodeids; **bare** id matches all parametrized children.
- **Unknown to select** (collected item with no matching select id): **keep / run**, reason tracked as `unknown_to_select`, counted in summary. Never silently drop.
- Deselected → `config.hook.pytest_deselected`. **Do not write deselected tests into `test_results`** (not even as `skip`) (P1).
- `--testpilot-order`: reorder kept items by select rank (unknowns after ranked, stable by nodeid).

**xdist:** run select **once on controller**; workers receive the already-filtered/ordered item set (or equivalent gateway broadcast). Test with `-n 2`.

### 3.5 Fail-open (plugin_error)

On: missing binary, model error, snapshot error, nonzero exit, timeout (`--testpilot-select-timeout`, default 10s), JSON parse error → run **all** collected tests; warning; summary `fallback=plugin_error(<kind>)` where `kind ∈ {no_bin, model, snapshot, exit, timeout, json, …}`.  
Never select zero silently. Select failures do **not** fail the pytest session (strict flag remains for DB record path only).

**`plugin_error` + DB (P5):** `mode` would have been `select`, but **do not write any `test_run_selection` rows**; set `test_runs.meta.plugin_error=<kind>`; set **`partial=0`** (full suite ran).

**Timeout test:** fake bin that `sleep 11` with **default** 10s timeout → all tests run + `fallback=plugin_error(timeout)` in summary.

### 3.6 Shadow

- `--testpilot-shadow`: do **not** deselect; run all tests.
- Still call select; compute would-select set.
- `shadow_caught_fail` =  
  `| {new real fails} ∩ would_select | / | {new real fails} |`  
  where **new real fails** = tests with `fail|error` this run, **not** flaky, that did **not** fail on the resolved **base** clean real run.  
  If denominator 0 → store **`null`** (not 1.0).
- Persist in `test_runs.meta` JSON plus integer counters (`shadow_new_fails`, `shadow_caught`, …).

### 3.7 Terminal summary (required lines)

- `safe.enabled=true|false`
- `base=… (…)`
- selected / deselected counts
- `n_selected_ids` vs `n_collected_items`
- fallback (select `fallback_all` and/or `plugin_error`)
- safe/select reasons
- shadow metrics if shadow

### 3.8 DB v7 — `test_run_selection`

```sql
CREATE TABLE IF NOT EXISTS test_run_selection (
  run_id        INTEGER NOT NULL REFERENCES test_runs(id),
  test_pk       INTEGER NOT NULL REFERENCES tests(id),
  selected      INTEGER NOT NULL CHECK (selected IN (0,1)),
  reason        TEXT,                 -- select reason or unknown_to_select / null
  rank          INTEGER,
  score         REAL,
  budget_kind   TEXT NOT NULL,
  budget_value  REAL NOT NULL,
  model_sha256  TEXT NOT NULL,
  safe_enabled  INTEGER NOT NULL CHECK (safe_enabled IN (0,1)),
  mode          TEXT NOT NULL CHECK (mode IN ('select','shadow')),
  PRIMARY KEY (run_id, test_pk)       -- UNIQUE(run_id, test_pk)
);
CREATE INDEX IF NOT EXISTS idx_test_run_selection_run ON test_run_selection(run_id);
```

**Semantics:**

| Situation | Rows |
|-----------|------|
| No `--testpilot-select` | **No rows** (absence ≠ “deselected”) |
| `mode=select` | **One row per collected test**: `selected` 0/1; deselected have no outcome rows |
| `mode=shadow` | **One row per collected test**: `selected` 0/1 = would-select |
| Synthetic run | **Do not write** selection rows |

Also store `rank/score/budget_*/model_sha256/safe_enabled` so Week 7 can recompute recall under other budgets / identify the model.

`SCHEMA_VERSION = 7`. Migrate v6→v7 additive (CREATE TABLE + index + bump version). Sync Python schema copy via existing `scripts/sync_schema.py`.

### 3.9 `test_runs.partial` (P1)

```sql
-- v7 additive on test_runs:
partial INTEGER NOT NULL DEFAULT 0 CHECK (partial IN (0,1))
```

| Run kind | `partial` | `test_run_selection` |
|----------|-----------|----------------------|
| Full pytest without `--testpilot-select` | `0` | none |
| `--testpilot-select` success, **not** shadow, **not** select `fallback_all`, **not** `plugin_error` | **`1`** | all collected, selected 0/1 |
| `--testpilot-shadow` | `0` | all collected, selected 0/1 |
| Select `fallback_all` (ran all candidates / effectively full) | `0` | as select mode rows |
| `plugin_error` fail-open (ran all) | `0` | **no rows**; `meta.plugin_error` |

**Consumers that must ignore `partial=1`:** `base_run` / last-clean-run, features dump / snapshots history, `recent_fail` / `flaky` (P2), coverage-for-history paths (same as synthetic exclusion).

**Test:** after a `partial=1` select run is in the DB, a subsequent `select --no-safe` and `features dump` are **byte-identical** to the same commands on a DB without that select run.

---

## 4. GitHub Action

### 4.1 Layout

- Composite: `.github/actions/testpilot/action.yml`
- Example consumer workflow: `docs/examples/testpilot-ci.yml` (or under `docs/`)
- Our CI: e2e job using **in-repo mini fixture** (not click)

### 4.2 Binary resolution

1. Input `testpilot-bin` if path exists and is executable  
2. Else `TESTPILOT_BIN`  
3. Else `cargo build -p testpilot-cli --release` + `Swatinem/rust-cache`  

Job summary **must** print: resolved path, `testpilot --version`, and **git commit SHA of the tree used to build** (checkout SHA when building; or `unknown` if prebuilt bin without metadata — prefer embedding/`--version` showing crate version + note checkout SHA).

### 4.3 Inputs

| Input | Default | Notes |
|-------|---------|-------|
| `budget-frac` / `max-tests` / `time-budget` | — | Exactly one required for PR select path |
| `model-path` | required for select | **Not** stored in DB cache; summary prints `model_sha256` from select JSON |
| `safe` | `true` | |
| `shadow` | `false` | |
| `base` | empty | Empty → plugin merge-base logic |
| `testpilot-bin` | empty | Prefers existing binary |
| `db-path` | `.testpilot/db.sqlite` | |

### 4.4 Cache (P3)

- **DB only** (not the model).
- **Mandatory unique key:** `testpilot-db-<repo>-${{ github.sha }}-${{ github.run_attempt }}`.
- **`restore-keys`:** `testpilot-db-<repo>-` (prefix) so PRs hit latest main DB.
- **`save` only on `push` to default branch** (main), after the push pipeline below. PRs restore but do not save.

### 4.5 Fetch / base / snapshot

- `fetch-depth: 0` (or deep fetch + fetch base branch) so merge-base works.
- Summary: `base=… (merge-base|explicit|fallback HEAD~1)`.
- PR path: ensure snapshot at merge-base — if missing, `features dump` incrementally until base (or equivalent collect+dump). If still missing → select errors → plugin fail-open all tests.
- **e2e cold-cache case:** empty/missing DB → all tests run, job green, summary shows plugin_error/snapshot path.

### 4.6 Push vs PR (P3)

| Event | Behavior |
|-------|----------|
| Push to main | **(1)** Full pytest `--testpilot` **without** `--testpilot-select` → **(2)** `collect git` → **(3)** `features dump` → **(4)** **save** DB cache under the unique key |
| Pull request | `--testpilot-select` (+ safe default on); restore cache; do **not** save |

### 4.7 e2e (in-repo mini fixture)

Assert:

1. Injected regression **selected** under safe select.  
2. Shadow: `shadow_caught_fail == 1.0`.  
3. `safe=false` + tiny budget: regression **may be missed**; summary shows `safe.enabled=false`.  
4. Cold cache: all tests + green.

Click/jinja: **measurements only** (not CI e2e).

---

## 5. Tests & measurements

### 5.1 Rust

- Each `fallback_all` reason (incl. `non_py_source`) → dedicated test.  
- Missing snapshot / bad model → nonzero exit (not fallback).  
- `recent_fail`: head-after-base ignored; **failing completed run on `base~1` is included**.  
- Budget: safe extras beyond N; time-budget excludes safe durations.  
- **`--no-safe` JSON (P8):** exact Week-4 fields + exactly keys `safe`, `n_safe_added`, `n_ranked`.  
- Determinism; synthetic + **partial** isolation.  
- **T1 / P1 regression:** v6→v7 migrate; dump + `select --no-safe` goldens; **partial select run does not change** subsequent dump/select bytes.

### 5.2 Plugin

- Select OK → correct deselect/order; bare param expands; deselected absent from `test_results`.  
- unknown_to_select kept.  
- Fail-open paths including **timeout** (sleep-11, default 10s); `plugin_error` → no selection rows, `partial=0`, `meta.plugin_error`.  
- Shadow metrics; DB v7; no synthetic selection.  
- xdist `-n 2`.  
- `--testpilot-auto-index` off by default.

### 5.3 Measurements (PR body, with commands)

On click & jinja `HEAD~1..HEAD` (or merge-base..HEAD):

- `|selected|` for `--no-safe` / `--safe` / `--safe --safe-include-covered` / **`--safe` with non-py-source on jinja templates** at fixed budget (e.g. frac 0.1).  
- Plugin overhead: median of 5 of `(wall pytest --testpilot-select − wall pytest without select)`.  
- Bench script remains `--no-safe` for latency continuity with Week 4 (~215 ms click).

---

## 6. README

- Install: pip plugin + `testpilot` binary + model dir.  
- Safe **on by default** because production models may be mutant-trained (`useful_on_real_val=null`); always show `safe.enabled` in output.  
- `--no-safe` / `--testpilot-no-safe` for research / Week-4 parity.  
- **Without a local index / snapshot, select fail-opens** unless `--testpilot-auto-index` (default off).  
- Shadow for offline evaluation (Week 7).  
- Action usage; cache behavior; limitations.

---

## 7. Acceptance checklist

- [ ] Safe default on; JSON `safe.enabled` always present.  
- [ ] Fallback_all only for config/src_frac/few_candidates; snapshot/model = errors.  
- [ ] Plugin fail-open never silent-empty; timeout test green.  
- [ ] v7 selection table + v6 migrate; dump/select `--no-safe` goldens intact.  
- [ ] Action cache/restore-keys; model not in DB cache; summary bin+model hashes.  
- [ ] e2e mini-fixture (select / shadow / no-safe miss / cold cache).  
- [ ] CI: fmt, clippy `-D warnings`, cargo test, python jobs, oracles green.  
- [ ] PR quotes measurement commands + numbers for recheck.

---

## File map

| Path | Role |
|------|------|
| `crates/testpilot-core/src/select.rs` | Safe + JSON counters |
| `crates/testpilot-cli/src/main.rs` | Flags |
| `crates/testpilot-core/schema.sql` / `store.rs` | v7 |
| `python/pytest-testpilot/src/pytest_testpilot/plugin.py` | Select/shadow |
| `python/pytest-testpilot/src/pytest_testpilot/store.py` | Selection writes |
| `.github/actions/testpilot/` | Composite action |
| `.github/workflows/ci.yml` | e2e job |
| `tests/fixtures/safe_e2e/` | Mini repo |
| `scripts/bench_select.py` | `--no-safe` |
| `README.md` / `docs/examples/` | Docs |

---

## Implementation plan

> For implementers: TDD where practical; one logical commit per task; no code until this spec is approved.

### Task 1: Schema v7 + partial + Week-4 regression lock

- [ ] `test_run_selection` + `test_runs.partial` + `SCHEMA_VERSION=7`; v6→v7 migrate (Rust + Python).  
- [ ] Gate `base_run` / dump history / recent_fail on `partial=0` (and synthetic=0).  
- [ ] **Goldens (P8):** dump byte-identical; `select --no-safe` = exact W4 fields + exactly `safe`, `n_safe_added`, `n_ranked`.  
- [ ] **P1 test:** insert partial select run → dump/select unchanged vs without it.  
- [ ] `scripts/sync_schema.py --check` green.  
- [ ] Commit: `Add schema v7 selection table, partial runs, and --no-safe goldens.`

### Task 2: Safe mode in Rust select

- [ ] §2 incl. P2 recent_fail (full runs with fails), P4 `non_py_source`, thresholds JSON, counters.  
- [ ] Tests per fallback reason (incl. non_py); recent_fail include/exclude; `--no-safe` exact golden; bench `--no-safe`.  
- [ ] Commit: `Add select --safe with recent_fail, non_py_source, and safe JSON.`

### Task 3: Pytest plugin select + shadow + xdist

- [ ] Options incl. auto-index (off), select-timeout (10s); base merge-base; fail-open; unknown_to_select; order.  
- [ ] Selection writes; `partial` rules; plugin_error → no selection rows + meta; no deselected in `test_results`.  
- [ ] Tests: happy path, fail-open, **sleep-11 timeout**, param expand, shadow, `-n 2`, partial isolation.  
- [ ] Commit: `Integrate testpilot select into pytest plugin with shadow and fail-open.`

### Task 4: Composite Action + docs example

- [ ] Action: bin pref, cargo fallback; cache key `testpilot-db-<repo>-${{ github.sha }}-${{ github.run_attempt }}` + prefix restore-keys; **push:** pytest full → collect → dump → save; PR select; summary.  
- [ ] Example workflow under `docs/`.  
- [ ] Commit: `Add TestPilot composite GitHub Action and example workflow.`

### Task 5: In-repo e2e fixture + CI job

- [ ] Mini fixture with controllable regression.  
- [ ] CI: select hit, shadow 1.0, safe=false miss+flag, cold-cache all-green.  
- [ ] Commit: `Add safe-mode e2e fixture and CI job.`

### Task 6: Measurements + README + PR

- [ ] click/jinja sizes ±safe ±covered ±non_py (jinja); plugin overhead median×5.  
- [ ] README: safe default, auto-index off → fail-open, shadow, Action, limits.  
- [ ] PR body with numbers + commands.  
- [ ] Commit: `Document Week 5 safe mode, plugin select, and Action.`

---

## Open points resolved

| Topic | Decision |
|-------|----------|
| Safe CLI default | On (`--no-safe` to disable) |
| useful_on_val gating | Rejected (silent shift after retrain) |
| Snapshot / bad model | Error → plugin fail-open; not fallback_all |
| Time budget vs safe | Safe/force not charged |
| Selection storage | `test_run_selection` with rank/score/budget/model/safe/mode |
| Select mode rows | All collected, selected 0/1; deselected absent from `test_results` |
| Partial runs | `partial=1` for successful subset select; ignored by history |
| recent_fail | Last K finished real non-partial runs ≤base, **including failing runs** |
| non_py_source | fallback_all; default on; extensible allowlist |
| plugin_error DB | No selection rows; `meta.plugin_error`; `partial=0` |
| auto-index | Default off |
| select timeout | Configurable; default 10s |
| Action binary | Cargo + cache; prefer testpilot-bin / TESTPILOT_BIN |
| DB cache | Unique key per sha+attempt; prefix restore; save on main after dump |
| Model cache | Not with DB; print model_sha256 |
| e2e repo | In-repo mini fixture; click/jinja for benches only |
| T1 golden | Exact W4 fields + exactly `safe`/`n_safe_added`/`n_ranked` |
