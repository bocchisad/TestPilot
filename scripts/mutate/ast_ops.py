"""AST mutation operators: one-site edits with byte-preserving stitch."""

from __future__ import annotations

import ast
import copy
import random
from dataclasses import dataclass
from typing import Iterable

_CMP_FLIPS: dict[type[ast.cmpop], type[ast.cmpop]] = {
    ast.Lt: ast.LtE,
    ast.LtE: ast.Lt,
    ast.Gt: ast.GtE,
    ast.GtE: ast.Gt,
}


@dataclass(frozen=True)
class MutationSite:
    """A single mutation candidate on a concrete AST node."""

    op_id: str
    lineno: int
    col_offset: int
    end_lineno: int
    end_col_offset: int
    node_key: str


def _site_sort_key(site: MutationSite) -> tuple[str, int, int, str]:
    return (site.op_id, site.lineno, site.col_offset, site.node_key)


def _node_key(node: ast.AST) -> str:
    if not _has_location(node):
        return f"{type(node).__name__}:?"
    return f"{type(node).__name__}:{node.lineno}:{node.col_offset}"


def _has_location(node: ast.AST) -> bool:
    return (
        hasattr(node, "lineno")
        and getattr(node, "lineno", None) is not None
        and getattr(node, "col_offset", None) is not None
    )


def _node_touched(node: ast.AST, touched_lines: set[int]) -> bool:
    if not _has_location(node):
        return False
    start = node.lineno
    end = node.end_lineno if node.end_lineno is not None else node.lineno
    return bool(touched_lines.intersection(range(start, end + 1)))


def _line_starts(source: str) -> list[int]:
    starts = [0]
    for i, ch in enumerate(source):
        if ch == "\n":
            starts.append(i + 1)
    return starts


def _utf8_offset_in_line(line: str, col_offset: int) -> int:
    """Map AST col_offset (Unicode code points) to byte index within encoded line."""
    encoded = line.encode("utf-8")
    if col_offset <= 0:
        return 0
    prefix = line[:col_offset]
    return len(prefix.encode("utf-8"))


def _span_byte_offsets(source: str, node: ast.AST) -> tuple[int, int] | None:
    if node.end_lineno is None or node.end_col_offset is None:
        return None
    lines = source.splitlines(keepends=True)
    if node.lineno < 1 or node.lineno > len(lines):
        return None
    line_starts = _line_starts(source)

    def byte_offset(lineno: int, col_offset: int) -> int:
        line_text = lines[lineno - 1]
        line_no_nl = line_text.splitlines()[0] if line_text else ""
        return line_starts[lineno - 1] + _utf8_offset_in_line(line_no_nl, col_offset)

    return byte_offset(node.lineno, node.col_offset), byte_offset(
        node.end_lineno, node.end_col_offset
    )


def _stitch(source: str, node: ast.AST, replacement: str) -> str | None:
    span = _span_byte_offsets(source, node)
    if span is None:
        return None
    start, end = span
    raw = source.encode("utf-8")
    rep = replacement.encode("utf-8")
    return (raw[:start] + rep + raw[end:]).decode("utf-8")


def compiles(source: str) -> bool:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    try:
        compile(tree, "<mutant>", "exec")
    except Exception:
        return False
    return True


def _flip_cmp(node: ast.Compare) -> ast.Compare | None:
    if not node.ops or len(node.ops) != 1:
        return None
    op = node.ops[0]
    new_op = _CMP_FLIPS.get(type(op))
    if new_op is None:
        return None
    out = copy.deepcopy(node)
    out.ops = [new_op()]
    return out


def _flip_eq(node: ast.Compare) -> ast.Compare | None:
    if not node.ops or len(node.ops) != 1:
        return None
    op = node.ops[0]
    if isinstance(op, ast.Eq):
        new_op: ast.cmpop = ast.NotEq()
    elif isinstance(op, ast.NotEq):
        new_op = ast.Eq()
    else:
        return None
    out = copy.deepcopy(node)
    out.ops = [new_op]
    return out


