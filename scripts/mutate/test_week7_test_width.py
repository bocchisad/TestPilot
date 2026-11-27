from __future__ import annotations

import sys
from pathlib import Path

import pytest
from run_week7_test_width import (
    build_bootstrap_command,
    build_measurement_env,
    measurement_python_path,
    parse_total_seconds,
    validate_options,
    verify_measurement_python,
)


def test_rejects_nonpositive_n_and_jobs_above_cli_limit():
    with pytest.raises(ValueError, match="N must be positive"):
        validate_options(n=0, k=4, jobs=2, suites=("click", "attrs"))
    with pytest.raises(ValueError, match="jobs must be between 1 and 4"):
        validate_options(n=400, k=4, jobs=5, suites=("click", "attrs"))


def test_rejects_unknown_and_duplicate_suites():
    with pytest.raises(ValueError, match="supported suites"):
        validate_options(n=400, k=4, jobs=2, suites=("click", "jinja"))
    with pytest.raises(ValueError, match="must be unique"):
        validate_options(n=400, k=4, jobs=2, suites=("click", "click"))


def test_dry_run_command_never_enables_mutation_or_force():
    command = build_bootstrap_command(
        binary="/tmp/testpilot",
        repo="/tmp/click",
        out="/tmp/out/click",
        suite="click",
        n=400,
        k=4,
        jobs=2,
        seed=42,
        dry_run=True,
    )
    assert "--dry-run" in command
    assert "--force" not in command
    assert "--max-commits" in command and command[command.index("--max-commits") + 1] == "400"


def test_parse_total_seconds_from_cli_output():
    assert parse_total_seconds("estimate total: 53.4 min\ntotal_s=3205.844\n") == pytest.approx(3205.844)
    with pytest.raises(ValueError, match="missing total_s"):
        parse_total_seconds("no estimate emitted")


def test_measurement_python_must_be_313_and_is_first_on_path():
    python = Path("/opt/testpilot-py313/bin/python")
    env = build_measurement_env({"PATH": "/usr/bin"}, python)
    assert env["PATH"].split(":")[0] == "/opt/testpilot-py313/bin"
    assert env["TESTPILOT_MEASURE_PYTHON"] == str(python)
    assert verify_measurement_python(Path(sys.executable)).startswith("3.13.")


def test_measurement_python_path_preserves_virtualenv_symlink(tmp_path):
    venv_python = tmp_path / "venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.symlink_to("/usr/bin/python3")
    assert measurement_python_path(venv_python, root=tmp_path) == venv_python
