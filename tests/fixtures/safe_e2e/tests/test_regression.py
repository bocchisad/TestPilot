"""Added on the regression commit by tests/e2e/test_safe_e2e.sh (not in baseline copy)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from calc import add  # noqa: E402


def test_regression_add():
    assert add(1, 1) == 2
