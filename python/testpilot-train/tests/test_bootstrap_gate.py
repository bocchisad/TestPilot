"""T4: synth-TEST cluster gate → verdict / ranker (R1/R2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from testpilot_train.metrics import MetricPoint, useful_by_paired
from testpilot_train.schema import FeaturesSchema
from testpilot_train.synth import generate_synthetic_parquet
from testpilot_train.train_lib import decide_bootstrap_verdict, train


def test_useful_by_paired_accepts_ci_low_equal_zero() -> None:
    assert useful_by_paired(MetricPoint(0.0, 0.0, 0.1, 5)) is True
    assert useful_by_paired(MetricPoint(-0.01, -0.02, 0.0, 5)) is False
    assert useful_by_paired(MetricPoint(0.1, 0.01, 0.2, 0)) is False


def test_decide_insufficient_test_C() -> None:
    v, r, reason = decide_bootstrap_verdict(
        n_C=9, n_mutants=40, ci_low=0.05, data_sufficient=False
    )
    assert v == "use_baseline"
    assert r == "cov_lines"
    assert reason == "insufficient_test_C"


def test_decide_use_model_when_ci_low_ge_0_and_sufficient() -> None:
    v, r, reason = decide_bootstrap_verdict(
        n_C=10, n_mutants=20, ci_low=0.0, data_sufficient=True
    )
    assert v == "use_model"
    assert r == "model"
    assert "ci_low" in reason


def test_decide_use_baseline_when_ci_low_negative() -> None:
    v, r, reason = decide_bootstrap_verdict(
        n_C=12, n_mutants=25, ci_low=-0.01, data_sufficient=True
    )
    assert v == "use_baseline"
    assert r == "cov_lines"
    assert reason == "synth_test_ci_low_lt_0"


def test_train_writes_ranker_and_bootstrap_block(
    tmp_path: Path, features_schema: FeaturesSchema
) -> None:
    path = tmp_path / "pairs.parquet"
    # Tiny synth → expect insufficient_test_C / data_insufficient → use_baseline.
    generate_synthetic_parquet(path, features_schema, n_changes=24, n_tests=20, seed=3)
    schema_path = Path(__file__).resolve().parents[3] / "features_schema.json"
    out = tmp_path / "model"
    payload = train([path], out, schema_path, seed=0)
    assert "ranker" in payload
    assert payload["ranker"] in ("model", "cov_lines")
    boot = payload.get("bootstrap") or {}
    assert boot.get("verdict") in ("use_model", "use_baseline")
    assert boot.get("gate") == "synthetic_test_vs_cov_lines_cluster_C"
    assert "reason" in boot
    assert (out / "model.json").is_file()
    raw = (out / "model.json").read_text(encoding="utf-8")
    assert '"ranker"' in raw
    assert '"bootstrap"' in raw
    # Val foil remains report-only; gate cites test.
    assert "paired_delta_ci_synthetic_test_cov_lines" in payload or boot[
        "verdict"
    ] == "use_baseline"
