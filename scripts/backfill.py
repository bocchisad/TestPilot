#!/usr/bin/env python3
"""Backfill pytest --testpilot runs over the last N commits via git worktrees.

venv lives *outside* each worktree (avoids dirty trees). Recorded sha = worktree HEAD.
DB path must be the main checkout's .testpilot DB (passed explicitly).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def run(args: list[str], cwd: Path | None = None, env: dict | None = None, check: bool = True) -> subprocess.CompletedProcess:
    e = os.environ.copy()
    if env:
        e.update(env)
    return subprocess.run(args, cwd=cwd, env=e, check=check, text=True, capture_output=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", type=Path, required=True)
    ap.add_argument("--last", type=int, required=True)
    ap.add_argument(
        "--db",
        type=Path,
        required=True,
        help="main DB path (plugin in worktree would otherwise write its own .testpilot)",
    )
    ap.add_argument("--pytest-args", default="-q", help="extra pytest args")
    args = ap.parse_args()

    repo = args.repo.resolve()
    db = args.db.resolve()
    if not db.parent.exists():
        db.parent.mkdir(parents=True, exist_ok=True)

    shas = run(
        ["git", "rev-list", f"-n{args.last}", "HEAD"], cwd=repo
    ).stdout.strip().splitlines()
    shas = list(reversed(shas))  # old → new

    work_root = Path(tempfile.mkdtemp(prefix="tp-backfill-"))
    venv = work_root / "venv"
    print(f"work_root={work_root} venv={venv} db={db} commits={len(shas)}")

    try:
        run([sys.executable, "-m", "venv", str(venv)])
        pip = venv / "bin" / "pip"
        pytest = venv / "bin" / "pytest"
        run([str(pip), "install", "-U", "pip", "wheel"], check=False)

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
                # install project into shared venv
                inst = run(
                    [str(pip), "install", "-e", str(wt)],
                    check=False,
                )
                if inst.returncode != 0:
                    # try requirements / pyproject without -e
                    inst2 = run([str(pip), "install", str(wt)], check=False)
                    if inst2.returncode != 0:
                        print("install failed; skip")
                        skip += 1
                        continue
                run([str(pip), "install", "pytest", "coverage", "pytest-testpilot"], check=False)
                # Prefer editable install of in-tree plugin if present
                plugin = repo / "python" / "pytest-testpilot"
                if plugin.exists():
                    run([str(pip), "install", "-e", str(plugin)], check=False)

                env = {
                    "COVERAGE_CORE": "ctrace",
                }
                cmd = [
                    str(pytest),
                    "--testpilot",
                    f"--testpilot-db={db}",
                    *args.pytest_args.split(),
                ]
                r = run(cmd, cwd=wt, env=env, check=False)
                head = run(["git", "rev-parse", "HEAD"], cwd=wt).stdout.strip()
                print(f"HEAD={head} pytest_rc={r.returncode}")
                if r.returncode not in (0, 1):  # 1 = tests failed (ok for labels)
                    print(r.stdout[-2000:] if r.stdout else "")
                    print(r.stderr[-2000:] if r.stderr else "")
                    skip += 1
                else:
                    ok += 1
            finally:
                run(["git", "worktree", "remove", "--force", str(wt)], cwd=repo, check=False)
                if wt.exists():
                    shutil.rmtree(wt, ignore_errors=True)

        print(f"done ok={ok} skip={skip}")
        return 0 if ok else 1
    finally:
        shutil.rmtree(work_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
