# Week 7: real and synthetic LORO evaluation

> Итог по зарегистрированному критерию: **превосходство на реальных данных не доказано**. Дефолтом остаётся `cov_lines`. Ни одна модельная ветка не прошла критерий 2.4.

## Данные и метод

- Семантический аудит разобрал 26 кандидатов: 24 оставлены в основной выборке, click PR #3872/#3873 исключён как spam, Jinja #2256 помечен uncertain и исключён из основной выборки. Пары #1533/#1554 (attrs) и #765/#849 (click) могут быть коррелированы.
- Основная популяция после исключения changed test functions: 24 положительных изменения (click 18, attrs 5, Jinja 1), 288,502 real feature rows. Чувствительность с функциями изменённых тестов: 26 изменений и 288,742 строки. Файлы тестов менялись в 2/24 основных строках; изменённые функции не совпадали с падающими функциями.
- Synthetic TEST: отдельные 32 положительных изменения; synthetic partial rows: 0. Синтетические результаты не объединяются с real.
- LORO, 3 удерживаемых репозитория. Для per-repo Δ нужно не менее 10 положительных изменений: click=18 (`ok`), attrs=5 и Jinja=1 (`data_insufficient`; числовой Δ не публикуется).
- В каждом хронологическом real validation fold было 0 real positives. Поэтому tuning использовал хронологический validation pool из обучающих репозиториев с synthetic positives; `real_only` обучался только на real данных, но validation оставался mixed. Это ограничивает интерпретацию выбора гиперпараметров.
- Paired CI в коде кластеризуется по `base_change`. В основных 24 строках это 22 bootstrap-кластера (две пары имеют одинаковый base). Связанные по issue/смыслу attrs #1533/#1554 и click #765/#849 имеют разные base и не сгруппированы; CI может недооценивать зависимость. Точечный paired mean взвешен по изменениям, а LORO macro recall даёт одинаковый вес репозиториям.
- `partial=1` не участвовал; score не трактуется как вероятность. Бюджетная кривая усредняется по положительным изменениям, поэтому её recall может отличаться от equal-repo LORO macro.

## Основные результаты: changed test functions исключены

LORO macro recall и paired Δ@10 по одним и тем же 24 реальным изменениям.

| Источник обучения | Recall @10 / @20 / @30 | Paired Δ@10 [95% CI] | click Δ@10 | Критерий 2.4 |
| --- | ---: | ---: | ---: | --- |
| Mutants only | 0.247 / 0.347 / 0.393 | +0.043 [-0.073, +0.172] | -0.011 | не пройден |
| Real only | 0.184 / 0.262 / 0.505 | -0.051 [-0.186, +0.093] | -0.061 | не пройден |
| Mix 4:1 | 0.064 / 0.236 / 0.459 | -0.098 [-0.205, +0.002] | -0.060 | не пройден |
| Mix 1:1 | 0.191 / 0.316 / 0.485 | -0.103 [-0.232, -0.001] | -0.089 | не пройден |
| Mix 1:4 | 0.278 / 0.585 / 0.691 | +0.021 [-0.143, +0.182] | -0.067 | не пройден |

`cov_lines` baseline: 0.195 / 0.301 / 0.407. В основной real-only оценке модельный recall@10 ниже baseline; критерий не выполнен и все репозитории не имеют достаточной ширины данных.

### Real-only ablations

| Вариант | Recall @10 / @20 / @30 | Paired Δ@10 [95% CI] |
| --- | ---: | ---: |
| full | 0.184 / 0.262 / 0.505 | -0.051 [-0.186, +0.093] |
| cov_lines_reranker | 0.265 / 0.342 / 0.519 | +0.022 [-0.063, +0.126] |
| without_cov_lines | 0.191 / 0.424 / 0.451 | -0.080 [-0.222, +0.042] |
| noncoverage_only | 0.057 / 0.112 / 0.179 | -0.076 [-0.200, +0.043] |
| without_import_distance | 0.019 / 0.127 / 0.305 | -0.167 [-0.271, -0.081] |
| without_cochange | 0.251 / 0.334 / 0.583 | -0.099 [-0.221, +0.011] |
| without_failure_history | 0.134 / 0.269 / 0.487 | -0.069 [-0.204, +0.062] |
| without_change_size | 0.133 / 0.253 / 0.443 | -0.054 [-0.205, +0.111] |

Ablations descriptive and underpowered; they do not establish causal feature effects.

## Synthetic diagnostic — отдельно

| Источник обучения | Recall @10 / @20 / @30 | Paired Δ@10 [95% CI] |
| --- | ---: | ---: |
| Mutants only | 0.674 / 0.807 / 0.875 | -0.003 [-0.019, +0.011] |
| Real only | 0.111 / 0.253 / 0.405 | -0.567 [-0.714, -0.417] |
| Mix 4:1 | 0.679 / 0.784 / 0.854 | +0.002 [-0.019, +0.023] |
| Mix 1:1 | 0.675 / 0.759 / 0.846 | -0.002 [-0.019, +0.016] |
| Mix 1:4 | 0.712 / 0.816 / 0.888 | +0.034 [+0.001, +0.083] |

