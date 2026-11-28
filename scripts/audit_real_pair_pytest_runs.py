#!/usr/bin/env python3
"""Audit saved PR-pair logs for pytest runs that did not finish normally."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


_LOG_MARKERS = {
    "pytest_interrupted": ("Interrupted:",),
    "pytest_internal_error": ("INTERNALERROR>",),
    "pytest_collection_error": ("ERROR collecting", "errors during collection"),
}
_RUNS = ("base-1.log", "base-2.log", "head-1.log", "head-2.log")


def audit_pair(row: dict[str, Any]) -> dict[str, Any]:
    """Find incomplete pytest logs and unusable empty comparisons for one saved pair."""
    reasons: set[str] = set()
    affected_logs: set[str] = set()
    log_dir = Path(row["log_dir"])
    for log_name in _RUNS:
        path = log_dir / log_name
        if not path.is_file():
            reasons.add("missing_run_log")
            affected_logs.add(log_name)
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for reason, markers in _LOG_MARKERS.items():
            if any(marker.lower() in text.lower() for marker in markers):
                reasons.add(reason)
                affected_logs.add(log_name)
    if row.get("pytest_exit_codes"):
        if any(code not in {0, 1} for code in row["pytest_exit_codes"].values()):
            reasons.add("pytest_nonzero_exit")
    if row.get("shared_base_test_ids", 0) == 0:
        reasons.add("no_shared_test_ids")
    return {
        "repo": row["repo"],
        "head": row["head"],
        "pr_numbers": row.get("pr_numbers", [row.get("pr", "")]),
        "excluded": bool(reasons),
        "reasons": sorted(reasons),
        "affected_logs": sorted(affected_logs),
    }


def audit_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Keep only the latest saved result per repo/head before auditing completed runs."""
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        latest[(row["repo"], row["head"])] = row
    completed = [row for row in latest.values() if row.get("status") == "completed"]
    audited = [audit_pair(row) for row in completed]
    excluded = [row for row in audited if row["excluded"]]
    by_repo: dict[str, dict[str, int]] = {}
    for repo in sorted({row["repo"] for row in completed}):
        own = [row for row in audited if row["repo"] == repo]
        by_repo[repo] = {
            "completed_heads": len(own),
            "excluded_heads": sum(row["excluded"] for row in own),
            "retained_heads": sum(not row["excluded"] for row in own),
            "positive_excluded_heads": sum(
                row["excluded"]
                and next(
                    source.get("positive", False)
                    for source in reversed(rows)
                    if source["repo"] == row["repo"] and source["head"] == row["head"]
                )
                for row in own
            ),
            "reason_counts": dict(
                Counter(reason for row in own for reason in row["reasons"])
            ),
        }
    return {
        "latest_unique_heads": len(latest),
        "completed_heads": len(completed),
        "excluded_heads": len(excluded),
        "retained_heads": len(completed) - len(excluded),
        "by_repo": by_repo,
        "excluded": excluded,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    rows = [
        json.loads(line)
        for line in args.pairs.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    result = audit_rows(rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {key: value for key, value in result.items() if key != "excluded"}, indent=2
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
