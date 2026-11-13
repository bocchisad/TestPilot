"""Light tests for run_mutant helpers."""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from mutate.run_mutant import default_timeout_sec  # noqa: E402


def test_default_timeout_sec() -> None:
    assert default_timeout_sec(None, None) == 60
    assert default_timeout_sec(25.0, None) == 75
    assert default_timeout_sec(10.0, None) == 60
    assert default_timeout_sec(25.0, 120) == 120
