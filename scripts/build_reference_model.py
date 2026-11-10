#!/usr/bin/env python3
"""Build committed week-4 reference model fixtures (fixed 50 rounds)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import lightgbm as lgb
import polars as pl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python" / "testpilot-train" / "src"))

from testpilot_train.artifact import (  # noqa: E402
    save_booster,
    tree_depth_stats,
    write_model_json,
    write_reference_predictions,
)
from testpilot_train.data import load_parquets  # noqa: E402
from testpilot_train.model import fit_reference_model, predict_raw  # noqa: E402
from testpilot_train.schema import load_features_schema  # noqa: E402
from testpilot_train.synth import generate_synthetic_parquet  # noqa: E402


def main() -> int:
    schema = load_features_schema(ROOT / "features_schema.json")
    out = ROOT / "tests" / "fixtures" / "reference_model"
    out.mkdir(parents=True, exist_ok=True)
    parquet = out / "pairs.parquet"
    generate_synthetic_parquet(
        parquet, schema, n_changes=60, n_tests=80, seed=42
    )
    frame = load_parquets([parquet], schema).frame
    booster = fit_reference_model(frame, schema, seed=42)
    save_booster(booster, out / "model.txt")
    write_reference_predictions(out / "reference_predictions.parquet", frame, booster, schema)
    n_trees, max_depth = tree_depth_stats(out / "model.txt")
    scores = predict_raw(booster, frame, schema)
    # Ensure fixture rows include sentinels in features
    assert frame.filter(pl.col("imp_dist") == 99).height > 0
    assert frame.filter(pl.col("pf_max") == -1).height > 0
    assert n_trees >= 50, n_trees
    assert max_depth >= 3, max_depth
    payload = {
        "features_version": schema.features_version,
        "schema_sha256": schema.schema_sha256,
        "feature_names": list(schema.feature_names),
        "objective": "binary",
        "lightgbm_version": lgb.__version__,
        "seed": 42,
        "best_iteration": 50,
        "num_boost_round": 50,
        "params": {
            "objective": "binary",
            "learning_rate": 0.05,
            "num_leaves": 15,
            "min_data_in_leaf": 1,
            "min_sum_hessian_in_leaf": 0,
            "feature_fraction": 1.0,
            "bagging_fraction": 1.0,
            "bagging_freq": 0,
            "use_missing": True,
            "zero_as_missing": False,
            "deterministic": True,
            "force_col_wise": True,
            "num_threads": 1,
            "linear_tree": False,
            "seed": 42,
        },
        "predict_raw_score": True,
        "n_trees": n_trees,
        "max_depth": max_depth,
        "constraints": {
            "categorical": False,
            "linear_tree": False,
            "num_threads_train": 1,
            "use_missing": True,
            "zero_as_missing": False,
        },
        "sentinel_notes": {"pf_max": -1, "pq_max": -1, "dur": -1, "imp_dist": 99},
        "n_rows": frame.height,
        "score_mean": float(scores.mean()),
    }
    write_model_json(out / "model.json", payload)
    print(json.dumps({"n_trees": n_trees, "max_depth": max_depth, "out": str(out)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
