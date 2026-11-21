# TestPilot site — SPEC

Статус: сайт собран на `feat/site` поверх `main` `20f45a96fdd23f1a541308cde25f8bcbc562722e`. `demo.json` собран из `testpilot select`. В `main` не мержить и Pages не включать, пока mell не попросит.

Канонические пути:

- ветка `feat/site`, fast-forward на `main` `20f45a96fdd23f1a541308cde25f8bcbc562722e`
- worktree: `/Users/gothscum/Documents/проекты/TestPilot-site`
- рабочее дерево `feat/week6-bootstrap` не переключалось и не коммитится этим этапом

## 1. Что это

Одна статическая страница на двух языках: английский на `https://bocchisad.github.io/TestPilot/`, русский на `https://bocchisad.github.io/TestPilot/ru/`. Отдельных маршрутов под секции нет, секции — якоря. Бэкенда нет. Интерактив только у демо.

Сайт описывает TestPilot как офлайн-инструмент predictive test selection: Rust core и CLI, pytest-плагин, GitHub Action. Облака и LLM в самом TestPilot нет.

## 2. Откуда взяты факты

Факты берутся с `main` `20f45a96fdd23f1a541308cde25f8bcbc562722e`. PR #8 влит squash-коммитом, поэтому `d90754e` не является предком `main`.

| Что | Где проверено |
|---|---|
| Ветка сайта | `main` = `20f45a96fdd23f1a541308cde25f8bcbc562722e` |
| `testpilot select`, установка CLI и плагина, пример Action | README и `docs/examples/testpilot-ci.yml` на этом коммите |
| `testpilot bootstrap`, шаги, gate, `--allow-cold-baseline` | README и clap на этом же коммите. Подкоманда есть на `main` |
| Замеры | `docs/measurements/bootstrap-w6-click-jinja-attrs.json`, поле `interpretation`, и `docs/measurements/loro-w6-click-jinja-attrs/report.md` на этом коммите |

Команды на сайте — только те, что есть в README соответствующего коммита или в clap/`argparse`. Команды `testpilot rank` не существует, в макете она была заглушкой.

Лицензия есть: `LICENSE` в корне, MIT, Copyright (c) 2026 bocchisad. Заглушка в футере не нужна.

Дизайн: `~/testpilot-site-design/variant-b.html` и `tokens-b.md`. Файла `tokens.md` нет. Берутся токены варианта B.

Пакеты `pytest-testpilot` и `testpilot-train` в репозитории не публикуются: workflow публикации нет. На PyPI есть другие проекты с похожими именами (`testpilot` у Hypothesis, `pytest-pilot`, `testpilot-ai`). Сайт не должен предлагать `pip install testpilot`, `pip install pytest-testpilot` или `pip install testpilot-train` из индекса.

## 3. Зафиксированные решения

1. Текст про bootstrap описывает CLI на `main`. Фразы «нет на main» на сайте нет.
2. `demo.json` собран скриптом из `testpilot select --format json` по click. Коммиты, тесты и scores руками не подставляются.
3. Таблица замеров — `interpretation` из `bootstrap-w6-click-jinja-attrs.json`. На странице числа округляются в коде до трёх знаков. Полная точность остаётся в JSON по ссылке на SHA `20f45a9`. Строк mut2 и блока dry-run нет. Вывод: модель на уровне coverage-baseline, ranker по умолчанию `cov_lines`.
4. Числовые строки UI живут в словарях. Имена тестов, diff, reason-коды и числа из артефактов живут в JSON и не переводятся. Score нигде не называется вероятностью или процентом: при `ranker=model` это сырой LightGBM `predict_raw`, при `ranker=cov_lines` — число покрытых строк.
5. Янтарный цвет только у предупреждений: бейдж PLACEHOLDER, бейдж SYNTHETIC, блок safe mode, неизвестный reason. Score цвета `--ink`.
6. Фокус — обводка `--green` 2px, не янтарь.
7. Шрифт макета (SF Mono) не кладём: его нельзя честно self-host. Mono — vendored JetBrains Mono, текст — `system-ui`.

## 4. Страницы

Оба языка — один и тот же набор секций, сверху вниз:

1. Шапка
2. Hero `#top`
3. Problem `#problem`
4. Pipeline `#pipeline`
5. Demo `#demo`
6. Bootstrap `#bootstrap`
7. Install `#install`
8. Measurements `#measurements`
9. Comparison `#comparison`
10. Футер

Шапка: слово TestPilot, ссылки Pipeline / Demo / Install / GitHub, переключатель EN и RU. На 360px ссылки переносятся, ни одна не прячется. Текущий язык помечен `aria-current="page"`. EN ведёт на `{base}/`, RU на `{base}/ru/`. У обеих ссылок атрибут `data-keep-hash`. Короткий скрипт в layout при `hashchange` и перед кликом дописывает текущий `location.hash` (`#demo` и остальные якоря) к этим href. Без скрипта язык всё равно переключается, якорь тогда не сохраняется. GitHub — `https://github.com/bocchisad/TestPilot`.

`<html lang>` равен локали страницы (`en` или `ru`). В `<head>` три ссылки, абсолютные, с тем же base:

```html
<link rel="alternate" hreflang="en" href="https://bocchisad.github.io/TestPilot/" />
<link rel="alternate" hreflang="ru" href="https://bocchisad.github.io/TestPilot/ru/" />
<link rel="alternate" hreflang="x-default" href="https://bocchisad.github.io/TestPilot/" />
```

Футер: GitHub, документация `https://github.com/bocchisad/TestPilot/tree/20f45a96fdd23f1a541308cde25f8bcbc562722e/docs`, лицензия `https://github.com/bocchisad/TestPilot/blob/20f45a96fdd23f1a541308cde25f8bcbc562722e/LICENSE` с текстом «MIT © 2026 bocchisad». Ссылки на замеры используют тот же SHA, не `blob/main`.

## 5. Дизайн

Токены из `tokens-b.md`, без изменений значений:

```css
:root {
  --bg: #0d1110;
  --panel: #131a17;
  --panel2: #18211d;
  --ink: #e5eee8;
  --muted: #9cac9f;
  --line: #35443a;
  --green: #a2e6a7;
  --green2: #6ec97c;
  --amber: #d6aa5b;
  --red: #d87970;
  --sans: system-ui, sans-serif;
  --mono: "JetBrains Mono", ui-monospace, monospace;
  --s1: 4px; --s2: 8px; --s3: 12px; --s4: 16px;
  --s6: 24px; --s8: 32px; --s12: 48px;
  --r: 2px;
  --shadow: 0 18px 60px #050807;
  --on-green: #101510;
}
```

