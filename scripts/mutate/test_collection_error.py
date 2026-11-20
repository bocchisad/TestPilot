"""collection_error detector: junit phantoms (pytest 8/9) + import-breaking mutant."""

from __future__ import annotations

import os
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

from mutate.run_mutant import (  # noqa: E402
    _collected_count,
    _no_junit_mutant_plugin_collection_error,
    junit_collection_error,
    run_mutant,
)
from mutate.testpilot_bin import resolve_testpilot_bin  # noqa: E402


def _testpilot() -> str:
    return resolve_testpilot_bin()


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


def test_collected_count_accepts_pytest_terminal_summary_after_long_output() -> None:
    output = "collected 160 items\n" + ("collection detail\n" * 700)
    output = output[-4000:] + "\n================ 160 tests collected in 0.02s ================\n"
    assert _collected_count(output) == 160


@pytest.mark.parametrize("xml", ["<not-junit/>", "<testsuites/>", "<broken"])
def test_parent_control_rejects_invalid_or_empty_junit_shape(tmp_path: Path, xml: str) -> None:
    from mutate.run_mutant import _valid_junit

    junit = tmp_path / "junit.xml"
    junit.write_text(xml, encoding="utf-8")
    assert _valid_junit(junit) is False


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
    bin_ = _testpilot()
    os.environ["TESTPILOT_BIN"] = bin_

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

    try:
        import pytest as _pytest  # noqa: F401
    except ImportError:
        pytest.skip("pytest not installed")

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
        python_bin=Path(sys.executable),
        work_dir=tmp_path / "work",
        timeout_override=60,
    )
    assert result.collection_error is True, result.log_tail[-2000:]
    assert result.killed is False
    assert result.n_new_fail == 0


