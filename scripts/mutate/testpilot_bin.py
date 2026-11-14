"""Resolve the testpilot CLI binary for mutate tests."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def resolve_testpilot_bin() -> str:
    """Return path to testpilot. Prefer TESTPILOT_BIN, then CARGO_TARGET_DIR, then target/."""
    env = os.environ.get("TESTPILOT_BIN")
    if env:
        p = Path(env)
        if p.is_file():
            return str(p.resolve())
        raise FileNotFoundError(f"TESTPILOT_BIN={env} is not a file")

    candidates: list[Path] = []
    cargo_target = os.environ.get("CARGO_TARGET_DIR")
    if cargo_target:
        base = Path(cargo_target)
        candidates.extend(
            [
                base / "debug" / "testpilot",
                base / "release" / "testpilot",
            ]
        )
    candidates.extend(
        [
            ROOT / "target" / "debug" / "testpilot",
            ROOT / "target" / "release" / "testpilot",
        ]
    )
    for cand in candidates:
        if cand.is_file():
            return str(cand.resolve())
    raise FileNotFoundError(
        "testpilot binary not found; set TESTPILOT_BIN or CARGO_TARGET_DIR, "
        "or build with: cargo build -p testpilot-cli"
    )
