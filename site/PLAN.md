# TestPilot site implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Статический лендинг TestPilot с двумя языками, workflow GitHub Pages и демо. Первая сборка кода может быть с `placeholder: true`. На `main` и в Pages сайт попадает только после задачи 10, когда `demo.json` собран из select.

**Architecture:** Astro собирает одну страницу на локаль из словарей `en.ts` / `ru.ts` и двух JSON. Демо — один vanilla-модуль, который только переключает уже вшитые cases. Числа и команды не придумываются: они либо дословно из `site/SPEC.md`, либо отсутствуют.

**Tech Stack:** Astro (версия, которую вернёт `npm view astro version` в задаче 1), TypeScript, Python 3 stdlib для `build-demo-data`, GitHub Actions `upload-pages-artifact` и `deploy-pages`.

## Global Constraints

- Работать только в worktree `/Users/gothscum/Documents/проекты/TestPilot-site`, ветка `feat/site`. Не переключать `/Users/gothscum/Documents/проекты/TestPilot` и не трогать его грязные файлы.
- Менять только пути из раздела 10 SPEC плюс уже существующие `site/SPEC.md` и `site/PLAN.md`. Не править Rust, Python проекта, `.github/workflows/ci.yml`.
- Не пушить, не мержить и не коммитить, пока mell отдельно не попросит.
- `base` = `/TestPilot`. `site` = `https://bocchisad.github.io`. `trailingSlash: 'always'`.
- Английский без префикса, русский на `/ru/`. Любая видимая строка — из словаря, текст секций копируется из раздела 7 SPEC дословно.
- Цвета только из раздела 5 SPEC. Янтарь только у PLACEHOLDER, панели safe mode и неизвестного reason. Фокус — `--green`.
- Шрифт mono — файлы JetBrains Mono в `site/public/fonts/`, плюс `OFL.txt`. В HTML нет CDN, аналитики и чужих запросов.
- `site/src/data/demo.json` собран из select по click: `placeholder: false`. `generated_at` — время запуска скрипта. `model_measured_at` — `env.date_utc` артефакта.
- Замеры — раздел 9 SPEC. Таблица из `interpretation`, округление до трёх знаков в коде. Ссылки на SHA `20f45a96fdd23f1a541308cde25f8bcbc562722e`, не на `blob/main`. Строк mut2 и dry-run нет.
- Score не называть вероятностью или процентом. `hero.lede` EN: «ranks pytest tests by a failure score learned from your history». RU: «ранжирует тесты по score, обученному на истории».
- Select JSON без `ranker` (сборка с `main`) не ошибка: ranker берётся из `model.json`.
- `<html lang>`, `hreflang` `en` / `ru` / `x-default`, переключатель языка сохраняет `location.hash`.
- `npm run build` без ошибок и без предупреждений Astro.

SPEC: `site/SPEC.md`. Расхождений с ним быть не должно. Если спека молчит, не додумывать продукт — остановиться.

---

### Task 1: Каркас Astro

**Files:**
- Create: `site/package.json`, `site/package-lock.json`, `site/astro.config.mjs`, `site/tsconfig.json`
- Create: `site/src/pages/index.astro` (временная пустая страница, её заменит задача 4)

**Interfaces:**
- Consumes: ничего
- Produces: `npm run build` пишет `site/dist/` с префиксом `/TestPilot/`. Конфиг экспортирует `site`, `base`, `trailingSlash`, i18n `en`/`ru` с `prefixDefaultLocale: false`.

- [ ] **Step 1:** В каталоге `site/` поставить последнюю стабильную Astro и TypeScript, зафиксировать lockfile.

Run: `npm view astro version`
Записать эту версию в `package.json` без диапазона `^`.

- [ ] **Step 2:** Заполнить `astro.config.mjs` значениями из раздела 6 SPEC. `src/pages/index.astro` содержит один `h1` из словаря ещё нельзя — словаря нет. Временный текст `TestPilot` допустим только до задачи 3 и должен исчезнуть в задаче 4.

- [ ] **Step 3:** Сборка.

Run: `npm run build`
Expected: exit 0. В stderr нет строк warning от Astro. В `dist/` есть префикс пути `/TestPilot/`.