@pytest.mark.skipif(
    os.environ.get("TP_SKIP_SLOW") == "1", reason="slow mutant integration"
)
def test_class_creation_collection_failure_without_junit_is_not_infrastructure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An import-time class decorator failure can stop pytest before JUnit exists."""
    bin_ = _testpilot()
    monkeypatch.setenv("TESTPILOT_BIN", bin_)
    repo, _parent = _init_tiny_repo(tmp_path)
    db = tmp_path / "tp.sqlite"
    (repo / "tests" / "conftest.py").write_text("from pkg import X\n", encoding="utf-8")
    _git(repo, "add", "tests/conftest.py")
    _git(repo, "commit", "-m", "import package from conftest")
    parent = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    (repo / "pkg" / "__init__.py").write_text(
        "from collections import abc\n"
        "class BrokenMeta(type):\n"
        "    def __new__(mcs, name, bases, namespace):\n"
        "        if not bases:\n"
        "            return None\n"
        "        return super().__new__(mcs, name, bases, namespace)\n"
        "@abc.Mapping.register\n"
        "class Broken(metaclass=BrokenMeta):\n"
        "    pass\n"
        "X = 1\n",
        encoding="utf-8",
    )
    _git(repo, "add", "pkg/__init__.py")
    _git(repo, "commit", "-m", "break class creation")
    mutant_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    _git(repo, "reset", "--hard", parent)
    result = run_mutant(
        repo,
        db,
        mutant_sha,
        parent_sha=parent,
        pytest_bin=Path(sys.executable).with_name("pytest"),
        python_bin=Path(sys.executable),
        work_dir=tmp_path / "work",
        timeout_override=60,
    )
    assert result.pytest_rc == 4
    assert result.junit_path is None
    assert result.collection_error is True, result.log_tail[-2000:]
    assert result.collect_ok is False
    assert result.killed is False
    assert "Can only register classes" in result.log_tail
    from mutate_bootstrap import _append_outcome, _load_first_outcomes
    outcomes = tmp_path / "mutate_pairs.jsonl"
    _append_outcome(outcomes, "C:0", mutant_sha, result)
    persisted = _load_first_outcomes(outcomes)["C:0"]
    assert "Can only register classes" in persisted.get("collect_note", "")


@pytest.mark.parametrize("exit_code", [3, 4])
def test_non_collection_pytest_exit_without_junit_stays_infrastructure(
    tmp_path: Path, exit_code: int
) -> None:
    _testpilot()
    repo, parent = _init_tiny_repo(tmp_path)
    (repo / "pkg" / "__init__.py").write_text("X = 2\n", encoding="utf-8")
    _git(repo, "add", "pkg/__init__.py")
    _git(repo, "commit", "-m", "change package")
    mutant_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    _git(repo, "reset", "--hard", parent)
    wrapper = tmp_path / "pytest_wrap"
    wrapper.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"ImportError while loading conftest\" \"TypeError: Can only register classes\"\n"
        f"exit {exit_code}\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    result = run_mutant(
        repo,
        tmp_path / "tp.sqlite",
        mutant_sha,
        parent_sha=parent,
        pytest_bin=wrapper,
        python_bin=Path(sys.executable),
        work_dir=tmp_path / "work",
        timeout_override=60,
    )
    assert result.collection_error is False
    assert result.ok is False


def test_pytest_rc4_conftest_path_outside_mutant_worktree_stays_infrastructure(
    tmp_path: Path,
) -> None:
    _testpilot()
    repo, parent = _init_tiny_repo(tmp_path)
    (repo / "pkg" / "__init__.py").write_text("X = 2\n", encoding="utf-8")
    _git(repo, "add", "pkg/__init__.py")
    _git(repo, "commit", "-m", "change package")
    mutant_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    _git(repo, "reset", "--hard", parent)
    outside = tmp_path / "outside" / "conftest.py"
    outside.parent.mkdir()
    outside.write_text("# outside worktree\n", encoding="utf-8")
    wrapper = tmp_path / "pytest_wrap"
    wrapper.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"ImportError while loading conftest '{outside}'\" \"TypeError: Can only register classes\"\n"
        "exit 4\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    result = run_mutant(
        repo,
        tmp_path / "tp.sqlite",
        mutant_sha,
        parent_sha=parent,
        pytest_bin=wrapper,
        python_bin=Path(sys.executable),
        work_dir=tmp_path / "work",
        timeout_override=60,
    )
    assert result.collection_error is False
    assert result.ok is False


def test_pytest_rc3_with_conftest_header_stays_infrastructure(tmp_path: Path) -> None:
    _testpilot()
    repo, parent = _init_tiny_repo(tmp_path)
    (repo / "tests" / "conftest.py").write_text("from pkg import X\n", encoding="utf-8")
    _git(repo, "add", "tests/conftest.py")
    _git(repo, "commit", "-m", "import package from conftest")
    parent = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    (repo / "pkg" / "__init__.py").write_text("X = 2\n", encoding="utf-8")
    _git(repo, "add", "pkg/__init__.py")
    _git(repo, "commit", "-m", "change package")
    mutant_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    _git(repo, "reset", "--hard", parent)
    work = tmp_path / "work"
    conftest = work / f"wt-{mutant_sha[:12]}" / "tests" / "conftest.py"
    wrapper = tmp_path / "pytest_wrap"
    wrapper.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"ImportError while loading conftest '{conftest}'\" \"internal failure\"\n"
        "exit 3\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    result = run_mutant(
        repo,
        tmp_path / "tp.sqlite",
        mutant_sha,
        parent_sha=parent,
        pytest_bin=wrapper,
        python_bin=Path(sys.executable),
        work_dir=work,
        timeout_override=60,
    )
    assert result.pytest_rc == 3
    assert result.collection_error is False
    assert result.ok is False


@pytest.mark.parametrize(
    ("exit_code", "compact_traceback"), [(1, False), (3, True)]
)
def test_pytest11_plugin_failure_at_mutated_line_is_collection_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exit_code: int,
    compact_traceback: bool,
) -> None:
    monkeypatch.setenv("TESTPILOT_BIN", _testpilot())
    repo, _initial = _init_tiny_repo(tmp_path)
    source = repo / "src" / "attr" / "_make.py"
    source.parent.mkdir(parents=True)
    source.write_text("def value():\n    return 1\n", encoding="utf-8")
    _git(repo, "add", "src/attr/_make.py")
    _git(repo, "commit", "-m", "baseline package")
    parent = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    source.write_text("def value():\n    return 2\n", encoding="utf-8")
    _git(repo, "add", "src/attr/_make.py")
    _git(repo, "commit", "-m", "mutant breaks plugin import")
    mutant = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    _git(repo, "reset", "--hard", parent)
    work = tmp_path / "work"
    mutant_file = work / f"wt-{mutant[:12]}" / "src" / "attr" / "_make.py"
    wrapper = tmp_path / "pytest_wrap"
    if compact_traceback:
        frame = "E   src/attr/_make.py:2: in value"
        exception = 'E   TypeError: can only concatenate str (not "bool") to str'
    else:
        frame = f'  File "{mutant_file}", line 2, in <module>'
        exception = 'TypeError: can only concatenate str (not "bool") to str'
    output = [
        'Error while loading plugin "pytest11".',
        *( ["Traceback (most recent call last):"] if not compact_traceback else [] ),
        frame,
        "    return 2",
        exception,
    ]
    wrapper.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' " + " ".join(f"'{line}'" for line in output) + "\n"
        f"exit {exit_code}\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)

    result = run_mutant(
        repo,
        tmp_path / "tp.sqlite",
        mutant,
        parent_sha=parent,
        pytest_bin=wrapper,
        python_bin=Path(sys.executable),
        work_dir=work,
        timeout_override=60,
    )
    assert result.pytest_rc == exit_code
    assert result.junit_path is None
    assert result.collection_error is True, result.log_tail[-2000:]
    assert result.collect_ok is False
    assert result.killed is False
    assert "TypeError: can only concatenate" in result.collect_note
    from mutate_bootstrap import (
        _append_outcome,
        _load_first_outcomes,
        _result_from_stored_outcome,
    )

    outcomes = tmp_path / "mutate_pairs.jsonl"
    _append_outcome(outcomes, "C:0", mutant, result)
    persisted = _load_first_outcomes(outcomes)["C:0"]
    assert persisted["status"] == "collection_error"
    assert persisted["pytest_rc"] == exit_code
    assert "TypeError: can only concatenate" in persisted["collect_note"]
    reused = _result_from_stored_outcome(persisted, mutant)
    assert reused is not None
    assert reused.pytest_rc == exit_code


def test_pytest11_plugin_failure_at_unchanged_line_stays_infrastructure(
    tmp_path: Path,
) -> None:
    repo, _initial = _init_tiny_repo(tmp_path)
    (repo / "pkg" / "__init__.py").write_text(
        "def value():\n    return 1\n", encoding="utf-8"
    )
    _git(repo, "add", "pkg/__init__.py")
    _git(repo, "commit", "-m", "baseline package")
    parent = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    (repo / "pkg" / "__init__.py").write_text(
        "def value():\n    return 2\n", encoding="utf-8"
    )
    _git(repo, "add", "pkg/__init__.py")
    _git(repo, "commit", "-m", "mutant changes another line")
    mutant = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    _git(repo, "reset", "--hard", parent)
    worktree = tmp_path / "work" / f"wt-{mutant[:12]}"
    (worktree / "pkg").mkdir(parents=True)
    (worktree / "pkg" / "__init__.py").write_text(
        "def value():\n    return 2\n", encoding="utf-8"
    )
    tail = (
        'Error while loading plugin "pytest11".\n'
        "Traceback (most recent call last):\n"
        f'  File "{worktree / "pkg" / "__init__.py"}", line 1, in <module>\n'
        "TypeError: can only concatenate str (not \"bool\") to str\n"
    )

    assert _no_junit_mutant_plugin_collection_error(
        1, tail, worktree, repo, parent, mutant
    ) is False


@pytest.mark.parametrize("parent_collects", [True, False])
def test_pytest11_plugin_failure_uses_same_worker_parent_control(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    parent_collects: bool,
) -> None:
    monkeypatch.setenv("TESTPILOT_BIN", _testpilot())
    repo, _initial = _init_tiny_repo(tmp_path)
    source = repo / "src" / "attr" / "_make.py"
    source.parent.mkdir(parents=True)
    source.write_text("def value():\n    return 1\n", encoding="utf-8")
    _git(repo, "add", "src/attr/_make.py")
    _git(repo, "commit", "-m", "baseline package")
    parent = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    source.write_text("def value():\n    return 2\n", encoding="utf-8")
    _git(repo, "add", "src/attr/_make.py")
    _git(repo, "commit", "-m", "mutant fails from generated code")
    mutant = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    _git(repo, "reset", "--hard", parent)

    wrapper = tmp_path / "pytest_wrap"
    parent_output = (
        "cat > \"$junit\" <<'XML'\n"
        "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n"
        "<testsuites><testsuite name=\"tests\" tests=\"1\">"
        "<testcase classname=\"tests.test_basic\" name=\"test_ok\"/>"
        "</testsuite></testsuites>\n"
        "XML\n"
        "echo '1 test collected'\n"
        "exit 0\n"
    )
    if not parent_collects:
        parent_output = (
            "printf '%s\\n' 'Error while loading plugin \"pytest11\".' "
            "'E   src/attr/_make.py:1: in value' "
            "'E   TypeError: can only concatenate str (not \"bool\") to str'\n"
            "exit 3\n"
        )
    wrapper.write_text(
        "#!/bin/sh\n"
        "junit=''\n"
        "for arg in \"$@\"; do case \"$arg\" in --junitxml=*) junit=${arg#--junitxml=};; esac; done\n"
        "case \"$PWD\" in *wt-parent-*)\n"
        + parent_output
        + "esac\n"
        "printf '%s\\n' 'Error while loading plugin \"pytest11\".' "
        "'E   src/attr/_make.py:1: in value' "
        "'E   TypeError: can only concatenate str (not \"bool\") to str'\n"
        "exit 3\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)

    result = run_mutant(
        repo,
        tmp_path / "tp.sqlite",
        mutant,
        parent_sha=parent,
        pytest_bin=wrapper,
        python_bin=Path(sys.executable),
        work_dir=tmp_path / "work",
        timeout_override=60,
    )
    assert result.pytest_rc == 3
    if parent_collects:
        assert result.collection_error is True, result.collect_note[-2000:]
        assert "parent control collection succeeded" in result.collect_note
        assert "n_tests=1" in result.collect_note
        assert result.killed is False
    else:
        assert result.collection_error is False
        assert "parent control collection failed" in result.collect_note
