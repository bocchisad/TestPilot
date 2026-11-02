from __future__ import annotations

import contextlib
import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class GitInfo:
    sha: str
    dirty: bool
    diff_hash: str | None


def git_toplevel(start: Path | None = None) -> Path:
    root = start or Path.cwd()
    out = _run(["git", "rev-parse", "--show-toplevel"], root)
    return Path(out.strip()).resolve()


def collect_gitinfo(cwd: Path | None = None) -> GitInfo:
    root = cwd or Path.cwd()
    with contextlib.suppress(RuntimeError):
        root = git_toplevel(root)
    sha = _run(["git", "rev-parse", "HEAD"], root).strip()

    # Fast dirty probe: default untracked listing (dirs as single entries).
    # Avoid `status -uall` here — it walks every file under untracked dirs and
    # dominated sessionfinish (~150ms on 200 tiny tests).
    porcelain = _run(["git", "status", "--porcelain"], root)
    relevant_lines = []
    for line in porcelain.splitlines():
        path = line[3:] if len(line) >= 3 else line
        # rename lines: "R  old -> new" — take the new path after " -> "
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        path = path.strip().strip('"')
        if _ignored_for_dirty(path):
            continue
        relevant_lines.append(line)
    dirty = bool(relevant_lines)
    if not dirty:
        return GitInfo(sha=sha, dirty=False, diff_hash=None)

    h = hashlib.sha256()
    tracked = _run(["git", "diff", "HEAD"], root, check=False)
    h.update(b"tracked\0")
    h.update(tracked.encode("utf-8", errors="surrogateescape"))
    h.update(b"\0untracked\0")
    # B14: list every untracked file (including under dirs) only when dirty
    others = _run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        root,
        check=False,
    )
    for rel in sorted(others.splitlines()):
        rel = rel.strip().strip('"')
        if not rel or _ignored_for_dirty(rel):
            continue
        p = root / rel
        h.update(rel.encode())
        h.update(b"\0")
        if p.is_file():
            h.update(p.read_bytes())
        h.update(b"\0")
    for line in relevant_lines:
        h.update(line.encode())
        h.update(b"\0")
    return GitInfo(sha=sha, dirty=True, diff_hash=h.hexdigest())


def _ignored_for_dirty(path: str) -> bool:
    # N2: strip "./" prefix only — never lstrip("./") which eats leading dots.
    norm = path.replace("\\", "/")
    while norm.startswith("./"):
        norm = norm.removeprefix("./")
    if norm == ".testpilot" or norm.startswith(".testpilot/"):
        return True
    name = Path(norm).name
    return bool(name == ".coverage" or name.startswith(".coverage."))


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
