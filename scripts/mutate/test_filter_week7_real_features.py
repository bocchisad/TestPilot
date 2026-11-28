import importlib.util
from pathlib import Path

import pyarrow as pa

SCRIPT = Path(__file__).resolve().parents[1] / "filter_week7_real_features.py"
SPEC = importlib.util.spec_from_file_location("filter_week7_real_features", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
filter_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(filter_module)


def test_filter_drops_audited_heads_and_preserves_parquet_schema_metadata():
    table = pa.table(
        {
            "repo": ["click", "click", "jinja"],
            "change": ["bad", "good", "ok"],
            "label": [0, 1, 0],
        }
    ).replace_schema_metadata({b"features_version": b"2"})

    filtered, excluded_rows = filter_module.filter_table(
        table, {("click", "bad")}
    )

    assert excluded_rows == 1
    assert filtered.num_rows == 2
    assert filtered["change"].to_pylist() == ["good", "ok"]
    assert filtered.schema.metadata == {b"features_version": b"2"}
