"""SQLite helpers that stay compatible with pre-v7 TestPilot DBs."""

from __future__ import annotations

import sqlite3


def test_runs_partial_gate(con: sqlite3.Connection, alias: str = "r") -> str:
    """SQL fragment excluding partial select runs when the column exists.

    Pre-v7 databases lack ``test_runs.partial``; referencing it would raise
    ``no such column``. When the column is present, keep the Week-5 filter.
    """
    cols = {row[1] for row in con.execute("PRAGMA table_info(test_runs)")}
    if "partial" in cols:
        return f"AND IFNULL({alias}.partial, 0) = 0"
    return ""
