"""P2: warn_always_failing ignores synthetic runs."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from backfill import warn_always_failing  # noqa: E402


def test_synthetic_run_does_not_trigger_warn(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite"
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE schema_version(version INTEGER PRIMARY KEY);
        INSERT INTO schema_version(version) VALUES (6);
        CREATE TABLE commits(sha TEXT PRIMARY KEY, author_ts INTEGER, message TEXT, oversized INTEGER);
        INSERT INTO commits VALUES ('c1', 1, 'c', 0);
        CREATE TABLE tests(id INTEGER PRIMARY KEY, test_id TEXT UNIQUE);
        INSERT INTO tests(test_id) VALUES ('t::always');
        CREATE TABLE test_runs(
          id INTEGER PRIMARY KEY, sha TEXT, source TEXT, started_at INTEGER,
          finished_at INTEGER, dirty INTEGER, diff_hash TEXT, input_hash TEXT, meta TEXT,
          synthetic INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE test_results(
          run_id INTEGER, test_pk INTEGER, outcome TEXT, duration_ms INTEGER,
          message TEXT, flaky INTEGER, attempts INTEGER
        );
        -- one real pass
        INSERT INTO test_runs VALUES (1,'c1','pytest',1,2,0,NULL,'r',NULL,0);
        INSERT INTO test_results VALUES (1,1,'pass',1,NULL,0,1);
        -- one synthetic fail (would push fail_rate to 50% if counted; add more to hit 90%)
        INSERT INTO test_runs VALUES (2,'c1','pytest',3,4,0,NULL,'s',NULL,1);
        INSERT INTO test_results VALUES (2,1,'fail',1,'boom',0,1);
        """
    )
    # Add 9 more synthetic fails → 90% if counted, 0% if excluded
    for i in range(3, 12):
        con.execute(
            "INSERT INTO test_runs VALUES (?,?,?,?,?,?,?,?,?,1)",
            (i, "c1", "pytest", i, i, 0, None, f"s{i}", None),
        )
        con.execute(
            "INSERT INTO test_results VALUES (?,?,?,?,?,?,?)",
            (i, 1, "fail", 1, "x", 0, 1),
        )
    con.commit()
    con.close()

    rows = warn_always_failing(db, threshold=0.9)
    assert rows == []
