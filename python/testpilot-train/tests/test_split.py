import polars as pl
import pytest

from testpilot_train.data import load_parquets
from testpilot_train.schema import FeaturesSchema
from testpilot_train.split import (
    _chrono_split_by_changes,
    chrono_split,
    filter_slice,
)
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


def test_chrono_m8_regression_no_mutants(tmp_path, features_schema: FeaturesSchema):
    """Without mutants, unique-idx split matches legacy change-count split (M8)."""
    path = tmp_path / "s.parquet"
    generate_synthetic_parquet(path, features_schema, n_changes=30, n_tests=5, seed=4)
    frame = load_parquets([path], features_schema).frame
    old_plan = _chrono_split_by_changes(frame)
    new_plan = chrono_split(frame)
    for slice_name in ("train", "val", "test"):
        assert old_plan.changes_for(slice_name) == new_plan.changes_for(slice_name)


def test_chrono_mutants_share_commit_idx(tmp_path, features_schema: FeaturesSchema):
    """All changes with the same commit_idx land in the same slice."""
    path = tmp_path / "s.parquet"
    generate_synthetic_parquet(path, features_schema, n_changes=15, n_tests=4, seed=5)
    base = load_parquets([path], features_schema).frame
    sub = base.filter(pl.col("commit_idx") == 3)
    mutants = sub.with_columns(
        pl.lit(1).alias("synthetic"),
        pl.col("change").alias("base_change"),
    ).with_columns((pl.lit("mut_") + pl.col("change").str.slice(0, 8)).alias("change"))
    frame = pl.concat([base, mutants], how="vertical_relaxed")
    plan = chrono_split(frame)
    idx3_changes = set(
        frame.filter(pl.col("commit_idx") == 3).select(["repo", "change"]).iter_rows()
    )
    buckets = [
        name
        for name in ("train", "val", "test")
        if idx3_changes <= plan.changes_for(name)
    ]
    assert len(buckets) == 1


def test_chrono_too_few(tmp_path, features_schema: FeaturesSchema):
    path = tmp_path / "s.parquet"
    generate_synthetic_parquet(path, features_schema, n_changes=5, n_tests=5, seed=3)
    frame = load_parquets([path], features_schema).frame
    with pytest.raises(ValueError, match="need >= 10"):
        chrono_split(frame)
