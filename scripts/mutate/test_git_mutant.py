"""Tests for git_mutant.py using a temporary git repository."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from mutate.git_mutant import (  # noqa: E402
    MUTATE_IDENTITY,
    author_ts,
    committer_ts,
    create_mutant_commit,
    delete_mutant_ref,
    mutant_ref,
)


def _git(repo: Path, *args: str, env: dict | None = None) -> str:
    e = os.environ.copy()
    if env:
        e.update(env)
    return subprocess.check_output(["git", *args], cwd=repo, text=True, env=e).strip()


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
    # Distinct author vs committer on C (M1).
    env = {
        "GIT_AUTHOR_DATE": "2020-06-01T12:00:00+0000",
        "GIT_COMMITTER_DATE": "2020-06-02T18:00:00+0000",
        "GIT_AUTHOR_NAME": "Author",
        "GIT_AUTHOR_EMAIL": "a@example.com",
        "GIT_COMMITTER_NAME": "Committer",
        "GIT_COMMITTER_EMAIL": "c@example.com",
    }
    subprocess.check_call(["git", "commit", "-m", "init"], cwd=repo, env={**os.environ, **env})
    (repo / "pkg" / "mod.py").write_text("x = 2\n")
    _git(repo, "add", "pkg/mod.py")
    subprocess.check_call(
        ["git", "commit", "-m", "change"],
        cwd=repo,
        env={**os.environ, **env},
    )
    return repo


def test_mutant_commit_parent_ref_and_dates(git_repo: Path) -> None:
    c_sha = _git(git_repo, "rev-parse", "HEAD")
    a_c = author_ts(git_repo, c_sha)
    c_c = committer_ts(git_repo, c_sha)
    assert a_c != c_c
    m_sha = create_mutant_commit(
        git_repo,
        c_sha,
        3,
        {"pkg/mod.py": "x = 0\n"},
    )
    parents = _git(git_repo, "rev-list", "--parents", "-n", "1", m_sha).split()
    assert parents[1] == c_sha
    assert _git(git_repo, "rev-parse", mutant_ref(c_sha, 3)) == m_sha
    assert int(_git(git_repo, "log", "-1", "--format=%at", m_sha)) == a_c + 3
    assert int(_git(git_repo, "log", "-1", "--format=%ct", m_sha)) == c_c + 3
    assert _git(git_repo, "log", "-1", "--format=%an", m_sha) == MUTATE_IDENTITY["GIT_AUTHOR_NAME"]
    assert _git(git_repo, "show", f"{m_sha}:pkg/mod.py").strip() == "x = 0"


def test_mutant_commit_without_user_config(tmp_path: Path) -> None:
    """commit-tree must not depend on git user.name/email config."""
    repo = tmp_path / "bare"
    repo.mkdir()
    subprocess.check_call(["git", "init", "-b", "main"], cwd=repo)
    # Intentionally no user.name / user.email
    (repo / "f.py").write_text("def f():\n    return 1\n")
    env = {
        **os.environ,
        **MUTATE_IDENTITY,
        "GIT_AUTHOR_DATE": "@1000000000",
        "GIT_COMMITTER_DATE": "@1000000000",
    }
    _git(repo, "add", "f.py", env=env)
    subprocess.check_call(
        ["git", "commit", "-m", "c"],
        cwd=repo,
        env=env,
    )
    c_sha = _git(repo, "rev-parse", "HEAD")
    # Clear identity from config if any leaked
    subprocess.run(["git", "config", "--unset-all", "user.name"], cwd=repo, check=False)
    subprocess.run(["git", "config", "--unset-all", "user.email"], cwd=repo, check=False)
    m = create_mutant_commit(repo, c_sha, 0, {"f.py": "def f():\n    return 0\n"})
    assert len(m) == 40


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
