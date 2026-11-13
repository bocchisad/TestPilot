import polars as pl
import pytest

from testpilot_train.data import add_new_fail, feature_matrix, load_parquets, training_mask
from testpilot_train.schema import FeaturesSchema


def test_load_and_schema_gate(tiny_parquet, features_schema: FeaturesSchema):
    loaded = load_parquets([tiny_parquet], features_schema)
    assert loaded.frame.height > 0
    assert "new_fail" in loaded.frame.columns


def test_new_fail_and_persistent(features_schema: FeaturesSchema, tmp_path):
    # Manual two-change chain: parent fails test_a; child fails test_a (persistent) and test_b (new)
    rows = []
    feat = {n: 0 for n in features_schema.feature_names}
    feat["imp_dist"] = 99
    feat["pf_max"] = -1.0
    feat["pq_max"] = -1.0
    feat["dur"] = -1

    def row(change, parent, idx, test, label):
        r = {
            "repo": "r",
            "change": change,
            "parent": parent,
            "commit_idx": idx,
            "time": 1000 + idx,
            "test": test,
            "label": label,
            "flaky": 0,
            **feat,
        }
        return r

    c0, c1 = "0" * 40, "1" * 40
    missing_parent = "f" * 40
    # Root change: parent absent → parent_missing (not persistent)
    for t, lab in [("ta", 1), ("tb", 0)]:
        rows.append(row(c0, missing_parent, 0, t, lab))
    for t, lab in [("ta", 1), ("tb", 1)]:
        rows.append(row(c1, c0, 1, t, lab))

    frame = pl.DataFrame(rows)
    enriched, n_missing, n_persistent, n_flaky, n_orphan = add_new_fail(frame)
    assert n_flaky == 0
    assert n_missing == 2  # both root rows
    assert n_orphan == 0
    child = enriched.filter(pl.col("change") == c1)
    ta = child.filter(pl.col("test") == "ta").row(0, named=True)
    tb = child.filter(pl.col("test") == "tb").row(0, named=True)
    assert bool(ta["parent_fail"]) is True
    assert ta["new_fail"] == 0
    assert bool(tb["parent_fail"]) is False
    assert tb["new_fail"] == 1
    assert n_persistent == 1


def test_orphan_test_not_new_fail(features_schema: FeaturesSchema):
    """Phantom collection test_id on M absent from C → new_fail=0, counted as orphan."""
    feat = {n: 0 for n in features_schema.feature_names}
    feat["imp_dist"] = 99
    feat["pf_max"] = -1.0
    feat["pq_max"] = -1.0
    feat["dur"] = -1
    c0, c1 = "0" * 40, "1" * 40

    def row(change, parent, idx, test, label, synthetic=0):
        return {
            "repo": "r",
            "change": change,
            "parent": parent,
            "commit_idx": idx,
            "time": 1000 + idx,
            "test": test,
            "label": label,
            "flaky": 0,
            "synthetic": synthetic,
            "base_change": c0 if synthetic else "",
            **feat,
        }

    rows = [
        row(c0, "f" * 40, 0, "tests/t.py::test_ok", 0),
        row(c1, c0, 1, "tests/t.py::test_ok", 0, synthetic=1),
        row(c1, c0, 1, "tests.test_basic", 1, synthetic=1),  # orphan phantom
    ]
    frame = pl.DataFrame(rows)
    enriched, _, _, _, n_orphan = add_new_fail(frame)
    assert n_orphan == 1
    ph = enriched.filter(pl.col("test") == "tests.test_basic").row(0, named=True)
    assert bool(ph["orphan_vs_parent"]) is True
    assert ph["new_fail"] == 0
    ok = enriched.filter(pl.col("test") == "tests/t.py::test_ok").filter(
        pl.col("change") == c1
    ).row(0, named=True)
    assert bool(ok["orphan_vs_parent"]) is False
    assert ok["new_fail"] == 0

    mask = training_mask(enriched)
    train_rows = enriched.filter(pl.Series(mask))
    # root rows excluded via parent_missing
    assert train_rows.filter(pl.col("change") == c0).height == 0
    assert train_rows.filter(pl.col("change") == c1).height == 2


def test_feature_matrix_rejects_service_columns(features_schema: FeaturesSchema, tmp_path):
    df = pl.DataFrame({n: [0.0] for n in features_schema.feature_names + ("synthetic",)})
    with pytest.raises(ValueError, match="service columns"):
        feature_matrix(df, features_schema.feature_names + ("synthetic",))


def test_nan_rejected(tiny_parquet, features_schema: FeaturesSchema, tmp_path):
    df = pl.read_parquet(tiny_parquet)
    df = df.with_columns(pl.lit(float("nan")).alias("cc_max"))
    bad = tmp_path / "bad.parquet"
    import pyarrow.parquet as pq

    table = df.to_arrow().replace_schema_metadata(
        {b"features_version": str(features_schema.features_version).encode()}
    )
    pq.write_table(table, bad)
    with pytest.raises(ValueError, match="NaN"):
        load_parquets([bad], features_schema)
