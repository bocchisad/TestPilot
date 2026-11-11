"""Leakage canaries on synthetic data."""

from __future__ import annotations

import numpy as np
import polars as pl

from testpilot_train.baselines import random_recall
from testpilot_train.data import feature_matrix, training_mask
from testpilot_train.metrics import change_metrics, macro_mean
from testpilot_train.model import DEFAULT_PARAMS, fit_binary, per_change_metrics_for_scores, predict_raw
from testpilot_train.schema import FeaturesSchema
from testpilot_train.split import chrono_split, filter_slice


def shuffled_label_gap(
    frame: pl.DataFrame, schema: FeaturesSchema, seed: int = 0
) -> float:
    """|recall@10% (shuffled train labels) - random| on val."""
    plan = chrono_split(frame, min_changes=10)
    train = filter_slice(frame, plan, "train")
    val = filter_slice(frame, plan, "val")
    rng = np.random.default_rng(seed)
    train_shuf = train.with_columns(
        pl.Series("new_fail", rng.permutation(train["new_fail"].to_numpy()))
    )
    fit = fit_binary(train_shuf, val, schema, seed)
    scores = predict_raw(fit.booster, val, schema)
    per = per_change_metrics_for_scores(val, scores, "new_fail")
    model_r = macro_mean(per, "recall@10%")
    rnd = []
    for _k, grp in val.group_by(["repo", "change"], maintain_order=True):
        grp = grp.filter(~pl.col("parent_missing"))
        y = grp["new_fail"].to_numpy()
        n_pos = int(y.sum())
        if n_pos == 0:
            continue
        rnd.append(random_recall(len(y), n_pos, 0.10))
    random_r = float(np.mean(rnd)) if rnd else 0.0
    return abs(model_r - random_r)


def future_feature_canary(
    frame: pl.DataFrame, schema: FeaturesSchema, seed: int = 0
) -> tuple[bool, float, float]:
    """Inject new_fail as a feature; expect it to dominate gain and beat random."""
    import lightgbm as lgb

    plan = chrono_split(frame, min_changes=10)
    train = filter_slice(frame, plan, "train")
    val = filter_slice(frame, plan, "val")
    mask_tr = training_mask(train)
    mask_va = training_mask(val)
    tr = train.filter(pl.Series(mask_tr)).sort(["repo", "commit_idx", "change", "test"])
    va = val.filter(pl.Series(mask_va)).sort(["repo", "commit_idx", "change", "test"])
    Xtr = np.column_stack(
        [feature_matrix(tr, schema.feature_names), tr["new_fail"].to_numpy().astype(np.float64)]
    )
    Xva = np.column_stack(
        [feature_matrix(va, schema.feature_names), va["new_fail"].to_numpy().astype(np.float64)]
    )
    ytr = tr["new_fail"].to_numpy().astype(np.float64)
    params = {
        **DEFAULT_PARAMS,
        "objective": "binary",
        "metric": "auc",
        "seed": seed,
        "min_data_in_leaf": 1,
        "min_sum_hessian_in_leaf": 0,
        "feature_fraction": 1.0,
        "bagging_fraction": 1.0,
        "bagging_freq": 0,
        "scale_pos_weight": max(len(ytr) - ytr.sum(), 1) / max(ytr.sum(), 1),
    }
    names = list(schema.feature_names) + ["__future_label"]
    dtr = lgb.Dataset(Xtr, label=ytr, feature_name=names)
    booster = lgb.train(params, dtr, num_boost_round=50)
    gains = booster.feature_importance(importance_type="gain")
    future_gain = float(gains[-1])
    other_max = float(gains[:-1].max()) if len(gains) > 1 else 0.0
    scores = booster.predict(Xva, raw_score=True)
    va2 = va.with_columns(pl.Series("_score", scores))
    per = []
    for _k, grp in va2.group_by(["repo", "change"], maintain_order=True):
        grp = grp.filter(~pl.col("parent_missing"))
        y = grp["new_fail"].to_numpy()
        if int(y.sum()) == 0:
            continue
        per.append(change_metrics(y, grp["_score"].to_numpy()))
    leak_r = macro_mean(per, "recall@10%")
    rnd = []
    for _k, grp in va.group_by(["repo", "change"], maintain_order=True):
        grp = grp.filter(~pl.col("parent_missing"))
        y = grp["new_fail"].to_numpy()
        n_pos = int(y.sum())
        if not n_pos:
            continue
        rnd.append(random_recall(len(y), n_pos, 0.10))
    random_r = float(np.mean(rnd)) if rnd else 0.0
    # Dense positives cap recall@10% even for an oracle; gain dominance is the canary.
    fired = future_gain > other_max * 10 and leak_r > random_r
    return fired, leak_r, random_r
