"""collection_error detector: junit phantoms (pytest 8/9) + import-breaking mutant."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1]
_ROOT = Path(__file__).resolve().parents[2]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from mutate.run_mutant import junit_collection_error, run_mutant  # noqa: E402


def _write_junit(path: Path, cases: list[dict]) -> None:
    suite = ET.Element("testsuite", attrib={"tests": str(len(cases))})
    for c in cases:
        tc = ET.SubElement(
            suite,
            "testcase",
            attrib={
                "classname": c.get("classname", ""),
                "name": c.get("name", ""),
                "time": "0.01",
            },
        )
        if c.get("error"):
            err = ET.SubElement(
                tc,
                "error",
                attrib={"message": c["error"], "type": c.get("etype", "Error")},
            )
            err.text = c.get("body", c["error"])
    path.write_text(ET.tostring(suite, encoding="unicode"), encoding="utf-8")


def test_junit_module_phantom_pytest8_style(tmp_path: Path) -> None:
    """pytest 8 macOS: classname=name=module, message=collection failure."""
    junit = tmp_path / "out.xml"
    _write_junit(
        junit,
        [
            {
                "classname": "tests.test_basic",
                "name": "tests.test_basic",
                "error": "collection failure",
                "etype": "Collector",
            }
        ],
    )
    assert junit_collection_error(junit) is True


def test_junit_empty_name_phantom(tmp_path: Path) -> None:
    junit = tmp_path / "out.xml"
    _write_junit(
        junit,
        [
            {
                "classname": "tests.test_info_dict",
                "name": "",
                "error": "ImportError while importing test module",
            }
        ],
    )
    assert junit_collection_error(junit) is True


def test_junit_module_path_no_function(tmp_path: Path) -> None:
    junit = tmp_path / "out.xml"
    _write_junit(
        junit,
        [
            {
                "classname": "tests.test_api",
                "name": "tests.test_api",
                "error": "failed to collect",
            }
        ],
    )
    assert junit_collection_error(junit) is True


def test_junit_normal_assertion_failure_not_collection(tmp_path: Path) -> None:
    junit = tmp_path / "out.xml"
    suite = ET.Element("testsuite", attrib={"tests": "1"})
    tc = ET.SubElement(
        suite,
        "testcase",
        attrib={"classname": "tests.test_basic", "name": "test_foo", "time": "0.01"},
    )
    fail = ET.SubElement(
        tc, "failure", attrib={"message": "assert 1 == 2", "type": "AssertionError"}
    )
    fail.text = "assert 1 == 2"
    junit.write_text(ET.tostring(suite, encoding="unicode"), encoding="utf-8")
    assert junit_collection_error(junit) is False


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _init_tiny_repo(root: Path) -> tuple[Path, str]:
    """Minimal package: tests import lib at collection time."""
    repo = root / "proj"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "pkg").mkdir()
    (repo / "pkg" / "__init__.py").write_text("X = 1\n", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_basic.py").write_text(
        textwrap.dedent(
            """\
            from pkg import X

            def test_ok():
                assert X == 1
            """
        ),
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")
    sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    return repo, sha


@pytest.mark.skipif(
    os.environ.get("TP_SKIP_SLOW") == "1", reason="slow mutant integration"
)
def test_import_break_mutant_is_collection_error(tmp_path: Path) -> None:
    """Mutant that breaks module import → collection_error=1, kill=0."""
    repo, parent = _init_tiny_repo(tmp_path)
    db = tmp_path / "tp.sqlite"
    (repo / "pkg" / "__init__.py").write_text(
        "raise ImportError('boom')\n", encoding="utf-8"
    )
    _git(repo, "add", "pkg/__init__.py")
    _git(repo, "commit", "-m", "break import")
    mutant_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    _git(repo, "reset", "--hard", parent)

    pytest_bin = Path(shutil.which("pytest") or sys.executable)
    # Prefer `python -m pytest` via sys.executable wrapper if needed
    if pytest_bin.name != "pytest" and "pytest" not in pytest_bin.name:
        # Fall back: use python -m pytest by pointing pytest_bin at a shim... 
        # run_mutant expects a pytest executable; use `python -m pytest` via env.
        pass
    python_bin = Path(sys.executable)
    # Ensure pytest module is available
    try:
        import pytest as _pytest  # noqa: F401
    except ImportError:
        pytest.skip("pytest not installed")

    # Create a small wrapper script that invokes python -m pytest
    wrapper = tmp_path / "pytest_wrap"
    wrapper.write_text(
        f"#!/bin/sh\nexec {sys.executable} -m pytest \"$@\"\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)

    result = run_mutant(
        repo,
        db,
        mutant_sha,
        parent_sha=parent,
        pytest_bin=wrapper,
        python_bin=python_bin,
        work_dir=tmp_path / "work",
        timeout_override=60,
    )
    assert result.collection_error is True, result.log_tail[-2000:]
    assert result.killed is False
    assert result.n_new_fail == 0