- [ ] **Step 4:** Не коммитить.

### Task 2: Токены, шрифт, каркас страницы

**Files:**
- Create: `site/public/fonts/JetBrainsMono-Regular.woff2`
- Create: `site/public/fonts/JetBrainsMono-Bold.woff2`
- Create: `site/public/fonts/OFL.txt`
- Create: `site/public/favicon.svg`
- Create: `site/src/styles/global.css`
- Create: `site/src/layouts/Base.astro`
- Create: `site/src/components/Header.astro`
- Create: `site/src/components/Footer.astro`

**Interfaces:**
- Consumes: токены раздела 5 SPEC
- Produces: `Base.astro` принимает `locale: 'en' | 'ru'` и `title: string`, подключает `global.css`, ставит `<html lang>`. `Header` и `Footer` принимают тот же `locale` и рисуют ссылки из раздела 4 SPEC. Компоненты пока могут получить строки аргументом; с задачи 3 строки приходят только из словаря.

- [ ] **Step 1:** Скачать JetBrains Mono с релиза https://github.com/JetBrains/JetBrainsMono (OFL-1.1), положить Regular и Bold woff2 и текст лицензии. Проверить, что в woff2 есть кириллица: отрендерить строку «Пайплайн» этим шрифтом не должен заменять её на `.notdef`. Если в выбранном файле кириллицы нет, взять файл того же семейства, где она есть, и записать имя файла в комментарии CSS.

- [ ] **Step 2:** `global.css` объявляет переменные из раздела 5 и `@font-face` через `import.meta.env.BASE_URL`. Никакого `url(https://...)`.

- [ ] **Step 3:** `Base.astro` задаёт фон `--bg`, цвет `--ink`, шрифт mono для страницы, sans для абзацев через класс `.prose`. `prefers-reduced-motion: reduce` отключает `scroll-behavior`.

- [ ] **Step 4:** Шапка и футер по разделу 4. На ширине 360px все четыре ссылки шапки доступны, `display: none` на них нет. Фокус-стиль общий: `outline: 2px solid var(--green); outline-offset: 4px`.

- [ ] **Step 5:** `npm run build` exit 0, stderr без warning. В `dist` есть `fonts/JetBrainsMono-Regular.woff2` и favicon.

- [ ] **Step 6:** Не коммитить.

### Task 3: Словари

**Files:**
- Create: `site/src/i18n/types.ts`
- Create: `site/src/i18n/en.ts`
- Create: `site/src/i18n/ru.ts`

**Interfaces:**
- Consumes: раздел 7 SPEC
- Produces: `export const messages` типа `Messages` в обоих файлах. `en.ts` — источник типа (`satisfies Messages` недостаточно, если ключи разъедутся: `ru.ts` аннотирован `const messages: Messages`). Функция `t(locale)` не нужна, страницы импортируют нужный словарь.

Ключи, которые должны существовать (имена можно вложить объектами, но строка из SPEC не перефразируется):

`brand`, `nav.pipeline`, `nav.demo`, `nav.install`, `nav.github`, `lang.en`, `lang.ru`, `footer.docs`, `footer.license`, `hero.*`, `problem.*`, `pipeline.title`, `pipeline.steps[5]` (title + body), `demo.*`, `reason.*`, `safeReason.*`, `bootstrap.*` включая `steps[9]` и `gate`, `install.*`, `measurements.*`, `comparison.*`, `cmp.testmon`, `cmp.smart`, `cmp.datadog`, `cmp.meta`.

Подписи строк таблицы замеров — ключи `meas.*` из раздела 9 SPEC, дословно, на обоих языках. Сами числа в словарь не класть.

- [ ] **Step 1:** Перенести английские строки из SPEC в `en.ts`.

- [ ] **Step 2:** Перенести русские строки в `ru.ts` с аннотацией типа `Messages`.

- [ ] **Step 3:** `npm run build` падает, если из `ru.ts` удалён один ключ. Вернуть ключ. Сборка снова exit 0 без warning.

- [ ] **Step 4:** Поиск по `site/src` не находит пользовательский текст вне `i18n/` и JSON. Команды и идентификаторы (`testpilot select`, reason-коды) живут в словаре как часть дословной строки SPEC, не в разметке.

