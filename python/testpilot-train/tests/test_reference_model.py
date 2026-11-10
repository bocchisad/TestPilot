from pathlib import Path

import lightgbm as lgb
import numpy as np
import polars as pl
import pytest

from testpilot_train.artifact import tree_depth_stats
from testpilot_train.data import load_parquets
from testpilot_train.model import predict_raw
from testpilot_train.schema import FeaturesSchema, load_features_schema

REPO_ROOT = Path(__file__).resolve().parents[3]
FIX = REPO_ROOT / "tests" / "fixtures" / "reference_model"


@pytest.fixture(scope="module")
def schema() -> FeaturesSchema:
    return load_features_schema(REPO_ROOT / "features_schema.json")


def test_reference_fixture_exists():
    assert (FIX / "model.txt").is_file()
    assert (FIX / "model.json").is_file()
    assert (FIX / "reference_predictions.parquet").is_file()
    assert (FIX / "pairs.parquet").is_file()


def test_reference_trees_and_depth():
    n_trees, max_depth = tree_depth_stats(FIX / "model.txt")
    assert n_trees >= 50
    assert max_depth >= 3


def test_reference_predictions_match_raw_score(schema: FeaturesSchema):
    frame = load_parquets([FIX / "pairs.parquet"], schema).frame
    booster = lgb.Booster(model_file=str(FIX / "model.txt"))
    ordered = frame.sort(["repo", "change", "test"])
    got = predict_raw(booster, ordered, schema)
    ref = pl.read_parquet(FIX / "reference_predictions.parquet").sort(
        ["repo", "change", "test"]
    )
    assert np.allclose(got, ref["raw_score"].to_numpy(), atol=1e-6, rtol=0)
    # Features include sentinels
    assert ordered.filter(pl.col("imp_dist") == 99).height > 0
    assert ordered.filter(pl.col("pf_max") == -1).height > 0
    assert ordered.filter(pl.col("dur") == -1).height > 0


def test_reference_model_json_missing_flags():
    import json

    meta = json.loads((FIX / "model.json").read_text(encoding="utf-8"))
    assert meta["predict_raw_score"] is True
    assert meta["constraints"]["use_missing"] is True
    assert meta["constraints"]["zero_as_missing"] is False
    assert meta["params"]["use_missing"] is True
    assert meta["params"]["zero_as_missing"] is False
