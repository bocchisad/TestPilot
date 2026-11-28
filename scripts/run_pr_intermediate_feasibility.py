#!/usr/bin/env python3
"""Inventory and run a bounded PR-intermediate feasibility sample."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import shutil
import statistics
import subprocess
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from run_real_pair_calibration import _install, _junit, _run

_GITHUB_REPOS = {
    "click": "pallets/click",
    "jinja": "pallets/jinja",
    "attrs": "python-attrs/attrs",
}


def _git(repo: Path, *args: str, deadline: float | None = None) -> str:
    timeout = 300
    if deadline is not None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(["git", *args], 0)
        timeout = min(timeout, remaining)
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout)[-2000:])
    return result.stdout.strip()


def inventory_repository(name: str, repo: Path, main_ref: str, deadline: float) -> dict:
    main = _git(repo, "rev-parse", main_ref, deadline=deadline)
    refs = _git(
        repo,
        "for-each-ref",
        "--format=%(refname:strip=2) %(objectname) %(committerdate:unix)",
        "refs/pr",
        deadline=deadline,
    )
    rows = []
    for line in refs.splitlines():
        number, head, committed = line.split()
        if not number.isdigit() or int(number) < 1:
            continue
        try:
            base = _git(repo, "merge-base", main, head, deadline=deadline)
            commits = _git(
                repo,
                "rev-list",
                "--reverse",
                "--topo-order",
                f"{base}..{head}",
                deadline=deadline,
            ).splitlines()
        except (RuntimeError, subprocess.SubprocessError):
            continue
        rows.append(
            {
                "repo": name,
                "pr": number,
                "ref": f"refs/pr/{number}",
                "head": head,
                "base": base,
                "main": main,
                "date_unix": int(committed),
                "commit_count": len(commits),
                "commits": commits,
            }
        )
    eligible = [row for row in rows if row["commit_count"] >= 2]
    eligible.sort(key=lambda row: (row["date_unix"], int(row["pr"])), reverse=True)
    return {
        "repo": name,
        "repo_path": str(repo.resolve()),
        "main_ref": main_ref,
        "main_sha": main,
        "pr_refs_seen": len(rows),
        "pr_with_at_least_two_commits": len(eligible),
        "pr_heads_already_in_main": sum(row["commit_count"] == 0 for row in rows),
        "sample": eligible[:10],
    }


def _unique_commit_rows(samples: dict[str, list[dict]]) -> dict[str, list[dict]]:
    unique: dict[str, list[dict]] = {}
    for name, prs in samples.items():
        by_sha: dict[str, dict] = {}
        for pr in prs:
            for index, sha in enumerate(pr["commits"], 1):
                row = by_sha.setdefault(
                    sha,
                    {
                        "repo": name,
                        "head": sha,
                        "base": pr["base"],
                        "commit_count": pr["commit_count"],
                        "pr_numbers": [],
                        "pr_commit_indices": [],
                    },
                )
                row["pr_numbers"].append(pr["pr"])
                row["pr_commit_indices"].append(
                    {"pr": pr["pr"], "index": index, "count": pr["commit_count"]}
                )
        for row in by_sha.values():
            row["pr_numbers"] = sorted(set(row["pr_numbers"]), key=int)
            row["pr"] = row["pr_numbers"][0]
            row["commit_index"] = min(
                link["index"] for link in row["pr_commit_indices"]
            )
        unique[name] = sorted(
            by_sha.values(),
            key=lambda row: (row["pr_commit_indices"][0]["index"], row["head"]),
        )
    return unique


def _classify_check_runs(payload: dict) -> dict:
    runs = payload.get("check_runs") or []
    if not runs:
        return {"status": "unavailable", "check_run_count": 0, "conclusions": []}
    latest: dict[tuple[str, str], dict] = {}
    for run in runs:
        key = (run.get("name", ""), (run.get("app") or {}).get("slug", ""))
        stamp = run.get("completed_at") or run.get("started_at") or ""
        previous = latest.get(key)
        previous_stamp = (
            previous.get("completed_at") or previous.get("started_at") or ""
            if previous
            else ""
        )
        if previous is None or stamp >= previous_stamp:
            latest[key] = run
    conclusions = [run.get("conclusion") for run in latest.values()]
    failed = {"failure", "timed_out", "startup_failure"}
    successful = {"success", "neutral", "skipped"}
    if any(value in failed for value in conclusions):
        status = "failure"
    elif conclusions and all(value in successful for value in conclusions):
        status = "success"
    else:
        status = "unavailable"
    return {
        "status": status,
        "check_run_count": len(runs),
        "latest_check_count": len(latest),
        "conclusions": sorted(str(value) for value in conclusions),
        "failure_check_names": sorted(
            run.get("name", "")
            for run in latest.values()
            if run.get("conclusion") in failed
        ),
    }


def _select_ci_pytest_rows(rows: list[dict], rng: random.Random) -> dict[str, object]:
    """Choose all CI failures, seeded 10% of successes, or first PR commit on missing CI."""
    by_pr: dict[str, list[tuple[dict, dict]]] = {}
    for row in rows:
        for link in row["pr_commit_indices"]:
            by_pr.setdefault(link["pr"], []).append((row, link))

    chosen: dict[str, dict] = {}
    selection_reasons: dict[str, set[str]] = {}
    fallback_prs = []
    eligible_failures: dict[str, dict] = {}
    eligible_successes: dict[str, dict] = {}
    for pr_number, linked_rows in by_pr.items():
        ordered = sorted(linked_rows, key=lambda item: item[1]["index"])
        pr_rows = {row["head"]: row for row, _link in ordered}
        if any(row["ci"]["status"] == "unavailable" for row in pr_rows.values()):
            fallback_prs.append(pr_number)
            first = ordered[0][0]
            chosen[first["head"]] = first
            selection_reasons.setdefault(first["head"], set()).add(
                "fallback_first_commit_for_pr"
            )
            continue
        for row in pr_rows.values():
            if row["ci"]["status"] == "failure":
                eligible_failures[row["head"]] = row
            elif row["ci"]["status"] == "success":
                eligible_successes[row["head"]] = row

    for head, row in eligible_failures.items():
        chosen[head] = row
        selection_reasons.setdefault(head, set()).add("ci_failure")
    sample_count = math.ceil(0.1 * len(eligible_successes)) if eligible_successes else 0
    sampled_successes = (
        rng.sample(list(eligible_successes.values()), sample_count)
        if sample_count
        else []
    )
    for row in sampled_successes:
        chosen[row["head"]] = row
        selection_reasons.setdefault(row["head"], set()).add(
            "random_10_percent_ci_success"
        )
    for head, row in chosen.items():
        row["selection_reasons"] = sorted(selection_reasons.get(head, set()))

    return {
        "selected": list(chosen.values()),
        "fallback_pr_numbers": sorted(fallback_prs, key=int),
        "eligible_failure_count": len(eligible_failures),
        "sampled_success_count": len(sampled_successes),
    }


def _stable_positive_test_ids(outcomes: Mapping[str, Mapping[str, str]]) -> list[str]:
    """Return only base-pass/head-fail test IDs stable across both runs per tree."""
    required = ("base1", "base2", "head1", "head2")
    if any(label not in outcomes for label in required):
        return []
    shared = set.intersection(*(set(outcomes[label]) for label in required))
    return sorted(
        test
        for test in shared
        if outcomes["base1"][test] == outcomes["base2"][test] == "pass"
        and outcomes["head1"][test] == outcomes["head2"][test] == "fail"
    )


def _promote_pair_timeout(result: dict, remaining_s: int, pair_timeout_s: int) -> dict:
    """Label a timed-out pair as batch-deadline only when the batch shortened it."""
    if result.get("status") == "timeout" and remaining_s < pair_timeout_s:
        result.update(
            status="batch_deadline",
            reason="batch_budget_exhausted_before_pair_timeout",
        )
    return result


def _fetch_check_runs(slug: str, sha: str) -> dict:
    endpoint = f"repos/{slug}/commits/{sha}/check-runs?per_page=100"
    result = subprocess.run(
        ["gh", "api", "--paginate", "--slurp", endpoint],
        check=False,
        capture_output=True,
        text=True,
        timeout=45,
    )
    if result.returncode:
        return {
            "status": "unavailable",
            "error": (result.stderr or result.stdout)[-1000:],
        }
    try:
        pages = json.loads(result.stdout)
        payload = {
            "check_runs": [run for page in pages for run in page.get("check_runs", [])]
        }
    except (json.JSONDecodeError, TypeError, AttributeError):
        return {"status": "unavailable", "error": "invalid_check_runs_response"}
    return _classify_check_runs(payload)


def _load_ci_inventory(
    samples: dict[str, list[dict]], github_repos: dict[str, str], cache_path: Path
) -> tuple[dict[str, dict], dict[str, list[dict]]]:
    unique = _unique_commit_rows(samples)
    cache = (
        json.loads(cache_path.read_text(encoding="utf-8"))
        if cache_path.is_file()
        else {}
    )
    requests = []
    for name, rows in unique.items():
        slug = github_repos.get(name)
        for row in rows:
            key = f"{slug}:{row['head']}"
            if key not in cache:
                if slug:
                    requests.append((key, slug, row["head"]))
                else:
                    cache[key] = {"status": "unavailable", "error": "missing_repo_slug"}
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {
            pool.submit(_fetch_check_runs, slug, sha): key
            for key, slug, sha in requests
        }
        for future, key in futures.items():
            try:
                cache[key] = future.result()
            except (OSError, subprocess.SubprocessError) as exc:
                cache[key] = {"status": "unavailable", "error": str(exc)}
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, indent=2) + "\n", encoding="utf-8")

    ci_rows: dict[str, dict] = {}
    selected: dict[str, list[dict]] = {}
    rng = random.Random(20261122)
    for name, rows in unique.items():
        slug = github_repos.get(name, "")
        for row in rows:
            row["ci"] = cache.get(f"{slug}:{row['head']}", {"status": "unavailable"})
            ci_rows[f"{name}:{row['head']}"] = row
        unavailable = [row for row in rows if row["ci"]["status"] == "unavailable"]
        failures = [row for row in rows if row["ci"]["status"] == "failure"]
        successes = [row for row in rows if row["ci"]["status"] == "success"]
        selection = _select_ci_pytest_rows(rows, rng)
        selected[name] = selection["selected"]
        ci_rows[f"{name}:summary"] = {
            "github_repo": slug,
            "raw_commit_appearances": sum(len(pr["commits"]) for pr in samples[name]),
            "unique_commit_shas": len(rows),
            "shared_commit_duplicates_removed": sum(
                len(pr["commits"]) for pr in samples[name]
            )
            - len(rows),
            "ci_failure_commits": len(failures),
            "ci_success_commits": len(successes),
            "ci_unavailable_commits": len(unavailable),
            "selected_failure_commits": selection["eligible_failure_count"],
            "sampled_success_commits": selection["sampled_success_count"],
            "fallback_pr_count": len(selection["fallback_pr_numbers"]),
            "fallback_pr_numbers": selection["fallback_pr_numbers"],
            "selected_for_pytest": len(selected[name]),
            "selection_policy": "fallback_first_commit_for_pr_when_ci_unavailable; otherwise all_failure_plus_seeded_random_10_percent_success",
            "unavailable_shas": [row["head"] for row in unavailable],
        }
    return ci_rows, selected


def _head_test_change_details(
    repo: Path, head: str, test_ids: list[str], deadline: float
) -> dict:
    changed_paths = set(
        _git(
            repo, "diff", "--name-only", f"{head}^1", head, deadline=deadline
        ).splitlines()
    )
    tree_files = set(
        _git(repo, "ls-tree", "-r", "--name-only", head, deadline=deadline).splitlines()
    )
    test_files = set()
    unresolved = []
    for test_id in test_ids:
        module = test_id.split("::", 1)[0].split(".")
        resolved = None
        for end in range(len(module), 0, -1):
            candidate = "/".join(module[:end]) + ".py"
            if candidate in tree_files:
                resolved = candidate
                break
        if resolved:
            test_files.add(resolved)
        else:
            unresolved.append(test_id)
    return {
        "changed_paths": sorted(changed_paths),
        "positive_test_files": sorted(test_files),
        "test_modified_in_head": (
            bool(changed_paths & test_files) if test_ids and not unresolved else None
        ),
        "unresolved_positive_test_ids": unresolved,
    }


def _pr_groups(samples: dict[str, list[dict]]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for prs in samples.values():
        if not prs:
            continue
        parent = {pr["pr"]: pr["pr"] for pr in prs}

        def find(pr: str) -> str:
            while parent[pr] != pr:
                parent[pr] = parent[parent[pr]]
                pr = parent[pr]
            return pr

        unique = _unique_commit_rows({prs[0]["repo"]: prs})[prs[0]["repo"]]
        for row in unique:
            numbers = row["pr_numbers"]
            for number in numbers[1:]:
                left, right = find(numbers[0]), find(number)
                if left != right:
                    parent[right] = left
        components: dict[str, list[str]] = {}
        for pr in parent:
            components.setdefault(find(pr), []).append(pr)
        for component in components.values():
            merged = sorted(component, key=int)
            for pr in component:
                groups[pr] = merged
    return groups


def _run_pair(
    repo: Path, row: dict, work_root: Path, python: Path, timeout_s: int
) -> dict:
    started = time.monotonic()
    deadline = started + timeout_s
    token = f"{row['repo']}-pr{row['pr']}-{row['head'][:10]}"
    base_wt = work_root / "worktrees" / f"{token}-base"
    head_wt = work_root / "worktrees" / f"{token}-head"
    base_venv = work_root / "venvs" / f"{token}-base"
    head_venv = work_root / "venvs" / f"{token}-head"
    logs = work_root / "pairs" / token
    logs.mkdir(parents=True, exist_ok=True)
    result = {
        "repo": row["repo"],
        "pr": row["pr"],
        "pr_numbers": row.get("pr_numbers", [row["pr"]]),
        "pr_group": row.get("pr_group", row.get("pr_numbers", [row["pr"]])),
        "pr_commit_indices": row.get("pr_commit_indices", []),
        "pr_commit_index": row["commit_index"],
        "pr_commit_count": row["commit_count"],
        "base": row["base"],
        "head": row["head"],
        "ci": row.get("ci", {}),
        "status": "preflight",
        "tests_started": False,
        "positive": False,
        "positive_test_ids": [],
        "wall_seconds": None,
        "log_dir": str(logs),
    }
    worktrees = []
    try:
        for target, commit in ((base_wt, row["base"]), (head_wt, row["head"])):
            target.parent.mkdir(parents=True, exist_ok=True)
            made = subprocess.run(
                ["git", "worktree", "add", "--detach", str(target), commit],
                cwd=repo,
                capture_output=True,
                text=True,
                timeout=max(0.01, min(300, deadline - time.monotonic())),
            )
            if made.returncode:
                result.update(
                    status="git_objects_missing",
                    reason="worktree_failed",
                    stderr=made.stderr[-1000:],
                )
                return result
            worktrees.append(target)
        for venv, tree in ((base_venv, base_wt), (head_venv, head_wt)):
            made = _run(
                [str(python), "-m", "venv", str(venv)], cwd=repo, deadline=deadline
            )
            if made.returncode:
                result.update(status="env_incompatible", reason="venv_setup_failed")
                return result
            ok, log = _install(venv / "bin" / "python", tree, deadline, full=True)
            with (logs / f"install-{tree.name}.log").open("w", encoding="utf-8") as out:
                out.write(log)
            if not ok:
                result.update(
                    status="env_incompatible", reason=f"install_failed:{tree.name}"
                )
                return result
        outcomes = {}
        for label, tree, venv in (
            ("base", base_wt, base_venv),
            ("head", head_wt, head_venv),
        ):
            for attempt in (1, 2):
                xml = logs / f"{label}-{attempt}.xml"
                command = [
                    str(venv / "bin" / "python"),
                    "-m",
                    "pytest",
                    "-q",
                    f"--junitxml={xml}",
                ]
                result["tests_started"] = True
                test_run = _run(command, cwd=tree, deadline=deadline)
                (logs / f"{label}-{attempt}.log").write_bytes(
                    test_run.stdout + b"\n--- stderr ---\n" + test_run.stderr
                )
                if not xml.is_file():
                    failure_text = (test_run.stdout + b"\n" + test_run.stderr).decode(
                        "utf-8", errors="replace"
                    )
                    syntax_error = next(
                        (
                            marker
                            for marker in (
                                "IndentationError",
                                "TabError",
                                "SyntaxError",
                            )
                            if marker in failure_text
                        ),
                        None,
                    )
                    if syntax_error:
                        result.update(
                            status="head_source_invalid"
                            if label == "head"
                            else "base_source_invalid",
                            reason=f"pytest_source_syntax_error:{label}:{syntax_error}",
                            diagnostic="\n".join(failure_text.splitlines()[-12:]),
                        )
                    else:
                        result.update(
                            status="test_collection_error",
                            reason=f"pytest_no_junit:{label}",
                            diagnostic="\n".join(failure_text.splitlines()[-12:]),
                        )
                    return result
                outcomes[f"{label}{attempt}"] = _junit(xml)
        base_ids = set(outcomes["base1"]) & set(outcomes["base2"])
        shared = base_ids & set(outcomes["head1"]) & set(outcomes["head2"])
        positive_ids = _stable_positive_test_ids(outcomes)
        result.update(
            status="completed",
            base_test_ids=len(base_ids),
            shared_base_test_ids=len(shared),
            positive=bool(positive_ids),
            positive_test_ids=positive_ids,
            test_id_policy="test IDs present in base; all four runs shared; repeated pass on base and fail on head",
        )
        result.update(
            _head_test_change_details(repo, row["head"], positive_ids, deadline)
        )
        if positive_ids:
            key = json.dumps(
                {
                    "repo": row["repo"],
                    "pr_numbers": row.get(
                        "pr_group", row.get("pr_numbers", [row["pr"]])
                    ),
                    "test_ids": positive_ids,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            result["unique_change_id"] = hashlib.sha256(key.encode()).hexdigest()
    except subprocess.TimeoutExpired:
        result.update(status="timeout", reason="pair_timeout")
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        result.update(status="env_incompatible", reason=f"setup_or_result_error:{exc}")
    finally:
        result["wall_seconds"] = round(time.monotonic() - started, 3)
        for tree in reversed(worktrees):
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(tree)],
                cwd=repo,
                capture_output=True,
            )
        shutil.rmtree(base_venv, ignore_errors=True)
        shutil.rmtree(head_venv, ignore_errors=True)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", action="append", required=True, metavar="NAME=PATH")
    parser.add_argument("--main-ref", default="refs/remotes/upstream/main")
    parser.add_argument("--python", default="python3.13")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--sample-prs", type=int, default=10)
    parser.add_argument("--pair-timeout-s", type=int, default=300)
    parser.add_argument("--batch-timeout-s", type=int, default=1800)
    parser.add_argument("--inventory-only", action="store_true")
    parser.add_argument(
        "--github-repo", action="append", default=[], metavar="NAME=OWNER/REPO"
    )
    parser.add_argument("--ci-cache", type=Path)
    args = parser.parse_args(argv)
    batch_started = time.monotonic()
    batch_budget = min(args.batch_timeout_s, 1800)
    deadline = batch_started + batch_budget
    repos = {
        value.split("=", 1)[0]: Path(value.split("=", 1)[1]).resolve()
        for value in args.repo
    }
    python = Path(shutil.which(args.python) or args.python).resolve()
    if not python.is_file():
        parser.error(f"Python interpreter not found: {args.python}")
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    work_root = out_dir / "work"
    work_root.mkdir(exist_ok=True)
    inventories = {
        name: inventory_repository(name, repo, args.main_ref, deadline)
        for name, repo in repos.items()
    }
    inventory_finished = time.monotonic()
    (out_dir / "inventory.json").write_text(
        json.dumps(inventories, indent=2) + "\n", encoding="utf-8"
    )
    # Round-robin commit index, then repository, then PR: cover recent PRs
    # across all repositories before consuming deeper multi-commit histories.
    samples = {
        name: data["sample"][: args.sample_prs] for name, data in inventories.items()
    }
    github_repos = dict(_GITHUB_REPOS)
    github_repos.update(value.split("=", 1) for value in args.github_repo)
    cache_path = args.ci_cache or out_dir / "check-runs-cache.json"
    ci_rows, selected = _load_ci_inventory(samples, github_repos, cache_path)
    groups = _pr_groups(samples)
    for name, rows in selected.items():
        for row in rows:
            row["pr_group"] = groups[row["pr"]]
    ci_report = {"seed": 20261122, "repositories": {}, "commits": {}}
    for name in samples:
        summary = ci_rows[f"{name}:summary"]
        selected_shas = {row["head"] for row in selected[name]}
        ci_report["repositories"][name] = {
            **summary,
            "selected_commits": [
                {
                    "head": row["head"],
                    "pr_numbers": row["pr_numbers"],
                    "commit_indices": row["pr_commit_indices"],
                    "ci_status": row["ci"]["status"],
                    "ci_conclusions": row["ci"].get("conclusions", []),
                    "selection_reasons": row.get("selection_reasons", []),
                }
                for row in selected[name]
            ],
        }
        for row in _unique_commit_rows(samples)[name]:
            ci_report["commits"][f"{name}:{row['head']}"] = {
                "pr_numbers": row["pr_numbers"],
                "commit_indices": row["pr_commit_indices"],
                "ci": ci_rows[f"{name}:{row['head']}"]["ci"],
                "selected_for_pytest": row["head"] in selected_shas,
            }
    (out_dir / "ci-inventory.json").write_text(
        json.dumps(ci_report, indent=2) + "\n", encoding="utf-8"
    )
    if args.inventory_only:
        prior_summary_path = out_dir / "summary.json"
        prior_summary = (
            json.loads(prior_summary_path.read_text(encoding="utf-8"))
            if prior_summary_path.is_file()
            else {}
        )
        for name, summary in ci_report["repositories"].items():
            prior = prior_summary.get("repositories", {}).get(name, {})
            median = prior.get("median_pair_s")
            p90 = prior.get("p90_pair_s")
            summary["forecast_median_s"] = (
                round(summary["selected_for_pytest"] * median, 1)
                if median is not None
                else None
            )
            summary["forecast_p90_s"] = (
                round(summary["selected_for_pytest"] * p90, 1)
                if p90 is not None
                else None
            )
        ci_report["elapsed_s"] = round(time.monotonic() - batch_started, 2)
        ci_report["inventory_elapsed_s"] = round(inventory_finished - batch_started, 2)
        (out_dir / "ci-inventory.json").write_text(
            json.dumps(ci_report, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(ci_report["repositories"], indent=2))
        return 0
    results = []
    pair_file = out_dir / "pairs.jsonl"
    if pair_file.is_file() and pair_file.stat().st_size:
        parser.error(
            "--out-dir already contains pairs.jsonl; choose a fresh output directory for a pytest run"
        )
    for name, repo in repos.items():
        for row in selected[name]:
            if deadline - time.monotonic() < 1:
                break
            batch_remaining = max(0, int(deadline - time.monotonic()))
            remaining = min(args.pair_timeout_s, batch_remaining)
            result = _run_pair(repo, row, work_root, python, remaining)
            _promote_pair_timeout(result, remaining, args.pair_timeout_s)
            results.append(result)
            with pair_file.open("a", encoding="utf-8") as out:
                out.write(json.dumps(result, sort_keys=True) + "\n")
            print(
                f"{name} PRs#{','.join(result['pr_numbers'])} {row['head'][:10]}: {result['status']} {result['wall_seconds']}s positive={result['positive']}",
                flush=True,
            )
    summary = {
        "python": "3.13",
        "main_ref": args.main_ref,
        "sample_prs": args.sample_prs,
        "pair_timeout_s": args.pair_timeout_s,
        "batch_timeout_s": min(args.batch_timeout_s, 1800),
        "elapsed_s": round(time.monotonic() - batch_started, 2),
        "inventory_elapsed_s": round(inventory_finished - batch_started, 2),
        "repositories": {},
    }
    for name, inventory in inventories.items():
        own = [row for row in results if row["repo"] == name]
        times = [row["wall_seconds"] for row in own if row.get("tests_started")]
        sorted_times = sorted(times)
        sample = samples[name]
        summary["repositories"][name] = {
            "pr_refs_seen": inventory["pr_refs_seen"],
            "pr_with_at_least_two_commits": inventory["pr_with_at_least_two_commits"],
            "sample_prs": len(sample),
            "sample_intermediate_commits": sum(len(pr["commits"]) for pr in sample),
            "unique_intermediate_head_shas": len(
                _unique_commit_rows({name: sample})[name]
            ),
            "ci_selected_heads": ci_report["repositories"][name]["selected_for_pytest"],
            "attempted_pairs": len(own),
            "started_pairs": sum(row["tests_started"] for row in own),
            "completed_pairs": sum(row["status"] == "completed" for row in own),
            "positive_pairs": sum(
                row["status"] == "completed" and row["positive"] for row in own
            ),
            "unique_positive_changes": len(
                {row["unique_change_id"] for row in own if row.get("unique_change_id")}
            ),
            "test_modified_positive_pairs": sum(
                row.get("test_modified_in_head") is True
                for row in own
                if row.get("positive")
            ),
            "positive_prs": sorted(
                {
                    pr
                    for row in own
                    if row["status"] == "completed" and row["positive"]
                    for pr in row.get("pr_numbers", [row["pr"]])
                },
                key=int,
            ),
            "positive_pr_groups": sorted(
                {
                    ",".join(row.get("pr_group", row.get("pr_numbers", [row["pr"]])))
                    for row in own
                    if row["status"] == "completed" and row["positive"]
                }
            ),
            "env_incompatible": sum(row["status"] == "env_incompatible" for row in own),
            "git_objects_missing": sum(
                row["status"] == "git_objects_missing" for row in own
            ),
            "timeout": sum(row["status"] == "timeout" for row in own),
            "batch_deadline": sum(row["status"] == "batch_deadline" for row in own),
            "median_pair_s": statistics.median(times) if times else None,
            "p90_pair_s": sorted_times[math.ceil(0.9 * len(sorted_times)) - 1]
            if sorted_times
            else None,
            "positive_test_ids": sum(
                len(row.get("positive_test_ids", [])) for row in own
            ),
        }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