`--on-green` взят из макета (текст на зелёной кнопке), в `tokens-b.md` его нет. Других цветов не добавлять.

Контраст на `--bg` и `--panel`, посчитан для этих пар: `--ink` 16.05 / 14.92, `--muted` 7.98 / 7.42, `--green` 13.05 / 12.13, `--green2` 9.33 на `--bg`, `--amber` 8.83 на `--bg`, `--red` 6.24 на `--bg`, `--on-green` на `--green` 12.68. Все выше 4.5:1, обычный текст 11px тоже проходит AA. Если при вёрстке цвет текста или фона изменится, пару надо пересчитать и не принимать ниже 4.5:1.

Правила:

- Радиус 2px. Свечения нет. Декоративной анимации нет. `scroll-behavior` только без `prefers-reduced-motion`.
- Тень `--shadow` только у терминала в hero, как в макете. Это тёмная тень, не glow.
- Янтарь: бейдж PLACEHOLDER, бейдж SYNTHETIC, панель safe mode, reason, которого нет в словаре.
- Кнопки: primary — фон `--green`, текст `--on-green`; вторая — обводка `--green2`, текст `--green`.
- Ширина колонки `min(1180px, calc(100% - 48px))`, ниже 560px — `calc(100% - 32px)`. На 360px контентная ширина 328px, горизонтального скролла страницы нет. Таблица замеров скроллится внутри своего блока.
- Сетка как в макете: hero две колонки до 850px, pipeline пять колонок, ниже 850px две, ниже 560px одна. Demo и install так же складываются в одну колонку.
- Фокус видим на ссылках, кнопках и элементах демо. Обводка не снимается.
- Секции — `section` с `h2`. Шапка — `header`, навигация — `nav` с именем.

Шрифты лежат в репозитории, в HTML нет запросов к CDN:

- `site/public/fonts/JetBrainsMono-Regular.woff2`
- `site/public/fonts/JetBrainsMono-Bold.woff2`
- `site/public/fonts/OFL.txt` из релиза JetBrains Mono (OFL-1.1)

Нужны латинские и кириллические глифы. `font-display: swap`. URL в `@font-face` через `import.meta.env.BASE_URL`, не абсолютный путь от корня домена.

## 6. Стек и деплой

- Astro, статическая сборка. Версию зафиксировать в `site/package-lock.json` той, которую на момент реализации вернёт `npm view astro version`. Пакет не плавает.
- Зависимости: сам Astro и TypeScript. Без React, Preact, Vue, UI-kit и аналитики.
- Демо — один модуль `site/src/scripts/demo.ts` без фреймворка, подключённый как `<script>` только в секции демо.
- `site/astro.config.mjs`: `site: 'https://bocchisad.github.io'`, `base: '/TestPilot'`, `trailingSlash: 'always'`. Локали `en` и `ru`, `prefixDefaultLocale: false`.
- Скрипты npm: `dev`, `build`, `preview`. `build` должен заканчиваться с пустым stderr по предупреждениям Astro. Если Astro пишет warning, сборка для приёмки не считается зеленой.

Workflow только новый файл `.github/workflows/site.yml`. `.github/workflows/ci.yml` не менять. Существующие job'ы (`rust`, `python`, `python-no-xdist`, `python-wheel`, `e2e`, `safe-e2e`, `bootstrap-e2e`, `features-oracle`, `train-oracle`) не трогать.

```yaml
name: site
on:
  push:
    branches: [main]
    paths:
      - 'site/**'
      - '.github/workflows/site.yml'
  pull_request:
    paths:
      - 'site/**'
      - '.github/workflows/site.yml'
  workflow_dispatch:
permissions:
  contents: read
  pages: write
  id-token: write
concurrency:
  group: pages
  cancel-in-progress: false
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '22'
          cache: npm
          cache-dependency-path: site/package-lock.json
      - run: npm ci
        working-directory: site
      - run: npm run build
        working-directory: site
      - uses: actions/upload-pages-artifact@v3
        if: github.event_name != 'pull_request'
        with:
          path: site/dist
  deploy:
    needs: build
    if: github.event_name != 'pull_request'
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - id: deployment
        uses: actions/deploy-pages@v4
```

На `pull_request` выполняется только сборка. `upload-pages-artifact` и `deploy` при pull request не запускаются.

Пуш и включение Pages в настройках репозитория этим этапом не делаются. Пока источник Pages в репозитории не переключён на GitHub Actions, workflow после мержа в `main` сам сайт не опубликует. Это одноразовая настройка, её делает mell.

Локальная проверка base path: `npm run preview` и открытие `http://127.0.0.1:4321/TestPilot/` и `http://127.0.0.1:4321/TestPilot/ru/`. Корень `http://127.0.0.1:4321/` не является страницей сайта.

## 7. i18n

Словари `site/src/i18n/en.ts` и `site/src/i18n/ru.ts` экспортируют один и тот же тип. Ключ, который есть только в одном языке, ломает `npm run build` (тип не совпадёт). В `.astro` нет строковых литералов, видимых пользователю, кроме данных из JSON.

Переключатель меняет URL, а не подменяет строки на клиенте. Демо читает подписи из `data-i18n`, которые Astro уже подставил для этой локали.

Тексты ниже копируются в словари дословно.

### Шапка и футер

EN

- `brand`: TestPilot
- `nav.pipeline`: Pipeline
- `nav.demo`: Demo
- `nav.install`: Install
- `nav.github`: GitHub
- `lang.en`: EN
- `lang.ru`: RU
- `footer.docs`: Docs
- `footer.license`: MIT © 2026 bocchisad

RU

- `nav.pipeline`: Пайплайн
- `nav.demo`: Демо
- `nav.install`: Установка
- `nav.github`: GitHub
- `lang.en`: EN
- `lang.ru`: RU
- `footer.docs`: Документация
- `footer.license`: MIT © 2026 bocchisad

### Hero

EN

- `hero.title`: From diff to ranked pytest tests
- `hero.lede`: TestPilot ranks pytest tests by a failure score learned from your history, then runs the ones that fit a budget. The core and the CLI are Rust, collection is a pytest plugin, and CI uses a GitHub Action. It runs locally: no cloud account and no LLM.
- `hero.demo`: Try demo
- `hero.github`: GitHub
- `hero.safe`: Safe mode is on by default. The run keeps every candidate when the diff touches project config, a non-Python file inside a source package, more than 30% of the Python source files, or when fewer than 50 tests are candidates.
- `hero.termLabel`: terminal / testpilot
- `hero.command`: строка команды, не перевод. См. ниже.

RU