def _flip_arith(node: ast.BinOp) -> ast.BinOp | None:
    if isinstance(node.op, ast.Add):
        new_op: ast.operator = ast.Sub()
    elif isinstance(node.op, ast.Sub):
        new_op = ast.Add()
    else:
        return None
    out = copy.deepcopy(node)
    out.op = new_op
    return out


def _flip_bool(node: ast.BoolOp) -> ast.BoolOp | None:
    if isinstance(node.op, ast.And):
        new_op: ast.boolop = ast.Or()
    elif isinstance(node.op, ast.Or):
        new_op = ast.And()
    else:
        return None
    out = copy.deepcopy(node)
    out.op = new_op
    return out


def _not_wrap(node: ast.expr) -> ast.expr | None:
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return copy.deepcopy(node.operand)
    out = ast.UnaryOp(op=ast.Not(), operand=copy.deepcopy(node))
    ast.copy_location(out, node)
    return out


def _const_pm1(node: ast.Constant) -> ast.Constant | None:
    if not isinstance(node.value, (int, float)):
        return None
    out = copy.deepcopy(node)
    if isinstance(node.value, bool):
        return None
    out.value = node.value + 1 if isinstance(node.value, int) else node.value + 1.0
    return out


def _return_none(node: ast.Return) -> ast.Return | None:
    if node.value is None:
        return None
    out = copy.deepcopy(node)
    out.value = ast.Constant(value=None)
    return out


def _find_node(root: ast.AST, site: MutationSite) -> ast.AST | None:
    for node in ast.walk(root):
        if not _has_location(node):
            continue
        if (
            node.lineno == site.lineno
            and node.col_offset == site.col_offset
            and _node_key(node) == site.node_key
        ):
            return node
    return None


def _mutate_node(node: ast.AST, op_id: str) -> ast.AST | None:
    if op_id == "cmp_flip" and isinstance(node, ast.Compare):
        return _flip_cmp(node)
    if op_id == "eq_flip" and isinstance(node, ast.Compare):
        return _flip_eq(node)
    if op_id == "arith_flip" and isinstance(node, ast.BinOp):
        return _flip_arith(node)
    if op_id == "bool_flip" and isinstance(node, ast.BoolOp):
        return _flip_bool(node)
    if op_id == "not_wrap" and isinstance(node, ast.expr):
        return _not_wrap(node)
    if op_id == "const_pm1" and isinstance(node, ast.Constant):
        return _const_pm1(node)
    if op_id == "return_none" and isinstance(node, ast.Return):
        return _return_none(node)
    return None


def _body_functions(module: ast.Module) -> dict[int, ast.FunctionDef | ast.AsyncFunctionDef]:
    """Map statement line -> containing function (innermost)."""
    result: dict[int, ast.FunctionDef | ast.AsyncFunctionDef] = {}

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.stack: list[ast.FunctionDef | ast.AsyncFunctionDef] = []

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self.stack.append(node)
            for stmt in node.body:
                result[stmt.lineno] = node
            self.generic_visit(node)
            self.stack.pop()

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self.stack.append(node)
            for stmt in node.body:
                result[stmt.lineno] = node
            self.generic_visit(node)
            self.stack.pop()

    Visitor().visit(module)
    return result


def _add_site(sites: list[MutationSite], op_id: str, node: ast.AST) -> None:
    end_lineno = node.end_lineno if node.end_lineno is not None else node.lineno
    end_col = node.end_col_offset if node.end_col_offset is not None else node.col_offset
    sites.append(
        MutationSite(
            op_id=op_id,
            lineno=node.lineno,
            col_offset=node.col_offset,
            end_lineno=end_lineno,
            end_col_offset=end_col,
            node_key=_node_key(node),
        )
    )


