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
