"""Tests for immutable measurement provenance snapshots."""

from __future__ import annotations

import subprocess
import importlib.util
import hashlib
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "measurement_provenance.py"
SPEC = importlib.util.spec_from_file_location("measurement_provenance", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
measurement_provenance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(measurement_provenance)
capture_provenance = measurement_provenance.capture_provenance


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Test")
    (root / "scripts").mkdir()
    (root / "scripts" / "measure_one.sh").write_text("#!/bin/sh\nexit 0\n")
    (root / "scripts" / "mutate_bootstrap.py").write_text("print('mutate')\n")
    git(root, "add", "scripts")
    git(root, "commit", "-qm", "fixture")
    return root


def binary(tmp_path: Path, *, failure: bool = False) -> Path:
    path = tmp_path / "testpilot-bin"
    if failure:
        path.write_text("#!/bin/sh\necho broken >&2\nexit 3\n")
    else:
        path.write_text("#!/bin/sh\necho 'testpilot 0.1.0'\n")
    path.chmod(0o755)
    return path


def test_clean_script_records_matching_committed_and_worktree_blobs(tmp_path: Path):
    root = repository(tmp_path)
    manifest = capture_provenance(
        root,
        binary(tmp_path),
        required_paths=["scripts/measure_one.sh"],
    )
    row = manifest["scripts"][0]
    assert row["commit_blob_sha1"] == row["worktree_blob_sha1"]
    assert row["dirty"] is False
    assert manifest["bin_version"] == "testpilot 0.1.0"
    assert (
        manifest["binary_sha256"]
        == hashlib.sha256(binary(tmp_path).read_bytes()).hexdigest()
    )
    assert manifest["head_sha"] == git(root, "rev-parse", "HEAD")


def test_default_measurement_provenance_includes_active_wrapper_and_backfill():
    required, _optional = measurement_provenance._inventory(Path.cwd())

    assert "scripts/run_week7_test_width.py" in required
    assert "scripts/backfill.py" in required


def test_edited_script_keeps_committed_blob_and_marks_worktree_dirty(tmp_path: Path):
    root = repository(tmp_path)
    script = root / "scripts" / "measure_one.sh"
    script.write_text("#!/bin/sh\necho changed\n")
    manifest = capture_provenance(
        root,
        binary(tmp_path),
        required_paths=["scripts/measure_one.sh"],
    )
    row = manifest["scripts"][0]
    assert row["commit_blob_sha1"] != row["worktree_blob_sha1"]
    assert row["dirty"] is True


def test_missing_optional_script_is_recorded_without_failing(tmp_path: Path):
    root = repository(tmp_path)
    manifest = capture_provenance(
        root,
        binary(tmp_path),
        required_paths=["scripts/measure_one.sh"],
        optional_paths=["scripts/measure_optional.sh"],
    )
    assert manifest["missing_optional_scripts"] == ["scripts/measure_optional.sh"]


def test_binary_version_failure_aborts_snapshot(tmp_path: Path):
    root = repository(tmp_path)
    with pytest.raises(RuntimeError, match="--version"):
        capture_provenance(
            root,
            binary(tmp_path, failure=True),
            required_paths=["scripts/measure_one.sh"],
        )


def test_malformed_binary_version_aborts_snapshot(tmp_path: Path):
    root = repository(tmp_path)
    bad = tmp_path / "bad-bin"
    bad.write_text("#!/bin/sh\necho not-a-version\n")
    bad.chmod(0o755)
    with pytest.raises(RuntimeError, match="--version"):
        capture_provenance(root, bad, required_paths=["scripts/measure_one.sh"])
