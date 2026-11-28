# Week 7: real and synthetic LORO evaluation

> Итог после аудита завершения pytest: **превосходство на реальных данных не доказано**. Дефолтом остаётся `cov_lines`. Ни одна модельная ветка не прошла критерий 2.4.

## Проверка полноты real-пар

Ревью обнаружило, что прежний runner принимал наличие JUnit XML за достаточное подтверждение завершения pytest. В full-history артефактах проверены логи четырёх повторов для 452 последних уникальных завершённых голов:

- 132 головы исключены: у 130 в логах есть `Interrupted:`/collection/internal error (click 112, Jinja 18), ещё 12 имеют ноль общих test IDs (click 9, Jinja 3). Категории пересекаются.
- Положительных голов среди исключённых нет. 120 исключённых голов дали 680 строк в исходный feature parquet; 12 нулевых сравнений не дали строк.
- Все пары и строки отфильтрованы по `repo/head` перед LORO. Основные 24 положительных изменения сохранились; метрики предыдущего LORO помечены как superseded.
- Новый runner сохраняет exit code каждого повтора и не маркирует interrupted, internal/collection error, отсутствие тестов или пустое shared-сравнение как `completed`.

Подробная разбивка и список голов: `real-pair-pytest-completeness.json`. Точный фильтр и размеры/sha parquet: `real-feature-build-audited.json`. Очищенные parquet остаются локально в `.cache/measurements/week7/pr-intermediate/real-features-audited/`.

## Данные и метод

- Семантический аудит разобрал 26 кандидатов: 24 в основной выборке (click 18, attrs 5, Jinja 1), ещё 2 строки — sensitivity с функциями изменённых тестов. Click PR #3872/#3873 исключены как spam; Jinja #2256 исключён как uncertain.
- После фильтра неполных прогонов primary feature parquet содержит 287,822 строки; sensitivity parquet — 288,062. В primary 24 real positive changes образуют 22 bootstrap-кластера `base_change`.
- Тестовые файлы менялись в 2/24 основных строках, но падающие функции не менялись. Для per-repo Δ нужно ≥10 positive changes: click=18 (`ok`), attrs=5 и Jinja=1 (`data_insufficient`; числовой Δ не используется в гейте).
- Synthetic TEST остался отдельным: 32 положительных изменения, 320,346 строк; `partial=1` не участвовал.
- В каждом хронологическом real validation fold было 0 real positives. Tuning поэтому использовал validation pool из train-репозиториев с synthetic positives; `real_only` обучался только на real rows, validation оставался mixed. Это ограничивает интерпретацию выбора гиперпараметров.
- Paired CI кластеризуется по `base_change`. Связанные по issue/смыслу attrs #1533/#1554 и click #765/#849 имеют разные base и не объединены; CI может недооценивать зависимость. Paired mean взвешен изменениями, LORO macro recall даёт равный вес репозиториям.

## Основные real результаты: функции изменённых тестов исключены

LORO macro recall по 24 real changes; paired Δ@10 сравнивает те же held-out changes с `cov_lines`.

| Источник обучения | Recall @10 / @20 / @30 | Paired Δ@10 [95% CI] | click Δ@10 | Критерий 2.4 |
| --- | ---: | ---: | ---: | --- |
| Mutants only | 0.153 / 0.296 / 0.467 | −0.017 [−0.147, +0.118] | −0.014 | не пройден |
| Real only | 0.068 / 0.214 / 0.304 | −0.087 [−0.213, +0.042] | −0.044 | не пройден |
| Mix 4:1 | 0.114 / 0.519 / 0.630 | −0.057 [−0.195, +0.089] | −0.043 | не пройден |
| Mix 1:1 | 0.259 / 0.465 / 0.559 | −0.092 [−0.216, +0.012] | −0.070 | не пройден |
| Mix 1:4 | 0.330 / 0.426 / 0.463 | −0.043 [−0.190, +0.100] | −0.060 | не пройден |

`cov_lines` baseline: **0.195 / 0.301 / 0.407**. Ни одна paired CI не исключает 0 для основных model arms; attrs и Jinja также недостаточно представлены для per-repo gate. Real-only @10 ниже `cov_lines`, а общий критерий требует одновременного прохождения всех условий.

### Real-only ablations

| Вариант | Recall @10 / @20 / @30 | Paired Δ@10 [95% CI] |
| --- | ---: | ---: |
| full | 0.068 / 0.214 / 0.304 | −0.087 [−0.213, +0.042] |
| cov_lines reranker | 0.194 / 0.281 / 0.409 | −0.001 [−0.056, +0.091] |
| without cov_lines | 0.118 / 0.284 / 0.567 | −0.048 [−0.189, +0.104] |
| noncoverage only | 0.044 / 0.138 / 0.535 | −0.110 [−0.213, −0.027] |
| without import distance | 0.056 / 0.136 / 0.224 | −0.134 [−0.262, −0.011] |
| without co-change | 0.095 / 0.228 / 0.268 | −0.100 [−0.222, +0.022] |
| without failure history | 0.058 / 0.169 / 0.240 | −0.172 [−0.276, −0.095] |
| without change size | 0.148 / 0.315 / 0.353 | −0.048 [−0.183, +0.093] |

Ablations descriptive and underpowered; they do not establish causal feature effects.

## Synthetic diagnostic — отдельно

