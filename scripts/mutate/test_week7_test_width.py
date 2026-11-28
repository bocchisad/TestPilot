from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import run_week7_test_width as width_runner
from run_week7_test_width import (
    _validate_full_run_plan,
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
    with pytest.raises(ValueError, match="K must be positive"):
        validate_options(n=400, k=0, jobs=2, suites=("click", "attrs"))
    with pytest.raises(ValueError, match="jobs must be between 1 and 4"):
        validate_options(n=400, k=4, jobs=0, suites=("click", "attrs"))


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
    assert (
        "--max-commits" in command
        and command[command.index("--max-commits") + 1] == "400"
    )


def test_parse_total_seconds_from_cli_output():
    assert parse_total_seconds(
        "estimate total: 53.4 min\ntotal_s=3205.844\n"
    ) == pytest.approx(3205.844)
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


def test_verify_checkout_rejects_pin_mismatch_and_dirty_checkout(tmp_path, monkeypatch):
    repo = tmp_path / "click"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "README.md").write_text("clean\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            "init",
        ],
        check=True,
        capture_output=True,
    )
    sha = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    monkeypatch.setattr(width_runner, "PINS", {"click": {"sha": "wrong-pin"}})
    with pytest.raises(ValueError, match="pin mismatch"):
        width_runner.verify_checkout(repo, "click")

    monkeypatch.setattr(width_runner, "PINS", {"click": {"sha": sha}})
    (repo / "untracked.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(ValueError, match="dirty click checkout"):
        width_runner.verify_checkout(repo, "click")


def test_dry_run_never_enters_full_measurement_path(tmp_path, monkeypatch):
    root = tmp_path
    cache_root = root / ".cache/measurements/week7/test-width"
    repos_root = cache_root / "repos"
    (repos_root / "click").mkdir(parents=True)
    run_dir = cache_root / "dry-run"
    binary = root / "testpilot"
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(0o755)
    monkeypatch.setattr(width_runner, "ROOT", root)
    monkeypatch.setattr(width_runner, "verify_checkout", lambda *_args: None)
    monkeypatch.setattr(
        width_runner, "verify_measurement_python", lambda _python: "3.13.0"
    )
    monkeypatch.setattr(
        width_runner.subprocess,
        "check_output",
        lambda command, **_kwargs: (
            "testpilot 0.1.0\n" if command[-1] == "--version" else "a" * 40 + "\n"
        ),
    )

    def provenance(command, **_kwargs):
        output = Path(command[command.index("--out") + 1])
        output.write_text(
            '{"binary_sha256":"hash","bin_version":"testpilot 0.1.0", "scripts":[]}\n',
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0)

    calls = []
    monkeypatch.setattr(width_runner.subprocess, "run", provenance)

    def stream(command, _env, log_path):
        calls.append(command)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("total_s=1\n", encoding="utf-8")
        return 0, "total_s=1\n"

    monkeypatch.setattr(width_runner, "_stream", stream)
    args = SimpleNamespace(
        suites=["click"],
        n=10,
        k=1,
        jobs=1,
        binary=str(binary),
        python=str(root / "python3.13"),
        run_dir=str(run_dir),
        repos_root=str(repos_root),
        mode="dry-run",
        seed=42,
    )

    assert width_runner.run(args) == 0
    assert len(calls) == 1
    assert "--dry-run" in calls[0]
    assert "--no-resume" not in calls[0]
    assert not (run_dir / "full-run.json").exists()
    assert not (run_dir / "out").exists()


def test_full_run_refuses_existing_suite_output_directory(tmp_path, monkeypatch):
    root = tmp_path
    cache_root = root / ".cache/measurements/week7/test-width"
    repos_root = cache_root / "repos"
    (repos_root / "click").mkdir(parents=True)
    run_dir = cache_root / "existing-run"
    (run_dir / "out/click").mkdir(parents=True)
    (run_dir / "dry-run.json").write_text(
        '{"n": 10, "k": 1, "suites": ["click"]}\n', encoding="utf-8"
    )
    binary = root / "testpilot"
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(0o755)
    monkeypatch.setattr(width_runner, "ROOT", root)
    monkeypatch.setattr(width_runner, "verify_checkout", lambda *_args: None)
    monkeypatch.setattr(
        width_runner, "verify_measurement_python", lambda _python: "3.13.0"
    )
    args = SimpleNamespace(
        suites=["click"],
        n=10,
        k=1,
        jobs=1,
        binary=str(binary),
        python=str(root / "python3.13"),
        run_dir=str(run_dir),
        repos_root=str(repos_root),
        mode="full",
        seed=42,
    )

    with pytest.raises(ValueError, match="refusing to overwrite existing output"):
        width_runner.run(args)


def test_full_run_requires_exact_dry_run_settings_binary_and_script_snapshot():
    planned = {
        "n": 10,
        "k": 1,
        "jobs": 1,
        "seed": 42,
        "suites": ["click"],
        "measurement_python_version": "3.13.0",
        "binary_version": "testpilot 0.1.0",
    }
    manifest = {
        "binary_sha256": "binary-a",
        "bin_version": "testpilot 0.1.0",
        "scripts": [
            {
                "path": "scripts/run_week7_test_width.py",
                "worktree_blob_sha1": "width-a",
                "dirty": False,
            },
            {
                "path": "scripts/backfill.py",
                "worktree_blob_sha1": "backfill-a",
                "dirty": False,
            },
        ],
    }
    params = {"n": 10, "k": 1, "jobs": 1, "seed": 42, "suites": ["click"]}

    assert (
        _validate_full_run_plan(planned, params, "3.13.0", manifest, manifest) is None
    )
    with pytest.raises(ValueError, match="jobs"):
        _validate_full_run_plan(
            planned, {**params, "jobs": 2}, "3.13.0", manifest, manifest
        )
    with pytest.raises(ValueError, match="seed"):
        _validate_full_run_plan(
            planned, {**params, "seed": 7}, "3.13.0", manifest, manifest
        )
    with pytest.raises(ValueError, match="binary SHA256"):
        _validate_full_run_plan(
            planned,
            params,
            "3.13.0",
            manifest,
            {**manifest, "binary_sha256": "binary-b"},
        )
    with pytest.raises(ValueError, match="scripts/run_week7_test_width.py"):
        _validate_full_run_plan(
            planned,
            params,
            "3.13.0",
            manifest,
            {
                **manifest,
                "scripts": [
                    {**manifest["scripts"][0], "worktree_blob_sha1": "changed"},
                    manifest["scripts"][1],
                ],
            },
        )
