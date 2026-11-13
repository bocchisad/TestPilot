"""Flaky after rerun must land in DB as flaky=1."""

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

from mutate.run_mutant import _merge_junit_with_flaky, collect_junit_synthetic  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def _testpilot() -> str:
    for cand in (
        ROOT / "target" / "debug" / "testpilot",
        ROOT / "target" / "release" / "testpilot",
    ):
        if cand.is_file():
            return str(cand)
    pytest.skip("testpilot binary not built")


def test_merged_junit_sets_flaky_in_db(tmp_path: Path) -> None:
    bin_ = _testpilot()
    os.environ["TESTPILOT_BIN"] = bin_
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.check_call(["git", "init", "-b", "main"], cwd=repo)
    subprocess.check_call(["git", "config", "user.email", "t@e.com"], cwd=repo)
    subprocess.check_call(["git", "config", "user.name", "T"], cwd=repo)
    (repo / "t.py").write_text("def test_x():\n    assert True\n")
    subprocess.check_call(["git", "add", "."], cwd=repo)
    subprocess.check_call(["git", "commit", "-m", "c"], cwd=repo)
    sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()

    subprocess.check_call([bin_, "init"], cwd=repo)
    db = repo / ".testpilot" / "db.sqlite"
    subprocess.check_call([bin_, "--db", str(db), "collect", "git"], cwd=repo)

    j1 = tmp_path / "j1.xml"
    j2 = tmp_path / "j2.xml"
    merged = tmp_path / "jm.xml"
    # Fail then pass → collapse_reruns sets flaky=1, outcome=pass
    j1.write_text(
        """<?xml version="1.0"?>
<testsuite>
  <testcase classname="t" name="test_x" time="0.01">
    <failure message="boom">boom</failure>
  </testcase>
</testsuite>
""",
        encoding="utf-8",
    )
    j2.write_text(
        """<?xml version="1.0"?>
<testsuite>
  <testcase classname="t" name="test_x" time="0.01"/>
</testsuite>
""",
        encoding="utf-8",
    )
    _merge_junit_with_flaky(j1, j2, {"t::test_x"}, merged)
    ok, msg = collect_junit_synthetic(repo, db, sha, str(merged), force=True)
    assert ok, msg

    row = sqlite3.connect(db).execute(
        """
        SELECT r.flaky, r.outcome FROM test_results r
        JOIN test_runs u ON u.id=r.run_id
        WHERE u.sha=? AND IFNULL(u.synthetic,0)=1
        """,
        (sha,),
    ).fetchone()
    assert row is not None
    assert int(row[0]) == 1
