"""Week 7 source weighting and leakage-safe chronological grouping."""

from __future__ import annotations

import polars as pl


def assign_source_weights(
    frame: pl.DataFrame, synthetic_to_real: tuple[int, int] = (1, 1)
) -> pl.DataFrame:
    """Weight each change query so total synthetic:real query weight matches the ratio."""
    syn_target, real_target = synthetic_to_real
    if syn_target <= 0 or real_target <= 0:
        raise ValueError("source weights must be positive")
    if "synthetic" not in frame.columns:
        raise ValueError("frame is missing synthetic source column")
    counts = {
        int(row["synthetic"]): int(row["change_count"])
        for row in frame.select(["repo", "synthetic", "change"])
        .unique()
        .group_by("synthetic")
        .agg(pl.len().alias("change_count"))
        .to_dicts()
    }
    if counts.get(0, 0) == 0 or counts.get(1, 0) == 0:
        raise ValueError("mix weighting requires both real and synthetic changes")
    targets = {1: float(syn_target), 0: float(real_target)}
    target_sum = sum(targets.values())
    row_counts = frame.group_by(["repo", "synthetic", "change"]).agg(
        pl.len().alias("_n_rows")
    )
    row_counts = row_counts.with_columns(
        (
            pl.col("synthetic").replace_strict(targets)
            / pl.col("synthetic").replace_strict(counts)
            / pl.col("_n_rows")
            * frame.height
            / target_sum
        ).alias("sample_weight")
    )
    return frame.join(
        row_counts.select(["repo", "synthetic", "change", "sample_weight"]),
        on=["repo", "synthetic", "change"],
        how="left",
    )


def split_groups_by_time(
    frame: pl.DataFrame,
    fractions: tuple[float, float, float] = (0.70, 0.15, 0.15),
) -> dict[str, set[tuple[str, str]]]:
    """Split source-pair groups chronologically, keeping every change in a group together."""
    if len(fractions) != 3 or any(value <= 0 for value in fractions):
        raise ValueError("three positive train/validation/test fractions are required")
    if abs(sum(fractions) - 1.0) > 1e-9:
        raise ValueError("split fractions must sum to one")
    required = {"repo", "change", "base_change", "time", "synthetic"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"frame missing split columns: {sorted(missing)}")

    changes = frame.select(["repo", "synthetic", "base_change", "change", "time"]).unique()
    groups = changes.group_by(["repo", "synthetic", "base_change"]).agg(
        pl.col("time").max().alias("_time"), pl.col("change").unique().alias("_changes")
    )
    assignments: dict[str, set[tuple[str, str]]] = {
        "train": set(),
        "validation": set(),
        "test": set(),
    }
    for repo in sorted(groups["repo"].unique().to_list()):
        repo_groups = groups.filter(pl.col("repo") == repo).sort(["_time", "base_change"])
        n = repo_groups.height
        if n < 3:
            raise ValueError(f"repo {repo!r} has only {n} source groups; need at least 3")
        n_train = max(1, int(n * fractions[0]))
        n_val = max(1, int(n * fractions[1]))
        if n_train + n_val >= n:
            n_val = 1
            n_train = n - 2
        boundaries = (n_train, n_train + n_val)
        for idx, group in enumerate(repo_groups.iter_rows(named=True)):
            name = "train" if idx < boundaries[0] else "validation" if idx < boundaries[1] else "test"
            assignments[name].update((repo, change) for change in group["_changes"])
    return assignments


def filter_changes(
    frame: pl.DataFrame, changes: set[tuple[str, str]]
) -> pl.DataFrame:
    """Filter a frame by `(repo, change)` keys."""
    if not changes:
        return frame.clear()
    keys = pl.DataFrame(
        {"repo": [repo for repo, _change in changes], "change": [change for _repo, change in changes]}
    )
    return frame.join(keys, on=["repo", "change"], how="semi")
