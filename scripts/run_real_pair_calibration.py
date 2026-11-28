#!/usr/bin/env python3
"""Run a bounded first calibration batch for screened real-pair leads."""

from __future__ import annotations

import argparse
import ast
import json
import math
import re
import shutil
import statistics
import subprocess
import time
import tomllib
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from pathlib import Path

_REQ_FILES = (
    "requirements-test.txt",
    "requirements-tests.txt",
    "requirements/test.txt",
    "requirements/tests.txt",
    "tests/requirements.txt",
    "test-requirements.txt",
)


def _run(
    args: list[str], *, cwd: Path, deadline: float, input_bytes: bytes | None = None
) -> subprocess.CompletedProcess:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise subprocess.TimeoutExpired(args, 0)
    return subprocess.run(
        args,
        cwd=cwd,
        input=input_bytes,
        capture_output=True,
        timeout=min(remaining, 300),
        check=False,
    )


def _git(repo: Path, *args: str, deadline: float) -> str:
    result = _run(["git", *args], cwd=repo, deadline=deadline)
    if result.returncode:
        raise RuntimeError(
            (result.stderr or result.stdout).decode(errors="replace")[-2500:]
        )
    return result.stdout.decode(errors="replace").strip()


def _install(
    python: Path, tree: Path, deadline: float, *, full: bool
) -> tuple[bool, str]:
    logs: list[str] = []

    def pip(*args: str) -> bool:
        result = _run([str(python), "-m", "pip", *args], cwd=tree, deadline=deadline)
        logs.append(
            f"$ pip {' '.join(args)}\nrc={result.returncode}\n"
            f"{(result.stdout or b'').decode(errors='replace')[-800:]}"
            f"{(result.stderr or b'').decode(errors='replace')[-800:]}"
        )
        return result.returncode == 0

    if full:
        metadata = tree / "pyproject.toml"
        project = (
            tomllib.loads(metadata.read_text(encoding="utf-8"))
            if metadata.is_file()
            else {}
        )
        has_package = metadata.is_file() or (tree / "setup.py").is_file()
        if has_package and not pip("install", "-e", ".") and not pip("install", "."):
            return False, "\n".join(logs)
        extras = set(
            ((project.get("project") or {}).get("optional-dependencies") or {})
        )
        for extra in ("test", "tests", "testing"):
            if extra in extras and not pip("install", "-e", f".[{extra}]"):
                return False, "\n".join(logs)
        groups = project.get("dependency-groups") or {}
        for group in ("test", "tests"):
            if group in groups and not pip("install", "--group", group):
                return False, "\n".join(logs)
        for name in _REQ_FILES:
            req = tree / name
            if req.is_file() and not pip("install", "-r", str(req)):
                return False, "\n".join(logs)
        if not pip("install", "pytest"):
            return False, "\n".join(logs)
    else:
        if (tree / "pyproject.toml").is_file() or (tree / "setup.py").is_file():
            if not pip("install", "-e", ".", "--no-deps"):
                return False, "\n".join(logs)
    return True, "\n".join(logs)


def _junit(path: Path) -> dict[str, str]:
    root = ET.parse(path).getroot()
    outcomes: dict[str, str] = {}
    for case in root.iter("testcase"):
        key = "::".join(
            part for part in (case.get("classname"), case.get("name")) if part
        )
        if case.find("failure") is not None or case.find("error") is not None:
            outcomes[key] = "fail"
        elif case.find("skipped") is not None:
            outcomes[key] = "skip"
        else:
            outcomes[key] = "pass"
    return outcomes


