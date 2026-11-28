#!/usr/bin/env python3
"""Run isolated Week 7 synthetic-width measurements for click and attrs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PINS = {
    "click": {
        "url": "https://github.com/pallets/click.git",
        "sha": "934813e4d421071a1b3db3973c02fe2721359a6e",
    },
    "attrs": {
        "url": "https://github.com/python-attrs/attrs.git",
        "sha": "598494a618410490cfbe0c896b7a544f6d23e0d9",
    },
}
DEFAULT_N = 400
DEFAULT_K = 4
DEFAULT_JOBS = 2
DEFAULT_SEED = 42
DEFAULT_SUITE_ORDER = ("click", "attrs")


def validate_options(
    *, n: int, k: int, jobs: int, suites: tuple[str, ...]
) -> None:
    if n <= 0:
        raise ValueError("N must be positive")
    if k <= 0:
        raise ValueError("K must be positive")
    if not 1 <= jobs <= 4:
        raise ValueError("jobs must be between 1 and 4")
    if not suites or set(suites) - PINS.keys():
        raise ValueError(f"supported suites are: {', '.join(PINS)}")
    if len(set(suites)) != len(suites):
        raise ValueError("suite names must be unique")


def build_bootstrap_command(
    *,
    binary: str,
    repo: str,
    out: str,
    suite: str,
    n: int,
    k: int,
    jobs: int,
    seed: int,
    dry_run: bool,
) -> list[str]:
    command = [
        binary,
        "bootstrap",
        "--repo",
        repo,
        "--out",
        out,
        "--max-commits",
        str(n),
        "--mutants-per-commit",
        str(k),
        "--jobs",
        str(jobs),
        "--seed",
        str(seed),
        "--suite",
        suite,
        "--train-threads",
        "1",
    ]
    if dry_run:
        command.append("--dry-run")
    else:
        command.append("--no-resume")
    return command


def parse_total_seconds(stdout: str) -> float:
    match = re.search(r"^total_s=([0-9]+(?:\.[0-9]+)?)\s*$", stdout, re.MULTILINE)
    if not match:
        raise ValueError("missing total_s in dry-run output")
    return float(match.group(1))


def _script_signature(manifest: dict[str, Any]) -> tuple[tuple[str, str, bool], ...]:
    return tuple(
        sorted(
            (
                str(row["path"]),
                str(row["worktree_blob_sha1"]),
                bool(row["dirty"]),
            )
            for row in manifest.get("scripts", [])
        )
    )


def _validate_full_run_plan(
    planned: dict[str, Any],
    parameters: dict[str, Any],
    python_version: str,
    planned_provenance: dict[str, Any],
    current_provenance: dict[str, Any],
) -> None:
    for name in ("n", "k", "jobs", "seed", "suites"):
        if planned.get(name) != parameters.get(name):
            raise ValueError(f"full-run {name} does not match the saved dry run")
    if planned.get("measurement_python_version") != python_version:
        raise ValueError("full-run Python version does not match the saved dry run")
    if planned.get("binary_version") != current_provenance.get("bin_version"):
        raise ValueError("full-run binary version does not match the saved dry run")
    if planned_provenance.get("binary_sha256") != current_provenance.get("binary_sha256"):
        raise ValueError("full-run binary SHA256 does not match the saved dry run")
    if _script_signature(planned_provenance) != _script_signature(current_provenance):
        before = {row[0]: row[1:] for row in _script_signature(planned_provenance)}
        after = {row[0]: row[1:] for row in _script_signature(current_provenance)}
        changed = sorted(
            path for path in set(before) | set(after) if before.get(path) != after.get(path)
        )
        raise ValueError(f"full-run script provenance changed: {', '.join(changed)}")


def _capture_provenance(
    binary: Path, out_path: Path, env: dict[str, str]
) -> dict[str, Any]:
    command = [
        sys.executable,
        str(ROOT / "scripts/measurement_provenance.py"),
        "--root",
        str(ROOT),
        "--bin",
        str(binary),
        "--out",
        str(out_path),
    ]
    subprocess.run(command, cwd=ROOT, env=env, check=True)
    return json.loads(out_path.read_text(encoding="utf-8"))


def verify_measurement_python(python: Path) -> str:
    if not python.is_file() or not os.access(python, os.X_OK):
        raise ValueError(f"measurement Python is not executable: {python}")
    version = subprocess.check_output(
        [str(python), "-c", "import sys; print(sys.version.split()[0])"], text=True
    ).strip()
    if version.split(".")[:2] != ["3", "13"]:
        raise ValueError(f"measurement Python must be 3.13.x, got {version}")
    return version


def measurement_python_path(configured: str | Path, root: Path = ROOT) -> Path:
    """Make the path absolute without dereferencing a venv's Python symlink."""
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.absolute()


