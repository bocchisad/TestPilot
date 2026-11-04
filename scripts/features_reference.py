#!/usr/bin/env python3
"""Naive reference implementation of Week-2 features (must match Rust dump)."""

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
    # parents before children via rev-list --topo-order --reverse
    lines = git(cwd, "rev-list", "--topo-order", "--reverse", "HEAD").splitlines()
    out = []
    for sha in lines:
        ts = int(git(cwd, "show", "-s", "--format=%at", sha))
        out.append((sha, ts))
    # stable tie-break by sha among equal? git topo is enough for fixture
    return out


def first_parent(cwd: Path, sha: str) -> str | None:
    ps = git(cwd, "rev-list", "--parents", "-n", "1", sha).split()
    if len(ps) < 2:
        return None
    return ps[1]


def changed_paths(cwd: Path, parent: str, sha: str) -> list[str]:
    out = git(cwd, "diff", "--name-only", f"{parent}..{sha}")
    return [p for p in out.splitlines() if p]


def is_test_path(path: str) -> bool:
    p = path.replace("\\", "/")
    if "/tests/" in p or p.startswith("tests/"):
        return True
    return "test" in p.rsplit("/", 1)[-1].lower()


def src_files(paths: list[str]) -> list[str]:
    return sorted({p for p in paths if p.endswith(".py") and not is_test_path(p)})


def tokenize(s: str) -> set[str]:
    s = s.replace("_", " ")
    # approximate lockfile regex without lookaround
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
    # history
    test_stats: dict[str, dict] = defaultdict(
        lambda: {"obs": 0, "fails": 0, "flaky": 0, "first": None, "dur": None}
    )
    file_test: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    func_test: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    tests_in_file: dict[str, set[str]] = defaultdict(set)
    co_events: list[tuple[list[str], int]] = []
    cov_snaps: dict[str, dict[str, dict[str, set[int]]]] = {}
    processed: list[str] = []
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
            processed.append(sha)
            continue

        primary = last_clean_with_results(sha)
        if primary is None:
            rid = last_cov_run(sha)
            if rid:
                cov_snaps[sha] = load_cov(rid)
            processed.append(sha)
            continue

        paths = changed_paths(repo, parent, sha)
        srcf = src_files(paths)
        commit_idx += 1
        res = results(primary)

        # coverage lookup: parent then earlier
        chain = [parent]
        cur = parent
        for _ in range(10000):
            pp = first_parent(repo, cur)
            if not pp:
                break
            chain.append(pp)
            cur = pp
        cov = None
        lag = 0
        for csha in chain:
            if csha in cov_snaps:
                cov = cov_snaps[csha]
                break
            lag += 1
        if cov is None:
            cov = {}
            lag = 0

        for tid, outcome, dur, flaky in res:
            tf = test_file_of(tid)
            # cochange
            cc_max = cc_sum = 0.0
            for f in srcf:
                w = 0.0
                for files, t in co_events:
                    if f in files and tf in files:
                        w += decay(t0, t)
                cc_max = max(cc_max, w)
                cc_sum += w
            # imports: cheap — 99 unless test file appears to import src basename
            imp_dist = NO_PATH
            try:
                content = git(repo, "show", f"{parent}:{tf}")
                for f in srcf:
                    mod = Path(f).stem
                    if re.search(rf"\b{re.escape(mod)}\b", content):
                        imp_dist = 1
                        break
            except subprocess.CalledProcessError:
                pass
            # names
            src_tok: set[str] = set()
            for f in srcf:
                src_tok |= tokenize(f)
                src_tok |= tokenize(Path(f).stem)
            test_tok = tokenize(tid) | tokenize(tf) | tokenize(Path(tf).stem)
            inter = src_tok & test_tok
            name_file = 1 if inter else 0
            name_jac = (len(inter) / len(src_tok)) if src_tok else 0.0
            name_func = 0
            # coverage
            paths_cov = cov.get(tid, {})
            cov_known = 1 if paths_cov else 0
            cov_file = 0
            cov_lines = 0
            cov_breadth = sum(len(v) for v in paths_cov.values())
            for f in srcf:
                if f in paths_cov and paths_cov[f]:
                    cov_file = 1
            st = test_stats[tid]
            label = 1 if outcome in ("fail", "error") else 0
            row = {
                "repo": repo_name,
                "change": sha,
                "parent": parent,
                "commit_idx": commit_idx,
                "time": t0,
                "test": tid,
                "label": label,
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
                "cov_func": 0,
                "cov_lines": cov_lines,
                "cov_breadth": cov_breadth,
                "cov_mod": 0,
                "cov_lag": lag if cov_known or cov else 0,
                "t_fail_rate": (st["fails"] / st["obs"]) if st["obs"] else 0.0,
                "t_fail_cnt": st["fails"],
                "t_obs": st["obs"],
                "pf_max": -1.0,
                "pq_max": -1.0,
                "t_age_days": ((t0 - st["first"]) / 86400.0) if st["first"] is not None else 0.0,
                "dur": st["dur"] if st["dur"] is not None else -1,
                "tfile_ntests": len(tests_in_file.get(tf, ())),
                "t_changed": 1 if tf in paths else 0,
                "t_flaky_rate": (st["flaky"] / st["obs"]) if st["obs"] else 0.0,
                "ch_nfiles": len(paths),
                "ch_nsrc": len(srcf),
                "ch_add": 0,
                "ch_rm": 0,
                "ch_nfunc": 0,
            }
            # pf_max
            best = None
            for f in srcf:
                fails, samples = file_test[(f, tid)]
                if samples:
                    r = fails / samples
                    best = r if best is None else max(best, r)
            row["pf_max"] = -1.0 if best is None else best
            rows.append(row)

        # updates
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
        for oid in other_clean(sha, primary):
            for tid, outcome, dur, flaky in results(oid):
                st = test_stats[tid]
                st["obs"] += 1
                if flaky:
                    st["flaky"] += 1
        if len(paths) <= 50 and len(paths) >= 2:
            co_events.append((paths, t0))
        rid = last_cov_run(sha)
        if rid:
            cov_snaps[sha] = load_cov(rid)
        processed.append(sha)

    rows.sort(key=lambda r: (r["time"], r["change"], r["test"]))
    args.out.write_text(json.dumps(rows, indent=2, sort_keys=True))
    print(f"wrote {len(rows)} rows to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
