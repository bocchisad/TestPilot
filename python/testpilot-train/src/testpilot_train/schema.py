"""features_schema.json loader and hash."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

SERVICE_NAMES: tuple[str, ...] = (
    "repo",
    "change",
    "parent",
    "commit_idx",
    "time",
    "test",
    "label",
    "flaky",
)


@dataclass(frozen=True)
class FeaturesSchema:
    features_version: int
    column_names: tuple[str, ...]
    feature_names: tuple[str, ...]
    schema_sha256: str
    path: Path


def load_features_schema(path: Path | str) -> FeaturesSchema:
    p = Path(path)
    raw = p.read_bytes()
    doc = json.loads(raw.decode("utf-8"))
    names = tuple(c["name"] for c in doc["columns"])
    if names[: len(SERVICE_NAMES)] != SERVICE_NAMES:
        raise ValueError(
            f"service columns mismatch: got {names[: len(SERVICE_NAMES)]}, "
            f"expected {SERVICE_NAMES}"
        )
    feature_names = names[len(SERVICE_NAMES) :]
    if len(feature_names) != 29:
        raise ValueError(f"expected 29 feature columns, got {len(feature_names)}")
    return FeaturesSchema(
        features_version=int(doc["features_version"]),
        column_names=names,
        feature_names=feature_names,
        schema_sha256=hashlib.sha256(raw).hexdigest(),
        path=p,
    )


def assert_schema_matches_model(schema: FeaturesSchema, model_meta: dict) -> None:
    """Gate: training-time features_schema.json hash must match model.json."""
    expected = model_meta.get("schema_sha256")
    if expected is None:
        raise ValueError("model.json missing schema_sha256")
    if schema.schema_sha256 != expected:
        raise ValueError(
            f"schema_sha256 mismatch: features-schema={schema.schema_sha256} "
            f"model.json={expected}"
        )
    ver = model_meta.get("features_version")
    if ver is not None and int(ver) != schema.features_version:
        raise ValueError(
            f"features_version mismatch: features-schema={schema.features_version} "
            f"model.json={ver}"
        )
    names = model_meta.get("feature_names")
    if names is not None and tuple(names) != schema.feature_names:
        raise ValueError(
            "feature_names mismatch between features-schema and model.json "
            f"(got {len(schema.feature_names)} vs model {len(names)})"
        )
