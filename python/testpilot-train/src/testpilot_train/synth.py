"""Synthetic parquet generator for leakage tests (same schema)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from testpilot_train.schema import FeaturesSchema

_INT_FEATURES = {
    "imp_dist",
    "imp_direct",
    "name_file",
    "name_func",
    "cov_known",
    "cov_file",
    "cov_func",
    "cov_lines",
    "cov_breadth",
    "cov_mod",
    "cov_lag",
    "t_fail_cnt",
    "t_obs",
    "dur",
    "tfile_ntests",
    "t_changed",
    "ch_nfiles",
    "ch_nsrc",
    "ch_add",
    "ch_rm",
    "ch_nfunc",
}


def generate_synthetic_parquet(
    path: Path,
    schema: FeaturesSchema,
    *,
    n_changes: int = 200,
    n_tests: int = 200,
    seed: int = 0,
) -> Path:
    rng = np.random.default_rng(seed)
    cols: dict[str, list] = {c: [] for c in schema.column_names}

    for ci in range(n_changes):
        change = f"{ci:040x}"
        parent = f"{max(ci - 1, 0):040x}"
        sig_a = rng.normal(size=n_tests)
        sig_b = rng.normal(size=n_tests)
        logits = 1.5 * sig_a - 0.8 * sig_b
        parent_fail = rng.random(n_tests) < 0.08
        new = (rng.random(n_tests) < 1 / (1 + np.exp(-logits))) & (~parent_fail)
        label = new | parent_fail

        for t in range(n_tests):
            cols["repo"].append("synth")
            cols["change"].append(change)
            cols["parent"].append(parent)
            cols["commit_idx"].append(ci)
            cols["time"].append(1_700_000_000 + ci)
            cols["test"].append(f"tests/test_{t:03d}.py::test_x")
            cols["label"].append(int(label[t]))
            cols["flaky"].append(0)
            if "synthetic" in cols:
                cols["synthetic"].append(0)
            if "base_change" in cols:
                cols["base_change"].append("")
            for name in schema.feature_names:
                if name == "cc_max":
                    cols[name].append(float(sig_a[t]))
                elif name == "name_jac":
                    cols[name].append(float(sig_b[t]))
                elif name == "imp_dist":
                    cols[name].append(99 if rng.random() < 0.3 else int(rng.integers(1, 6)))
                elif name in ("pf_max", "pq_max"):
                    cols[name].append(-1.0 if rng.random() < 0.2 else float(rng.random()))
                elif name == "dur":
                    cols[name].append(-1 if rng.random() < 0.1 else int(rng.integers(1, 50)))
                elif name in _INT_FEATURES:
                    cols[name].append(int(rng.integers(0, 5)))
                else:
                    cols[name].append(float(rng.random()))

    arrays = {}
    for name in schema.column_names:
        if name in ("repo", "change", "parent", "test", "base_change"):
            arrays[name] = pa.array(cols[name], type=pa.string())
        elif (
            name in ("commit_idx", "time", "label", "flaky", "synthetic")
            or name in _INT_FEATURES
        ):
            arrays[name] = pa.array(cols[name], type=pa.int64())
        else:
            arrays[name] = pa.array(cols[name], type=pa.float64())

    table = pa.table(arrays).replace_schema_metadata(
        {b"features_version": str(schema.features_version).encode()}
    )
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path, compression="snappy")
    return path
