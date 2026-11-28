import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from audit_real_pair_pytest_runs import audit_pair  # noqa: E402


def test_audit_rejects_interrupted_and_collection_error_logs(tmp_path):
    log_dir = tmp_path / "pair"
    log_dir.mkdir()
    for name in ("base-1.log", "base-2.log", "head-1.log", "head-2.log"):
        (log_dir / name).write_text("10 passed\n", encoding="utf-8")
    (log_dir / "head-1.log").write_text(
        "ERROR collecting tests/test_x.py\n!!!!!!!!!!!!!!!! Interrupted: 21 errors during collection\n",
        encoding="utf-8",
    )
    row = {
        "repo": "jinja",
        "head": "sha",
        "status": "completed",
        "positive": False,
        "shared_base_test_ids": 0,
        "log_dir": str(log_dir),
    }

    result = audit_pair(row)

    assert result["excluded"] is True
    assert result["reasons"] == [
        "no_shared_test_ids",
        "pytest_collection_error",
        "pytest_interrupted",
    ]
    assert result["affected_logs"] == ["head-1.log"]


def test_clean_pair_with_shared_test_ids_is_retained(tmp_path):
    log_dir = tmp_path / "pair"
    log_dir.mkdir()
    for name in ("base-1.log", "base-2.log", "head-1.log", "head-2.log"):
        (log_dir / name).write_text("10 passed\n", encoding="utf-8")

    result = audit_pair(
        {
            "repo": "click",
            "head": "sha",
            "status": "completed",
            "positive": False,
            "shared_base_test_ids": 10,
            "log_dir": str(log_dir),
        }
    )

    assert result["excluded"] is False
    assert result["reasons"] == []
