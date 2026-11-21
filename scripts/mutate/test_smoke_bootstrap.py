"""Smoke: run mutate_bootstrap entrypoint 2×2 on the features fixture (train-oracle)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from mutate.testpilot_bin import resolve_testpilot_bin  # noqa: E402


def _testpilot() -> str:
    return resolve_testpilot_bin()


def test_mutate_bootstrap_entrypoint_2x2(tmp_path: Path) -> None:
    bin_ = _testpilot()
    fixture = tmp_path / "fixture"
    subprocess.check_call(
        [
            sys.executable,
            str(SCRIPTS / "generate_features_fixture.py"),
            "--out",
            str(fixture),
        ],
        cwd=ROOT,
    )
    db = fixture / ".testpilot" / "db.sqlite"
    assert db.is_file()

    env = {**os.environ, "TESTPILOT_BIN": bin_, "PYTHONPATH": str(SCRIPTS)}
    out = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "mutate_bootstrap.py"),
            "--repo",
            str(fixture),
            "--db",
            str(db),
            "--commits",
            "2",
            "--mutants-per-commit",
            "2",
            "--seed",
            "7",
            "--timeout-sec",
            "60",
            "--plugin",
            str(ROOT / "python" / "pytest-testpilot"),
            "--json",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stderr + out.stdout
    # Find JSON object in stdout (may have noise)
    text = out.stdout.strip()
    start = text.find("{")
    assert start >= 0, text
    report = json.loads(text[start:])
    assert "n_mutants_attempted" in report
    assert "n_eligible_C" in report
    assert "kill_rate_by_operator" in report
    assert "n_collection_error" in report
    # Entry point ran; may have 0 mutants if fixture hunks lack func-body sites.
    assert report["n_eligible_C"] >= 0
