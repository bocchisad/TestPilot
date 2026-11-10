import pytest

from testpilot_train.data import load_parquets
from testpilot_train.schema import FeaturesSchema
from testpilot_train.split import chrono_split, filter_slice
from testpilot_train.synth import generate_synthetic_parquet


def test_chrono_no_overlap(tmp_path, features_schema: FeaturesSchema):
    path = tmp_path / "s.parquet"
    generate_synthetic_parquet(path, features_schema, n_changes=20, n_tests=10, seed=2)
    frame = load_parquets([path], features_schema).frame
    plan = chrono_split(frame)
    train = set(plan.changes_for("train"))
    val = set(plan.changes_for("val"))
    test = set(plan.changes_for("test"))
    assert not (train & val)
    assert not (val & test)
    assert not (train & test)
    rs = plan.by_repo[0]
    assert rs.train_commit_idx[1] < rs.val_commit_idx[0]
    assert rs.val_commit_idx[1] < rs.test_commit_idx[0]
    assert filter_slice(frame, plan, "train").height > 0


def test_chrono_too_few(tmp_path, features_schema: FeaturesSchema):
    path = tmp_path / "s.parquet"
    generate_synthetic_parquet(path, features_schema, n_changes=5, n_tests=5, seed=3)
    frame = load_parquets([path], features_schema).frame
    with pytest.raises(ValueError, match="need >= 10"):
        chrono_split(frame)
