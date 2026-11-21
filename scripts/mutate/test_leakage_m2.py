"""M2 leakage: DB with synthetic runs must not change real dump without --include-mutants."""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path

import pytest

from mutate.testpilot_bin import resolve_testpilot_bin  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def _testpilot() -> str:
    return resolve_testpilot_bin()


def _dump(bin_: str, repo: Path, out: Path) -> None:
    subprocess.check_call(
        [
            bin_,
            "--db",
            str(repo / ".testpilot" / "db.sqlite"),
            "features",
            "dump",
            "--repo",
            str(repo),
            "--out",
            str(out),
        ],
        cwd=ROOT,
    )


def test_dump_identical_with_vs_without_synthetic_runs_in_db():
    """Byte-identical Phase A dump: DB with mutant runs vs without (no --include-mutants)."""
    bin_ = _testpilot()
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        base = td_path / "fixture"
        subprocess.check_call(
            [
                "python3",
                str(ROOT / "scripts" / "generate_features_fixture.py"),
                "--out",
                str(base),
            ],
            cwd=ROOT,
        )
        # Warm snapshots once, then fork DBs so both dumps start from the same state.
        _dump(bin_, base, td_path / "warm.parquet")

        clean_parent = td_path / "clean"
        dirty_parent = td_path / "dirty"
        clean_parent.mkdir()
        dirty_parent.mkdir()
        clean = clean_parent / "proj"
        dirty = dirty_parent / "proj"
        shutil.copytree(base, clean)
        shutil.copytree(base, dirty)

        out_clean = td_path / "clean.parquet"
        _dump(bin_, clean, out_clean)

        conn = sqlite3.connect(dirty / ".testpilot" / "db.sqlite")
        sha = conn.execute("SELECT sha FROM commits LIMIT 1").fetchone()[0]
        conn.execute(
            "INSERT INTO test_runs(sha,source,started_at,finished_at,dirty,synthetic)"
            " VALUES (?,?,1,2,0,1)",
            (sha, "junit"),
        )
        run_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        pk = conn.execute("SELECT id FROM tests LIMIT 1").fetchone()[0]
        conn.execute(
            "INSERT INTO test_results(run_id,test_pk,outcome,duration_ms,flaky,attempts)"
            " VALUES (?,?, 'fail', 1, 0, 1)",
            (run_id, pk),
        )
        conn.commit()
        conn.close()

        out_dirty = td_path / "dirty.parquet"
        _dump(bin_, dirty, out_dirty)
        assert out_clean.read_bytes() == out_dirty.read_bytes()
