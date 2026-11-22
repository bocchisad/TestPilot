#!/usr/bin/env python3
"""Screen locally available repository history for candidate real bug-fix pairs.

This command only reads Git metadata. It does not fetch, check out, install, or
run repository code. Candidate matches are leads for manual audit, not positives.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path

_MESSAGE_MATCH = re.compile(
    r"\b(?:fix(?:es|ed|ing)?|bug|regression|revert)\b|"
    r"\b(?:fixes|closes)\s+#\d+\b",
    re.IGNORECASE,
)
_GIT_HEADER = re.compile(r"^([0-9a-f]{40})\x1f(.*?)\x1f(.*?)\x1f(.*?)\x1f(.*)$")
_PATH_HEADER = re.compile(r"^([0-9a-f]{40})\x1f(.*)$")


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    )
    return result.stdout


def _messages(repo: Path) -> dict[str, dict[str, str]]:
    raw = _git(
        repo,
        "log",
        "--all",
        "--format=%x1e%H%x1f%P%x1f%cI%x1f%s%x1f%b",
    )
    commits: dict[str, dict[str, str]] = {}
    for record in raw.split("\x1e"):
        if not record.strip():
            continue
        lines = record.lstrip("\n").splitlines()
        match = _GIT_HEADER.match(lines[0])
        if not match:
            continue
        sha, parents, committed_at, subject, first_body_line = match.groups()
        body = "\n".join([first_body_line, *lines[1:]]).strip()
        commits[sha] = {
            "parents": parents,
            "committed_at": committed_at,
            "subject": subject,
            "body": body,
        }
    return commits


def _path_commits(repo: Path, *pathspecs: str) -> set[str]:
    raw = _git(repo, "log", "--all", "--format=%H", "--", *pathspecs)
    return {line.strip() for line in raw.splitlines() if line.strip()}


def _changed_source_test_paths(repo: Path) -> dict[str, list[str]]:
    raw = _git(
        repo,
        "log",
        "--all",
        "--format=%H\x1f%s",
        "--name-only",
        "--",
        "src/**",
        "tests/**",
    )
    result: dict[str, list[str]] = {}
    current: str | None = None
    for line in raw.splitlines():
        match = _PATH_HEADER.match(line)
        if match:
            current = match.group(1)
            result.setdefault(current, [])
        elif current and line.strip():
            result[current].append(line.strip())
    return result


def _adds_test_behavior(
    repo: Path, parents: str, commit: str
) -> tuple[bool | None, str | None]:
    parent = parents.split()[0] if parents else ""
    if not parent:
        return None, "root_commit_has_no_parent"
    result = subprocess.run(
        ["git", "diff", "--unified=0", parent, commit, "--", "tests/**"],
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
    """Return every history candidate and source/test-path screening counts."""
    repo = repo_path.resolve()
    commits = _messages(repo)
    if not commits:
        raise ValueError(f"no commits found in local Git history: {repo}")
    history_commit_count = int(_git(repo, "rev-list", "--all", "--count").strip())
    head = _git(repo, "rev-parse", "HEAD").strip()
    is_shallow = _git(repo, "rev-parse", "--is-shallow-repository").strip() == "true"
    changelog_commits = _path_commits(
        repo,
        ":(glob)**/CHANGES*",
        ":(glob)**/CHANGELOG*",
        ":(glob)**/NEWS*",
    )
    changed_paths = _changed_source_test_paths(repo)

    candidates = []
    for sha, metadata in commits.items():
        message = f"{metadata['subject']}\n{metadata['body']}"
        reasons = []
        if _MESSAGE_MATCH.search(message):
            reasons.append("message_pattern")
        if sha in changelog_commits:
            reasons.append("changes_file")
        if not reasons:
            continue

        paths = sorted(set(changed_paths.get(sha, [])))
        has_source = any(
            path.startswith("src/") and path.endswith(".py") for path in paths
        )
        has_test = any(
            path.startswith("tests/") and path.endswith(".py") for path in paths
        )
        has_test_behavior_addition, test_behavior_scan_error = (
            _adds_test_behavior(repo, metadata["parents"], sha)
            if has_source and has_test
            else (False, None)
        )
        candidates.append(
            {
                "repo": name,
                "commit": sha,
                **metadata,
                "match_reasons": reasons,
                "source_test_paths": paths,
                "has_source_and_test_changes": has_source and has_test,
                "has_test_behavior_addition": has_test_behavior_addition,
                "test_behavior_scan_complete": has_test_behavior_addition is not None,
                "test_behavior_scan_error": test_behavior_scan_error,
            }
        )

    candidates.sort(key=lambda row: (row["committed_at"], row["commit"]))
    return {
        "repo": name,
        "repo_path": str(repo),
        "head": head,
        "history_commit_count": history_commit_count,
        "is_shallow": is_shallow,
        "candidate_count": len(candidates),
        "source_and_test_candidate_count": sum(
            row["has_source_and_test_changes"] for row in candidates
        ),
        "test_behavior_addition_candidate_count": sum(
            row["has_test_behavior_addition"] is True for row in candidates
        ),
        "test_behavior_scan_incomplete_count": sum(
            row["test_behavior_scan_complete"] is False for row in candidates
        ),
        "candidate_commits": candidates,
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
            f"{summary['candidate_count']} candidates; "
            f"{summary['source_and_test_candidate_count']} changed both source and tests"
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
