"""LightGBM training: binary vs lambdarank."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import lightgbm as lgb
import numpy as np
import polars as pl

from testpilot_train.data import feature_matrix, training_mask
from testpilot_train.metrics import change_metrics, macro_mean
from testpilot_train.schema import FeaturesSchema

DEFAULT_PARAMS: dict[str, Any] = {
    "learning_rate": 0.05,
    "num_leaves": 31,
    "min_data_in_leaf": 50,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "deterministic": True,
    "force_col_wise": True,
    "num_threads": 1,
    "linear_tree": False,
    "use_missing": True,
    "zero_as_missing": False,
    "verbosity": -1,
}

REFERENCE_PARAMS: dict[str, Any] = {
    **DEFAULT_PARAMS,
    "num_leaves": 15,
    "min_data_in_leaf": 1,
    "min_sum_hessian_in_leaf": 0,
    "bagging_fraction": 1.0,
    "bagging_freq": 0,
    "feature_fraction": 1.0,
}


@dataclass
class FitResult:
    booster: lgb.Booster
    objective: str
    params: dict[str, Any]
    best_iteration: int
    val_recall10: float


def _prepare_xy(
    frame: pl.DataFrame, schema: FeaturesSchema
) -> tuple[np.ndarray, np.ndarray, pl.DataFrame]:
    mask = training_mask(frame)
    sub = frame.filter(pl.Series(mask))
    # Contiguous by change for lambdarank
    sub = sub.sort(["repo", "commit_idx", "change", "test"])
    X = feature_matrix(sub, schema.feature_names)
    y = sub["new_fail"].to_numpy().astype(np.float64)
    return X, y, sub


def _group_sizes(frame: pl.DataFrame) -> list[int]:
    # Assert contiguity
    changes = frame["change"].to_list()
    repos = frame["repo"].to_list()
    keys = list(zip(repos, changes, strict=True))
    for i in range(1, len(keys)):
        if keys[i] != keys[i - 1] and keys[i] in set(keys[:i]):
            raise AssertionError("change rows not contiguous for lambdarank group")
    sizes: list[int] = []
    i = 0
    n = len(keys)
    while i < n:
        j = i + 1
        while j < n and keys[j] == keys[i]:
            j += 1
        sizes.append(j - i)
        i = j
    return sizes


def _truncation_level(group_sizes: list[int]) -> int:
    if not group_sizes:
        return 10
    med = int(np.median(group_sizes))
    return max(1, int(round(0.10 * med)))


def fit_binary(
    train: pl.DataFrame,
    val: pl.DataFrame,
    schema: FeaturesSchema,
    seed: int,
) -> FitResult:
    Xtr, ytr, _ = _prepare_xy(train, schema)
    Xva, yva, sub_va = _prepare_xy(val, schema)
    n_pos = max(float(ytr.sum()), 1.0)
    n_neg = max(float(len(ytr) - ytr.sum()), 1.0)
    params = {
        **DEFAULT_PARAMS,
        "objective": "binary",
        "metric": "auc",
        "seed": seed,
        "bagging_seed": seed,
        "feature_fraction_seed": seed,
        "data_random_seed": seed,
        "scale_pos_weight": n_neg / n_pos,
    }
    dtrain = lgb.Dataset(Xtr, label=ytr, feature_name=list(schema.feature_names), free_raw_data=False)
    dval = lgb.Dataset(Xva, label=yva, reference=dtrain, free_raw_data=False)
    booster = lgb.train(
        params,
        dtrain,
        num_boost_round=500,
        valid_sets=[dval],
        valid_names=["val"],
        callbacks=[lgb.early_stopping(50, verbose=False), lgb.log_evaluation(period=0)],
    )
    scores = booster.predict(Xva, raw_score=True)
    per = _per_change_from_frame(sub_va, scores)
    return FitResult(
        booster=booster,
        objective="binary",
        params=params,
        best_iteration=int(booster.best_iteration or booster.current_iteration()),
        val_recall10=macro_mean(per, "recall@10%"),
    )


def fit_lambdarank(
    train: pl.DataFrame,
    val: pl.DataFrame,
    schema: FeaturesSchema,
    seed: int,
) -> FitResult:
    Xtr, ytr, sub_tr = _prepare_xy(train, schema)
    Xva, yva, sub_va = _prepare_xy(val, schema)
    gtr = _group_sizes(sub_tr)
    gva = _group_sizes(sub_va)
    trunc = _truncation_level(gtr)
    params = {
        **DEFAULT_PARAMS,
        "objective": "lambdarank",
        "metric": "ndcg",
        "eval_at": [trunc],
        "lambdarank_truncation_level": trunc,
        "seed": seed,
        "bagging_seed": seed,
        "feature_fraction_seed": seed,
        "data_random_seed": seed,
        "label_gain": list(range(max(2, int(ytr.max()) + 1))),
    }
    dtrain = lgb.Dataset(
        Xtr, label=ytr, group=gtr, feature_name=list(schema.feature_names), free_raw_data=False
    )
    dval = lgb.Dataset(
        Xva, label=yva, group=gva, reference=dtrain, free_raw_data=False
    )
    booster = lgb.train(
        params,
        dtrain,
        num_boost_round=500,
        valid_sets=[dval],
        valid_names=["val"],
        callbacks=[lgb.early_stopping(50, verbose=False), lgb.log_evaluation(period=0)],
    )
    scores = booster.predict(Xva, raw_score=True)
    per = _per_change_from_frame(sub_va, scores)
    return FitResult(
        booster=booster,
        objective="lambdarank",
        params=params,
        best_iteration=int(booster.best_iteration or booster.current_iteration()),
        val_recall10=macro_mean(per, "recall@10%"),
    )


def select_objective(
    train: pl.DataFrame,
    val: pl.DataFrame,
    schema: FeaturesSchema,
    seed: int,
) -> FitResult:
    b = fit_binary(train, val, schema, seed)
    try:
        r = fit_lambdarank(train, val, schema, seed)
    except Exception:
        return b
    if r.val_recall10 > b.val_recall10:
        return r
    return b


def _per_change_from_frame(frame: pl.DataFrame, scores: np.ndarray) -> list[dict[str, float]]:
    out: list[dict[str, float]] = []
    frame = frame.with_columns(pl.Series("_score", scores))
    for _k, grp in frame.group_by(["repo", "change"], maintain_order=True):
        grp = grp.filter(~pl.col("parent_missing"))
        y = grp["new_fail"].to_numpy()
        if int(y.sum()) == 0:
            continue
        out.append(change_metrics(y, grp["_score"].to_numpy()))
    return out


def predict_raw(booster: lgb.Booster, frame: pl.DataFrame, schema: FeaturesSchema) -> np.ndarray:
    X = feature_matrix(frame, schema.feature_names)
    return np.asarray(booster.predict(X, raw_score=True), dtype=np.float64)


def fit_reference_model(
    frame: pl.DataFrame, schema: FeaturesSchema, seed: int = 42
) -> lgb.Booster:
    """Fixed 50-round model for week-4 parity fixtures (no early stopping)."""
    X, y, sub = _prepare_xy(frame, schema)
    params = {
        **REFERENCE_PARAMS,
        "objective": "binary",
        "metric": "auc",
        "seed": seed,
        "bagging_seed": seed,
        "feature_fraction_seed": seed,
        "data_random_seed": seed,
        "scale_pos_weight": max((len(y) - y.sum()), 1.0) / max(y.sum(), 1.0),
    }
    dtrain = lgb.Dataset(X, label=y, feature_name=list(schema.feature_names))
    return lgb.train(params, dtrain, num_boost_round=50)


def per_change_metrics_for_scores(
    frame: pl.DataFrame,
    scores: np.ndarray,
    label_col: str = "new_fail",
) -> list[dict[str, float]]:
    tmp = frame.with_columns(pl.Series("_score", scores))
    out: list[dict[str, float]] = []
    for _k, grp in tmp.group_by(["repo", "change"], maintain_order=True):
        if label_col == "new_fail":
            grp = grp.filter(~pl.col("parent_missing"))
        y = grp[label_col].to_numpy()
        if int(y.sum()) == 0:
            continue
        out.append(change_metrics(y, grp["_score"].to_numpy()))
    return out
