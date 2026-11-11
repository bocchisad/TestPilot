import numpy as np
import pytest

from testpilot_train.metrics import (
    apfd_tied,
    bootstrap_ci,
    paired_delta_ci,
    recall_at_k_tied,
)


def test_all_equal_scores_recall_is_k_over_n():
    y = np.array([1, 0, 1, 0, 0, 1, 0, 0, 0, 0])
    scores = np.zeros(len(y))
    n = len(y)
    for frac in (0.05, 0.10, 0.20):
        k = max(1, int(np.floor(frac * n)))
        assert recall_at_k_tied(y, scores, frac) == pytest.approx(k / n)


def test_borderline_tie_group():
    y = np.array([1, 0, 1, 0, 0])
    scores = np.array([10.0, 9.0, 5.0, 5.0, 5.0])
    r = recall_at_k_tied(y, scores, 0.6)
    assert r == pytest.approx((1 + 1 / 3) / 2)


def test_apfd_all_ties():
    y = np.array([1, 0, 1, 0])
    scores = np.ones(4)
    expected = 1.0 - (2.5 + 2.5) / (2 * 4) + 1.0 / (2 * 4)
    assert apfd_tied(y, scores) == pytest.approx(expected)


def test_cluster_bootstrap_treats_cluster_as_unit():
    """Duplicating changes inside a cluster does not narrow the CI (M3)."""
    per = [
        {"recall@10%": 1.0},
        {"recall@10%": 1.0},
        {"recall@10%": 0.0},
        {"recall@10%": 0.0},
    ]
    clusters = ["a", "a", "b", "b"]
    pt = bootstrap_ci(per, "recall@10%", seed=7, n_boot=400, cluster_ids=clusters)
    assert pt.mean == pytest.approx(0.5)
    assert pt.n_changes == 4
    assert pt.ci_high - pt.ci_low >= 0.35


def test_cluster_bootstrap_matches_change_when_one_per_cluster():
    a = {"recall@10%": 0.5}
    b = {"recall@10%": 0.6}
    c = {"recall@10%": 0.4}
    pt_one = bootstrap_ci([a, b, c], "recall@10%", seed=3, n_boot=300, cluster_ids=["1", "2", "3"])
    pt_dup = bootstrap_ci(
        [a, a, b, c],
        "recall@10%",
        seed=3,
        n_boot=300,
        cluster_ids=["1", "1", "2", "3"],
    )
    assert pt_one.mean == pytest.approx(pt_dup.mean)
    assert pt_one.ci_low == pytest.approx(pt_dup.ci_low, rel=0.1)
    assert pt_one.ci_high == pytest.approx(pt_dup.ci_high, rel=0.1)


def test_paired_delta_ci_shape():
    model = [{"recall@10%": 0.5}, {"recall@10%": 0.6}]
    base = [{"recall@10%": 0.4}, {"recall@10%": 0.55}]
    pt = paired_delta_ci(model, base, "recall@10%", seed=0, n_boot=200)
    assert pt.n_changes == 2
    assert pt.mean == pytest.approx(0.075)
    assert pt.ci_low <= pt.mean <= pt.ci_high
