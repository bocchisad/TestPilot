"""Load feature parquet, schema gate, new_fail derivation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import polars as pl
import pyarrow.parquet as pq

from testpilot_train.schema import FeaturesSchema


@dataclass
class LoadedData:
    frame: pl.DataFrame
    schema: FeaturesSchema
    n_rows_parent_missing: int
    n_persistent: int
    n_flaky: int


def _parquet_features_version(path: Path) -> int | None:
    meta = pq.read_metadata(path)
    kv = meta.metadata or {}
    for k, v in kv.items():
        key = k.decode() if isinstance(k, (bytes, bytearray)) else str(k)
        if key == "features_version":
            val = v.decode() if isinstance(v, (bytes, bytearray)) else str(v)
            return int(val)
    return None


def load_parquets(paths: list[Path], schema: FeaturesSchema) -> LoadedData:
    frames: list[pl.DataFrame] = []
    for path in paths:
        ver = _parquet_features_version(path)
        if ver is None:
            raise ValueError(f"{path}: missing features_version metadata")
        if ver != schema.features_version:
            raise ValueError(
                f"{path}: features_version={ver}, schema expects {schema.features_version}"
            )
        df = pl.read_parquet(path)
        cols = tuple(df.columns)
        if cols != schema.column_names:
            raise ValueError(
                f"{path}: column mismatch\n  got={cols}\n  expected={schema.column_names}"
            )
        frames.append(df)
    frame = pl.concat(frames, how="vertical") if len(frames) > 1 else frames[0]
    _reject_nonfinite(frame, schema.feature_names)
    frame, n_missing, n_persistent, n_flaky = add_new_fail(frame)
    return LoadedData(
        frame=frame,
        schema=schema,
        n_rows_parent_missing=n_missing,
        n_persistent=n_persistent,
        n_flaky=n_flaky,
    )


def _reject_nonfinite(frame: pl.DataFrame, feature_names: tuple[str, ...]) -> None:
    for name in feature_names:
        s = frame[name].cast(pl.Float64)
        if bool(s.is_nan().any()) or bool(s.is_infinite().any()):
            raise ValueError(f"feature column {name!r} contains NaN or Inf")


def add_new_fail(frame: pl.DataFrame) -> tuple[pl.DataFrame, int, int, int]:
    """Add parent_fail, parent_missing, new_fail columns."""
    fail_keys = (
        frame.filter((pl.col("label") == 1) & (pl.col("flaky") == 0))
        .select(["repo", "change", "test"])
        .unique()
        .with_columns(pl.lit(True).alias("_failed"))
    )
    change_set = set(zip(frame["repo"].to_list(), frame["change"].to_list(), strict=True))
    parent_missing = [
        (r, p) not in change_set
        for r, p in zip(frame["repo"].to_list(), frame["parent"].to_list(), strict=True)
    ]

    parent_fails = fail_keys.rename({"change": "parent"})
    enriched = (
        frame.join(parent_fails, on=["repo", "parent", "test"], how="left")
        .with_columns(pl.col("_failed").fill_null(False).alias("parent_fail"))
        .drop("_failed")
        .with_columns(pl.Series("parent_missing", parent_missing))
    )

    label1 = (pl.col("label") == 1) & (pl.col("flaky") == 0)
    new_fail = (label1 & (~pl.col("parent_fail")) & (~pl.col("parent_missing"))).cast(pl.Int64)
    enriched = enriched.with_columns(new_fail.alias("new_fail"))

    n_flaky = int(frame.filter(pl.col("flaky") == 1).height)
    n_persistent = int(
        enriched.filter(label1 & pl.col("parent_fail") & (~pl.col("parent_missing"))).height
    )
    n_missing = int(sum(parent_missing))
    return enriched, n_missing, n_persistent, n_flaky


def training_mask(frame: pl.DataFrame) -> np.ndarray:
    """Eligible train rows: not flaky, not persistent, parent present."""
    label1 = (frame["label"].to_numpy() == 1) & (frame["flaky"].to_numpy() == 0)
    parent_fail = frame["parent_fail"].to_numpy()
    parent_missing = frame["parent_missing"].to_numpy()
    flaky = frame["flaky"].to_numpy() == 1
    persistent = label1 & parent_fail & (~parent_missing)
    return (~flaky) & (~persistent) & (~parent_missing)


def feature_matrix(frame: pl.DataFrame, feature_names: tuple[str, ...]) -> np.ndarray:
    return np.ascontiguousarray(
        frame.select(list(feature_names)).to_numpy(), dtype=np.float64
    )


def diagnostics(frame: pl.DataFrame) -> dict:
    changes = frame.group_by(["repo", "change"]).agg(
        pl.col("label")
        .filter((pl.col("label") == 1) & (pl.col("flaky") == 0))
        .len()
        .alias("n_fail"),
        pl.col("new_fail").sum().alias("n_new_fail"),
    )
    per_test = (
        frame.group_by(["repo", "test"])
        .agg(
            pl.len().alias("n_appear"),
            ((pl.col("label") == 1) & (pl.col("flaky") == 0)).sum().alias("n_fail"),
        )
        .with_columns((pl.col("n_fail") / pl.col("n_appear")).alias("fail_rate"))
    )
    always_broken = int(per_test.filter(pl.col("fail_rate") >= 0.9).height)
    n_label1 = int(frame.filter((pl.col("label") == 1) & (pl.col("flaky") == 0)).height)
    n_persistent = int(
        frame.filter(
            (pl.col("label") == 1)
            & (pl.col("flaky") == 0)
            & pl.col("parent_fail")
            & (~pl.col("parent_missing"))
        ).height
    )
    return {
        "n_changes": int(changes.height),
        "fail_per_change_mean": float(changes["n_fail"].mean() or 0.0),
        "new_fail_per_change_mean": float(changes["n_new_fail"].mean() or 0.0),
        "n_always_broken_tests": always_broken,
        "n_label1": n_label1,
        "n_persistent_among_label1": n_persistent,
        "persistent_share_among_label1": (n_persistent / n_label1) if n_label1 else 0.0,
        "n_changes_with_new_fail": int(changes.filter(pl.col("n_new_fail") > 0).height),
    }