def build_measurement_env(
    base: dict[str, str], python: Path, root: Path = ROOT
) -> dict[str, str]:
    env = base.copy()
    env["PATH"] = str(python.parent) + os.pathsep + env.get("PATH", "")
    env["TESTPILOT_MEASURE_PYTHON"] = str(python)
    existing_pythonpath = env.get("PYTHONPATH", "")
    paths = [root / "python/pytest-testpilot/src", root / "python/testpilot-train/src", root / "scripts"]
    env["PYTHONPATH"] = os.pathsep.join(
        [*(str(path) for path in paths), existing_pythonpath]
    ).rstrip(os.pathsep)
    return env


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), *args], text=True, stderr=subprocess.STDOUT
    ).strip()


def verify_checkout(repo: Path, suite: str) -> None:
    if not repo.is_dir():
        raise ValueError(f"missing {suite} checkout: {repo}")
    actual = _git(repo, "rev-parse", "HEAD")
    expected = PINS[suite]["sha"]
    if actual != expected:
        raise ValueError(f"{suite} pin mismatch: expected {expected}, got {actual}")
    if _git(repo, "status", "--porcelain", "--untracked-files=normal"):
        raise ValueError(f"refusing dirty {suite} checkout: {repo}")
    if (repo / ".testpilot").exists():
        raise ValueError(f"refusing checkout with existing .testpilot state: {repo}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stream(command: list[str], env: dict[str, str], log_path: Path) -> tuple[int, str]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    captured: list[str] = []
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            log.write(line)
            log.flush()
            sys.stdout.write(line)
            sys.stdout.flush()
            captured.append(line)
        return process.wait(), "".join(captured)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> int:
    suites = tuple(args.suites)
    validate_options(n=args.n, k=args.k, jobs=args.jobs, suites=suites)
    binary = Path(args.binary).resolve()
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise ValueError(f"binary is not executable: {binary}")
    python = measurement_python_path(args.python)
    python_version = verify_measurement_python(python)
    run_dir = Path(args.run_dir)
    if not run_dir.is_absolute():
        run_dir = ROOT / run_dir
    run_dir = run_dir.resolve()
    if not run_dir.is_relative_to(ROOT / ".cache/measurements/week7/test-width"):
        raise ValueError("run-dir must be inside .cache/measurements/week7/test-width")
    repos_root = Path(args.repos_root)
    if not repos_root.is_absolute():
        repos_root = ROOT / repos_root
    repos_root = repos_root.resolve()
    if not repos_root.is_relative_to(ROOT / ".cache/measurements/week7/test-width"):
        raise ValueError("repos-root must be inside .cache/measurements/week7/test-width")

    for suite in suites:
        verify_checkout(repos_root / suite, suite)

    dry_run_path = run_dir / "dry-run.json"
    if args.mode == "full":
        if not dry_run_path.is_file():
            raise ValueError("full mode requires a successful dry-run.json in the same run-dir")
        planned = json.loads(dry_run_path.read_text(encoding="utf-8"))
        parameters = {
            "n": args.n,
            "k": args.k,
            "jobs": args.jobs,
            "seed": args.seed,
            "suites": list(suites),
        }
        for suite in suites:
            out = run_dir / "out" / suite
            if out.exists():
                raise ValueError(f"refusing to overwrite existing output: {out}")
    elif run_dir.exists():
        raise ValueError(f"refusing to overwrite existing run-dir: {run_dir}")

    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "logs").mkdir(exist_ok=True)
    env = build_measurement_env(os.environ, python)
    env["TESTPILOT_BIN"] = str(binary)

    if args.mode == "dry-run":
        provenance = run_dir / "measurement-provenance.json"
        dry_provenance = _capture_provenance(binary, provenance, env)
        entries = []
        total_s = 0.0
        for suite in suites:
            command = build_bootstrap_command(
                binary=str(binary),
                repo=str(repos_root / suite),
                out=str(run_dir / "out" / suite),
                suite=suite,
                n=args.n,
                k=args.k,
                jobs=args.jobs,
                seed=args.seed,
                dry_run=True,
            )
            code, stdout = _stream(command, env, run_dir / "logs" / f"{suite}-dry-run.txt")
            if code:
                raise subprocess.CalledProcessError(code, command)
            seconds = parse_total_seconds(stdout)
            total_s += seconds
            entries.append(
                {
                    "suite": suite,
                    "pin": PINS[suite]["sha"],
                    "estimate_seconds": seconds,
                    "command": command,
                    "log": f"logs/{suite}-dry-run.txt",
                }
            )
        report = {
            "mode": "dry-run",
            "created_at_utc": datetime.now(UTC).isoformat(),
            "branch_head": _git(ROOT, "rev-parse", "HEAD"),
            "binary": str(binary.relative_to(ROOT)),
            "binary_version": subprocess.check_output([str(binary), "--version"], text=True).strip(),
            "binary_sha256": dry_provenance["binary_sha256"],
            "measurement_python": str(python.relative_to(ROOT)),
            "measurement_python_version": python_version,
            "n": args.n,
            "k": args.k,
            "jobs": args.jobs,
            "seed": args.seed,
            "suites": list(suites),
            "suite_estimates": entries,
            "estimated_total_seconds": total_s,
            "measurement_provenance": "measurement-provenance.json",
            "note": "Dry runs only. They do not start mutation/test runs or write bootstrap outputs.",
        }
        _write_json(dry_run_path, report)
        print(f"estimated_total_seconds={total_s:.3f}")
        print(f"dry_run_report={dry_run_path.relative_to(ROOT)}")
        return 0

    planned_provenance_path = run_dir / planned.get(
        "measurement_provenance", "measurement-provenance.json"
    )
    if not planned_provenance_path.is_file():
        raise ValueError("saved dry-run provenance is missing")
    planned_provenance = json.loads(
        planned_provenance_path.read_text(encoding="utf-8")
    )
    full_provenance_path = run_dir / "full-run-provenance.json"
    full_provenance = _capture_provenance(binary, full_provenance_path, env)
    _validate_full_run_plan(
        planned,
        parameters,
        python_version,
        planned_provenance,
        full_provenance,
    )

    report_path = run_dir / "full-run.json"
    report: dict[str, Any] = {
        "mode": "full",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "branch_head": _git(ROOT, "rev-parse", "HEAD"),
        "measurement_python": str(python.relative_to(ROOT)),
        "measurement_python_version": python_version,
        "binary": str(binary.relative_to(ROOT)),
        "binary_version": full_provenance["bin_version"],
        "binary_sha256": full_provenance["binary_sha256"],
        "n": args.n,
        "k": args.k,
        "jobs": args.jobs,
        "seed": args.seed,
        "suites": list(suites),
        "dry_run_report": "dry-run.json",
        "measurement_provenance": "full-run-provenance.json",
        "runs": [],
        "status": "running",
    }
    _write_json(report_path, report)
    for suite in suites:
        out = run_dir / "out" / suite
        command = build_bootstrap_command(
            binary=str(binary),
            repo=str(repos_root / suite),
            out=str(out),
            suite=suite,
            n=args.n,
            k=args.k,
            jobs=args.jobs,
            seed=args.seed,
            dry_run=False,
        )
        suite_env = env.copy()
        if suite == "attrs":
            suite_env["PYTEST_ADDOPTS"] = "--ignore=tests/test_mypy.yml"
        started = time.monotonic()
        code, _stdout = _stream(command, suite_env, run_dir / "logs" / f"{suite}-full-run.txt")
        elapsed = round(time.monotonic() - started, 3)
        entry: dict[str, Any] = {
            "suite": suite,
            "pin": PINS[suite]["sha"],
            "exit_code": code,
            "wall_seconds": elapsed,
            "command": command,
            "log": f"logs/{suite}-full-run.txt",
            "out": f"out/{suite}",
        }
        report["runs"].append(entry)
        if code:
            report["status"] = "failed"
            report["failed_suite"] = suite
            _write_json(report_path, report)
            return code
        bootstrap_report = out / "bootstrap_report.json"
        if not bootstrap_report.is_file():
            report["status"] = "failed"
            report["failed_suite"] = suite
            report["error"] = f"missing {bootstrap_report.relative_to(ROOT)}"
            _write_json(report_path, report)
            return 1
        measured = json.loads(bootstrap_report.read_text(encoding="utf-8"))
        gate = measured.get("paired_delta_ci_synthetic_test_cov_lines") or {}
        n_c = gate.get("n_C")
        entry.update(
            {
                "bootstrap_report_sha256": _sha256(bootstrap_report),
                "n_backfill_reused": measured.get("n_backfill_reused"),
                "n_mutants_attempted": measured.get("n_mutants_attempted"),
                "n_C": n_c,
                "gate_verdict": (measured.get("bootstrap") or {}).get("verdict"),
                "gate_reason": (measured.get("bootstrap") or {}).get("reason"),
                "width_target_met": isinstance(n_c, int) and n_c >= 10,
            }
        )
        _write_json(report_path, report)
        print(f"{suite}: n_C={n_c}; width_target_met={entry['width_target_met']}")
    report["status"] = "complete"
    _write_json(report_path, report)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("dry-run", "full"), required=True)
    parser.add_argument("--n", type=int, default=DEFAULT_N)
    parser.add_argument("--k", type=int, default=DEFAULT_K)
    parser.add_argument("--jobs", type=int, default=DEFAULT_JOBS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--suites", nargs="+", choices=tuple(PINS), default=list(DEFAULT_SUITE_ORDER))
    parser.add_argument("--binary", default=str(ROOT / "target/release/testpilot"))
    parser.add_argument("--python", default=str(ROOT / ".cache/week7-train-env/bin/python"))
    parser.add_argument(
        "--repos-root", default=".cache/measurements/week7/test-width/repos"
    )
    parser.add_argument(
        "--run-dir", default=".cache/measurements/week7/test-width/run-n400"
    )
    args = parser.parse_args()
    try:
        return run(args)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
