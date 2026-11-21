"""R6: backfill --sha reuses existing clean real runs (no second pytest)."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKFILL = ROOT / "scripts" / "backfill.py"


def test_has_reusable_real_run_helper(tmp_path: Path) -> None:
    # Import after path setup
    sys.path.insert(0, str(ROOT / "scripts"))
    from backfill import _has_reusable_real_run  # noqa: E402

    db = tmp_path / "db.sqlite"
    assert _has_reusable_real_run(db, "deadbeef") is False

    con = sqlite3.connect(str(db))
    con.executescript(
        """
        CREATE TABLE test_runs (
          id INTEGER PRIMARY KEY,
          sha TEXT, dirty INTEGER, synthetic INTEGER, partial INTEGER,
          finished_at INTEGER
        );
        CREATE TABLE test_results (run_id INTEGER, test_id TEXT);
        INSERT INTO test_runs(id, sha, dirty, synthetic, partial, finished_at)
          VALUES (1, 'abc', 0, 0, 0, 100);
        INSERT INTO test_results(run_id, test_id) VALUES (1, 't');
        """
    )
    con.commit()
    con.close()
    assert _has_reusable_real_run(db, "abc") is True
    assert _has_reusable_real_run(db, "zzz") is False


def test_backfill_requires_last_or_sha() -> None:
    r = subprocess.run(
        [sys.executable, str(BACKFILL), "--repo", ".", "--db", "/tmp/x.sqlite"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode != 0
    assert "--last" in (r.stderr + r.stdout) or "sha" in (r.stderr + r.stdout).lower()
