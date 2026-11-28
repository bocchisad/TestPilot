#!/usr/bin/env python3
"""Screen local Git history for auditable real bug-fix pair leads.

The scanner is read-only. It examines all refs, diffs merge commits against
their first parent, and deduplicates equivalent patches before emitting a
newest-first shortlist. It does not infer that a lead is a confirmed bug.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

_MESSAGE_MATCH = re.compile(
    r"\b(?:fix(?:es|ed|ing)?|bug(?:fix)?|regression|revert)\b|"
    r"\b(?:fixes|closes)\s+#\d+\b",
    re.IGNORECASE,
)
_REVERT_SUBJECT = re.compile(r'^revert\s+".*"\s*$', re.IGNORECASE)
_GIT_HEADER = re.compile(r"^([0-9a-f]{40})\x1f(.*?)\x1f(.*?)\x1f(.*?)\x1f(.*)$")

_PACKAGE_ROOTS = {
    "click": ("src/click/", "click/"),
    "jinja": ("src/jinja2/", "src/jinja/", "jinja2/", "jinja/"),
    "attrs": ("src/attr/", "src/attrs/", "attr/", "attrs/"),
}


def _git(repo: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, check=check, capture_output=True, text=True
    )
    return result.stdout


def _messages(repo: Path) -> dict[str, dict[str, str]]:
    raw = _git(repo, "log", "--all", "--format=%x1e%H%x1f%P%x1f%cI%x1f%s%x1f%b")
    commits: dict[str, dict[str, str]] = {}
    for record in raw.split("\x1e"):
        if not record.strip():
            continue
        lines = record.lstrip("\n").splitlines()
        match = _GIT_HEADER.match(lines[0])
        if not match:
            continue
        sha, parents, committed_at, subject, body_start = match.groups()
        commits[sha] = {
            "parents": parents,
            "committed_at": committed_at,
            "subject": subject,
            "body": "\n".join([body_start, *lines[1:]]).strip(),
        }
    return commits


def _path_commits(repo: Path, *pathspecs: str) -> set[str]:
    raw = _git(repo, "log", "--all", "--format=%H", "--", *pathspecs)
    return {line.strip() for line in raw.splitlines() if line.strip()}


def _first_parent(metadata: dict[str, str]) -> str | None:
    return metadata["parents"].split()[0] if metadata["parents"] else None


def _changed_paths(repo: Path, parent: str | None, commit: str) -> list[str]:
    if parent is None:
        args = [
            "git",
            "diff-tree",
            "--root",
            "--no-commit-id",
            "--name-only",
            "-r",
            "-z",
            commit,
        ]
    else:
        # Explicit parent diff is important for merges; merge-internal commits
        # are independently scanned by --all history traversal.
        args = ["git", "diff", "--name-only", "-z", parent, commit]
    result = subprocess.run(args, cwd=repo, check=False, capture_output=True, text=True)
    if result.returncode:
        return []
    raw = result.stdout
    return sorted({path for path in raw.split("\0") if path})


def _all_changed_paths(
    repo: Path, commits: dict[str, dict[str, str]]
) -> tuple[dict[str, list[str]], set[str]]:
    """Read ordinary-commit paths in one Git process; merges use first-parent diffs."""
    batch = subprocess.run(
        ["git", "log", "--all", "--format=tformat:%H", "--name-only", "-z"],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    result: dict[str, set[str]] = defaultdict(set)
    unavailable: set[str] = set()
    current: str | None = None
    for token in batch.stdout.split("\0"):
        token = token.strip("\n")
        if token in commits:
            current = token
        elif current and token:
            result[current].add(token)
    for sha, metadata in commits.items():
        if len(metadata["parents"].split()) > 1:
            merge_paths = _changed_paths(repo, _first_parent(metadata), sha)
            if merge_paths:
                result[sha].update(merge_paths)
            else:
                unavailable.add(sha)
    if batch.returncode:
        for sha, metadata in commits.items():
            if sha in result or sha in unavailable:
                continue
            paths = _changed_paths(repo, _first_parent(metadata), sha)
            if paths:
                result[sha].update(paths)
            else:
                unavailable.add(sha)
    return {sha: sorted(paths) for sha, paths in result.items()}, unavailable


def _is_test_path(path: str) -> bool:
    parts = [part.lower() for part in Path(path).parts]
    name = parts[-1] if parts else ""
    return (
        any(
            part in {"tests", "testsuite", "test", "fixtures", "fixture"}
            for part in parts
        )
        or name.startswith("test_")
        or name.endswith("_test.py")
    )


def _is_preserved_noncode(path: str) -> bool:
    parts = [part.lower() for part in Path(path).parts]
    name = parts[-1] if parts else ""
    return (
        _is_test_path(path)
        or any(part in {"docs", "doc", "documentation"} for part in parts)
        or name.startswith(("changes", "changelog", "news"))
    )


def _source_paths(name: str, paths: list[str]) -> list[str]:
    roots = _PACKAGE_ROOTS.get(name, (f"src/{name}/", f"{name}/"))
    return sorted(
        path
        for path in paths
        if path.startswith(roots) and not _is_preserved_noncode(path)
    )


def _test_paths(paths: list[str]) -> list[str]:
    return sorted(path for path in paths if _is_test_path(path))


def _patch_id(repo: Path, parent: str | None, commit: str) -> str | None:
    if parent is None:
        return None
    diff = subprocess.run(
        ["git", "diff", "--binary", parent, commit],
        cwd=repo,
        check=False,
        capture_output=True,
    )
    if diff.returncode:
        return None
    result = subprocess.run(
        ["git", "patch-id", "--stable"],
        input=diff.stdout,
        cwd=repo,
        check=False,
        capture_output=True,
    )
    if result.returncode:
        return None
    return result.stdout.split()[0].decode("ascii") if result.stdout.split() else None


def _adds_test_behavior(
    repo: Path, parents: str, commit: str, test_paths: list[str] | None = None
) -> tuple[bool | None, str | None]:
    parent = parents.split()[0] if parents else ""
    if not parent:
        return None, "root_commit_has_no_parent"
    paths = test_paths or ["tests/**", "testsuite/**", "**/tests/**", "**/testsuite/**"]
    result = subprocess.run(
        ["git", "diff", "--unified=0", parent, commit, "--", *paths],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        return None, result.stderr.strip()
    return (
        any(
            line.startswith("+")
            and not line.startswith("+++")
            and re.search(r"\b(?:assert|raises|test_[A-Za-z0-9_]+)\b", line[1:])
            for line in result.stdout.splitlines()
        ),
        None,
    )


def scan_repository(name: str, repo_path: Path) -> dict:
    """Return patch-id-deduplicated screening leads, newest first."""
    repo = repo_path.resolve()
    commits = _messages(repo)
    if not commits:
        raise ValueError(f"no commits found in local Git history: {repo}")
    history_commit_count = int(_git(repo, "rev-list", "--all", "--count").strip())
    head = _git(repo, "rev-parse", "HEAD").strip()
    is_shallow = _git(repo, "rev-parse", "--is-shallow-repository").strip() == "true"
    changelog_commits = _path_commits(
        repo, ":(glob)**/CHANGES*", ":(glob)**/CHANGELOG*", ":(glob)**/NEWS*"
    )
    changed_paths_by_commit, unavailable_paths = _all_changed_paths(repo, commits)

    candidates: list[dict] = []
    revert_excluded_count = 0
    for sha, metadata in commits.items():
        if _REVERT_SUBJECT.match(metadata["subject"]):
            revert_excluded_count += 1
            continue
        reasons = []
        if _MESSAGE_MATCH.search(f"{metadata['subject']}\n{metadata['body']}"):
            reasons.append("message_pattern")
        if sha in changelog_commits:
            reasons.append("changes_file")
        if not reasons:
            continue

        parent = _first_parent(metadata)
        paths = changed_paths_by_commit.get(sha, [])
        source_paths = _source_paths(name, paths)
        test_paths = _test_paths(paths)
        has_source_test = bool(source_paths and test_paths)
        behavior, error = (
            _adds_test_behavior(repo, metadata["parents"], sha, test_paths)
            if has_source_test
            else (False, None)
        )
        candidates.append(
            {
                "repo": name,
                "commit": sha,
                **metadata,
                "is_merge": len(metadata["parents"].split()) > 1,
                "diff_parent": parent,
                "match_reasons": reasons,
                "changed_paths": paths,
                "package_source_paths": source_paths,
                "test_paths": test_paths,
                "has_source_and_test_changes": has_source_test,
                "has_test_behavior_addition": behavior,
                "test_behavior_scan_complete": behavior is not None,
                "test_behavior_scan_error": error,
                "changed_paths_scan_complete": sha not in unavailable_paths,
                "patch_id": (
                    _patch_id(repo, parent, sha)
                    if sha not in unavailable_paths
                    else None
                ),
            }
        )

    # A merge commit and its first-parent equivalent, or a cherry-pick in a
    # stable branch, share a stable patch-id. Keep the newest representative,
    # while preserving every commit/ref for audit.
    candidates.sort(key=lambda row: (row["committed_at"], row["commit"]), reverse=True)
    groups: dict[str, list[dict]] = defaultdict(list)
    unique: list[dict] = []
    for row in candidates:
        patch = row["patch_id"]
        if patch:
            groups[patch].append(row)
        else:
            unique.append(row)
    for patch, group in groups.items():
        representative = dict(group[0])
        representative["duplicate_commits"] = [
            {
                "commit": row["commit"],
                "subject": row["subject"],
                "committed_at": row["committed_at"],
                "is_merge": row["is_merge"],
                "diff_parent": row["diff_parent"],
                "patch_id": patch,
            }
            for row in group
        ]
        unique.append(representative)
    unique.sort(key=lambda row: (row["committed_at"], row["commit"]), reverse=True)
    raw_count = len(candidates)
    return {
        "repo": name,
        "repo_path": str(repo),
        "head": head,
        "history_commit_count": history_commit_count,
        "is_shallow": is_shallow,
        "raw_candidate_count": raw_count,
        "revert_excluded_count": revert_excluded_count,
        "deduplicated_candidate_count": raw_count - len(unique),
        "candidate_count": len(unique),
        "source_and_test_candidate_count": sum(
            row["has_source_and_test_changes"] for row in unique
        ),
        "test_behavior_addition_candidate_count": sum(
            row["has_test_behavior_addition"] is True for row in unique
        ),
        "test_behavior_scan_incomplete_count": sum(
            not row["test_behavior_scan_complete"] for row in unique
        ),
        "changed_paths_scan_incomplete_count": sum(
            not row["changed_paths_scan_complete"] for row in unique
        ),
        "candidate_commits": unique,
    }


def _parse_repo(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("expected NAME=PATH")
    name, path = value.split("=", 1)
    if not name or not path:
        raise argparse.ArgumentTypeError("expected NAME=PATH")
    return name, Path(path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo", action="append", type=_parse_repo, required=True, metavar="NAME=PATH"
    )
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        summaries = [scan_repository(name, path) for name, path in args.repo]
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.error(str(exc))
    args.out_dir.mkdir(parents=True, exist_ok=True)
    compact = []
    for summary in summaries:
        rows = summary.pop("candidate_commits")
        candidate_path = args.out_dir / f"candidates-{summary['repo']}.jsonl"
        candidate_path.write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
            encoding="utf-8",
        )
        compact.append({**summary, "candidate_file": candidate_path.name})
        print(
            f"{summary['repo']}: {summary['history_commit_count']} local commits; "
            f"{summary['raw_candidate_count']} leads before patch-id dedupe; "
            f"{summary['candidate_count']} after; {summary['revert_excluded_count']} Revert subjects excluded"
        )
    summary_path = args.out_dir / "candidate-scan-summary.json"
    summary_path.write_text(
        json.dumps({"local_only": True, "repositories": compact}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
