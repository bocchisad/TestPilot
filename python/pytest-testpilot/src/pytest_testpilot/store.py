from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class StoreError(RuntimeError):
    pass


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
        schema = SCHEMA_PATH.read_text(encoding="utf-8")
        with self.conn:
            self.conn.executescript(schema)
            row = self.conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
            if row is None:
                self.conn.execute(
                    "INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,)
                )
            elif int(row[0]) != SCHEMA_VERSION:
                raise StoreError(
                    f"unsupported schema_version {row[0]}; expected {SCHEMA_VERSION}"
                )

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
        with self.conn:
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
            self.conn.executemany(
                """
                INSERT INTO test_results(
                    run_id, test_id, outcome, duration_ms, message, flaky, attempts
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        run_id,
                        r["test_id"],
                        r["outcome"],
                        int(r["duration_ms"]),
                        r.get("message"),
                        1 if r.get("flaky") else 0,
                        int(r.get("attempts", 1)),
                    )
                    for r in results
                ],
            )
            self.conn.executemany(
                """
                INSERT INTO test_coverage(run_id, test_id, path, lines)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (run_id, c["test_id"], c["path"], c["lines"])
                    for c in coverage
                ],
            )
            return run_id

    def close(self) -> None:
        self.conn.close()
