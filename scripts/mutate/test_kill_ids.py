"""P1: kill uses DB-normalized IDs; persistent fail on C is not a kill."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from mutate.run_mutant import (  # noqa: E402
    collect_junit_synthetic,
    new_fail_count,
    parent_fail_set,
    run_fail_set,
)

ROOT = Path(__file__).resolve().parents[2]


def _testpilot() -> str:
    for cand in (
        ROOT / "target" / "debug" / "testpilot",
        ROOT / "target" / "release" / "testpilot",
    ):
        if cand.is_file():
            return str(cand)
    pytest.skip("testpilot binary not built")


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def test_junit_and_plugin_ids_match_after_collect(tmp_path: Path) -> None:
    """Same test collected via junit uses the same ID the plugin would store."""
    bin_ = _testpilot()
    os.environ["TESTPILOT_BIN"] = bin_
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.check_call(["git", "init", "-b", "main"], cwd=repo)
    subprocess.check_call(["git", "config", "user.email", "t@e.com"], cwd=repo)
    subprocess.check_call(["git", "config", "user.name", "T"], cwd=repo)
    (repo / "tests").mkdir()
    (repo / "tests" / "test_echo.py").write_text(
        "def test_echo():\n    assert True\n"
    )
    _git(repo, "add", ".")
    subprocess.check_call(["git", "commit", "-m", "c"], cwd=repo)
    sha = _git(repo, "rev-parse", "HEAD")

    subprocess.check_call([bin_, "init"], cwd=repo)
    db = repo / ".testpilot" / "db.sqlite"
    subprocess.check_call([bin_, "--db", str(db), "collect", "git"], cwd=repo)

    venv = tmp_path / "venv"
    subprocess.check_call([sys.executable, "-m", "venv", str(venv)])
    pip = venv / "bin" / "pip"
    subprocess.check_call(
        [
            str(pip),
            "install",
            "-q",
            "pytest",
            "-e",
            str(ROOT / "python" / "pytest-testpilot"),
        ]
    )
    # Plugin path
    r = subprocess.run(
        [
            str(venv / "bin" / "pytest"),
            "--testpilot",
            f"--testpilot-db={db}",
            "-q",
            "tests/test_echo.py",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    plugin_ids = {
        str(t[0])
        for t in sqlite3.connect(db).execute(
            """
            SELECT t.test_id FROM test_results r
            JOIN tests t ON t.id=r.test_pk
            JOIN test_runs u ON u.id=r.run_id
            WHERE u.sha=? AND IFNULL(u.synthetic,0)=0
            """,
            (sha,),
        )
    }

    junit = tmp_path / "j.xml"
    subprocess.run(
        [str(venv / "bin" / "pytest"), f"--junitxml={junit}", "-q", "tests/test_echo.py"],
        cwd=repo,
        check=False,
    )
    # Fresh sha slot for synthetic so we don't collide input_hash
    ok, msg = collect_junit_synthetic(
        repo, db, sha, str(junit), force=True
    )
    assert ok, msg
    junit_ids = run_fail_set(db, sha, synthetic=True)  # may be empty (pass)
    # All collected synthetic test_ids for this sha
    syn_ids = {
        str(t[0])
        for t in sqlite3.connect(db).execute(
            """
            SELECT t.test_id FROM test_results r
            JOIN tests t ON t.id=r.test_pk
            JOIN test_runs u ON u.id=r.run_id
            WHERE u.sha=? AND IFNULL(u.synthetic,0)=1
            """,
            (sha,),
        )
    }
    assert plugin_ids == syn_ids
    assert "tests/test_echo.py::test_echo" in plugin_ids


def test_persistent_fail_is_not_kill_new_fail_is(tmp_path: Path) -> None:
    """C has persistent fail; same fail on M → kill=0; extra fail on M → kill=1."""
    db = tmp_path / "db.sqlite"
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE schema_version(version INTEGER PRIMARY KEY);
        INSERT INTO schema_version(version) VALUES (6);
        CREATE TABLE commits(sha TEXT PRIMARY KEY, author_ts INTEGER, message TEXT, oversized INTEGER);
        INSERT INTO commits VALUES ('C', 1, 'c', 0);
        INSERT INTO commits VALUES ('M1', 2, 'm1', 0);
        INSERT INTO commits VALUES ('M2', 3, 'm2', 0);
        CREATE TABLE tests(id INTEGER PRIMARY KEY, test_id TEXT UNIQUE);
        INSERT INTO tests(test_id) VALUES
          ('tests/test_utils.py::test_pager'),
          ('tests/test_core.py::test_new');
        CREATE TABLE test_runs(
          id INTEGER PRIMARY KEY, sha TEXT, source TEXT, started_at INTEGER,
          finished_at INTEGER, dirty INTEGER, diff_hash TEXT, input_hash TEXT, meta TEXT,
          synthetic INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE test_results(
          run_id INTEGER, test_pk INTEGER, outcome TEXT, duration_ms INTEGER,
          message TEXT, flaky INTEGER, attempts INTEGER
        );
        -- C: pager fails persistently
        INSERT INTO test_runs VALUES (1,'C','pytest',1,2,0,NULL,'c',NULL,0);
        INSERT INTO test_results VALUES (1,1,'fail',1,'pager',0,1);
        INSERT INTO test_results VALUES (1,2,'pass',1,NULL,0,1);
        -- M1: same pager fail only → not a kill
        INSERT INTO test_runs VALUES (2,'M1','junit',3,4,0,NULL,'m1',NULL,1);
        INSERT INTO test_results VALUES (2,1,'fail',1,'pager',0,1);
        INSERT INTO test_results VALUES (2,2,'pass',1,NULL,0,1);
        -- M2: pager + new fail → kill
        INSERT INTO test_runs VALUES (3,'M2','junit',5,6,0,NULL,'m2',NULL,1);
        INSERT INTO test_results VALUES (3,1,'fail',1,'pager',0,1);
        INSERT INTO test_results VALUES (3,2,'fail',1,'new',0,1);
        """
    )
    con.commit()
    con.close()

    fails_c = parent_fail_set(db, "C")
    assert fails_c == {"tests/test_utils.py::test_pager"}

    fails_m1 = run_fail_set(db, "M1", synthetic=True)
    assert new_fail_count(fails_m1, fails_c) == 0

    fails_m2 = run_fail_set(db, "M2", synthetic=True)
    assert new_fail_count(fails_m2, fails_c) == 1
    assert fails_m2 - fails_c == {"tests/test_core.py::test_new"}
