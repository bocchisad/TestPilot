"""is_source_py denylist (docs/ etc.)."""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from mutate.hunks import is_source_py  # noqa: E402


def test_docs_conf_not_source() -> None:
    assert is_source_py("docs/conf.py") is False
    assert is_source_py("doc/conf.py") is False
    assert is_source_py("documentation/x.py") is False


def test_pkg_source_ok() -> None:
    assert is_source_py("pkg/__init__.py") is True
    assert is_source_py("src/foo.py") is True


def test_tests_not_source() -> None:
    assert is_source_py("tests/test_x.py") is False
    assert is_source_py("pkg/tests/test_x.py") is False
