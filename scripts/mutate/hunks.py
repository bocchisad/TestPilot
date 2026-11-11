"""Git unified-diff hunk parsing for mutation bootstrap."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


def parse_hunks(repo: Path, parent: str, sha: str) -> list[dict]:
    """Parse unified diff -U0 into per-file old/new line sets (1-based)."""
    try:
        raw = subprocess.check_output(
            ["git", "diff", "-U0", "--find-renames", f"{parent}..{sha}"],
            cwd=repo,
        )
    except subprocess.CalledProcessError:
        return []
    text = raw.decode("utf-8", errors="replace")
    hunks: list[dict] = []
    cur: dict | None = None
    for line in text.splitlines():
        if line.startswith("diff --git "):
            if cur:
                hunks.append(cur)
            cur = {
                "path": "",
                "old_path": None,
                "change_type": "M",
                "old_lines": set(),
                "new_lines": set(),
                "is_binary": False,
            }
        elif line.startswith("rename from ") and cur is not None:
            cur["old_path"] = line[len("rename from ") :]
            cur["change_type"] = "R"
        elif line.startswith("rename to ") and cur is not None:
            cur["path"] = line[len("rename to ") :]
        elif line.startswith("+++ b/") and cur is not None:
            cur["path"] = line[6:]
        elif line.startswith("--- a/") and cur is not None and cur["old_path"] is None:
            cur["old_path"] = line[6:]
        elif line.startswith("Binary files") and cur is not None:
            cur["is_binary"] = True
            m = re.search(r" and b/(.+) differ$", line)
            if m:
                cur["path"] = m.group(1)
            m2 = re.search(r"^Binary files a/(.+) and ", line)
            if m2 and not cur.get("old_path"):
                cur["old_path"] = m2.group(1)
                if not cur["path"]:
                    cur["path"] = m2.group(1)
        elif line.startswith("new file mode") and cur is not None:
            cur["change_type"] = "A"
        elif line.startswith("deleted file mode") and cur is not None:
            cur["change_type"] = "D"
        elif line.startswith("@@") and cur is not None:
            m = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
            if not m:
                continue
            old_start, old_len = int(m.group(1)), int(m.group(2) or "1")
            new_start, new_len = int(m.group(3)), int(m.group(4) or "1")
            if old_len:
                for ln in range(old_start, old_start + old_len):
                    cur["old_lines"].add(ln)
            if new_len:
                for ln in range(new_start, new_start + new_len):
                    cur["new_lines"].add(ln)
    if cur:
        hunks.append(cur)
    for h in hunks:
        if not h["path"] and h["old_path"]:
            h["path"] = h["old_path"]
    return [h for h in hunks if h["path"]]


def show_blob(repo: Path, rev: str, path: str) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "show", f"{rev}:{path}"],
            cwd=repo,
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        return None


def is_source_py(path: str) -> bool:
    norm = path.replace("\\", "/")
    if not norm.endswith(".py"):
        return False
    parts = norm.split("/")
    if parts and parts[0] == "tests":
        return False
    if any(p == "tests" for p in parts):
        return False
    base = parts[-1]
    if base.startswith("test_") and parts[-2:] == ["tests", base]:
        return False
    return True
