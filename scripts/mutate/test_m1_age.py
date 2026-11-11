"""M1: t_age_days on mutant M matches a real child with the same author time (not merely C)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from mutate.git_mutant import author_ts, create_mutant_commit  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def _testpilot() -> str:
    for cand in (
        ROOT / "target" / "debug" / "testpilot",
        ROOT / "target" / "release" / "testpilot",
    ):
        if cand.is_file():
            return str(cand)
    pytest.skip("testpilot binary not built")


def _git(repo: Path, *args: str, env: dict | None = None) -> str:
    e = os.environ.copy()
    if env:
        e.update(env)
    return subprocess.check_output(["git", *args], cwd=repo, text=True, env=e).strip()


def test_t_age_days_matches_peer_with_same_author_time(tmp_path: Path) -> None:
    bin_ = _testpilot()
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.check_call(["git", "init", "-b", "main"], cwd=repo)
    subprocess.check_call(["git", "config", "user.email", "t@e.com"], cwd=repo)
    subprocess.check_call(["git", "config", "user.name", "T"], cwd=repo)

    (repo / "pkg").mkdir()
    (repo / "pkg" / "__init__.py").write_text("")
    (repo / "pkg" / "a.py").write_text("def add(x, y):\n    return x + y\n")
    (repo / "test_a.py").write_text(
        "from pkg.a import add\n\ndef test_add():\n    assert add(1, 2) == 3\n"
    )
    _git(repo, "add", ".")
    # Root: author ≠ committer
    env0 = {
        "GIT_AUTHOR_DATE": "@1000000000",
        "GIT_COMMITTER_DATE": "@1000001000",
        "GIT_AUTHOR_NAME": "A",
        "GIT_AUTHOR_EMAIL": "a@e.com",
        "GIT_COMMITTER_NAME": "C",
        "GIT_COMMITTER_EMAIL": "c@e.com",
    }
    subprocess.check_call(
        ["git", "commit", "-m", "root"], cwd=repo, env={**os.environ, **env0}
    )

    # C: change function body
    (repo / "pkg" / "a.py").write_text("def add(x, y):\n    return x + y + 0\n")
    _git(repo, "add", "pkg/a.py")
    env_c = {
        **env0,
        "GIT_AUTHOR_DATE": "@1000002000",
        "GIT_COMMITTER_DATE": "@1000003000",
    }
    subprocess.check_call(
        ["git", "commit", "-m", "C"], cwd=repo, env={**os.environ, **env_c}
    )
    c_sha = _git(repo, "rev-parse", "HEAD")
    a_c = author_ts(repo, c_sha)

    # Real peer D with author = author(C)+5 (same as mutant index 5)
    (repo / "pkg" / "a.py").write_text("def add(x, y):\n    return x + y + 0  # peer\n")
    _git(repo, "add", "pkg/a.py")
    env_d = {
        **env0,
        "GIT_AUTHOR_DATE": f"@{a_c + 5}",
        "GIT_COMMITTER_DATE": f"@{a_c + 5 + 100}",
    }
    subprocess.check_call(
        ["git", "commit", "-m", "D"], cwd=repo, env={**os.environ, **env_d}
    )
    d_sha = _git(repo, "rev-parse", "HEAD")

    # Mutant M off C with index 5 → author(C)+5
    m_sha = create_mutant_commit(
        repo,
        c_sha,
        5,
        {"pkg/a.py": "def add(x, y):\n    return x - y + 0\n"},
    )
    assert int(_git(repo, "log", "-1", "--format=%at", m_sha)) == a_c + 5

    # Populate DB with runs so dump has rows
    subprocess.check_call([bin_, "init"], cwd=repo)
    db = repo / ".testpilot" / "db.sqlite"
    subprocess.check_call([bin_, "--db", str(db), "collect", "git"], cwd=repo)

    # Minimal synthetic run on M + real runs on C and D via junit
    venv = tmp_path / "venv"
    subprocess.check_call([sys.executable, "-m", "venv", str(venv)])
    pip = venv / "bin" / "pip"
    subprocess.check_call(
        [str(pip), "install", "-q", "pytest", "-e", str(ROOT / "python" / "pytest-testpilot")]
    )

    def _run_at(sha: str, junit: Path, synthetic: bool = False) -> None:
        wt = tmp_path / f"wt-{sha[:8]}"
        if wt.exists():
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(wt)], cwd=repo, check=False
            )
        subprocess.check_call(
            ["git", "worktree", "add", "--detach", str(wt), sha], cwd=repo
        )
        # Mutants may fail tests; still write junit for collect.
        subprocess.run(
            [str(venv / "bin" / "pytest"), f"--junitxml={junit}", "-q"],
            cwd=wt,
            check=False,
        )
        cmd = [
            bin_,
            "--db",
            str(db),
            "collect",
            "junit",
            str(junit),
            "--sha",
            sha,
            "--force",
        ]
        if synthetic:
            cmd.append("--synthetic")
        subprocess.check_call(cmd, cwd=repo)
        subprocess.check_call(
            ["git", "worktree", "remove", "--force", str(wt)], cwd=repo
        )

    _run_at(c_sha, tmp_path / "c.xml")
    _run_at(d_sha, tmp_path / "d.xml")
    _run_at(m_sha, tmp_path / "m.xml", synthetic=True)

    out = tmp_path / "feat.parquet"
    subprocess.check_call(
        [
            bin_,
            "--db",
            str(db),
            "features",
            "dump",
            "--out",
            str(out),
            "--include-mutants",
        ],
        cwd=repo,
    )

    import pyarrow.parquet as pq

    table = pq.read_table(out)
    cols = {n: table.column(n).to_pylist() for n in table.column_names}
    n = len(cols["change"])
    ages = {}
    for i in range(n):
        key = (cols["change"][i], cols["test"][i])
        ages[key] = float(cols["t_age_days"][i])
    # Same test id across C/D/M
    test_id = cols["test"][0]
    assert (m_sha, test_id) in ages and (d_sha, test_id) in ages
    assert ages[(m_sha, test_id)] == ages[(d_sha, test_id)]
    # Must NOT simply equal C (author times differ by 5s → age differs)
    if (c_sha, test_id) in ages:
        assert ages[(m_sha, test_id)] != ages[(c_sha, test_id)]
