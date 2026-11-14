#!/usr/bin/env python3
"""Aggregate W6 bootstrap measurement allowlisted artifacts into summary JSON."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

ABS_BAD = re.compile(r"(/home/|/Users/|/tmp/)")


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def forbid_abs(obj: object, path: str) -> None:
    blob = json.dumps(obj, ensure_ascii=False)
    if ABS_BAD.search(blob):
        raise SystemExit(f"absolute path leak in {path}")


def paired_ci(train: dict) -> tuple[object, object, object]:
    # Prefer synthetic TEST cluster-paired vs cov_lines fields.
    for key in (
        "paired_delta_ci_synthetic_test_cov_lines",
        "paired_delta_ci_test",
        "paired_delta_ci_synthetic_test",
    ):
        v = train.get(key)
        if isinstance(v, dict):
            ci = v.get("ci95") or v.get("ci") or [None, None]
            if isinstance(ci, (list, tuple)) and len(ci) >= 2:
                return v.get("mean"), ci[0], ci[1]
            return (
                v.get("mean") or v.get("delta"),
                v.get("ci_low"),
                v.get("ci_high"),
            )
    boot = train.get("bootstrap") or {}
    return None, None, None


def test_nc(train: dict) -> tuple[object, object]:
    diag = train.get("diagnostics") or {}
    n_c = (
        diag.get("mutant_test_distinct_base_change")
        or diag.get("test_n_C")
        or train.get("test_n_C")
    )
    n_m = (
        diag.get("mutant_test_new_fail_changes")
        or diag.get("test_n_mutants")
        or train.get("test_n_mutants")
    )
    return n_c, n_m


def collect_mutants(repo: Path) -> dict:
    """SHA sets + kill/survive (non-flaky) + timeout pairs from mutate_pairs + DB."""
    pairs_path = repo / ".testpilot" / "bootstrap" / "mutate_pairs.jsonl"
    pairs: list[dict] = []
    if pairs_path.is_file():
        for line in pairs_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                pairs.append(json.loads(line))
    db = repo / ".testpilot" / "db.sqlite"
    labels: dict[str, str] = {}
    timeouts: list[str] = []
    if db.is_file():
        con = sqlite3.connect(db)
        for rec in pairs:
            m = rec.get("m_sha")
            pair = rec.get("pair")
            if not m:
                continue
            # Timeout mutants: no finished synthetic run or flagged — best-effort.
            row = con.execute(
                """
                SELECT COUNT(*) FROM test_runs
                WHERE sha=? AND IFNULL(synthetic,0)=1 AND finished_at IS NOT NULL
                """,
                (m,),
            ).fetchone()
            if not row or row[0] == 0:
                timeouts.append(pair or m)
                continue
            fails = con.execute(
                """
                SELECT COUNT(*) FROM test_runs r
                JOIN test_results tr ON tr.run_id = r.id
                WHERE r.sha=? AND IFNULL(r.synthetic,0)=1
                  AND tr.outcome IN ('fail','error') AND IFNULL(tr.flaky,0)=0
                """,
                (m,),
            ).fetchone()[0]
            labels[m] = "kill" if fails else "survive"
        con.close()
    m_shas = sorted(labels.keys())
    return {
        "m_shas_non_timeout": m_shas,
        "labels": labels,
        "timeout_pairs": sorted(timeouts),
        "pairs": sorted({p.get("pair") for p in pairs if p.get("pair")}),
    }


def repo_block(root: Path, name: str) -> dict:
    out = root / "docs" / "measurements" / f"bootstrap-{name}"
    br = load(out / "bootstrap_report.json")
    mj = load(out / "model.json")
    train = br
    tr_path = out / "report.json"
    if tr_path.is_file():
        train = {**load(tr_path), **br}
    mean, lo, hi = paired_ci(train if tr_path.is_file() else mj)
    if mean is None:
        mean, lo, hi = paired_ci(mj)
    n_c, n_m = test_nc(train if tr_path.is_file() else mj)
    if n_c is None:
        n_c, n_m = test_nc(mj)
    boot = mj.get("bootstrap") or br.get("bootstrap") or {}
    block = {
        "tip_repo": name,
        "step_timings_s": br.get("step_timings_s"),
        "n_backfill_new": br.get("n_backfill_new"),
        "n_backfill_reused": br.get("n_backfill_reused"),
        "backfill_s_per_commit": br.get("backfill_s_per_commit"),
        "backfill_s_per_commit_reason": br.get("backfill_s_per_commit_reason"),
        "mutate_s_per_mutant": br.get("mutate_s_per_mutant"),
        "n_mutants_attempted": br.get("n_mutants_attempted"),
        "n_timeout": br.get("n_timeout"),
        "timeout_rate": br.get("timeout_rate"),
        "n_eligible": br.get("n_eligible"),
        "n_actual": br.get("n_actual"),
        "verdict": boot.get("verdict"),
        "reason": boot.get("reason"),
        "ranker": mj.get("ranker") or br.get("ranker"),
        "test_n_C": n_c,
        "test_n_mutants": n_m,
        "paired_delta_mean": mean,
        "ci_low": lo,
        "ci_high": hi,
        "steps_resumed": br.get("steps_resumed"),
    }
    forbid_abs(br, str(out / "bootstrap_report.json"))
    forbid_abs(mj, str(out / "model.json"))
    if tr_path.is_file():
        forbid_abs(load(tr_path), str(tr_path))
    return block


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--level-b-out", type=Path, required=True)
    ap.add_argument("--pins-click", required=True)
    ap.add_argument("--pins-jinja", required=True)
    ap.add_argument("--pins-attrs", required=True)
    args = ap.parse_args()
    root: Path = args.root

    def pin(s: str) -> dict:
        sha, tag = s.split(":", 1)
        return {"sha": sha, "tag": tag}

    host = {}
    host_path = root / "docs" / "measurements" / "env-host.txt"
    if host_path.is_file():
        for line in host_path.read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                host[k] = v

    dry = {}
    dry_path = root / "docs" / "measurements" / "dry-run-estimates.json"
    if dry_path.is_file():
        dry = load(dry_path)

    per = {name: repo_block(root, name) for name in ("click", "jinja", "attrs")}

    # Level-b: compare first click in-repo DB vs second out dir's sibling... 
    # Second run used same pin; mutants live under .cache/repos/click after last run.
    # For level-b we need gold from first run — re-collect from bootstrap-click pairs
    # stored only if measure script left mutate_pairs. After second run, DB is second.
    # So compare labels from /tmp level-b out's sibling repo state vs saved gold.
    # Practical approach: save gold mutants into summary from first report's
    # completed pairs file if still present under a stamped path.
    click_repo = root / ".cache" / "repos" / "click"
    gold_path = root / "docs" / "measurements" / "bootstrap-click" / "level_b_gold.json"
    # After first click run, measure script should have saved gold; if missing, capture now
    # from current repo only once (second run overwrote DB — gold must be saved mid-script).
    # Fallback: try /tmp stamp.
    gold = None
    if gold_path.is_file():
        gold = load(gold_path)
    b_mut = collect_mutants(click_repo)
    level_b = {
        "note": "gold captured mid-measure; see level_b_gold.json if present",
        "run_b": b_mut,
        "gold": gold,
        "sha_match": None,
        "label_match": None,
    }
    if gold:
        level_b["sha_match"] = set(gold.get("m_shas_non_timeout") or []) == set(
            b_mut.get("m_shas_non_timeout") or []
        )
        level_b["label_match"] = gold.get("labels") == b_mut.get("labels")

    # LORO: concat parquets if present and train.
    loro_out = root / "docs" / "measurements" / "loro-w6-click-jinja-attrs"
    loro_out.mkdir(parents=True, exist_ok=True)
    loro_meta: dict = {"skipped": True, "reason": "missing_pairs_parquet"}
    parts = []
    try:
        import polars as pl  # type: ignore

        frames = []
        for name in ("click", "jinja", "attrs"):
            pq = root / "docs" / "measurements" / f"bootstrap-{name}" / "pairs.parquet"
            # pairs may be gitignored but still on disk after measure
            if not pq.is_file():
                # also check under repo .testpilot from last run — not available per-repo out
                continue
            df = pl.read_parquet(pq)
            if "repo" in df.columns:
                df = df.with_columns(pl.lit(name).alias("repo"))
            frames.append(df)
        if len(frames) >= 2:
            merged = pl.concat(frames, how="diagonal_relaxed")
            merged_path = loro_out / "pairs_merged.parquet"
            merged.write_parquet(merged_path)
            cmd = [
                sys.executable,
                "-m",
                "testpilot_train.cli",
                "train",
                "--data",
                str(merged_path),
                "--out",
                str(loro_out),
                "--seed",
                "42",
            ]
            subprocess.run(cmd, check=False, cwd=str(root))
            if (loro_out / "report.json").is_file():
                loro_meta = load(loro_out / "report.json").get("loro") or {"ok": True}
                forbid_abs(load(loro_out / "report.json"), str(loro_out / "report.json"))
                loro_meta = {"skipped": False, "loro": loro_meta}
            else:
                loro_meta = {"skipped": True, "reason": "train_failed"}
        else:
            loro_meta = {"skipped": True, "reason": "need_ge_2_parquets", "found": len(frames)}
    except Exception as exc:  # noqa: BLE001
        loro_meta = {"skipped": True, "reason": f"loro_error:{exc}"}

    summary = {
        "pins": {
            "click": pin(args.pins_click),
            "jinja": pin(args.pins_jinja),
            "attrs": pin(args.pins_attrs),
        },
        "env": {
            **host,
            "pip_freeze": "docs/measurements/env-pip-freeze.txt",
        },
        "dry_run_estimates": dry,
        "repos": per,
        "level_b": level_b,
        "loro": loro_meta,
        "n_C_widths_citation": {
            "path": "docs/measurements/mut2-80x5-click-jinja.json",
            "n_C_test_over_N": load(
                root / "docs" / "measurements" / "mut2-80x5-click-jinja.json"
            ).get("n_C_test_over_N"),
            "mutant_test_distinct_base_change": load(
                root / "docs" / "measurements" / "mut2-80x5-click-jinja.json"
            )
            .get("diagnostics", {})
            .get("mutant_test_distinct_base_change"),
        },
    }
    forbid_abs(summary, "bootstrap-w6-click-jinja-attrs.json")
    out = root / "docs" / "measurements" / "bootstrap-w6-click-jinja-attrs.json"
    out.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
