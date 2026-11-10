import json
from pathlib import Path

import pytest

from testpilot_train.data import load_parquets
from testpilot_train.schema import assert_schema_matches_model, load_features_schema
from testpilot_train.synth import generate_synthetic_parquet
from testpilot_train.train_lib import evaluate, train


def test_wrong_features_version(tmp_path: Path, features_schema, tiny_parquet):
    import pyarrow.parquet as pq

    table = pq.read_table(tiny_parquet)
    bad = tmp_path / "bad_ver.parquet"
    pq.write_table(
        table.replace_schema_metadata({b"features_version": b"999"}),
        bad,
    )
    with pytest.raises(ValueError, match="features_version"):
        load_parquets([bad], features_schema)


def test_shuffled_columns(tmp_path: Path, features_schema, tiny_parquet):
    import polars as pl
    import pyarrow.parquet as pq

    df = pl.read_parquet(tiny_parquet)
    cols = list(df.columns)
    # swap two feature columns
    i = cols.index("cc_max")
    j = cols.index("cc_sum")
    cols[i], cols[j] = cols[j], cols[i]
    shuffled = df.select(cols)
    bad = tmp_path / "shuf.parquet"
    table = shuffled.to_arrow().replace_schema_metadata(
        {b"features_version": str(features_schema.features_version).encode()}
    )
    pq.write_table(table, bad)
    with pytest.raises(ValueError, match="column mismatch"):
        load_parquets([bad], features_schema)


def test_schema_hash_mismatch_on_evaluate(tmp_path: Path, features_schema: Path):
    schema_path = Path(__file__).resolve().parents[3] / "features_schema.json"
    schema = load_features_schema(schema_path)
    path = tmp_path / "pairs.parquet"
    generate_synthetic_parquet(path, schema, n_changes=40, n_tests=20, seed=3)
    out = tmp_path / "model"
    train([path], out, schema_path, seed=0)

    # Tamper model.json hash
    meta_path = out / "model.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["schema_sha256"] = "0" * 64
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    with pytest.raises(ValueError, match="schema_sha256 mismatch"):
        evaluate(out, [path], schema_path, slice_name="test")


def test_assert_schema_matches_model_ok():
    schema = load_features_schema(
        Path(__file__).resolve().parents[3] / "features_schema.json"
    )
    assert_schema_matches_model(
        schema,
        {
            "schema_sha256": schema.schema_sha256,
            "features_version": schema.features_version,
            "feature_names": list(schema.feature_names),
        },
    )
