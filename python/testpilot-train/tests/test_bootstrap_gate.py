"""T4: synth-TEST cluster gate, cold baseline flag, strict JSON."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from testpilot_train.artifact import write_model_json
from testpilot_train.metrics import MetricPoint, useful_by_paired
from testpilot_train.schema import FeaturesSchema
from testpilot_train.split import InsufficientCommitsError
from testpilot_train.synth import generate_synthetic_parquet
from testpilot_train.train_lib import decide_bootstrap_verdict, train

ROOT = Path(__file__).resolve().parents[3]
SCHEMA = ROOT / "features_schema.json"


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


def test_decide_data_insufficient_n_C_ge_10_n_mut_lt_20() -> None:
    v, r, reason = decide_bootstrap_verdict(
        n_C=12, n_mutants=15, ci_low=0.05, data_sufficient=False
    )
    assert v == "use_baseline"
    assert reason == "data_insufficient"
    assert r == "cov_lines"


def test_decide_use_model_when_ci_low_ge_0_and_sufficient() -> None:
    v, r, reason = decide_bootstrap_verdict(
        n_C=10, n_mutants=20, ci_low=0.0, data_sufficient=True
    )
    assert v == "use_model"
    assert r == "model"


def test_decide_use_baseline_when_ci_low_negative() -> None:
    v, r, reason = decide_bootstrap_verdict(
        n_C=12, n_mutants=25, ci_low=-0.01, data_sufficient=True
    )
    assert v == "use_baseline"
    assert reason == "synth_test_ci_low_lt_0"


def test_write_model_json_rejects_nan(tmp_path: Path) -> None:
    path = tmp_path / "model.json"
    write_model_json(
        path,
        {
            "a": float("nan"),
            "b": float("inf"),
            "c": {"ci95": [float("nan"), 1.0]},
            "ok": 1,
        },
    )
    text = path.read_text(encoding="utf-8")
    assert "NaN" not in text
    assert "Infinity" not in text
    loaded = json.loads(text)
    assert loaded["a"] is None
    assert loaded["b"] is None
    assert loaded["c"]["ci95"][0] is None
    assert loaded["ok"] == 1


def test_plain_train_fails_below_10_commit_idx(
    tmp_path: Path, features_schema: FeaturesSchema
) -> None:
    path = tmp_path / "pairs.parquet"
    generate_synthetic_parquet(path, features_schema, n_changes=4, n_tests=10, seed=1)
    with pytest.raises(InsufficientCommitsError, match="commit_idx"):
        train([path], tmp_path / "model", SCHEMA, seed=0, allow_cold_baseline=False)


def test_cold_baseline_with_flag_deterministic(
    tmp_path: Path, features_schema: FeaturesSchema
) -> None:
    path = tmp_path / "pairs.parquet"
    generate_synthetic_parquet(path, features_schema, n_changes=4, n_tests=10, seed=1)
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    pa = train([path], out_a, SCHEMA, seed=0, allow_cold_baseline=True)
    pb = train([path], out_b, SCHEMA, seed=99, allow_cold_baseline=True)
    assert pa["ranker"] == "cov_lines"
    assert pa["bootstrap"]["verdict"] == "use_baseline"
    assert pa["bootstrap"]["reason"] == "insufficient_commit_idx"
    assert not (out_a / "model.txt").exists()
    ha = hashlib.sha256((out_a / "model.json").read_bytes()).hexdigest()
    hb = hashlib.sha256((out_b / "model.json").read_bytes()).hexdigest()
    assert ha == hb


def test_cli_plain_vs_cold_flag(tmp_path: Path, features_schema: FeaturesSchema) -> None:
    import os

    path = tmp_path / "pairs.parquet"
    generate_synthetic_parquet(path, features_schema, n_changes=4, n_tests=8, seed=2)
    # Editable .pth with non-ASCII repo paths is flaky; pin src on PYTHONPATH.
    env = {**os.environ, "PYTHONPATH": str(ROOT / "python/testpilot-train/src")}
    out = tmp_path / "m"
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "testpilot_train.cli",
            "train",
            "--data",
            str(path),
            "--out",
            str(out),
            "--features-schema",
            str(SCHEMA),
            "--seed",
            "0",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert r.returncode != 0
    assert "commit_idx" in (r.stderr + r.stdout)

    out2 = tmp_path / "m2"
    r2 = subprocess.run(
        [
            sys.executable,
            "-m",
            "testpilot_train.cli",
            "train",
            "--data",
            str(path),
            "--out",
            str(out2),
            "--features-schema",
            str(SCHEMA),
            "--seed",
            "0",
            "--allow-cold-baseline",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert r2.returncode == 0, r2.stderr + r2.stdout
    meta = json.loads((out2 / "model.json").read_text(encoding="utf-8"))
    assert meta["bootstrap"]["reason"] == "insufficient_commit_idx"


def _write_synth_mutant_parquet(
    path: Path,
    schema: FeaturesSchema,
    *,
    n_changes: int,
    n_tests: int = 12,
    seed: int = 1,
    n_bases: int = 8,
) -> None:
    """Real-looking synth dump: synthetic=1, base_change cycles over n_bases C's."""
    import polars as pl
    import pyarrow.parquet as pq

    generate_synthetic_parquet(path, schema, n_changes=n_changes, n_tests=n_tests, seed=seed)
    df = pl.read_parquet(path)
    # Map each change to a base_change bucket so cluster-by-C works.
    changes = df.select("change").unique().sort("change")["change"].to_list()
    mapping = {ch: f"{(i % n_bases):040x}" for i, ch in enumerate(changes)}
    df = df.with_columns(
        pl.lit(1).alias("synthetic"),
        pl.col("change").replace(mapping).alias("base_change"),
    )
    table = df.to_arrow().replace_schema_metadata(
        {b"features_version": str(schema.features_version).encode()}
    )
    pq.write_table(table, path)


