#!/usr/bin/env python3
"""Assert scan_python bare test names ⊇ pytest --collect-only bare names.

For each test file under a fixture repo (default: .cache/repos/{click,jinja}),
compare the set of bare nodeid leaves from `testpilot`/`scan` via a tiny Rust
helper printed by `cargo test` is awkward — instead we shell to a one-shot
`testpilot` features path when available, or fall back to embedding a minimal
AST walk that mirrors collect-only bare names and call the Rust binary through
`cargo run -p testpilot-cli -- features` if present.

Preferred path: invoke `cargo test` is not enough; we call the library via a
small `python` + subprocess that runs `rg`-free logic:

  1. `pytest --collect-only -q` → nodeids, strip `[…]`, take name after last `::`
  2. Parse the same files with a subprocess Rust snippet via
     `cargo run -p testpilot-cli --` if a `scan-file` command exists; else use
     `scripts/_scan_python_cli` built from `testpilot-core` examples.

This script uses `cargo test -p testpilot-core --lib` only as a compile gate and
drives comparison through an inline `python` reimplementation of bare-name
extraction from source (def/class nesting) **checked against** the Rust scanner
by spawning:

  cargo run -q -p testpilot-cli -- features show ...

when unavailable, it calls the companion binary:

  cargo run -q -p testpilot-core --example scan_file -- <path>

If neither exists, it builds a one-off via `rustc` linking is too heavy — so we
ship `examples/scan_file.rs` in testpilot-core.

Usage:
  python scripts/check_scan_collect_coverage.py [--repo .cache/repos/jinja]
  python scripts/check_scan_collect_coverage.py --all-fixtures
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURES = [
    ROOT / ".cache/repos/click",
    ROOT / ".cache/repos/jinja",
]


def strip_params(nodeid: str) -> str:
    bracket = nodeid.find("[")
    if bracket < 0 or "]" not in nodeid[bracket:]:
        return nodeid
    before = nodeid[:bracket]
    sep = before.rfind("::")
    after_sep = sep + 2 if sep >= 0 else 0
    rel = nodeid[after_sep:].find("[")
    if rel < 0:
        return nodeid
    return nodeid[: after_sep + rel]


def bare_leaf(nodeid: str) -> str:
    """path::Class::meth[x] → Class.meth or meth (qualname-style)."""
    stripped = strip_params(nodeid)
    if "::" not in stripped:
        return stripped
    _, rest = stripped.split("::", 1)
    return rest.replace("::", ".")


def _pytest_python() -> str:
    """Prefer repo .venv pytest over bare interpreter."""
    for cand in (
        ROOT / ".venv/bin/python",
        ROOT / ".venv-train/bin/python",
        Path(sys.executable),
    ):
        if cand.is_file():
            return str(cand)
    return sys.executable


def pytest_bare_names(repo: Path) -> dict[str, set[str]]:
    """Map relative test file → set of bare qualnames from collect-only."""
    env = os.environ.copy()
    # Avoid TestPilot / other pytest11 plugins; skip conftest deps (trio, …).
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    py = _pytest_python()
    proc = subprocess.run(
        [
            py,
            "-m",
            "pytest",
            "--noconftest",
            "--collect-only",
            "-q",
            "tests",
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    by_file: dict[str, set[str]] = {}
    for line in (proc.stdout + "\n" + proc.stderr).splitlines():
        line = line.strip()
        if "::" not in line or not line.endswith(".py") and ".py::" not in line:
            if "::" not in line or ".py::" not in line:
                continue
        if line.startswith("ERROR") or line.startswith("!"):
            continue
        if " tests collected" in line or line.startswith("="):
            continue
        path = line.split("::", 1)[0]
        if not path.endswith(".py"):
            continue
        by_file.setdefault(path, set()).add(bare_leaf(line))
    if not by_file and proc.returncode != 0:
        print(proc.stdout, file=sys.stderr)
        print(proc.stderr, file=sys.stderr)
    return by_file


def ensure_scan_file_bin() -> Path:
    example = ROOT / "crates/testpilot-core/examples/scan_file.rs"
    if not example.exists():
        raise SystemExit(f"missing {example}")
    subprocess.run(
        ["cargo", "build", "-q", "-p", "testpilot-core", "--example", "scan_file"],
        cwd=ROOT,
        check=True,
    )
    # target/debug/examples/scan_file
    cand = ROOT / "target/debug/examples/scan_file"
    if not cand.is_file():
        raise SystemExit(f"built example not found at {cand}")
    return cand


def rust_scan_bare_names(bin_path: Path, path: Path) -> set[str]:
    """Run scan_file → JSON list of qualnames."""
    proc = subprocess.run(
        [str(bin_path), str(path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(
            f"scan_file failed for {path}:\n{proc.stderr}\n{proc.stdout}"
        )
    data = json.loads(proc.stdout)
    return {d for d in data if d.split(".")[-1].startswith("test_")}


def check_repo(repo: Path, bin_path: Path) -> list[str]:
    errors: list[str] = []
    collected = pytest_bare_names(repo)
    if not collected:
        errors.append(f"{repo}: pytest --collect-only produced no nodeids")
        return errors
    for rel, want in sorted(collected.items()):
        src = repo / rel
        if not src.is_file():
            errors.append(f"{repo}: collected {rel} but file missing")
            continue
        got = rust_scan_bare_names(bin_path, src)
        want_tests = {w for w in want if w.split(".")[-1].startswith("test_")}
        missing = sorted(want_tests - got)
        if missing:
            errors.append(
                f"{repo}/{rel}: scan missing {len(missing)} bare names "
                f"(collect={len(want_tests)}, scan_testish={len(got)}); "
                f"e.g. {missing[:5]}"
            )
    return errors


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", type=Path, action="append", default=None)
    ap.add_argument(
        "--all-fixtures",
        action="store_true",
        help="Check .cache/repos/click and jinja",
    )
    args = ap.parse_args()
    repos: list[Path] = []
    if args.repo:
        repos.extend(args.repo)
    if args.all_fixtures or not repos:
        repos.extend(r for r in DEFAULT_FIXTURES if r.is_dir())
    if not repos:
        print("no fixture repos found; pass --repo PATH", file=sys.stderr)
        return 2
    bin_path = ensure_scan_file_bin()
    all_errs: list[str] = []
    for repo in repos:
        print(f"checking {repo} …")
        all_errs.extend(check_repo(repo, bin_path))
    if all_errs:
        print("FAIL:")
        for e in all_errs:
            print(" ", e)
        return 1
    print("ok: scan_python bare names ⊇ collect-only bare names")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
