#!/usr/bin/env python3
"""Bootstrap synthetic new_fail labels via AST mutants (tasks 3–4)."""

from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from backfill import install_project, resolve_plugin, run  # noqa: E402
from mutate.ast_ops import (  # noqa: E402
    MutationSite,
    apply_mutation,
    dedup_by_diff,
    find_sites,
    order_sites,
    sample_sites,
)
from mutate.git_mutant import (  # noqa: E402
    collect_git_sha,
    create_mutant_commit,
    delete_mutant_ref,
    first_parent,
)
from mutate.hunks import is_source_py, parse_hunks, show_blob  # noqa: E402
from mutate.run_mutant import run_mutant  # noqa: E402
from mutate.sample_commits import sample_commits  # noqa: E402


@dataclass
class BootstrapStats:
    n_eligible_C: int = 0
    n_commits_sampled: int = 0
    n_mutants_attempted: int = 0
    n_invalid: int = 0
    n_timeout: int = 0
    n_equivalent: int = 0
    n_flaky_rerun: int = 0
    n_killed: int = 0  # ≥1 new_fail vs C
    n_collection_error: int = 0
    n_high_fail_rate: int = 0  # >50% tests failed (shown separately)
    attempts_by_operator: dict[str, int] = field(default_factory=dict)
    kill_by_operator: dict[str, int] = field(default_factory=dict)
    n_collect_git_failed: int = 0
    n_collect_junit_failed: int = 0


def _commit_seed(base_seed: int, sha: str) -> int:
    return base_seed ^ int(sha[:12], 16)


def _candidate_mutants(
    repo: Path,
    parent_sha: str,
    commit_sha: str,
    seed: int,
    k: int,
) -> tuple[list[tuple[str, MutationSite, str]], int]:
    """Compile-valid mutants with distinct (operator, node) and dedup_by_diff."""
    hunks = parse_hunks(repo, parent_sha, commit_sha)
    n_invalid = 0
    raw: list[tuple[str, MutationSite, str]] = []

    for h in hunks:
        path = h["path"]
        if h["is_binary"] or not is_source_py(path):
            continue
        src = show_blob(repo, commit_sha, path)
        if src is None:
            continue
        touched = set(h["new_lines"])
        sites = find_sites(src, touched)
        for site in order_sites(sites, seed):
            out = apply_mutation(src, site)
            if out is None:
                n_invalid += 1
                continue
            raw.append((path, site, out))

    by_path: dict[str, list[tuple[MutationSite, str]]] = defaultdict(list)
    for path, site, text in raw:
        by_path[path].append((site, text))

    deduped: list[tuple[str, MutationSite, str]] = []
    seen_ops: set[tuple[str, str]] = set()
    for path in sorted(by_path):
        for site, text in dedup_by_diff(by_path[path]):
            key = (site.op_id, site.node_key)
            if key in seen_ops:
                continue
            seen_ops.add(key)
            deduped.append((path, site, text))

    sites_only = [site for _, site, _ in deduped]
    picked = sample_sites(sites_only, min(k, len(sites_only)), seed)
    pick_set = {(s.op_id, s.node_key) for s in picked}
    chosen = [row for row in deduped if (row[1].op_id, row[1].node_key) in pick_set]
    chosen.sort(key=lambda r: (r[1].op_id, r[1].lineno, r[1].col_offset, r[0]))
    return chosen, n_invalid


