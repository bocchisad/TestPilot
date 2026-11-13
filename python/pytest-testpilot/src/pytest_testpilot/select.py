"""Orchestrate ``testpilot select`` for the pytest plugin."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from pytest_testpilot.gitinfo import _run

SELECT_TIMEOUT_DEFAULT = 10.0


@dataclass
class BaseResolution:
    sha: str
    label: str  # merge-base | explicit | fallback HEAD~1


@dataclass
class SelectPlan:
    """Outcome of planning which items to run (before modifyitems)."""

    ok: bool = False
    plugin_error: str | None = None
    payload: dict[str, Any] | None = None
    base: BaseResolution | None = None
    fallback_all: bool = False
    shadow: bool = False
    order: bool = False
    # item -> selection row fields for DB
    per_item: dict[int, dict[str, Any]] = field(default_factory=dict)
    deselected: list[pytest.Item] = field(default_factory=list)
    kept: list[pytest.Item] = field(default_factory=list)
    unknown_count: int = 0
    summary: dict[str, Any] = field(default_factory=dict)


def resolve_testpilot_bin() -> Path | None:
    env = os.environ.get("TESTPILOT_BIN")
    if env:
        p = Path(env)
        if p.is_file() and os.access(p, os.X_OK):
            return p
    found = shutil.which("testpilot")
    if found:
        return Path(found)
    # wheel layout: package dir may ship a native bin
    here = Path(__file__).resolve().parent
    for name in ("testpilot", "testpilot.exe"):
        cand = here / name
        if cand.is_file() and os.access(cand, os.X_OK):
            return cand
    return None


def resolve_base(repo_root: Path, explicit: str | None) -> BaseResolution:
    if explicit:
        sha = _run(["git", "rev-parse", explicit], repo_root).strip()
        return BaseResolution(sha=sha, label="explicit")
    refs = _default_branch_refs(repo_root)
    head = _run(["git", "rev-parse", "HEAD"], repo_root).strip()
    for ref in refs:
        with context_suppress():
            base = _run(["git", "merge-base", "HEAD", ref], repo_root).strip()
            if base and base != head:
                return BaseResolution(sha=base, label="merge-base")
    parent = _run(["git", "rev-parse", "HEAD~1"], repo_root, check=False).strip()
    if parent:
        return BaseResolution(sha=parent, label="fallback HEAD~1")
    return BaseResolution(sha=head, label="fallback HEAD~1")


def _default_branch_refs(repo_root: Path) -> list[str]:
    out: list[str] = []
    sym = _run(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
        repo_root,
        check=False,
    ).strip()
    if sym:
        out.append(sym.replace("refs/remotes/", ""))
    for name in ("origin/main", "origin/master", "main", "master"):
        if name not in out:
            out.append(name)
    return out


class context_suppress:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: object) -> bool:
        return True


def run_auto_index(bin_path: Path, repo_root: Path, db_path: Path) -> str | None:
    """Return plugin_error kind on failure, else None."""
    env = os.environ.copy()
    try:
        proc = subprocess.run(
            [str(bin_path), "--db", str(db_path), "collect", "git"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
        if proc.returncode != 0:
            return "snapshot"
    except (OSError, subprocess.TimeoutExpired):
        return "snapshot"
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "features.parquet"
        try:
            proc = subprocess.run(
                [
                    str(bin_path),
                    "--db",
                    str(db_path),
                    "features",
                    "dump",
                    "--out",
                    str(out),
                ],
                cwd=repo_root,
                capture_output=True,
                text=True,
                timeout=300,
                env=env,
            )
            if proc.returncode != 0:
                return "snapshot"
        except (OSError, subprocess.TimeoutExpired):
            return "snapshot"
    return None


def build_select_cmd(
    bin_path: Path,
    *,
    db_path: Path,
    base_sha: str,
    model: Path,
    budget_frac: float | None,
    max_tests: int | None,
    time_budget: float | None,
    no_safe: bool,
    worktree: bool,
) -> list[str]:
    cmd = [
        str(bin_path),
        "--db",
        str(db_path),
        "select",
        "--format",
        "json",
        "--base",
        base_sha,
        "--model",
        str(model),
    ]
    if worktree:
        cmd.append("--worktree")
    else:
        cmd.extend(["--head", "HEAD"])
    if budget_frac is not None:
        cmd.extend(["--budget-frac", str(budget_frac)])
    elif max_tests is not None:
        cmd.extend(["--max-tests", str(max_tests)])
    elif time_budget is not None:
        cmd.extend(["--time-budget", str(time_budget)])
    if no_safe:
        cmd.append("--no-safe")
    return cmd


def invoke_select_json(
    cmd: list[str],
    repo_root: Path,
    timeout: float,
) -> tuple[dict[str, Any] | None, str | None]:
    """Return (payload, plugin_error kind)."""
    try:
        proc = subprocess.run(
            cmd,
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except OSError:
        return None, "exit"
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").lower()
        if "snapshot" in err or "feature history" in err or "collect git" in err:
            return None, "snapshot"
        if "model" in err or "booster" in err:
            return None, "model"
        return None, "exit"
    text = proc.stdout.strip()
    if not text:
        return None, "json"
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None, "json"
    if not isinstance(payload, dict):
        return None, "json"
    return payload, None


_PARAM_SUFFIX = re.compile(r"\[[^\]]*\]$")


def bare_test_id(test_id: str) -> str:
    return _PARAM_SUFFIX.sub("", test_id)


def match_selected_entry(test_id: str, by_id: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    if test_id in by_id:
        return by_id[test_id]
    bare = bare_test_id(test_id)
    if bare in by_id:
        return by_id[bare]
    return None


def plan_selection(
    items: list[pytest.Item],
    payload: dict[str, Any],
    *,
    shadow: bool,
    order: bool,
    test_id_fn: Any,
) -> SelectPlan:
    plan = SelectPlan(ok=True, payload=payload, shadow=shadow, order=order)
    safe = payload.get("safe") or {}
    plan.fallback_all = bool(safe.get("fallback_all"))

    tests_json = payload.get("tests") or []
    by_id: dict[str, dict[str, Any]] = {}
    for row in tests_json:
        tid = row.get("test_id")
        if isinstance(tid, str):
            by_id[tid] = row

    n_candidates = int(payload.get("n_candidates") or 0)
    n_selected = int(payload.get("n_selected") or len(tests_json))
    n_deselect = max(0, n_candidates - n_selected)

    matched: list[pytest.Item] = []
    not_matched: list[pytest.Item] = []
    for item in items:
        tid = test_id_fn(item)
        if match_selected_entry(tid, by_id) is not None:
            matched.append(item)
        else:
            not_matched.append(item)

    not_matched_sorted = sorted(not_matched, key=lambda it: it.nodeid)
    deselect_these = not_matched_sorted[:n_deselect]
    unknown_these = not_matched_sorted[n_deselect:]
    plan.unknown_count = len(unknown_these)

    if plan.fallback_all or shadow:
        plan.kept = list(items)
        plan.deselected = []
    else:
        deselect_set = {id(x) for x in deselect_these}
        plan.kept = [it for it in items if id(it) not in deselect_set]
        plan.deselected = deselect_these

    budget = payload.get("budget") or {}
    budget_kind = str(budget.get("kind") or "frac")
    budget_value = float(budget.get("value") or 0.0)
    model_sha = str(payload.get("model_sha256") or "")
    safe_enabled = bool(safe.get("enabled"))

    for item in items:
        tid = test_id_fn(item)
        entry = match_selected_entry(tid, by_id)
        iid = id(item)
        score = None
        rank = None
        if entry is not None:
            selected = 1
            reason = str(entry.get("reason") or "ranked")
            score = entry.get("score")
            rank = entry.get("rank")
        elif item in unknown_these:
            selected = 1
            reason = "unknown_to_select"
        elif plan.fallback_all:
            selected = 1
            reason = "fallback_all"
        else:
            selected = 0
            reason = "deselected"
        plan.per_item[iid] = {
            "test_id": tid,
            "selected": selected,
            "reason": reason,
            "score": score,
            "rank": rank,
            "budget_kind": budget_kind,
            "budget_value": budget_value,
            "model_sha256": model_sha,
            "safe_enabled": safe_enabled,
        }

    plan.summary = {
        "n_selected_ids": len(tests_json),
        "n_collected_items": len(items),
        "n_kept": len(plan.kept),
        "n_deselected": len(plan.deselected),
        "n_unknown_to_select": plan.unknown_count,
        "fallback_all": plan.fallback_all,
        "safe_enabled": safe_enabled,
    }
    return plan


def apply_order(plan: SelectPlan, test_id_fn: Any) -> None:
    if not plan.order or plan.fallback_all:
        return
    payload = plan.payload or {}
    tests_json = payload.get("tests") or []
    rank_by: dict[str, int] = {}
    for row in tests_json:
        tid = row.get("test_id")
        if isinstance(tid, str):
            r = int(row.get("rank") or 10_000_000)
            rank_by[tid] = r
            rank_by[bare_test_id(tid)] = r

    def sort_key(it: pytest.Item) -> tuple[int, str]:
        tid = test_id_fn(it)
        ent = match_selected_entry(tid, {r["test_id"]: r for r in tests_json if "test_id" in r})
        if ent is not None:
            return (int(ent.get("rank") or 0), it.nodeid)
        return (10_000_000, it.nodeid)

    plan.kept.sort(key=sort_key)


def validate_budget_options(config: pytest.Config) -> None:
    if not (
        config.getoption("--testpilot-select") or config.getoption("--testpilot-shadow")
    ):
        return
    opts = [
        config.getoption("--testpilot-budget-frac"),
        config.getoption("--testpilot-max-tests"),
        config.getoption("--testpilot-time-budget"),
    ]
    n_set = sum(1 for o in opts if o is not None)
    if n_set != 1:
        raise pytest.UsageError(
            "with --testpilot-select, pass exactly one of "
            "--testpilot-budget-frac, --testpilot-max-tests, or --testpilot-time-budget"
        )
    model = config.getoption("--testpilot-model")
    if not model:
        raise pytest.UsageError("--testpilot-model DIR is required with --testpilot-select")


def selection_rows_for_db(plan: SelectPlan, items: list[pytest.Item]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in items:
        meta = plan.per_item.get(id(item))
        if meta:
            rows.append(dict(meta))
    return rows


def compute_shadow_meta(
    store_path: Path,
    base_sha: str,
    results: list[dict[str, Any]],
    would_select: set[str],
) -> dict[str, Any]:
    import sqlite3

    conn = sqlite3.connect(store_path)
    try:
        base_fails: set[str] = set()
        row = conn.execute(
            """
            SELECT id FROM test_runs
            WHERE sha = ? AND source = 'pytest' AND synthetic = 0 AND partial = 0
              AND finished_at IS NOT NULL
            ORDER BY finished_at DESC LIMIT 1
            """,
            (base_sha,),
        ).fetchone()
        if row:
            run_id = int(row[0])
            for r in conn.execute(
                """
                SELECT t.test_id FROM test_results tr
                JOIN tests t ON t.id = tr.test_pk
                WHERE tr.run_id = ? AND tr.outcome IN ('fail', 'error')
                """,
                (run_id,),
            ):
                base_fails.add(str(r[0]))
    finally:
        conn.close()

    new_fails: list[str] = []
    for r in results:
        if r.get("flaky"):
            continue
        if r.get("outcome") not in ("fail", "error"):
            continue
        tid = r["test_id"]
        if tid not in base_fails:
            new_fails.append(tid)

    caught = [t for t in new_fails if t in would_select or bare_test_id(t) in would_select]
    denom = len(new_fails)
    ratio = (len(caught) / denom) if denom else None
    return {
        "shadow_new_fails": denom,
        "shadow_caught": len(caught),
        "shadow_caught_fail": ratio,
    }


def format_select_summary_line(plan: SelectPlan) -> str:
    parts: list[str] = []
    if plan.base:
        parts.append(f"base={plan.base.sha} ({plan.base.label})")
    s = plan.summary
    if s:
        parts.append(
            f"selected={s.get('n_selected_ids')} collected={s.get('n_collected_items')} "
            f"kept={s.get('n_kept')} deselected={s.get('n_deselected')} "
            f"unknown={s.get('n_unknown_to_select', 0)}"
        )
    if plan.fallback_all:
        parts.append("fallback=fallback_all")
    if plan.plugin_error:
        parts.append(f"fallback=plugin_error({plan.plugin_error})")
    if s.get("safe_enabled") is not None:
        parts.append(f"safe.enabled={str(s.get('safe_enabled')).lower()}")
    return "testpilot select: " + " ".join(parts)