- `hero.title`: От diff к ранжированным тестам pytest
- `hero.lede`: TestPilot ранжирует тесты по score, обученному на истории, и запускает те, что помещаются в бюджет. Ядро и CLI на Rust, сбор результатов делает плагин pytest, в CI подключается GitHub Action. Считается локально: без облака и без LLM.
- `hero.demo`: Открыть демо
- `hero.github`: GitHub
- `hero.safe`: Safe mode включён по умолчанию. Полный набор кандидатов остаётся, если diff задевает конфиг проекта, не-Python файл внутри пакета с исходниками, больше 30% Python-файлов исходников или если кандидатов меньше 50.

Команда в терминале, одна и та же на обоих языках, из README на `main`:

```bash
testpilot select --model "$MODEL" --base HEAD~1 --head HEAD --budget-frac 0.1 --format json
```

Под ней нет выдуманного вывода. Вторая строка терминала — подпись, что это команда из README, а не запись сессии.

EN `hero.termNote`: Command from the README. This block is not a captured session.
RU `hero.termNote`: Команда из README. Это не запись настоящего запуска.

### Problem

EN

- `problem.title`: The suite is the slow part
- `problem.lede`: A CI job that runs every test waits on tests this diff cannot affect. TestPilot orders candidates by a failure score learned from your history and keeps a prefix that fits one budget: a fraction of the list, a fixed count, or a time budget.
- `problem.failopen`: If the pytest plugin cannot run select — no binary, no model, or no history snapshot — it fail-opens and runs the whole session. Cutting the suite is not the fallback.

RU

- `problem.title`: Долго ждать весь набор
- `problem.lede`: CI, который гоняет все тесты, ждёт и те, которых этот diff не касается. TestPilot ранжирует тесты по score, обученному на истории, и оставляет префикс под один бюджет: долю списка, фиксированное число или лимит по времени.
- `problem.failopen`: Если плагин не может запустить select — нет бинаря, модели или снимка истории — он fail-open и выполняет всю сессию. Урезание набора не является запасным путём.

Чисел про ускорение здесь нет.

### Pipeline

EN `pipeline.title`: From the diff to a budgeted run
RU `pipeline.title`: От diff до запуска по бюджету

Пять шагов. Первая фраза — смысл, вторая — деталь из кода. Обе в словаре.

EN

1. Collect. History and the diff land in a local SQLite file. `testpilot collect git` walks only commits reachable from HEAD, not `--all`, parents before children, through gix. A commit with more files than `--max-files-per-commit` (default 5000) is stored as `oversized=1` and gets no `file_changes` rows.
2. Features. Each change and test pair becomes a feature vector. `testpilot features dump` writes Parquet at `features_version` 2. Opening a v4 database runs migration 4→5, which drops `feature_history_snapshot`; `features show` needs a fresh dump after that.
3. Train. LightGBM learns the `new_fail` label. `testpilot-train train` uses lightgbm 4.5.0, with a binary candidate (early stopping on auc) and a lambdarank candidate (early stopping on ndcg), 500 rounds and early stopping at 50. Select checks `schema_sha256` against the schema embedded in the Rust binary.
4. Select. One budget keeps a prefix of the ranked list. Pass exactly one of `--budget-frac`, `--max-tests`, or `--time-budget`. A test's reason is the first match of `fallback_all`, `new_or_changed`, `recent_fail`, `flaky`, `covered`, `ranked`. `--time-budget` charges only ranked picks; forced and safe-added tests are not charged. `dur` in the JSON is the last duration in milliseconds, or `-1` when unknown.
5. Safe mode. A risky diff runs every candidate. Safe mode is on unless `--no-safe` is set. `fallback_all` is set when a non-delete hunk matches the config surface (`pyproject.toml`, `pytest.ini`, `**/conftest.py`, `requirements*.txt`, `requirements*.in`, `poetry.lock`, `uv.lock`, `Pipfile.lock`, `pdm.lock`, `.github/workflows/**`), or a non-Python file inside a source package (not docs, tests, or the doc allowlist `*.md`, `*.rst`, `docs/**`, `CHANGES*`, `LICENSE*`), or the fraction of changed Python source files is above `--safe-src-frac-threshold` (default 0.30), or there are fewer than `--safe-min-candidates` (default 50) candidates. The JSON always includes `safe.enabled`.

RU — тот же смысл, те же идентификаторы не переводятся:

1. Collect. История и diff попадают в локальный SQLite. `testpilot collect git` идёт только по коммитам, достижимым из HEAD, не `--all`, от родителей к потомкам, через gix. Коммит, где файлов больше `--max-files-per-commit` (по умолчанию 5000), пишется как `oversized=1` и без строк `file_changes`.
2. Features. Пара «изменение, тест» становится вектором признаков. `testpilot features dump` пишет Parquet, `features_version` 2. Открытие базы v4 запускает миграцию 4→5, она делает `DROP TABLE feature_history_snapshot`; после этого `features show` нужен новый dump.
3. Train. LightGBM учится на метке `new_fail`. `testpilot-train train` сидит на lightgbm 4.5.0: кандидат binary с early stopping по auc и кандидат lambdarank по ndcg, 500 раундов и остановка на 50. Select сверяет `schema_sha256` со схемой, вшитой в Rust-бинарь.
4. Select. Один бюджет оставляет префикс ранжированного списка. Нужен ровно один из `--budget-frac`, `--max-tests`, `--time-budget`. Причина теста — первое совпадение из `fallback_all`, `new_or_changed`, `recent_fail`, `flaky`, `covered`, `ranked`. `--time-budget` списывает время только с ranked; force и safe-added в бюджет не входят. `dur` в JSON — длительность последнего прогона в миллисекундах, либо `-1`, если она неизвестна.
5. Safe mode. Рискованный diff запускает всех кандидатов. Safe mode включён, пока не передан `--no-safe`. `fallback_all` ставится, если hunk не-удаление попадает в config surface (`pyproject.toml`, `pytest.ini`, `**/conftest.py`, `requirements*.txt`, `requirements*.in`, `poetry.lock`, `uv.lock`, `Pipfile.lock`, `pdm.lock`, `.github/workflows/**`), или меняется не-Python файл внутри исходного пакета (не docs, не tests и не allowlist `*.md`, `*.rst`, `docs/**`, `CHANGES*`, `LICENSE*`), или доля изменённых Python-исходников выше `--safe-src-frac-threshold` (по умолчанию 0.30), или кандидатов меньше `--safe-min-candidates` (по умолчанию 50). В JSON всегда есть `safe.enabled`.

### Demo

EN

