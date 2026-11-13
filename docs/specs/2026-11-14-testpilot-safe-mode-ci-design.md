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

### 2.3 `recent_fail` / `flaky`

- Only **non-synthetic** runs.
- Walk the same first-parent / clean-run ancestry used for `base_run_sha`, restricted to runs with `sha` **at or before `base`** (author/commit order consistent with Week-4 dump leakage rules).
- Take the last **K** such clean real runs (`--safe-recent-k`, default 5).
- Union: tests with `outcome ∈ {fail,error}` on those runs → reason `recent_fail`.
- Known flaky: `flaky=1` on any of those K runs (or existing flaky dictionary if already in store — prefer evidence from the K runs) → reason `flaky`.
- **Test:** a failing run recorded at `head` (or after `base`) must **not** change `recent_fail` set (Week-4-style leakage).

### 2.4 `covered` (optional)

- Requires change feature rows already computed for candidates.
- Include if `cov_lines > 0` OR `cov_func > 0`.
- Default **off** (`--safe-include-covered` to enable). PR measurements: |selected| with/without on click & jinja.

### 2.5 `fallback_all` reasons (select succeeds)

Only when select can score normally. Each reason string stable for tests/summary:

| Reason id | Condition |
|-----------|-----------|
| `config_surface` | Diff touches any of: `conftest.py`, `pytest.ini`, `pyproject.toml` (any change — treat as pytest-related; optionally require `[tool.pytest]` hunk if cheap), `setup.cfg`, `requirements*.txt` / `*.in`, lockfiles (`poetry.lock`, `uv.lock`, `Pipfile.lock`, `Cargo.lock` if present as project lock for Python env — document exact glob list in impl), `.github/workflows/**` |
| `src_frac` | Changed non-test `.py` paths / all non-test `.py` paths at head > **X** (`--safe-src-frac-threshold`, default 0.30) |
| `few_candidates` | `n_candidates < M` (`--safe-min-candidates`, default 50) |

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
      "include_covered": false
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

- `--no-safe`: same fields as Week 4 **plus** `safe.enabled=false`, empty `reasons`, zeros for safe counters, `fallback_all=false`. Deterministic byte-comparable for golden tests where applicable (allowlisted extra keys).
- Per-test `reason`: if multiple apply, precedence `fallback_all` > `new_or_changed` > `recent_fail` > `flaky` > `covered` > `ranked` (document in code).

### 2.7 Determinism & synthetic

