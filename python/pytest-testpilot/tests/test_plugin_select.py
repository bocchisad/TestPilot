"""pytest-testpilot select / shadow / fail-open."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_SRC = Path(__file__).resolve().parents[1] / "src"


def _run_pytest(
    root: Path,
    *args: str,
    extra_env: dict[str, str] | None = None,
    timeout: float = 120,
) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items()}
    env["COVERAGE_CORE"] = "ctrace"
    src = str(_PLUGIN_SRC)
    prev = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = src if not prev else src + os.pathsep + prev
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "pytest", *args],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


def _git_init(root: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)


def _init_db(root: Path) -> None:
    from pytest_testpilot.store import SCHEMA_VERSION, Store

    db = root / ".testpilot" / "db.sqlite"
    store = Store(db)
    assert SCHEMA_VERSION == 7
    store.close()


def _write_fake_bin(root: Path, body: str) -> Path:
    path = root / "fake_testpilot"
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _select_json(payload: dict) -> str:
    return textwrap.dedent(
        f"""\
        #!/usr/bin/env {sys.executable}
        import json
        print(json.dumps({payload!r}))
        """
    )


@pytest.fixture
def select_repo(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "tests").mkdir()
    (root / "tests" / "test_a.py").write_text(
        "def test_one():\n    assert True\n\ndef test_two():\n    assert True\n",
        encoding="utf-8",
    )
    (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    _git_init(root)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "i"], cwd=root, check=True, capture_output=True)
    (root / "model").mkdir()
    _init_db(root)
    return root


def test_select_happy_deselects(select_repo: Path) -> None:
    payload = {
        "model_sha256": "dead",
        "features_version": 2,
        "base": "aaa",
        "head": "bbb",
        "base_run_sha": "aaa",
        "n_candidates": 2,
        "n_selected": 1,
        "n_forced": 0,
        "n_safe_added": 0,
        "n_ranked": 1,
        "budget": {"kind": "frac", "value": 0.5},
        "safe": {"enabled": True, "fallback_all": False, "reasons": [], "thresholds": {}},
        "tests": [
            {
                "test_id": "tests/test_a.py::test_one",
                "score": 1.0,
                "rank": 1,
                "dur": 10,
                "reason": "ranked",
            }
        ],
    }
    fake = _write_fake_bin(select_repo, _select_json(payload))
    proc = _run_pytest(
        select_repo,
        "--testpilot",
        "--testpilot-select",
        "--testpilot-budget-frac=0.5",
        "--testpilot-model",
        str(select_repo / "model"),
        extra_env={"TESTPILOT_BIN": str(fake)},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "deselected=1" in proc.stdout + proc.stderr
    db = sqlite3.connect(select_repo / ".testpilot" / "db.sqlite")
    partial = db.execute("SELECT partial, meta FROM test_runs ORDER BY id DESC LIMIT 1").fetchone()
    assert int(partial[0]) == 1
    meta = json.loads(partial[1])
    assert "plugin_error" not in meta
    ids = {r[0] for r in db.execute("SELECT t.test_id FROM test_results tr JOIN tests t ON t.id=tr.test_pk")}
    assert ids == {"tests/test_a.py::test_one"}
    sel = db.execute("SELECT COUNT(*) FROM test_run_selection").fetchone()[0]
    assert int(sel) == 2


def test_fail_open_no_bin(select_repo: Path) -> None:
    # Isolate PATH so a real `testpilot` on PATH cannot mask an explicit miss.
    proc = _run_pytest(
        select_repo,
        "--testpilot",
        "--testpilot-select",
        "--testpilot-budget-frac=0.5",
        "--testpilot-model",
        str(select_repo / "model"),
        extra_env={
            "TESTPILOT_BIN": "/nonexistent/testpilot",
            "PATH": "/usr/bin:/bin",
        },
    )
    assert proc.returncode == 0
    assert "plugin_error(no_bin)" in proc.stdout + proc.stderr
    db = sqlite3.connect(select_repo / ".testpilot" / "db.sqlite")
    assert int(db.execute("SELECT COUNT(*) FROM test_run_selection").fetchone()[0]) == 0
    meta = json.loads(db.execute("SELECT meta FROM test_runs").fetchone()[0])
    assert meta.get("plugin_error") == "no_bin"


def test_fail_open_bad_json(select_repo: Path) -> None:
    fake = _write_fake_bin(
        select_repo,
        f"#!{sys.executable}\nprint('not json')\n",
    )
    proc = _run_pytest(
        select_repo,
        "--testpilot",
        "--testpilot-select",
        "--testpilot-budget-frac=0.5",
        "--testpilot-model",
        str(select_repo / "model"),
        extra_env={"TESTPILOT_BIN": str(fake)},
    )
    assert proc.returncode == 0
    assert "plugin_error(json)" in proc.stdout + proc.stderr


def test_fail_open_timeout_default_10s(select_repo: Path) -> None:
    fake = _write_fake_bin(
        select_repo,
        f"#!{sys.executable}\nimport time\ntime.sleep(11)\n",
    )
    proc = _run_pytest(
        select_repo,
        "--testpilot",
        "--testpilot-select",
        "--testpilot-budget-frac=0.5",
        "--testpilot-model",
        str(select_repo / "model"),
        extra_env={"TESTPILOT_BIN": str(fake)},
        timeout=60,
    )
    assert proc.returncode == 0
    out = proc.stdout + proc.stderr
    assert "plugin_error(timeout)" in out
    db = sqlite3.connect(select_repo / ".testpilot" / "db.sqlite")
    assert int(db.execute("SELECT COUNT(*) FROM test_results").fetchone()[0]) == 2


def test_param_bare_expands(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "tests").mkdir()
    (root / "tests" / "test_p.py").write_text(
        "import pytest\n@pytest.mark.parametrize('x',[1,2])\ndef test_p(x):\n    assert x\n",
        encoding="utf-8",
    )
    _git_init(root)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "i"], cwd=root, check=True, capture_output=True)
    (root / "model").mkdir()
    _init_db(root)
    payload = {
        "model_sha256": "d",
        "n_candidates": 2,
        "n_selected": 1,
        "budget": {"kind": "count", "value": 1},
        "safe": {"enabled": True, "fallback_all": False, "reasons": []},
        "tests": [{"test_id": "tests/test_p.py::test_p", "score": 1.0, "rank": 1, "dur": 1, "reason": "ranked"}],
    }
    fake = _write_fake_bin(root, _select_json(payload))
    proc = _run_pytest(
        root,
        "--testpilot",
        "--testpilot-select",
        "--testpilot-max-tests=1",
        "--testpilot-model",
        str(root / "model"),
        extra_env={"TESTPILOT_BIN": str(fake)},
    )
    assert proc.returncode == 0
    db = sqlite3.connect(root / ".testpilot" / "db.sqlite")
    ids = {r[0] for r in db.execute("SELECT t.test_id FROM test_results tr JOIN tests t ON t.id=tr.test_pk")}
    assert ids == {"tests/test_p.py::test_p[1]", "tests/test_p.py::test_p[2]"}


def test_unknown_to_select_kept(select_repo: Path) -> None:
    (select_repo / "tests" / "test_extra.py").write_text(
        "def test_extra():\n    assert True\n", encoding="utf-8"
    )
    payload = {
        "model_sha256": "d",
        "n_candidates": 2,
        "n_selected": 1,
        "budget": {"kind": "frac", "value": 0.5},
        "safe": {"enabled": True, "fallback_all": False, "reasons": []},
        "tests": [{"test_id": "tests/test_a.py::test_one", "score": 1.0, "rank": 1, "dur": 1, "reason": "ranked"}],
    }
    fake = _write_fake_bin(select_repo, _select_json(payload))
    proc = _run_pytest(
        select_repo,
        "--testpilot",
        "--testpilot-select",
        "--testpilot-budget-frac=0.5",
        "--testpilot-model",
        str(select_repo / "model"),
        extra_env={"TESTPILOT_BIN": str(fake)},
    )
    assert proc.returncode == 0
    assert "unknown=1" in proc.stdout + proc.stderr
    db = sqlite3.connect(select_repo / ".testpilot" / "db.sqlite")
    ids = {r[0] for r in db.execute("SELECT t.test_id FROM test_results tr JOIN tests t ON t.id=tr.test_pk")}
    assert "tests/test_extra.py::test_extra" in ids
    assert "tests/test_a.py::test_two" not in ids


def test_shadow_runs_all_partial_zero(select_repo: Path) -> None:
    payload = {
        "model_sha256": "d",
        "n_candidates": 2,
        "n_selected": 1,
        "budget": {"kind": "frac", "value": 0.5},
        "safe": {"enabled": True, "fallback_all": False, "reasons": []},
        "tests": [{"test_id": "tests/test_a.py::test_one", "score": 1.0, "rank": 1, "dur": 1, "reason": "ranked"}],
    }
    fake = _write_fake_bin(select_repo, _select_json(payload))
    proc = _run_pytest(
        select_repo,
        "--testpilot",
        "--testpilot-select",
        "--testpilot-shadow",
        "--testpilot-budget-frac=0.5",
        "--testpilot-model",
        str(select_repo / "model"),
        extra_env={"TESTPILOT_BIN": str(fake)},
    )
    assert proc.returncode == 0
    db = sqlite3.connect(select_repo / ".testpilot" / "db.sqlite")
    assert int(db.execute("SELECT partial FROM test_runs").fetchone()[0]) == 0
    assert int(db.execute("SELECT COUNT(*) FROM test_results").fetchone()[0]) == 2
    assert int(db.execute("SELECT COUNT(*) FROM test_run_selection").fetchone()[0]) == 2


def test_xdist_select_once(select_repo: Path) -> None:
    pytest.importorskip("xdist")
    payload = {
        "model_sha256": "d",
        "n_candidates": 2,
        "n_selected": 2,
        "budget": {"kind": "frac", "value": 1.0},
        "safe": {"enabled": True, "fallback_all": True, "reasons": ["few_candidates"]},
        "tests": [
            {"test_id": "tests/test_a.py::test_one", "score": 1.0, "rank": 1, "dur": 1, "reason": "fallback_all"},
            {"test_id": "tests/test_a.py::test_two", "score": 1.0, "rank": 2, "dur": 1, "reason": "fallback_all"},
        ],
    }
    fake = _write_fake_bin(select_repo, _select_json(payload))
    proc = _run_pytest(
        select_repo,
        "-n",
        "2",
        "--testpilot",
        "--testpilot-select",
        "--testpilot-budget-frac=1",
        "--testpilot-model",
        str(select_repo / "model"),
        extra_env={"TESTPILOT_BIN": str(fake)},
        timeout=90,
    )
    assert proc.returncode == 0
    assert int(
        sqlite3.connect(select_repo / ".testpilot" / "db.sqlite")
        .execute("SELECT COUNT(*) FROM test_results")
        .fetchone()[0]
    ) == 2
