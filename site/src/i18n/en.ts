export const messages = {
  brand: "TestPilot",
  nav: {
    label: "Sections",
    pipeline: "Pipeline",
    demo: "Demo",
    install: "Install",
    github: "GitHub",
  },
  lang: { en: "EN", ru: "RU" },
  footer: {
    docs: "Docs",
    license: "MIT © 2026 bocchisad",
  },
  hero: {
    title: "From diff to ranked pytest tests",
    lede: "TestPilot ranks pytest tests by a failure score learned from your history, then runs the ones that fit a budget. The core and the CLI are Rust, collection is a pytest plugin, and CI uses a GitHub Action. It runs locally: no cloud account and no LLM.",
    demo: "Try demo",
    github: "GitHub",
    safe: "Safe mode is on by default. The run keeps every candidate when the diff touches project config, a non-Python file inside a source package, more than 30% of the Python source files, or when fewer than 50 tests are candidates.",
    termLabel: "terminal / testpilot",
    command:
      'testpilot select --model "$MODEL" --base HEAD~1 --head HEAD --budget-frac 0.1 --format json',
    termNote: "Command from the README. This block is not a captured session.",
  },
  problem: {
    title: "The suite is the slow part",
    lede: "A CI job that runs every test waits on tests this diff cannot affect. TestPilot orders candidates by a failure score learned from your history and keeps a prefix that fits one budget: a fraction of the list, a fixed count, or a time budget.",
    failopen:
      "If the pytest plugin cannot run select — no binary, no model, or no history snapshot — it fail-opens and runs the whole session. Cutting the suite is not the fallback.",
  },
  pipeline: {
    title: "From the diff to a budgeted run",
    steps: [
      {
        name: "Collect",
        body: "History and the diff land in a local SQLite file. `testpilot collect git` walks only commits reachable from HEAD, not `--all`, parents before children, through gix. A commit with more files than `--max-files-per-commit` (default 5000) is stored as `oversized=1` and gets no `file_changes` rows.",
      },
      {
        name: "Features",
        body: "Each change and test pair becomes a feature vector. `testpilot features dump` writes Parquet at `features_version` 2. Opening a v4 database runs migration 4→5, which drops `feature_history_snapshot`; `features show` needs a fresh dump after that.",
      },
      {
        name: "Train",
        body: "LightGBM learns the `new_fail` label. `testpilot-train train` uses lightgbm 4.5.0, with a binary candidate (early stopping on auc) and a lambdarank candidate (early stopping on ndcg), 500 rounds and early stopping at 50. Select checks `schema_sha256` against the schema embedded in the Rust binary.",
      },
      {
        name: "Select",
        body: "One budget keeps a prefix of the ranked list. Pass exactly one of `--budget-frac`, `--max-tests`, or `--time-budget`. A test's reason is the first match of `fallback_all`, `new_or_changed`, `recent_fail`, `flaky`, `covered`, `ranked`. `--time-budget` charges only ranked picks; forced and safe-added tests are not charged. `dur` in the JSON is the last duration in milliseconds, or `-1` when unknown.",
      },
      {
        name: "Safe mode",
        body: "A risky diff runs every candidate. Safe mode is on unless `--no-safe` is set. `fallback_all` is set when a non-delete hunk matches the config surface (`pyproject.toml`, `pytest.ini`, `**/conftest.py`, `requirements*.txt`, `requirements*.in`, `poetry.lock`, `uv.lock`, `Pipfile.lock`, `pdm.lock`, `.github/workflows/**`), or a non-Python file inside a source package (not docs, tests, or the doc allowlist `*.md`, `*.rst`, `docs/**`, `CHANGES*`, `LICENSE*`), or the fraction of changed Python source files is above `--safe-src-frac-threshold` (default 0.30), or there are fewer than `--safe-min-candidates` (default 50) candidates. The JSON always includes `safe.enabled`.",
      },
    ],
  },
  demo: {
    title: "Ranked tests for a real change",
    lede: "Pick a commit. The diff and the ranked tests come from a TestPilot select JSON, not from a drawing.",
    badge: "PLACEHOLDER DATA",
    empty: "No select run is loaded. The list stays empty until `build-demo-data` gets a ranking JSON.",
    selected: "selected",
    saved: "time not selected",
    caught: "failure in the selection",
    savedMissing: "not in the select JSON",
    caughtMissing: "not in the outcomes file",
    caughtYes: "yes",
    caughtNo: "no",
    safe: "Safe mode returned the full candidate set.",
    synthetic: "SYNTHETIC",
    measured:
      "model_measured_at {time} is env.date_utc from docs/measurements/bootstrap-w6-click-jinja-attrs.json at 20f45a9. It is the environment fingerprint time, not this select run.",
    caughtNote:
      "A yes or no needs an outcomes file. On a synthetic commit that file is the mutant run, so a caught failure was planted by the mutant.",
    scoreNote:
      "Raw score, ranker {ranker}. For model this is LightGBM predict_raw. For cov_lines it is a covered-line count. Not a probability.",
    commits: "commits",
    diff: "diff",
    ranked: "ranked tests",
  },
  reason: {
    fallback_all: "safe mode kept every candidate",
    new_or_changed: "the test was added or its definition changed",
    recent_fail: "failed in one of the last {k} clean runs",
    flaky: "marked flaky in history",
    covered:
      "safe mode added it for coverage; this needs `--safe-include-covered`, which defaults to off",
    ranked: "kept by score inside the budget",
  },
  safeReason: {
    config_surface: "project or CI config changed",
    few_candidates: "fewer candidates than the minimum",
    src_frac: "too large a share of Python source files changed",
    non_py_source: "a non-Python file inside a source package changed",
  },
  bootstrap: {
    title: "Cold start when history has no failures",
    lede: "`testpilot bootstrap` backfills real pytest runs, adds mutation labels, trains, and writes a verdict.",
    gateTitle: "The gate",
    command: `cargo build -p testpilot-cli --release
./target/release/testpilot bootstrap --repo "$REPO" --out "$MODEL_DIR" \\
  --max-commits 200 --mutants-per-commit 4 --jobs 2 --seed 42`,
    defaults:
      "The README example passes `--jobs 2`. The CLI default is `--jobs 1`. Defaults are N=200, K=4, seed 42, suite `click`, resume on. `--jobs` cannot exceed 4.",
    gate: "`--allow-cold-baseline` belongs to `testpilot-train train` and is off by default. Without it, fewer than 10 distinct `commit_idx` values raise `InsufficientCommitsError` and the process exits 2 before `chrono_split`. `testpilot bootstrap` always passes the flag. With the flag, that cold case writes `model.json` with `ranker=cov_lines`, `bootstrap.verdict=use_baseline`, `reason=insufficient_commit_idx`, and no `model.txt`. That reason is not a gate result. `chrono_split` does not start.\n\nAfter a split, the gate looks only at the synthetic TEST cluster against the `cov_lines` baseline. Validation metrics are reported and do not decide. `use_model` happens only when `ci_low >= 0` and the slice is sufficient: at least 10 distinct C and at least 20 kill mutants. Otherwise `verdict=use_baseline` and `ranker=cov_lines`, with reason `insufficient_test_C` (n_C < 10), `data_insufficient`, `no_test_signal` (no CI), or `synth_test_ci_low_lt_0`. Accept reason is `synth_test_ci_low_ge_0`. When the ranker is `cov_lines`, select orders by `cov_lines`, then `cov_func`, then `test_id`. Safe mode still applies. A larger N or more history is a new run with `--force`.",
    less: "On Click, `less` has to be on PATH. Without it, pager tests fail on every commit. Those failures are persistent, not `new_fail`.",
    steps: [
      {
        name: "cleanup",
        body: "Removes leftover bootstrap worktrees and virtualenvs from an interrupted run.",
      },
      {
        name: "init",
        body: "Creates the SQLite database and appends `.testpilot/` to `.git/info/exclude`, not to the tracked `.gitignore`.",
      },
      {
        name: "collect_git",
        body: "Indexes the target repository history into that database.",
      },
      {
        name: "backfill",
        body: "Runs pytest on the planned SHAs that do not already have a finished clean real run with test results. The default set is the last `max(N, 20)` first-parent commits, union the sampled commits. An incompatible historical pytest is restored to pytest 8.3.4, pluggy `>=1.5,<2`, and py `>=1.11,<2`.",
      },
      {
        name: "mutate",
        body: "Applies AST mutants, `K` per sampled commit. Workers use separate virtualenvs. `--jobs` is at most 4. Synthetic runs are stored with `synthetic=1`.",
      },
      {
        name: "features_dump",
        body: "Dumps pairs with mutant rows included. The step is done when the database fingerprint matches, not when a file mtime changes.",
      },
      {
        name: "train",
        body: "Runs `testpilot-train train` and always passes `--allow-cold-baseline`.",
      },
      {
        name: "gate",
        body: "Reads `bootstrap.verdict` from `model.json`.",
      },
      {
        name: "finalize",
        body: "Writes `bootstrap_report.json` and `bootstrap_report.md`.",
      },
    ],
  },
  install: {
    title: "Build from this repo",
    lede: "Nothing here is published to PyPI or crates.io. `pip install testpilot`, `pytest-pilot`, and `testpilot-ai` are other projects. The commands below are the ones in this repository's README.",
    cli: "CLI",
    cliCommand: `cargo build -p testpilot-cli --release
export PATH="$PWD/target/release:$PATH"
testpilot --version`,
    cliNote:
      "Workspace version 0.1.0. `--version` also prints the git SHA embedded at build time.",
    plugin: "pytest plugin",
    pluginCommand: `pip install -e python/pytest-testpilot
pytest --testpilot --testpilot-db=/abs/path/to/db.sqlite`,
    pluginNote:
      'Package `pytest-testpilot` 0.1.0, Python >= 3.10. Use the equals form so pytest does not treat the path as a rootdir. Select is a separate flag and needs exactly one budget, for example `--testpilot-select --testpilot-model "$MODEL" --testpilot-budget-frac 0.1`.',
    action: "GitHub Action",
    actionCommand: `- uses: actions/checkout@v4
  with:
    fetch-depth: 0
- uses: ./.github/actions/testpilot
  with:
    model-dir: path/to/your/model
    budget-frac: '0.15'
    safe: 'true'
    pytest-args: '-q'`,
    actionNote:
      "This composite action lives at `.github/actions/testpilot`. The checked-in example runs it from this repository. On a push to the default branch it runs full pytest, then `collect git`, then `features dump`. On a pull request it runs `--testpilot-select` and requires exactly one of `budget-frac`, `max-tests`, or `time-budget`. It is not a Marketplace action. In another repository the same file builds `testpilot-cli` only if that repo contains the crates, and it pip-installs `pytest-testpilot` only if `python/pytest-testpilot/pyproject.toml` is in the workspace. Otherwise it installs pytest and coverage and does not install this plugin. Pass `testpilot-bin` if you already have a binary.",
    trainNote:
      "Package `testpilot-train` 0.1.0 pins lightgbm==4.5.0. Same rule: install from this checkout, not from PyPI.",
    trainCommand: "pip install -e 'python/testpilot-train[dev]'",
  },
  measurements: {
    title: "Week 6 result",
    lede: "The model is still level with the coverage baseline. The default ranker is cov_lines.",
    pooledLead:
      "Pooled TEST delta versus cov_lines. It does not establish an advantage across held-out repositories.",
    pooledValue: "Δ {delta}, ci95 [{lo}, {hi}], n={n}, n_C={nC}",
    macro: "macro",
    limits: [
      "The dry-run estimate underpredicted measured wall time by 32–42%.",
      "Click and attrs each had TEST n_C=5, below the gate minimum of 10.",
    ],
    col: {
      repo: "Held-out repository",
      model: "Model recall@10%",
      baseline: "cov_lines",
      delta: "Δ",
      gate: "Bootstrap gate",
    },
  },
  comparison: {
    title: "How the other selectors decide",
    lede: "TestPilot is offline and MIT-licensed. The score is computed on your machine: a LightGBM predict_raw value, or a covered-line count when the gate selects the cov_lines ranker. Nothing is sent out. The rows below are the other tools' own descriptions, not a benchmark.",
    col: { tool: "Tool", approach: "Approach", source: "Source" },
  },
  cmp: {
    testmon:
      "pytest-testmon records which lines each test hit, using coverage.py, and stores that in `.testmondata`. On the next run it deselects tests whose file and function checksums did not change. Tests that failed last time stay selected. This is local.",
    smart:
      "CloudBees Smart Tests, formerly Launchable, ranks tests by semantic similarity between the change and the test source. The product overview says the ranking uses large language models. The selection write-up says it does not use code coverage. A subset is requested from their service with `smart-tests subset`.",
    datadog:
      "Datadog Test Impact Analysis collects per-test coverage and sends it to Datadog. A test is skipped only when a previous passing run exists on a commit whose covered files and tracked files match the current commit.",
    meta: "There is no public Meta product by this name. Machalica, Samylkin, Porth, and Chandra describe a classifier trained on historical outcomes in the Facebook monorepo (ICSE-SEIP 2019). Figures in that paper are about Facebook's deployment. They are not TestPilot results.",
  },
} as const;

