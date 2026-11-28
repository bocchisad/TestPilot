#!/usr/bin/env python3
"""Run authorized CI-filtered PR-intermediate pairs in resumable 30-minute batches."""

from __future__ import annotations

import argparse
import json
import math
import random
import shutil
import statistics
import time
from collections import Counter
from pathlib import Path

from run_pr_intermediate_feasibility import _pr_groups, _run_pair


def _rows(
    inventory: dict, ci_inventory: dict, repo_paths: dict[str, Path], attrs_limit: int
) -> list[dict]:
    rows = []
    for name, repo_data in inventory.items():
        prs = repo_data["prs"]
        pr_by_number = {pr["pr"]: pr for pr in prs}
        groups = _pr_groups(
            {
                name: [
                    {
                        "repo": name,
                        "pr": pr["pr"],
                        "commits": pr["commits"],
                        "base": pr["base"],
                        "commit_count": pr["commit_count"],
                    }
                    for pr in prs
                ]
            }
        )
        selected = []
        if name != "attrs":
            selected = ci_inventory["selected_heads"][name]
        else:
            rng = random.Random(20261122)
            by_pr: dict[str, list[str]] = {}
            status_by_sha = {}
            for pr in prs[:attrs_limit]:
                by_pr[pr["pr"]] = list(dict.fromkeys(pr["commits"]))
                for sha in by_pr[pr["pr"]]:
                    status_by_sha[sha] = ci_inventory["commits"][
                        f"python-attrs/attrs:{sha}"
                    ]["ci"]["status"]
            chosen: dict[str, dict] = {}
            fail_heads = set()
            success_heads = set()
            for pr_number, shas in by_pr.items():
                statuses = [status_by_sha[sha] for sha in shas]
                if "unavailable" in statuses:
                    first = shas[0]
                    chosen.setdefault(
                        first, {"head": first, "pr_numbers": [], "reasons": []}
                    )["pr_numbers"].append(pr_number)
                    chosen[first]["reasons"].append("fallback_first_commit_for_pr")
                    continue
                for sha, status in zip(shas, statuses, strict=True):
                    if status == "failure":
                        fail_heads.add(sha)
                    elif status == "success":
                        success_heads.add(sha)
            for sha in fail_heads:
                chosen.setdefault(sha, {"head": sha, "pr_numbers": [], "reasons": []})[
                    "reasons"
                ].append("ci_failure")
                chosen[sha]["pr_numbers"].extend(
                    link["pr"]
                    for link in repo_data["unique_commits"][sha]["commit_indices"]
                    if link["pr"] in by_pr
                )
            success_sample = math.ceil(0.1 * len(success_heads)) if success_heads else 0
            for sha in (
                rng.sample(sorted(success_heads), success_sample)
                if success_sample
                else []
            ):
                chosen.setdefault(sha, {"head": sha, "pr_numbers": [], "reasons": []})[
                    "reasons"
                ].append("random_10_percent_ci_success")
                chosen[sha]["pr_numbers"].extend(
                    link["pr"]
                    for link in repo_data["unique_commits"][sha]["commit_indices"]
                    if link["pr"] in by_pr
                )
            selected = list(chosen.values())

        for choice in selected:
            sha = choice["head"]
            unique = repo_data["unique_commits"][sha]
            links = unique["commit_indices"]
            pr_numbers = sorted(set(unique["prs"]), key=int)
            primary_pr = unique["prs"][0]
            primary = pr_by_number[primary_pr]
            group = groups[primary_pr]
            ci = ci_inventory["commits"][
                f"{'pallets/click' if name == 'click' else 'pallets/jinja' if name == 'jinja' else 'python-attrs/attrs'}:{sha}"
            ]["ci"]
            rows.append(
                {
                    "repo": name,
                    "pr": primary_pr,
                    "pr_numbers": pr_numbers,
                    "pr_group": group,
                    "pr_commit_indices": links,
                    "commit_index": min(link["index"] for link in links),
                    "commit_count": primary["commit_count"],
                    "base": primary["base"],
                    "head": sha,
                    "ci": ci,
                    "selection_reasons": choice.get("reasons", []),
                    "repo_path": str(repo_paths[name]),
                    "newest_pr_rank": min(
                        next(i for i, pr in enumerate(prs) if pr["pr"] == n)
                        for n in pr_numbers
                    ),
                }
            )
    rows.sort(
        key=lambda row: (
            row["newest_pr_rank"],
            row["commit_index"],
            row["repo"],
            row["head"],
        )
    )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--ci-inventory", type=Path, required=True)
    parser.add_argument(
        "--repo-paths", action="append", required=True, metavar="NAME=PATH"
    )
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--attrs-newest-prs", type=int, default=90)
    parser.add_argument("--pair-timeout-s", type=int, default=300)
    parser.add_argument("--batch-timeout-s", type=int, default=1800)
    parser.add_argument("--python", default="python3.13")
    args = parser.parse_args()

    repos = {
        item.split("=", 1)[0]: Path(item.split("=", 1)[1]).resolve()
        for item in args.repo_paths
    }
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    ci_inventory = json.loads(args.ci_inventory.read_text(encoding="utf-8"))
    rows = _rows(inventory, ci_inventory, repos, args.attrs_newest_prs)
    out_dir = args.out_dir.resolve()
    work_root = out_dir / "work"
    work_root.mkdir(parents=True, exist_ok=True)
    pair_file = out_dir / "pairs.jsonl"
    prior = []
    if pair_file.is_file():
        prior = [
            json.loads(line)
            for line in pair_file.read_text(encoding="utf-8").splitlines()
            if line
        ]
    terminal = {
        "completed",
        "git_objects_missing",
        "env_incompatible",
        "head_source_invalid",
        "base_source_invalid",
        "test_collection_error",
        "timeout",
    }
    finished = {row["head"] for row in prior if row.get("status") in terminal}
    pending = [row for row in rows if row["head"] not in finished]
    python = Path(shutil.which(args.python) or args.python).resolve()
    if not python.is_file():
        parser.error(f"Python interpreter not found: {args.python}")
    budget = min(args.batch_timeout_s, 1800)
    deadline = time.monotonic() + budget
    batch_started = time.monotonic()
    batch_rows = []
    for row in pending:
        remaining = deadline - time.monotonic()
        if remaining < min(args.pair_timeout_s, 60):
            break
        timeout = min(args.pair_timeout_s, int(remaining))
        result = _run_pair(repos[row["repo"]], row, work_root, python, timeout)
        if result["status"] == "timeout" and timeout < args.pair_timeout_s:
            result.update(
                status="batch_deadline",
                reason="batch_budget_exhausted_before_pair_timeout",
            )
        result["batch"] = 1 + max((line.get("batch", 0) for line in prior), default=0)
        result["selection_reasons"] = row["selection_reasons"]
        result["newest_pr_rank"] = row["newest_pr_rank"]
        batch_rows.append(result)
        with pair_file.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(result, sort_keys=True) + "\n")
            stream.flush()
        print(
            f"{row['repo']} PRs#{','.join(result['pr_numbers'])} {row['head'][:10]}: "
            f"{result['status']} {result['wall_seconds']}s positive={result['positive']}",
            flush=True,
        )

    latest_by_head = {row["head"]: row for row in prior}
    latest_by_head.update({row["head"]: row for row in batch_rows})
    cumulative = list(latest_by_head.values())
    status_counts = Counter(row.get("status", "unknown") for row in cumulative)
    report = {
        "attrs_newest_prs": args.attrs_newest_prs,
        "pair_timeout_s": args.pair_timeout_s,
        "batch_timeout_s": budget,
        "batch_elapsed_s": round(time.monotonic() - batch_started, 2),
        "planned_unique_candidates": len(rows),
        "terminal_unique_heads": len(
            finished
            | {row["head"] for row in batch_rows if row.get("status") in terminal}
        ),
        "pending_unique_heads": sum(
            row["head"] not in finished
            and row["head"]
            not in {x["head"] for x in batch_rows if x.get("status") in terminal}
            for row in rows
        ),
        "statuses": dict(status_counts),
        "positive_pairs": sum(bool(row.get("positive")) for row in cumulative),
        "positive_outside_new_test": sum(
            bool(row.get("positive")) and row.get("test_modified_in_head") is False
            for row in cumulative
        ),
        "median_pair_s": statistics.median(
            [
                row["wall_seconds"]
                for row in cumulative
                if row.get("wall_seconds") is not None
            ]
        )
        if cumulative
        else None,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "progress.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
