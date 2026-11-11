#!/usr/bin/env python3
"""Backfill pytest --testpilot runs over the last N commits via git worktrees.

venv lives *outside* each worktree (avoids dirty trees). Recorded sha = worktree HEAD.
DB path must be the main checkout's .testpilot DB (passed explicitly).
Plugin is installed from a local path (never PyPI `pytest-testpilot`).

Per-commit project install tries editable install plus test extras / PEP 735
dependency-groups / requirements files so the suite matches the project's own
CI environment (not a bare `pip install .`).
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

DEFAULT_PLUGIN = Path(__file__).resolve().parents[1] / "python" / "pytest-testpilot"

# Test-only extras / groups. Do NOT install "dev" by default — on projects like
# pallets/click that pulls tox/uv/ruff and dominates per-commit wall time.
_EXTRA_CANDIDATES = ("test", "tests", "testing")
_REQ_FILE_CANDIDATES = (
    "requirements-test.txt",
    "requirements-tests.txt",
    "requirements/test.txt",
    "requirements/tests.txt",
    "tests/requirements.txt",
    "test-requirements.txt",
)
_GROUP_CANDIDATES = ("tests", "test")


def run(
    args: list[str],
    cwd: Path | None = None,
    env: dict | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess:
    e = os.environ.copy()
    if env:
        e.update(env)
    return subprocess.run(args, cwd=cwd, env=e, check=check, text=True, capture_output=True)


def resolve_plugin(explicit: Path | None) -> Path:
    plugin = (explicit or DEFAULT_PLUGIN).resolve()
    if not (plugin / "pyproject.toml").exists() and not (plugin / "setup.py").exists():
        raise SystemExit(
            f"plugin path missing packaging metadata: {plugin}\n"
            "pass --plugin PATH to a TestPilot python/pytest-testpilot checkout"
        )
    return plugin


def _pyproject(wt: Path) -> dict:
    path = wt / "pyproject.toml"
    if not path.is_file():
        return {}
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _requires_python(wt: Path) -> str | None:
    doc = _pyproject(wt)
    return (doc.get("project") or {}).get("requires-python")


def _declared_extras(wt: Path) -> set[str]:
    doc = _pyproject(wt)
    opt = (doc.get("project") or {}).get("optional-dependencies") or {}
    return set(opt)


def _declared_groups(wt: Path) -> set[str]:
    doc = _pyproject(wt)
    groups = doc.get("dependency-groups") or {}
    return set(groups)


def install_project(pip: Path, wt: Path) -> tuple[bool, str]:
    """Install project + test dependencies into the shared venv.

    Returns (ok, log_tail).
    """
    logs: list[str] = []

    def pip_run(cmd: list[str]) -> subprocess.CompletedProcess:
        r = run([str(pip), *cmd], cwd=wt, check=False)
        logs.append(
            f"$ pip {' '.join(cmd)}\nrc={r.returncode}\n"
            f"{(r.stdout or '')[-800:]}\n{(r.stderr or '')[-800:]}"
        )
        return r

    # Base package (editable preferred).
    base = pip_run(["install", "-e", "."])
    if base.returncode != 0:
        base = pip_run(["install", "."])
        if base.returncode != 0:
            return False, "\n".join(logs)[-4000:]

    extras = _declared_extras(wt)
    for name in _EXTRA_CANDIDATES:
        if name in extras:
            pip_run(["install", "-e", f".[{name}]"])

    groups = _declared_groups(wt)
    for name in _GROUP_CANDIDATES:
        if name in groups:
            # pip >= 25.1: PEP 735 dependency groups
            r = pip_run(["install", "--group", name])
            if r.returncode != 0:
                # Fallback: parse group list as requirement specs when possible
                specs = groups_get_specs(wt, name)
                if specs:
                    pip_run(["install", *specs])

    for rel in _REQ_FILE_CANDIDATES:
        req = wt / rel
        if req.is_file():
            pip_run(["install", "-r", str(req)])

    return True, "\n".join(logs)[-2000:]


def groups_get_specs(wt: Path, group: str) -> list[str]:
    """Best-effort extract of string requirement specs from a dependency-group."""
    doc = _pyproject(wt)
    raw = (doc.get("dependency-groups") or {}).get(group) or []
    out: list[str] = []
    for item in raw:
        if isinstance(item, str):
            out.append(item)
    return out


def warn_always_failing(db: Path, threshold: float = 0.9) -> list[dict]:
    """Print tests that fail/error in ≥threshold of runs; return rows for callers."""
    if not db.is_file():
        return []
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        SELECT t.test_id AS test_id,
               COUNT(*) AS n_appear,
               SUM(CASE WHEN r.outcome IN ('fail','error') THEN 1 ELSE 0 END) AS n_fail,
               1.0 * SUM(CASE WHEN r.outcome IN ('fail','error') THEN 1 ELSE 0 END)
                   / COUNT(*) AS fail_rate
        FROM test_results r
        JOIN tests t ON t.id = r.test_pk
        JOIN test_runs u ON u.id = r.run_id
        WHERE u.source = 'pytest' AND IFNULL(u.synthetic, 0) = 0
        GROUP BY t.test_id
        HAVING fail_rate >= ?
        ORDER BY fail_rate DESC, test_id
        """,
        (threshold,),
    ).fetchall()
    if not rows:
        con.close()
        return []

    print(
        f"WARNING: {len(rows)} test(s) fail in ≥{threshold:.0%} of pytest runs "
        "(likely backfill environment: deps / Python / plugins / locale):"
    )
    out: list[dict] = []
    for row in rows:
        msg_row = con.execute(
            """
            SELECT r.message
            FROM test_results r
            JOIN tests t ON t.id = r.test_pk
            WHERE t.test_id = ? AND r.outcome IN ('fail','error')
              AND r.message IS NOT NULL AND length(r.message) > 0
            ORDER BY r.run_id
            LIMIT 1
            """,
            (row["test_id"],),
        ).fetchone()
        first_line = ""
        if msg_row and msg_row["message"]:
            first_line = msg_row["message"].strip().splitlines()[0][:240]
        print(
            f"  - {row['test_id']}  "
            f"{row['n_fail']}/{row['n_appear']} ({row['fail_rate']:.0%})"
            + (f"  | {first_line}" if first_line else "")
        )
        out.append(
            {
                "test_id": row["test_id"],
                "n_fail": int(row["n_fail"]),
                "n_appear": int(row["n_appear"]),
                "fail_rate": float(row["fail_rate"]),
                "first_error_line": first_line,
            }
        )
    con.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Backfill TestPilot runs; installs plugin from --plugin (local path)."
    )
    ap.add_argument("--repo", type=Path, required=True)
    ap.add_argument("--last", type=int, required=True)
    ap.add_argument(
        "--db",
        type=Path,
        required=True,
        help="main DB path (plugin in worktree would otherwise write its own .testpilot)",
    )
    ap.add_argument(
        "--plugin",
        type=Path,
        default=None,
        help=f"path to pytest-testpilot package (default: {DEFAULT_PLUGIN})",
    )
    ap.add_argument("--pytest-args", default="-q", help="extra pytest args")
    ap.add_argument(
        "--python",
        type=Path,
        default=None,
        help="Python interpreter for the backfill venv (default: current sys.executable). "
        "Use a version satisfying the project's requires-python.",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="resolve paths / print plan without creating worktrees or pip-installing",
    )
    ap.add_argument(
        "--always-fail-threshold",
        type=float,
        default=0.9,
        help="warn if a test fails in this fraction of runs (default 0.9)",
    )
    args = ap.parse_args()

    repo = args.repo.resolve()
    db = args.db.resolve()
    plugin = resolve_plugin(args.plugin)
    py = Path(args.python).resolve() if args.python else Path(sys.executable)
    print(f"plugin={plugin}")
    print(f"repo={repo} db={db} last={args.last}")
    print(f"python={py}")

    if args.dry_run:
        assert "pytest-testpilot" not in str(repo) or plugin != repo, plugin
        req = _requires_python(repo)
        print(f"requires-python={req!r}")
        print("dry-run ok (no pip, no worktrees)")
        return 0

    if not db.parent.exists():
        db.parent.mkdir(parents=True, exist_ok=True)

    shas = (
        run(["git", "rev-list", f"-n{args.last}", "HEAD"], cwd=repo)
        .stdout.strip()
        .splitlines()
    )
    shas = list(reversed(shas))

    work_root = Path(tempfile.mkdtemp(prefix="tp-backfill-"))
    venv = work_root / "venv"
    print(f"work_root={work_root} venv={venv} commits={len(shas)}")

    try:
        run([str(py), "-m", "venv", str(venv)])
        pip = venv / "bin" / "pip"
        pytest = venv / "bin" / "pytest"
        run([str(pip), "install", "-U", "pip", "wheel"], check=False)
        r_plugin = run([str(pip), "install", "-e", str(plugin)], check=False)
        if r_plugin.returncode != 0:
            print("plugin install failed:", r_plugin.stderr[-2000:])
            return 1
        # Modern pytest (plugin needs hashable plugins / current APIs). Historical
        # trees that raise PytestRemovedIn10Warning are handled via -W ignore below.
        run([str(pip), "install", "pytest>=8,<10", "coverage"], check=False)

        ok = skip = 0
        for sha in shas:
            wt = work_root / f"wt-{sha[:12]}"
            print(f"== {sha[:12]} ==")
            try:
                run(["git", "worktree", "add", "--detach", str(wt), sha], cwd=repo)
            except subprocess.CalledProcessError as e:
                print("worktree add failed:", e.stderr)
                skip += 1
                continue
            try:
                req = _requires_python(wt)
                if req:
                    print(f"requires-python={req}")
                installed, log_tail = install_project(pip, wt)
                if not installed:
                    print("install failed; skip")
                    print(log_tail[-1500:])
                    skip += 1
                    continue

                env = {"COVERAGE_CORE": "ctrace"}
                # Prefer UTF-8 locale so tests that assert on encoding don't flake.
                env.setdefault("LC_ALL", os.environ.get("LC_ALL", "en_US.UTF-8"))
                env.setdefault("LANG", os.environ.get("LANG", "en_US.UTF-8"))
                junit = work_root / f"junit-{sha[:12]}.xml"
                # Warning class names differ across pytest 8/9; resolve against the
                # *venv* pytest so -W does not abort when the attribute is missing.
                warn_args: list[str] = []
                probe = run(
                    [
                        str(venv / "bin" / "python"),
                        "-c",
                        "import pytest; print(' '.join(n for n in ("
                        "'PytestRemovedIn10Warning','PytestRemovedIn9Warning',"
                        "'PytestDeprecationWarning','PytestUnraisableExceptionWarning'"
                        ") if hasattr(pytest,n)))",
                    ],
                    check=False,
                )
                for name in (probe.stdout or "").split():
                    warn_args += ["-W", f"ignore::pytest.{name}"]
                cmd = [
                    str(pytest),
                    "--testpilot",
                    f"--testpilot-db={db}",
                    f"--junitxml={junit}",
                    *warn_args,
                    *args.pytest_args.split(),
                ]
                r = run(cmd, cwd=wt, env=env, check=False)
                head = run(["git", "rev-parse", "HEAD"], cwd=wt).stdout.strip()
                print(f"HEAD={head} pytest_rc={r.returncode}")
                if r.returncode not in (0, 1):
                    print(r.stdout[-2000:] if r.stdout else "")
                    print(r.stderr[-2000:] if r.stderr else "")
                    skip += 1
                else:
                    ok += 1
            finally:
                run(
                    ["git", "worktree", "remove", "--force", str(wt)],
                    cwd=repo,
                    check=False,
                )
                if wt.exists():
                    shutil.rmtree(wt, ignore_errors=True)

        print(f"done ok={ok} skip={skip}")
        warn_always_failing(db, threshold=args.always_fail_threshold)
        return 0 if ok else 1
    finally:
        shutil.rmtree(work_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
