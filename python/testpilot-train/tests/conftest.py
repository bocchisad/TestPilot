"""Shared fixtures for testpilot-train."""

from __future__ import annotations

from pathlib import Path

import pytest

from testpilot_train.schema import FeaturesSchema, load_features_schema
from testpilot_train.synth import generate_synthetic_parquet


REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = REPO_ROOT / "features_schema.json"


@pytest.fixture(scope="session")
def features_schema() -> FeaturesSchema:
    return load_features_schema(SCHEMA_PATH)


@pytest.fixture
def tiny_parquet(tmp_path: Path, features_schema: FeaturesSchema) -> Path:
    path = tmp_path / "tiny.parquet"
    generate_synthetic_parquet(
        path, features_schema, n_changes=12, n_tests=20, seed=1
    )
    return path


@pytest.fixture(scope="session")
def synth_parquet(tmp_path_factory: pytest.TempPathFactory, features_schema: FeaturesSchema) -> Path:
    path = tmp_path_factory.mktemp("synth") / "synth.parquet"
    generate_synthetic_parquet(
        path, features_schema, n_changes=200, n_tests=200, seed=0
    )
    return path
