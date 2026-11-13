"""High-fail-rate (HF) filter: denominator = rows of the change (run M)."""

from __future__ import annotations

import polars as pl

from testpilot_train.train_lib import _high_fail_change_keys


def _change_frame(
    *,
    repo: str,
    change: str,
    n_fail: int,
    n_pass: int,
    synthetic: int = 1,
) -> pl.DataFrame:
    rows = []
    for i in range(n_fail):
        rows.append(
            {
                "repo": repo,
                "change": change,
                "test": f"t_fail_{i}",
                "label": 1,
                "synthetic": synthetic,
            }
        )
    for i in range(n_pass):
        rows.append(
            {
                "repo": repo,
                "change": change,
                "test": f"t_pass_{i}",
                "label": 0,
                "synthetic": synthetic,
            }
        )
    return pl.DataFrame(rows)


def test_hf_denominator_is_change_row_count():
    """1232/2648 ≈ 46.5% must not be HF; 1219/2313 ≈ 52.7% must be HF.

    Denominator is len(rows) for the change (= tests in dump / run M), not a
    separate collected-N and not unique(test) alone when duplicates exist.
    """
    # Mirrors click top mutant a49a5c… (not HF) and f3dcb7… (HF).
    low = _change_frame(repo="click", change="low", n_fail=1232, n_pass=2648 - 1232)
    high = _change_frame(repo="click", change="high", n_fail=1219, n_pass=2313 - 1219)
    real = _change_frame(
        repo="click", change="real", n_fail=2000, n_pass=0, synthetic=0
    )
    frame = pl.concat([low, high, real])
    keys = _high_fail_change_keys(frame)
    assert ("click", "low") not in keys
    assert ("click", "high") in keys
    # Real (non-synthetic) changes are never HF-excluded.
    assert ("click", "real") not in keys

    rates = frame.group_by(["repo", "change"]).agg(
        (pl.col("label") == 1).sum().alias("n_fail"),
        pl.len().alias("n"),
    )
    by = {r["change"]: r for r in rates.iter_rows(named=True)}
    assert by["low"]["n"] == 2648
    assert by["low"]["n_fail"] / by["low"]["n"] <= 0.5
    assert by["high"]["n_fail"] / by["high"]["n"] > 0.5


def test_hf_ignores_non_synthetic_slice():
    frame = _change_frame(
        repo="r", change="c", n_fail=9, n_pass=1, synthetic=0
    )
    assert _high_fail_change_keys(frame) == set()
