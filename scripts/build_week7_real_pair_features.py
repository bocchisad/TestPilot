#!/usr/bin/env python3
"""Build paired real-case feature datasets from the authorized PR run artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from run_real_pair_calibration import _changed_test_functions  # noqa: E402


def normalize_pytest_id(classname: str, name: str) -> str:
    """Match `junit::normalize_test_id` for pytest module/class IDs."""
    if "::" in classname or "/" in classname or classname.endswith(".py"):
        return f"{classname}::{name}" if classname else name
    parts = classname.split(".") if classname else []
    if not parts:
        return name
    last = parts[-1]
    is_class = (
        bool(last)
        and last[0].isupper()
        and all(ch.isascii() and (ch.isalnum() or ch == "_") for ch in last)
    )
    if is_class and len(parts) > 1:
        return f"{'/'.join(parts[:-1])}.py::{last}::{name}"
    return f"{'/'.join(parts)}.py::{name}"


def parse_junit(path: Path) -> dict[str, str]:
    root = ET.parse(path).getroot()
    out: dict[str, str] = {}
    for case in root.iter("testcase"):
        test_id = normalize_pytest_id(case.get("classname", ""), case.get("name", ""))
        if case.find("failure") is not None or case.find("error") is not None:
            out[test_id] = "fail"
        elif case.find("skipped") is not None:
            out[test_id] = "skip"
        else:
            out[test_id] = "pass"
    return out


def stable_new_fail_labels(runs: dict[str, dict[str, str]]) -> dict[str, int]:
    """Label only complete, repeat-stable IDs; new failure means pass/pass→fail/fail."""
    required = ("base1", "base2", "head1", "head2")
    if any(name not in runs for name in required):
        raise ValueError("all four repeat outcome maps are required")
    ids = set.intersection(*(set(runs[name]) for name in required))
    out: dict[str, int] = {}
    for test_id in ids:
        outcomes = [runs[name][test_id] for name in required]
        if any(value not in {"pass", "fail"} for value in outcomes):
            continue
        if outcomes[0] != outcomes[1] or outcomes[2] != outcomes[3]:
            continue
        out[test_id] = int(outcomes[0] == "pass" and outcomes[2] == "fail")
    return out


def is_modified_test_function(test_id: str, changed_functions: set[str]) -> bool:
    """Compare normalized file-style feature IDs to dotted JUnit/AST function IDs."""
    parts = test_id.split("::")
    if len(parts) < 2:
        return False
    module = parts[0].removesuffix(".py").replace("/", ".")
    function = parts[-1].split("[", 1)[0]
    return f"{module}::{function}" in changed_functions


def _is_test_path(path: str) -> bool:
    lower = path.lower()
    return "/tests/" in f"/{lower}" or lower.startswith("tests/") or "test" in Path(path).name.lower()


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _pair_outcomes(pair_dir: Path) -> dict[str, dict[str, str]]:
    return {
        "base1": parse_junit(pair_dir / "base-1.xml"),
        "base2": parse_junit(pair_dir / "base-2.xml"),
        "head1": parse_junit(pair_dir / "head-1.xml"),
        "head2": parse_junit(pair_dir / "head-2.xml"),
    }


def _feature_rows(
    *, binary: Path, repo_path: Path, db_path: Path, base: str, head: str
) -> list[dict[str, Any]]:
    command = [
        str(binary),
        "features",
        "show",
        "--repo",
        str(repo_path),
        "--db",
        str(db_path),
        "--base",
        base,
        "--head",
        head,
        "--format",
        "json",
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    rows = json.loads(result.stdout)
    if not isinstance(rows, list):
        raise ValueError(f"features show returned non-list for {head}")
    return rows


def _pair_dir(root: Path, row: dict[str, Any]) -> Path:
    token_prefix = f"{row['repo']}-pr"
    for path in (root / "work" / "pairs").iterdir():
        if path.name.startswith(token_prefix) and row["head"][:10] in path.name:
            return path
    raise FileNotFoundError(f"pair logs missing for {row['repo']} {row['head']}")


def _exclude_audited_groups(
    rows: list[dict[str, Any]], excluded_groups: set[tuple[str, tuple[str, ...]]]
) -> list[dict[str, Any]]:
    """Exclude every intermediate head belonging to an audited PR group."""
    kept = []
    for row in rows:
        group = tuple(
            sorted(set(row.get("pr_group") or row.get("pr_numbers") or [row.get("pr", "")]))
        )
        if (str(row["repo"]), group) not in excluded_groups:
            kept.append(row)
    return kept


def _load_schema(path: Path) -> tuple[list[str], int]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    return [col["name"] for col in doc["columns"]], int(doc["features_version"])


def _write_parquet(path: Path, rows: list[dict[str, Any]], columns: list[str], version: int) -> None:
    table = pa.Table.from_pylist([{name: row[name] for name in columns} for row in rows])
    metadata = dict(table.schema.metadata or {})
    metadata[b"features_version"] = str(version).encode()
    table = table.replace_schema_metadata(metadata)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)


def build(args: argparse.Namespace) -> dict[str, Any]:
    root = args.root.resolve()
    run_dir = args.run_dir.resolve()
    raw_path = run_dir / "pairs.jsonl"
    raw_rows = [json.loads(line) for line in raw_path.read_text().splitlines() if line.strip()]
    # Keep the final result for each unique head SHA; the raw log contains retries.
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for row in raw_rows:
        latest[(row["repo"], row["head"])] = row
    completed = [row for row in latest.values() if row.get("status") == "completed"]
    excluded_groups = set()
    for value in args.exclude_pr_group:
        repo, sep, raw_ids = value.partition(":")
        if not sep or not repo or not raw_ids:
            raise ValueError(f"invalid --exclude-pr-group value: {value!r}")
        excluded_groups.add((repo, tuple(sorted(set(raw_ids.split(","))))))
    completed = _exclude_audited_groups(completed, excluded_groups)
    completed.sort(key=lambda row: (row["repo"], row.get("head", "")))
    repo_paths = {
        name: (root / args.repo_path_template.format(repo=name)).resolve()
        for name in ("click", "jinja", "attrs")
    }
    db_paths = {
        name: (root / args.db_template.format(repo=name)).resolve()
        for name in ("click", "jinja", "attrs")
    }
    binary = args.binary.resolve()
    columns, version = _load_schema(args.schema.resolve())
    output_rows: dict[str, list[dict[str, Any]]] = {
        "all": [],
        "without_changed_functions": [],
    }
    errors: list[dict[str, str]] = []
    change_order: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for row in completed:
        repo = row["repo"]
        repo_path = repo_paths[repo]
        head = row["head"]
        base = row["base"]
        try:
            pair_dir = _pair_dir(run_dir, row)
            outcomes = _pair_outcomes(pair_dir)
            labels = stable_new_fail_labels(outcomes)
            features = _feature_rows(
                binary=binary,
                repo_path=repo_path,
                db_path=db_paths[repo],
                base=base,
                head=head,
            )
            feature_by_id = {feature["test"]: feature for feature in features}
            stable_ids = set(labels)
            missing = stable_ids - set(feature_by_id)
            if missing:
                raise ValueError(f"{len(missing)} stable JUnit IDs absent from features show")
            parents = _git(repo_path, "rev-list", "--parents", "-n", "1", head).split()
            diff_parent = parents[1] if len(parents) > 1 else base
            test_paths = [path for path in row.get("changed_paths", []) if _is_test_path(path)]
            changed_functions = _changed_test_functions(
                repo_path,
                {"commit": head, "diff_parent": diff_parent, "test_paths": test_paths},
                time.monotonic() + 60,
            )
            commit_time = int(_git(repo_path, "show", "-s", "--format=%ct", head))
            change_order[repo].append((commit_time, head))
            pr_group = sorted(set(row.get("pr_group") or row.get("pr_numbers") or [row.get("pr", "")]))
            pair_id = hashlib.sha256((repo + "\0" + "|".join(pr_group)).encode()).hexdigest()
            for test_id in sorted(stable_ids):
                feature = dict(feature_by_id[test_id])
                modified_function = is_modified_test_function(test_id, changed_functions)
                feature.update(
                    repo=repo,
                    change=head,
                    parent=base,
                    test=test_id,
                    label=labels[test_id],
                    flaky=0,
                    synthetic=0,
                    base_change=pair_id,
                    time=commit_time,
                )
                feature["_modified_function"] = modified_function
                output_rows["all"].append(feature)
                if not modified_function:
                    output_rows["without_changed_functions"].append(feature)
        except Exception as exc:  # record exclusions; do not silently turn them into negatives
            errors.append({"repo": repo, "head": head, "error": f"{type(exc).__name__}: {exc}"})
    for repo, changes in change_order.items():
        # Same timestamp commits from a stacked PR retain deterministic SHA ordering.
        for idx, (_ts, head) in enumerate(sorted(set(changes)), start=1):
            for variant in output_rows.values():
                for item in variant:
                    if item["repo"] == repo and item["change"] == head:
                        item["commit_idx"] = idx
    written = {}
    for variant, rows in output_rows.items():
        cleaned = [{key: value for key, value in row.items() if not key.startswith("_")} for row in rows]
        path = args.out_dir / f"real-pair-features-{variant}.parquet"
        _write_parquet(path, cleaned, columns, version)
        written[variant] = {"path": str(path.resolve()), "rows": len(cleaned)}
    summary = {
        "completed_unique_heads": len(completed),
        "excluded_pr_groups": [
            {"repo": repo, "pr_group": list(group)}
            for repo, group in sorted(excluded_groups)
        ],
        "feature_errors": len(errors),
        "feature_error_path": str((args.out_dir / "feature-errors.jsonl").resolve()),
        "outputs": written,
        "label_definition": "pass/pass on base and fail/fail on head; stable shared test IDs only",
        "without_changed_functions": "drop all stable test IDs in test functions modified by head commit",
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "feature-errors.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in errors)
    )
    (args.out_dir / "feature-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--run-dir", type=Path, default=Path(".cache/measurements/week7/pr-intermediate/full-history-run"))
    parser.add_argument("--repo-path-template", default=".cache/measurements/week7/pr-intermediate/feature-worktrees/{repo}")
    parser.add_argument("--db-template", default=".cache/measurements/week7/pr-intermediate/feature-db/{repo}.sqlite")
    parser.add_argument("--binary", type=Path, default=Path("target/debug/testpilot"))
    parser.add_argument("--schema", type=Path, default=Path("features_schema.json"))
    parser.add_argument("--out-dir", type=Path, default=Path(".cache/measurements/week7/pr-intermediate/real-features"))
    parser.add_argument(
        "--exclude-pr-group",
        action="append",
        default=[],
        help="repo:pr[,pr] group excluded by semantic audit (repeatable)",
    )
    args = parser.parse_args()
    summary = build(args)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
