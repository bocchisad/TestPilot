# testpilot-train

Offline LightGBM training on TestPilot feature parquet (week 3).

Design: [`docs/specs/2026-11-09-testpilot-training-design.md`](../../docs/specs/2026-11-09-testpilot-training-design.md).

## Install

```bash
pip install -e 'python/testpilot-train[dev]'
```

## Train

Primary label is **`new_fail`** (fail on change C that did not fail on parent). The parquet `parent` is the **first parent** of the change (merge commits use `parent_idx=0` only). Metrics are tie-aware; useful-gate is a paired bootstrap of model − best val heuristic.

Pinned deps: see `requirements.lock` (regenerate with `pip freeze` after `pip install -e '.[dev]'`).

```bash
testpilot-train train \
  --data /tmp/click_pairs.parquet /tmp/jinja_pairs.parquet \
  --out /tmp/tp-model \
  --features-schema features_schema.json \
  --seed 42
```

Artifacts in `--out`: `model.txt`, `model.json`, `report.md`, `report.json`, `reference_predictions.parquet`.

## Evaluate

Default slice is the chrono **test** ranges stored in `model.json` (known repos). Unknown repos use all changes. Requesting a slice that intersects train prints a warning.

```bash
testpilot-train evaluate --model /tmp/tp-model --data /tmp/click_pairs.parquet
testpilot-train baselines --data /tmp/click_pairs.parquet
```

## Reference model (week-4 parity)

Frozen 50-round binary model (no early stopping) lives in `tests/fixtures/reference_model/`. Rebuild with:

```bash
python scripts/build_reference_model.py
```

Predictions use `booster.predict(..., raw_score=True)` (margin, no sigmoid).

## Tests

```bash
pytest python/testpilot-train -q
```

CI `train-oracle` keeps this under 60s. Leakage canaries run on synthetic parquet (~200×200); the features dump fixture is for schema/load/parity only.
