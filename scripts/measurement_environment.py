#!/usr/bin/env python3
"""Publish dependency versions without machine-local direct URLs."""
from __future__ import annotations

import argparse
import importlib.metadata as metadata
import json
from pathlib import Path
import re
import subprocess
import sys


def normalize_freeze(raw: str, versions: dict[str, str]) -> str:
    result = []
    for line in raw.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        match = re.fullmatch(r"([\w.-]+)\s+@\s+file:.*", line)
        if match:
            name = match[1]
            version = versions.get(re.sub(r"[-_.]+", "-", name).lower())
            if version is None:
                raise ValueError(f"missing version for local dependency {name}")
            line = f"{name}=={version}"
        if re.search(r"(?:file:|/(?:Users|home|tmp|private|opt)/|^-e /)", line):
            raise ValueError("machine-local path in dependency freeze")
        result.append(line)
    return "\n".join(result) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--freeze-out", type=Path, required=True)
    parser.add_argument("--versions-out", type=Path, required=True)
    args = parser.parse_args()
    versions = {re.sub(r"[-_.]+", "-", d.metadata["Name"]).lower(): d.version
                for d in metadata.distributions() if d.metadata.get("Name")}
    raw = subprocess.check_output([sys.executable, "-m", "pip", "freeze"], text=True)
    freeze = normalize_freeze(raw, versions)
    fingerprint = {"python": sys.version.split()[0], "pytest": metadata.version("pytest"),
                   "lightgbm": metadata.version("lightgbm"),
                   "local_source_packages": {
                       "pytest-testpilot": "python/pytest-testpilot",
                       "testpilot-train": "python/testpilot-train"},
                   "freeze_note": "Local file URLs normalized to name==installed-version; source paths are repository-relative."}
    args.freeze_out.write_text(freeze, encoding="utf-8")
    args.versions_out.write_text(json.dumps(fingerprint, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
