from pathlib import Path
import sys

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from build_week7_real_pair_features import (  # noqa: E402
    _exclude_audited_groups,
    _exclude_invalid_heads,
    is_modified_test_function,
    normalize_pytest_id,
    stable_new_fail_labels,
)


def test_normalize_pytest_id_matches_feature_store_ids():
    assert (
        normalize_pytest_id("tests.test_math.TestFoo", "test_add[case0]")
        == "tests/test_math.py::TestFoo::test_add[case0]"
    )
    assert normalize_pytest_id("tests.test_math", "test_add") == (
        "tests/test_math.py::test_add"
    )


def test_stable_new_fail_requires_two_base_passes_and_two_head_failures():
    runs = {
        "base1": {"t1": "pass", "t2": "fail", "t3": "pass"},
        "base2": {"t1": "pass", "t2": "fail", "t3": "fail"},
        "head1": {"t1": "fail", "t2": "fail", "t3": "fail"},
        "head2": {"t1": "fail", "t2": "fail", "t3": "pass"},
    }

    assert stable_new_fail_labels(runs) == {"t1": 1, "t2": 0}


def test_tests_missing_from_any_repeat_and_flaky_tests_are_omitted():
    runs = {
        "base1": {"stable": "pass", "flaky": "pass", "missing": "pass"},
        "base2": {"stable": "pass", "flaky": "pass", "missing": "pass"},
        "head1": {"stable": "pass", "flaky": "fail", "missing": "pass"},
        "head2": {"stable": "pass", "flaky": "pass"},
    }

    assert stable_new_fail_labels(runs) == {"stable": 0}


def test_changed_function_matches_path_style_feature_id():
    assert is_modified_test_function(
        "tests/test_commands.py::test_auto_shorthelp[param0]",
        {"tests.test_commands::test_auto_shorthelp"},
    )
    assert not is_modified_test_function(
        "tests/test_basic.py::test_basic_group",
        {"tests.test_commands::test_auto_shorthelp"},
    )


def test_semantic_audit_excludes_all_heads_in_a_pr_group():
    rows = [
        {"repo": "click", "pr_group": ["3872", "3873"], "head": "first"},
        {"repo": "click", "pr_group": ["3872", "3873"], "head": "last"},
        {"repo": "click", "pr_group": ["other"], "head": "keep"},
    ]

    assert _exclude_audited_groups(rows, {("click", ("3872", "3873"))}) == [rows[2]]


def test_invalid_pytest_heads_are_removed_from_feature_build_inputs():
    rows = [
        {"repo": "click", "head": "bad"},
        {"repo": "click", "head": "good"},
        {"repo": "jinja", "head": "bad"},
    ]
    exclusions = {("click", "bad"), ("jinja", "bad")}

    assert _exclude_invalid_heads(rows, exclusions) == [rows[1]]
