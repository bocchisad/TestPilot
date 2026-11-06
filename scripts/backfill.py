#!/usr/bin/env python3
"""Backfill pytest --testpilot runs over the last N commits via git worktrees.

venv lives *outside* each worktree (avoids dirty trees). Recorded sha = worktree HEAD.
DB path must be the main checkout's .testpilot DB (passed explicitly).
Plugin is installed from a local path (never PyPI `pytest-testpilot`).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_PLUGIN = Path(__file__).resolve().parents[1] / "python" / "pytest-testpilot"


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
        "--dry-run",
        action="store_true",
        help="resolve paths / print plan without creating worktrees or pip-installing",
    )
    args = ap.parse_args()

    repo = args.repo.resolve()
    db = args.db.resolve()
    plugin = resolve_plugin(args.plugin)
    print(f"plugin={plugin}")
    print(f"repo={repo} db={db} last={args.last}")

    if args.dry_run:
        # Must not require PyPI or a plugin inside --repo.
        assert "pytest-testpilot" not in str(repo) or plugin != repo, plugin
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
        run([sys.executable, "-m", "venv", str(venv)])
        pip = venv / "bin" / "pip"
        pytest = venv / "bin" / "pytest"
        run([str(pip), "install", "-U", "pip", "wheel"], check=False)
        # Local plugin only — never `pip install pytest-testpilot` from PyPI.
        r_plugin = run([str(pip), "install", "-e", str(plugin)], check=False)
        if r_plugin.returncode != 0:
            print("plugin install failed:", r_plugin.stderr[-2000:])
            return 1
        run([str(pip), "install", "pytest", "coverage"], check=False)

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
                inst = run([str(pip), "install", "-e", str(wt)], check=False)
                if inst.returncode != 0:
                    inst2 = run([str(pip), "install", str(wt)], check=False)
                    if inst2.returncode != 0:
                        print("install failed; skip")
                        skip += 1
                        continue

                env = {"COVERAGE_CORE": "ctrace"}
                cmd = [
                    str(pytest),
                    "--testpilot",
                    f"--testpilot-db={db}",
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
        return 0 if ok else 1
    finally:
        shutil.rmtree(work_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
