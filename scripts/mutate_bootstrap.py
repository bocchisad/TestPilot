#!/usr/bin/env python3
"""Bootstrap synthetic new_fail labels via AST mutants (tasks 3–4)."""

from __future__ import annotations

import argparse
import json
import os
import signal
import shutil
import sys
import tempfile
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    first_parent,
)
from mutate.hunks import is_source_py, parse_hunks, show_blob  # noqa: E402
from mutate.run_mutant import (  # noqa: E402
    kill_active_pytest_groups,
    run_mutant,
    synthetic_run_exists,
)
from mutate.sample_commits import sample_commits  # noqa: E402

_STOP = threading.Event()
_HARD_STOP = threading.Event()  # SIGINT/SIGTERM: kill children now
_PAIR_IO_LOCK = threading.Lock()


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
    n_skipped_completed: int = 0
    interrupted: bool = False
    completed_pairs: list[str] = field(default_factory=list)


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


def _pair_key(c_sha: str, i: int) -> str:
    return f"{c_sha}:{i}"


def _append_pair(pairs_out: Path | None, pair: str, m_sha: str) -> None:
    if pairs_out is None:
        return
    pairs_out.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"pair": pair, "m_sha": m_sha}) + "\n"
    with _PAIR_IO_LOCK:
        with pairs_out.open("a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())


_PAUSE_ENV = "TESTPILOT_TEST_PAUSE_AFTER_PAIRS"
_PAUSE_MARKER_NAME = "TESTPILOT_PAUSE_AFTER_PAIRS"


def _pause_marker_path(repo: Path) -> Path:
    return repo / ".testpilot" / "bootstrap" / _PAUSE_MARKER_NAME


def maybe_test_pause_after_pairs(repo: Path, n_completed: int) -> None:
    """Test-only: after N completed pairs, write a marker and sleep until removed/SIGINT.

    No-op unless ``TESTPILOT_TEST_PAUSE_AFTER_PAIRS`` is set to a positive int.

    When the marker is removed (e2e handshake), set ``_STOP``/``_HARD_STOP`` so
    mutate exits 130 without relying on SIGINT reaching the Rust parent — ctrlc
    does not reliably fire for background processes on CI runners.
    """
    raw = os.environ.get(_PAUSE_ENV)
    if raw is None or raw.strip() == "":
        return
    try:
        n = int(raw)
    except ValueError:
        return
    if n <= 0 or n_completed < n:
        return
    marker = _pause_marker_path(repo)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(f"paused after {n_completed} pairs\n", encoding="utf-8")
    # Sleep until marker deleted (tests) or process interrupted.
    while marker.is_file() and not _STOP.is_set() and not _HARD_STOP.is_set():
        time.sleep(0.05)
    if not marker.is_file():
        _STOP.set()
        _HARD_STOP.set()


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
    jobs: int = 1,
    completed_pairs: set[str] | None = None,
    pairs_out: Path | None = None,
    deadline_unix: float | None = None,
) -> BootstrapStats:
    stats = BootstrapStats()
    done = set(completed_pairs or ())
    sample = sample_commits(db, commits_n, seed, repo=repo)
    stats.n_eligible_C = sample.n_eligible_C
    stats.n_commits_sampled = len(sample.sampled)

    if not sample.sampled:
        return stats

    plugin_path = resolve_plugin(plugin)
    temp_root = work_root is None
    if work_root is None:
        boot = repo / ".testpilot" / "bootstrap"
        boot.mkdir(parents=True, exist_ok=True)
        owning = Path(tempfile.mkdtemp(prefix="wt-mutate-", dir=str(boot)))
    else:
        owning = work_root
        owning.mkdir(parents=True, exist_ok=True)
    venv = owning / "venv"
    run_root = owning / "runs"
    run_root.mkdir(parents=True, exist_ok=True)

    jobs = max(1, min(int(jobs), 4))
    git_lock = threading.Lock()
    db_lock = threading.Lock()
    stats_lock = threading.Lock()

    def _should_stop() -> bool:
        if _STOP.is_set() or _HARD_STOP.is_set():
            return True
        if deadline_unix is not None and time.time() >= deadline_unix:
            return True
        return False

    def _budget_hit() -> bool:
        return deadline_unix is not None and time.time() >= deadline_unix

    try:
        run([str(python), "-m", "venv", str(venv)])
        pip = venv / "bin" / "pip"
        pytest = venv / "bin" / "pytest"
        vpy = venv / "bin" / "python"
        run([str(pip), "install", "-U", "pip", "wheel"], check=False)
        run([str(pip), "install", "-e", str(plugin_path)], check=False)
        run([str(pip), "install", "pytest>=8,<10"], check=False)

        extra = pytest_args.split() if pytest_args else ["-q"]

        # Build work items (C, i, path, site, src) sequentially.
        tasks: list[tuple[str, int, str, MutationSite, str, float | None]] = []
        for item in sample.sampled:
            if _should_stop():
                stats.interrupted = True
                break
            c_sha = item.sha
            parent = first_parent(repo, c_sha)
            if not parent:
                continue
            c_seed = _commit_seed(seed, c_sha)
            chosen, n_inv = _candidate_mutants(repo, parent, c_sha, c_seed, mutants_k)
            with stats_lock:
                stats.n_invalid += n_inv
            if not chosen:
                continue
            for i, (path, site, new_src) in enumerate(chosen):
                key = _pair_key(c_sha, i)
                if key in done:
                    with stats_lock:
                        stats.n_skipped_completed += 1
                        if key not in stats.completed_pairs:
                            stats.completed_pairs.append(key)
                    continue
                tasks.append((c_sha, i, path, site, new_src, item.duration_sec))

        def _process(
            task: tuple[str, int, str, MutationSite, str, float | None],
        ) -> None:
            if _should_stop():
                return
            c_sha, i, path, site, new_src, duration_sec = task
            key = _pair_key(c_sha, i)
            with stats_lock:
                stats.n_mutants_attempted += 1
                stats.attempts_by_operator[site.op_id] = (
                    stats.attempts_by_operator.get(site.op_id, 0) + 1
                )
            try:
                with git_lock:
                    m_sha = create_mutant_commit(
                        repo,
                        c_sha,
                        i,
                        {path: new_src},
                    )
                    cg = collect_git_sha(repo, db, m_sha)
            except Exception as exc:  # noqa: BLE001
                print(
                    f"commit mutant failed C={c_sha[:12]} i={i}: {exc}",
                    file=sys.stderr,
                )
                with stats_lock:
                    stats.n_invalid += 1
                return

            if not cg.ok:
                with stats_lock:
                    stats.n_collect_git_failed += 1
                print(cg.note or cg.stderr, file=sys.stderr)

            # Already have synthetic run → mark complete without re-pytest.
            with db_lock:
                already = synthetic_run_exists(db, m_sha)
            if already:
                with stats_lock:
                    if key not in stats.completed_pairs:
                        stats.completed_pairs.append(key)
                    n_done = len(stats.completed_pairs)
                _append_pair(pairs_out, key, m_sha)
                maybe_test_pause_after_pairs(repo, n_done)
                return

            result = run_mutant(
                repo,
                db,
                m_sha,
                parent_sha=c_sha,
                pytest_bin=pytest,
                python_bin=vpy,
                work_dir=run_root,
                duration_sec=duration_sec,
                timeout_override=timeout_sec,
                pytest_extra_args=extra,
                pip_bin=pip,
                install_project_cb=install_project,
                mutant_index=i,
                db_lock=db_lock,
            )

            with stats_lock:
                if result.timed_out:
                    stats.n_timeout += 1
                    # timeouts are not completed pairs (may retry)
                    return
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
                if key not in stats.completed_pairs:
                    stats.completed_pairs.append(key)
                n_done = len(stats.completed_pairs)
            _append_pair(pairs_out, key, m_sha)
            maybe_test_pause_after_pairs(repo, n_done)

        if jobs <= 1:
            for task in tasks:
                if _should_stop():
                    stats.interrupted = True
                    break
                _process(task)
                if _should_stop():
                    stats.interrupted = True
                    break
        else:
            with ThreadPoolExecutor(max_workers=jobs) as pool:
                futs = []
                for task in tasks:
                    if _should_stop():
                        stats.interrupted = True
                        break
                    futs.append(pool.submit(_process, task))
                for fut in as_completed(futs):
                    fut.result()
                    if _HARD_STOP.is_set() or _budget_hit() or _STOP.is_set():
                        stats.interrupted = True
                        for f in futs:
                            f.cancel()
                        # Soft budget: in-flight finish via executor shutdown(wait=True).
                        # Hard SIGINT: children already signalled in handler.
                        break
    finally:
        if temp_root:
            shutil.rmtree(owning, ignore_errors=True)

    if _STOP.is_set() or _HARD_STOP.is_set() or _budget_hit():
        stats.interrupted = True

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
        "n_skipped_completed": stats.n_skipped_completed,
        "interrupted": stats.interrupted,
        "completed_pairs": list(stats.completed_pairs),
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
    ap.add_argument("--jobs", type=int, default=1, help="parallel mutant pytest (1–4)")
    ap.add_argument(
        "--work-root",
        type=Path,
        default=None,
        help="worktree/venv root (default: <repo>/.testpilot/bootstrap/wt-mutate-*)",
    )
    ap.add_argument(
        "--completed-pairs",
        type=Path,
        default=None,
        help="JSON file with list of already-done \"C_sha:i\" pairs",
    )
    ap.add_argument(
        "--pairs-out",
        type=Path,
        default=None,
        help="append JSONL of completed pairs as they finish",
    )
    ap.add_argument(
        "--deadline-unix",
        type=float,
        default=None,
        help="unix timestamp; stop after current mutant when reached",
    )
    args = ap.parse_args()

    def _handle_sig(_signum: int, _frame: object) -> None:
        _STOP.set()
        _HARD_STOP.set()
        kill_active_pytest_groups(signal.SIGINT)

    signal.signal(signal.SIGINT, _handle_sig)
    signal.signal(signal.SIGTERM, _handle_sig)

    py = Path(args.python or sys.executable).resolve()
    repo = args.repo.resolve()
    db = args.db.resolve()

    done: set[str] = set()
    if args.completed_pairs and args.completed_pairs.is_file():
        raw = json.loads(args.completed_pairs.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            done = {str(x) for x in raw}

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
        work_root=args.work_root.resolve() if args.work_root else None,
        jobs=args.jobs,
        completed_pairs=done,
        pairs_out=args.pairs_out,
        deadline_unix=args.deadline_unix,
    )

    report = stats_to_report(stats)

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for key, val in report.items():
            print(f"{key}={val}")

    # 130 = interrupted (Ctrl-C / deadline); nonzero so Rust can flush status.
    if stats.interrupted:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
