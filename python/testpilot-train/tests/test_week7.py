import polars as pl
import pytest

from testpilot_train.model import _group_sizes, fit_lambdarank
from testpilot_train.data import load_parquets
from testpilot_train.schema import FeaturesSchema
from testpilot_train.train_lib import _budget_metrics_by_change, _feature_sentinels
from testpilot_train.week7 import assign_source_weights, split_groups_by_time


def _frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "repo": ["r", "r", "r", "r", "r", "r"],
            "change": ["s1", "s1", "s1b", "s2", "r1", "r2"],
            "base_change": ["b1", "b1", "b1", "b2", "p1", "p2"],
            "commit_idx": [1, 1, 2, 3, 4, 5],
            "time": [1, 1, 2, 3, 4, 5],
            "synthetic": [1, 1, 1, 1, 0, 0],
        }
    )


def test_source_weights_have_requested_total_group_ratio():
    weighted = assign_source_weights(_frame(), synthetic_to_real=(4, 1))
    totals = weighted.group_by("synthetic").agg(pl.col("sample_weight").sum())
    by_source = {int(row["synthetic"]): row["sample_weight"] for row in totals.to_dicts()}

    assert by_source[1] / by_source[0] == pytest.approx(4.0)


def test_group_split_keeps_all_changes_from_a_source_pair_together():
    frame = _frame()
    split = split_groups_by_time(frame)

    assigned = {}
    for name, changes in split.items():
        for repo, change in changes:
            assigned[(repo, change)] = name
    assert assigned[("r", "s1")] == assigned[("r", "s1b")]
    assert {change for _repo, change in assigned} == {"s1", "s1b", "s2", "r1", "r2"}


def test_lambdarank_group_check_is_contiguous_and_returns_sizes():
    frame = pl.DataFrame(
        {"repo": ["r", "r", "r"], "change": ["a", "a", "b"]}
    )
    assert _group_sizes(frame) == [2, 1]
    with pytest.raises(AssertionError, match="not contiguous"):
        _group_sizes(
            pl.DataFrame(
                {"repo": ["r", "r", "r"], "change": ["a", "b", "a"]}
            )
        )


def test_budget_savings_are_unavailable_when_duration_is_unknown():
    frame = pl.DataFrame(
        {
            "repo": ["r", "r"],
            "change": ["c", "c"],
            "new_fail": [1, 0],
            "dur": [100, -1],
        }
    )
    budget = _budget_metrics_by_change(frame, [1.0, 0.0])[("r", "c")]

    assert budget["n_unknown_durations"] == 1
    assert budget["duration_coverage"] == 0.5
    assert budget["curve"]["time_saved@10%"] is None


def test_feature_sentinel_reports_coverage_and_source_columns():
    frame = pl.DataFrame(
        {
            "cov_known": [1, 0],
            "cov_lines": [1, 0],
            "imp_dist": [1, 99],
            "imp_direct": [1, 0],
        }
    )
    sentinel = _feature_sentinels(frame)

    assert sentinel["coverage_known"]["coverage"] == 0.5
    assert sentinel["import_distance"]["coverage"] == 0.5


def test_lambdarank_accepts_row_aligned_source_weights(
    tiny_parquet, features_schema: FeaturesSchema
):
    frame = load_parquets([tiny_parquet], features_schema).frame.with_columns(
        pl.lit(1.0).alias("sample_weight")
    )

    fit = fit_lambdarank(frame, frame, features_schema, seed=19)

    assert fit.objective == "lambdarank"


def test_budget_savings_use_expected_duration_for_boundary_ties():
    frame = pl.DataFrame(
        {
            "repo": ["r", "r", "r"],
            "change": ["c", "c", "c"],
            "new_fail": [1, 0, 0],
            "dur": [90, 30, 60],
        }
    )

    budget = _budget_metrics_by_change(frame, [1.0, 1.0, 1.0])[("r", "c")]

    # At a 10% budget, one of three tied tests is selected in expectation.
    assert budget["curve"]["time_saved@10%"] == pytest.approx(2 / 3)
    assert budget["curve"]["boundary_tied_tests@10%"] == 3
