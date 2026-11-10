"""Tie-aware ranking metrics and bootstrap CIs."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class MetricPoint:
    mean: float
    ci_low: float
    ci_high: float
    n_changes: int


def recall_at_k_tied(y: np.ndarray, scores: np.ndarray, frac: float) -> float:
    """Expected recall@k with random tie-breaking within equal-score groups.

    When all scores are equal: returns k/n (for n_pos > 0).
    """
    y = np.asarray(y, dtype=np.int64)
    scores = np.asarray(scores, dtype=np.float64)
    n = y.shape[0]
    if n == 0:
        return float("nan")
    n_pos = int(y.sum())
    if n_pos == 0:
        return float("nan")
    k = max(1, int(np.floor(frac * n)))
    k = min(k, n)

    # Sort by score descending; stable within ties for grouping only.
    order = np.argsort(-scores, kind="mergesort")
    y_s = y[order]
    s_s = scores[order]

    hits = 0.0
    i = 0
    remaining = k
    while remaining > 0 and i < n:
        j = i + 1
        while j < n and s_s[j] == s_s[i]:
            j += 1
        group = y_s[i:j]
        g = j - i
        if g <= remaining:
            hits += float(group.sum())
            remaining -= g
            i = j
            continue
        # Borderline group: expected positives in random subset of size remaining
        g_pos = float(group.sum())
        hits += remaining * (g_pos / g)
        remaining = 0
    return hits / n_pos


def apfd_tied(y: np.ndarray, scores: np.ndarray) -> float:
    """APFD with expected ranks under random tie-breaking within score groups."""
    y = np.asarray(y, dtype=np.int64)
    scores = np.asarray(scores, dtype=np.float64)
    n = y.shape[0]
    n_pos = int(y.sum())
    if n == 0 or n_pos == 0:
        return float("nan")
    order = np.argsort(-scores, kind="mergesort")
    y_s = y[order]
    s_s = scores[order]
    # Expected 1-based ranks for positives
    rank_sum = 0.0
    pos = 0  # positions before current group (0-based count)
    i = 0
    while i < n:
        j = i + 1
        while j < n and s_s[j] == s_s[i]:
            j += 1
        g = j - i
        g_pos = int(y_s[i:j].sum())
        # expected rank of a random positive in this group: pos + (1+g)/2
        if g_pos:
            exp_rank = pos + (1 + g) / 2.0
            rank_sum += g_pos * exp_rank
        pos += g
        i = j
    return 1.0 - rank_sum / (n_pos * n) + 1.0 / (2.0 * n)


def first_all_fail_frac(y: np.ndarray, scores: np.ndarray) -> tuple[float, float]:
    """Expected first/last fail position / n under tie-aware ranking."""
    y = np.asarray(y, dtype=np.int64)
    scores = np.asarray(scores, dtype=np.float64)
    n = y.shape[0]
    n_pos = int(y.sum())
    if n == 0 or n_pos == 0:
        return float("nan"), float("nan")
    order = np.argsort(-scores, kind="mergesort")
    y_s = y[order]
    s_s = scores[order]
    # Walk groups; expected min/max position of positives
    # Approximate: use mid-rank of first group containing a positive / last group
    pos = 0
    first = None
    last = None
    i = 0
    while i < n:
        j = i + 1
        while j < n and s_s[j] == s_s[i]:
            j += 1
        g = j - i
        g_pos = int(y_s[i:j].sum())
        if g_pos:
            # expected earliest: among g_pos positives placed uniformly in g slots
            # E[min rank] = pos + (g_pos+1)/(g_pos+1) ... use pos+1 for first hit expectation
            if first is None:
                # E[min of g_pos uniform without replacement in 1..g] = (g_pos+1)/(g_pos+1) wait
                # E[min] = (g - g_pos + 1) / (g_pos + 1) ? Actually for hypergeometric ranks:
                # E[min of sample] = (n_pos+1)/(g_pos+1) in 1..g — standard: (g+1)/(g_pos+1)
                e_min = (g + 1) / (g_pos + 1)
                first = pos + e_min
            e_max = (g_pos * (g + 1)) / (g_pos + 1)
            last = pos + e_max
        pos += g
        i = j
    assert first is not None and last is not None
    return first / n, last / n


def change_metrics(y: np.ndarray, scores: np.ndarray) -> dict[str, float]:
    return {
        "recall@5%": recall_at_k_tied(y, scores, 0.05),
        "recall@10%": recall_at_k_tied(y, scores, 0.10),
        "recall@20%": recall_at_k_tied(y, scores, 0.20),
        "apfd": apfd_tied(y, scores),
        "first_fail_frac": first_all_fail_frac(y, scores)[0],
        "all_fail_frac": first_all_fail_frac(y, scores)[1],
    }


def macro_mean(per_change: list[dict[str, float]], key: str) -> float:
    vals = [c[key] for c in per_change if c[key] == c[key]]  # not nan
    return float(np.mean(vals)) if vals else float("nan")


def bootstrap_ci(
    per_change: list[dict[str, float]],
    key: str,
    seed: int,
    n_boot: int = 1000,
) -> MetricPoint:
    vals = np.array([c[key] for c in per_change if c[key] == c[key]], dtype=np.float64)
    n = vals.shape[0]
    if n == 0:
        return MetricPoint(float("nan"), float("nan"), float("nan"), 0)
    rng = np.random.default_rng(seed)
    means = np.empty(n_boot, dtype=np.float64)
    for b in range(n_boot):
        idx = rng.integers(0, n, size=n)
        means[b] = vals[idx].mean()
    lo, hi = np.quantile(means, [0.025, 0.975])
    return MetricPoint(float(vals.mean()), float(lo), float(hi), n)


def paired_delta_ci(
    model_per: list[dict[str, float]],
    base_per: list[dict[str, float]],
    key: str,
    seed: int,
    n_boot: int = 1000,
) -> MetricPoint:
    """Bootstrap CI for mean(model - baseline) on the same change order."""
    assert len(model_per) == len(base_per)
    deltas = []
    for m, b in zip(model_per, base_per, strict=True):
        if m[key] == m[key] and b[key] == b[key]:
            deltas.append(m[key] - b[key])
    arr = np.asarray(deltas, dtype=np.float64)
    n = arr.shape[0]
    if n == 0:
        return MetricPoint(float("nan"), float("nan"), float("nan"), 0)
    rng = np.random.default_rng(seed)
    means = np.empty(n_boot, dtype=np.float64)
    for b in range(n_boot):
        idx = rng.integers(0, n, size=n)
        means[b] = arr[idx].mean()
    lo, hi = np.quantile(means, [0.025, 0.975])
    return MetricPoint(float(arr.mean()), float(lo), float(hi), n)


def useful_by_paired(delta: MetricPoint) -> bool:
    return delta.n_changes > 0 and delta.ci_low > 0.0
