#!/usr/bin/env python3
"""Inventory CI check-runs for every unique PR-intermediate commit SHA."""

from __future__ import annotations

import argparse
import json
import math
import random
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from run_pr_intermediate_feasibility import (
    _GITHUB_REPOS,
    _classify_check_runs,
    _fetch_check_runs,
)


def _graphql_check_runs(slug: str, shas: list[str]) -> dict[str, dict]:
    owner, repo = slug.split("/", 1)
    fields = (
        "... on Commit { oid statusCheckRollup { contexts(first:100) { "
        "totalCount nodes { __typename ... on CheckRun { name conclusion "
        "completedAt startedAt checkSuite { app { slug } } } } } } }"
    )
    aliases = [f'c{i}: object(oid:"{sha}") {{{fields}}}' for i, sha in enumerate(shas)]
    query = (
        'query { repository(owner:"'
        + owner
        + '",name:"'
        + repo
        + '") { '
        + " ".join(aliases)
        + " } }"
    )
    result = subprocess.run(
        ["gh", "api", "graphql", "-f", "query=" + query],
        check=False,
        capture_output=True,
        text=True,
        timeout=90,
    )
    if result.returncode:
        return {
            sha: {"status": "unavailable", "error": result.stderr[-1000:]}
            for sha in shas
        }
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {
            sha: {"status": "unavailable", "error": "invalid_graphql_json"}
            for sha in shas
        }
    data = (payload.get("data") or {}).get("repository") or {}
    errors = payload.get("errors") or []
    answer = {}
    for i, sha in enumerate(shas):
        commit = data.get(f"c{i}")
        rollup = commit.get("statusCheckRollup") if commit else None
        contexts = (rollup or {}).get("contexts") or {}
        nodes = contexts.get("nodes") or []
        if errors or not commit or not rollup or contexts.get("totalCount", 0) > 100:
            answer[sha] = {"status": "unavailable", "error": "graphql_incomplete"}
            continue
        check_runs = []
        for node in nodes:
            if node.get("__typename") != "CheckRun":
                continue
            suite = node.get("checkSuite") or {}
            app = suite.get("app") or {}
            check_runs.append(
                {
                    "name": node.get("name", ""),
                    "conclusion": (node.get("conclusion") or "").lower() or None,
                    "completed_at": node.get("completedAt"),
                    "started_at": node.get("startedAt"),
                    "app": {"slug": app.get("slug", "")},
                }
            )
        classified = _classify_check_runs({"check_runs": check_runs})
        classified.update({"source": "graphql_status_check_rollup_check_runs"})
        answer[sha] = classified
    return answer


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rest-budget", type=int, default=4700)
    parser.add_argument("--seed", type=int, default=20261122)
    args = parser.parse_args()

    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    cache = (
        json.loads(args.cache.read_text(encoding="utf-8"))
        if args.cache.is_file()
        else {}
    )
    commits: dict[str, dict] = {}
    repos: dict[str, dict] = {}
    for name, data in inventory.items():
        slug = _GITHUB_REPOS[name]
        for sha, row in data["unique_commits"].items():
            key = f"{slug}:{sha}"
            commits[key] = {"repo": name, "sha": sha, **row}
        repos[name] = {"slug": slug, "eligible_pr_count": data["eligible_pr_count"]}

    needed = [key for key in commits if key not in cache]
    rate = subprocess.run(
        ["gh", "api", "rate_limit"], capture_output=True, text=True, check=True
    )
    remaining = json.loads(rate.stdout)["resources"]["core"]["remaining"]
    rest_count = min(len(needed), args.rest_budget, max(0, remaining - 100))
    rest_keys = needed[:rest_count]
    completed = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {
            pool.submit(
                _fetch_check_runs,
                commits[key]["repo"] and repos[commits[key]["repo"]]["slug"],
                commits[key]["sha"],
            ): key
            for key in rest_keys
        }
        for future in as_completed(futures):
            key = futures[future]
            try:
                cache[key] = future.result()
            except Exception as exc:  # noqa: BLE001 - preserve every API failure as unavailable
                cache[key] = {"status": "unavailable", "error": str(exc)}
            completed += 1
            if completed % 25 == 0 or completed == len(rest_keys):
                args.cache.parent.mkdir(parents=True, exist_ok=True)
                args.cache.write_text(
                    json.dumps(cache, indent=2) + "\n", encoding="utf-8"
                )
                print(
                    f"REST check-runs fetched {completed}/{len(rest_keys)}", flush=True
                )

    remaining_keys = [key for key in needed if key not in cache]
    for name, repo_info in repos.items():
        slug = repo_info["slug"]
        selected = [key for key in remaining_keys if commits[key]["repo"] == name]
        for offset in range(0, len(selected), 20):
            batch = selected[offset : offset + 20]
            fetched = _graphql_check_runs(slug, [commits[key]["sha"] for key in batch])
            for key in batch:
                cache[key] = fetched.get(
                    commits[key]["sha"],
                    {"status": "unavailable", "error": "graphql_missing_sha"},
                )
                cache[key]["inventory_source"] = "graphql_fallback_after_rest_budget"
            args.cache.parent.mkdir(parents=True, exist_ok=True)
            args.cache.write_text(json.dumps(cache, indent=2) + "\n", encoding="utf-8")
        print(f"GraphQL fallback queried {len(selected)} {name} commits", flush=True)

    rng = random.Random(args.seed)
    selected_heads: dict[str, dict[str, dict]] = {name: {} for name in repos}
    summaries = {}
    for name, data in inventory.items():
        slug = repos[name]["slug"]
        by_pr: dict[str, list[tuple[str, dict]]] = {}
        for sha, row in data["unique_commits"].items():
            key = f"{slug}:{sha}"
            by_pr_rows = row["commit_indices"]
            for link in by_pr_rows:
                by_pr.setdefault(link["pr"], []).append((sha, link))
        unavailable = failures = successes = 0
        for sha in data["unique_commits"]:
            status = cache.get(f"{slug}:{sha}", {}).get("status", "unavailable")
            if status == "failure":
                failures += 1
            elif status == "success":
                successes += 1
            else:
                unavailable += 1
        fallback_prs = []
        eligible_failures: dict[str, dict] = {}
        eligible_successes: dict[str, dict] = {}
        for pr, links in by_pr.items():
            ordered = sorted(links, key=lambda pair: pair[1]["index"])
            pr_shas = list(dict.fromkeys(sha for sha, _ in ordered))
            statuses = [
                cache.get(f"{slug}:{sha}", {}).get("status", "unavailable")
                for sha in pr_shas
            ]
            if "unavailable" in statuses:
                fallback_prs.append(pr)
                first_sha = ordered[0][0]
                selected_heads[name].setdefault(first_sha, {"pr_numbers": []})
                selected_heads[name][first_sha]["pr_numbers"].append(pr)
                selected_heads[name][first_sha].setdefault("reasons", set()).add(
                    "fallback_first_commit_for_pr"
                )
                continue
            for sha, status in zip(pr_shas, statuses, strict=True):
                if status == "failure":
                    eligible_failures.setdefault(sha, {"sha": sha, "pr_numbers": []})[
                        "pr_numbers"
                    ].append(pr)
                elif status == "success":
                    eligible_successes.setdefault(sha, {"sha": sha, "pr_numbers": []})[
                        "pr_numbers"
                    ].append(pr)
        for sha, row in eligible_failures.items():
            selected_heads[name].setdefault(sha, {"pr_numbers": [], "reasons": set()})[
                "pr_numbers"
            ].extend(row["pr_numbers"])
            selected_heads[name][sha]["reasons"].add("ci_failure")
        success_rows = list(eligible_successes.values())
        sample_n = math.ceil(0.1 * len(success_rows)) if success_rows else 0
        for row in rng.sample(success_rows, sample_n) if sample_n else []:
            selected_heads[name].setdefault(
                row["sha"], {"pr_numbers": [], "reasons": set()}
            )["pr_numbers"].extend(row["pr_numbers"])
            selected_heads[name][row["sha"]]["reasons"].add(
                "random_10_percent_ci_success"
            )
        for sha, row in selected_heads[name].items():
            row["pr_numbers"] = sorted(set(row["pr_numbers"]), key=int)
            row["reasons"] = sorted(row["reasons"])
        summaries[name] = {
            "eligible_pr_count": len(data["prs"]),
            "raw_commit_appearances": data["raw_commit_appearances"],
            "unique_head_shas": data["unique_head_shas"],
            "shared_head_duplicates_removed": data["raw_commit_appearances"]
            - data["unique_head_shas"],
            "ci_failure": failures,
            "ci_success": successes,
            "ci_unavailable": unavailable,
            "fallback_pr_count": len(fallback_prs),
            "fallback_pr_numbers": sorted(fallback_prs, key=int),
            "selected_for_pytest": len(selected_heads[name]),
            "selected_failure_heads": len(eligible_failures),
            "sampled_success_heads": sample_n,
        }

    report = {
        "seed": args.seed,
        "status_policy": "same as check-runs API: latest completed check by name/app; failure/timed_out/startup_failure => failure; all success/neutral/skipped => success; otherwise unavailable",
        "api_sources": "REST check-runs endpoint for cached REST rows; GraphQL statusCheckRollup check-run contexts for the rest, with a rollup-state query to confirm absent statuses",
        "repositories": summaries,
        "commits": {
            key: {
                "repo": row["repo"],
                "pr_numbers": row["prs"],
                "commit_indices": row["commit_indices"],
                "ci": cache.get(key, {"status": "unavailable"}),
                "selected_for_pytest": row["sha"] in selected_heads[row["repo"]],
            }
            for key, row in commits.items()
        },
        "selected_heads": {
            name: [{"head": sha, **details} for sha, details in sorted(rows.items())]
            for name, rows in selected_heads.items()
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summaries, indent=2))
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
