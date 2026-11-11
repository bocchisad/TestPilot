"""Write model.txt / model.json / reference predictions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import lightgbm as lgb
import polars as pl

from testpilot_train.model import predict_raw
from testpilot_train.schema import FeaturesSchema


def save_booster(booster: lgb.Booster, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    booster.save_model(str(path))


def write_model_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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
