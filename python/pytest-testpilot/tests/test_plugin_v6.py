"""Plugin must write into a Rust-migrated v7 database (partial + selection)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _testpilot() -> str:
    for cand in (
        ROOT / "target" / "debug" / "testpilot",
        ROOT / "target" / "release" / "testpilot",
    ):
        if cand.is_file():
            return str(cand)
    pytest.skip("testpilot binary not built")


def test_plugin_writes_into_rust_v7_db(tmp_path: Path) -> None:
    bin_ = _testpilot()
    repo = tmp_path / "proj"
    repo.mkdir()
    subprocess.check_call(["git", "init", "-b", "main"], cwd=repo)
    subprocess.check_call(["git", "config", "user.email", "t@e.com"], cwd=repo)
    subprocess.check_call(["git", "config", "user.name", "T"], cwd=repo)
    (repo / "t.py").write_text("def test_ok():\n    assert True\n")
    subprocess.check_call(["git", "add", "t.py"], cwd=repo)
    subprocess.check_call(["git", "commit", "-m", "i"], cwd=repo)

    # Create DB via Rust CLI → schema v7 with partial + selection.
    subprocess.check_call([bin_, "init"], cwd=repo)
    db = repo / ".testpilot" / "db.sqlite"
    assert db.is_file()

    import sqlite3

    ver = sqlite3.connect(db).execute("SELECT version FROM schema_version").fetchone()[0]
    assert int(ver) == 7
    cols = {
        r[1]
        for r in sqlite3.connect(db).execute("PRAGMA table_info(test_runs)").fetchall()
    }
    assert "synthetic" in cols
    assert "partial" in cols
    tables = {
        r[0]
        for r in sqlite3.connect(db)
        .execute("SELECT name FROM sqlite_master WHERE type='table'")
        .fetchall()
    }
    assert "test_run_selection" in tables

    sys.path.insert(0, str(ROOT / "python" / "pytest-testpilot" / "src"))
    from pytest_testpilot.store import SCHEMA_VERSION, Store

    assert SCHEMA_VERSION == 7
    store = Store(db)
    run_id = store.insert_run(
        sha="deadbeef",
        source="pytest",
        started_at=1,
        finished_at=2,
        dirty=False,
        diff_hash=None,
        input_hash=None,
        meta=None,
        results=[
            {
                "test_id": "t.py::test_ok",
                "outcome": "pass",
                "duration_ms": 1,
                "message": None,
                "flaky": False,
                "attempts": 1,
            }
        ],
        coverage=[],
        partial=False,
    )
    assert run_id >= 1
    row = sqlite3.connect(db).execute(
        "SELECT synthetic, partial FROM test_runs WHERE id=?", (run_id,)
    ).fetchone()
    assert int(row[0]) == 0
    assert int(row[1]) == 0
