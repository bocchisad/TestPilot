"""Markdown + JSON training reports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _hist_lines(title: str, dist: dict[str, Any] | None) -> list[str]:
    if not dist:
        return []
    lines = [
        f"### {title}",
        "",
        f"- min/p50/p90/max: {dist.get('min')}/{dist.get('p50')}/{dist.get('p90')}/{dist.get('max')}",
        "",
    ]
    for row in dist.get("histogram") or []:
        lines.append(f"- {row['bin']}: {row['count']}")
    lines.append("")
    return lines


def _metric_lines(title: str, block: dict[str, Any] | None) -> list[str]:
    if not block:
        return []
    lines = [f"## {title}", ""]
    for name, m in block.items():
        lines.append(f"- {name}: {m}")
    lines.append("")
    return lines


def _baseline_table_lines(title: str, table: dict[str, Any] | None) -> list[str]:
    if not table:
        return []
    lines = [f"### {title}", ""]
    for name, row in table.items():
        if name == "best_for_paired_gate":
            continue
        if not isinstance(row, dict):
            continue
        rec = row.get("recall@10%")
        n = row.get("n")
        if rec is None:
            lines.append(f"- `{name}`: n={n} (no new_fail changes)")
        else:
            lines.append(
                f"- `{name}`: recall@10%={rec.get('mean'):.4f} "
                f"ci95={rec.get('ci95')} n={n}"
            )
    lines.append("")
    return lines


def write_report(out_dir: Path, payload: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    useful = payload.get("useful_on_val")
    useful_s = "null (data_insufficient)" if useful is None else str(useful)
    lines = [
        "# TestPilot training report",
        "",
        f"- status: **{payload.get('status')}**",
        f"- objective: `{payload.get('objective')}`",
        f"- useful_on_val ({payload.get('useful_on_val_label', 'useful_on_val')}): "
        f"{useful_s}",
        f"- useful_on_synthetic_val "
        f"({payload.get('useful_on_synthetic_val_label', 'useful_on_synthetic_val')}): "
        f"{payload.get('useful_on_synthetic_val')}",
        f"- useful_on_synthetic_test "
        f"({payload.get('useful_on_synthetic_test_label', 'useful_on_synthetic_test')}): "
        f"{payload.get('useful_on_synthetic_test')}",
        f"- ranker: `{payload.get('ranker')}`",
        f"- bootstrap.verdict: **{(payload.get('bootstrap') or {}).get('verdict')}** "
        f"({(payload.get('bootstrap') or {}).get('reason')})",
        f"- features_version: {payload.get('features_version')}",
        f"- schema_sha256: `{payload.get('schema_sha256')}`",
    ]
    if payload.get("note_real_gate"):
        lines.append(f"- note: {payload['note_real_gate']}")
    if payload.get("note"):
        lines.append(f"- note: {payload['note']}")
    if payload.get("note_gate"):
        lines.append(f"- note: {payload['note_gate']}")
    lines += ["", "## Diagnostics", ""]
    diag = payload.get("diagnostics") or {}
    for k, v in diag.items():
        if k in (
            "fail_per_change",
            "new_fail_per_change",
            "fail_per_change_including_high_fail",
            "new_fail_per_change_including_high_fail",
            "always_broken_tests",
        ):
            continue
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines += _hist_lines(
        "Failures per change after HF filter (`label=1`, flaky=0)",
        diag.get("fail_per_change"),
    )
    lines += _hist_lines(
        "new_fail per change after HF filter",
        diag.get("new_fail_per_change"),
    )
    lines += _hist_lines(
        "Failures per change including high-fail-rate mutants",
        diag.get("fail_per_change_including_high_fail"),
    )
    lines += _hist_lines(
        "new_fail per change including high-fail-rate mutants",
        diag.get("new_fail_per_change_including_high_fail"),
    )
    always = diag.get("always_broken_tests") or []
    lines += [
        f"### Always-broken tests (≥90% fails, n={len(always)})",
        "",
    ]
    for row in always[:50]:
        lines.append(
            f"- `{row.get('repo')}` `{row.get('test')}`: "
            f"{row.get('n_fail')}/{row.get('n_appear')} ({row.get('fail_rate'):.0%})"
        )
    if len(always) > 50:
        lines.append(f"- … +{len(always) - 50} more")
    lines += ["", "## Val metrics (new_fail)", ""]
    for name, m in (payload.get("metrics_val_new_fail") or {}).items():
        lines.append(f"- {name}: {m}")
    lines.append("")
    lines += _metric_lines(
        "Val metrics including high-fail-rate mutants",
        payload.get("metrics_val_new_fail_including_high_fail"),
    )
    lines += _metric_lines(
        "Val metrics excluding high-fail-rate mutants",
        payload.get("metrics_val_new_fail_excluding_high_fail"),
    )
    lines += _metric_lines(
        "Test metrics including high-fail-rate mutants",
        payload.get("metrics_test_new_fail_including_high_fail"),
    )
    lines += _metric_lines(
        "Test metrics excluding high-fail-rate mutants",
        payload.get("metrics_test_new_fail_excluding_high_fail"),
    )

    bases = payload.get("baselines") or {}
    if bases:
        lines += [
            "## Baselines (recall@10%)",
            "",
            f"- best for paired gate: `{bases.get('best_for_paired_gate')}`",
            "",
        ]
        lines += _baseline_table_lines("Val", bases.get("val"))
        lines += _baseline_table_lines("Test", bases.get("test"))
        lines += _baseline_table_lines("Val synthetic", bases.get("val_synthetic"))
        lines += _baseline_table_lines("Test synthetic", bases.get("test_synthetic"))

    loro = payload.get("loro") or {}
    if loro:
        lines += ["", "## LORO (macro recall@10% new_fail on held-out test)", ""]
        lines.append(f"- macro: {loro.get('macro_recall@10%')}")
        for row in loro.get("per_repo") or []:
            held = row.get("held_out")
            lines.append(
                f"- {held}: model={row.get('recall@10%')} "
                f"n_changes={row.get('n_changes')}"
            )
            bases = row.get("baselines") or {}
            if bases:
                parts = [
                    f"{name}={bases.get(name)}"
                    for name in ("cov_lines", "cc_max", "random")
                    if name in bases
                ]
                if parts:
                    lines.append(f"  - baselines: {', '.join(parts)}")
    lines += ["", "## Paired delta (model − best baseline)", ""]
    lines.append(f"- val: {payload.get('paired_delta_ci_val')}")
    lines.append(f"- test: {payload.get('paired_delta_ci_test')}")
    if payload.get("paired_delta_ci_synthetic_val_cov_lines"):
        lines.append(
            f"- synthetic val vs cov_lines (report-only): "
            f"{payload.get('paired_delta_ci_synthetic_val_cov_lines')}"
        )
    if payload.get("paired_delta_ci_synthetic_test_cov_lines"):
        lines.append(
            f"- synthetic test vs cov_lines (gate): "
            f"{payload.get('paired_delta_ci_synthetic_test_cov_lines')}"
        )
    lines += ["", "## Candidates", ""]
    for row in payload.get("candidates") or []:
        lines.append(f"- {row}")
    lines += ["", "## Feature gain (top)", ""]
    for row in (payload.get("feature_gain") or [])[:20]:
        lines.append(f"- {row['name']}: {row['gain']}")

    feat_stats = payload.get("feature_value_stats_synthetic") or []
    if feat_stats:
        lines += ["", "## Feature value stats (synthetic)", ""]
        lines.append("| feature | n_unique | n_rows | sentinel | sentinel_share |")
        lines.append("|---|---:|---:|---:|---:|")
        for row in feat_stats:
            lines.append(
                f"| `{row['name']}` | {row['n_unique']} | "
                f"{row['n_rows']} | {row.get('sentinel')} | "
                f"{row['sentinel_share']:.4f} |"
            )
        lines.append("")

    (out_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
