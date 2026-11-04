#!/usr/bin/env python3
"""Compare Rust Parquet dump to features_reference.py JSON."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Columns the reference implements faithfully (leak-sensitive + service).
COMPARE_COLS = [
    "change",
    "parent",
    "test",
    "label",
    "flaky",
    "time",
    "commit_idx",
    "dur",
    "t_obs",
    "t_fail_cnt",
    "t_fail_rate",
    "t_flaky_rate",
    "t_age_days",
    "cc_max",
    "cc_sum",
    "name_file",
    "name_jac",
    "cov_known",
    "cov_lag",
    "ch_nfiles",
    "ch_nsrc",
    "t_changed",
    "tfile_ntests",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", type=Path, required=True)
    ap.add_argument("--reference", type=Path, required=True)
    ap.add_argument("--atol", type=float, default=1e-5)
    args = ap.parse_args()

    try:
        import pyarrow.parquet as pq
    except ImportError:
        print("pyarrow required: pip install pyarrow", file=sys.stderr)
        return 2

    table = pq.read_table(args.parquet)
    rust_rows = table.to_pylist()
    ref_rows = json.loads(args.reference.read_text())

    def key(r):
        return (r["change"], r["test"])

    rust_map = {key(r): r for r in rust_rows}
    ref_map = {key(r): r for r in ref_rows}
    if set(rust_map) != set(ref_map):
        only_rust = sorted(set(rust_map) - set(ref_map))
        only_ref = sorted(set(ref_map) - set(rust_map))
        print("row key mismatch", file=sys.stderr)
        print(" only rust", only_rust[:10], file=sys.stderr)
        print(" only ref", only_ref[:10], file=sys.stderr)
        return 1

    errs = 0
    for k in sorted(rust_map):
        a, b = rust_map[k], ref_map[k]
        for col in COMPARE_COLS:
            if col not in a or col not in b:
                continue
            va, vb = a[col], b[col]
            if isinstance(va, float) or isinstance(vb, float):
                if abs(float(va) - float(vb)) > args.atol:
                    print(f"mismatch {k} {col}: rust={va} ref={vb}", file=sys.stderr)
                    errs += 1
            else:
                if va != vb:
                    print(f"mismatch {k} {col}: rust={va} ref={vb}", file=sys.stderr)
                    errs += 1
    if errs:
        print(f"{errs} mismatches", file=sys.stderr)
        return 1
    print(f"ok: {len(rust_map)} rows, {len(COMPARE_COLS)} columns")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