def run_bootstrap(
    repo: Path,
    db: Path,
    *,
    commits_n: int,
    mutants_k: int,
    seed: int,
    timeout_sec: int | None,
    python: Path,
    plugin: Path | None,
    pytest_args: str,
    work_root: Path | None,
) -> BootstrapStats:
    stats = BootstrapStats()
    sample = sample_commits(db, commits_n, seed)
    stats.n_eligible_C = sample.n_eligible_C
    stats.n_commits_sampled = len(sample.sampled)

    if not sample.sampled:
        return stats

    plugin_path = resolve_plugin(plugin)
    temp_root = work_root is None
    owning = work_root or Path(tempfile.mkdtemp(prefix="tp-mutate-"))
    venv = owning / "venv"
    run_root = owning / "runs"
    run_root.mkdir(parents=True, exist_ok=True)

    try:
        run([str(python), "-m", "venv", str(venv)])
        pip = venv / "bin" / "pip"
        pytest = venv / "bin" / "pytest"
        vpy = venv / "bin" / "python"
        run([str(pip), "install", "-U", "pip", "wheel"], check=False)
        run([str(pip), "install", "-e", str(plugin_path)], check=False)
        run([str(pip), "install", "pytest>=8,<10"], check=False)

        extra = pytest_args.split() if pytest_args else ["-q"]

        for item in sample.sampled:
            c_sha = item.sha
            parent = first_parent(repo, c_sha)
            if not parent:
                continue
            c_seed = _commit_seed(seed, c_sha)
            chosen, n_inv = _candidate_mutants(
                repo, parent, c_sha, c_seed, mutants_k
            )
            stats.n_invalid += n_inv
            if not chosen:
                continue

            for i, (path, site, new_src) in enumerate(chosen):
                stats.n_mutants_attempted += 1
                stats.attempts_by_operator[site.op_id] = (
                    stats.attempts_by_operator.get(site.op_id, 0) + 1
                )
                try:
                    m_sha = create_mutant_commit(
                        repo,
                        c_sha,
                        i,
                        {path: new_src},
                    )
                except Exception as exc:  # noqa: BLE001
                    print(
                        f"commit mutant failed C={c_sha[:12]} i={i}: {exc}",
                        file=sys.stderr,
                    )
                    stats.n_invalid += 1
                    continue

                cg = collect_git_sha(repo, db, m_sha)
                if not cg.ok:
                    stats.n_collect_git_failed += 1
                    print(cg.note or cg.stderr, file=sys.stderr)

                result = run_mutant(
                    repo,
                    db,
                    m_sha,
                    parent_sha=c_sha,
                    pytest_bin=pytest,
                    python_bin=vpy,
                    work_dir=run_root,
                    duration_sec=item.duration_sec,
                    timeout_override=timeout_sec,
                    pytest_extra_args=extra,
                    pip_bin=pip,
                    install_project_cb=install_project,
                )

                if result.timed_out:
                    stats.n_timeout += 1
                    delete_mutant_ref(repo, c_sha, i)
                    continue

                stats.n_flaky_rerun += result.n_flaky_rerun
                if not result.collect_ok:
                    stats.n_collect_junit_failed += 1
                if result.collection_error:
                    stats.n_collection_error += 1
                if result.high_fail_rate:
                    stats.n_high_fail_rate += 1

                if result.killed:
                    stats.n_killed += 1
                    stats.kill_by_operator[site.op_id] = (
                        stats.kill_by_operator.get(site.op_id, 0) + 1
                    )
                elif result.n_new_fail == 0 and not result.collection_error:
                    stats.n_equivalent += 1
    finally:
        if temp_root:
            shutil.rmtree(owning, ignore_errors=True)

    return stats


def stats_to_report(stats: BootstrapStats) -> dict:
    kill_by_op = dict(sorted(stats.kill_by_operator.items()))
    attempts = dict(sorted(stats.attempts_by_operator.items()))
    return {
        "n_eligible_C": stats.n_eligible_C,
        "n_commits_sampled": stats.n_commits_sampled,
        "n_mutants_attempted": stats.n_mutants_attempted,
        "n_invalid": stats.n_invalid,
        "n_timeout": stats.n_timeout,
        "n_equivalent": stats.n_equivalent,
        "n_flaky_rerun": stats.n_flaky_rerun,
        "n_killed": stats.n_killed,
        "n_collection_error": stats.n_collection_error,
        "n_high_fail_rate": stats.n_high_fail_rate,
        "attempts_by_operator": attempts,
        "kill_by_operator": kill_by_op,
        "kill_rate_by_operator": {
            op: (stats.kill_by_operator.get(op, 0) / max(stats.attempts_by_operator.get(op, 0), 1))
            for op in sorted(
                set(stats.attempts_by_operator) | set(stats.kill_by_operator)
            )
        },
        "n_collect_git_failed": stats.n_collect_git_failed,
        "n_collect_junit_failed": stats.n_collect_junit_failed,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="AST mutant bootstrap for TestPilot")
    ap.add_argument("--repo", type=Path, required=True)
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--commits", type=int, required=True, dest="commits_n")
    ap.add_argument("--mutants-per-commit", type=int, required=True)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--timeout-sec", type=int, default=None)
    ap.add_argument(
        "--python",
        type=Path,
        default=None,
        help="interpreter for bootstrap venv (default: current)",
    )
    ap.add_argument("--plugin", type=Path, default=None, help="pytest-testpilot path")
    ap.add_argument("--pytest-args", default="-q")
    ap.add_argument("--json", action="store_true", help="print stats JSON on stdout")
    args = ap.parse_args()

    py = Path(args.python or sys.executable).resolve()
    repo = args.repo.resolve()
    db = args.db.resolve()

    stats = run_bootstrap(
        repo,
        db,
        commits_n=args.commits_n,
        mutants_k=args.mutants_per_commit,
        seed=args.seed,
        timeout_sec=args.timeout_sec,
        python=py,
        plugin=args.plugin,
        pytest_args=args.pytest_args,
        work_root=None,
    )

    report = stats_to_report(stats)

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for key, val in report.items():
            print(f"{key}={val}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
