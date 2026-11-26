#!/usr/bin/env python3
"""Run Week 7 real-only TEST comparisons for source arms and feature ablations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import polars as pl

from testpilot_train.data import load_parquets, load_real_pair_parquet
from testpilot_train.schema import load_features_schema
from testpilot_train.train_lib import evaluate_week7_loro


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--synthetic", type=Path, action="append", required=True)
    parser.add_argument("--real-all", type=Path, required=True)
    parser.add_argument("--real-without-changed-functions", type=Path, required=True)
    parser.add_argument("--schema", type=Path, default=Path("features_schema.json"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=717)
    args = parser.parse_args()

    schema = load_features_schema(args.schema)
    synthetic = load_parquets(args.synthetic, schema).frame.filter(pl.col("synthetic") == 1)
    if "partial" in synthetic.columns:
        synthetic = synthetic.filter(pl.col("partial") == 0)
    real_all = load_real_pair_parquet(args.real_all, schema)
    real_without = load_real_pair_parquet(args.real_without_changed_functions, schema)
    result = evaluate_week7_loro(
        synthetic_frame=synthetic,
        real_frame=real_all,
        real_without_changed_functions=real_without,
        schema=schema,
        seed=args.seed,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps({name: {"real_positive_changes": body["n_unique_real_positive_changes"],
                             "comparisons": len(body["comparisons"]),
                             "synthetic_comparisons": len(body["synthetic_comparisons"])}
                      for name, body in result.items()}, indent=2))


if __name__ == "__main__":
    main()
