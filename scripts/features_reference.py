#!/usr/bin/env python3
"""Reference implementation of Week-2 features (must match Rust dump / spec)."""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import subprocess
import sys
from collections import defaultdict, deque
from pathlib import Path

TAU_DAYS = 180.0
NO_PATH = 99
MAX_IMP = 6
STOP = {"test", "tests", "src", "py", "init"}


def git(cwd: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


def topo_old_to_new(cwd: Path) -> list[tuple[str, int]]:
    lines = git(cwd, "rev-list", "--topo-order", "--reverse", "HEAD").splitlines()
    out = []
    for sha in lines:
        ts = int(git(cwd, "show", "-s", "--format=%at", sha))
        out.append((sha, ts))
    return out


def first_parent(cwd: Path, sha: str) -> str | None:
    ps = git(cwd, "rev-list", "--parents", "-n", "1", sha).split()
    if len(ps) < 2:
        return None
    return ps[1]


def is_test_path(path: str) -> bool:
    p = path.replace("\\", "/")
    if "/tests/" in p or p.startswith("tests/"):
        return True
    return "test" in p.rsplit("/", 1)[-1].lower()


def src_files(paths: list[str]) -> list[str]:
    return sorted({p for p in paths if p.endswith(".py") and not is_test_path(p)})


def tokenize(s: str) -> set[str]:
    s = s.replace("_", " ")
    tokens: set[str] = set()
    i = 0
    chars = list(s)
    n = len(chars)
    while i < n:
        c = chars[i]
        if c.isdigit():
            j = i + 1
            while j < n and chars[j].isdigit():
                j += 1
            tok = "".join(chars[i:j]).lower()
            if len(tok) >= 3 and tok not in STOP:
                tokens.add(tok)
            i = j
        elif c.isupper():
            j = i + 1
            while j < n and chars[j].isupper():
                j += 1
            if j < n and chars[j].islower() and j - i > 1:
                tok = "".join(chars[i : j - 1]).lower()
                if len(tok) >= 3 and tok not in STOP:
                    tokens.add(tok)
                i = j - 1
            elif j < n and chars[j].islower():
                j2 = j
                while j2 < n and (chars[j2].islower() or chars[j2].isdigit()):
                    j2 += 1
                tok = "".join(chars[i:j2]).lower()
                if len(tok) >= 3 and tok not in STOP:
                    tokens.add(tok)
                i = j2
            else:
                tok = "".join(chars[i:j]).lower()
                if len(tok) >= 3 and tok not in STOP:
                    tokens.add(tok)
                i = j
        elif c.islower() or c.isalpha():
            j = i + 1
            while j < n and (chars[j].islower() or chars[j].isdigit()):
                j += 1
            tok = "".join(chars[i:j]).lower()
            if len(tok) >= 3 and tok not in STOP:
                tokens.add(tok)
            i = j
        else:
            i += 1
    return tokens


def decay(t0: int, t: int) -> float:
    return math.exp(-max(0, t0 - t) / 86400.0 / TAU_DAYS)


def decode_lines(blob: bytes) -> set[int]:
    if not blob or blob[0] != 0x01:
        return set()
    lines = []
    prev = 0
    i = 1
    first = True
    while i < len(blob):
        result = 0
        shift = 0
        while True:
            b = blob[i]
            i += 1
            result |= (b & 0x7F) << shift
            if not (b & 0x80):
                break
            shift += 7
        line = result if first else prev + result
        first = False
        lines.append(line)
        prev = line
    return set(lines)


def test_file_of(tid: str) -> str:
    return tid.split("::", 1)[0].replace("\\", "/")


def parse_hunks(cwd: Path, parent: str, sha: str) -> list[dict]:
    """Parse unified diff -U0 into per-file old/new line sets (1-based)."""
    try:
        raw = subprocess.check_output(
            ["git", "diff", "-U0", "--find-renames", f"{parent}..{sha}"],
            cwd=cwd,
        )
    except subprocess.CalledProcessError:
        return []
    text = raw.decode("utf-8", errors="replace")
    hunks: list[dict] = []
    cur: dict | None = None
    for line in text.splitlines():
        if line.startswith("diff --git "):
            if cur:
                hunks.append(cur)
            cur = {
                "path": "",
                "old_path": None,
                "change_type": "M",
                "old_lines": set(),
                "new_lines": set(),
                "is_binary": False,
            }
        elif line.startswith("rename from ") and cur is not None:
            cur["old_path"] = line[len("rename from ") :]
            cur["change_type"] = "R"
        elif line.startswith("rename to ") and cur is not None:
            cur["path"] = line[len("rename to ") :]
        elif line.startswith("+++ b/") and cur is not None:
            cur["path"] = line[6:]
        elif line.startswith("--- a/") and cur is not None and cur["old_path"] is None:
            cur["old_path"] = line[6:]
        elif line.startswith("Binary files") and cur is not None:
            cur["is_binary"] = True
            # e.g. "Binary files /dev/null and b/pkg/data.bin differ"
            m = re.search(r" and b/(.+) differ$", line)
            if m:
                cur["path"] = m.group(1)
            m2 = re.search(r"^Binary files a/(.+) and ", line)
            if m2 and not cur.get("old_path"):
                cur["old_path"] = m2.group(1)
                if not cur["path"]:
                    cur["path"] = m2.group(1)
        elif line.startswith("new file mode") and cur is not None:
            cur["change_type"] = "A"
        elif line.startswith("deleted file mode") and cur is not None:
            cur["change_type"] = "D"
        elif line.startswith("@@") and cur is not None:
            m = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
            if not m:
                continue
            old_start, old_len = int(m.group(1)), int(m.group(2) or "1")
            new_start, new_len = int(m.group(3)), int(m.group(4) or "1")
            if old_len == 0:
                pass
            else:
                for ln in range(old_start, old_start + old_len):
                    cur["old_lines"].add(ln)
            if new_len == 0:
                pass
            else:
                for ln in range(new_start, new_start + new_len):
                    cur["new_lines"].add(ln)
    if cur:
        hunks.append(cur)
    # Fix empty path for pure deletes
    for h in hunks:
        if not h["path"] and h["old_path"]:
            h["path"] = h["old_path"]
    return [h for h in hunks if h["path"]]


def show_blob(cwd: Path, rev: str, path: str) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "show", f"{rev}:{path}"],
            cwd=cwd,
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        return None


