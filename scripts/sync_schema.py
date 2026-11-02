#!/usr/bin/env python3
"""Copy crates/testpilot-core/schema.sql into the pytest plugin package."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "crates" / "testpilot-core" / "schema.sql"
DST = (
    ROOT
    / "python"
    / "pytest-testpilot"
    / "src"
    / "pytest_testpilot"
    / "schema.sql"
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if destination differs from source (CI mode)",
    )
    args = parser.parse_args()

    if not SRC.is_file():
        print(f"missing source schema: {SRC}", file=sys.stderr)
        return 1

    text = SRC.read_text(encoding="utf-8")
    if args.check:
        if not DST.is_file():
            print(f"missing synced schema: {DST}", file=sys.stderr)
            return 1
        if DST.read_text(encoding="utf-8") != text:
            print(
                "schema.sql out of sync; run: python scripts/sync_schema.py",
                file=sys.stderr,
            )
            return 1
        print("schema.sql in sync")
        return 0

    DST.parent.mkdir(parents=True, exist_ok=True)
    DST.write_text(text, encoding="utf-8")
    print(f"wrote {DST.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
