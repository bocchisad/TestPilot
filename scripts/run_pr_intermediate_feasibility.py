#!/usr/bin/env python3
"""Inventory and run a bounded PR-intermediate feasibility sample."""

from __future__ import annotations

import argparse
import json
import math
import shutil
import statistics
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path

from run_real_pair_calibration import _install, _junit, _run


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
        "pr_commit_index": row["commit_index"],
        "pr_commit_count": row["commit_count"],
        "base": row["base"],
        "head": row["head"],
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
                    result.update(
                        status="env_incompatible", reason=f"pytest_no_junit:{label}"
                    )
                    return result
                outcomes[f"{label}{attempt}"] = _junit(xml)
        base_ids = set(outcomes["base1"]) & set(outcomes["base2"])
        shared = base_ids & set(outcomes["head1"]) & set(outcomes["head2"])
        positive_ids = sorted(
            test
            for test in shared
            if outcomes["base1"][test] == outcomes["base2"][test] == "pass"
            and outcomes["head1"][test] == outcomes["head2"][test] == "fail"
        )
        result.update(
            status="completed",
            base_test_ids=len(base_ids),
            shared_base_test_ids=len(shared),
            positive=bool(positive_ids),
            positive_test_ids=positive_ids,
            test_id_policy="test IDs present in base; all four runs shared; repeated pass on base and fail on head",
        )
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
    rounds = max(
        (
            max((len(pr["commits"]) for pr in prs), default=0)
            for prs in samples.values()
        ),
        default=0,
    )
    results = []
    pair_file = out_dir / "pairs.jsonl"
    for index in range(rounds):
        for name, repo in repos.items():
            for pr in samples[name]:
                if index >= len(pr["commits"]):
                    continue
                if deadline - time.monotonic() < 1:
                    break
                row = {
                    **pr,
                    "repo": name,
                    "head": pr["commits"][index],
                    "commit_index": index + 1,
                }
                batch_remaining = max(0, int(deadline - time.monotonic()))
                remaining = min(args.pair_timeout_s, batch_remaining)
                result = _run_pair(repo, row, work_root, python, remaining)
                if result["status"] == "timeout" and remaining < args.pair_timeout_s:
                    result.update(
                        status="batch_deadline",
                        reason="batch_budget_exhausted_before_pair_timeout",
                    )
                results.append(result)
                with pair_file.open("a", encoding="utf-8") as out:
                    out.write(json.dumps(result, sort_keys=True) + "\n")
                print(
                    f"{name} PR#{pr['pr']} {row['head'][:10]}: {result['status']} {result['wall_seconds']}s positive={result['positive']}",
                    flush=True,
                )
        else:
            continue
        break
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
            "attempted_pairs": len(own),
            "started_pairs": sum(row["tests_started"] for row in own),
            "completed_pairs": sum(row["status"] == "completed" for row in own),
            "positive_pairs": sum(
                row["status"] == "completed" and row["positive"] for row in own
            ),
            "positive_prs": sorted(
                {
                    row["pr"]
                    for row in own
                    if row["status"] == "completed" and row["positive"]
                },
                key=int,
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
