"""Tests for sample_commits.py eligibility and deterministic sampling."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from mutate.sample_commits import (  # noqa: E402
    has_first_parent,
    list_eligible_commits,
    sample_commits,
)

SCHEMA = """
CREATE TABLE commits (
  sha TEXT PRIMARY KEY,
  author_ts INTEGER NOT NULL,
  message TEXT NOT NULL,
  oversized INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE file_changes (
  sha TEXT NOT NULL,
  path TEXT NOT NULL,
  change_type TEXT NOT NULL,
  old_path TEXT,
  lines_added INTEGER NOT NULL DEFAULT 0,
  lines_deleted INTEGER NOT NULL DEFAULT 0,
  is_binary INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (sha, path)
);
CREATE TABLE test_runs (
  id INTEGER PRIMARY KEY,
  sha TEXT NOT NULL,
  source TEXT NOT NULL,
  started_at INTEGER NOT NULL,
  finished_at INTEGER,
  dirty INTEGER NOT NULL DEFAULT 0,
  synthetic INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE tests (id INTEGER PRIMARY KEY, test_id TEXT NOT NULL UNIQUE);
CREATE TABLE test_results (
  run_id INTEGER NOT NULL,
  test_pk INTEGER NOT NULL,
  outcome TEXT NOT NULL,
  duration_ms INTEGER NOT NULL,
  PRIMARY KEY (run_id, test_pk)
);
"""


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "db.sqlite"
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    for i, sha in enumerate(["aaa", "bbb", "ccc"]):
        con.execute(
            "INSERT INTO commits(sha, author_ts, message) VALUES (?,?,?)",
            (sha, i, "m"),
        )
    con.execute(
        """
        INSERT INTO file_changes(sha,path,change_type,lines_added,lines_deleted,is_binary)
        VALUES ('aaa','pkg/a.py','M',1,0,0)
        """
    )
    con.execute(
        """
        INSERT INTO file_changes(sha,path,change_type,lines_added,lines_deleted,is_binary)
        VALUES ('bbb','tests/test_x.py','M',1,0,0)
        """
    )
    con.execute(
        """
        INSERT INTO file_changes(sha,path,change_type,lines_added,lines_deleted,is_binary)
        VALUES ('ccc','pkg/c.py','M',1,0,0)
        """
    )
    con.execute(
        "INSERT INTO test_runs(id,sha,source,started_at,finished_at,dirty,synthetic) "
        "VALUES (1,'aaa','pytest',0,10,0,0)"
    )
    con.execute(
        "INSERT INTO test_runs(id,sha,source,started_at,finished_at,dirty,synthetic) "
        "VALUES (2,'bbb','pytest',0,10,0,0)"
    )
    con.execute(
        "INSERT INTO test_runs(id,sha,source,started_at,finished_at,dirty,synthetic) "
        "VALUES (3,'ccc','pytest',0,10,0,1)"
    )
    con.execute("INSERT INTO tests(id,test_id) VALUES (1,'t::x')")
    con.execute(
        "INSERT INTO test_results(run_id,test_pk,outcome,duration_ms) VALUES (1,1,'pass',1)"
    )
    con.execute(
        "INSERT INTO test_results(run_id,test_pk,outcome,duration_ms) VALUES (2,1,'pass',1)"
    )
    con.execute(
        "INSERT INTO test_results(run_id,test_pk,outcome,duration_ms) VALUES (3,1,'pass',1)"
    )
    con.commit()
    con.close()
    return path


def test_eligible_only_source_py_clean_nonsynthetic(db: Path) -> None:
    eligible = list_eligible_commits(db)
    assert [e.sha for e in eligible] == ["aaa"]
    assert eligible[0].duration_sec == 10.0


def test_sample_commits_deterministic(db: Path) -> None:
    a = sample_commits(db, 1, seed=42)
    b = sample_commits(db, 1, seed=42)
    assert a.n_eligible_C == 1
    assert a.sampled == b.sampled
    assert a.sampled[0].sha == "aaa"


def test_eligible_excludes_root_without_first_parent(
    tmp_path: Path, db: Path
) -> None:
    """With repo set, root SHA (no parent) is dropped even if DB-eligible."""
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.check_call(["git", "init", "-b", "main"], cwd=repo)
    subprocess.check_call(["git", "config", "user.email", "t@e.com"], cwd=repo)
    subprocess.check_call(["git", "config", "user.name", "t"], cwd=repo)
    (repo / "f.py").write_text("x = 1\n")
    subprocess.check_call(["git", "add", "."], cwd=repo)
    subprocess.check_call(["git", "commit", "-m", "root"], cwd=repo)
    root = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    (repo / "f.py").write_text("x = 2\n")
    subprocess.check_call(["git", "add", "."], cwd=repo)
    subprocess.check_call(["git", "commit", "-m", "child"], cwd=repo)
    child = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()

    assert not has_first_parent(repo, root)
    assert has_first_parent(repo, child)

    # Remap DB fixture SHAs is hard; seed a tiny DB with real git SHAs.
    import sqlite3

    path = tmp_path / "gitdb.sqlite"
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    for i, sha in enumerate([root, child]):
        con.execute(
            "INSERT INTO commits(sha, author_ts, message) VALUES (?,?,?)",
            (sha, i, "m"),
        )
        con.execute(
            """
            INSERT INTO file_changes(sha,path,change_type,lines_added,lines_deleted,is_binary)
            VALUES (?,'pkg/a.py','M',1,0,0)
            """,
            (sha,),
        )
        con.execute(
            "INSERT INTO test_runs(id,sha,source,started_at,finished_at,dirty,synthetic) "
            "VALUES (?,?, 'pytest', 0, 10, 0, 0)",
            (i + 1, sha),
        )
        con.execute(
            "INSERT INTO test_results(run_id,test_pk,outcome,duration_ms) VALUES (?,1,'pass',1)",
            (i + 1,),
        )
    con.execute("INSERT INTO tests(id,test_id) VALUES (1,'t::x')")
    con.commit()
    con.close()

    without = list_eligible_commits(path)
    assert {e.sha for e in without} == {root, child}
    with_repo = list_eligible_commits(path, repo=repo)
    assert {e.sha for e in with_repo} == {child}
    sampled = sample_commits(path, 10, seed=1, repo=repo)
    assert all(e.sha != root for e in sampled.sampled)
    assert all(e.sha != root for e in sampled.eligible)
