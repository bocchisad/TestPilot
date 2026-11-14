"""R3 level-(a): fixed parquet → identical model.txt @ threads=1."""

from __future__ import annotations

import hashlib
from pathlib import Path

from testpilot_train.schema import FeaturesSchema
from testpilot_train.synth import generate_synthetic_parquet
from testpilot_train.train_lib import train


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_fixed_parquet_train_identical_model_txt(
    tmp_path: Path, features_schema: FeaturesSchema
) -> None:
    data = tmp_path / "pairs.parquet"
    generate_synthetic_parquet(data, features_schema, n_changes=36, n_tests=24, seed=9)
    schema_path = Path(__file__).resolve().parents[3] / "features_schema.json"

    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    # OMP threads already default to 1 in model fit params.
    pa = train([data], out_a, schema_path, seed=42)
    pb = train([data], out_b, schema_path, seed=42)
    assert (out_a / "model.txt").is_file()
    assert _sha256(out_a / "model.txt") == _sha256(out_b / "model.txt")
    assert pa.get("best_iteration") == pb.get("best_iteration")
    assert pa.get("ranker") == pb.get("ranker")
    assert (pa.get("bootstrap") or {}).get("verdict") == (
        pb.get("bootstrap") or {}
    ).get("verdict")