def scan_defs(src: str) -> list[tuple[str, str, int, int]]:
    """Return (qualname, kind, start, end_exclusive) approximating the Rust scanner."""
    lines = src.splitlines()
    defs: list[tuple[str, str, int, int]] = []
    stack: list[tuple[int, str, str, int, int]] = []
    pending_dec: int | None = None
    for i, raw in enumerate(lines, start=1):
        indent = len(raw) - len(raw.lstrip(" \t"))
        trimmed = raw.strip()
        if not trimmed or trimmed.startswith("#"):
            continue
        while stack and indent <= stack[-1][0] and not trimmed.startswith("@"):
            ind, kind, name, start, dec = stack.pop()
            qual = ".".join([*(x[2] for x in stack), name])
            defs.append((qual, kind, min(dec, start), i))
        if trimmed.startswith("@"):
            if pending_dec is None:
                pending_dec = i
            continue
        for prefix, kind in (("async def ", "async def"), ("def ", "def"), ("class ", "class")):
            if trimmed.startswith(prefix):
                rest = trimmed[len(prefix) :]
                name = re.match(r"[A-Za-z_][A-Za-z0-9_]*", rest)
                nm = name.group(0) if name else "?"
                dec = pending_dec if pending_dec is not None else i
                pending_dec = None
                stack.append((indent, kind, nm, i, dec))
                break
        else:
            pending_dec = None
    eof = len(lines) + 1
    while stack:
        ind, kind, name, start, dec = stack.pop()
        qual = ".".join([*(x[2] for x in stack), name])
        defs.append((qual, kind, min(dec, start), eof))
    return defs


def changed_funcs(path: str, old_src: str | None, old_lines: set[int]) -> list[str]:
    if not old_lines:
        return []
    if old_src is None:
        return [f"{path}::<module>"]
    defs = scan_defs(old_src)
    hit = []
    covered: set[int] = set()
    for qual, kind, start, end in defs:
        if kind == "class":
            continue
        inter = False
        for ln in range(start, end):
            if ln in old_lines:
                inter = True
                covered.add(ln)
        if inter:
            hit.append(f"{path}::{qual}")
    if any(ln not in covered for ln in old_lines):
        hit.append(f"{path}::<module>")
    return sorted(set(hit))


