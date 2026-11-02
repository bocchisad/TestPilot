from __future__ import annotations

import json
from pathlib import Path

import pytest

from pytest_testpilot.codec import decode_lines, encode_lines

VECTORS = (
    Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "coverage_vectors.json"
)


def test_shared_vectors() -> None:
    data = json.loads(VECTORS.read_text(encoding="utf-8"))
    for item in data:
        blob = bytes.fromhex(item["hex"])
        if err := item.get("error"):
            with pytest.raises(ValueError, match=err):
                decode_lines(blob)
            continue
        encoded = encode_lines(item["lines"])
        assert encoded.hex() == item["hex"]
        expected = sorted(set(item["lines"]))
        assert decode_lines(blob) == expected


def test_empty() -> None:
    assert encode_lines([]) == b"\x01"
    assert decode_lines(b"\x01") == []
