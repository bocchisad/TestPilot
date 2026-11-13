"""Sample eligible base commits C from the TestPilot DB (M6)."""

from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from mutate.dbutil import test_runs_partial_gate
from mutate.hunks import is_source_py


@dataclass(frozen=True)
class EligibleCommit:
    sha: str
    duration_sec: float | None


@dataclass(frozen=True)
class SampleResult:
    eligible: list[EligibleCommit]
    n_eligible_C: int
    sampled: list[EligibleCommit]


def _has_clean_nonsynthetic_run(con: sqlite3.Connection, sha: str) -> bool:
    gate = test_runs_partial_gate(con, "r")
    row = con.execute(
        f"""
        SELECT 1 FROM test_runs r
        WHERE r.sha = ? AND r.dirty = 0 AND r.synthetic = 0 {gate}
          AND EXISTS (SELECT 1 FROM test_results tr WHERE tr.run_id = r.id)
        LIMIT 1
        """,
        (sha,),
    ).fetchone()
    return row is not None


def _has_source_py_change(con: sqlite3.Connection, sha: str) -> bool:
    rows = con.execute(
        """
        SELECT path FROM file_changes
        WHERE sha = ? AND change_type IN ('A', 'M', 'R') AND is_binary = 0
        """,
        (sha,),
    ).fetchall()
    for (path,) in rows:
        if path and is_source_py(path):
            return True
    return False


def _duration_sec(con: sqlite3.Connection, sha: str) -> float | None:
    gate = test_runs_partial_gate(con, "r")
    row = con.execute(
        f"""
        SELECT r.finished_at, r.started_at
        FROM test_runs r
        WHERE r.sha = ? AND r.dirty = 0 AND r.synthetic = 0 {gate}
          AND EXISTS (SELECT 1 FROM test_results tr WHERE tr.run_id = r.id)
        ORDER BY r.id DESC
        LIMIT 1
        """,
        (sha,),
    ).fetchone()
    if not row:
        return None
    finished_at, started_at = row[0], row[1]
    if finished_at is not None and started_at is not None and finished_at >= started_at:
        return float(finished_at - started_at)
    run_id_row = con.execute(
        f"""
        SELECT r.id FROM test_runs r
        WHERE r.sha = ? AND r.dirty = 0 AND r.synthetic = 0 {gate}
          AND EXISTS (SELECT 1 FROM test_results tr WHERE tr.run_id = r.id)
        ORDER BY r.id DESC
        LIMIT 1
        """,
        (sha,),
    ).fetchone()
    if not run_id_row:
        return None
    total_ms = con.execute(
        "SELECT COALESCE(SUM(duration_ms), 0) FROM test_results WHERE run_id = ?",
        (run_id_row[0],),
    ).fetchone()[0]
    return float(total_ms) / 1000.0 if total_ms else None


def list_eligible_commits(db: Path) -> list[EligibleCommit]:
    con = sqlite3.connect(str(db))
    try:
        shas = [
            r[0]
            for r in con.execute(
                """
                SELECT DISTINCT c.sha
                FROM commits c
                ORDER BY c.author_ts ASC, c.sha ASC
                """
            ).fetchall()
        ]
        out: list[EligibleCommit] = []
        for sha in shas:
            if not _has_clean_nonsynthetic_run(con, sha):
                continue
            if not _has_source_py_change(con, sha):
                continue
            out.append(EligibleCommit(sha=sha, duration_sec=_duration_sec(con, sha)))
        return out
    finally:
        con.close()


def sample_commits(db: Path, n: int, seed: int) -> SampleResult:
    eligible = list_eligible_commits(db)
    n_eligible = len(eligible)
    if n <= 0 or n_eligible == 0:
        return SampleResult(eligible=eligible, n_eligible_C=n_eligible, sampled=[])
    k = min(n, n_eligible)
    rng = random.Random(seed)
    ordered = sorted(eligible, key=lambda e: (e.sha,))
    sampled = rng.sample(ordered, k)
    sampled.sort(key=lambda e: e.sha)
    return SampleResult(eligible=eligible, n_eligible_C=n_eligible, sampled=sampled)
