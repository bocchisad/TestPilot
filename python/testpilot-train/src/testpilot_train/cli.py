"""testpilot-train CLI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from testpilot_train.baselines import BASELINE_NAMES, eval_baseline_per_change
from testpilot_train.data import load_parquets
from testpilot_train.metrics import bootstrap_ci
from testpilot_train.schema import load_features_schema
from testpilot_train.split import chrono_split, filter_slice
from testpilot_train.train_lib import evaluate, train


def _repo_root() -> Path:
    # .../python/testpilot-train/src/testpilot_train/cli.py → repo root
    return Path(__file__).resolve().parents[4]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="testpilot-train")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_train = sub.add_parser("train")
    p_train.add_argument("--data", type=Path, nargs="+", required=True)
    p_train.add_argument("--out", type=Path, required=True)
    p_train.add_argument(
        "--features-schema",
        type=Path,
        default=None,
        help="default: <repo>/features_schema.json",
    )
    p_train.add_argument("--seed", type=int, default=42)
    p_train.add_argument(
        "--exclude-high-fail-rate",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Exclude synthetic changes where >50%% of tests fail from train/val/test "
            "slices used for metrics and the paired gate (default: on). "
            "Report still includes dual metrics when high-fail changes exist."
        ),
    )
    p_train.add_argument(
        "--allow-cold-baseline",
        action="store_true",
        default=False,
        help=(
            "If unique commit_idx < 10, write baseline-only model.json "
            "(ranker=cov_lines) instead of failing. Used by `testpilot bootstrap` only."
        ),
    )

    p_eval = sub.add_parser("evaluate")
    p_eval.add_argument("--model", type=Path, required=True)
    p_eval.add_argument("--data", type=Path, nargs="+", required=True)
    p_eval.add_argument("--features-schema", type=Path, default=None)
    p_eval.add_argument(
        "--slice", choices=("test", "val", "train"), default="test"
    )

    p_base = sub.add_parser("baselines")
    p_base.add_argument("--data", type=Path, nargs="+", required=True)
    p_base.add_argument("--features-schema", type=Path, default=None)
    p_base.add_argument("--seed", type=int, default=42)

    args = ap.parse_args(argv)
    schema_path = args.features_schema or (_repo_root() / "features_schema.json")

    if args.cmd == "train":
        try:
            payload = train(
                args.data,
                args.out,
                schema_path,
                seed=args.seed,
                exclude_high_fail_rate=args.exclude_high_fail_rate,
                allow_cold_baseline=args.allow_cold_baseline,
            )
        except Exception as e:  # noqa: BLE001 — surface InsufficientCommitsError cleanly
            from testpilot_train.split import InsufficientCommitsError

            if isinstance(e, InsufficientCommitsError):
                print(f"error: {e}", file=sys.stderr)
                return 2
            raise
        print(
            json.dumps(
                {
                    "status": payload["status"],
                    "out": str(args.out),
                    "ranker": payload.get("ranker"),
                    "bootstrap": payload.get("bootstrap"),
                },
                indent=2,
                allow_nan=False,
            )
        )
        return 0

    if args.cmd == "evaluate":
        result = evaluate(args.model, args.data, schema_path, slice_name=args.slice)
        print(json.dumps(result, indent=2))
        return 0

    if args.cmd == "baselines":
        schema = load_features_schema(schema_path)
        loaded = load_parquets(args.data, schema)
        plan = chrono_split(loaded.frame)
        val = filter_slice(loaded.frame, plan, "val")
        out = {}
        for name in BASELINE_NAMES:
            per = eval_baseline_per_change(val, name, "new_fail")
            pt = bootstrap_ci(per, "recall@10%", seed=args.seed)
            out[name] = {
                "recall@10%": {"mean": pt.mean, "ci95": [pt.ci_low, pt.ci_high]}
            }
        print(json.dumps(out, indent=2))
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
