from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 4
SCHEMA_PATH = Path(__file__).with_name("schema.sql")

_MIGRATE_2_TO_3 = """
CREATE TABLE IF NOT EXISTS python_blob_cache (
  blob_sha TEXT PRIMARY KEY,
  imports_json TEXT NOT NULL,
  defs_json TEXT NOT NULL
);
"""

_MIGRATE_3_TO_4 = """
CREATE TABLE IF NOT EXISTS feature_history_snapshot (
  sha TEXT PRIMARY KEY,
  history_json TEXT NOT NULL,
  cochange_json TEXT NOT NULL,
  cov_source_sha TEXT
);
"""


class StoreError(RuntimeError):
    pass


def schema_version_message(found: int, expected: int = SCHEMA_VERSION) -> str:
    if found < expected:
        return (
            f"schema outdated (version {found}; need {expected}): "
            "delete .testpilot and run `testpilot init`"
        )
    return (
        f"schema too new (version {found}; this build supports {expected}): "
        "upgrade testpilot"
    )


class Store:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self._apply_pragmas()
        self.migrate()

    def _apply_pragmas(self) -> None:
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=5000")

    def migrate(self) -> None:
        # N1: validate existing version before applying DDL so a v1 DB does not
        # die with "no such column: test_pk" while creating new indexes.
        try:
            row = self.conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
        except sqlite3.Error:
            row = None
        if row is not None:
            found = int(row[0])
            if found == SCHEMA_VERSION:
                return
            # Additive upgrades only; v1 still requires delete.
            if found == 2 and SCHEMA_VERSION >= 3:
                self.conn.executescript(_MIGRATE_2_TO_3)
                if SCHEMA_VERSION >= 4:
                    self.conn.executescript(_MIGRATE_3_TO_4)
                self.conn.execute(
                    "UPDATE schema_version SET version = ?", (SCHEMA_VERSION,)
                )
                self.conn.commit()
                return
            if found == 3 and SCHEMA_VERSION == 4:
                self.conn.executescript(_MIGRATE_3_TO_4)
                self.conn.execute(
                    "UPDATE schema_version SET version = ?", (SCHEMA_VERSION,)
                )
                self.conn.commit()
                return
            raise StoreError(schema_version_message(found))

        schema = SCHEMA_PATH.read_text(encoding="utf-8")
        statements = [s.strip() for s in schema.split(";") if s.strip()]
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            for stmt in statements:
                self.conn.execute(stmt)
            row = self.conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
            if row is None:
                self.conn.execute(
                    "INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,)
                )
            elif int(row[0]) != SCHEMA_VERSION:
                raise StoreError(schema_version_message(int(row[0])))
            self.conn.execute("COMMIT")
        except Exception:
            with contextlib.suppress(sqlite3.Error):
                self.conn.execute("ROLLBACK")
            raise

    def _upsert_test(self, test_id: str) -> int:
        self.conn.execute("INSERT OR IGNORE INTO tests(test_id) VALUES (?)", (test_id,))
        row = self.conn.execute(
            "SELECT id FROM tests WHERE test_id = ?", (test_id,)
        ).fetchone()
        assert row is not None
        return int(row[0])

    def insert_run(
        self,
        *,
        sha: str,
        source: str,
        started_at: int,
        finished_at: int | None,
        dirty: bool,
        diff_hash: str | None,
        input_hash: str | None,
        meta: str | None,
        results: list[dict[str, Any]],
        coverage: list[dict[str, Any]],
    ) -> int:
        self.conn.execute("BEGIN")
        try:
            cur = self.conn.execute(
                """
                INSERT INTO test_runs(
                    sha, source, started_at, finished_at, dirty, diff_hash, input_hash, meta
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sha,
                    source,
                    started_at,
                    finished_at,
                    1 if dirty else 0,
                    diff_hash,
                    input_hash,
                    meta,
                ),
            )
            run_id = int(cur.lastrowid)
            for r in results:
                pk = self._upsert_test(r["test_id"])
                self.conn.execute(
                    """
                    INSERT INTO test_results(
                        run_id, test_pk, outcome, duration_ms, message, flaky, attempts
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        pk,
                        r["outcome"],
                        int(r["duration_ms"]),
                        r.get("message"),
                        1 if r.get("flaky") else 0,
                        int(r.get("attempts", 1)),
                    ),
                )
            for c in coverage:
                pk = self._upsert_test(c["test_id"])
                self.conn.execute(
                    """
                    INSERT INTO test_coverage(run_id, test_pk, path, lines)
                    VALUES (?, ?, ?, ?)
                    """,
                    (run_id, pk, c["path"], c["lines"]),
                )
            self.conn.execute("COMMIT")
            return run_id
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

    def close(self) -> None:
        self.conn.close()
