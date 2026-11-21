import type { Messages } from "./types";

export const messages: Messages = {
  brand: "TestPilot",
  nav: {
    label: "Разделы",
    pipeline: "Пайплайн",
    demo: "Демо",
    install: "Установка",
    github: "GitHub",
  },
  lang: { en: "EN", ru: "RU" },
  footer: {
    docs: "Документация",
    license: "MIT © 2026 bocchisad",
  },
  hero: {
    title: "От diff к ранжированным тестам pytest",
    lede: "TestPilot ранжирует тесты по score, обученному на истории, и запускает те, что помещаются в бюджет. Ядро и CLI на Rust, сбор результатов делает плагин pytest, в CI подключается GitHub Action. Считается локально: без облака и без LLM.",
    demo: "Открыть демо",
    github: "GitHub",
    safe: "Safe mode включён по умолчанию. Полный набор кандидатов остаётся, если diff задевает конфиг проекта, не-Python файл внутри пакета с исходниками, больше 30% Python-файлов исходников или если кандидатов меньше 50.",
    termLabel: "terminal / testpilot",
    command:
      'testpilot select --model "$MODEL" --base HEAD~1 --head HEAD --budget-frac 0.1 --format json',
    termNote: "Команда из README. Это не запись настоящего запуска.",
  },
  problem: {
    title: "Долго ждать весь набор",
    lede: "CI, который гоняет все тесты, ждёт и те, которых этот diff не касается. TestPilot ранжирует тесты по score, обученному на истории, и оставляет префикс под один бюджет: долю списка, фиксированное число или лимит по времени.",
    failopen:
      "Если плагин не может запустить select — нет бинаря, модели или снимка истории — он fail-open и выполняет всю сессию. Урезание набора не является запасным путём.",
  },
  pipeline: {
    title: "От diff до запуска по бюджету",
    steps: [
      {
        name: "Collect",
        body: "История и diff попадают в локальный SQLite. `testpilot collect git` идёт только по коммитам, достижимым из HEAD, не `--all`, от родителей к потомкам, через gix. Коммит, где файлов больше `--max-files-per-commit` (по умолчанию 5000), пишется как `oversized=1` и без строк `file_changes`.",
      },
      {
        name: "Features",
        body: "Пара «изменение, тест» становится вектором признаков. `testpilot features dump` пишет Parquet, `features_version` 2. Открытие базы v4 запускает миграцию 4→5, она делает `DROP TABLE feature_history_snapshot`; после этого `features show` нужен новый dump.",
      },
      {
        name: "Train",
        body: "LightGBM учится на метке `new_fail`. `testpilot-train train` сидит на lightgbm 4.5.0: кандидат binary с early stopping по auc и кандидат lambdarank по ndcg, 500 раундов и остановка на 50. Select сверяет `schema_sha256` со схемой, вшитой в Rust-бинарь.",
      },
      {
        name: "Select",
        body: "Один бюджет оставляет префикс ранжированного списка. Нужен ровно один из `--budget-frac`, `--max-tests`, `--time-budget`. Причина теста — первое совпадение из `fallback_all`, `new_or_changed`, `recent_fail`, `flaky`, `covered`, `ranked`. `--time-budget` списывает время только с ranked; force и safe-added в бюджет не входят. `dur` в JSON — длительность последнего прогона в миллисекундах, либо `-1`, если она неизвестна.",
      },
      {
        name: "Safe mode",
        body: "Рискованный diff запускает всех кандидатов. Safe mode включён, пока не передан `--no-safe`. `fallback_all` ставится, если hunk не-удаление попадает в config surface (`pyproject.toml`, `pytest.ini`, `**/conftest.py`, `requirements*.txt`, `requirements*.in`, `poetry.lock`, `uv.lock`, `Pipfile.lock`, `pdm.lock`, `.github/workflows/**`), или меняется не-Python файл внутри исходного пакета (не docs, не tests и не allowlist `*.md`, `*.rst`, `docs/**`, `CHANGES*`, `LICENSE*`), или доля изменённых Python-исходников выше `--safe-src-frac-threshold` (по умолчанию 0.30), или кандидатов меньше `--safe-min-candidates` (по умолчанию 50). В JSON всегда есть `safe.enabled`.",
      },
    ],
  },
  demo: {
    title: "Ранжирование на настоящем изменении",
    lede: "Выберите коммит. Diff и ранжированные тесты берутся из JSON команды select, это не макет.",
    badge: "PLACEHOLDER DATA",
    empty: "Прогон select не загружен. Список пуст, пока `build-demo-data` не получит ranking JSON.",
    selected: "выбрано",
    saved: "время невыбранных",
    caught: "падение внутри выбора",
    savedMissing: "в JSON select этого нет",
    caughtMissing: "в файле исходов этого нет",
    caughtNone: "—",
    caughtYes: "да",
    caughtNo: "нет",
    safe: "Safe mode вернул полный набор кандидатов.",
    synthetic: "SYNTHETIC",
    measured:
      "model_measured_at {time} — env.date_utc из docs/measurements/bootstrap-w6-click-jinja-attrs.json на коммите 20f45a9. Это время снимка окружения, не время этого select.",
    nothingToCatch: "Исходы есть, новых падений нет — ловить нечего.",
    onlyFlaky: "Упал только нестабильный тест, он не считается.",
    missedBudget:
      "Новые падения есть, но не попали в бюджет {pct}%. Упало {failed}, в выборке {selected}.",
    planted: "На synthetic-коммите это прогон мутанта, и пойманное падение посажено мутацией.",
    scoreNote:
      "Сырой score, ranker {ranker}. Для model это LightGBM predict_raw. Для cov_lines это число покрытых строк. Не вероятность.",
    commits: "коммиты",
    diff: "diff",
    ranked: "ранжированные тесты",
  },
  reason: {
    fallback_all: "safe mode оставил всех кандидатов",
    new_or_changed: "тест добавлен или изменилось его определение",
    recent_fail: "падал в одном из {k} последних чистых прогонов",
    flaky: "в истории помечен как flaky",
    covered:
      "safe mode добавил его по покрытию; для этого нужен `--safe-include-covered`, по умолчанию он выключен",
    ranked: "оставлен по score внутри бюджета",
  },
  safeReason: {
    config_surface: "изменился конфиг проекта или CI",
    few_candidates: "кандидатов меньше порога",
    src_frac: "изменилась слишком большая доля Python-исходников",
    non_py_source: "изменился не-Python файл внутри исходного пакета",
  },
  bootstrap: {
    title: "Холодный старт, когда в истории нет падений",
    lede: "`testpilot bootstrap` догоняет реальные прогоны pytest, добавляет метки мутаций, обучает модель и записывает verdict.",
    gateTitle: "Gate",
    command: `cargo build -p testpilot-cli --release
./target/release/testpilot bootstrap --repo "$REPO" --out "$MODEL_DIR" \\
  --max-commits 200 --mutants-per-commit 4 --jobs 2 --seed 42`,
    defaults:
      "В примере README стоит `--jobs 2`. Default в CLI — `--jobs 1`. Остальные default: N=200, K=4, seed 42, suite `click`, resume включён. `--jobs` не больше 4.",
    gate: "`--allow-cold-baseline` — флаг `testpilot-train train`, по умолчанию выключен. Без него меньше 10 различных `commit_idx` дают `InsufficientCommitsError` и выход 2 ещё до `chrono_split`. `testpilot bootstrap` всегда передаёт этот флаг. С флагом холодный случай пишет `model.json` с `ranker=cov_lines`, `bootstrap.verdict=use_baseline`, `reason=insufficient_commit_idx` и без `model.txt`. Это не результат gate. `chrono_split` при этом не стартует.\n\nПосле split gate смотрит только на синтетический TEST-кластер против бейзлайна `cov_lines`. Метрики validation печатаются и решение не принимают. `use_model` бывает только если `ci_low >= 0` и срез достаточный: не меньше 10 различных C и не меньше 20 kill-мутантов. Иначе `verdict=use_baseline` и `ranker=cov_lines`, причина `insufficient_test_C` (n_C < 10), `data_insufficient`, `no_test_signal` (нет CI) или `synth_test_ci_low_lt_0`. Причина принятия — `synth_test_ci_low_ge_0`. При `ranker=cov_lines` select сортирует по `cov_lines`, затем `cov_func`, затем `test_id`. Safe mode при этом работает. Больший N или новая история — это новый запуск с `--force`.",
    less: "Для Click в PATH нужен `less`. Без него pager-тесты падают на каждом коммите. Это устойчивые падения, не `new_fail`.",
    steps: [
      { name: "cleanup", body: "Убирает worktree и virtualenv, оставшиеся от прерванного прогона." },
      {
        name: "init",
        body: "Создаёт базу SQLite и дописывает `.testpilot/` в `.git/info/exclude`, не в отслеживаемый `.gitignore`.",
      },
      { name: "collect_git", body: "Кладёт историю целевого репозитория в эту базу." },
      {
        name: "backfill",
        body: "Запускает pytest на тех SHA из плана, для которых ещё нет законченного чистого реального прогона с результатами тестов. Набор по умолчанию — последние `max(N, 20)` коммитов first-parent плюс выбранные коммиты. Несовместимый исторический pytest возвращается к pytest 8.3.4, pluggy `>=1.5,<2` и py `>=1.11,<2`.",
      },
      {
        name: "mutate",
        body: "Ставит AST-мутанты, `K` штук на выбранный коммит. У воркеров отдельные virtualenv. `--jobs` не больше 4. Синтетические прогоны пишутся с `synthetic=1`.",
      },
      {
        name: "features_dump",
        body: "Dump с мутантными строками. Шаг считается сделанным по fingerprint базы, а не по mtime файла.",
      },
      {
        name: "train",
        body: "Запускает `testpilot-train train` и всегда передаёт `--allow-cold-baseline`.",
      },
      { name: "gate", body: "Читает `bootstrap.verdict` из `model.json`." },
      { name: "finalize", body: "Пишет `bootstrap_report.json` и `bootstrap_report.md`." },
    ],
  },
  install: {
    title: "Сборка из этого репозитория",
    lede: "В PyPI и crates.io этого проекта нет. `pip install testpilot`, `pytest-pilot` и `testpilot-ai` — другие проекты. Ниже команды из README этого репозитория.",
    cli: "CLI",
    cliCommand: `cargo build -p testpilot-cli --release
export PATH="$PWD/target/release:$PATH"
testpilot --version`,
    cliNote: "Версия workspace — 0.1.0. `--version` дополнительно печатает git SHA, вшитый при сборке.",
    plugin: "плагин pytest",
    pluginCommand: `pip install -e python/pytest-testpilot
pytest --testpilot --testpilot-db=/abs/path/to/db.sqlite`,
    pluginNote:
      'Пакет `pytest-testpilot` 0.1.0, Python >= 3.10. Форма с `=` нужна, чтобы pytest не принял путь за rootdir. Select — отдельный флаг и требует ровно один бюджет, например `--testpilot-select --testpilot-model "$MODEL" --testpilot-budget-frac 0.1`.',
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
      "Composite action лежит в `.github/actions/testpilot`. Пример в репозитории запускает её из этого же репозитория. Push в default branch: полный pytest, затем `collect git`, затем `features dump`. Pull request: `--testpilot-select` и ровно один из `budget-frac`, `max-tests`, `time-budget`. В Marketplace её нет. В чужом репозитории этот файл соберёт `testpilot-cli`, только если там есть эти crates, и поставит `pytest-testpilot`, только если в workspace есть `python/pytest-testpilot/pyproject.toml`. Иначе ставятся pytest и coverage, без этого плагина. Свой бинарь передаётся через `testpilot-bin`.",
    trainNote:
      "Пакет `testpilot-train` 0.1.0 фиксирует lightgbm==4.5.0. То же правило: ставить из этого checkout, не из PyPI.",
    trainCommand: "pip install -e 'python/testpilot-train[dev]'",
  },
  measurements: {
    title: "Итог недели 6",
    lede: "Модель пока на уровне coverage-baseline, по умолчанию cov_lines.",
    pooledLead:
      "Pooled TEST delta против cov_lines. Это не преимущество на held-out репозиториях.",
    pooledValue: "Δ {delta}, ci95 [{lo}, {hi}], n={n}, n_C={nC}",
    macro: "macro",
    limits: [
      "Dry-run занизил измеренное время на 32–42%.",
      "У Click и attrs в TEST n_C=5, это меньше порога gate 10.",
    ],
    col: {
      repo: "Held-out репозиторий",
      model: "Model recall@10%",
      baseline: "cov_lines",
      delta: "Δ",
      gate: "Bootstrap gate",
    },
  },
  comparison: {
    title: "Как выбирают другие",
    lede: "TestPilot работает офлайн, лицензия MIT. Score считается на вашей машине: LightGBM predict_raw или число покрытых строк, если gate выбрал ranker cov_lines. Наружу ничего не отправляется. В строках — формулировки самих инструментов, это не бенчмарк.",
    col: { tool: "Инструмент", approach: "Подход", source: "Источник" },
  },
  cmp: {
    testmon:
      "pytest-testmon запоминает, какие строки задел каждый тест, через coverage.py, и хранит это в `.testmondata`. На следующем запуске снимает тесты, у которых не изменились контрольные суммы файла и функции. Тесты, которые упали в прошлый раз, остаются. Это локальный режим.",
    smart:
      "CloudBees Smart Tests, раньше Launchable, ранжирует тесты по смысловой близости изменения и исходника теста. В обзоре продукта сказано, что ранжирование использует большие языковые модели. В описании отбора сказано, что покрытие кода не используется. Подмножество запрашивается у их сервиса командой `smart-tests subset`.",
    datadog:
      "Datadog Test Impact Analysis собирает покрытие каждого теста и отправляет его в Datadog. Тест пропускается только если есть прошлый успешный прогон на коммите, где покрытые файлы и отслеживаемые файлы совпали с текущим коммитом.",
    meta: "Публичного продукта Meta с таким именем нет. Machalica, Samylkin, Porth и Chandra описывают классификатор на исторических исходах в монорепозитории Facebook (ICSE-SEIP 2019). Цифры в статье относятся к их внедрению. Это не результаты TestPilot.",
  },
};
