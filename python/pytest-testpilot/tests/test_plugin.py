from __future__ import annotations

import os
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
    # B10/N2: do NOT pre-ignore .testpilot/ or .coverage* — dirty filter must handle them.
    (root / ".gitignore").write_text(
        ".pytest_cache/\n__pycache__/\n*.pyc\n",
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


_PLUGIN_SRC = Path(__file__).resolve().parents[1] / "src"


def _run_pytest(
    root: Path,
    *args: str,
    cov_core: str | None = "ctrace",
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items()}
    if cov_core is None:
        env.pop("COVERAGE_CORE", None)
    else:
        env["COVERAGE_CORE"] = cov_core
    # Ensure subprocesses see the plugin even if the editable install is flaky.
    src = str(_PLUGIN_SRC)
    prev = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = src if not prev else src + os.pathsep + prev
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "pytest", *args],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        env=env,
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
    # B10/N2: .testpilot and .coverage must not mark dirty even when not gitignored
    (git_repo / ".testpilot").mkdir(exist_ok=True)
    (git_repo / ".testpilot" / "db.sqlite").write_bytes(b"")
    (git_repo / ".coverage").write_text("x", encoding="utf-8")
    (git_repo / ".coverage.gw0").write_text("y", encoding="utf-8")
    # Ensure they are NOT ignored via gitignore/exclude
    gi = (git_repo / ".gitignore").read_text(encoding="utf-8")
    assert ".testpilot" not in gi
    assert ".coverage" not in gi
    exclude = git_repo / ".git" / "info" / "exclude"
    if exclude.exists():
        assert ".testpilot" not in exclude.read_text(encoding="utf-8")
        assert ".coverage" not in exclude.read_text(encoding="utf-8")

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


