from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from testpilot_train.data import load_parquets
from testpilot_train.model import predict_raw, select_objective
from testpilot_train.schema import FeaturesSchema
from testpilot_train.split import chrono_split, loro_train_val
from testpilot_train.synth import generate_synthetic_parquet
from testpilot_train.train_lib import _run_loro


def _two_repo_parquet(path: Path, schema: FeaturesSchema) -> Path:
    a = path.parent / "a.parquet"
    b = path.parent / "b.parquet"
    generate_synthetic_parquet(a, schema, n_changes=20, n_tests=30, seed=1)
    generate_synthetic_parquet(b, schema, n_changes=20, n_tests=30, seed=2)
    ta = pq.read_table(a)
    tb = pq.read_table(b)
    # rewrite repo column
    def with_repo(table, repo: str):
        cols = table.to_pydict()
        cols["repo"] = [repo] * len(cols["repo"])
        arrays = {k: pa.array(v, type=table.schema.field(k).type) for k, v in cols.items()}
        return pa.table(arrays).replace_schema_metadata(table.schema.metadata)

    out = pa.concat_tables([with_repo(ta, "repo_a"), with_repo(tb, "repo_b")])
    pq.write_table(out, path, compression="snappy")
    return path


def test_loro_two_repos(tmp_path: Path, features_schema: FeaturesSchema):
    path = tmp_path / "two.parquet"
    _two_repo_parquet(path, features_schema)
    frame = load_parquets([path], features_schema).frame
    plan = chrono_split(frame, min_changes=10)
    assert len(plan.by_repo) == 2
    tr, va, te = loro_train_val(frame, plan, "repo_a")
    assert set(tr["repo"].unique().to_list()) == {"repo_b"}
    assert set(va["repo"].unique().to_list()) == {"repo_b"}
    assert set(te["repo"].unique().to_list()) == {"repo_a"}
    fit, _ = select_objective(tr, va, features_schema, seed=0)
    scores = predict_raw(fit.booster, te, features_schema)
    assert scores.shape[0] == te.height

    loro = _run_loro(frame, plan, features_schema, seed=0)
    assert loro["skipped"] is False
    assert len(loro["per_repo"]) == 2
    assert "macro_recall@10%" in loro
    # macro is finite or nan if no new_fail in held-out tests — still a float
    assert isinstance(loro["macro_recall@10%"], float)
    for row in loro["per_repo"]:
        bases = row.get("baselines") or {}
        assert set(bases) >= {"cov_lines", "cc_max", "random"}
        for v in bases.values():
            assert isinstance(v, float)
