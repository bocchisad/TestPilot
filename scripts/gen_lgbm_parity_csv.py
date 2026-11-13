#!/usr/bin/env python3
"""Regenerate tests/fixtures/reference_model/parity_scores.csv (exact f64 repr).

Requires: lightgbm, polars, testpilot-train (or PYTHONPATH=python/testpilot-train/src).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import polars as pl

from testpilot_train.schema import load_features_schema


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="TestPilot repo root",
    )
    args = ap.parse_args()
    root: Path = args.root
    fix = root / "tests" / "fixtures" / "reference_model"
    schema = load_features_schema(root / "features_schema.json")
    meta = json.loads((fix / "model.json").read_text(encoding="utf-8"))
    best = int(meta["best_iteration"])
    if meta["schema_sha256"] != schema.schema_sha256:
        raise SystemExit("schema_sha256 mismatch vs features_schema.json")
    if list(meta["feature_names"]) != list(schema.feature_names):
        raise SystemExit("feature_names mismatch")

    pairs = pl.read_parquet(fix / "pairs.parquet").sort(["repo", "change", "test"])
    ref = pl.read_parquet(fix / "reference_predictions.parquet").sort(
        ["repo", "change", "test"]
    )
    joined = pairs.join(ref, on=["repo", "change", "test"], how="inner")
    if joined.height != ref.height:
        raise SystemExit("join dropped rows")

    booster = lgb.Booster(model_file=str(fix / "model.txt"))
    x = joined.select(list(schema.feature_names)).to_numpy()
    scores = booster.predict(x, raw_score=True, num_iteration=best)
    max_err = float(np.max(np.abs(scores - joined["raw_score"].to_numpy())))
    if max_err > 1e-12:
        raise SystemExit(f"reference_predictions drift: max_abs_err={max_err}")

    cols = ["repo", "change", "test", "raw_score", *schema.feature_names]
    lines = [",".join(cols)]
    for i, row in enumerate(joined.iter_rows(named=True)):
        cells = [row["repo"], row["change"], row["test"], repr(float(scores[i]))]
        for name in schema.feature_names:
            cells.append(repr(float(row[name])))
        lines.append(",".join(cells))
    out = fix / "parity_scores.csv"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out} rows={joined.height} best_iteration={best} max_abs_err={max_err}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
