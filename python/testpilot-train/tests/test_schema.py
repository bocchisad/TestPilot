from pathlib import Path

from testpilot_train.schema import SERVICE_NAMES, load_features_schema

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_schema_has_29_features():
    schema = load_features_schema(REPO_ROOT / "features_schema.json")
    assert schema.features_version == 1
    assert schema.column_names[:8] == SERVICE_NAMES
    assert len(schema.feature_names) == 29
    assert len(schema.schema_sha256) == 64
