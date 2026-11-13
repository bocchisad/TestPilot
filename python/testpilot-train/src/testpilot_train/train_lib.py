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
    MetricPoint,
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


def _metric_block(
    per: list[dict[str, float]],
    seed: int,
    cluster_ids: list[str] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in (
        "recall@5%",
        "recall@10%",
        "recall@20%",
        "apfd",
        "first_fail_frac",
        "all_fail_frac",
    ):
        pt = bootstrap_ci(per, key, seed=seed, cluster_ids=cluster_ids)
        out[key] = {
            "mean": pt.mean,
            "ci95": [pt.ci_low, pt.ci_high],
            "n_changes": pt.n_changes,
        }
    return out


def _cluster_id(frame: pl.DataFrame, repo: str, change: str) -> str:
    sub = frame.filter((pl.col("repo") == repo) & (pl.col("change") == change))
    if sub.height == 0:
        return f"{repo}:{change}"
    if "synthetic" in sub.columns and int(sub["synthetic"].max() or 0) == 1:
        bc = sub["base_change"][0]
        if bc:
            return f"{repo}:{bc}"
    return f"{repo}:{change}"


def _cluster_ids_for_keys(
    frame: pl.DataFrame, keys: list[tuple[str, str]]
) -> list[str]:
    return [_cluster_id(frame, r, c) for r, c in keys]


def _mutant_test_stats(test_df: pl.DataFrame) -> tuple[int, int]:
    """Mutant changes with ≥1 new_fail, and distinct base_change among them."""
    if "synthetic" not in test_df.columns:
        return 0, 0
    mut = test_df.filter(pl.col("synthetic") == 1)
    ch = (
        mut.group_by(["repo", "change"])
        .agg(pl.col("new_fail").sum().alias("n"))
        .filter(pl.col("n") > 0)
    )
    n_changes = ch.height
    if "base_change" not in mut.columns:
        return n_changes, 0
    meta = mut.select(["repo", "change", "base_change"]).unique()
    joined = ch.join(meta, on=["repo", "change"], how="left")
    n_bases = int(
        joined.filter(pl.col("base_change").is_not_null() & (pl.col("base_change") != ""))
        .select("base_change")
        .unique()
        .height
    )
    return n_changes, n_bases


def _baseline_keyed_for_slice(
    slice_df: pl.DataFrame, name: str
) -> dict[tuple[str, str], dict[str, float]]:
    keyed: dict[tuple[str, str], dict[str, float]] = {}
    for keys, grp in slice_df.group_by(["repo", "change"], maintain_order=True):
        repo, change = keys  # type: ignore[misc]
        g = grp.filter(~pl.col("parent_missing"))
        y = g["new_fail"].to_numpy()
        if int(y.sum()) == 0:
            continue
        keyed[(str(repo), str(change))] = change_metrics(y, baseline_scores(g, name))
    return keyed


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


def _high_fail_change_keys(frame: pl.DataFrame) -> set[tuple[str, str]]:
    """Synthetic changes where >50% of tests fail/error (label=1)."""
    if "synthetic" not in frame.columns or frame.height == 0:
        return set()
    syn = frame.filter(pl.col("synthetic") == 1)
    if syn.height == 0:
        return set()
    rates = syn.group_by(["repo", "change"]).agg(
        (pl.col("label") == 1).sum().alias("n_fail"),
        pl.len().alias("n"),
    )
    out: set[tuple[str, str]] = set()
    for row in rates.iter_rows(named=True):
        if row["n"] > 0 and (row["n_fail"] / row["n"]) > 0.5:
            out.add((str(row["repo"]), str(row["change"])))
    return out


def _exclude_changes(frame: pl.DataFrame, keys: set[tuple[str, str]]) -> pl.DataFrame:
    if not keys or frame.height == 0:
        return frame
    mask = [
        (r, c) not in keys
        for r, c in zip(frame["repo"].to_list(), frame["change"].to_list(), strict=True)
    ]
    return frame.filter(pl.Series(mask))


def _feature_uniqueness_report(
    frame: pl.DataFrame, feature_names: tuple[str, ...]
) -> list[dict[str, Any]]:
    """Per-feature unique share + sentinel share (synthetic slice)."""
    sentinels = {"imp_dist": 99, "pf_max": -1.0, "pq_max": -1.0, "dur": -1}
    if frame.height == 0:
        return []
    out: list[dict[str, Any]] = []
    n = frame.height
    for name in feature_names:
        s = frame[name]
        nuniq = int(s.n_unique())
        sent = sentinels.get(name)
        if sent is None:
            sent_share = 0.0
        else:
            sent_share = float((s == sent).sum()) / n
        out.append(
            {
                "name": name,
                "n_unique": nuniq,
                "unique_share": nuniq / n,
                "sentinel": sent,
                "sentinel_share": sent_share,
            }
        )
    out.sort(key=lambda r: (r["unique_share"], -r["sentinel_share"]))
    return out


def _baseline_recall_table(
    frame: pl.DataFrame, seed: int
) -> dict[str, dict[str, Any]]:
    """recall@10% per baseline for a slice (empty if no new_fail changes)."""
    table: dict[str, dict[str, Any]] = {}
    for name in BASELINE_NAMES:
        keyed: dict[tuple[str, str], dict[str, float]] = {}
        for keys, grp in frame.group_by(["repo", "change"], maintain_order=True):
            repo, change = keys  # type: ignore[misc]
            g = grp.filter(~pl.col("parent_missing"))
            y = g["new_fail"].to_numpy()
            if int(y.sum()) == 0:
                continue
            keyed[(str(repo), str(change))] = change_metrics(y, baseline_scores(g, name))
        if not keyed:
            table[name] = {"recall@10%": None, "n": 0}
            continue
        vals = [keyed[k] for k in sorted(keyed)]
        pt = bootstrap_ci(vals, "recall@10%", seed=seed)
        table[name] = {
            "recall@10%": {"mean": pt.mean, "ci95": [pt.ci_low, pt.ci_high]},
            "n": pt.n_changes,
        }
    return table


def train(
    data_paths: list[Path],
    out_dir: Path,
    features_schema: Path,
    seed: int = 42,
    *,
    exclude_high_fail_rate: bool = True,
) -> dict[str, Any]:
    schema = load_features_schema(features_schema)
    loaded = load_parquets(data_paths, schema)
    frame = loaded.frame
    diag = diagnostics(frame)
    high_fail_keys = _high_fail_change_keys(frame)
    frame_gate = (
        _exclude_changes(frame, high_fail_keys) if exclude_high_fail_rate else frame
    )
    plan = chrono_split(frame_gate)
    train_df = filter_slice(frame_gate, plan, "train")
    val_df = filter_slice(frame_gate, plan, "val")
    test_df = filter_slice(frame_gate, plan, "test")
    # Full slices (incl. high-fail) for dual metrics reporting.
    plan_all = chrono_split(frame)
    val_df_all = filter_slice(frame, plan_all, "val")
    test_df_all = filter_slice(frame, plan_all, "test")

    fit, candidates = select_objective(train_df, val_df, schema, seed)

    val_scores = predict_raw(fit.booster, val_df, schema)
    test_scores = predict_raw(fit.booster, test_df, schema)
    val_keyed = _per_change_keyed(val_df, val_scores, "new_fail")
    test_keyed = _per_change_keyed(test_df, test_scores, "new_fail")
    val_keys = sorted(val_keyed)
    test_keys = sorted(test_keyed)
    val_per = [val_keyed[k] for k in val_keys]
    test_per = [test_keyed[k] for k in test_keys]
    val_clusters = _cluster_ids_for_keys(val_df, val_keys)
    test_clusters = _cluster_ids_for_keys(test_df, test_keys)

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
    align_keys = sorted(set(val_keyed) & set(baseline_val_keyed[best_name]))
    align_clusters = _cluster_ids_for_keys(val_df, align_keys)
    delta_val = paired_delta_ci(
        m_val, b_val, "recall@10%", seed=seed, cluster_ids=align_clusters
    )

    cov_val_keyed = _baseline_keyed_for_slice(val_df, "cov_lines")
    m_cov, b_cov = _align_per(val_keyed, cov_val_keyed)
    cov_keys = sorted(set(val_keyed) & set(cov_val_keyed))
    delta_val_cov = paired_delta_ci(
        m_cov,
        b_cov,
        "recall@10%",
        seed=seed + 11,
        cluster_ids=_cluster_ids_for_keys(val_df, cov_keys),
    )

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
    test_align_keys = sorted(set(test_keyed) & set(base_test_keyed))
    delta_test = paired_delta_ci(
        m_test,
        b_test,
        "recall@10%",
        seed=seed + 1,
        cluster_ids=_cluster_ids_for_keys(test_df, test_align_keys),
    )

    n_test_new = len(test_keyed)
    has_synthetic = "synthetic" in frame.columns and int(
        frame.filter(pl.col("synthetic") == 1).height
    ) > 0
    # When any synthetic rows exist, gate sufficiency on synthetic test slice (M3).
    if has_synthetic:
        n_mut, n_bases = _mutant_test_stats(test_df)
        status = "ok" if (n_mut >= 20 and n_bases >= 10) else "data_insufficient"
    else:
        status = "ok" if n_test_new >= 20 else "data_insufficient"

    useful_on_val_label = "useful_on_real_val" if has_synthetic else "useful_on_val"
    # Real useful-gate: null when there are no real new_fail changes on val.
    val_real_for_gate = (
        val_df.filter(pl.col("synthetic") == 0)
        if has_synthetic and "synthetic" in val_df.columns
        else val_df
    )
    n_real_val_new = int(
        val_real_for_gate.group_by(["repo", "change"])
        .agg(pl.col("new_fail").sum().alias("n"))
        .filter(pl.col("n") > 0)
        .height
    )
    if has_synthetic and n_real_val_new == 0:
        useful_on_val = None
    else:
        useful_on_val = useful_by_paired(delta_val)

    # Synthetic useful-gate vs cov_lines — always when synthetic rows exist (P2).
    val_syn_df = (
        val_df.filter(pl.col("synthetic") == 1)
        if "synthetic" in val_df.columns
        else val_df.clear()
    )
    useful_on_synthetic_val: bool | None = False
    delta_synthetic_val_cov: MetricPoint | None = None
    if val_syn_df.height > 0:
        syn_scores = predict_raw(fit.booster, val_syn_df, schema)
        syn_keyed = _per_change_keyed(val_syn_df, syn_scores, "new_fail")
        cov_syn = _baseline_keyed_for_slice(val_syn_df, "cov_lines")
        m_s, b_s = _align_per(syn_keyed, cov_syn)
        syn_keys = sorted(set(syn_keyed) & set(cov_syn))
        if m_s:
            delta_synthetic_val_cov = paired_delta_ci(
                m_s,
                b_s,
                "recall@10%",
                seed=seed + 12,
                cluster_ids=_cluster_ids_for_keys(val_syn_df, syn_keys),
            )
            useful_on_synthetic_val = useful_by_paired(delta_synthetic_val_cov)
        else:
            useful_on_synthetic_val = None

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

    def _slice_metric_payload(df: pl.DataFrame, sc: np.ndarray, boot_seed: int) -> dict[str, Any]:
        if df.height == 0:
            return {}
        keyed = _per_change_keyed(df, sc, "new_fail")
        keys = sorted(keyed)
        if not keys:
            return {}
        return _metric_block(
            [keyed[k] for k in keys],
            boot_seed,
            _cluster_ids_for_keys(df, keys),
        )

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
            "n_orphan_vs_parent": loaded.n_orphan_vs_parent,
            "n_high_fail_rate_changes": len(high_fail_keys),
            "exclude_high_fail_rate": exclude_high_fail_rate,
        },
        "metrics_val_new_fail": _metric_block(val_per, seed, val_clusters),
        "metrics_test_new_fail": _metric_block(test_per, seed + 2, test_clusters),
        "metrics_val_label_with_flaky": _metric_block(
            _label_secondary(val_df, val_scores, drop_flaky=False), seed + 3
        ),
        "metrics_val_label_no_flaky": _metric_block(
            _label_secondary(val_df, val_scores, drop_flaky=True), seed + 4
        ),
        "best_baseline_val": best_name,
        "foil_baseline_mutation_bootstrap": "cov_lines",
        "baselines": {
            "val": _baseline_recall_table(val_df, seed + 20),
            "test": _baseline_recall_table(test_df, seed + 21),
            "val_synthetic": _baseline_recall_table(val_syn_df, seed + 22)
            if val_syn_df.height
            else {},
            "test_synthetic": _baseline_recall_table(
                test_df.filter(pl.col("synthetic") == 1)
                if has_synthetic and "synthetic" in test_df.columns
                else test_df.clear(),
                seed + 23,
            )
            if has_synthetic
            else {},
            "best_for_paired_gate": best_name,
        },
        "paired_delta_ci_val": {
            "mean": delta_val.mean,
            "ci95": [delta_val.ci_low, delta_val.ci_high],
            "n": delta_val.n_changes,
        },
        "paired_delta_ci_val_cov_lines": {
            "mean": delta_val_cov.mean,
            "ci95": [delta_val_cov.ci_low, delta_val_cov.ci_high],
            "n": delta_val_cov.n_changes,
        },
        "paired_delta_ci_test": {
            "mean": delta_test.mean,
            "ci95": [delta_test.ci_low, delta_test.ci_high],
            "n": delta_test.n_changes,
        },
        "useful_on_val": useful_on_val,
        "useful_on_val_label": useful_on_val_label,
        "useful_on_synthetic_val": useful_on_synthetic_val,
        "useful_on_synthetic_val_label": "useful_on_synthetic_val",
        "status": status,
        "feature_gain": feature_gain,
        "feature_value_stats_synthetic": _feature_uniqueness_report(
            frame.filter(pl.col("synthetic") == 1)
            if has_synthetic and "synthetic" in frame.columns
            else frame.clear(),
            schema.feature_names,
        ),
        "sentinel_notes": {"pf_max": -1, "pq_max": -1, "dur": -1, "imp_dist": 99},
        "constraints": {
            "categorical": False,
            "linear_tree": False,
            "num_threads_train": 1,
            "use_missing": True,
            "zero_as_missing": False,
        },
    }
    # Dual metrics: with high-fail included (all) vs excluded (primary gate slices).
    if high_fail_keys:
        val_all_scores = predict_raw(fit.booster, val_df_all, schema)
        test_all_scores = predict_raw(fit.booster, test_df_all, schema)
        payload["metrics_val_new_fail_including_high_fail"] = _slice_metric_payload(
            val_df_all, val_all_scores, seed + 30
        )
        payload["metrics_test_new_fail_including_high_fail"] = _slice_metric_payload(
            test_df_all, test_all_scores, seed + 31
        )
        payload["metrics_val_new_fail_excluding_high_fail"] = payload["metrics_val_new_fail"]
        payload["metrics_test_new_fail_excluding_high_fail"] = payload[
            "metrics_test_new_fail"
        ]
    if "synthetic" in frame.columns:
        val_real_df = val_df.filter(pl.col("synthetic") == 0)
        val_syn_only = val_df.filter(pl.col("synthetic") == 1)
        test_real_df = test_df.filter(pl.col("synthetic") == 0)
        test_syn_df = test_df.filter(pl.col("synthetic") == 1)
        payload["metrics_val_new_fail_real"] = _slice_metric_payload(
            val_real_df, predict_raw(fit.booster, val_real_df, schema), seed + 5
        )
        payload["metrics_val_new_fail_synthetic"] = _slice_metric_payload(
            val_syn_only, predict_raw(fit.booster, val_syn_only, schema), seed + 6
        )
        payload["metrics_test_new_fail_real"] = _slice_metric_payload(
            test_real_df, predict_raw(fit.booster, test_real_df, schema), seed + 7
        )
        payload["metrics_test_new_fail_synthetic"] = _slice_metric_payload(
            test_syn_df, predict_raw(fit.booster, test_syn_df, schema), seed + 8
        )
        if has_synthetic:
            n_mut, n_bases = _mutant_test_stats(test_df)
            payload["diagnostics"]["mutant_test_new_fail_changes"] = n_mut
            payload["diagnostics"]["mutant_test_distinct_base_change"] = n_bases
    if delta_synthetic_val_cov is not None:
        payload["paired_delta_ci_synthetic_val_cov_lines"] = {
            "mean": delta_synthetic_val_cov.mean,
            "ci95": [delta_synthetic_val_cov.ci_low, delta_synthetic_val_cov.ci_high],
            "n": delta_synthetic_val_cov.n_changes,
        }
    if useful_on_val is None and has_synthetic:
        payload["note_real_gate"] = (
            "useful_on_real_val=null: no real new_fail changes on val "
            "(data_insufficient for the real gate)"
        )
    if status == "data_insufficient":
        if has_synthetic:
            payload["note"] = (
                "synthetic data present: test needs ≥20 mutant changes with "
                "≥1 new_fail and ≥10 distinct base_change; metrics shown but no conclusions"
            )
        else:
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
    keyed = _per_change_keyed(eval_df, scores, "new_fail")
    keys = sorted(keyed)
    per = [keyed[k] for k in keys]
    clusters = _cluster_ids_for_keys(eval_df, keys) if keys else None
    return {
        "n_rows": eval_df.height,
        "metrics": _metric_block(per, seed=0, cluster_ids=clusters),
    }
