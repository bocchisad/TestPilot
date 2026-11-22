#!/usr/bin/env python3
"""Capture the code and binary versions used by a measurement run."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Sequence


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _inventory(root: Path) -> tuple[list[str], list[str]]:
    required = [
        "scripts/mutate_bootstrap.py",
        "scripts/mutate/run_mutant.py",
        "scripts/aggregate_bootstrap_w6.py",
        "scripts/features_reference.py",
        "scripts/measurement_provenance.py",
    ]
    required.extend(
        path.relative_to(root).as_posix()
        for path in sorted((root / "scripts").glob("measure_*.sh"))
    )
    return required, []


def capture_provenance(
    root: Path,
    binary: Path,
    *,
    required_paths: Sequence[str] | None = None,
    optional_paths: Sequence[str] = (),
) -> dict:
    root = root.resolve()
    if required_paths is None:
        required_paths, discovered_optional = _inventory(root)
        optional_paths = (*optional_paths, *discovered_optional)

    try:
        head_sha = _git(root, "rev-parse", "HEAD")
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(f"cannot determine measurement source HEAD: {exc}") from exc

    try:
        version = subprocess.run(
            [str(binary), "--version"], capture_output=True, text=True, check=False
        )
    except OSError as exc:
        raise RuntimeError(f"cannot run measurement binary --version: {exc}") from exc
    bin_version = (version.stdout or version.stderr).strip()
    if version.returncode != 0 or not re.search(r"\b\d+\.\d+\.\d+\b", bin_version):
        raise RuntimeError(
            f"measurement binary --version failed (exit {version.returncode}): "
            f"{bin_version or 'empty or malformed version output'}"
        )
    try:
        binary_sha256 = hashlib.sha256(Path(binary).read_bytes()).hexdigest()
    except OSError as exc:
        raise RuntimeError(f"cannot hash measurement binary {binary}: {exc}") from exc

    rows = []
    for relative in sorted(set(required_paths)):
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"script path must be repository-relative: {relative}")
        file_path = root / path
        if not file_path.is_file():
            raise RuntimeError(f"required measurement script is missing: {relative}")
        try:
            commit_blob = _git(root, "rev-parse", f"HEAD:{path.as_posix()}")
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"required measurement script is absent from HEAD: {relative}"
            ) from exc
        blob_bytes = subprocess.run(
            ["git", "hash-object", str(path)],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        status = _git(root, "status", "--porcelain", "--", path.as_posix())
        rows.append(
            {
                "path": path.as_posix(),
                "commit_blob_sha1": commit_blob,
                "worktree_blob_sha1": blob_bytes,
                "dirty": bool(status) or commit_blob != blob_bytes,
            }
        )

    missing_optional = []
    for relative in sorted(set(optional_paths)):
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(
                f"optional script path must be repository-relative: {relative}"
            )
        if not (root / path).is_file():
            missing_optional.append(path.as_posix())
        else:
            # Present optional scripts are part of the reproducibility snapshot too.
            rows.extend(
                capture_provenance(
                    root,
                    binary,
                    required_paths=[path.as_posix()],
                )["scripts"]
            )

    return {
        "head_sha": head_sha,
        "captured_at_utc": datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "bin_version": bin_version,
        "binary_sha256": binary_sha256,
        "scripts": sorted(rows, key=lambda row: row["path"]),
        "missing_optional_scripts": missing_optional,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--bin", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        manifest = capture_provenance(args.root, args.bin)
    except (RuntimeError, ValueError, OSError, subprocess.CalledProcessError) as exc:
        parser.error(str(exc))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote provenance snapshot to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