- `demo.title`: Ranked tests for a real change
- `demo.lede`: Pick a commit. The diff and the ranked tests come from a TestPilot select JSON, not from a drawing.
- `demo.badge`: PLACEHOLDER DATA
- `demo.empty`: No select run is loaded. The list stays empty until `build-demo-data` gets a ranking JSON.
- `demo.selected`: selected
- `demo.saved`: time not selected
- `demo.caught`: failure in the selection
- `demo.savedMissing`: not in the select JSON
- `demo.caughtMissing`: not in the outcomes file
- `demo.caughtYes`: yes
- `demo.caughtNo`: no
- `demo.safe`: Safe mode returned the full candidate set.
- `demo.synthetic`: SYNTHETIC
- `demo.caughtNote`: A yes or no needs an outcomes file. On a synthetic commit that file is the mutant run, so a caught failure was planted by the mutant.
- `demo.scoreNote`: Raw score, ranker {ranker}. For model this is LightGBM predict_raw. For cov_lines it is a covered-line count. Not a probability.

RU

- `demo.title`: Ранжирование на настоящем изменении
- `demo.lede`: Выберите коммит. Diff и ранжированные тесты берутся из JSON команды select, это не макет.
- `demo.badge`: PLACEHOLDER DATA
- `demo.empty`: Прогон select не загружен. Список пуст, пока `build-demo-data` не получит ranking JSON.
- `demo.selected`: выбрано
- `demo.saved`: время невыбранных
- `demo.caught`: падение внутри выбора
- `demo.savedMissing`: в JSON select этого нет
- `demo.caughtMissing`: в файле исходов этого нет
- `demo.caughtYes`: да
- `demo.caughtNo`: нет
- `demo.safe`: Safe mode вернул полный набор кандидатов.
- `demo.synthetic`: SYNTHETIC
- `demo.caughtNote`: «Да» или «нет» бывает только при файле исходов. На synthetic-коммите это прогон мутанта, и пойманное падение посажено мутацией.
- `demo.scoreNote`: Сырой score, ranker {ranker}. Для model это LightGBM predict_raw. Для cov_lines это число покрытых строк. Не вероятность.

Глоссы reason, ключ `reason.<code>`:

| code | EN | RU |
|---|---|---|
| `fallback_all` | safe mode kept every candidate | safe mode оставил всех кандидатов |
| `new_or_changed` | the test was added or its definition changed | тест добавлен или изменилось его определение |
| `recent_fail` | failed in one of the last {k} clean runs | падал в одном из {k} последних чистых прогонов |
| `flaky` | marked flaky in history | в истории помечен как flaky |
| `covered` | safe mode added it for coverage; this needs `--safe-include-covered`, which defaults to off | safe mode добавил его по покрытию; для этого нужен `--safe-include-covered`, по умолчанию он выключен |
| `ranked` | kept by score inside the budget | оставлен по score внутри бюджета |

Коды `config_surface`, `few_candidates`, `src_frac`, `non_py_source` показываются в бейдже safe mode как есть, плюс глосс:

| code | EN | RU |
|---|---|---|
| `config_surface` | project or CI config changed | изменился конфиг проекта или CI |
| `few_candidates` | fewer candidates than the minimum | кандидатов меньше порога |
| `src_frac` | too large a share of Python source files changed | изменилась слишком большая доля Python-исходников |
| `non_py_source` | a non-Python file inside a source package changed | изменился не-Python файл внутри исходного пакета |

`{k}` в `recent_fail` — это `safe.thresholds.recent_k` из того же select JSON. Если поля нет, глосс не показывается, остаётся сырой код. Число 5 подставлять по памяти нельзя, даже хотя default CLI именно 5.

Неизвестный код: сырая строка и класс янтарного предупреждения, без выдуманного перевода.

### Bootstrap

EN

- `bootstrap.title`: Cold start when history has no failures
- `bootstrap.lede`: `testpilot bootstrap` backfills real pytest runs, adds mutation labels, trains, and writes a verdict.
- `bootstrap.gateTitle`: The gate

RU

- `bootstrap.title`: Холодный старт, когда в истории нет падений
- `bootstrap.lede`: `testpilot bootstrap` догоняет реальные прогоны pytest, добавляет метки мутаций, обучает модель и записывает verdict.
- `bootstrap.gateTitle`: Gate

Шаги, порядок из `STEP_IDS`. Имя шага не переводится.

EN

1. `cleanup`. Removes leftover bootstrap worktrees and virtualenvs from an interrupted run.
2. `init`. Creates the SQLite database and appends `.testpilot/` to `.git/info/exclude`, not to the tracked `.gitignore`.
3. `collect_git`. Indexes the target repository history into that database.
4. `backfill`. Runs pytest on the planned SHAs that do not already have a finished clean real run with test results. The default set is the last `max(N, 20)` first-parent commits, union the sampled commits. An incompatible historical pytest is restored to pytest 8.3.4, pluggy `>=1.5,<2`, and py `>=1.11,<2`.
5. `mutate`. Applies AST mutants, `K` per sampled commit. Workers use separate virtualenvs. `--jobs` is at most 4. Synthetic runs are stored with `synthetic=1`.
6. `features_dump`. Dumps pairs with mutant rows included. The step is done when the database fingerprint matches, not when a file mtime changes.
7. `train`. Runs `testpilot-train train` and always passes `--allow-cold-baseline`.
8. `gate`. Reads `bootstrap.verdict` from `model.json`.
9. `finalize`. Writes `bootstrap_report.json` and `bootstrap_report.md`.

RU

1. `cleanup`. Убирает worktree и virtualenv, оставшиеся от прерванного прогона.
2. `init`. Создаёт базу SQLite и дописывает `.testpilot/` в `.git/info/exclude`, не в отслеживаемый `.gitignore`.
3. `collect_git`. Кладёт историю целевого репозитория в эту базу.
4. `backfill`. Запускает pytest на тех SHA из плана, для которых ещё нет законченного чистого реального прогона с результатами тестов. Набор по умолчанию — последние `max(N, 20)` коммитов first-parent плюс выбранные коммиты. Несовместимый исторический pytest возвращается к pytest 8.3.4, pluggy `>=1.5,<2` и py `>=1.11,<2`.
5. `mutate`. Ставит AST-мутанты, `K` штук на выбранный коммит. У воркеров отдельные virtualenv. `--jobs` не больше 4. Синтетические прогоны пишутся с `synthetic=1`.
6. `features_dump`. Dump с мутантными строками. Шаг считается сделанным по fingerprint базы, а не по mtime файла.
7. `train`. Запускает `testpilot-train train` и всегда передаёт `--allow-cold-baseline`.
8. `gate`. Читает `bootstrap.verdict` из `model.json`.
9. `finalize`. Пишет `bootstrap_report.json` и `bootstrap_report.md`.

