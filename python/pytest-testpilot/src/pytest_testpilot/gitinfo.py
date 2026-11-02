from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class GitInfo:
    sha: str
    dirty: bool
    diff_hash: str | None


def collect_gitinfo(cwd: Path | None = None) -> GitInfo:
    root = cwd or Path.cwd()
    sha = _run(["git", "rev-parse", "HEAD"], root).strip()
    porcelain = _run(["git", "status", "--porcelain"], root)
    dirty = bool(porcelain.strip())
    if not dirty:
        return GitInfo(sha=sha, dirty=False, diff_hash=None)

    h = hashlib.sha256()
    # Tracked diff
    tracked = _run(["git", "diff", "HEAD"], root, check=False)
    h.update(b"tracked\0")
    h.update(tracked.encode("utf-8", errors="surrogateescape"))
    # Untracked files (sorted)
    h.update(b"\0untracked\0")
    for line in porcelain.splitlines():
        status = line[:2]
        path = line[3:]
        if status.startswith("??") or "?" in status:
            # untracked
            p = root / path
            h.update(path.encode())
            h.update(b"\0")
            if p.is_file():
                h.update(p.read_bytes())
            h.update(b"\0")
        else:
            h.update(line.encode())
            h.update(b"\0")
    return GitInfo(sha=sha, dirty=True, diff_hash=h.hexdigest())


def _run(cmd: list[str], cwd: Path, check: bool = True) -> str:
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed: {proc.stderr}")
    return proc.stdout
