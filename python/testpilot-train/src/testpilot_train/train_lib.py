"""End-to-end train / evaluate orchestration."""

from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import polars as pl

from testpilot_train.artifact import save_booster, write_model_json, write_reference_predictions
from testpilot_train.baselines import BASELINE_NAMES, baseline_scores
from testpilot_train.data import diagnostics, load_parquets
from testpilot_train.metrics import (
    bootstrap_ci,
    change_metrics,
    macro_mean,
    paired_delta_ci,
    useful_by_paired,
)
from testpilot_train.model import (
    EARLY_STOPPING_ROUNDS,
    NUM_BOOST_ROUND,
    per_change_metrics_for_scores,
    predict_raw,
    select_objective,
)
from testpilot_train.report import write_report
from testpilot_train.schema import assert_schema_matches_model, load_features_schema
from testpilot_train.split import (
    chrono_split,
    filter_slice,
    loro_train_val,
    split_to_json,
)


def _metric_block(per: list[dict[str, float]], seed: int) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in (
        "recall@5%",
        "recall@10%",
        "recall@20%",
        "apfd",
        "first_fail_frac",
        "all_fail_frac",
    ):
        pt = bootstrap_ci(per, key, seed=seed)
        out[key] = {
            "mean": pt.mean,
            "ci95": [pt.ci_low, pt.ci_high],
            "n_changes": pt.n_changes,
        }
    return out


def _per_change_keyed(
    frame: pl.DataFrame,
    scores: np.ndarray,
    label_col: str,
) -> dict[tuple[str, str], dict[str, float]]:
    tmp = frame.with_columns(pl.Series("_score", scores))
    out: dict[tuple[str, str], dict[str, float]] = {}
    for keys, grp in tmp.group_by(["repo", "change"], maintain_order=True):
        repo, change = keys  # type: ignore[misc]
        if label_col == "new_fail":
            grp = grp.filter(~pl.col("parent_missing"))
        y = grp[label_col].to_numpy()
        if int(y.sum()) == 0:
            continue
        out[(str(repo), str(change))] = change_metrics(y, grp["_score"].to_numpy())
    return out


def _align_per(
    a: dict[tuple[str, str], dict[str, float]],
    b: dict[tuple[str, str], dict[str, float]],
) -> tuple[list[dict[str, float]], list[dict[str, float]]]:
    keys = sorted(set(a) & set(b))
    return [a[k] for k in keys], [b[k] for k in keys]


def _label_secondary(
    frame: pl.DataFrame, scores: np.ndarray, *, drop_flaky: bool
) -> list[dict[str, float]]:
    tmp = frame.with_columns(pl.Series("_score", scores))
    if drop_flaky:
        tmp = tmp.filter(pl.col("flaky") == 0)
    out: list[dict[str, float]] = []
    for _k, grp in tmp.group_by(["repo", "change"], maintain_order=True):
        y = grp["label"].to_numpy()
        if int(y.sum()) == 0:
            continue
        out.append(change_metrics(y, grp["_score"].to_numpy()))
    return out


def _run_loro(
    frame: pl.DataFrame,
    plan,
    schema,
    seed: int,
) -> dict[str, Any]:
    repos = [rs.repo for rs in plan.by_repo]
    if len(repos) < 2:
        return {"skipped": True, "reason": "need ≥2 repos"}
    per_repo = []
    recalls = []
    for held in repos:
        tr, va, te = loro_train_val(frame, plan, held)
        fit, _cands = select_objective(tr, va, schema, seed)
        scores = predict_raw(fit.booster, te, schema)
        per = per_change_metrics_for_scores(te, scores, "new_fail")
        r10 = macro_mean(per, "recall@10%")
        per_repo.append(
            {
                "held_out": held,
                "recall@10%": r10,
                "n_changes": len(per),
                "objective": fit.objective,
                "best_iteration": fit.best_iteration,
            }
        )
        if r10 == r10:
            recalls.append(r10)
    return {
        "skipped": False,
        "per_repo": per_repo,
        "macro_recall@10%": float(np.mean(recalls)) if recalls else float("nan"),
    }