Пример команды из README week 6, как пример, не как значения по умолчанию:

```bash
cargo build -p testpilot-cli --release
./target/release/testpilot bootstrap --repo "$REPO" --out "$MODEL_DIR" \
  --max-commits 200 --mutants-per-commit 4 --jobs 2 --seed 42
```

Рядом, отдельной фразой, фактические default из clap на `20f45a9`: `--max-commits` 200, `--mutants-per-commit` 4, `--jobs` 1, `--seed` 42, `--suite click`, `--resume` включён. `--jobs` в примере равен 2, default в CLI равен 1. Верхняя граница `--jobs` — 4.

EN `bootstrap.defaults`: The README example passes `--jobs 2`. The CLI default is `--jobs 1`. Defaults are N=200, K=4, seed 42, suite `click`, resume on. `--jobs` cannot exceed 4.
RU `bootstrap.defaults`: В примере README стоит `--jobs 2`. Default в CLI — `--jobs 1`. Остальные default: N=200, K=4, seed 42, suite `click`, resume включён. `--jobs` не больше 4.

Gate, оба языка, идентификаторы одинаковые. Английский абзац и русский абзац:

EN `bootstrap.gate`:

`--allow-cold-baseline` belongs to `testpilot-train train` and is off by default. Without it, fewer than 10 distinct `commit_idx` values raise `InsufficientCommitsError` and the process exits 2 before `chrono_split`. `testpilot bootstrap` always passes the flag. With the flag, that cold case writes `model.json` with `ranker=cov_lines`, `bootstrap.verdict=use_baseline`, `reason=insufficient_commit_idx`, and no `model.txt`. That reason is not a gate result. `chrono_split` does not start.

After a split, the gate looks only at the synthetic TEST cluster against the `cov_lines` baseline. Validation metrics are reported and do not decide. `use_model` happens only when `ci_low >= 0` and the slice is sufficient: at least 10 distinct C and at least 20 kill mutants. Otherwise `verdict=use_baseline` and `ranker=cov_lines`, with reason `insufficient_test_C` (n_C < 10), `data_insufficient`, `no_test_signal` (no CI), or `synth_test_ci_low_lt_0`. Accept reason is `synth_test_ci_low_ge_0`. When the ranker is `cov_lines`, select orders by `cov_lines`, then `cov_func`, then `test_id`. Safe mode still applies. A larger N or more history is a new run with `--force`.

RU `bootstrap.gate`:

`--allow-cold-baseline` — флаг `testpilot-train train`, по умолчанию выключен. Без него меньше 10 различных `commit_idx` дают `InsufficientCommitsError` и выход 2 ещё до `chrono_split`. `testpilot bootstrap` всегда передаёт этот флаг. С флагом холодный случай пишет `model.json` с `ranker=cov_lines`, `bootstrap.verdict=use_baseline`, `reason=insufficient_commit_idx` и без `model.txt`. Это не результат gate. `chrono_split` при этом не стартует.

После split gate смотрит только на синтетический TEST-кластер против бейзлайна `cov_lines`. Метрики validation печатаются и решение не принимают. `use_model` бывает только если `ci_low >= 0` и срез достаточный: не меньше 10 различных C и не меньше 20 kill-мутантов. Иначе `verdict=use_baseline` и `ranker=cov_lines`, причина `insufficient_test_C` (n_C < 10), `data_insufficient`, `no_test_signal` (нет CI) или `synth_test_ci_low_lt_0`. Причина принятия — `synth_test_ci_low_ge_0`. При `ranker=cov_lines` select сортирует по `cov_lines`, затем `cov_func`, затем `test_id`. Safe mode при этом работает. Больший N или новая история — это новый запуск с `--force`.

Отдельная строка, потому что она в README и легко ошибиться: на Click без `less` в PATH падают pager-тесты на каждом коммите. Это устойчивые падения, не `new_fail`.

EN `bootstrap.less`: On Click, `less` has to be on PATH. Without it, pager tests fail on every commit. Those failures are persistent, not `new_fail`.
RU `bootstrap.less`: Для Click в PATH нужен `less`. Без него pager-тесты падают на каждом коммите. Это устойчивые падения, не `new_fail`.

### Install

Три колонки. В каждой сначала честная фраза «из исходников», потом команды из README / example workflow.

EN `install.title`: Build from this repo
RU `install.title`: Сборка из этого репозитория

EN `install.lede`: Nothing here is published to PyPI or crates.io. `pip install testpilot`, `pytest-pilot`, and `testpilot-ai` are other projects. The commands below are the ones in this repository's README.
RU `install.lede`: В PyPI и crates.io этого проекта нет. `pip install testpilot`, `pytest-pilot` и `testpilot-ai` — другие проекты. Ниже команды из README этого репозитория.

CLI, обе локали, блок кода общий:

```bash
cargo build -p testpilot-cli --release
export PATH="$PWD/target/release:$PATH"
testpilot --version
```

EN `install.cliNote`: Workspace version 0.1.0. `--version` also prints the git SHA embedded at build time.
RU `install.cliNote`: Версия workspace — 0.1.0. `--version` дополнительно печатает git SHA, вшитый при сборке.

Plugin:

```bash
pip install -e python/pytest-testpilot
pytest --testpilot --testpilot-db=/abs/path/to/db.sqlite
```

EN `install.pluginNote`: Package `pytest-testpilot` 0.1.0, Python >= 3.10. Use the equals form so pytest does not treat the path as a rootdir. Select is a separate flag and needs exactly one budget, for example `--testpilot-select --testpilot-model "$MODEL" --testpilot-budget-frac 0.1`.
RU `install.pluginNote`: Пакет `pytest-testpilot` 0.1.0, Python >= 3.10. Форма с `=` нужна, чтобы pytest не принял путь за rootdir. Select — отдельный флаг и требует ровно один бюджет, например `--testpilot-select --testpilot-model "$MODEL" --testpilot-budget-frac 0.1`.

Action. Пример из `docs/examples/testpilot-ci.yml` на `20f45a9`:

```yaml
- uses: actions/checkout@v4
  with:
    fetch-depth: 0
- uses: ./.github/actions/testpilot
  with:
    model-dir: path/to/your/model
    budget-frac: '0.15'
    safe: 'true'
    pytest-args: '-q'
```

