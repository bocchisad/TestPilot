#!/usr/bin/env python3
"""Filter interrupted pytest heads from a saved real-feature Parquet file."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq


def filter_table(
    table: pa.Table, excluded_heads: set[tuple[str, str]]
) -> tuple[pa.Table, int]:
    """Drop audited repo/change pairs while preserving Arrow schema metadata."""
    repos = table["repo"].to_pylist()
    changes = table["change"].to_pylist()
    keep = pa.array(
        [
            (str(repo), str(change)) not in excluded_heads
            for repo, change in zip(repos, changes, strict=True)
        ]
    )
    filtered = table.filter(keep)
    return filtered, table.num_rows - filtered.num_rows


def filter_parquet(input_path: Path, audit_path: Path, output_path: Path) -> dict[str, Any]:
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    excluded = {
        (str(row["repo"]), str(row["head"]))
        for row in audit.get("excluded", [])
    }
    source = pq.read_table(input_path)
    filtered, excluded_rows = filter_table(source, excluded)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(filtered, output_path)
    return {
        "input": str(input_path),
        "input_rows": source.num_rows,
        "excluded_heads": len(excluded),
        "excluded_rows": excluded_rows,
        "output": str(output_path),
        "output_rows": filtered.num_rows,
        "features_version": (filtered.schema.metadata or {}).get(
            b"features_version", b""
        ).decode(),
        "output_bytes": output_path.stat().st_size,
        "output_sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
        "audit": str(audit_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(filter_parquet(args.input, args.audit, args.out), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