def test_train_synth_gate_runs_on_synthetic_ones(
    tmp_path: Path, features_schema: FeaturesSchema
) -> None:
    # Enough commit_idx after HF filter; synthetic=1 with few C → not no_synthetic_test.
    path = tmp_path / "pairs.parquet"
    _write_synth_mutant_parquet(
        path, features_schema, n_changes=40, n_tests=10, seed=4, n_bases=8
    )
    out = tmp_path / "model"
    payload = train(
        [path],
        out,
        SCHEMA,
        seed=0,
        allow_cold_baseline=False,
        exclude_high_fail_rate=False,
    )
    assert payload["bootstrap"]["reason"] != "no_synthetic_test"
    assert payload["bootstrap"]["verdict"] in ("use_model", "use_baseline")
    assert payload["ranker"] in ("model", "cov_lines")
    # 8 bases → n_C on test usually <10 or data_insufficient
    assert payload["bootstrap"]["reason"] in (
        "insufficient_test_C",
        "data_insufficient",
        "synth_test_ci_low_lt_0",
        "synth_test_ci_low_ge_0",
        "no_test_signal",
    )
    text = (out / "model.json").read_text(encoding="utf-8")
    assert "NaN" not in text
    assert json.loads(text)


def test_decide_covers_data_insufficient_threshold() -> None:
    """n_C≥10 and n_mut<20 ⇒ data_insufficient (status path), not insufficient_test_C."""
    v, _r, reason = decide_bootstrap_verdict(
        n_C=10, n_mutants=19, ci_low=0.2, data_sufficient=False
    )
    assert v == "use_baseline"
    assert reason == "data_insufficient"


def test_train_writes_ranker_no_synthetic_still_baseline(
    tmp_path: Path, features_schema: FeaturesSchema
) -> None:
    path = tmp_path / "pairs.parquet"
    generate_synthetic_parquet(path, features_schema, n_changes=24, n_tests=20, seed=3)
    out = tmp_path / "model"
    payload = train([path], out, SCHEMA, seed=0)
    assert payload["ranker"] == "cov_lines"
    assert payload["bootstrap"]["verdict"] == "use_baseline"
    assert payload["bootstrap"]["reason"] == "no_synthetic_test"
