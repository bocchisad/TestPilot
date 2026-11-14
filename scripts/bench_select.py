#!/usr/bin/env python3
"""Warm-up + N timed `testpilot select` runs; print median_ms and p90_ms.

Example:
  python scripts/bench_select.py \\
    --bin ./target/release/testpilot \\
    --repo .cache/repos/click \\
    --db .cache/repos/click/.testpilot/db.sqlite \\
    --model tests/fixtures/reference_model \\
    --base HEAD~1 --head HEAD --budget-frac 0.1
"""

from __future__ import annotations

import argparse
import statistics
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", type=Path, required=True)
    ap.add_argument("--repo", type=Path, required=True)
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--model", type=Path, required=True)
    ap.add_argument("--base", default="HEAD~1")
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--budget-frac", type=float, default=0.1)
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--warmup", type=int, default=1)
    args = ap.parse_args()

    cmd = [
        str(args.bin),
        "--db",
        str(args.db),
        "select",
        "--repo",
        str(args.repo),
        "--model",
        str(args.model),
        "--base",
        args.base,
        "--head",
        args.head,
        "--budget-frac",
        str(args.budget_frac),
        "--format",
        "lines",
        "--no-safe",
    ]

    for i in range(args.warmup):
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stderr or r.stdout, file=sys.stderr)
            return r.returncode or 1
        print(f"warmup {i + 1}: ok n_lines={len((r.stdout or '').splitlines())}")

    times_ms: list[float] = []
    n_out = 0
    for i in range(args.runs):
        t0 = time.perf_counter()
        r = subprocess.run(cmd, capture_output=True, text=True)
        dt = (time.perf_counter() - t0) * 1000.0
        if r.returncode != 0:
            print(r.stderr or r.stdout, file=sys.stderr)
            return r.returncode or 1
        times_ms.append(dt)
        n_out = len((r.stdout or "").splitlines())
        print(f"run {i + 1}: {dt:.1f}ms")

    times_ms.sort()
    median = statistics.median(times_ms)
    # nearest-rank p90
    idx = min(len(times_ms) - 1, max(0, int(0.9 * (len(times_ms) - 1) + 0.5)))
    p90 = times_ms[idx]
    print(
        f"median_ms={median:.1f} p90_ms={p90:.1f} n_runs={len(times_ms)} "
        f"n_selected_lines={n_out}"
    )
    print("cmd:", " ".join(cmd))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