Run: `rg -n "TestPilot|pytest|Safe mode" site/src --glob '!site/src/i18n/**' --glob '!site/src/data/**'`
Expected: совпадений нет. Запускать после задачи 4 повторно.

- [ ] **Step 5:** Не коммитить.

### Task 4: Статические секции

**Files:**
- Create: `site/src/components/Hero.astro`
- Create: `site/src/components/Problem.astro`
- Create: `site/src/components/Pipeline.astro`
- Create: `site/src/components/Bootstrap.astro`
- Create: `site/src/components/Install.astro`
- Create: `site/src/components/Measurements.astro`
- Create: `site/src/components/Comparison.astro`
- Modify: `site/src/pages/index.astro`
- Create: `site/src/pages/ru/index.astro`
- Create: `site/src/data/measurements.json`
- Modify: `site/src/components/Header.astro`, `Footer.astro` так, чтобы они читали словарь

**Interfaces:**
- Consumes: `Messages`, `measurements.json`
- Produces: обе страницы рендерят секции в порядке раздела 4 SPEC. `index.astro` передаёт `en`, `ru/index.astro` передаёт `ru`. Общего третьего языка нет.

`measurements.json`:

```json
{
  "git_sha": "d90754e844f010870d8d12f046bf1e19fd72cd14",
  "rows": [
    {
      "id": "mut2-preset",
      "value": "80x5",
      "href": "https://github.com/bocchisad/TestPilot/blob/d90754e844f010870d8d12f046bf1e19fd72cd14/docs/measurements/mut2-80x5-click-jinja.json"
    }
  ]
}
```

Остальные ряды — по таблице раздела 9. Для paired delta `value` — одна строка, собранная из трёх чисел файла без округления: mean, затем ci95, затем n. Для dry-run `value` — `18.4 min`, `32.4 min`, `74.8 min`, а вторая строка ячейки (не новое число) содержит SHA, тег, бинарь `0.1.0 (fd8546f0bac4)`, `N=200 K=4 jobs=2 seed=42` из SPEC. Примечание mut2 копируется в поле `note` дословно.

Перед записью JSON сверить числа командой из worktree:

```bash
git show d90754e844f010870d8d12f046bf1e19fd72cd14:docs/measurements/mut2-80x5-click-jinja.json
git show d90754e844f010870d8d12f046bf1e19fd72cd14:docs/measurements/dry-run-click.txt | head -n 12
git show d90754e844f010870d8d12f046bf1e19fd72cd14:docs/measurements/dry-run-jinja.txt | head -n 12
git show d90754e844f010870d8d12f046bf1e19fd72cd14:docs/measurements/dry-run-attrs.txt | head -n 12
```

Если блоб не совпал со SPEC, остановиться и не подменять число.

Hero-терминал содержит ровно команду из SPEC и `hero.termNote`. Вывода select нет.

Bootstrap показывает 9 шагов и абзац gate. Команда-пример и фраза про default `--jobs` — обе, чтобы 2 и 1 не слиплись.

Install: три колонки CLI / plugin / Action и заметка про `testpilot-train` под CLI. Блоки кода общие для локалей.

Comparison: четыре строки, ссылки из SPEC, без цифр статьи Meta.

- [ ] **Step 1:** Собрать `measurements.json` из `git show` выше.

- [ ] **Step 2:** Разметить секции. Заголовки секций имеют id из раздела 4: `top`, `problem`, `pipeline`, `demo` (пустое место до задачи 6), `bootstrap`, `install`, `measurements`, `comparison`.

- [ ] **Step 3:** `npm run build` exit 0, stderr без warning.

- [ ] **Step 4:** `npm run preview` и проверить, что `http://127.0.0.1:4321/TestPilot/` и `http://127.0.0.1:4321/TestPilot/ru/` открываются, а ссылка EN/RU меняет локаль. Остановить preview.

- [ ] **Step 5:** Не коммитить.

### Task 5: Скрипт демо и placeholder

**Files:**
- Create: `site/scripts/build-demo-data`
- Create: `site/scripts/test_build_demo_data.py`
- Create: `site/src/data/demo.json`

