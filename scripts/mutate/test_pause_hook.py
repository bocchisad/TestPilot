"""TESTPILOT_TEST_PAUSE_AFTER_PAIRS is a no-op unless env is set."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import mutate_bootstrap as mb  # noqa: E402


def test_pause_hook_noop_without_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("TESTPILOT_TEST_PAUSE_AFTER_PAIRS", raising=False)
    marker = tmp_path / ".testpilot" / "bootstrap" / mb._PAUSE_MARKER_NAME
    # Should return immediately and never create a marker.
    mb.maybe_test_pause_after_pairs(tmp_path, n_completed=99)
    assert not marker.exists()


def test_pause_hook_writes_marker_then_exits_when_removed(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("TESTPILOT_TEST_PAUSE_AFTER_PAIRS", "2")
    marker = tmp_path / ".testpilot" / "bootstrap" / mb._PAUSE_MARKER_NAME

    import threading

    def _clear() -> None:
        import time

        for _ in range(100):
            if marker.is_file():
                marker.unlink()
                return
            time.sleep(0.02)

    t = threading.Thread(target=_clear, daemon=True)
    t.start()
    mb.maybe_test_pause_after_pairs(tmp_path, n_completed=2)
    t.join(timeout=5)
    assert not marker.exists()
