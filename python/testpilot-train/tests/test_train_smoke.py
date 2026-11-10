from pathlib import Path

from testpilot_train.schema import FeaturesSchema
from testpilot_train.synth import generate_synthetic_parquet
from testpilot_train.train_lib import evaluate, train


def test_train_evaluate_smoke(tmp_path: Path, features_schema: FeaturesSchema):
    path = tmp_path / "pairs.parquet"
    generate_synthetic_parquet(path, features_schema, n_changes=40, n_tests=30, seed=11)
    schema_path = Path(__file__).resolve().parents[3] / "features_schema.json"
    out = tmp_path / "model"
    payload = train([path], out, schema_path, seed=0)
    assert (out / "model.txt").is_file()
    assert (out / "model.json").is_file()
    assert (out / "report.md").is_file()
    assert payload["predict_raw_score"] is True
    assert "status" in payload
    result = evaluate(out, [path], schema_path, slice_name="test")
    assert "metrics" in result