**Interfaces:**
- Consumes: контракт раздела 8 SPEC
- Produces: CLI

```text
python3 site/scripts/build-demo-data --placeholder --out site/src/data/demo.json
python3 site/scripts/build-demo-data --input DIR --out site/src/data/demo.json
```

Exit 0 только если выход записан целиком. Exit 2, если вход неполон или противоречив; целевой файл тогда не меняется. Shebang `#!/usr/bin/env python3`, только stdlib.

- [ ] **Step 1:** Написать тесты в `site/scripts/test_build_demo_data.py` на временных каталогах. Фикстуры тестов не копируются в `site/src/data/`.

Случаи:

1. `--placeholder` пишет `placeholder: true`, `source: null`, `cases: []`.
2. `--input` без `rankings/` возвращает exit 2, существующий out-файл остаётся побайтово тем же.
3. `rankings/<sha>.json`, у которого `len(tests) != n_selected`, exit 2.
4. `head` в JSON не равен имени файла, exit 2.
5. Временный git-репозиторий с одним коммитом и ranking JSON, где `head` — полный SHA этого коммита, `base` — тот же SHA (пустой diff допустим), `tests` из двух элементов с `score` литералом `0.1` и `dur` литералом `-1`. В ranking есть `ranker: "model"`. Выходной `score` равен строке `0.1`, `saved_ms` равен `null`, `caught` равен `null`, `synthetic` равен `false`, `subject` равен сообщению коммита фикстуры.
5b. Тот же репозиторий, но ranking без поля `ranker`, а `model.json` содержит `"ranker": "cov_lines"`. Exit 0, в case `ranker` равен `cov_lines`.
5c. Ranking без `ranker` и `model.json` без `ranker` — exit 2. Ranking с `ranker: "model"` при `model.json` `ranker: "cov_lines"` — exit 2.
5d. Коммит, на который указывает `refs/testpilot/mutants/c/0`, даёт `synthetic: true`. Коммит только в `refs/heads/main` даёт `synthetic: false`.
6. Тот же ranking плюс `candidates` с одним невыбранным тестом `dur: 1000` и выбранным `dur: 5`. `saved_ms` равен `1000`.
7. Тот же набор, но у невыбранного `dur: -1`. `saved_ms` равен `null`.
8. `outcomes` с `complete: true` и `fail` у выбранного id. `caught` равен `true`.
9. `outcomes` с `complete: false` и без падений среди выбранных. `caught` равен `null`, не `false`.
10. `outcomes` с `complete: true`, падение есть только у id, которого нет в `tests`. `caught` равен `false`.

- [ ] **Step 2:** Запустить тесты и убедиться, что они падают, потому что скрипта ещё нет.

Run: `python3 site/scripts/test_build_demo_data.py`
Expected: ненулевой exit, нет файла скрипта или нет функций.

- [ ] **Step 3:** Реализовать скрипт ровно по разделу 8. `score` и `dur` читать через `json.loads(..., parse_int=str, parse_float=str)` для этих полей или вырезать литерал из сырого текста. Не прогонять их через бинарный float.

- [ ] **Step 4:** Тесты exit 0.

- [ ] **Step 5:** Сгенерировать данные сайта.

Run: `python3 site/scripts/build-demo-data --placeholder --out site/src/data/demo.json`
Expected: файл как в разделе 8, `placeholder: true`.

- [ ] **Step 6:** Не коммитить.

### Task 6: Секция демо

**Files:**
- Create: `site/src/components/Demo.astro`
- Create: `site/src/scripts/demo.ts`
- Modify: `site/src/pages/index.astro`, `site/src/pages/ru/index.astro`

**Interfaces:**
- Consumes: `demo.json`, ключи `demo.*`, `reason.*`, `safeReason.*`
- Produces: при `placeholder: true` бейдж и `demo.empty`, ноль кнопок коммитов. При `placeholder: false` кнопки по `cases`, клик и Enter/Space меняют diff, список тестов и три метрики. Скрипт не содержит строк перевода и не содержит тестовых имён: имена приходят из JSON, подписи из `data-*`, которые Astro выставил из словаря.