def test_coveragerc_not_ignored_for_dirty(git_repo: Path) -> None:
    # N2: .coveragerc must NOT be treated like .coverage
    (git_repo / ".coveragerc").write_text("[run]\nbranch = False\n", encoding="utf-8")
    result = _run_pytest(
        git_repo,
        "tests/test_sample.py::test_pass",
        "--testpilot",
        f"--testpilot-db={git_repo / '.testpilot' / 'db.sqlite'}",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    conn = _db(git_repo)
    dirty = conn.execute("SELECT dirty FROM test_runs").fetchone()[0]
    assert dirty == 1


def test_ignored_for_dirty_removeprefix() -> None:
    # N2 unit: lstrip("./") would mangle ".testpilot/..." — removeprefix must not
    from pytest_testpilot.gitinfo import _ignored_for_dirty

    assert _ignored_for_dirty(".testpilot/db.sqlite")
    assert _ignored_for_dirty("./.testpilot/db.sqlite")
    assert _ignored_for_dirty(".coverage")
    assert _ignored_for_dirty(".coverage.123")
    assert not _ignored_for_dirty(".coveragerc")
    assert not _ignored_for_dirty("pkg/foo.py")


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
    result = _run_pytest(
        sub,
        "tests/test_f.py",
        "--testpilot",
        f"--testpilot-db={db}",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    conn = sqlite3.connect(db)
    tid = conn.execute("SELECT test_id FROM tests").fetchone()[0]
    assert tid == "sub/tests/test_f.py::test_f", tid
    paths = [r[0] for r in conn.execute("SELECT DISTINCT path FROM test_coverage")]
    assert any(p == "sub/pkg/__init__.py" or p.endswith("sub/pkg/__init__.py") for p in paths), paths


def _require_or_skip_noxdist(msg: str) -> None:
    # R4-3: CI sets TESTPILOT_REQUIRE_NO_XDIST=1 so a silent skip cannot greenwash.
    if os.environ.get("TESTPILOT_REQUIRE_NO_XDIST") == "1":
        pytest.fail(msg)
    pytest.skip(msg)


def test_b7_venv_without_xdist(tmp_path: Path, git_repo: Path) -> None:
    # B7 / NEW-5 / R4-3: plugin loads when pytest-xdist is absent.
    # Prefer a pre-built wheelhouse (TESTPILOT_WHEELHOUSE from CI `pip download`);
    # otherwise try a local download. Never hit PyPI during the venv install itself.
    import venv

    preset = os.environ.get("TESTPILOT_WHEELHOUSE")
    if preset:
        wheelhouse = Path(preset)
        if not wheelhouse.is_dir() or not list(wheelhouse.glob("*.whl")):
            _require_or_skip_noxdist(f"TESTPILOT_WHEELHOUSE empty: {wheelhouse}")
    else:
        wheelhouse = tmp_path / "wheels"
        wheelhouse.mkdir()
        # Transitive deps (exceptiongroup/tomli on 3.10) come along without --no-deps.
        downloaded = subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "download",
                "-d",
                str(wheelhouse),
                "pytest",
                "coverage",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if downloaded.returncode != 0 or not list(wheelhouse.glob("*.whl")):
            _require_or_skip_noxdist(
                f"cannot download wheels for no-xdist venv: {downloaded.stderr[-400:]}"
            )

    vdir = tmp_path / "noxdist"
    venv.EnvBuilder(with_pip=True, symlinks=True).create(vdir)
    py = vdir / ("Scripts" if sys.platform == "win32" else "bin") / "python"
    plugin_src = Path(__file__).resolve().parents[1] / "src"
    installed = subprocess.run(
        [
            str(py),
            "-m",
            "pip",
            "install",
            "-q",
            "--no-index",
            f"--find-links={wheelhouse}",
            "pytest",
            "coverage",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if installed.returncode != 0:
        _require_or_skip_noxdist(f"offline wheel install failed: {installed.stderr[-400:]}")
    check = subprocess.run(
        [
            str(py),
            "-c",
            "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('xdist') is None else 1)",
        ],
        check=False,
    )
    assert check.returncode == 0, "xdist must not be installed in this venv"
    env = {k: v for k, v in os.environ.items()}
    env["COVERAGE_CORE"] = "ctrace"
    env["PYTHONPATH"] = str(plugin_src) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    result = subprocess.run(
        [
            str(py),
            "-m",
            "pytest",
            "-p",
            "pytest_testpilot.plugin",
            "tests/test_sample.py::test_pass",
            "--testpilot",
            f"--testpilot-db={git_repo / '.testpilot' / 'db.sqlite'}",
        ],
        cwd=git_repo,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "unknown hook" not in (result.stdout + result.stderr).lower()
    conn = _db(git_repo)
    assert conn.execute("SELECT COUNT(*) FROM test_runs").fetchone()[0] == 1


def test_sysmon_env_still_collects_coverage(git_repo: Path) -> None:
    # N7: without pytest-cov, own ctrace Coverage still collects under sysmon env.
    result = _run_pytest(
        git_repo,
        "tests/test_sample.py::test_pass",
        "--testpilot",
        f"--testpilot-db={git_repo / '.testpilot' / 'db.sqlite'}",
        cov_core="sysmon",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    conn = _db(git_repo)
    n = conn.execute("SELECT COUNT(*) FROM test_coverage").fetchone()[0]
    assert n > 0, "sysmon env must not wipe coverage; own ctrace instance required"


def test_coverage_core_restored_in_session_and_subprocess(git_repo: Path) -> None:
    # N7: COVERAGE_CORE must be restored after plugin start (session + child procs).
    (git_repo / "tests" / "test_env_core.py").write_text(
        """
import os
import subprocess
import sys

def test_env_intact():
    assert os.environ.get("COVERAGE_CORE") == "sysmon"
    out = subprocess.check_output(
        [sys.executable, "-c", "import os; print(os.environ.get('COVERAGE_CORE', ''))"],
        text=True,
    )
    assert out.strip() == "sysmon"
""",
        encoding="utf-8",
    )
    result = _run_pytest(
        git_repo,
        "tests/test_env_core.py",
        "--testpilot",
        f"--testpilot-db={git_repo / '.testpilot' / 'db.sqlite'}",
        cov_core="sysmon",
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _parse_cov_total(output: str) -> str:
    for line in output.splitlines():
        if line.strip().startswith("TOTAL"):
            parts = line.split()
            return parts[-1]  # e.g. "100%"
    raise AssertionError(f"no TOTAL line in coverage report:\n{output}")


def test_sysmon_with_pytest_cov_does_not_break_user_report(git_repo: Path) -> None:
    # NEW-1: second tracer must not pause pytest-cov under COVERAGE_CORE=sysmon.
    without = _run_pytest(
        git_repo,
        "tests/test_sample.py::test_pass",
        "--cov=pkg",
        "--cov-report=term-missing",
        cov_core="sysmon",
    )
    assert without.returncode == 0, without.stdout + without.stderr
    total_without = _parse_cov_total(without.stdout + without.stderr)

    with_tp = _run_pytest(
        git_repo,
        "tests/test_sample.py::test_pass",
        "--cov=pkg",
        "--cov-report=term-missing",
        "--testpilot",
        f"--testpilot-db={git_repo / '.testpilot' / 'db.sqlite'}",
        cov_core="sysmon",
    )
    assert with_tp.returncode == 0, with_tp.stdout + with_tp.stderr
    combined = with_tp.stdout + with_tp.stderr
    total_with = _parse_cov_total(combined)
    assert total_with == total_without, (total_with, total_without, combined)
    assert "No data was collected" not in combined

    conn = _db(git_repo)
    n = conn.execute("SELECT COUNT(*) FROM test_coverage").fetchone()[0]
    assert n > 0 or "coverage collection skipped" in combined, combined


def test_xdist_with_pytest_cov_writes_coverage_rows(git_repo: Path) -> None:
    # NEW-2
    pytest.importorskip("xdist")
    result = _run_pytest(
        git_repo,
        "tests",
        "-n",
        "2",
        "--cov=pkg",
        "--cov-report=",
        "--testpilot",
        f"--testpilot-db={git_repo / '.testpilot' / 'db.sqlite'}",
    )
    assert result.returncode in (0, 1), result.stdout + result.stderr
    conn = _db(git_repo)
    assert conn.execute("SELECT COUNT(*) FROM test_runs").fetchone()[0] == 1
    n = conn.execute("SELECT COUNT(*) FROM test_coverage").fetchone()[0]
    assert n > 0, result.stdout + result.stderr


def test_no_cov_overrides_addopts_cov_still_collects(git_repo: Path) -> None:
    # R4-1: IDE debug often adds --no-cov on top of addopts=--cov.
    (git_repo / "pytest.ini").write_text(
        "[pytest]\npythonpath = .\naddopts = --cov=pkg\n",
        encoding="utf-8",
    )
    result = _run_pytest(
        git_repo,
        "tests/test_sample.py::test_pass",
        "--no-cov",
        "--testpilot",
        f"--testpilot-db={git_repo / '.testpilot' / 'db.sqlite'}",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    conn = _db(git_repo)
    n = conn.execute("SELECT COUNT(*) FROM test_coverage").fetchone()[0]
    assert n > 0, result.stdout + result.stderr


def test_sysmon_cov_skip_does_not_warn_under_filterwarnings_error(git_repo: Path) -> None:
    # R4-2: skipped collection must not call switch_context (CoverageWarning → error).
    if not hasattr(sys, "monitoring"):
        pytest.skip("COVERAGE_CORE=sysmon needs Python 3.12+ sys.monitoring")
    (git_repo / "pytest.ini").write_text(
        "[pytest]\npythonpath = .\nfilterwarnings =\n    error\n",
        encoding="utf-8",
    )
    result = _run_pytest(
        git_repo,
        "tests/test_sample.py::test_pass",
        "--cov=pkg",
        "--cov-report=",
        "--testpilot",
        "--testpilot-strict",
        f"--testpilot-db={git_repo / '.testpilot' / 'db.sqlite'}",
        cov_core="sysmon",
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert "coverage collection skipped" in combined, combined


def test_sysmon_xdist_cov_surfaces_skip_warning(git_repo: Path) -> None:
    # R4-4: workers skip under sysmon+cov; controller must print one warning.
    if not hasattr(sys, "monitoring"):
        pytest.skip("COVERAGE_CORE=sysmon needs Python 3.12+ sys.monitoring")
    pytest.importorskip("xdist")
    result = _run_pytest(
        git_repo,
        "tests/test_sample.py::test_pass",
        "-n",
        "2",
        "--cov=pkg",
        "--cov-report=",
        "--testpilot",
        f"--testpilot-db={git_repo / '.testpilot' / 'db.sqlite'}",
        cov_core="sysmon",
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert combined.count("coverage collection skipped") == 1, combined
    conn = _db(git_repo)
    assert conn.execute("SELECT COUNT(*) FROM test_runs").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM test_coverage").fetchone()[0] == 0


def test_stale_schema_v1_is_reported(git_repo: Path) -> None:
    # N1: opening a v1 DB must surface a clear error (and not write a run).
    import sqlite3 as sq

    db = git_repo / ".testpilot" / "db.sqlite"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sq.connect(db)
    conn.executescript(
        """
        CREATE TABLE schema_version (version INTEGER PRIMARY KEY);
        INSERT INTO schema_version(version) VALUES (1);
        CREATE TABLE test_runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          sha TEXT NOT NULL,
          source TEXT NOT NULL,
          started_at INTEGER NOT NULL,
          finished_at INTEGER,
          dirty INTEGER NOT NULL DEFAULT 0,
          diff_hash TEXT,
          input_hash TEXT,
          meta TEXT
        );
        """
    )
    conn.close()
    result = _run_pytest(
        git_repo,
        "tests/test_sample.py::test_pass",
        "--testpilot",
        f"--testpilot-db={db}",
        "--testpilot-strict",
    )
    combined = result.stdout + result.stderr
    assert combined.count("schema outdated") == 1, combined
    assert "delete .testpilot" in combined
    assert result.returncode != 0
    conn2 = sq.connect(db)
    ver = conn2.execute("SELECT version FROM schema_version").fetchone()[0]
    assert ver == 1
    conn2.close()


def test_stale_schema_non_strict_does_not_fail_user_tests(git_repo: Path) -> None:
    import sqlite3 as sq

    db = git_repo / ".testpilot" / "db.sqlite"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sq.connect(db)
    conn.executescript(
        "CREATE TABLE schema_version (version INTEGER PRIMARY KEY);"
        "INSERT INTO schema_version(version) VALUES (1);"
    )
    conn.close()
    result = _run_pytest(
        git_repo,
        "tests/test_sample.py::test_pass",
        "--testpilot",
        f"--testpilot-db={db}",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    combined = result.stdout + result.stderr
    assert "schema outdated" in combined
    assert combined.count("schema outdated") == 1


def test_nodeid_norm_cache_is_session_scoped() -> None:
    # NEW-3: norm_cache must live on the plugin (session), not be rebuilt per file.
    import inspect

    from pytest_testpilot.plugin import TestPilotPlugin

    src = inspect.getsource(TestPilotPlugin._collect_coverage_rows)
    assert "self._nodeid_norm_cache" in src
    assert "norm_cache: dict" not in src and "norm_cache = {}" not in src


def test_schema_too_new_message(git_repo: Path) -> None:
    import sqlite3 as sq

    db = git_repo / ".testpilot" / "db.sqlite"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sq.connect(db)
    conn.executescript(
        "CREATE TABLE schema_version (version INTEGER PRIMARY KEY);"
        "INSERT INTO schema_version(version) VALUES (99);"
    )
    conn.close()
    result = _run_pytest(
        git_repo,
        "tests/test_sample.py::test_pass",
        "--testpilot",
        f"--testpilot-db={db}",
    )
    combined = result.stdout + result.stderr
    assert "schema too new" in combined
    assert "outdated" not in combined
    assert result.returncode == 0