EN `install.actionNote`: This composite action lives at `.github/actions/testpilot`. The checked-in example runs it from this repository. On a push to the default branch it runs full pytest, then `collect git`, then `features dump`. On a pull request it runs `--testpilot-select` and requires exactly one of `budget-frac`, `max-tests`, or `time-budget`. It is not a Marketplace action. In another repository the same file builds `testpilot-cli` only if that repo contains the crates, and it pip-installs `pytest-testpilot` only if `python/pytest-testpilot/pyproject.toml` is in the workspace. Otherwise it installs pytest and coverage and does not install this plugin. Pass `testpilot-bin` if you already have a binary.
RU `install.actionNote`: Composite action лежит в `.github/actions/testpilot`. Пример в репозитории запускает её из этого же репозитория. Push в default branch: полный pytest, затем `collect git`, затем `features dump`. Pull request: `--testpilot-select` и ровно один из `budget-frac`, `max-tests`, `time-budget`. В Marketplace её нет. В чужом репозитории этот файл соберёт `testpilot-cli`, только если там есть эти crates, и поставит `pytest-testpilot`, только если в workspace есть `python/pytest-testpilot/pyproject.toml`. Иначе ставятся pytest и coverage, без этого плагина. Свой бинарь передаётся через `testpilot-bin`.

Train, коротко под CLI, потому что bootstrap его вызывает, отдельной колонки нет:

```bash
pip install -e 'python/testpilot-train[dev]'
```

EN `install.trainNote`: Package `testpilot-train` 0.1.0 pins lightgbm==4.5.0. Same rule: install from this checkout, not from PyPI.
RU `install.trainNote`: Пакет `testpilot-train` 0.1.0 фиксирует lightgbm==4.5.0. То же правило: ставить из этого checkout, не из PyPI.

### Measurements

EN

- `measurements.title`: Week 6 result
- `measurements.lede`: The model is still level with the coverage baseline. The default ranker is cov_lines.
- `measurements.pooledLead`: Pooled TEST delta versus cov_lines. It does not establish an advantage across held-out repositories.
- `measurements.pooledValue`: Δ {delta}, ci95 [{lo}, {hi}], n={n}, n_C={nC}
- `measurements.macro`: macro
- `measurements.col.repo`: Held-out repository
- `measurements.col.model`: Model recall@10%
- `measurements.col.baseline`: cov_lines
- `measurements.col.delta`: Δ
- `measurements.col.gate`: Bootstrap gate

RU

- `measurements.title`: Итог недели 6
- `measurements.lede`: Модель пока на уровне coverage-baseline, по умолчанию cov_lines.
- `measurements.pooledLead`: Pooled TEST delta против cov_lines. Это не преимущество на held-out репозиториях.
- `measurements.pooledValue`: Δ {delta}, ci95 [{lo}, {hi}], n={n}, n_C={nC}
- `measurements.macro`: macro
- `measurements.col.repo`: Held-out репозиторий
- `measurements.col.model`: Model recall@10%
- `measurements.col.baseline`: cov_lines
- `measurements.col.delta`: Δ
- `measurements.col.gate`: Bootstrap gate

Ограничения, оба языка, массивом `measurements.limits`: dry-run занизил измеренное время на 32–42%; у Click и attrs в TEST n_C=5, это меньше порога gate 10. Английские строки — те же факты. Числа таблицы считаются в компоненте из `interpretation`, не записаны в словарь.

### Comparison

EN

- `comparison.title`: How the other selectors decide
- `comparison.lede`: TestPilot is offline and MIT-licensed. The score is computed on your machine: a LightGBM predict_raw value, or a covered-line count when the gate selects the cov_lines ranker. Nothing is sent out. The rows below are the other tools' own descriptions, not a benchmark.
- `comparison.col.tool`: Tool
- `comparison.col.approach`: Approach
- `comparison.col.source`: Source

RU

- `comparison.title`: Как выбирают другие
- `comparison.lede`: TestPilot работает офлайн, лицензия MIT. Score считается на вашей машине: LightGBM predict_raw или число покрытых строк, если gate выбрал ranker cov_lines. Наружу ничего не отправляется. В строках — формулировки самих инструментов, это не бенчмарк.
- `comparison.col.tool`: Инструмент
- `comparison.col.approach`: Подход
- `comparison.col.source`: Источник

Подходы:

EN

- `cmp.testmon`: pytest-testmon records which lines each test hit, using coverage.py, and stores that in `.testmondata`. On the next run it deselects tests whose file and function checksums did not change. Tests that failed last time stay selected. This is local.
- `cmp.smart`: CloudBees Smart Tests, formerly Launchable, ranks tests by semantic similarity between the change and the test source. The product overview says the ranking uses large language models. The selection write-up says it does not use code coverage. A subset is requested from their service with `smart-tests subset`.
- `cmp.datadog`: Datadog Test Impact Analysis collects per-test coverage and sends it to Datadog. A test is skipped only when a previous passing run exists on a commit whose covered files and tracked files match the current commit.
- `cmp.meta`: There is no public Meta product by this name. Machalica, Samylkin, Porth, and Chandra describe a classifier trained on historical outcomes in the Facebook monorepo (ICSE-SEIP 2019). Figures in that paper are about Facebook's deployment. They are not TestPilot results.

RU

- `cmp.testmon`: pytest-testmon запоминает, какие строки задел каждый тест, через coverage.py, и хранит это в `.testmondata`. На следующем запуске снимает тесты, у которых не изменились контрольные суммы файла и функции. Тесты, которые упали в прошлый раз, остаются. Это локальный режим.
- `cmp.smart`: CloudBees Smart Tests, раньше Launchable, ранжирует тесты по смысловой близости изменения и исходника теста. В обзоре продукта сказано, что ранжирование использует большие языковые модели. В описании отбора сказано, что покрытие кода не используется. Подмножество запрашивается у их сервиса командой `smart-tests subset`.
- `cmp.datadog`: Datadog Test Impact Analysis собирает покрытие каждого теста и отправляет его в Datadog. Тест пропускается только если есть прошлый успешный прогон на коммите, где покрытые файлы и отслеживаемые файлы совпали с текущим коммитом.
- `cmp.meta`: Публичного продукта Meta с таким именем нет. Machalica, Samylkin, Porth и Chandra описывают классификатор на исторических исходах в монорепозитории Facebook (ICSE-SEIP 2019). Цифры в статье относятся к их внедрению. Это не результаты TestPilot.

Ссылки, колонка Source, одинаковые для обоих языков:

- pytest-testmon: https://github.com/tarpas/pytest-testmon
- CloudBees, две ссылки в одной ячейке: https://docs.cloudbees.com/docs/cloudbees-smart-tests/latest/ и https://docs.cloudbees.com/docs/cloudbees-smart-tests/latest/features/predictive-test-selection/how-we-select-tests
- Datadog: https://docs.datadoghq.com/tests/test_impact_analysis/how_it_works/
- Meta: https://arxiv.org/abs/1810.05286

