"""Create deterministic mutant git commits and optional single-SHA collect."""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

MUTANT_REF_PREFIX = "refs/testpilot/mutants"


def _run(
    repo: Path,
    args: list[str],
    *,
    input_text: str | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    e = os.environ.copy()
    if env:
        e.update(env)
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        input=input_text,
        capture_output=True,
        text=True,
        check=check,
        env=e,
    )


def mutant_ref(parent_sha: str, index: int) -> str:
    return f"{MUTANT_REF_PREFIX}/{parent_sha}/{index}"


def delete_mutant_ref(repo: Path, parent_sha: str, index: int) -> None:
    ref = mutant_ref(parent_sha, index)
    _run(repo, ["update-ref", "-d", ref], check=False)


def author_ts(repo: Path, sha: str) -> int:
    out = _run(repo, ["log", "-1", "--format=%at", sha]).stdout.strip()
    return int(out)


def committer_ts(repo: Path, sha: str) -> int:
    out = _run(repo, ["log", "-1", "--format=%ct", sha]).stdout.strip()
    return int(out)


MUTATE_IDENTITY = {
    "GIT_AUTHOR_NAME": "testpilot-mutate",
    "GIT_AUTHOR_EMAIL": "testpilot-mutate@localhost",
    "GIT_COMMITTER_NAME": "testpilot-mutate",
    "GIT_COMMITTER_EMAIL": "testpilot-mutate@localhost",
}


def _hash_blob(repo: Path, content: str) -> str:
    p = _run(
        repo,
        ["hash-object", "-w", "--stdin"],
        input_text=content,
    )
    return p.stdout.strip()


def _build_tree(repo: Path, parent_sha: str, updates: dict[str, str]) -> str:
    with tempfile.TemporaryDirectory(prefix="tp-mutant-index-") as td:
        index_file = Path(td) / "index"
        env = {"GIT_INDEX_FILE": str(index_file)}
        _run(repo, ["read-tree", parent_sha], env=env)
        for path, content in updates.items():
            blob = _hash_blob(repo, content)
            _run(
                repo,
                ["update-index", "--add", "--cacheinfo", "100644", blob, path],
                env=env,
            )
        return _run(repo, ["write-tree"], env=env).stdout.strip()


def create_mutant_commit(
    repo: Path,
    parent_sha: str,
    index: int,
    file_updates: dict[str, str],
    *,
    message: str | None = None,
) -> str:
    """Commit M with parent=C; author=author(C)+i, committer=committer(C)+i."""
    if not file_updates:
        raise ValueError("file_updates must not be empty")
    tree = _build_tree(repo, parent_sha, file_updates)
    a_ts = author_ts(repo, parent_sha) + index
    c_ts = committer_ts(repo, parent_sha) + index
    env = {
        **MUTATE_IDENTITY,
        "GIT_AUTHOR_DATE": f"@{a_ts}",
        "GIT_COMMITTER_DATE": f"@{c_ts}",
    }
    msg = message or f"testpilot-mutant {index}"
    p = _run(
        repo,
        ["commit-tree", tree, "-p", parent_sha, "-m", msg],
        env=env,
    )
    new_sha = p.stdout.strip()
    ref = mutant_ref(parent_sha, index)
    _run(repo, ["update-ref", ref, new_sha])
    return new_sha


@dataclass
class CollectGitShaResult:
    ok: bool
    attempted: list[list[str]]
    stderr: str = ""
    note: str = ""


def collect_git_sha(repo: Path, db: Path, sha: str) -> CollectGitShaResult:
    """Index commit M into the DB (requires `testpilot collect git --sha` when available)."""
    testpilot = os.environ.get("TESTPILOT_BIN", "testpilot")
    attempts = [
        [testpilot, "--db", str(db), "collect", "git", "--sha", sha],
        [testpilot, "--db", str(db), "collect", "git-sha", "--sha", sha],
    ]
    last_err = ""
    for cmd in attempts:
        r = subprocess.run(cmd, cwd=repo, capture_output=True, text=True)
        if r.returncode == 0:
            return CollectGitShaResult(ok=True, attempted=attempts)
        last_err = (r.stderr or r.stdout or "")[-2000:]
    verify = subprocess.run(
        ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
        cwd=repo,
        capture_output=True,
    )
    note = (
        "single-SHA git collect is not available in this testpilot build; "
        "re-run with a CLI that supports `collect git --sha` (mutants live off HEAD)."
    )
    if verify.returncode != 0:
        note = f"commit {sha} missing in repo; " + note
    return CollectGitShaResult(
        ok=False,
        attempted=attempts,
        stderr=last_err,
        note=note,
    )


def first_parent(repo: Path, sha: str) -> str | None:
    out = _run(repo, ["rev-list", "--parents", "-n", "1", sha]).stdout.strip()
    parts = out.split()
    if len(parts) < 2:
        return None
    return parts[1]
