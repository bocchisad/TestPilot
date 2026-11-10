"""Heuristic baselines and random expectation."""

from __future__ import annotations

import numpy as np
import polars as pl

from testpilot_train.metrics import change_metrics


def random_recall(n: int, n_pos: int, frac: float) -> float:
    if n == 0 or n_pos == 0:
        return float("nan")
    k = max(1, int(np.floor(frac * n)))
    k = min(k, n)
    # E[hits]/n_pos = k/n
    return k / n


def score_fail_per_day(frame: pl.DataFrame) -> np.ndarray:
    age = np.maximum(frame["t_age_days"].to_numpy().astype(np.float64), 1.0 / 86400.0)
    return frame["t_fail_cnt"].to_numpy().astype(np.float64) / age


def baseline_scores(frame: pl.DataFrame, name: str) -> np.ndarray:
    if name == "t_fail_rate":
        return frame["t_fail_rate"].to_numpy().astype(np.float64)
    if name == "cc_max":
        return frame["cc_max"].to_numpy().astype(np.float64)
    if name == "imp_dist":
        # ascending: negate for descending sort convention in metrics
        return -frame["imp_dist"].to_numpy().astype(np.float64)
    if name == "cov_lines":
        return frame["cov_lines"].to_numpy().astype(np.float64)
    if name == "fail_per_day":
        return score_fail_per_day(frame)
    if name == "random":
        # constant scores → tie-aware recall = k/n
        return np.zeros(frame.height, dtype=np.float64)
    raise KeyError(name)


BASELINE_NAMES = (
    "random",
    "t_fail_rate",
    "cc_max",
    "imp_dist",
    "cov_lines",
    "fail_per_day",
)


def eval_baseline_per_change(
    frame: pl.DataFrame,
    name: str,
    label_col: str = "new_fail",
) -> list[dict[str, float]]:
    out: list[dict[str, float]] = []
    for _keys, grp in frame.group_by(["repo", "change"], maintain_order=True):
        # Exclude parent_missing from new_fail metrics
        if label_col == "new_fail":
            grp = grp.filter(~pl.col("parent_missing"))
        y = grp[label_col].to_numpy()
        if int(y.sum()) == 0:
            continue
        scores = baseline_scores(grp, name)
        out.append(change_metrics(y, scores))
    return out
