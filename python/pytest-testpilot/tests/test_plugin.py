from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from pytest_testpilot.codec import decode_lines


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "pkg").mkdir()
    (root / "pkg" / "__init__.py").write_text(
        "def touch_pass():\n    return 1\n\ndef touch_fail():\n    return 2\n\ndef touch_skip():\n    return 3\n",
        encoding="utf-8",
    )
    (root / "tests").mkdir()
    (root / "tests" / "test_sample.py").write_text(
        """
from pkg import touch_pass, touch_fail, touch_skip

def test_pass():
    assert touch_pass() == 1

def test_fail():
    assert touch_fail() == 999

def test_skip():
    import pytest
    pytest.skip("nope")
    assert touch_skip() == 3
""",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True)
    return root


def _run_pytest(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", *args],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )


def _db(root: Path) -> sqlite3.Connection:
    return sqlite3.connect(root / ".testpilot" / "db.sqlite")


def test_outcomes_duration_coverage(git_repo: Path) -> None:
    result = _run_pytest(
        git_repo,
        "tests",
        "--testpilot",
        f"--testpilot-db={git_repo / '.testpilot' / 'db.sqlite'}",
    )
    assert result.returncode in (0, 1), result.stdout + result.stderr

    conn = _db(git_repo)
    rows = conn.execute(
        "SELECT test_id, outcome, duration_ms FROM test_results ORDER BY test_id"
    ).fetchall()
    outcomes = {r[0].split("::")[-1]: r[1] for r in rows}
    assert outcomes["test_pass"] == "pass"
    assert outcomes["test_fail"] == "fail"
    assert outcomes["test_skip"] == "skip"
    assert all(r[2] >= 0 for r in rows)

    cov = conn.execute(
        "SELECT test_id, path, lines FROM test_coverage WHERE path LIKE '%pkg%'"
    ).fetchall()
    assert cov, "expected coverage rows for pkg"
    by_test: dict[str, set[int]] = {}
    for test_id, _path, blob in cov:
        name = test_id.split("::")[-1]
        by_test.setdefault(name, set()).update(decode_lines(blob))
    assert "test_pass" in by_test
    assert "test_fail" in by_test
    # pass should touch X line, fail should touch Y line — not identical sets
    assert by_test["test_pass"] != by_test["test_fail"]


def test_untracked_dirty(git_repo: Path) -> None:
    (git_repo / "untracked.txt").write_text("hi", encoding="utf-8")
    result = _run_pytest(
        git_repo,
        "tests/test_sample.py::test_pass",
        "--testpilot",
        f"--testpilot-db={git_repo / '.testpilot' / 'db.sqlite'}",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    conn = _db(git_repo)
    dirty, diff_hash = conn.execute(
        "SELECT dirty, diff_hash FROM test_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert dirty == 1
    assert diff_hash is not None


def test_strict_propagates(git_repo: Path) -> None:
    bad = git_repo / "nope" / "x" / "db.sqlite"
    (git_repo / "nope").write_text("file", encoding="utf-8")
    result = _run_pytest(
        git_repo,
        "tests/test_sample.py::test_pass",
        "--testpilot",
        "--testpilot-strict",
        f"--testpilot-db={bad}",
    )
    assert result.returncode != 0
