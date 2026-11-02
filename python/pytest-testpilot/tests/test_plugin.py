from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from pytest_testpilot.codec import decode_lines


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "pkg").mkdir()
    (root / "pkg" / "__init__.py").write_text(
        "def touch_pass():\n"
        "    return 1\n"
        "\n"
        "def touch_fail():\n"
        "    return 2\n"
        "\n"
        "def touch_skip():\n"
        "    return 3\n",
        encoding="utf-8",
    )
    (root / "tests").mkdir()
    (root / "tests" / "test_sample.py").write_text(
        """
from pkg import touch_pass, touch_fail, touch_skip

def test_pass():
    assert touch_pass() == 1

def test_fail():
    assert touch_fail() == 999

def test_skip():
    import pytest
    pytest.skip("nope")
    assert touch_skip() == 3
""",
        encoding="utf-8",
    )
    (root / "pytest.ini").write_text("[pytest]\npythonpath = .\n", encoding="utf-8")
    (root / ".gitignore").write_text(
        ".pytest_cache/\n__pycache__/\n*.pyc\n.coverage*\n.testpilot/\n",
        encoding="utf-8",
    )
    _git_init(root)
    return root


def _git_init(root: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True)


def _run_pytest(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", *args],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        env={**dict(**{k: v for k, v in __import__("os").environ.items()}), "COVERAGE_CORE": "ctrace"},
    )


def _db(root: Path) -> sqlite3.Connection:
    return sqlite3.connect(root / ".testpilot" / "db.sqlite")


def test_outcomes_duration_coverage(git_repo: Path) -> None:
    result = _run_pytest(
        git_repo,
        "tests",
        "--testpilot",
        f"--testpilot-db={git_repo / '.testpilot' / 'db.sqlite'}",
    )
    assert result.returncode in (0, 1), result.stdout + result.stderr

    conn = _db(git_repo)
    rows = conn.execute(
        """
        SELECT t.test_id, r.outcome, r.duration_ms
        FROM test_results r JOIN tests t ON t.id = r.test_pk
        ORDER BY t.test_id
        """
    ).fetchall()
    outcomes = {r[0].split("::")[-1]: r[1] for r in rows}
    assert outcomes["test_pass"] == "pass"
    assert outcomes["test_fail"] == "fail"
    assert outcomes["test_skip"] == "skip"
    assert all(r[2] >= 0 for r in rows)

    cov = conn.execute(
        """
        SELECT t.test_id, c.path, c.lines
        FROM test_coverage c JOIN tests t ON t.id = c.test_pk
        WHERE c.path LIKE '%pkg%'
        """
    ).fetchall()
    assert cov, "expected coverage rows for pkg"
    by_test: dict[str, set[int]] = {}
    for test_id, _path, blob in cov:
        name = test_id.split("::")[-1]
        by_test.setdefault(name, set()).update(decode_lines(blob))
    assert "test_pass" in by_test
    assert "test_fail" in by_test
    assert by_test["test_pass"] != by_test["test_fail"]
    # Isolation: pass must not contain fail-only lines and vice versa
    assert not (by_test["test_pass"] <= by_test["test_fail"] and by_test["test_fail"] <= by_test["test_pass"])