Поведение метрик — раздел 8 SPEC. Неизвестный `reason` получает класс янтарного предупреждения и сырой код.

- [ ] **Step 1:** Разметка панели: список, diff, три метрики, список тестов, слот safe mode. `aria-live="polite"` на блоке метрик.

- [ ] **Step 2:** `demo.ts` читает JSON, который Astro сериализовал в `<script type="application/json" id="demo-data">`. Никакого `fetch` на внешний хост.

- [ ] **Step 3:** Сборка exit 0 без warning. В dist на обеих локалях есть бейдж PLACEHOLDER DATA. Поиск по dist не находит выдуманных nodeid (`test_module`, `PLACEHOLDER: pytest`).

- [ ] **Step 4:** Не коммитить.

### Task 7: Workflow Pages

**Files:**
- Create: `.github/workflows/site.yml`

**Interfaces:**
- Consumes: YAML из раздела 6 SPEC
- Produces: workflow с тем же `on`, `permissions`, `concurrency` и шагами. Других workflow-файлов diff не содержит.

- [ ] **Step 1:** Записать YAML из SPEC.

- [ ] **Step 2:** Проверить, что `ci.yml` не в diff.

Run: `git diff --name-only main -- .github/workflows`
Expected: только `.github/workflows/site.yml`.

- [ ] **Step 3:** Не пушить и не менять настройки Pages.

### Task 8: Приёмка

**Files:**
- Modify: нет, если задачи 1–7 уже зелёные. Чинить только то, что провалило проверку, и только в путях раздела 10 SPEC.

- [ ] **Step 1:** `npm run build` в `site/`. Exit 0, stderr пуст от warning.

- [ ] **Step 2:** `python3 site/scripts/test_build_demo_data.py`. Exit 0.

- [ ] **Step 3:** Preview и обе локали.

Run: `npm run preview -- --host 127.0.0.1 --port 4321`
Открыть `http://127.0.0.1:4321/TestPilot/` и `http://127.0.0.1:4321/TestPilot/ru/`.

- [ ] **Step 4:** Lighthouse по этим двум URL, категории performance, accessibility, best-practices, seo. Записать четыре числа на URL. Не подменять их целью.

- [ ] **Step 5:** Скриншоты 360, 768 и 1440 для обеих локалей. На 360 у `document.documentElement.scrollWidth` нет превышения над шириной вьюпорта.

- [ ] **Step 6:** Пройти ссылки шапки, футера, таблицы замеров и сравнения. Якоря `#problem`, `#pipeline`, `#demo`, `#bootstrap`, `#install`, `#measurements`, `#comparison` есть на обеих страницах. Переключатель EN/RU на `#demo` открывает ту же секцию на другом языке. В `<head>` есть `hreflang` `en`, `ru`, `x-default`. `<html lang>` совпадает с локалью.

- [ ] **Step 6b:** Base path в собранных файлах.

Run: `rg -n --pcre2 '(href|src)="/(?!TestPilot/)' site/dist`
Expected: нет совпадений.

- [ ] **Step 7:** Отчёт mell: SHA `feat/site` (или «не закоммичено», если коммита не было), список файлов, команды проверки, числа Lighthouse, что demo.json всё ещё placeholder, что неотслеживаемые отчёты click/jinja в сайт не входили.

- [ ] **Step 8:** Не коммитить и не пушить. Не включать Pages и не мержить в `main`: для этого нужна задача 10.

### Task 9: После мержа PR #8

Сделано. PR #8 влит squash-коммитом `20f45a96fdd23f1a541308cde25f8bcbc562722e`, поэтому `d90754e` не предок `main` и эта проверка предка не используется. Ссылки футера и замеров закреплены на `20f45a9`, не на `blob/main`. Таблица — `interpretation`, без mut2 и без dry-run.

**Files:**
- Modify: `site/src/data/measurements.json`
- Modify: `site/src/i18n/en.ts`, `site/src/i18n/ru.ts` — только фразы, которые говорят «нет на main» / называют коммит `d90754e` как единственное место bootstrap
- Modify: компоненты, если ссылка на блоб зашита не только в JSON