Эти synthetic результаты не отвечают на вопрос о real-коммитах и не меняют решение по дефолту.

## Бюджетная кривая — real-only

Среднее по 24 изменениям. Для tie на бюджетной границе берётся ожидаемое значение при равновероятном выборе среди тестов с одинаковым score; это устраняет зависимость от порядка строк.

| Selector | Recall @10 / @20 / @30 | Время сэкономлено @10 / @20 / @30 |
| --- | ---: | ---: |
| Модель | 0.140 / 0.230 / 0.397 | 0.675 / 0.547 / 0.384 |
| cov_lines | 0.191 / 0.291 / 0.388 | 0.902 / 0.786 / 0.669 |
| Запустить всё | 1.000 / 1.000 / 1.000 | 0 / 0 / 0 |

Покрытие длительностей: 14,361 известных, 0 неизвестных (100%). На границе выбора у `cov_lines` ties во всех 24 изменениях при каждом бюджете; у модели — 22/24, 21/24 и 23/24.

## Чувствительность: включить changed test functions

| Источник обучения | Recall @10 / @20 / @30 | Paired Δ@10 [95% CI] | click Δ@10 |
| --- | ---: | ---: | ---: |
| Mutants only | 0.242 / 0.340 / 0.381 | +0.032 [-0.062, +0.151] | -0.019 |
| Real only | 0.188 / 0.338 / 0.407 | -0.052 [-0.181, +0.077] | -0.007 |
| Mix 4:1 | 0.050 / 0.213 / 0.372 | -0.085 [-0.195, +0.024] | -0.031 |
| Mix 1:1 | 0.384 / 0.404 / 0.560 | -0.028 [-0.182, +0.123] | +0.000 |
| Mix 1:4 | 0.296 / 0.491 / 0.603 | -0.091 [-0.207, +0.009] | -0.110 |

## Запуск и артефакты

Повторить из корня репозитория, указав локальный Week 6 checkout в `W6_ROOT` и Python 3.13 training env в `PYTHON`:

```sh
$PYTHON scripts/run_week7_loro.py \
  --synthetic "$W6_ROOT/docs/measurements/bootstrap-click/pairs.parquet" \
  --synthetic "$W6_ROOT/docs/measurements/bootstrap-jinja/pairs.parquet" \
  --synthetic "$W6_ROOT/docs/measurements/bootstrap-attrs/pairs.parquet" \
  --real-all .cache/measurements/week7/pr-intermediate/real-features/real-pair-features-all.parquet \
  --real-without-changed-functions .cache/measurements/week7/pr-intermediate/real-features/real-pair-features-without_changed_functions.parquet \
  --schema features_schema.json \
  --out .cache/measurements/week7/pr-intermediate/loro-summary.json
```

- Машиночитаемые метрики и provenance: `summary.json`; полные кривые: `budget-curve.json`.
- Вывод команды: `loro-run-output.json`; feature-build summary: `real-feature-build-output.json`.
- Pair audit и семантические решения: `real-pair-audit.md`, `real-pair-semantic-audit.md`, три manifest JSONL.
- Feature build обработал 447 уникальных head SHA, исключил click PR group #3872/#3873 и Jinja #2256, записал 0 feature errors; parquet содержит 288,742 / 288,502 rows. На legacy source встречались Python `SyntaxWarning` про `\s`; построение завершилось успешно. Сводка и SHA артефактов: `real-feature-build-output.json`.
- Реальные parquet и raw JSON остаются локальными в `.cache/measurements/week7/pr-intermediate/`.
- Python 3.13.18; polars 1.17.1; LightGBM 4.5.0; NumPy 2.1.3; PyArrow 18.1.0; testpilot-train 0.1.0.
- Week 6 provenance восстановлен из исторических файлов/Git, а не зафиксирован snapshot во время прогона. В attrs 36 пар относятся к последнему запуску, 76 — оценка более ранних запусков; точная версия classifier на каждую пару неизвестна.
- Финальный LORO процесс завершился с exit code 0 примерно за 39 минут. Локальная CI-equivalent проверка train и mutate suites выполнялась ранее; после финальных изменений повторяется ниже. GitHub CI ещё не запускался; коммитов/PR для этих изменений нет.
- Task 1: после отдельного согласования запущен synthetic width `N=400`, `K=4` для click и attrs. `n_C=9` и `n_C=6`, цель `n_C ≥ 10` не достигнута; оба отчёта остаются `data_insufficient`, gate — `use_baseline`. Jinja Week 6 остаётся `n_C=11`. Подробный отчёт, copied JSON outputs, команды и provenance caveats: `test-width/test-width-report.md` и `test-width/results.json`. Новый N/репозиторий не запускался; сначала нужна новая оценка и отдельное согласование.
- Task 3 (`select --explain`) не запускался.
