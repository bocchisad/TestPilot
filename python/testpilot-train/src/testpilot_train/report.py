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


def write_report(out_dir: Path, payload: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    lines = [
        "# TestPilot training report",
        "",
        f"- status: **{payload.get('status')}**",
        f"- objective: `{payload.get('objective')}`",
        f"- useful_on_val ({payload.get('useful_on_val_label', 'useful_on_val')}): "
        f"{payload.get('useful_on_val')}",
        f"- useful_on_synthetic_val "
        f"({payload.get('useful_on_synthetic_val_label', 'useful_on_synthetic_val')}): "
        f"{payload.get('useful_on_synthetic_val')}",
        f"- features_version: {payload.get('features_version')}",
        f"- schema_sha256: `{payload.get('schema_sha256')}`",
        "",
        "## Diagnostics",
        "",
    ]
    diag = payload.get("diagnostics") or {}
    for k, v in diag.items():
        if k in (
            "fail_per_change",
            "new_fail_per_change",
            "always_broken_tests",
        ):
            continue
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines += _hist_lines("Failures per change (`label=1`, flaky=0)", diag.get("fail_per_change"))
    lines += _hist_lines("new_fail per change", diag.get("new_fail_per_change"))
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
    loro = payload.get("loro") or {}
    if loro:
        lines += ["", "## LORO (macro recall@10% new_fail on held-out test)", ""]
        lines.append(f"- macro: {loro.get('macro_recall@10%')}")
        for row in loro.get("per_repo") or []:
            lines.append(f"- {row.get('held_out')}: {row.get('recall@10%')}")
    lines += ["", "## Paired delta (model − best baseline)", ""]
    lines.append(f"- val: {payload.get('paired_delta_ci_val')}")
    lines.append(f"- test: {payload.get('paired_delta_ci_test')}")
    lines += ["", "## Candidates", ""]
    for row in payload.get("candidates") or []:
        lines.append(f"- {row}")
    lines += ["", "## Feature gain (top)", ""]
    for row in (payload.get("feature_gain") or [])[:20]:
        lines.append(f"- {row['name']}: {row['gain']}")
    (out_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