def _changed_test_functions(repo: Path, row: dict, deadline: float) -> set[str]:
    paths = [path for path in row["test_paths"] if path.endswith(".py")]
    if not paths:
        return set()
    result = _run(
        [
            "git",
            "diff",
            "--unified=0",
            row["diff_parent"],
            row["commit"],
            "--",
            *paths,
        ],
        cwd=repo,
        deadline=deadline,
    )
    if result.returncode:
        return set()
    changed: dict[str, set[int]] = {}
    current: str | None = None
    for line in result.stdout.decode(errors="replace").splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
            changed.setdefault(current, set())
        elif line.startswith("@@") and current:
            match = re.search(r"\+(\d+)(?:,(\d+))?", line)
            if match:
                start = int(match.group(1))
                count = int(match.group(2) or "1")
                changed[current].update(range(start, start + count))
    names: set[str] = set()
    for path, lines in changed.items():
        if not lines:
            continue
        content = _run(
            ["git", "show", f"{row['commit']}:{path}"], cwd=repo, deadline=deadline
        )
        if content.returncode:
            continue
        try:
            tree = ast.parse(content.stdout.decode("utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef)
            ) or not node.name.startswith("test_"):
                continue
            start = min(
                [node.lineno, *(decorator.lineno for decorator in node.decorator_list)]
            )
            end = node.end_lineno or node.lineno
            if any(start <= line <= end for line in lines):
                module = Path(path).with_suffix("").as_posix().replace("/", ".")
                names.add(f"{module}::{node.name}")
    return names


def run_pair(
    name: str, repo: Path, row: dict, root: Path, python: Path, timeout_s: int
) -> dict:
    started = time.monotonic()
    deadline = started + timeout_s
    token = f"{name}-{row['commit'][:12]}"
    worktree = root / "worktrees" / token
    venv = root / "venvs" / token
    logs_dir = root / "pairs" / token
    logs_dir.mkdir(parents=True, exist_ok=True)
    base = row["commit"]
    parent = row["diff_parent"]
    source_paths = row["package_source_paths"]
    result: dict = {
        "repo": name,
        "fix_commit": base,
        "base": base,
        "head": None,
        "status": "started",
        "wall_seconds": None,
        "positive": False,
        "tests_started": False,
        "positive_outside_new_test": False,
        "positive_test_ids": [],
        "log_dir": str(logs_dir),
    }
    try:
        if not source_paths:
            result.update(status="revert_conflict", reason="no_package_source_paths")
            return result
        worktree.parent.mkdir(parents=True, exist_ok=True)
        venv.parent.mkdir(parents=True, exist_ok=True)
        added_patch = _run(
            ["git", "diff", "--binary", parent, base, "--", *source_paths],
            cwd=repo,
            deadline=deadline,
        )
        if added_patch.returncode:
            result.update(
                status="git_objects_missing",
                reason="source_diff_unavailable",
                git_error=added_patch.stderr.decode(errors="replace")[-1000:],
            )
            return result
        add = _run(
            ["git", "worktree", "add", "--detach", str(worktree), base],
            cwd=repo,
            deadline=deadline,
        )
        if add.returncode:
            commit_check = _run(
                ["git", "cat-file", "-e", f"{base}^{{commit}}"],
                cwd=repo,
                deadline=deadline,
            )
            result.update(
                status=(
                    "worktree_setup_error"
                    if commit_check.returncode == 0
                    else "git_objects_missing"
                ),
                reason="worktree_setup_failed",
                git_error=add.stderr.decode(errors="replace")[-1000:],
            )
            return result
        create = _run(
            [str(python), "-m", "venv", str(venv)], cwd=repo, deadline=deadline
        )
        if create.returncode:
            result.update(status="env_incompatible", reason="venv_setup_failed")
            return result
        vp = venv / "bin" / "python"
        ok, log = _install(vp, worktree, deadline, full=True)
        (logs_dir / "install.log").write_text(log, encoding="utf-8")
        if not ok:
            result.update(status="env_incompatible", reason="base_install_failed")
            return result
        reverse = _run(
            ["git", "apply", "--reverse", "--3way"],
            cwd=worktree,
            deadline=deadline,
            input_bytes=added_patch.stdout,
        )
        if reverse.returncode:
            result.update(
                status="revert_conflict",
                reason="revert_conflict",
                revert_stderr=reverse.stderr.decode(errors="replace")[-1000:],
            )
            return result
        stage = _run(
            ["git", "add", "--", *source_paths], cwd=worktree, deadline=deadline
        )
        if stage.returncode:
            result.update(status="revert_conflict", reason="cannot_stage_head_tree")
            return result
        head_tree = _git(worktree, "write-tree", deadline=deadline)
        result.update(
            head=head_tree,
            head_tree=head_tree,
            reverted_paths=source_paths,
            retained_test_paths=row["test_paths"],
        )
        ok, log = _install(vp, worktree, deadline, full=False)
        with (logs_dir / "install.log").open("a", encoding="utf-8") as out:
            out.write("\nHEAD editable install:\n" + log)
        if not ok:
            result.update(status="env_incompatible", reason="head_build_install_failed")
            return result
        forward = _run(
            ["git", "apply", "--3way"],
            cwd=worktree,
            deadline=deadline,
            input_bytes=added_patch.stdout,
        )
        if forward.returncode:
            result.update(
                status="revert_conflict",
                reason="cannot_restore_fixed_source_for_preflight",
            )
            return result
        ok, log = _install(vp, worktree, deadline, full=False)
        with (logs_dir / "install.log").open("a", encoding="utf-8") as out:
            out.write("\nBase editable install:\n" + log)
        if not ok:
            result.update(status="env_incompatible", reason="base_build_install_failed")
            return result
        reverse = _run(
            ["git", "apply", "--reverse", "--3way"],
            cwd=worktree,
            deadline=deadline,
            input_bytes=added_patch.stdout,
        )
        if reverse.returncode:
            result.update(status="revert_conflict", reason="revert_conflict")
            return result
        new_tests = _changed_test_functions(repo, row, deadline)
        runs: list[dict[str, str]] = []
        for tree_label in ("head", "base"):
            for attempt in (1, 2):
                junit_path = logs_dir / f"{tree_label}-{attempt}.xml"
                command = [str(vp), "-m", "pytest", "-q", f"--junitxml={junit_path}"]
                try:
                    result["tests_started"] = True
                    completed = _run(command, cwd=worktree, deadline=deadline)
                except subprocess.TimeoutExpired:
                    result.update(status="timeout", reason="pair_timeout")
                    return result
                (logs_dir / f"{tree_label}-{attempt}.log").write_bytes(
                    completed.stdout + b"\n--- stderr ---\n" + completed.stderr
                )
                if not junit_path.is_file():
                    result.update(
                        status="env_incompatible", reason="pytest_no_junit_output"
                    )
                    return result
                runs.append(_junit(junit_path))
            if tree_label == "head":
                # Return to fixed code before the second pair of test passes.
                fixed = _run(
                    ["git", "apply", "--3way"],
                    cwd=worktree,
                    deadline=deadline,
                    input_bytes=added_patch.stdout,
                )
                if fixed.returncode:
                    result.update(
                        status="revert_conflict", reason="cannot_restore_fixed_tree"
                    )
                    return result
        head1, head2, base1, base2 = runs
        shared = set(head1) & set(head2) & set(base1) & set(base2)
        positives = sorted(
            test
            for test in shared
            if head1[test] == head2[test] == "fail"
            and base1[test] == base2[test] == "pass"
        )
        outside = []
        for test in positives:
            test_module, test_name = test.rsplit("::", 1)
            test_name = test_name.split("[", 1)[0]
            is_changed_test = any(
                test_name == function
                and (test_module == module or test_module.startswith(module + "."))
                for changed in new_tests
                for module, function in [changed.split("::", 1)]
            )
            if not is_changed_test:
                outside.append(test)
        result.update(
            status="completed",
            shared_test_count=len(shared),
            positive=bool(positives),
            positive_test_ids=positives,
            positive_outside_new_test=bool(outside),
            outside_new_test_ids=outside,
            new_test_functions=sorted(new_tests),
            test_id_intersection_policy="present in all four repeated base/head runs",
        )
    except subprocess.TimeoutExpired:
        result.update(status="timeout", reason="pair_timeout")
    except (OSError, RuntimeError, ValueError, ET.ParseError) as exc:
        result.update(status="env_incompatible", reason=f"setup_or_result_error: {exc}")
    finally:
        result["wall_seconds"] = round(time.monotonic() - started, 3)
        if worktree.exists():
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(worktree)],
                cwd=repo,
                capture_output=True,
            )
        shutil.rmtree(venv, ignore_errors=True)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", action="append", required=True, metavar="NAME=PATH")
    parser.add_argument("--candidate-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--python", default="python3.13")
    parser.add_argument("--per-repo-limit", type=int, default=15)
    parser.add_argument("--pair-timeout-s", type=int, default=300)
    parser.add_argument("--batch-timeout-s", type=int, default=1800)
    args = parser.parse_args(argv)
    repos = {}
    for value in args.repo:
        name, path = value.split("=", 1)
        repos[name] = Path(path).resolve()
    python = Path(shutil.which(args.python) or args.python).resolve()
    if not python.is_file():
        parser.error(f"Python interpreter not found: {args.python}")
    args.candidate_dir = args.candidate_dir.resolve()
    args.out_dir = args.out_dir.resolve()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    root = args.out_dir / "calibration-work"
    root.mkdir(exist_ok=True)
    deadline = time.monotonic() + min(args.batch_timeout_s, 1800)
    selected = {}
    for name in repos:
        path = args.candidate_dir / f"candidates-{name}.jsonl"
        rows = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        leads = [row for row in rows if row["has_source_and_test_changes"]]
        selected[name] = (len(rows), leads[: args.per_repo_limit], len(leads))
    results = []
    max_rows = max((len(data[1]) for data in selected.values()), default=0)
    for index in range(max_rows):
        for name, repo in repos.items():
            if index >= len(selected[name][1]):
                continue
            row = selected[name][1][index]
            if deadline - time.monotonic() < 1:
                continue
            cap = min(args.pair_timeout_s, int(deadline - time.monotonic()))
            result = run_pair(name, repo, row, root, python, cap)
            results.append(result)
            (args.out_dir / "calibration-pairs.jsonl").open(
                "a", encoding="utf-8"
            ).write(json.dumps(result, sort_keys=True) + "\n")
            print(
                f"{name} {row['commit'][:12]}: {result['status']} {result['wall_seconds']}s positive={result['positive']}",
                flush=True,
            )
    summary = {
        "python": str(python),
        "pair_timeout_s": args.pair_timeout_s,
        "batch_timeout_s": min(args.batch_timeout_s, 1800),
        "repositories": {},
    }
    for name in repos:
        selected_count, leads, total_leads = selected[name]
        own = [row for row in results if row["repo"] == name]
        durations = [
            row["wall_seconds"]
            for row in own
            if row.get("tests_started") and row.get("wall_seconds") is not None
        ]
        durations_sorted = sorted(durations)
        p90 = (
            durations_sorted[math.ceil(0.9 * len(durations_sorted)) - 1]
            if durations_sorted
            else None
        )
        summary["repositories"][name] = {
            "post_dedupe_leads": selected_count,
            "source_and_test_leads": total_leads,
            "selected_for_calibration": len(leads),
            "started": sum(row["tests_started"] for row in own),
            "completed": sum(row["status"] == "completed" for row in own),
            "preflight_only": sum(not row["tests_started"] for row in own),
            "env_incompatible": sum(row["status"] == "env_incompatible" for row in own),
            "revert_conflict": sum(row["status"] == "revert_conflict" for row in own),
            "git_objects_missing": sum(
                row["status"] == "git_objects_missing" for row in own
            ),
            "worktree_setup_error": sum(
                row["status"] == "worktree_setup_error" for row in own
            ),
            "timeout": sum(row["status"] == "timeout" for row in own),
            "positive": sum(
                row["status"] == "completed" and row["positive"] for row in own
            ),
            "positive_outside_new_test": sum(
                row["status"] == "completed" and row["positive_outside_new_test"]
                for row in own
            ),
            "median_pair_s": statistics.median(durations) if durations else None,
            "p90_pair_s": p90,
            "forecast_remaining_at_median_s": round(
                (total_leads - len(own)) * statistics.median(durations), 1
            )
            if durations
            else None,
        }
    summary["elapsed_s"] = round(
        min(args.batch_timeout_s, 1800) - max(0, deadline - time.monotonic()), 2
    )
    (args.out_dir / "calibration-summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
