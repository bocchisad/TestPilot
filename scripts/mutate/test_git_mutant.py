"""Tests for git_mutant.py using a temporary git repository."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from mutate.git_mutant import (  # noqa: E402
    committer_ts,
    create_mutant_commit,
    delete_mutant_ref,
    mutant_ref,
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.check_call(["git", "init", "-b", "main"], cwd=repo)
    subprocess.check_call(
        ["git", "config", "user.email", "t@example.com"],
        cwd=repo,
    )
    subprocess.check_call(["git", "config", "user.name", "T"], cwd=repo)
    (repo / "pkg").mkdir()
    (repo / "pkg" / "mod.py").write_text("x = 1\n")
    _git(repo, "add", "pkg/mod.py")
    subprocess.check_call(["git", "commit", "-m", "init"], cwd=repo)
    (repo / "pkg" / "mod.py").write_text("x = 2\n")
    _git(repo, "add", "pkg/mod.py")
    subprocess.check_call(
        ["git", "commit", "-m", "change", "--date", "2020-06-01T12:00:00+0000"],
        cwd=repo,
    )
    return repo


def test_mutant_commit_parent_ref_and_dates(git_repo: Path) -> None:
    c_sha = _git(git_repo, "rev-parse", "HEAD")
    ts_c = committer_ts(git_repo, c_sha)
    m_sha = create_mutant_commit(
        git_repo,
        c_sha,
        3,
        {"pkg/mod.py": "x = 0\n"},
    )
    parents = _git(git_repo, "rev-list", "--parents", "-n", "1", m_sha).split()
    assert parents[1] == c_sha
    assert _git(git_repo, "rev-parse", mutant_ref(c_sha, 3)) == m_sha
    author = _git(git_repo, "log", "-1", "--format=%at", m_sha)
    assert int(author) == ts_c + 3
    assert _git(git_repo, "show", f"{m_sha}:pkg/mod.py").strip() == "x = 0"


def test_mutant_commit_deterministic(git_repo: Path) -> None:
    c_sha = _git(git_repo, "rev-parse", "HEAD")
    a = create_mutant_commit(git_repo, c_sha, 1, {"pkg/mod.py": "x = 9\n"})
    delete_mutant_ref(git_repo, c_sha, 1)
    b = create_mutant_commit(git_repo, c_sha, 1, {"pkg/mod.py": "x = 9\n"})
    assert a == b


def test_delete_mutant_ref(git_repo: Path) -> None:
    c_sha = _git(git_repo, "rev-parse", "HEAD")
    create_mutant_commit(git_repo, c_sha, 0, {"pkg/mod.py": "x = 5\n"})
    ref = mutant_ref(c_sha, 0)
    assert _git(git_repo, "rev-parse", ref)
    delete_mutant_ref(git_repo, c_sha, 0)
    with pytest.raises(subprocess.CalledProcessError):
        _git(git_repo, "rev-parse", ref)
