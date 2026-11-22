import subprocess
import sys
import time
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from collect_real_bugfix_pairs import _adds_test_behavior, scan_repository  # noqa: E402
from run_real_pair_calibration import _changed_test_functions  # noqa: E402


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def commit(repo: Path, message: str, files: dict[str, str]) -> str:
    for name, content in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    git(repo, "add", "--all")
    git(
        repo,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-m",
        message,
    )
    return git(repo, "rev-parse", "HEAD")


def test_scan_supports_old_layout_and_all_package_file_extensions(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    commit(
        repo,
        "initial",
        {
            "click/core.py": "old = 0\n",
            "click/build-info.json": "{}\n",
            "tests/test_pkg.py": "def test_pkg(): pass\n",
            "docs/reference.md": "Old docs.\n",
            "fixtures/input.json": "{}\n",
            "CHANGES.rst": "Initial release.\n",
        },
    )
    bug_fix = commit(
        repo,
        "Fix bugfix behavior; fixes #42",
        {
            "click/core.py": "new = 1\n",
            "click/build-info.json": '{"schema": 2}\n',
            "tests/test_pkg.py": "def test_pkg(): assert True\n",
            "docs/reference.md": "Updated docs.\n",
            "fixtures/input.json": '{"x": 1}\n',
            "CHANGES.rst": "Fixed behavior.\n",
        },
    )
    reverted = commit(
        repo,
        'Revert "Fix bugfix behavior; fixes #42"',
        {
            "click/core.py": "old = 0\n",
            "click/build-info.json": "{}\n",
            "tests/test_pkg.py": "def test_pkg(): pass\n",
        },
    )

    result = scan_repository("click", repo)

    assert result["revert_excluded_count"] == 1
    assert (
        result["raw_candidate_count"] == 2
    )  # the initial CHANGES commit remains a lead
    assert result["candidate_count"] == 2
    row = next(row for row in result["candidate_commits"] if row["commit"] == bug_fix)
    assert row["commit"] == bug_fix
    assert row["match_reasons"] == ["message_pattern", "changes_file"]
    assert row["package_source_paths"] == ["click/build-info.json", "click/core.py"]
    assert row["test_paths"] == ["fixtures/input.json", "tests/test_pkg.py"]
    assert row["has_test_behavior_addition"]
    assert reverted not in {
        candidate["commit"] for candidate in result["candidate_commits"]
    }
    complete, error = _adds_test_behavior(repo, "missing-parent", bug_fix)
    assert complete is None
    assert error


def test_jinja_legacy_testsuite_is_kept_as_test_data(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    commit(
        repo,
        "initial",
        {
            "jinja2/runtime.py": "old = 0\n",
            "jinja2/testsuite/test_runtime.py": "def test_x(): pass\n",
        },
    )
    fix = commit(
        repo,
        "bugfix: fix runtime behavior",
        {
            "jinja2/runtime.py": "new = 1\n",
            "jinja2/testsuite/test_runtime.py": "def test_x(): assert True\n",
        },
    )

    result = scan_repository("jinja", repo)
    row = result["candidate_commits"][0]
    assert row["commit"] == fix
    assert row["package_source_paths"] == ["jinja2/runtime.py"]
    assert row["test_paths"] == ["jinja2/testsuite/test_runtime.py"]


def test_patch_id_deduplicates_cherry_pick_and_merge_using_first_parent(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "branch", "-M", "main")
    root = commit(
        repo,
        "initial",
        {
            "src/click/core.py": "old = 0\n",
            "tests/test_core.py": "def test_core(): pass\n",
        },
    )
    git(repo, "checkout", "-b", "fix")
    fix = commit(
        repo,
        "Fix bug in core",
        {
            "src/click/core.py": "new = 1\n",
            "tests/test_core.py": "def test_core(): assert True\n",
        },
    )
    git(repo, "checkout", "main")
    git(
        repo,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "merge",
        "--no-ff",
        "fix",
        "-m",
        "Merge bugfix from fix branch",
    )
    merge = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", "stable", root)
    git(repo, "cherry-pick", fix)

    result = scan_repository("click", repo)

    assert result["raw_candidate_count"] == 3
    assert result["deduplicated_candidate_count"] == 2
    assert result["candidate_count"] == 1
    row = result["candidate_commits"][0]
    assert len(row["duplicate_commits"]) == 3
    merge_row = next(
        item for item in row["duplicate_commits"] if item["commit"] == merge
    )
    assert merge_row["is_merge"] is True
    assert merge_row["diff_parent"] == git(repo, "rev-parse", f"{merge}^1")
    assert merge_row["patch_id"] == row["patch_id"]


def test_changed_test_function_finds_assertion_added_inside_existing_test(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    parent = commit(
        repo,
        "initial",
        {
            "tests/test_one.py": "def test_changed():\n    value = 1\n    assert value\n\ndef test_other():\n    assert True\n"
        },
    )
    fix = commit(
        repo,
        "bugfix: add case",
        {
            "tests/test_one.py": "def test_changed():\n    value = 1\n    assert value\n    assert value == 1\n\ndef test_other():\n    assert True\n"
        },
    )
    functions = _changed_test_functions(
        repo,
        {
            "diff_parent": parent,
            "commit": fix,
            "test_paths": ["tests/test_one.py"],
        },
        time.monotonic() + 10,
    )

    assert functions == {"tests.test_one::test_changed"}
