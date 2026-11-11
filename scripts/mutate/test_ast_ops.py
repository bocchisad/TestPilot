"""Unit tests for scripts/mutate/ast_ops.py."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from mutate.ast_ops import (  # noqa: E402
    MutationSite,
    apply_mutation,
    dedup_by_diff,
    find_sites,
    order_sites,
    sample_sites,
)


def _one_site(source: str, touched: set[int], op_id: str) -> MutationSite:
    matches = [s for s in find_sites(source, touched) if s.op_id == op_id]
    assert len(matches) >= 1, f"no site for {op_id!r} in {source!r}"
    return matches[0]


def test_cmp_flip():
    source = "def f():\n    if x < 1:\n        pass\n"
    site = _one_site(source, {2}, "cmp_flip")
    out = apply_mutation(source, site)
    assert out is not None
    assert "<=" in out
    assert "< 1" not in out


def test_eq_flip():
    source = "def f():\n    if a == b:\n        pass\n"
    site = _one_site(source, {2}, "eq_flip")
    out = apply_mutation(source, site)
    assert out is not None
    assert "!=" in out


def test_arith_flip():
    source = "def f():\n    y = a + b\n"
    site = _one_site(source, {2}, "arith_flip")
    out = apply_mutation(source, site)
    assert out is not None
    assert "a-b" in out.replace(" ", "")


def test_bool_flip():
    source = "def f():\n    if a and b:\n        pass\n"
    site = _one_site(source, {2}, "bool_flip")
    out = apply_mutation(source, site)
    assert out is not None
    assert " or " in out


def test_not_wrap_adds_not():
    source = "def f():\n    if flag:\n        pass\n"
    site = _one_site(source, {2}, "not_wrap")
    out = apply_mutation(source, site)
    assert out is not None
    assert "not" in out and "flag" in out
    assert "notflag" in out.replace(" ", "")


def test_not_wrap_unwraps_existing_not():
    source = "def f():\n    if not flag:\n        pass\n"
    site = _one_site(source, {2}, "not_wrap")
    out = apply_mutation(source, site)
    assert out is not None
    assert "not not" not in out
    assert "flag" in out


def test_const_pm1():
    source = "def f():\n    n = 3\n"
    site = _one_site(source, {2}, "const_pm1")
    out = apply_mutation(source, site)
    assert out is not None
    assert "4" in out


def test_return_none():
    source = "def f():\n    return 1\n"
    site = _one_site(source, {2}, "return_none")
    out = apply_mutation(source, site)
    assert out is not None
    assert "return None" in out


def test_drop_call():
    source = "def g():\n    side_effect()\n    return 0\n"
    site = _one_site(source, {2}, "drop_call")
    out = apply_mutation(source, site)
    assert out is not None
    assert "side_effect" not in out


def test_compile_skip_drop_call_leaves_invalid_function():
    source = "def g():\n    only()\n"
    site = _one_site(source, {2}, "drop_call")
    out = apply_mutation(source, site)
    assert out is None


def test_byte_preserve_outside_mutation_span():
    source = "def f():\n    a=1\n\n    # keep   spaces\n    if x < 1:\n        pass\n"
    orig = source.encode("utf-8")
    site = _one_site(source, {5}, "cmp_flip")
    out = apply_mutation(source, site)
    assert out is not None
    out_b = out.encode("utf-8")

    span_start = orig.find(b"x < 1")
    assert span_start >= 0
    assert orig[:span_start] == out_b[:span_start]
    assert orig[span_start + len(b"x < 1") :] == out_b[span_start + len(b"x <= 1") :]


def test_find_sites_respects_touched_lines():
    source = "def f():\n    if a == 1:\n        pass\n    if b == 2:\n        pass\n"
    sites = find_sites(source, {2})
    assert all(s.lineno == 2 for s in sites if s.op_id == "eq_flip")
    assert not any(s.lineno == 4 for s in sites)


def test_find_sites_skips_module_level():
    source = "X = 1\nif a == 1:\n    pass\n\ndef f():\n    if b == 2:\n        pass\n"
    sites = find_sites(source, {1, 2, 6})
    assert all(s.lineno >= 6 for s in sites)


def test_dedup_by_diff():
    s1 = MutationSite("eq_flip", 1, 3, 1, 10, "Compare:1:3")
    s2 = MutationSite("cmp_flip", 2, 0, 2, 5, "Compare:2:0")
    body = "if a != b:\n    pass\n"
    out = dedup_by_diff([(s1, body), (s2, body), (s2, body + "# diff\n")])
    assert len(out) == 2


def test_deterministic_sample_sites():
    source = "\n".join(
        [
            "def f():",
            "    if a == 1:",
            "        pass",
            "    if b < 2:",
            "        pass",
            "    return 3",
        ]
    )
    touched = {2, 4, 5}
    sites = find_sites(source, touched)
    a = sample_sites(sites, 3, seed=42)
    b = sample_sites(sites, 3, seed=42)
    assert a == b
    assert order_sites(sites, 0) == sorted(
        sites, key=lambda s: (s.op_id, s.lineno, s.col_offset, s.node_key)
    )


def test_apply_returns_none_on_bad_syntax_input():
    site = MutationSite("eq_flip", 1, 0, 1, 1, "nope")
    assert apply_mutation("def (:)\n", site) is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