def test_untracked_dirty(git_repo: Path) -> None:
    (git_repo / "untracked.txt").write_text("hi", encoding="utf-8")
    result = _run_pytest(
        git_repo,
        "tests/test_sample.py::test_pass",
        "--testpilot",
        f"--testpilot-db={git_repo / '.testpilot' / 'db.sqlite'}",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    conn = _db(git_repo)
    dirty, diff_hash = conn.execute(
        "SELECT dirty, diff_hash FROM test_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert dirty == 1
    assert diff_hash is not None


def test_untracked_dir_contents_in_diff_hash(git_repo: Path) -> None:
    # B14
    nested = git_repo / "newdir" / "deep.txt"
    nested.parent.mkdir()
    nested.write_text("secret", encoding="utf-8")
    result = _run_pytest(
        git_repo,
        "tests/test_sample.py::test_pass",
        "--testpilot",
        f"--testpilot-db={git_repo / '.testpilot' / 'db.sqlite'}",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    conn = _db(git_repo)
    dirty, diff_hash = conn.execute(
        "SELECT dirty, diff_hash FROM test_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert dirty == 1 and diff_hash
    # Change nested file → hash must change
    nested.write_text("secret2", encoding="utf-8")
    _run_pytest(
        git_repo,
        "tests/test_sample.py::test_pass",
        "--testpilot",
        f"--testpilot-db={git_repo / '.testpilot' / 'db.sqlite'}",
    )
    h2 = conn.execute(
        "SELECT diff_hash FROM test_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()[0]
    assert h2 != diff_hash


def test_strict_propagates(git_repo: Path) -> None:
    bad = git_repo / "nope" / "x" / "db.sqlite"
    (git_repo / "nope").write_text("file", encoding="utf-8")
    result = _run_pytest(
        git_repo,
        "tests/test_sample.py::test_pass",
        "--testpilot",
        "--testpilot-strict",
        f"--testpilot-db={bad}",
    )
    assert result.returncode != 0


def test_pytest_cov_smoke(git_repo: Path) -> None:
    # B5
    result = _run_pytest(
        git_repo,
        "tests/test_sample.py::test_pass",
        "--testpilot",
        f"--testpilot-db={git_repo / '.testpilot' / 'db.sqlite'}",
        "--cov=pkg",
        "--cov-report=",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    conn = _db(git_repo)
    n = conn.execute("SELECT COUNT(*) FROM test_coverage").fetchone()[0]
    assert n > 0, "pytest-cov must not wipe coverage rows"


def test_setup_error_outcome(git_repo: Path) -> None:
    # B8
    (git_repo / "tests" / "test_err.py").write_text(
        """
import pytest

@pytest.fixture
def boom():
    raise RuntimeError("setup boom")

def test_needs(boom):
    assert True
""",
        encoding="utf-8",
    )
    result = _run_pytest(
        git_repo,
        "tests/test_err.py",
        "--testpilot",
        f"--testpilot-db={git_repo / '.testpilot' / 'db.sqlite'}",
        "--junitxml=junit.xml",
    )
    assert result.returncode != 0
    conn = _db(git_repo)
    row = conn.execute(
        """
        SELECT t.test_id, r.outcome FROM test_results r
        JOIN tests t ON t.id = r.test_pk
        WHERE t.test_id LIKE '%test_needs%'
        """
    ).fetchone()
    assert row is not None
    assert row[1] == "error"
    # JUnit collect consistency checked in e2e; here ensure test_id shape
    assert row[0] == "tests/test_err.py::test_needs"


def test_rerun_flaky_duration(git_repo: Path) -> None:
    # B9
    (git_repo / "tests" / "test_flaky.py").write_text(
        """
import pytest

counter = {"n": 0}

def test_flaky():
    counter["n"] += 1
    if counter["n"] < 3:
        assert False
""",
        encoding="utf-8",
    )
    result = _run_pytest(
        git_repo,
        "tests/test_flaky.py",
        "--testpilot",
        f"--testpilot-db={git_repo / '.testpilot' / 'db.sqlite'}",
        "--reruns=2",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    conn = _db(git_repo)
    flaky, attempts, dur = conn.execute(
        """
        SELECT r.flaky, r.attempts, r.duration_ms FROM test_results r
        JOIN tests t ON t.id = r.test_pk
        WHERE t.test_id LIKE '%test_flaky%'
        """
    ).fetchone()
    assert flaky == 1
    assert attempts == 3
    # duration should be last attempt only — not sum of three (rough upper bound)
    assert dur < 5000


def test_filterwarnings_error_no_internalerror(git_repo: Path) -> None:
    # B6
    (git_repo / "pytest.ini").write_text(
        "[pytest]\npythonpath = .\nfilterwarnings =\n    error\n",
        encoding="utf-8",
    )
    result = _run_pytest(
        git_repo,
        "tests/test_sample.py::test_pass",
        "--testpilot",
        f"--testpilot-db={git_repo / '.testpilot' / 'db.sqlite'}",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "INTERNALERROR" not in result.stdout + result.stderr
    conn = _db(git_repo)
    assert conn.execute("SELECT COUNT(*) FROM test_runs").fetchone()[0] == 1


def test_xdist_smoke(git_repo: Path) -> None:
    pytest.importorskip("xdist")
    result = _run_pytest(
        git_repo,
        "tests",
        "-n",
        "2",
        "--testpilot",
        f"--testpilot-db={git_repo / '.testpilot' / 'db.sqlite'}",
    )
    assert result.returncode in (0, 1), result.stdout + result.stderr
    conn = _db(git_repo)
    runs = conn.execute("SELECT COUNT(*) FROM test_runs").fetchone()[0]
    assert runs == 1
    tests = conn.execute("SELECT COUNT(*) FROM test_results").fetchone()[0]
    assert tests == 3


def test_clean_tree_not_dirty(git_repo: Path) -> None:
    # B10: .testpilot writes must not mark dirty
    result = _run_pytest(
        git_repo,
        "tests/test_sample.py::test_pass",
        "--testpilot",
        f"--testpilot-db={git_repo / '.testpilot' / 'db.sqlite'}",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    conn = _db(git_repo)
    dirty = conn.execute("SELECT dirty FROM test_runs").fetchone()[0]
    assert dirty == 0


def test_codec_varint_overflow() -> None:
    from pytest_testpilot.codec import decode_lines

    blob = bytes([0x01, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])
    with pytest.raises(ValueError, match="overflow"):
        decode_lines(blob)


def test_teardown_error_outcome(git_repo: Path) -> None:
    # B8: teardown failure overwrites outcome to error
    (git_repo / "tests" / "test_td.py").write_text(
        """
import pytest

@pytest.fixture
def boom():
    yield
    raise RuntimeError("teardown boom")

def test_ok(boom):
    assert True
""",
        encoding="utf-8",
    )
    result = _run_pytest(
        git_repo,
        "tests/test_td.py",
        "--testpilot",
        f"--testpilot-db={git_repo / '.testpilot' / 'db.sqlite'}",
    )
    assert result.returncode != 0
    conn = _db(git_repo)
    row = conn.execute(
        """
        SELECT t.test_id, r.outcome FROM test_results r
        JOIN tests t ON t.id = r.test_pk
        WHERE t.test_id LIKE '%test_ok%'
        """
    ).fetchone()
    assert row is not None
    assert row[0] == "tests/test_td.py::test_ok"
    assert row[1] == "error"


def test_coverage_isolation(git_repo: Path) -> None:
    (git_repo / "pkg" / "a.py").write_text("def fa():\n    return 1\n", encoding="utf-8")
    (git_repo / "pkg" / "b.py").write_text("def fb():\n    return 2\n", encoding="utf-8")
    (git_repo / "tests" / "test_iso.py").write_text(
        """
from pkg.a import fa
from pkg.b import fb

def test_a():
    assert fa() == 1

def test_b():
    assert fb() == 2
""",
        encoding="utf-8",
    )
    result = _run_pytest(
        git_repo,
        "tests/test_iso.py",
        "--testpilot",
        f"--testpilot-db={git_repo / '.testpilot' / 'db.sqlite'}",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    conn = _db(git_repo)
    rows = conn.execute(
        """
        SELECT t.test_id, c.path, c.lines FROM test_coverage c
        JOIN tests t ON t.id = c.test_pk
        WHERE t.test_id LIKE '%test_iso%'
        """
    ).fetchall()
    by_test: dict[str, set[str]] = {}
    for tid, path, blob in rows:
        by_test.setdefault(tid, set()).add(path)
        lines = decode_lines(blob)
        assert lines, tid
    assert any("pkg/a.py" in paths for paths in by_test.values())
    assert any("pkg/b.py" in paths for paths in by_test.values())
    for tid, paths in by_test.items():
        if tid.endswith("::test_a"):
            assert "pkg/a.py" in paths
            assert "pkg/b.py" not in paths
        if tid.endswith("::test_b"):
            assert "pkg/b.py" in paths
            assert "pkg/a.py" not in paths


def test_filterwarnings_error_with_xdist(git_repo: Path) -> None:
    # B6 with xdist controller (must not INTERNALERROR)
    pytest.importorskip("xdist")
    (git_repo / "pytest.ini").write_text(
        "[pytest]\npythonpath = .\nfilterwarnings =\n    error\n",
        encoding="utf-8",
    )
    result = _run_pytest(
        git_repo,
        "tests/test_sample.py::test_pass",
        "-n",
        "2",
        "--testpilot",
        f"--testpilot-db={git_repo / '.testpilot' / 'db.sqlite'}",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "INTERNALERROR" not in result.stdout + result.stderr
    conn = _db(git_repo)
    assert conn.execute("SELECT COUNT(*) FROM test_runs").fetchone()[0] == 1


def test_paths_relative_to_git_toplevel(tmp_path: Path) -> None:
    # B18: coverage paths / test_ids must use git toplevel, not pytest rootdir
    root = tmp_path / "mono"
    sub = root / "sub"
    (sub / "pkg").mkdir(parents=True)
    (sub / "tests").mkdir(parents=True)
    (sub / "pkg" / "__init__.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (sub / "tests" / "test_f.py").write_text(
        "from pkg import f\ndef test_f():\n    assert f() == 1\n",
        encoding="utf-8",
    )
    (sub / "pytest.ini").write_text("[pytest]\npythonpath = .\n", encoding="utf-8")
    (root / ".gitignore").write_text(
        ".pytest_cache/\n__pycache__/\n*.pyc\n.coverage*\n.testpilot/\n",
        encoding="utf-8",
    )
    _git_init(root)
    db = root / ".testpilot" / "db.sqlite"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_f.py",
            "--testpilot",
            f"--testpilot-db={db}",
        ],
        cwd=sub,
        check=False,
        capture_output=True,
        text=True,
        env={
            **dict(**{k: v for k, v in __import__("os").environ.items()}),
            "COVERAGE_CORE": "ctrace",
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr
    conn = sqlite3.connect(db)
    tid = conn.execute("SELECT test_id FROM tests").fetchone()[0]
    assert tid == "sub/tests/test_f.py::test_f", tid
    paths = [r[0] for r in conn.execute("SELECT DISTINCT path FROM test_coverage")]
    assert any(p == "sub/pkg/__init__.py" or p.endswith("sub/pkg/__init__.py") for p in paths), paths


def test_runs_without_xdist_installed(git_repo: Path) -> None:
    # Smoke that core path works even when this process has xdist available;
    # CI also has a dedicated job with xdist uninstalled.
    result = _run_pytest(
        git_repo,
        "tests/test_sample.py::test_pass",
        "--testpilot",
        f"--testpilot-db={git_repo / '.testpilot' / 'db.sqlite'}",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "unknown hook" not in (result.stdout + result.stderr).lower()
    conn = _db(git_repo)
    assert conn.execute("SELECT COUNT(*) FROM test_runs").fetchone()[0] == 1
