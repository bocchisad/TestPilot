"""Run pytest on a mutant commit in a worktree; junit ingest with synthetic flag."""

from __future__ import annotations

import os
import re
import shutil
import signal
import sqlite3
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class MutantRunResult:
    ok: bool
    timed_out: bool = False
    junit_path: Path | None = None
    pytest_rc: int | None = None
    n_failed: int = 0
    n_tests: int = 0
    n_flaky_rerun: int = 0
    flaky_tests: set[str] = field(default_factory=set)
    n_new_fail: int = 0
    killed: bool = False  # ≥1 new_fail vs parent C
    high_fail_rate: bool = False  # >50% of tests failed
    collection_error: bool = False
    collect_ok: bool = False
    collect_note: str = ""
    log_tail: str = ""
    orphan_check_ok: bool = True


def default_timeout_sec(duration_sec: float | None, override: int | None) -> int:
    if override is not None:
        return override
    if duration_sec is None:
        return 60
    return max(60, int(3 * duration_sec))


def _run(
    args: list[str],
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    e = os.environ.copy()
    if env:
        e.update(env)
    return subprocess.run(
        args, cwd=cwd, env=e, text=True, capture_output=True, check=check
    )


def _case_id(case: ET.Element) -> str:
    classname = case.get("classname") or ""
    name = case.get("name") or ""
    if classname:
        return f"{classname}::{name}"
    return name


def _failed_from_junit(path: Path) -> list[str]:
    if not path.is_file():
        return []
    root = ET.parse(path).getroot()
    failed: list[str] = []
    for case in root.iter("testcase"):
        if case.find("failure") is not None or case.find("error") is not None:
            failed.append(_case_id(case))
    return failed


def _all_tests_from_junit(path: Path) -> list[str]:
    if not path.is_file():
        return []
    root = ET.parse(path).getroot()
    return [_case_id(c) for c in root.iter("testcase")]


def _merge_junit_with_flaky(
    junit1: Path, junit2: Path | None, flaky: set[str], out: Path
) -> None:
    """Write a single junit where flaky tests appear as fail then pass (collapse → flaky=1)."""
    if not junit1.is_file():
        return
    root = ET.parse(junit1).getroot()
    # Collect pass outcomes from rerun for flaky ids
    rerun_pass: set[str] = set(flaky)
    # Emit: for each case in junit1, if flaky append a second pass case with same id
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    if suite is None:
        # multi-suite: wrap by concatenating raw XML
        parts = [junit1.read_text(encoding="utf-8")]
        if junit2 and junit2.is_file():
            parts.append(junit2.read_text(encoding="utf-8"))
        out.write_text("\n".join(parts), encoding="utf-8")
        return

    extras: list[ET.Element] = []
    for case in list(suite.iter("testcase")):
        tid = _case_id(case)
        if tid in rerun_pass:
            # Keep failure in junit1; add a pass sibling so collapse_reruns sees both.
            clone = ET.Element("testcase", attrib=dict(case.attrib))
            extras.append(clone)
    for e in extras:
        suite.append(e)
    tree = ET.ElementTree(root)
    tree.write(out, encoding="utf-8", xml_declaration=True)


_COLLECTION_RE = re.compile(
    r"(ERROR collecting|ImportError|ModuleNotFoundError|collected 0 items|"
    r"collection failure)",
    re.I,
)

_COLLECTION_MSG_RE = re.compile(
    r"(collection failure|ERROR collecting|failed to collect|ImportError while importing)",
    re.I,
)


def junit_collection_error(path: Path) -> bool:
    """True if junit encodes a pytest collection failure (pytest 8/9 on macOS/Linux)."""
    if not path.is_file():
        return False
    root = ET.parse(path).getroot()
    for case in root.iter("testcase"):
        name = case.get("name") or ""
        classname = case.get("classname") or ""
        node = case.find("error")
        if node is None:
            node = case.find("failure")
        if node is None:
            continue
        msg = f"{node.get('message') or ''}\n{node.text or ''}"
        if _COLLECTION_MSG_RE.search(msg):
            return True
        # Module-level phantom case: empty name, or name == classname / module path.
        if not name.strip():
            return True
        if classname and (name == classname or name == classname.rsplit(".", 1)[-1]):
            # pytest often emits classname=tests.test_foo name=tests.test_foo
            if "test_" not in name.split(".")[-1] or name == classname:
                if "collection" in msg.lower() or name == classname:
                    return True
        # name is a dotted module (no function), classname empty or same module
        if "." in name and "::" not in name and not name.startswith("test_"):
            if classname == name or not classname:
                return True
    return False


def run_test_count(db: Path, sha: str, *, synthetic: bool) -> int:
    if not db.is_file():
        return 0
    syn = 1 if synthetic else 0
    con = sqlite3.connect(str(db))
    row = con.execute(
        """
        SELECT r.id FROM test_runs r
        WHERE r.sha=? AND r.dirty=0 AND IFNULL(r.synthetic,0)=?
          AND EXISTS (SELECT 1 FROM test_results tr WHERE tr.run_id=r.id)
        ORDER BY r.id DESC LIMIT 1
        """,
        (sha, syn),
    ).fetchone()
    if not row:
        con.close()
        return 0
    n = con.execute(
        "SELECT COUNT(*) FROM test_results WHERE run_id=?", (row[0],)
    ).fetchone()[0]
    con.close()
    return int(n)


def _pytest_warn_args(python: Path) -> list[str]:
    probe = _run(
        [
            str(python),
            "-c",
            "import pytest; print(' '.join(n for n in ("
            "'PytestRemovedIn10Warning','PytestRemovedIn9Warning',"
            "'PytestDeprecationWarning','PytestUnraisableExceptionWarning'"
            ") if hasattr(pytest,n)))",
        ],
        check=False,
    )
    out: list[str] = []
    for name in (probe.stdout or "").split():
        out += ["-W", f"ignore::pytest.{name}"]
    return out


def _run_pytest_group(
    cmd: list[str],
    cwd: Path,
    env: dict[str, str],
    timeout_sec: int,
) -> tuple[int, bool, str, int | None]:
    """Returns (rc, timed_out, log_tail, pgid)."""
    tail = ""
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env={**os.environ, **env},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    pgid = None
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        pgid = None
    try:
        out, _ = proc.communicate(timeout=timeout_sec)
        tail = (out or "")[-4000:]
        return proc.returncode if proc.returncode is not None else 1, False, tail, pgid
    except subprocess.TimeoutExpired:
        try:
            if pgid is not None:
                os.killpg(pgid, signal.SIGKILL)
            else:
                proc.kill()
        except ProcessLookupError:
            proc.kill()
        try:
            out, _ = proc.communicate(timeout=5)
            tail = (out or "")[-4000:]
        except subprocess.TimeoutExpired:
            proc.kill()
        return 124, True, tail, pgid


def _pgid_alive(pgid: int | None) -> bool:
    if pgid is None:
        return False
    try:
        os.killpg(pgid, 0)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def run_fail_set(
    db: Path,
    sha: str,
    *,
    synthetic: bool,
) -> set[str]:
    """Hard fails from latest clean run for sha (plugin/junit IDs via DB)."""
    if not db.is_file():
        return set()
    syn = 1 if synthetic else 0
    con = sqlite3.connect(str(db))
    row = con.execute(
        """
        SELECT r.id FROM test_runs r
        WHERE r.sha=? AND r.dirty=0 AND IFNULL(r.synthetic,0)=?
          AND EXISTS (SELECT 1 FROM test_results tr WHERE tr.run_id=r.id)
        ORDER BY r.id DESC LIMIT 1
        """,
        (sha, syn),
    ).fetchone()
    if not row:
        con.close()
        return set()
    fails = {
        str(t[0])
        for t in con.execute(
            """
            SELECT t.test_id FROM test_results r
            JOIN tests t ON t.id=r.test_pk
            WHERE r.run_id=? AND r.outcome IN ('fail','error') AND r.flaky=0
            """,
            (row[0],),
        )
    }
    con.close()
    return fails


def parent_fail_set(db: Path, parent_sha: str) -> set[str]:
    """Non-synthetic clean run failures on C (for new_fail)."""
    return run_fail_set(db, parent_sha, synthetic=False)


def new_fail_count(fails_m: set[str], fails_c: set[str]) -> int:
    """Kill signal: number of hard fails on M that were not hard fails on C."""
    return len(fails_m - fails_c)


def collect_junit_synthetic(
    repo: Path,
    db: Path,
    sha: str,
    junit_glob: str,
    *,
    force: bool = True,
    cwd: Path | None = None,
) -> tuple[bool, str]:
    testpilot = os.environ.get("TESTPILOT_BIN", "testpilot")
    cmd = [
        testpilot,
        "--db",
        str(db),
        "collect",
        "junit",
        junit_glob,
        "--sha",
        sha,
        "--synthetic",
    ]
    if force:
        cmd.append("--force")
    try:
        r = _run(cmd, cwd=cwd or repo)
    except FileNotFoundError as e:
        return False, f"testpilot binary not found ({testpilot}): {e}"
    if r.returncode == 0:
        return True, (r.stdout or "").strip()
    return False, ((r.stderr or r.stdout or "")[-2000:])


def run_mutant(
    repo: Path,
    db: Path,
    mutant_sha: str,
    *,
    parent_sha: str,
    pytest_bin: Path,
    python_bin: Path,
    work_dir: Path,
    duration_sec: float | None = None,
    timeout_override: int | None = None,
    pytest_extra_args: list[str] | None = None,
    pip_bin: Path | None = None,
    install_project_cb: object | None = None,
    mutant_index: int | None = None,
) -> MutantRunResult:
    """Checkout M in a worktree, pytest+junit (no coverage), optional flaky rerun."""
    from mutate.git_mutant import delete_mutant_ref

    timeout_sec = default_timeout_sec(duration_sec, timeout_override)
    work_dir.mkdir(parents=True, exist_ok=True)
    wt = work_dir / f"wt-{mutant_sha[:12]}"
    junit1 = work_dir / f"junit-{mutant_sha[:12]}-1.xml"
    junit2 = work_dir / f"junit-{mutant_sha[:12]}-2.xml"
    junit_merged = work_dir / f"junit-{mutant_sha[:12]}-merged.xml"

    def _drop_ref_on_timeout() -> None:
        if mutant_index is not None:
            delete_mutant_ref(repo, parent_sha, mutant_index)

    for p in (wt, junit1, junit2, junit_merged):
        if p.exists() and p.is_dir():
            _run(["git", "worktree", "remove", "--force", str(p)], cwd=repo, check=False)
        elif p.is_file():
            p.unlink(missing_ok=True)

    add = _run(
        ["git", "worktree", "add", "--detach", str(wt), mutant_sha],
        cwd=repo,
        check=False,
    )
    if add.returncode != 0:
        return MutantRunResult(
            ok=False,
            log_tail=(add.stderr or add.stdout or "")[-2000:],
        )

    env = {
        "LC_ALL": os.environ.get("LC_ALL", "en_US.UTF-8"),
        "LANG": os.environ.get("LANG", "en_US.UTF-8"),
    }
    extra = pytest_extra_args or []
    warn_args = _pytest_warn_args(python_bin)

    if pip_bin is not None and install_project_cb is not None:
        ok, log = install_project_cb(pip_bin, wt)  # type: ignore[misc]
        if not ok:
            return MutantRunResult(
                ok=False,
                log_tail=str(log)[-2000:],
            )

    try:
        cmd = [
            str(pytest_bin),
            f"--junitxml={junit1}",
            *warn_args,
            *extra,
        ]
        rc, timed_out, tail, pgid = _run_pytest_group(cmd, wt, env, timeout_sec)
        orphan_ok = not _pgid_alive(pgid)
        if timed_out:
            for p in (junit1, junit2, junit_merged):
                p.unlink(missing_ok=True)
            _drop_ref_on_timeout()
            return MutantRunResult(
                ok=False,
                timed_out=True,
                log_tail=tail,
                pytest_rc=rc,
                orphan_check_ok=orphan_ok,
            )

        collection_error = bool(_COLLECTION_RE.search(tail)) or (
            rc == 2 and not junit1.is_file()
        )
        if junit1.is_file():
            if not _all_tests_from_junit(junit1):
                collection_error = True
            if junit_collection_error(junit1):
                collection_error = True

        failed = _failed_from_junit(junit1)
        all_tests = _all_tests_from_junit(junit1)
        n_parent = run_test_count(db, parent_sha, synthetic=False)
        if n_parent > 0 and len(all_tests) * 2 < n_parent:
            collection_error = True

        n_flaky = 0
        flaky: set[str] = set()
        if failed and not collection_error:
            rerun = [
                str(pytest_bin),
                f"--junitxml={junit2}",
                *warn_args,
                *failed,
            ]
            rc2, timed_out2, tail2, pgid2 = _run_pytest_group(
                rerun, wt, env, timeout_sec
            )
            orphan_ok = orphan_ok and not _pgid_alive(pgid2)
            tail = tail2 or tail
            if timed_out2:
                for p in (junit1, junit2, junit_merged):
                    p.unlink(missing_ok=True)
                _drop_ref_on_timeout()
                return MutantRunResult(
                    ok=False,
                    timed_out=True,
                    log_tail=tail,
                    pytest_rc=rc2,
                    orphan_check_ok=orphan_ok,
                )
            if junit2.is_file():
                root = ET.parse(junit2).getroot()
                for case in root.iter("testcase"):
                    tid = _case_id(case)
                    if (
                        tid in failed
                        and case.find("failure") is None
                        and case.find("error") is None
                    ):
                        flaky.add(tid)
            n_flaky = len(flaky)

        # Merge so collapse_reruns sees fail+pass → flaky=1 in DB.
        if junit1.is_file():
            _merge_junit_with_flaky(
                junit1, junit2 if junit2.is_file() else None, flaky, junit_merged
            )
            collect_ok, collect_msg = collect_junit_synthetic(
                repo,
                db,
                mutant_sha,
                str(junit_merged),
                force=True,
                cwd=repo,
            )
        else:
            collect_ok, collect_msg = False, "no junit produced"

        # Kill = ≥1 new_fail vs C using DB IDs (same normalize path as Rust collect).
        # Collection-error mutants are never kills (phantom module-level cases).
        fails_c = parent_fail_set(db, parent_sha)
        fails_m = (
            run_fail_set(db, mutant_sha, synthetic=True) if collect_ok else set()
        )
        n_new_fail = 0 if collection_error else new_fail_count(fails_m, fails_c)

        n_failed = len(failed)
        n_tests = len(all_tests)
        high_fail = n_tests > 0 and (n_failed / n_tests) > 0.5
        if collection_error:
            high_fail = True

        return MutantRunResult(
            ok=rc in (0, 1) and not timed_out,
            timed_out=False,
            junit_path=junit_merged if junit_merged.is_file() else junit1,
            pytest_rc=rc,
            n_failed=n_failed,
            n_tests=n_tests,
            n_flaky_rerun=n_flaky,
            flaky_tests=flaky,
            n_new_fail=n_new_fail,
            killed=n_new_fail >= 1 and not collection_error,
            high_fail_rate=high_fail,
            collection_error=collection_error,
            collect_ok=collect_ok,
            collect_note=collect_msg,
            log_tail=tail,
            orphan_check_ok=orphan_ok,
        )
    finally:
        _run(["git", "worktree", "remove", "--force", str(wt)], cwd=repo, check=False)
        if wt.exists():
            shutil.rmtree(wt, ignore_errors=True)
