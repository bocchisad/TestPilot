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
        payload = train(args.data, args.out, schema_path, seed=args.seed)
        print(json.dumps({"status": payload["status"], "out": str(args.out)}, indent=2))
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
