"""Markdown + JSON training reports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


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
        f"- useful_on_val: {payload.get('useful_on_val')}",
        f"- features_version: {payload.get('features_version')}",
        f"- schema_sha256: `{payload.get('schema_sha256')}`",
        "",
        "## Diagnostics",
        "",
    ]
    diag = payload.get("diagnostics") or {}
    for k, v in diag.items():
        lines.append(f"- {k}: {v}")
    lines += ["", "## Val metrics (new_fail)", ""]
    for name, m in (payload.get("metrics_val_new_fail") or {}).items():
        lines.append(f"- {name}: {m}")
    lines += ["", "## Paired delta (model − best baseline)", ""]
    lines.append(f"- val: {payload.get('paired_delta_ci_val')}")
    lines.append(f"- test: {payload.get('paired_delta_ci_test')}")
    lines += ["", "## Feature gain (top)", ""]
    for row in payload.get("feature_gain") or []:
        lines.append(f"- {row['name']}: {row['gain']}")
    (out_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