| Источник обучения | Recall @10 / @20 / @30 | Paired Δ@10 [95% CI] |
| --- | ---: | ---: |
| Mutants only | 0.685 / 0.781 / 0.864 | +0.008 [−0.018, +0.035] |
| Real only | 0.166 / 0.263 / 0.324 | −0.512 [−0.661, −0.362] |
| Mix 4:1 | 0.685 / 0.775 / 0.856 | +0.008 [+0.001, +0.013] |
| Mix 1:1 | 0.719 / 0.813 / 0.878 | +0.041 [+0.009, +0.085] |
| Mix 1:4 | 0.681 / 0.755 / 0.846 | +0.003 [−0.011, +0.015] |

Synthetic `cov_lines` baseline: 0.678 / 0.762 / 0.837. Эти данные — только диагностика synthetic TEST и не отвечают на вопрос о реальных коммитах.

## Бюджетная кривая — primary real-only

Среднее по 24 изменениям; на budget boundary используется ожидаемый вклад при равновероятном выборе среди тестов с одинаковым score.

| Selector | Recall @10 / @20 / @30 | Время сэкономлено @10 / @20 / @30 |
| --- | ---: | ---: |
| Модель | 0.104 / 0.185 / 0.327 | 0.777 / 0.638 / 0.453 |
| cov_lines | 0.191 / 0.291 / 0.388 | 0.902 / 0.786 / 0.669 |
| Запустить всё | 1.000 / 1.000 / 1.000 | 0 / 0 / 0 |

Покрытие длительностей: 14,361 известных, 0 неизвестных. У `cov_lines` ties на границе во всех 24 изменениях; у модели — 22/24, 21/24 и 24/24.

## Чувствительность: включить функции изменённых тестов

| Источник обучения | Recall @10 / @20 / @30 | Paired Δ@10 [95% CI] |
| --- | ---: | ---: |
| Mutants only | 0.148 / 0.289 / 0.471 | −0.023 [−0.149, +0.097] |
| Real only | 0.058 / 0.134 / 0.272 | −0.062 [−0.193, +0.062] |
| Mix 4:1 | 0.221 / 0.512 / 0.593 | −0.028 [−0.177, +0.126] |
| Mix 1:1 | 0.266 / 0.388 / 0.548 | −0.070 [−0.180, +0.032] |
| Mix 1:4 | 0.393 / 0.511 / 0.625 | +0.028 [−0.077, +0.159] |

Sensitivity не меняет решение: включение функций тестов добавляет пару, но CI всё ещё включает 0 и per-repo gate недостаточно мощный.

## Запуск и артефакты

Команда очищенного LORO запуска, код завершения 0, длительность около 32 минут:

```sh
$PYTHON scripts/run_week7_loro.py \
  --synthetic "$W6_ROOT/docs/measurements/bootstrap-click/pairs.parquet" \
  --synthetic "$W6_ROOT/docs/measurements/bootstrap-jinja/pairs.parquet" \
  --synthetic "$W6_ROOT/docs/measurements/bootstrap-attrs/pairs.parquet" \
  --real-all .cache/measurements/week7/pr-intermediate/real-features-audited/real-pair-features-all.parquet \
  --real-without-changed-functions .cache/measurements/week7/pr-intermediate/real-features-audited/real-pair-features-without_changed_functions.parquet \
  --schema features_schema.json \
  --out .cache/measurements/week7/pr-intermediate/loro-summary-audited.json
```

- Полные метрики: `summary.json`; curves всех arm/ablation/source: `budget-curve.json`.
- stdout-компактная сводка: `loro-run-output.json`; hashes/inputs и версия кода: `loro-provenance-audited.json`.
- Полный audit interrupted heads: `real-pair-pytest-completeness.json`; размеры и sha очищенных features: `real-feature-build-audited.json`.
- Исходные real feature parquet, очищенные parquet и полный raw LORO JSON остаются локально в `.cache/measurements/week7/pr-intermediate/`.
- Чистые исходы pytest для истории восстановлены по четырём сохранённым логам; у прежних записей не было exit code в JSON. Поэтому audit исключает все головы с interruption/collection markers или нулём shared IDs и не пытается ретроспективно восстановить отсутствующие return codes.
- Python 3.13.18; polars 1.17.1; LightGBM 4.5.0; NumPy 2.1.3; PyArrow 18.1.0; `testpilot-train` 0.1.0. Источники и SHA-256 зафиксированы в `loro-provenance-audited.json`.
- CI run `45917443530` прошёл на PR #10 до этих review fixes; новых remote CI запусков не было. Итоговые локальные проверки после исправлений: `scripts/mutate` — 275 passed in 170.25s; `python/testpilot-train` — 56 passed in 6.09s; `ruff check --select E4,E7,E9,F scripts/` и тот же lint на изменённых training-файлах — all checks passed; targeted `ruff format --check` на 10 новых/затронутых файлах — already formatted; `git diff --check` — clean.
- Task 1 достиг ширины: после первоначального N=400 ниже порога выполнены с разрешения click N=500 (`n_C=15`, gate `use_baseline`) и attrs N=800 (`n_C=13`, synthetic gate `use_model`). Данные и времена — `test-width-options.md` и его run artifacts.
- Task 3 (`select --explain`) не запускался: это условный этап после закрытия 0–2, в рамках данного закрытия не реализовывался.