- Same inputs → same JSON (sort ties by `test_id`).
- Synthetic runs excluded from recent_fail/flaky/coverage history queries and from candidate base_run (unchanged Week 4).

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
```

Exactly one budget flag when select is on (mirror CLI).

### 3.2 Base resolution

1. If `--testpilot-base` set → use it.
2. Else try `merge-base(HEAD, origin/<default-branch>)` (detect default via `origin/HEAD` or `main`/`master`).
3. Else `HEAD~1`.

Print in terminal summary: `base=<sha> (merge-base|explicit|fallback HEAD~1)`.

### 3.3 Binary resolution

`TESTPILOT_BIN` → `PATH` (`testpilot`) → binary next to installed package/wheel if present.  
Invoke: `testpilot select --format json …` with 10s timeout.

### 3.4 Collection / deselect

- Hook: `pytest_collection_modifyitems`.
- Match select `test_id` to nodeids; **bare** id matches all parametrized children.
- **Unknown to select** (collected item with no matching select id): **keep / run**, reason tracked as `unknown_to_select`, counted in summary. Never silently drop.
- Deselected → `config.hook.pytest_deselected`.
- `--testpilot-order`: reorder kept items by select rank (unknowns after ranked, stable by nodeid).

**xdist:** run select **once on controller**; workers receive the already-filtered/ordered item set (or equivalent gateway broadcast). Test with `-n 2`.

### 3.5 Fail-open (plugin_error)

On: missing binary, model error, snapshot error, nonzero exit, timeout (10s), JSON parse error → run **all** collected tests; warning; summary `fallback=plugin_error(<kind>)` where `kind ∈ {no_bin, model, snapshot, exit, timeout, json, …}`.  
Never select zero silently. Select failures do **not** fail the pytest session (strict flag remains for DB record path only).

**Timeout test:** fake bin that `sleep 11` → after 10s all tests run + `fallback=plugin_error(timeout)` in summary.

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

### 4.4 Cache

- **DB only** (not the model).
- `key`: `testpilot-db-<repo>-<hash>` optional unique; **`restore-keys`**: `testpilot-db-<repo>-` so PRs hit latest main DB.
- **`save` only on `push` to default branch** (main). PRs restore but do not save (avoid cache pollution / history skew).

### 4.5 Fetch / base / snapshot

- `fetch-depth: 0` (or deep fetch + fetch base branch) so merge-base works.
- Summary: `base=… (merge-base|explicit|fallback HEAD~1)`.
- PR path: ensure snapshot at merge-base — if missing, `features dump` incrementally until base (or equivalent collect+dump). If still missing → select errors → plugin fail-open all tests.
- **e2e cold-cache case:** empty/missing DB → all tests run, job green, summary shows plugin_error/snapshot path.

### 4.6 Push vs PR

| Event | Behavior |
|-------|----------|
| Push to main | Full pytest `--testpilot` **without** `--testpilot-select` (grow history; no selection rows) |
| Pull request | `--testpilot-select` (+ safe default on) |

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

- Each `fallback_all` reason → dedicated test.  
- Missing snapshot / bad model → nonzero exit (not fallback).  
- `recent_fail` leakage: head-after-base run ignored.  
- Budget: safe extras beyond N; time-budget excludes safe durations.  
- `--no-safe` JSON ≈ Week 4 + `safe.enabled=false`.  
- Determinism; synthetic isolation.  
- **T1 regression:** v6 DB migrates to v7; `features dump` and `select --no-safe` outputs **byte-identical** to Week-4 goldens (dump golden + select fixture where applicable).

### 5.2 Plugin

- Select OK → correct deselect/order; bare param expands.  
- unknown_to_select kept.  
- Fail-open paths including **timeout** (sleep-11 fake bin).  
- Shadow metrics; DB v7 writes; no synthetic selection.  
- xdist `-n 2`.

### 5.3 Measurements (PR body, with commands)

On click & jinja `HEAD~1..HEAD` (or merge-base..HEAD):

- `|selected|` for `--no-safe` / `--safe` / `--safe --safe-include-covered` at fixed budget (e.g. frac 0.1).  
- Plugin overhead: median of 5 of `(wall pytest --testpilot-select − wall pytest without select)`.  
- Bench script remains `--no-safe` for latency continuity with Week 4 (~215 ms click).

---

## 6. README

- Install: pip plugin + `testpilot` binary + model dir.  
- Safe **on by default** because production models may be mutant-trained (`useful_on_real_val=null`); always show `safe.enabled` in output.  
- `--no-safe` / `--testpilot-no-safe` for research / Week-4 parity.  
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

### Task 1: Schema v7 + Week-4 regression lock

- [ ] Add `test_run_selection` + `SCHEMA_VERSION=7`; v6→v7 migrate in Rust + Python store.  
- [ ] **Regression:** open a v6 fixture DB → migrate → v7; `features dump` and `testpilot select --no-safe` outputs **byte-identical** to Week-4 goldens (dump parquet / select JSON allowlisting only additive `safe.*` if `--no-safe` still emits the block — prefer exact match on dump; for select, assert Week-4 fields unchanged and `safe.enabled=false`).  
- [ ] `scripts/sync_schema.py --check` green.  
- [ ] Commit: `Add schema v7 test_run_selection and lock dump/select --no-safe goldens.`

### Task 2: Safe mode in Rust select

- [ ] Implement §2 (order, budgets, recent_fail≤base, covered flag, fallback_all reasons, JSON).  
- [ ] Tests per fallback reason; leakage; `--no-safe` parity; counters.  
- [ ] Update `bench_select.py` to pass `--no-safe`.  
- [ ] Commit: `Add select --safe defaults with fallback_all and safe JSON.`

### Task 3: Pytest plugin select + shadow + xdist

- [ ] Options, base merge-base, bin resolve, 10s timeout, fail-open, unknown_to_select, order.  
- [ ] Write `test_run_selection` for select/shadow; skip synthetic.  
- [ ] Shadow metrics in meta.  
- [ ] Tests: happy path, fail-open, **sleep-11 timeout**, param expand, shadow, `-n 2`.  
- [ ] Commit: `Integrate testpilot select into pytest plugin with shadow and fail-open.`

### Task 4: Composite Action + docs example

- [ ] `.github/actions/testpilot` with bin pref, cargo fallback, DB cache restore-keys, save-on-main only, fetch-depth 0, dump-to-base, summary lines.  
- [ ] Example workflow under `docs/`.  
- [ ] Commit: `Add TestPilot composite GitHub Action and example workflow.`

### Task 5: In-repo e2e fixture + CI job

- [ ] Mini fixture with controllable regression.  
- [ ] CI job: select hit, shadow 1.0, safe=false miss+visible flag, cold-cache all-green.  
- [ ] Commit: `Add safe-mode e2e fixture and CI job.`

### Task 6: Measurements + README + PR

- [ ] Scripts/commands for click/jinja size ±safe ±covered and plugin overhead median×5.  
- [ ] README safe/shadow/Action/limits.  
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
| Select mode rows | All collected, selected 0/1 |
| Action binary | Cargo + cache; prefer testpilot-bin / TESTPILOT_BIN |
| DB cache | Prefix restore-keys; save only on main push |
| Model cache | Not with DB; print model_sha256 |
| e2e repo | In-repo mini fixture; click/jinja for benches only |
| Plugin timeout | 10s → plugin_error(timeout) |
| T1 golden | v6→v7 + dump/select `--no-safe` byte lock |
