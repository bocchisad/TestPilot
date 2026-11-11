"""M5: infinite-loop mutant times out; ref deleted; no orphan pg; no results retained."""

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

from mutate.git_mutant import (  # noqa: E402
    create_mutant_commit,
    delete_mutant_ref,
    mutant_ref,
)
from mutate.run_mutant import run_mutant  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _testpilot() -> str:
    for cand in (
        ROOT / "target" / "debug" / "testpilot",
        ROOT / "target" / "release" / "testpilot",
    ):
        if cand.is_file():
            return str(cand)
    pytest.skip("testpilot binary not built")


def test_infinite_loop_timeout_cleans_up(tmp_path: Path) -> None:
    bin_ = _testpilot()
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.check_call(["git", "init", "-b", "main"], cwd=repo)
    subprocess.check_call(["git", "config", "user.email", "t@e.com"], cwd=repo)
    subprocess.check_call(["git", "config", "user.name", "T"], cwd=repo)
    (repo / "pkg").mkdir()
    (repo / "pkg" / "__init__.py").write_text("")
    (repo / "pkg" / "m.py").write_text("def loop():\n    return 0\n")
    (repo / "test_m.py").write_text(
        "from pkg.m import loop\n\ndef test_loop():\n    assert loop() == 0\n"
    )
    _git(repo, "add", ".")
    subprocess.check_call(["git", "commit", "-m", "c"], cwd=repo)
    c_sha = _git(repo, "rev-parse", "HEAD")

    # Mutant: infinite loop in function body.
    loop_src = "def loop():\n    while True:\n        pass\n    return 0\n"
    m_sha = create_mutant_commit(repo, c_sha, 0, {"pkg/m.py": loop_src})

    subprocess.check_call([bin_, "init"], cwd=repo)
    db = repo / ".testpilot" / "db.sqlite"
    subprocess.check_call(
        [bin_, "--db", str(db), "collect", "git", "--sha", c_sha], cwd=repo
    )
    subprocess.check_call(
        [bin_, "--db", str(db), "collect", "git", "--sha", m_sha], cwd=repo
    )

    # Minimal venv with pytest
    venv = tmp_path / "venv"
    subprocess.check_call([sys.executable, "-m", "venv", str(venv)])
    pip = venv / "bin" / "pip"
    subprocess.check_call([str(pip), "install", "-q", "pytest"])
    pytest_bin = venv / "bin" / "pytest"
    py = venv / "bin" / "python"

    env = {**os.environ, "TESTPILOT_BIN": bin_}
    # Patch PATH for collect inside run_mutant
    os.environ["TESTPILOT_BIN"] = bin_

    result = run_mutant(
        repo,
        db,
        m_sha,
        parent_sha=c_sha,
        pytest_bin=pytest_bin,
        python_bin=py,
        work_dir=tmp_path / "runs",
        timeout_override=3,
        pytest_extra_args=["-q", "test_m.py"],
    )
    assert result.timed_out
    assert result.orphan_check_ok
    delete_mutant_ref(repo, c_sha, 0)
    with pytest.raises(subprocess.CalledProcessError):
        _git(repo, "rev-parse", mutant_ref(c_sha, 0))
    # No synthetic results for timed-out mutant
    n = sqlite3.connect(db).execute(
        "SELECT COUNT(*) FROM test_runs WHERE sha=? AND IFNULL(synthetic,0)=1",
        (m_sha,),
    ).fetchone()[0]
    assert n == 0
