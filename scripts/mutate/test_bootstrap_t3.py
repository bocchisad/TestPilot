"""T3 gaps: first-parent, jobs queue, time-budget, SIGINT, resume pairs."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = Path(__file__).resolve().parents[2]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from mutate.ast_ops import MutationSite  # noqa: E402
from mutate.run_mutant import MutantRunResult  # noqa: E402
from mutate_bootstrap import BootstrapStats, run_bootstrap  # noqa: E402
import mutate_bootstrap as mb  # noqa: E402


def _git(cwd: Path, *args: str) -> str:
    r = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return (r.stdout or "").strip()


def test_rev_list_first_parent_excludes_side_branch(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@e.com")
    _git(repo, "config", "user.name", "t")
    (repo / "f.txt").write_text("a\n")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-m", "A")
    a = _git(repo, "rev-parse", "HEAD")
    (repo / "f.txt").write_text("b\n")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-m", "B")
    _git(repo, "checkout", "-b", "side", a)
    (repo / "side.txt").write_text("s\n")
    _git(repo, "add", "side.txt")
    _git(repo, "commit", "-m", "S")
    s = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "main")
    _git(repo, "merge", "--no-ff", "-m", "M", "side")

    plain = _git(repo, "rev-list", "-n4", "HEAD").splitlines()
    assert any(x.startswith(s[:12]) or x == s for x in plain)

    fp = _git(repo, "rev-list", "--first-parent", "-n4", "HEAD").splitlines()
    assert not any(x.startswith(s[:12]) or x == s for x in fp)


def _seed_eligible(tmp_path: Path) -> tuple[Path, Path, str]:
    """Tiny git+DB with one eligible C (source .py change + clean real run)."""
    repo = tmp_path / "proj"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@e.com")
    _git(repo, "config", "user.name", "t")
    (repo / "pkg").mkdir()
    (repo / "pkg" / "__init__.py").write_text("def f(x):\n    return x + 1\n")
    (repo / "pyproject.toml").write_text(
        '[project]\nname="p"\nversion="0"\n'
        '[build-system]\nrequires=["setuptools"]\n'
        'build-backend="setuptools.build_meta"\n'
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    (repo / "pkg" / "__init__.py").write_text(
        "def f(x):\n    return x + 1\n\ndef g(x):\n    return x * 2\n"
    )
    _git(repo, "add", "pkg/__init__.py")
    _git(repo, "commit", "-m", "C")
    c_sha = _git(repo, "rev-parse", "HEAD")

    db = tmp_path / "db.sqlite"
    schema = ROOT / "python" / "pytest-testpilot" / "src" / "pytest_testpilot" / "schema.sql"
    con = sqlite3.connect(str(db))
    con.executescript(schema.read_text(encoding="utf-8"))
    con.execute(
        "INSERT INTO commits(sha, author_ts, message) VALUES (?, 1, 'C')",
        (c_sha,),
    )
    con.execute(
        """
        INSERT INTO file_changes(sha, path, change_type, is_binary)
        VALUES (?, 'pkg/__init__.py', 'M', 0)
        """,
        (c_sha,),
    )
    con.execute(
        """
        INSERT INTO test_runs(sha, source, dirty, synthetic, partial, finished_at, started_at)
        VALUES (?, 'pytest', 0, 0, 0, 10, 0)
        """,
        (c_sha,),
    )
    run_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.execute("INSERT INTO tests(id, test_id) VALUES (1, 't')")
    con.execute(
        """
        INSERT INTO test_results(run_id, test_pk, outcome, duration_ms, flaky)
        VALUES (?, 1, 'pass', 1, 0)
        """,
        (run_id,),
    )
    con.commit()
    con.close()
    return repo, db, c_sha


def _fake_site(i: int = 0) -> MutationSite:
    return MutationSite(
        op_id="bin_ops",
        node_key=f"n{i}",
        lineno=1 + i,
        col_offset=0,
        end_lineno=1 + i,
        end_col_offset=1,
    )


@pytest.fixture(autouse=True)
def _clear_stop_flags() -> None:
    mb._STOP.clear()
    mb._HARD_STOP.clear()
    yield
    mb._STOP.clear()
    mb._HARD_STOP.clear()


def test_jobs_same_counts_and_parallel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, db, c_sha = _seed_eligible(tmp_path)
    sites = [
        ("pkg/__init__.py", _fake_site(0), "def f(x):\n    return x - 1\n"),
        ("pkg/__init__.py", _fake_site(1), "def f(x):\n    return x + 2\n"),
        ("pkg/__init__.py", _fake_site(2), "def f(x):\n    return x + 3\n"),
        ("pkg/__init__.py", _fake_site(3), "def f(x):\n    return x + 4\n"),
    ]
    monkeypatch.setattr(
        mb,
        "_candidate_mutants",
        lambda *a, **k: (sites, 0),
    )
    monkeypatch.setattr(mb, "sample_commits", lambda *a, **k: type(
        "S",
        (),
        {
            "n_eligible_C": 1,
            "sampled": [type("E", (), {"sha": c_sha, "duration_sec": 0.1})()],
        },
    )())
    monkeypatch.setattr(mb, "first_parent", lambda *a, **k: "parent")
    monkeypatch.setattr(mb, "create_mutant_commit", lambda *a, **k: "m" + str(k.get("index", a[2] if len(a) > 2 else 0)))
    monkeypatch.setattr(
        mb,
        "collect_git_sha",
        lambda *a, **k: type("R", (), {"ok": True, "note": "", "stderr": ""})(),
    )
    monkeypatch.setattr(mb, "synthetic_run_exists", lambda *a, **k: False)
    monkeypatch.setattr(mb, "install_project", lambda *a, **k: (True, ""))
    monkeypatch.setattr(mb, "resolve_plugin", lambda *a, **k: ROOT / "python" / "pytest-testpilot")
    monkeypatch.setattr(mb, "run", lambda *a, **k: type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})())

    lock = threading.Lock()
    active = {"n": 0, "max": 0}
    calls = {"n": 0}

    def fake_run_mutant(*_a, **_k):
        with lock:
            active["n"] += 1
            active["max"] = max(active["max"], active["n"])
            calls["n"] += 1
        time.sleep(0.15)
        with lock:
            active["n"] -= 1
        return MutantRunResult(ok=True, collect_ok=True, killed=True, n_new_fail=1)

    monkeypatch.setattr(mb, "run_mutant", fake_run_mutant)

    # Avoid real venv creation.
    monkeypatch.setattr(mb, "run", lambda *a, **k: type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})())

    s1 = run_bootstrap(
        repo,
        db,
        commits_n=1,
        mutants_k=4,
        seed=1,
        timeout_sec=5,
        python=Path(sys.executable),
        plugin=ROOT / "python" / "pytest-testpilot",
        pytest_args="-q",
        work_root=tmp_path / "wt1",
        jobs=1,
    )
    calls1 = calls["n"]
    max1 = active["max"]
    calls["n"] = 0
    active["max"] = 0

    s2 = run_bootstrap(
        repo,
        db,
        commits_n=1,
        mutants_k=4,
        seed=1,
        timeout_sec=5,
        python=Path(sys.executable),
        plugin=ROOT / "python" / "pytest-testpilot",
        pytest_args="-q",
        work_root=tmp_path / "wt2",
        jobs=2,
    )
    assert s1.n_mutants_attempted == s2.n_mutants_attempted == 4
    assert s1.n_killed == s2.n_killed
    assert calls["n"] == 4
    assert active["max"] >= 2, "jobs=2 should run two pytest workers overlapping"
    assert max1 == 1


def test_time_budget_soft_stop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, db, c_sha = _seed_eligible(tmp_path)
    sites = [
        ("pkg/__init__.py", _fake_site(i), f"def f(x):\n    return x + {i}\n")
        for i in range(6)
    ]
    monkeypatch.setattr(mb, "_candidate_mutants", lambda *a, **k: (sites, 0))
    monkeypatch.setattr(mb, "sample_commits", lambda *a, **k: type(
        "S",
        (),
        {
            "n_eligible_C": 1,
            "sampled": [type("E", (), {"sha": c_sha, "duration_sec": 0.1})()],
        },
    )())
    monkeypatch.setattr(mb, "first_parent", lambda *a, **k: "parent")
    monkeypatch.setattr(
        mb,
        "create_mutant_commit",
        lambda *a, **k: f"m{a[2] if len(a) > 2 else 0}",
    )
    monkeypatch.setattr(
        mb,
        "collect_git_sha",
        lambda *a, **k: type("R", (), {"ok": True, "note": "", "stderr": ""})(),
    )
    monkeypatch.setattr(mb, "synthetic_run_exists", lambda *a, **k: False)
    monkeypatch.setattr(mb, "run", lambda *a, **k: type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    monkeypatch.setattr(mb, "resolve_plugin", lambda *a, **k: ROOT / "python" / "pytest-testpilot")

    def fake_run_mutant(*_a, **_k):
        time.sleep(0.2)
        return MutantRunResult(ok=True, collect_ok=True, killed=True, n_new_fail=1)

    monkeypatch.setattr(mb, "run_mutant", fake_run_mutant)

    deadline = time.time() + 0.25
    stats = run_bootstrap(
        repo,
        db,
        commits_n=1,
        mutants_k=6,
        seed=1,
        timeout_sec=5,
        python=Path(sys.executable),
        plugin=None,
        pytest_args="-q",
        work_root=tmp_path / "wt",
        jobs=1,
        deadline_unix=deadline,
    )
    assert stats.interrupted is True
    assert 0 < stats.n_mutants_attempted < 6
    assert len(stats.completed_pairs) == stats.n_mutants_attempted


def test_sigint_sets_interrupted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, db, c_sha = _seed_eligible(tmp_path)
    sites = [
        ("pkg/__init__.py", _fake_site(i), f"def f(x):\n    return x + {i}\n")
        for i in range(4)
    ]
    monkeypatch.setattr(mb, "_candidate_mutants", lambda *a, **k: (sites, 0))
    monkeypatch.setattr(mb, "sample_commits", lambda *a, **k: type(
        "S",
        (),
        {
            "n_eligible_C": 1,
            "sampled": [type("E", (), {"sha": c_sha, "duration_sec": 0.1})()],
        },
    )())
    monkeypatch.setattr(mb, "first_parent", lambda *a, **k: "parent")
    monkeypatch.setattr(
        mb,
        "create_mutant_commit",
        lambda *a, **k: f"m{a[2] if len(a) > 2 else 0}",
    )
    monkeypatch.setattr(
        mb,
        "collect_git_sha",
        lambda *a, **k: type("R", (), {"ok": True, "note": "", "stderr": ""})(),
    )
    monkeypatch.setattr(mb, "synthetic_run_exists", lambda *a, **k: False)
    monkeypatch.setattr(mb, "run", lambda *a, **k: type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    monkeypatch.setattr(mb, "resolve_plugin", lambda *a, **k: ROOT / "python" / "pytest-testpilot")

    started = threading.Event()

    def fake_run_mutant(*_a, **_k):
        started.set()
        time.sleep(2.0)
        return MutantRunResult(ok=True, collect_ok=True)

    monkeypatch.setattr(mb, "run_mutant", fake_run_mutant)

    mb._STOP.clear()
    mb._HARD_STOP.clear()
    holder: dict[str, BootstrapStats] = {}

    def worker() -> None:
        holder["s"] = run_bootstrap(
            repo,
            db,
            commits_n=1,
            mutants_k=4,
            seed=1,
            timeout_sec=5,
            python=Path(sys.executable),
            plugin=None,
            pytest_args="-q",
            work_root=tmp_path / "wt",
            jobs=1,
        )

    t = threading.Thread(target=worker)
    t.start()
    assert started.wait(timeout=2)
    mb._STOP.set()
    mb._HARD_STOP.set()
    t.join(timeout=5)
    assert not t.is_alive()
    assert holder["s"].interrupted is True


def test_resume_skips_completed_pairs_no_rerun(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, db, c_sha = _seed_eligible(tmp_path)
    sites = [
        ("pkg/__init__.py", _fake_site(i), f"def f(x):\n    return x + {i}\n")
        for i in range(3)
    ]
    monkeypatch.setattr(mb, "_candidate_mutants", lambda *a, **k: (sites, 0))
    monkeypatch.setattr(mb, "sample_commits", lambda *a, **k: type(
        "S",
        (),
        {
            "n_eligible_C": 1,
            "sampled": [type("E", (), {"sha": c_sha, "duration_sec": 0.1})()],
        },
    )())
    monkeypatch.setattr(mb, "first_parent", lambda *a, **k: "parent")
    monkeypatch.setattr(
        mb,
        "create_mutant_commit",
        lambda *a, **k: f"m{a[2] if len(a) > 2 else 0}",
    )
    monkeypatch.setattr(
        mb,
        "collect_git_sha",
        lambda *a, **k: type("R", (), {"ok": True, "note": "", "stderr": ""})(),
    )
    monkeypatch.setattr(mb, "synthetic_run_exists", lambda *a, **k: False)
    monkeypatch.setattr(mb, "run", lambda *a, **k: type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    monkeypatch.setattr(mb, "resolve_plugin", lambda *a, **k: ROOT / "python" / "pytest-testpilot")

    calls = {"n": 0}

    def fake_run_mutant(*_a, **_k):
        calls["n"] += 1
        return MutantRunResult(ok=True, collect_ok=True, killed=True, n_new_fail=1)

    monkeypatch.setattr(mb, "run_mutant", fake_run_mutant)

    done = {f"{c_sha}:0", f"{c_sha}:1"}
    stats = run_bootstrap(
        repo,
        db,
        commits_n=1,
        mutants_k=3,
        seed=1,
        timeout_sec=5,
        python=Path(sys.executable),
        plugin=None,
        pytest_args="-q",
        work_root=tmp_path / "wt",
        jobs=1,
        completed_pairs=done,
    )
    assert stats.n_skipped_completed == 2
    assert calls["n"] == 1
    assert stats.n_mutants_attempted == 1


def test_cli_jobs_and_deadline_flags() -> None:
    r = subprocess.run(
        [sys.executable, str(_SCRIPTS / "mutate_bootstrap.py"), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0
    assert "--jobs" in r.stdout
    assert "--deadline-unix" in r.stdout
    assert "--completed-pairs" in r.stdout