- [x] **Step 1:** Ссылки футера и замеров ведут на `20f45a96fdd23f1a541308cde25f8bcbc562722e`, не на `blob/main`. Таблица читает `interpretation`, числа округляет код страницы.

- [ ] **Step 2:** Убрать пометки «these files are not on main» / «Этих файлов нет на main» и «That subcommand is not on main», если README `main` уже содержит `testpilot bootstrap`.

- [ ] **Step 3:** Перечитать README `main` и сверить абзацы bootstrap в словарях: шаги, `--allow-cold-baseline`, default `--jobs`, gate reasons. Расхождение с README чинить по README, не по памяти.

- [ ] **Step 4:** `npm run build` exit 0 без warning. Не пушить, пока mell не попросит.

### Task 10: Реальные данные демо

Делать после того, как код задач 1–8 собирается. Сайт не мержить в `main` и Pages не включать, пока этот шаг не записал `demo.json` с `placeholder: false`.

Вход — click и каталог модели уже завершённого bootstrap (не новый полный bootstrap на 200 коммитов). Модель читать, не перезаписывать чужое дерево week 6: скопировать `model.json` во временный каталог.

- [ ] **Step 1:** Найти clone click и базу, на которой bootstrap уже отработал. Если clone или `.testpilot/db.sqlite` нет, остановиться и написать, чего не хватает. Не подставлять placeholder под видом реального прогона.

- [ ] **Step 2:** Выбрать 6–10 коммитов: часть first-parent из истории click, часть мутантов `refs/testpilot/mutants/` с известным падением (в synthetic run есть `fail` или `error`, которого не было на родителе). Список SHA записать в отчёт.

- [ ] **Step 3:** Для каждого SHA:

```bash
testpilot select --model "$MODEL" --repo "$CLICK" --db "$DB" \
  --base "$BASE" --head "$SHA" --budget-frac 0.1 --format json \
  > "rankings/$SHA.json"
testpilot select --model "$MODEL" --repo "$CLICK" --db "$DB" \
  --base "$BASE" --head "$SHA" --budget-frac 1 --no-safe --format json \
  > "candidates-select/$SHA.json"
```

Из второго JSON собрать `candidates/$SHA.json` вида `{"tests":[{"test_id","dur"}]}`. Outcomes — из полного прогона этого SHA (не из догадки): `outcomes/$SHA.json` с `complete: true` и исходами `pass`/`fail`/`error`/`skip`.

- [ ] **Step 4:** Собрать `input/source.json` (repo `https://github.com/pallets/click`, SHA clone, версия `testpilot --version`, дата из артефакта или из `env-host`, не «сегодня», если даты в артефакте нет — остановиться). Прогнать `python3 site/scripts/build-demo-data --input input --out site/src/data/demo.json`. Exit 0, `placeholder: false`, среди cases есть `synthetic: true` и `synthetic: false`.

- [ ] **Step 5:** Пересобрать сайт и убедиться, что бейдж SYNTHETIC виден на мутантных коммитах, а метрика пойманного падения на них показывает да/нет из outcomes. На реальных коммитах бейджа SYNTHETIC нет.

- [ ] **Step 6:** Не мержить и не включать Pages в этом шаге автоматически. Написать mell, что данные готовы и выкладка ждёт отдельной команды.

## Покрытие SPEC

| Раздел SPEC | Задача |
|---|---|
| 1–3 границы и решения | Global Constraints |
| 4 страницы, шапка, футер | 2, 4 |
| 5 дизайн и шрифт | 2 |
| 6 стек и workflow | 1, 7 |
| 7 тексты | 3, 4, 6 |
| 8 demo.json и скрипт | 5, 6 |
| 9 замеры | 4 |
| 10 список файлов | 1–7 |
| 11 вне скоупа | ничего не добавлять |
| 12 приёмка | 8 |
| Ссылки после мержа PR #8 | 9, не сейчас |
| Реальный demo.json | 10, до выкладки |

Ревью спеки принято. Задачи 1–8 выполняются. Задача 9 ждёт мержа PR #8. Задача 10 выполняется, когда есть clone и модель; без них `demo.json` остаётся placeholder, и на `main` сайт не выкладывается.
