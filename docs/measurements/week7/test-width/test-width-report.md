# Неделя 7: ширина synthetic TEST

## Результат

С согласованными параметрами `N=400`, `K=4` цель `n_C ≥ 10` не достигнута ни в click, ни в attrs. Оба отчёта имеют `data_insufficient`, bootstrap gate — `use_baseline` с причиной `insufficient_test_C`. В Jinja остаётся результат Week 6 `n_C=11`. Дефолт остаётся `cov_lines`; этот прогон не доказывает превосходство модели.

| Репозиторий | Закреплённый HEAD | Положительных изменений в TEST | `n_C` | Цель | Gate |
| --- | --- | ---: | ---: | --- | --- |
| click | `934813e4d421071a1b3db3973c02fe2721359a6e` | 18 | 9 | не достигнута | `use_baseline` (`insufficient_test_C`) |
| attrs | `598494a618410490cfbe0c896b7a544f6d23e0d9` | 20 | 6 | не достигнута | `use_baseline` (`insufficient_test_C`) |
| Jinja (Week 6) | pin из `docs/measurements/bootstrap-jinja/` | — | 11 | достигнута | без изменений |

`n_C`, размеры выборок, статусы и gates сверены с копиями `bootstrap_report.json` и `report.json`. Метрики модели в этих файлах — диагностика при недостаточной ширине TEST, не основание менять дефолт.

## Параметры и время

- Python `3.13.18`; бинарь `testpilot 0.1.0 (b9433610fc61)`, SHA-256 `eadf90c80386589015a2eafce0062b8d30e50b30bea916cd0da2e83ba62abe65`; `jobs=2`, `seed=42`.
- click: dry-run оценил `3205.844 s`; полный прогон занял `3474.168 s` (около 57 мин 54 с). Backfill: 400 новых, 211 eligible; 358 попыток мутаций, 348 завершённых пар, 10 таймаутов.
- attrs: dry-run оценил `10567.403 s`. Финальный отчёт появился через `8641.750 s` после начала первого full-run; интервал включает неудачные попытки и resumes. Завершённое состояние: 400 новых backfill-записей и 91 eligible change. Счётчик bootstrap `n_mutants_attempted=69` относится **только к последнему успешному вызову**; `mutate_rate_incomplete=true`, поэтому это не общее число попыток. В состоянии 161 завершённый pair key. JSONL содержит 167 строк и 161 уникальный ключ; шесть ранних строк `infrastructure_failure` получили результат после повтора. Итоговые статусы по уникальному ключу: 118 `complete`, 25 `setup_failure`, 18 `collection_error`.
- Сумма step timings attrs — около 113 мин 53 с, но счётчик мутаций относится к одному вызову, а end-to-end время включает retries. Не используем этот прогон для независимой калибровки dry-run; attrs исключён из калибровки по плану.

## Запуск и provenance

Результат click — из clean full-run, сохранённого в `.cache/measurements/week7/test-width/run-n400-py313b/`. Первый full-run attrs на `d04e853` остановился на вызванных мутантами setup/collection ошибках. После точечных изменений классификатора и resume запуск завершился из сохранённого состояния; итоговый отчёт находится в `.cache/measurements/week7/test-width/run-n400-attrs-py313c/`, состояние — в `repos-py313c/attrs/.testpilot/bootstrap/state.json`.

Сохранённый attrs provenance snapshot создан до изменений классификатора для resume. При повторной классификации использовался код из цепочки `0cabedf`, `72e639e`, `52e5c44`, `d3d4878`, `1b28f9d`, `e899527`. В ledger нет версии скрипта для каждой пары, поэтому attrs следует считать данными с несколькими версиями классификатора. Бинарь и закреплённые деревья репозиториев не менялись. На одном resume относительный `PATH` к venv перестал находиться после смены рабочей директории train-процесса; успешный повтор использовал абсолютный путь и повторно применил сохранённые результаты мутаций и feature dump.

## Команды

Параметры dry-run и полного запуска сохранены в локальных `dry-run.json`. Основной запуск выполнялся width runner с закреплёнными checkout:

```sh
$PYTHON scripts/run_week7_test_width.py \
  --mode dry-run --n 400 --k 4 --jobs 2 --seed 42 \
  --suites click attrs \
  --repos-root .cache/measurements/week7/test-width/repos-py313b \
  --run-dir .cache/measurements/week7/test-width/run-n400-py313b

$PYTHON scripts/run_week7_test_width.py \
  --mode full --n 400 --k 4 --jobs 2 --seed 42 \
  --suites click attrs \
  --repos-root .cache/measurements/week7/test-width/repos-py313b \
  --run-dir .cache/measurements/week7/test-width/run-n400-py313b
```

После ошибки установки test extras attrs повторён в чистом checkout `repos-py313c/attrs`, затем возобновлён через bootstrap CLI с `--resume`. Успешный финальный вызов train использовал абсолютный путь к venv; очищенный лог находится в `logs/attrs-resume-complete.txt`. Вывод click сохранён в `logs/click-full-run.txt`.

## Артефакты и проверки

- Копии отчётов: `artifacts/click-report.json`, `artifacts/click-bootstrap-report.json`, `artifacts/attrs-report.json`, `artifacts/attrs-bootstrap-report.json`.
- Полный feature parquet, модели, pair outcomes и промежуточные resume logs остались в локальном игнорируемом `.cache/measurements/week7/test-width/`. `results.json` содержит исходные пути и SHA-256 отчётов, parquet, моделей, state и provenance.
- Локальные проверки после последнего изменения классификатора: `python -m pytest -q scripts/mutate` — **258 passed**; `ruff check --select E4,E7,E9,F scripts/` — **passed**. Первый GitHub CI run выявил тест, зависевший от локального `.cache/week7-train-env`; после перевода теста на активный `sys.executable` PR #10 с head `74e00e7` прошёл GitHub Actions run `45917443530` (все jobs pass, deploy пропущен условным шагом).
- Дополнительный N и прогон четвёртого репозитория не запускались. Цель ширины всё ещё не достигнута; перед новым долгим запуском нужны новая dry-run оценка и отдельное согласование.
