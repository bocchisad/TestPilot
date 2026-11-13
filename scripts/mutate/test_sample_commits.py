"""Tests for sample_commits.py eligibility and deterministic sampling."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from mutate.sample_commits import list_eligible_commits, sample_commits  # noqa: E402

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
