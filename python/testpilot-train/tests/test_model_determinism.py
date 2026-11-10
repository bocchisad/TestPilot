from pathlib import Path

from testpilot_train.data import load_parquets
from testpilot_train.model import fit_binary, predict_raw
from testpilot_train.schema import FeaturesSchema
from testpilot_train.split import chrono_split, filter_slice
from testpilot_train.synth import generate_synthetic_parquet


def test_binary_train_deterministic(tmp_path: Path, features_schema: FeaturesSchema):
    path = tmp_path / "d.parquet"
    generate_synthetic_parquet(path, features_schema, n_changes=40, n_tests=40, seed=7)
    frame = load_parquets([path], features_schema).frame
    plan = chrono_split(frame)
    train = filter_slice(frame, plan, "train")
    val = filter_slice(frame, plan, "val")
    a = fit_binary(train, val, features_schema, seed=42)
    b = fit_binary(train, val, features_schema, seed=42)
    sa = predict_raw(a.booster, val, features_schema)
    sb = predict_raw(b.booster, val, features_schema)
    assert (sa == sb).all()