## 8. Демо: формат данных

Файл сайта: `site/src/data/demo.json`. Его пишет только `site/scripts/build-demo-data`. Руками не редактировать.

Первая версия — результат `--placeholder`:

```json
{
  "placeholder": true,
  "source": null,
  "cases": []
}
```

Пока `placeholder` истинно, секция показывает бейдж `PLACEHOLDER DATA` и пустое состояние. Коммитов, diff и тестов на экране нет.

### Вход скрипта для настоящего прогона

Каталог, который передаётся `--input`. Скрипт ничего не додумывает. Нет поля — выход 2, `demo.json` не меняется (запись во временный файл и `rename` только после всех проверок).

```text
input/
  source.json
  model.json
  report.json
  rankings/
    <40-hex head sha>.json
  candidates/            необязательно
    <same sha>.json
  outcomes/              необязательно
    <same sha>.json
```

`bootstrap_report.json` в каталоге может лежать, но для демо не обязателен. Если он есть, `fingerprint.bin_version` должен совпасть с `source.json.testpilot_version`, а `fingerprint.head_sha` — с `source.json.target_sha`. Иначе выход 2.

`source.json`, все поля обязательны, строки не пустые:

```json
{
  "target_repo": "https://github.com/pallets/click",
  "target_sha": "934813e4d421071a1b3db3973c02fe2721359a6e",
  "testpilot_version": "0.1.0 (20f45a96fdd2)",
  "clone": "/absolute/path/to/the/clone",
  "model_measured_at": "2026-11-20T06:59:03Z"
}
```

`generated_at` во входном файле нет. Скрипт пишет в выход `source.generated_at` время своего запуска, UTC, `YYYY-MM-DDTHH:MM:SSZ`. `model_measured_at` необязателен: если во входе непустая строка, она копируется как есть. Для click это `env.date_utc` из `bootstrap-w6-click-jinja-attrs.json`, время снимка окружения, не время select. На странице подпись `demo.measured` называет это поле и коммит `20f45a9`.

Проверки:

- `clone` — git-репозиторий, `git -C clone rev-parse HEAD` равен `target_sha`. URL origin не сравнивается с `target_repo`: origin может быть локальным путём, а `target_repo` в выходе остаётся строкой из `source.json`.
- `model.json` — объект с непустыми `ranker` и `schema_sha256`. `report.json` — объект. Скрипт не вытаскивает из них метрики в демо.
- Каждый файл в `rankings/` — JSON команды `testpilot select --format json`. Обязательные поля: `model_sha256`, `features_version`, `base`, `head`, `base_run_sha`, `n_candidates`, `n_selected`, `n_forced`, `n_safe_added`, `n_ranked`, `budget` (`kind`, `value`), `safe` (`enabled`, `fallback_all`, `reasons`, `thresholds`), `tests` (массив `{test_id, score, rank, dur, reason}`). Поле `ranker` необязательно. Если в select JSON `ranker` нет или он пустой, берётся `model.json` → `ranker`. Если нет ни там, ни там — выход 2. Если оба заданы и строки различаются — выход 2.
- Имя файла равно полю `head`, оба — 40 шестнадцатеричных символов в нижнем регистре. `WORKTREE` и короткие SHA отвергаются.
- `len(tests) == n_selected`. Иначе выход 2.
- `head` и `target_sha` есть в `clone` (`git cat-file -e`).
- `score` и `dur` сохраняются как исходные JSON-литералы, без прохода через бинарный float туда и обратно.

Откуда diff и подпись коммита, только git этого clone:

- subject: `git log -1 --format=%s <head>`
- author date: `git log -1 --format=%aI <head>`
- diff: `git diff --unified=3 <base> <head>`, где `base` и `head` взяты из select JSON. Если `base` не резолвится в этом clone, выход 2.
- `synthetic`: true, если `git -C clone for-each-ref --points-at <head> refs/testpilot/mutants` печатает хотя бы один ref. Иначе false. Других эвристик нет.

`candidates/<sha>.json`, если есть: `{"tests": [{"test_id": "...", "dur": 12}]}`. Это полный набор кандидатов, например второй select с `--budget-frac 1 --no-safe`. `saved_ms` = сумма `dur` тех кандидатов, чьих `test_id` нет среди выбранных. Если любого нужного `dur` нет или он `-1`, `saved_ms` равен `null`. Нет каталога `candidates` — `saved_ms` равен `null`. Не оценивать остаток по среднему и не вычитать сумму выбранных из ничего.

`outcomes/<sha>.json`: `{"complete": true, "tests": {"nodeid": "pass"}}`. Допустимые исходы: `pass`, `fail`, `error`, `skip`.

- `caught: true` — у выбранного теста исход `fail` или `error`.
- `caught: false` — `complete` истинно, среди всех записанных исходов есть `fail` или `error`, и ни один такой id не входит в выбор.
- `caught: null` — файла нет, `complete` ложно, или выбранные тесты прошли, но про невыбранные неизвестно. Нельзя ставить `false` в этом случае.

`safe_fallback` копируется из `safe.fallback_all`. `safe_reasons` копируется из `safe.reasons` как массив строк.

Выходной case:

```json
{
  "placeholder": false,
  "source": {
    "target_repo": "https://github.com/pallets/click",
    "target_sha": "934813e4d421071a1b3db3973c02fe2721359a6e",
    "testpilot_version": "0.1.0 (20f45a96fdd2)",
    "generated_at": "2026-11-21T21:16:23Z",
    "model_measured_at": "2026-11-20T06:59:03Z"
  },
  "cases": [
    {
      "head": "0123456789abcdef0123456789abcdef01234567",
      "base": "the base string from the select JSON",
      "subject": "from git log",
      "authored_at": "from git %aI",
      "diff": "from git diff",
      "n_selected": 0,
      "n_candidates": 0,
      "saved_ms": null,
      "caught": null,
      "synthetic": false,
      "safe_fallback": false,
      "safe_reasons": [],
      "recent_k": null,
      "ranker": "model",
      "model_sha256": "from the select JSON",
      "tests": [
        {
          "test_id": "copied",
          "score": "1.25",
          "rank": 1,
          "dur": "10",
          "reason": "ranked"
        }
      ]
    }
  ]
}
```

Порядок cases — порядок имён файлов. Скрипт не выбирает «интересные» коммиты и не отбрасывает лишние.

