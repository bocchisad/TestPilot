"""Write model.txt / model.json / reference predictions."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

import lightgbm as lgb
import polars as pl

from testpilot_train.model import predict_raw
from testpilot_train.schema import FeaturesSchema


def save_booster(booster: lgb.Booster, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    booster.save_model(str(path))


def _sanitize_jsonable(obj: Any) -> Any:
    """Replace non-finite floats with None; recurse into dict/list."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _sanitize_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_jsonable(v) for v in obj]
    if isinstance(obj, tuple):
        return [_sanitize_jsonable(v) for v in obj]
    # numpy scalars
    try:
        import numpy as np

        if isinstance(obj, np.floating):
            f = float(obj)
            return f if math.isfinite(f) else None
        if isinstance(obj, np.integer):
            return int(obj)
    except Exception:  # noqa: BLE001
        pass
    return obj


def write_model_json(path: Path, payload: dict[str, Any]) -> None:
    """Atomic strict JSON write (no NaN/Infinity literals)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = _sanitize_jsonable(payload)
    raw = json.dumps(clean, indent=2, sort_keys=True, allow_nan=False) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(raw, encoding="utf-8")
    os.replace(tmp, path)


def write_reference_predictions(
    path: Path,
    frame: pl.DataFrame,
    booster: lgb.Booster,
    schema: FeaturesSchema,
) -> None:
    ordered = frame.sort(["repo", "change", "test"])
    scores = predict_raw(booster, ordered, schema)
    out = ordered.select(["repo", "change", "test"]).with_columns(
        pl.Series("raw_score", scores)
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    out.write_parquet(path)


def load_model_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def tree_depth_stats(model_txt: Path) -> tuple[int, int]:
    """Return (n_trees, max_depth) from LightGBM text model.

    LightGBM v4 text models omit per-tree ``depth=``; depth is derived from
    ``left_child`` / ``right_child`` (negative indices are leaves).
    """
    text = model_txt.read_text(encoding="utf-8")
    n_trees = text.count("Tree=")
    max_depth = 0
    left: list[int] | None = None
    right: list[int] | None = None
    for line in text.splitlines():
        if line.startswith("left_child="):
            left = [int(x) for x in line.split("=", 1)[1].split()]
        elif line.startswith("right_child="):
            right = [int(x) for x in line.split("=", 1)[1].split()]
            if left is None or len(left) != len(right):
                left = None
                right = None
                continue

            def node_depth(i: int) -> int:
                if i < 0:
                    return 0
                assert left is not None and right is not None
                return 1 + max(node_depth(left[i]), node_depth(right[i]))

            max_depth = max(max_depth, node_depth(0))
            left = None
            right = None
    return n_trees, max_depth