def find_sites(source: str, touched_lines: set[int]) -> list[MutationSite]:
    """List mutation candidates on hunk-touched lines."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    if not isinstance(tree, ast.Module):
        return []

    sites: list[MutationSite] = []
    fn_by_stmt = _body_functions(tree)

    for node in ast.walk(tree):
        if not _has_location(node) or not _node_touched(node, touched_lines):
            continue

        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            if type(node.ops[0]) in _CMP_FLIPS:
                _add_site(sites, "cmp_flip", node)
            if isinstance(node.ops[0], (ast.Eq, ast.NotEq)):
                _add_site(sites, "eq_flip", node)

        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub)):
            _add_site(sites, "arith_flip", node)

        if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
            _add_site(sites, "bool_flip", node)

        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(
            node.value, bool
        ):
            _add_site(sites, "const_pm1", node)

        if isinstance(node, ast.Return) and node.value is not None:
            _add_site(sites, "return_none", node)

        if isinstance(node, (ast.If, ast.While, ast.Assert)):
            if _node_touched(node.test, touched_lines):
                _add_site(sites, "not_wrap", node.test)

        if isinstance(
            node,
            (ast.Compare, ast.BoolOp, ast.UnaryOp, ast.IfExp),
        ):
            _add_site(sites, "not_wrap", node)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        if not _node_touched(node, touched_lines):
            continue
        fn = fn_by_stmt.get(node.lineno)
        if fn is None:
            continue
        if not _node_touched(fn, touched_lines):
            continue
        _add_site(sites, "drop_call", node)

    # Dedupe identical (op, node_key)
    seen: set[tuple[str, str]] = set()
    unique: list[MutationSite] = []
    for s in sites:
        key = (s.op_id, s.node_key)
        if key in seen:
            continue
        seen.add(key)
        unique.append(s)
    return unique


def apply_mutation(source: str | bytes, site: MutationSite) -> str | None:
    """Apply one mutation; return new source or None if it does not compile."""
    if isinstance(source, bytes):
        text = source.decode("utf-8")
    else:
        text = source

    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None

    node = _find_node(tree, site)
    if node is None:
        return None

    if site.op_id == "drop_call":
        if not isinstance(node, ast.Expr):
            return None
        stitched = _stitch(text, node, "")
        if stitched is None:
            return None
        return stitched if compiles(stitched) else None

    mutated = _mutate_node(node, site.op_id)
    if mutated is None:
        return None

    try:
        replacement = ast.unparse(mutated)
    except Exception:
        return None

    stitched = _stitch(text, node, replacement)
    if stitched is None:
        return None
    if not compiles(stitched):
        return None
    return stitched


def dedup_by_diff(
    results: Iterable[tuple[MutationSite, str]],
) -> list[tuple[MutationSite, str]]:
    """Drop entries whose mutated source text is identical."""
    seen: set[str] = set()
    out: list[tuple[MutationSite, str]] = []
    for site, src in results:
        if src in seen:
            continue
        seen.add(src)
        out.append((site, src))
    return out


def order_sites(sites: list[MutationSite], seed: int) -> list[MutationSite]:
    """Deterministic sort of sites (seed reserved for API symmetry with sample_sites)."""
    _ = seed
    return sorted(sites, key=_site_sort_key)


def sample_sites(sites: list[MutationSite], k: int, seed: int) -> list[MutationSite]:
    """Sort sites, then take k via deterministic rng.sample."""
    ordered = order_sites(sites, seed)
    if k <= 0:
        return []
    if k >= len(ordered):
        return ordered
    rng = random.Random(seed)
    return rng.sample(ordered, k)


def apply_all_valid(source: str, sites: list[MutationSite]) -> list[tuple[MutationSite, str]]:
    """Helper for bootstrap: apply each site and keep compile-valid results."""
    results: list[tuple[MutationSite, str]] = []
    for site in sites:
        out = apply_mutation(source, site)
        if out is not None:
            results.append((site, out))
    return results