def train(
    data_paths: list[Path],
    out_dir: Path,
    features_schema: Path,
    seed: int = 42,
) -> dict[str, Any]:
    schema = load_features_schema(features_schema)
    loaded = load_parquets(data_paths, schema)
    frame = loaded.frame
    diag = diagnostics(frame)
    plan = chrono_split(frame)
    train_df = filter_slice(frame, plan, "train")
    val_df = filter_slice(frame, plan, "val")
    test_df = filter_slice(frame, plan, "test")

    fit, candidates = select_objective(train_df, val_df, schema, seed)

    val_scores = predict_raw(fit.booster, val_df, schema)
    test_scores = predict_raw(fit.booster, test_df, schema)
    val_keyed = _per_change_keyed(val_df, val_scores, "new_fail")
    test_keyed = _per_change_keyed(test_df, test_scores, "new_fail")
    val_per = [val_keyed[k] for k in sorted(val_keyed)]
    test_per = [test_keyed[k] for k in sorted(test_keyed)]

    baseline_val_keyed: dict[str, dict[tuple[str, str], dict[str, float]]] = {}
    best_name = "random"
    best_mean = -1.0
    for name in BASELINE_NAMES:
        keyed: dict[tuple[str, str], dict[str, float]] = {}
        for keys, grp in val_df.group_by(["repo", "change"], maintain_order=True):
            repo, change = keys  # type: ignore[misc]
            g = grp.filter(~pl.col("parent_missing"))
            y = g["new_fail"].to_numpy()
            if int(y.sum()) == 0:
                continue
            keyed[(str(repo), str(change))] = change_metrics(y, baseline_scores(g, name))
        baseline_val_keyed[name] = keyed
        _aligned_m, aligned_b = _align_per(val_keyed, keyed)
        m = macro_mean(aligned_b, "recall@10%")
        if m == m and m > best_mean:
            best_mean = m
            best_name = name

    m_val, b_val = _align_per(val_keyed, baseline_val_keyed[best_name])
    delta_val = paired_delta_ci(m_val, b_val, "recall@10%", seed=seed)

    base_test_keyed: dict[tuple[str, str], dict[str, float]] = {}
    for keys, grp in test_df.group_by(["repo", "change"], maintain_order=True):
        repo, change = keys  # type: ignore[misc]
        g = grp.filter(~pl.col("parent_missing"))
        y = g["new_fail"].to_numpy()
        if int(y.sum()) == 0:
            continue
        base_test_keyed[(str(repo), str(change))] = change_metrics(
            y, baseline_scores(g, best_name)
        )
    m_test, b_test = _align_per(test_keyed, base_test_keyed)
    delta_test = paired_delta_ci(m_test, b_test, "recall@10%", seed=seed + 1)

    n_test_new = len(test_keyed)
    status = "ok" if n_test_new >= 20 else "data_insufficient"
    useful_on_val = useful_by_paired(delta_val)

    gain = fit.booster.feature_importance(importance_type="gain")
    names = list(schema.feature_names)
    feature_gain = sorted(
        [{"name": n, "gain": float(g)} for n, g in zip(names, gain, strict=True)],
        key=lambda r: -r["gain"],
    )

    loro = _run_loro(frame, plan, schema, seed)

    # new_fail counts per chrono slice
    def slice_new_fail_changes(df: pl.DataFrame) -> int:
        return int(
            df.group_by(["repo", "change"])
            .agg(pl.col("new_fail").sum().alias("n"))
            .filter(pl.col("n") > 0)
            .height
        )

    trunc = fit.params.get("lambdarank_truncation_level")
    payload: dict[str, Any] = {
        "features_version": schema.features_version,
        "schema_sha256": schema.schema_sha256,
        "feature_names": list(schema.feature_names),
        "objective": fit.objective,
        "lightgbm_version": lgb.__version__,
        "seed": seed,
        "num_boost_round": NUM_BOOST_ROUND,
        "early_stopping_rounds": EARLY_STOPPING_ROUNDS,
        "stopping_metric": fit.stopping_metric,
        "best_iteration": fit.best_iteration,
        "candidates": [c.candidate_meta() for c in candidates],
        "params": fit.params,
        "ndcg_eval_at": trunc,
        "lambdarank_truncation_level": trunc,
        "predict_raw_score": True,
        "parent_for_new_fail": "first_parent",
        "splits": {
            "chrono": split_to_json(plan),
            "n_changes_with_new_fail": {
                "train": slice_new_fail_changes(train_df),
                "val": slice_new_fail_changes(val_df),
                "test": slice_new_fail_changes(test_df),
            },
        },
        "loro": loro,
        "diagnostics": {
            **diag,
            "n_rows_parent_missing": loaded.n_rows_parent_missing,
            "n_persistent": loaded.n_persistent,
            "n_flaky": loaded.n_flaky,
        },
        "metrics_val_new_fail": _metric_block(val_per, seed),
        "metrics_test_new_fail": _metric_block(test_per, seed + 2),
        "metrics_val_label_with_flaky": _metric_block(
            _label_secondary(val_df, val_scores, drop_flaky=False), seed + 3
        ),
        "metrics_val_label_no_flaky": _metric_block(
            _label_secondary(val_df, val_scores, drop_flaky=True), seed + 4
        ),
        "best_baseline_val": best_name,
        "paired_delta_ci_val": {
            "mean": delta_val.mean,
            "ci95": [delta_val.ci_low, delta_val.ci_high],
            "n": delta_val.n_changes,
        },
        "paired_delta_ci_test": {
            "mean": delta_test.mean,
            "ci95": [delta_test.ci_low, delta_test.ci_high],
            "n": delta_test.n_changes,
        },
        "useful_on_val": useful_on_val,
        "status": status,
        "feature_gain": feature_gain,
        "sentinel_notes": {"pf_max": -1, "pq_max": -1, "dur": -1, "imp_dist": 99},
        "constraints": {
            "categorical": False,
            "linear_tree": False,
            "num_threads_train": 1,
            "use_missing": True,
            "zero_as_missing": False,
        },
    }
    if status == "data_insufficient":
        payload["note"] = (
            "test has <20 changes with ≥1 new_fail; metrics shown but no conclusions"
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    save_booster(fit.booster, out_dir / "model.txt")
    write_model_json(out_dir / "model.json", payload)
    write_report(out_dir, payload)
    write_reference_predictions(
        out_dir / "reference_predictions.parquet", frame, fit.booster, schema
    )
    return payload


def evaluate(
    model_dir: Path,
    data_paths: list[Path],
    features_schema: Path,
    slice_name: str = "test",
) -> dict[str, Any]:
    schema = load_features_schema(features_schema)
    meta = json.loads((model_dir / "model.json").read_text(encoding="utf-8"))
    assert_schema_matches_model(schema, meta)
    loaded = load_parquets(data_paths, schema)
    booster = lgb.Booster(model_file=str(model_dir / "model.txt"))
    frame = loaded.frame

    chrono = (meta.get("splits") or {}).get("chrono") or {}
    known = set(chrono.keys())
    parts: list[pl.DataFrame] = []
    warned = False
    for repo in frame["repo"].unique().to_list():
        sub = frame.filter(pl.col("repo") == repo)
        if repo not in known:
            parts.append(sub)
            continue
        ranges = chrono[repo].get(slice_name) or chrono[repo]["test"]
        lo, hi = int(ranges[0]), int(ranges[1])
        train_lo, train_hi = chrono[repo]["train"]
        picked = sub.filter((pl.col("commit_idx") >= lo) & (pl.col("commit_idx") <= hi))
        overlap = picked.filter(
            (pl.col("commit_idx") >= int(train_lo))
            & (pl.col("commit_idx") <= int(train_hi))
        )
        if slice_name != "train" and overlap.height > 0 and not warned:
            warnings.warn(
                "evaluation slice intersects train commit_idx ranges for a known repo",
                stacklevel=2,
            )
            warned = True
        if slice_name == "train" and not warned:
            warnings.warn(
                "evaluation slice intersects train commit_idx ranges for a known repo",
                stacklevel=2,
            )
            warned = True
        parts.append(picked)
    eval_df = pl.concat(parts) if parts else frame.clear()
    scores = predict_raw(booster, eval_df, schema)
    per = per_change_metrics_for_scores(eval_df, scores, "new_fail")
    return {"n_rows": eval_df.height, "metrics": _metric_block(per, seed=0)}
