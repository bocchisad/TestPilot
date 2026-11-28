import random
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from run_pr_intermediate_feasibility import (  # noqa: E402
    _classify_check_runs,
    _promote_pair_timeout,
    _pytest_run_status,
    _pytest_source_invalid_status,
    _select_ci_pytest_rows,
    _stable_positive_test_ids,
)


def _commit(head, pr, index, ci_status, count=3):
    return {
        "head": head,
        "pr": str(pr),
        "commit_index": index,
        "pr_commit_indices": [{"pr": str(pr), "index": index, "count": count}],
        "ci": {"status": ci_status},
    }


def test_ci_classification_uses_latest_run_and_marks_partial_conclusions_unavailable():
    result = _classify_check_runs(
        {
            "check_runs": [
                {
                    "name": "tests",
                    "app": {"slug": "github-actions"},
                    "conclusion": "failure",
                    "completed_at": "2026-01-01T00:00:00Z",
                },
                {
                    "name": "tests",
                    "app": {"slug": "github-actions"},
                    "conclusion": "success",
                    "completed_at": "2026-01-02T00:00:00Z",
                },
                {
                    "name": "lint",
                    "app": {"slug": "github-actions"},
                    "conclusion": "action_required",
                    "completed_at": "2026-01-02T00:00:00Z",
                },
            ]
        }
    )

    assert result["status"] == "unavailable"
    assert result["conclusions"] == ["action_required", "success"]


def test_ci_sampling_keeps_failures_samples_seeded_successes_and_falls_back_first_per_pr():
    rows = [
        _commit("p1-first", 1, 1, "failure"),
        _commit("p1-second", 1, 2, "failure"),
        _commit("p1-third", 1, 3, "failure"),
        _commit("p2-first", 2, 1, "unavailable"),
        _commit("p2-second", 2, 2, "failure"),
        _commit("p3-first", 3, 1, "success", count=20),
    ]
    for index in range(2, 21):
        rows.append(_commit(f"p3-{index}", 3, index, "success", count=20))

    first = _select_ci_pytest_rows(rows, random.Random(20261122))
    second = _select_ci_pytest_rows(rows, random.Random(20261122))
    selected = {row["head"]: row for row in first["selected"]}

    expected_successes = {
        row["head"] for row in second["selected"] if row["head"].startswith("p3-")
    }
    assert set(selected) == {
        "p1-first",
        "p1-second",
        "p1-third",
        "p2-first",
        *expected_successes,
    }
    assert selected["p2-first"]["selection_reasons"] == ["fallback_first_commit_for_pr"]
    assert "p2-second" not in selected
    assert [
        row["head"] for row in first["selected"] if row["head"].startswith("p3-")
    ] == [row["head"] for row in second["selected"] if row["head"].startswith("p3-")]
    assert first["fallback_pr_numbers"] == ["2"]
    assert first["sampled_success_count"] == 2


def test_repeated_pair_positive_requires_the_same_test_to_pass_twice_then_fail_twice():
    outcomes = {
        "base1": {"stable": "pass", "flaky": "pass", "old_failure": "fail"},
        "base2": {"stable": "pass", "flaky": "pass", "old_failure": "fail"},
        "head1": {
            "stable": "fail",
            "flaky": "fail",
            "old_failure": "fail",
            "new": "fail",
        },
        "head2": {
            "stable": "fail",
            "flaky": "pass",
            "old_failure": "fail",
            "new": "fail",
        },
    }

    assert _stable_positive_test_ids(outcomes) == ["stable"]


def test_timeout_is_batch_deadline_only_when_batch_budget_shortened_pair_timeout():
    pair_timeout = {"status": "timeout", "reason": "pair_timeout"}
    batch_limited = {"status": "timeout", "reason": "pair_timeout"}

    assert (
        _promote_pair_timeout(pair_timeout, remaining_s=300, pair_timeout_s=300)[
            "status"
        ]
        == "timeout"
    )
    assert _promote_pair_timeout(
        batch_limited, remaining_s=299, pair_timeout_s=300
    ) == {
        "status": "batch_deadline",
        "reason": "batch_budget_exhausted_before_pair_timeout",
    }


def test_pytest_exit_and_collection_diagnostics_must_confirm_a_complete_run():
    assert _pytest_run_status(1, "1 failed, 10 passed") is None
    assert _pytest_run_status(
        2, "!!!!!!!!!!!!!!!! Interrupted: 21 errors during collection"
    ) == (
        "pytest_interrupted",
        "pytest_interrupted_exit:2",
    )
    assert _pytest_run_status(2, "KeyboardInterrupt") == (
        "pytest_interrupted",
        "pytest_interrupted_exit:2",
    )
    assert _pytest_run_status(3, "INTERNALERROR> plugin traceback") == (
        "pytest_internal_error",
        "pytest_internal_error_exit:3",
    )
    assert _pytest_run_status(2, "ERROR collecting tests/test_x.py") == (
        "test_collection_error",
        "pytest_collection_error_exit:2",
    )
    assert _pytest_run_status(5, "no tests ran") == (
        "pytest_no_tests",
        "pytest_no_tests_exit:5",
    )
    assert _pytest_source_invalid_status("head", "SyntaxError: invalid syntax") == (
        "head_source_invalid",
        "pytest_source_syntax_error:head:SyntaxError",
    )


def test_no_shared_test_ids_are_not_a_completed_negative_pair():
    assert (
        _stable_positive_test_ids(
            {
                "base1": {"a": "pass"},
                "base2": {"a": "pass"},
                "head1": {"b": "fail"},
                "head2": {"b": "fail"},
            }
        )
        == []
    )