Интерфейс: список коммитов — кнопки. `aria-pressed` у текущей. Клавиатура: Tab, Enter и Space. Смена коммита обновляет diff, таблицу тестов и три метрики. Регион метрик — `aria-live="polite"`. При `synthetic: true` на коммите и рядом с метрикой «падение» виден бейдж `demo.synthetic`, и под метрикой виден `demo.caughtNote`. Пойманное падение показывается и для обычных коммитов, если есть outcomes, но пометка synthetic объясняет, что «да» на мутанте — это посаженное падение. При `safe_fallback` виден бейдж safe mode со списком `safe_reasons`. Метрики: `n_selected` / `n_candidates` как в JSON; `saved_ms` как миллисекунды либо подпись `savedMissing`; `caught` — да, нет, или `caughtMissing`. Score печатается строкой из JSON. Рядом `demo.scoreNote` с подставленным `ranker`. Слов «probability», «вероятность» и знака процента у score нет. Reason в UI — код из JSON (`new_or_changed`, `recent_fail`, `flaky`, `covered`, `ranked`, `fallback_all`) и глосс из словаря `reason.<code>`.

Пустой `cases` при `placeholder: false` — выход 2. Настоящий файл без флага `placeholder: false` на сайт не кладётся.

Неотслеживаемые отчёты в `docs/measurements/bootstrap-click/` и `bootstrap-jinja/` этому входу не удовлетворяют: нет `rankings/`, нет `source.json`, путь `repo` внутри отчёта — локальный `.cache/measurements/...`. Их можно будет скормить скрипту только после отдельного `testpilot select --format json` по тем же SHA и оформления каталога как выше.

## 9. Замеры, которые можно показать

Файл `site/src/data/measurements.json` хранит объект `interpretation` целиком, как в `docs/measurements/bootstrap-w6-click-jinja-attrs.json` на `20f45a96fdd23f1a541308cde25f8bcbc562722e`. Ряды mut2 и оценки dry-run на страницу не попадают.

Ссылки, обе на этот SHA:

- `https://github.com/bocchisad/TestPilot/blob/20f45a96fdd23f1a541308cde25f8bcbc562722e/docs/measurements/bootstrap-w6-click-jinja-attrs.json`
- `https://github.com/bocchisad/TestPilot/blob/20f45a96fdd23f1a541308cde25f8bcbc562722e/docs/measurements/loro-w6-click-jinja-attrs/report.md`

Таблица: по строке на `interpretation.per_repo` (attrs, click, jinja) и строка macro. Колонки — recall@10% модели, recall@10% `cov_lines`, дельта, gate. Gate рисуется как `gate (gate_reason, n_C=test_n_C)`. У macro этой ячейки нет.

Округление в коде страницы, не в JSON: знак берётся у исходного числа, `Math.round(Math.abs(n) * 1000) / 1000`, затем три знака. Отрицательные — с юникод-минусом `−`, положительная дельта — с `+`. Так `0.8542713567839196` становится `0.854`, а `−0.020575126341097427` становится `−0.021`. Полные значения остаются только в связанном JSON.

Под таблицей — pooled TEST: дельта, ci95, n, n_C из `pooled_test_delta`, `pooled_test_ci95`, `pooled_test_n`, `pooled_test_n_C`, и фраза, что это не преимущество на held-out репозиториях. Дальше два ограничения из `interpretation.limitations`: dry-run занизил время на 32–42%; у Click и attrs TEST n_C=5, меньше порога 10.

Вывод на странице не говорит, что модель лучше baseline: «The model is still level with the coverage baseline. The default ranker is cov_lines.»

## 10. Файлы, которые появятся после «ок»

Только эти пути. Rust, Python проекта, `ci.yml` и чужие ветки не меняются.

```text
.github/workflows/site.yml
site/package.json
site/package-lock.json
site/astro.config.mjs
site/tsconfig.json
site/public/fonts/JetBrainsMono-Regular.woff2
site/public/fonts/JetBrainsMono-Bold.woff2
site/public/fonts/OFL.txt
site/public/favicon.svg
site/src/styles/global.css
site/src/i18n/types.ts
site/src/i18n/en.ts
site/src/i18n/ru.ts
site/src/data/demo.json
site/src/data/measurements.json
site/src/layouts/Base.astro
site/src/components/Header.astro
site/src/components/Footer.astro
site/src/components/Hero.astro
site/src/components/Problem.astro
site/src/components/Pipeline.astro
site/src/components/Demo.astro
site/src/components/Bootstrap.astro
site/src/components/Install.astro
site/src/components/Measurements.astro
site/src/components/Comparison.astro
site/src/pages/index.astro
site/src/pages/ru/index.astro
site/src/scripts/demo.ts
site/scripts/build-demo-data
site/scripts/test_build_demo_data.py
```

`favicon.svg` — монограмма из `--green` на `--bg`, без внешних запросов. `site/SPEC.md` и `site/PLAN.md` уже есть.

`build-demo-data` — исполняемый Python 3 со shebang `#!/usr/bin/env python3`, только стандартная библиотека. Зависимости сайта он не ставит.

## 11. Вне скоупа

- Публикация пакетов, правка README, перенос week 6 в `main`.
- Блог, доки на самом сайте, поиск, тёмная и светлая темы, переключатель темы.
- Аналитика, cookies, формы, комментарии.
- Живой запуск TestPilot в браузере.
- Выдуманные метрики, в том числе цифры из статьи Meta как будто они про TestPilot.

## 12. Приёмка

После реализации, до сообщения «готово»:

1. `npm run build` в `site/` без ошибок и без предупреждений Astro.
2. Preview на `{base}/` и `{base}/ru/`. Корневой URL dev-сервера без base не считается страницей. По `site/dist` поиск абсолютных `href="/` и `src="/`, у которых сразу после слэша нет `TestPilot/`, пустой. Команда: `rg -n --pcre2 '(href|src)="/(?!TestPilot/)' site/dist` — нет совпадений.
3. Lighthouse по preview для performance, accessibility, best practices, SEO. В отчёт — фактические числа, не цель.
4. Ширины 360, 768 и 1440. Скриншоты обеих локалей. На 360 нет горизонтального скролла страницы.
5. Все ссылки из секций и футера отвечают не ошибочным статусом. Якоря существуют на обеих локалях.
6. EN/RU: у каждой секции текст из своего словаря. Демо с клавиатуры меняет выбранный коммит, когда cases не пустые. На placeholder клавиатурный список пуст и это видно.
7. `python3 site/scripts/test_build_demo_data.py` зелёный.
8. В отчёте: SHA коммита сайта, список файлов, чем проверялось, что осталось placeholder. Коммит и пуш — только по отдельной просьбе.
