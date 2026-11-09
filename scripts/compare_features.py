#!/usr/bin/env python3
"""Compare Rust Parquet dump to features_reference.py JSON (all schema columns)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "features_schema.json"


def load_compare_cols(schema_path: Path) -> list[str]:
    doc = json.loads(schema_path.read_text())
    return [c["name"] for c in doc["columns"]]


def compare_rows(
    rust_rows: list[dict],
    ref_rows: list[dict],
    cols: list[str],
    atol: float = 1e-5,
) -> int:
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
        for col in cols:
            if col not in a or col not in b:
                print(f"missing column {col} in {k}", file=sys.stderr)
                errs += 1
                continue
            va, vb = a[col], b[col]
            if isinstance(va, float) or isinstance(vb, float):
                if abs(float(va) - float(vb)) > atol:
                    print(f"mismatch {k} {col}: rust={va} ref={vb}", file=sys.stderr)
                    errs += 1
            else:
                if va != vb:
                    print(f"mismatch {k} {col}: rust={va} ref={vb}", file=sys.stderr)
                    errs += 1
    if errs:
        print(f"{errs} mismatches", file=sys.stderr)
        return 1
    print(f"ok: {len(rust_map)} rows, {len(cols)} columns")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", type=Path, required=True)
    ap.add_argument("--reference", type=Path, required=True)
    ap.add_argument("--schema", type=Path, default=SCHEMA_PATH)
    ap.add_argument("--atol", type=float, default=1e-5)
    ap.add_argument(
        "--inject-rust-ch-add",
        type=int,
        default=None,
        help="W2-2 sensitivity: overwrite rust ch_add (expect non-zero exit)",
    )
    args = ap.parse_args()

    try:
        import pyarrow.parquet as pq
    except ImportError:
        print("pyarrow required: pip install pyarrow", file=sys.stderr)
        return 2

    cols = load_compare_cols(args.schema)
    assert len(cols) == 37, f"expected 37 columns, got {len(cols)}"

    rust_rows = pq.read_table(args.parquet).to_pylist()
    if args.inject_rust_ch_add is not None:
        for r in rust_rows:
            r["ch_add"] = args.inject_rust_ch_add
    ref_rows = json.loads(args.reference.read_text())
    return compare_rows(rust_rows, ref_rows, cols, args.atol)


if __name__ == "__main__":
    raise SystemExit(main())