export const links = {
  github: "https://github.com/bocchisad/TestPilot",
  locale: {
    en: "https://bocchisad.github.io/TestPilot/",
    ru: "https://bocchisad.github.io/TestPilot/ru/",
  },
  docs: "https://github.com/bocchisad/TestPilot/tree/20f45a96fdd23f1a541308cde25f8bcbc562722e/docs",
  license:
    "https://github.com/bocchisad/TestPilot/blob/20f45a96fdd23f1a541308cde25f8bcbc562722e/LICENSE",
  comparison: [
    {
      name: "pytest-testmon",
      hrefs: ["https://github.com/tarpas/pytest-testmon"],
      approach: "testmon" as const,
    },
    {
      name: "Launchable / CloudBees Smart Tests",
      hrefs: [
        "https://docs.cloudbees.com/docs/cloudbees-smart-tests/latest/",
        "https://docs.cloudbees.com/docs/cloudbees-smart-tests/latest/features/predictive-test-selection/how-we-select-tests",
      ],
      approach: "smart" as const,
    },
    {
      name: "Datadog Test Impact Analysis",
      hrefs: ["https://docs.datadoghq.com/tests/test_impact_analysis/how_it_works/"],
      approach: "datadog" as const,
    },
    {
      name: "Meta predictive test selection",
      hrefs: ["https://arxiv.org/abs/1810.05286"],
      approach: "meta" as const,
    },
  ],
};
