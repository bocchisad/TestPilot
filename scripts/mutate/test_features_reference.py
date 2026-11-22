"""Tests for canonical repository names in the reference feature exporter."""
from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "features_reference.py"
SPEC = importlib.util.spec_from_file_location("features_reference", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
features_reference = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(features_reference)


def test_attrs_export_uses_explicit_canonical_repo_name(tmp_path: Path):
    renamed_checkout = tmp_path / "attrs-rerun12"
    assert features_reference.export_repo_name(renamed_checkout, "attrs") == "attrs"
    assert "attrs-rerun12" not in features_reference.export_repo_name(renamed_checkout, "attrs")


def test_reference_export_defaults_to_checkout_name(tmp_path: Path):
    checkout = tmp_path / "jinja"
    assert features_reference.export_repo_name(checkout, None) == "jinja"