def list_py_paths(cwd: Path, rev: str) -> list[str]:
    try:
        out = git(cwd, "ls-tree", "-r", "--name-only", rev)
    except subprocess.CalledProcessError:
        return []
    return [p for p in out.splitlines() if p.endswith(".py")]


def build_import_dist(
    cwd: Path, rev: str, seeds: list[str], targets: list[str]
) -> dict[str, int]:
    """BFS import distance from each seed path to any target. Returns seed→dist."""
    all_py = list_py_paths(cwd, rev)
    path_set = set(all_py)
    modules: dict[str, str] = {}
    for p in all_py:
        no_ext = p[: -len(".py")].replace("\\", "/")
        if no_ext.endswith("/__init__"):
            mod = no_ext[: -len("/__init__")].replace("/", ".")
        else:
            mod = no_ext.replace("/", ".")
        modules.setdefault(mod, p)
        if mod.startswith("src."):
            modules.setdefault(mod[4:], p)

    def resolve(from_path: str, stmt: str) -> str | None:
        stmt = stmt.strip()
        if stmt.startswith("from "):
            rest = stmt[5:].strip()
            level = 0
            while rest.startswith("."):
                level += 1
                rest = rest[1:]
            if " import " not in rest and level == 0:
                return None
            if " import " in rest:
                mod_part, _names = rest.split(" import ", 1)
            else:
                mod_part, _names = "", rest
            mod_part = mod_part.strip()
            if level:
                parts = from_path.replace("\\", "/").split("/")[:-1]
                for _ in range(max(0, level - 1)):
                    if parts:
                        parts.pop()
                if mod_part:
                    parts.extend(mod_part.split("."))
                key = ".".join(parts)
            else:
                key = mod_part
            if key in modules and modules[key] in path_set:
                return modules[key]
            guess = key.replace(".", "/") + ".py"
            if guess in path_set:
                return guess
        elif stmt.startswith("import "):
            mod = stmt[7:].split(",")[0].strip().split()[0]
            if mod in modules and modules[mod] in path_set:
                return modules[mod]
        return None

    edges: dict[str, set[str]] = {}

    def scan_file(path: str) -> None:
        if path in edges:
            return
        src = show_blob(cwd, rev, path)
        nbrs: set[str] = set()
        if src:
            for line in src.splitlines():
                t = line.strip()
                if t.startswith("import ") or t.startswith("from "):
                    r = resolve(path, t)
                    if r:
                        nbrs.add(r)
        edges[path] = nbrs

    target_set = set(targets)
    out: dict[str, int] = {}
    for seed in seeds:
        q: deque[tuple[str, int]] = deque([(seed, 0)])
        seen = {seed}
        found = NO_PATH
        while q:
            node, d = q.popleft()
            if node in target_set:
                found = d
                break
            if d >= MAX_IMP:
                continue
            scan_file(node)
            for n in edges.get(node, ()):
                if n not in seen:
                    seen.add(n)
                    q.append((n, d + 1))
        out[seed] = found
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", type=Path, required=True)
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    repo: Path = args.repo
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    commits = topo_old_to_new(repo)
    test_stats: dict[str, dict] = defaultdict(
        lambda: {"obs": 0, "fails": 0, "flaky": 0, "first": None, "dur": None}
    )
    file_test: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    func_test: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    tests_in_file: dict[str, set[str]] = defaultdict(set)
    co_events: list[tuple[list[str], int]] = []
    cov_snaps: dict[str, dict[str, dict[str, set[int]]]] = {}
    rows = []
    commit_idx = 0
    repo_name = repo.name

    def last_clean_with_results(sha: str) -> int | None:
        r = conn.execute(
            "SELECT r.id FROM test_runs r WHERE r.sha=? AND r.dirty=0 "
            "AND EXISTS(SELECT 1 FROM test_results tr WHERE tr.run_id=r.id) "
            "ORDER BY r.id DESC LIMIT 1",
            (sha,),
        ).fetchone()
        return int(r[0]) if r else None

    def other_clean(sha: str, primary: int) -> list[int]:
        return [
            int(x[0])
            for x in conn.execute(
                "SELECT r.id FROM test_runs r WHERE r.sha=? AND r.dirty=0 AND r.id!=? "
                "AND EXISTS(SELECT 1 FROM test_results tr WHERE tr.run_id=r.id) ORDER BY r.id",
                (sha, primary),
            )
        ]

    def results(run_id: int):
        return list(
            conn.execute(
                "SELECT t.test_id, r.outcome, r.duration_ms, r.flaky FROM test_results r "
                "JOIN tests t ON t.id=r.test_pk WHERE r.run_id=? ORDER BY t.test_id",
                (run_id,),
            )
        )

    def load_cov(run_id: int) -> dict[str, dict[str, set[int]]]:
        by: dict[str, dict[str, set[int]]] = defaultdict(dict)
        for tid, path, blob in conn.execute(
            "SELECT t.test_id, c.path, c.lines FROM test_coverage c "
            "JOIN tests t ON t.id=c.test_pk WHERE c.run_id=?",
            (run_id,),
        ):
            by[tid][path] = decode_lines(blob)
        return by

    def last_cov_run(sha: str) -> int | None:
        r = conn.execute(
            "SELECT r.id FROM test_runs r WHERE r.sha=? AND r.dirty=0 "
            "AND EXISTS(SELECT 1 FROM test_coverage c WHERE c.run_id=r.id) "
            "ORDER BY r.id DESC LIMIT 1",
            (sha,),
        ).fetchone()
        return int(r[0]) if r else None

    for sha, t0 in commits:
        parent = first_parent(repo, sha)
        if parent is None:
            rid = last_cov_run(sha)
            if rid:
                cov_snaps[sha] = load_cov(rid)
            continue

        primary = last_clean_with_results(sha)
        if primary is None:
            rid = last_cov_run(sha)
            if rid:
                cov_snaps[sha] = load_cov(rid)
            continue

        hunks = parse_hunks(repo, parent, sha)
        paths = [h["path"] for h in hunks]
        srcf = src_files(paths)
        old_sources: dict[str, str] = {}
        funcs: list[str] = []
        ch_add = ch_rm = 0
        for h in hunks:
            ch_add += len(h["new_lines"])
            ch_rm += len(h["old_lines"])
            if h["path"].endswith(".py") and not is_test_path(h["path"]):
                op = h["old_path"] or h["path"]
                src = show_blob(repo, parent, op)
                if src is not None:
                    old_sources[h["path"]] = src
                funcs.extend(changed_funcs(h["path"], src, h["old_lines"]))
        funcs = sorted(set(funcs))
        ch_nfunc = len(funcs)
        commit_idx += 1
        res = results(primary)

        chain = [parent]
        cur = parent
        for _ in range(10000):
            pp = first_parent(repo, cur)
            if not pp:
                break
            chain.append(pp)
            cur = pp
        cov = {}
        lag = 0
        for csha in chain:
            if csha in cov_snaps:
                cov = cov_snaps[csha]
                break
            lag += 1

        # Import distances for test files in this run
        tfs = sorted({test_file_of(tid) for tid, *_ in res})
        seeds = sorted(set(tfs) | set(srcf))
        dist_map = build_import_dist(repo, parent, seeds, srcf)

        for tid, outcome, dur, flaky in res:
            tf = test_file_of(tid)
            cc_max = cc_sum = 0.0
            for f in srcf:
                w = 0.0
                for files, t in co_events:
                    if f in files and tf in files:
                        w += decay(t0, t)
                cc_max = max(cc_max, w)
                cc_sum += w

            imp_dist = dist_map.get(tf, NO_PATH)
            src_tok: set[str] = set()
            for f in srcf:
                src_tok |= tokenize(f)
                src_tok |= tokenize(Path(f).stem)
            test_tok = tokenize(tid) | tokenize(tf) | tokenize(Path(tf).stem)
            inter = src_tok & test_tok
            name_file = 1 if inter else 0
            name_jac = (len(inter) / len(src_tok)) if src_tok else 0.0
            name_func = 0
            for q in funcs:
                short = q.rsplit("::", 1)[-1].rsplit(".", 1)[-1]
                if short == "<module>" or len(short) < 3:
                    continue
                if short.lower() in tid.lower():
                    name_func = 1
                    break

            paths_cov = cov.get(tid, {})
            cov_known = 1 if paths_cov else 0
            cov_file = 0
            cov_lines = 0
            cov_func = 0
            cov_breadth = sum(len(v) for v in paths_cov.values())
            module_hit = False
            for f in srcf:
                covered = paths_cov.get(f, set())
                if not covered:
                    continue
                cov_file = 1
                hunk = next((h for h in hunks if h["path"] == f), None)
                old_changed = hunk["old_lines"] if hunk else set()
                inter_ln = covered & old_changed
                cov_lines += len(inter_ln)
                src = old_sources.get(f)
                if src is not None:
                    defs = scan_defs(src)
                    l2d: dict[int, str] = {}
                    for qual, kind, start, end in sorted(
                        defs, key=lambda d: d[3] - d[2]
                    ):
                        if kind == "class":
                            continue
                        for ln in range(start, end):
                            l2d[ln] = qual
                    funcs_hit = set()
                    for ln in inter_ln:
                        if ln in l2d:
                            funcs_hit.add(l2d[ln])
                        else:
                            module_hit = True
                    for ln in old_changed:
                        if ln not in covered and ln not in l2d:
                            module_hit = True
                    cov_func += len(funcs_hit)
            cov_mod = 1 if module_hit and imp_dist < 99 else 0

            st = test_stats[tid]
            best_pf = None
            for f in srcf:
                fails, samples = file_test[(f, tid)]
                if samples:
                    r = fails / samples
                    best_pf = r if best_pf is None else max(best_pf, r)
            best_pq = None
            for q in funcs:
                fails, samples = func_test[(q, tid)]
                if samples:
                    r = fails / samples
                    best_pq = r if best_pq is None else max(best_pq, r)

            row = {
                "repo": repo_name,
                "change": sha,
                "parent": parent,
                "commit_idx": commit_idx,
                "time": t0,
                "test": tid,
                "label": 1 if outcome in ("fail", "error") else 0,
                "flaky": int(flaky),
                "cc_max": cc_max,
                "cc_sum": cc_sum,
                "imp_dist": imp_dist,
                "imp_direct": 1 if imp_dist == 1 else 0,
                "name_file": name_file,
                "name_jac": name_jac,
                "name_func": name_func,
                "cov_known": cov_known,
                "cov_file": cov_file,
                "cov_func": cov_func,
                "cov_lines": cov_lines,
                "cov_breadth": cov_breadth,
                "cov_mod": cov_mod,
                "cov_lag": lag if cov else 0,
                "t_fail_rate": (st["fails"] / st["obs"]) if st["obs"] else 0.0,
                "t_fail_cnt": st["fails"],
                "t_obs": st["obs"],
                "pf_max": -1.0 if best_pf is None else best_pf,
                "pq_max": -1.0 if best_pq is None else best_pq,
                "t_age_days": ((t0 - st["first"]) / 86400.0) if st["first"] is not None else 0.0,
                "dur": st["dur"] if st["dur"] is not None else -1,
                "tfile_ntests": len(tests_in_file.get(tf, ())),
                "t_changed": 1 if tf in paths else 0,
                "t_flaky_rate": (st["flaky"] / st["obs"]) if st["obs"] else 0.0,
                "ch_nfiles": len(paths),
                "ch_nsrc": len(srcf),
                "ch_add": ch_add,
                "ch_rm": ch_rm,
                "ch_nfunc": ch_nfunc,
            }
            rows.append(row)

        for tid, outcome, dur, flaky in res:
            tf = test_file_of(tid)
            st = test_stats[tid]
            st["obs"] += 1
            if outcome in ("fail", "error"):
                st["fails"] += 1
            if flaky:
                st["flaky"] += 1
            if st["first"] is None:
                st["first"] = t0
            st["dur"] = dur
            tests_in_file[tf].add(tid)
            failed = outcome in ("fail", "error")
            for f in srcf:
                file_test[(f, tid)][1] += 1
                if failed:
                    file_test[(f, tid)][0] += 1
            for q in funcs:
                func_test[(q, tid)][1] += 1
                if failed:
                    func_test[(q, tid)][0] += 1
        for oid in other_clean(sha, primary):
            for tid, outcome, dur, flaky in results(oid):
                st = test_stats[tid]
                st["obs"] += 1
                if flaky:
                    st["flaky"] += 1
        if 2 <= len(paths) <= 50:
            co_events.append((paths, t0))
        rid = last_cov_run(sha)
        if rid:
            cov_snaps[sha] = load_cov(rid)

    rows.sort(key=lambda r: (r["time"], r["change"], r["test"]))
    args.out.write_text(json.dumps(rows, indent=2, sort_keys=True))
    print(f"wrote {len(rows)} rows to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
