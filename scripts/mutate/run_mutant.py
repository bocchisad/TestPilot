"""Run pytest on a mutant commit in a worktree; junit ingest with synthetic flag."""

from __future__ import annotations

import os
import shutil
import signal
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
    n_flaky_rerun: int = 0
    flaky_tests: set[str] = field(default_factory=set)
    killed: bool = False
    collect_ok: bool = False
    collect_note: str = ""
    log_tail: str = ""


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


def _failed_from_junit(path: Path) -> list[str]:
    if not path.is_file():
        return []
    root = ET.parse(path).getroot()
    failed: list[str] = []
    for case in root.iter("testcase"):
        if case.find("failure") is not None or case.find("error") is not None:
            classname = case.get("classname") or ""
            name = case.get("name") or ""
            if classname:
                failed.append(f"{classname}::{name}")
            elif name:
                failed.append(name)
    return failed


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
) -> tuple[int, bool, str]:
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
    try:
        out, _ = proc.communicate(timeout=timeout_sec)
        tail = (out or "")[-4000:]
        return proc.returncode if proc.returncode is not None else 1, False, tail
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            proc.kill()
        try:
            out, _ = proc.communicate(timeout=5)
            tail = (out or "")[-4000:]
        except subprocess.TimeoutExpired:
            proc.kill()
        return 124, True, tail


def collect_junit_synthetic(
    repo: Path,
    db: Path,
    sha: str,
    junit_glob: str,
    *,
    force: bool = False,
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
    r = _run(cmd, cwd=cwd or repo)
    if r.returncode == 0:
        return True, (r.stdout or "").strip()
    return False, ((r.stderr or r.stdout or "")[-2000:])


def run_mutant(
    repo: Path,
    db: Path,
    mutant_sha: str,
    *,
    pytest_bin: Path,
    python_bin: Path,
    work_dir: Path,
    duration_sec: float | None = None,
    timeout_override: int | None = None,
    pytest_extra_args: list[str] | None = None,
    pip_bin: Path | None = None,
    install_project_cb: object | None = None,
) -> MutantRunResult:
    """Checkout M in a worktree, pytest+junit (no coverage), optional flaky rerun."""
    timeout_sec = default_timeout_sec(duration_sec, timeout_override)
    work_dir.mkdir(parents=True, exist_ok=True)
    wt = work_dir / f"wt-{mutant_sha[:12]}"
    junit1 = work_dir / f"junit-{mutant_sha[:12]}-1.xml"
    junit2 = work_dir / f"junit-{mutant_sha[:12]}-2.xml"

    for p in (wt, junit1, junit2):
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
        rc, timed_out, tail = _run_pytest_group(cmd, wt, env, timeout_sec)
        if timed_out:
            return MutantRunResult(ok=False, timed_out=True, log_tail=tail, pytest_rc=rc)

        failed = _failed_from_junit(junit1)
        n_flaky = 0
        flaky: set[str] = set()
        if failed:
            rerun = [
                str(pytest_bin),
                f"--junitxml={junit2}",
                *warn_args,
                *failed,
            ]
            rc2, timed_out2, tail2 = _run_pytest_group(rerun, wt, env, timeout_sec)
            tail = tail2 or tail
            if timed_out2:
                return MutantRunResult(
                    ok=False,
                    timed_out=True,
                    junit_path=junit1,
                    log_tail=tail,
                    pytest_rc=rc2,
                )
            passed_rerun = set()
            if junit2.is_file():
                root = ET.parse(junit2).getroot()
                for case in root.iter("testcase"):
                    name = case.get("name") or ""
                    classname = case.get("classname") or ""
                    tid = f"{classname}::{name}" if classname else name
                    if (
                        tid in failed
                        and case.find("failure") is None
                        and case.find("error") is None
                    ):
                        passed_rerun.add(tid)
            flaky = passed_rerun
            n_flaky = len(flaky)

        glob_pattern = str(work_dir / f"junit-{mutant_sha[:12]}-*.xml")
        collect_ok, collect_msg = collect_junit_synthetic(
            repo,
            db,
            mutant_sha,
            glob_pattern,
            force=False,
            cwd=repo,
        )

        n_failed = len(_failed_from_junit(junit1))
        killed = n_failed > len(flaky) and n_failed > 0

        return MutantRunResult(
            ok=rc in (0, 1) and not timed_out,
            timed_out=False,
            junit_path=junit1,
            pytest_rc=rc,
            n_failed=n_failed,
            n_flaky_rerun=n_flaky,
            flaky_tests=flaky,
            killed=killed,
            collect_ok=collect_ok,
            collect_note=collect_msg,
            log_tail=tail,
        )
    finally:
        _run(["git", "worktree", "remove", "--force", str(wt)], cwd=repo, check=False)
        if wt.exists():
            shutil.rmtree(wt, ignore_errors=True)
