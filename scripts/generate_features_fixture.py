#!/usr/bin/env python3
"""Generate a tiny git repo + seeded .testpilot DB for features oracle tests.

Does not commit the generated .git into the TestPilot tree — write into --out DIR
(CI / local temp). Uses fixed GIT_AUTHOR_DATE / GIT_COMMITTER_DATE.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT_SCHEMA = Path(__file__).resolve().parents[1] / "crates" / "testpilot-core" / "schema.sql"


def run(cwd: Path, args: list[str], env: dict | None = None) -> str:
    e = os.environ.copy()
    if env:
        e.update(env)
    out = subprocess.check_output(args, cwd=cwd, env=e, text=True)
    return out.strip()


def git(cwd: Path, args: list[str], date: str) -> None:
    env = {
        "GIT_AUTHOR_NAME": "Fix",
        "GIT_AUTHOR_EMAIL": "fix@example.com",
        "GIT_COMMITTER_NAME": "Fix",
        "GIT_COMMITTER_EMAIL": "fix@example.com",
        "GIT_AUTHOR_DATE": date,
        "GIT_COMMITTER_DATE": date,
    }
    run(cwd, ["git", *args], env)


def write(cwd: Path, rel: str, body: str) -> None:
    path = cwd / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


def encode_lines(lines: list[int]) -> bytes:
    """Match Rust codec: 0x01 + delta varints."""
    lines = sorted(set(lines))
    out = bytearray([0x01])
    prev = 0
    for i, line in enumerate(lines):
        delta = line if i == 0 else line - prev
        prev = line
        while True:
            byte = delta & 0x7F
            delta >>= 7
            if delta:
                out.append(byte | 0x80)
            else:
                out.append(byte)
                break
    return bytes(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True, help="empty directory for fixture repo")
    args = ap.parse_args()
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        print(f"refusing non-empty --out {out}", file=sys.stderr)
        return 2

    git(out, ["init", "-b", "main"], "2020-01-01T00:00:00+00:00")
    git(out, ["config", "user.email", "fix@example.com"], "2020-01-01T00:00:00+00:00")
    git(out, ["config", "user.name", "Fix"], "2020-01-01T00:00:00+00:00")

    # C0
    write(
        out,
        "pkg/a.py",
        "def alpha():\n    return 1\n\ndef beta():\n    return 2\n",
    )
    write(out, "pkg/__init__.py", "")
    write(
        out,
        "tests/test_a.py",
        "from pkg.a import alpha, beta\n\ndef test_alpha():\n    assert alpha() == 1\n\ndef test_beta():\n    assert beta() == 2\n",
    )
    git(out, ["add", "."], "2020-01-01T00:00:00+00:00")
    git(out, ["commit", "-m", "c0 init"], "2020-01-01T00:00:00+00:00")
    c0 = run(out, ["git", "rev-parse", "HEAD"])

    # C1 — change alpha (will fail test_alpha later)
    write(
        out,
        "pkg/a.py",
        "def alpha():\n    return 99\n\ndef beta():\n    return 2\n",
    )
    git(out, ["add", "."], "2020-01-02T00:00:00+00:00")
    git(out, ["commit", "-m", "c1 break alpha"], "2020-01-02T00:00:00+00:00")
    c1 = run(out, ["git", "rev-parse", "HEAD"])

    # C2 — add util + import from tests
    write(out, "pkg/util.py", "def helper():\n    return 3\n")
    write(
        out,
        "tests/test_a.py",
        "from pkg.a import alpha, beta\nfrom pkg.util import helper\n\ndef test_alpha():\n    assert alpha() == 99\n\ndef test_beta():\n    assert beta() == 2\n\ndef test_helper():\n    assert helper() == 3\n",
    )
    git(out, ["add", "."], "2020-01-03T00:00:00+00:00")
    git(out, ["commit", "-m", "c2 util"], "2020-01-03T00:00:00+00:00")
    c2 = run(out, ["git", "rev-parse", "HEAD"])

    # C3 — rename-ish modify util
    write(out, "pkg/util.py", "def helper():\n    return 4\n")
    git(out, ["add", "."], "2020-01-04T00:00:00+00:00")
    git(out, ["commit", "-m", "c3 util bump"], "2020-01-04T00:00:00+00:00")
    c3 = run(out, ["git", "rev-parse", "HEAD"])

    # C4 — gamma
    write(
        out,
        "pkg/a.py",
        "def alpha():\n    return 99\n\ndef beta():\n    return 2\n\ndef gamma():\n    return 0\n",
    )
    git(out, ["add", "."], "2020-01-05T00:00:00+00:00")
    git(out, ["commit", "-m", "c4 gamma"], "2020-01-05T00:00:00+00:00")
    c4 = run(out, ["git", "rev-parse", "HEAD"])

    # C5 — rename util → helpers (cochange/coverage path continuity)
    git(out, ["mv", "pkg/util.py", "pkg/helpers.py"], "2020-01-06T00:00:00+00:00")
    write(
        out,
        "tests/test_a.py",
        "from pkg.a import alpha, beta\nfrom pkg.helpers import helper\n\ndef test_alpha():\n    assert alpha() == 99\n\ndef test_beta():\n    assert beta() == 2\n\ndef test_helper():\n    assert helper() == 4\n",
    )
    git(out, ["add", "."], "2020-01-06T00:00:00+00:00")
    git(out, ["commit", "-m", "c5 rename util"], "2020-01-06T00:00:00+00:00")
    c5 = run(out, ["git", "rev-parse", "HEAD"])

    # C6 — delete dead.py
    write(out, "pkg/dead.py", "def gone():\n    return 0\n")
    git(out, ["add", "pkg/dead.py"], "2020-01-07T00:00:00+00:00")
    git(out, ["commit", "-m", "c6 add dead"], "2020-01-07T00:00:00+00:00")
    c6 = run(out, ["git", "rev-parse", "HEAD"])
    git(out, ["rm", "pkg/dead.py"], "2020-01-08T00:00:00+00:00")
    git(out, ["commit", "-m", "c7 delete dead"], "2020-01-08T00:00:00+00:00")
    c7 = run(out, ["git", "rev-parse", "HEAD"])

    # C8 — binary blob (should not poison ch_*)
    (out / "pkg" / "data.bin").write_bytes(b"\x00\x01\x02\xffbinary")
    git(out, ["add", "pkg/data.bin"], "2020-01-09T00:00:00+00:00")
    git(out, ["commit", "-m", "c8 binary"], "2020-01-09T00:00:00+00:00")
    c8 = run(out, ["git", "rev-parse", "HEAD"])

    # C9 — broken Python syntax (dump must not panic)
    write(out, "pkg/broken.py", "def (\nclass\nimport\n")
    git(out, ["add", "pkg/broken.py"], "2020-01-10T00:00:00+00:00")
    git(out, ["commit", "-m", "c9 broken py"], "2020-01-10T00:00:00+00:00")
    c9 = run(out, ["git", "rev-parse", "HEAD"])

    # C10 — merge (first-parent only for change)
    git(out, ["checkout", "-b", "feature"], "2020-01-11T00:00:00+00:00")
    write(out, "pkg/feat.py", "def feat():\n    return 1\n")
    git(out, ["add", "pkg/feat.py"], "2020-01-11T00:00:00+00:00")
    git(out, ["commit", "-m", "c10 feature"], "2020-01-11T00:00:00+00:00")
    c10 = run(out, ["git", "rev-parse", "HEAD"])
    git(out, ["checkout", "main"], "2020-01-12T00:00:00+00:00")
    write(out, "pkg/mainline.py", "def mainline():\n    return 1\n")
    git(out, ["add", "pkg/mainline.py"], "2020-01-12T00:00:00+00:00")
    git(out, ["commit", "-m", "c11 mainline"], "2020-01-12T00:00:00+00:00")
    c11 = run(out, ["git", "rev-parse", "HEAD"])
    git(out, ["merge", "feature", "-m", "c12 merge"], "2020-01-13T00:00:00+00:00")
    c12 = run(out, ["git", "rev-parse", "HEAD"])

    # Main-line topo from HEAD: c0..c9,c11,c12 (c10 only on feature side).
    shas_main = []
    for line in run(out, ["git", "rev-list", "--topo-order", "--reverse", "HEAD"]).splitlines():
        shas_main.append(line)
    # Keep explicit labels for seeding
    labeled = {
        "c0": c0,
        "c1": c1,
        "c2": c2,
        "c3": c3,
        "c4": c4,
        "c5": c5,
        "c6": c6,
        "c7": c7,
        "c8": c8,
        "c9": c9,
        "c10": c10,
        "c11": c11,
        "c12": c12,
    }
    shas = shas_main
    # author times from git
    times = [int(run(out, ["git", "show", "-s", "--format=%at", s])) for s in shas]
    msgs = [run(out, ["git", "show", "-s", "--format=%s", s]) for s in shas]

    db_path = out / ".testpilot" / "db.sqlite"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(ROOT_SCHEMA.read_text())
    conn.execute("INSERT INTO schema_version(version) VALUES (3)")

    # Insert ALL reachable commits (including feature tip) for parent links
    all_shas = set(shas) | {c10}
    for s in all_shas:
        ts = int(run(out, ["git", "show", "-s", "--format=%at", s]))
        msg = run(out, ["git", "show", "-s", "--format=%s", s])
        oversized = 1 if s == c8 else 0  # treat binary commit as oversized edge
        conn.execute(
            "INSERT INTO commits(sha, author_ts, message, oversized) VALUES (?,?,?,?)",
            (s, ts, msg, oversized),
        )
    for s in all_shas:
        parents = run(out, ["git", "rev-list", "--parents", "-n", "1", s]).split()[1:]
        for idx, p in enumerate(parents):
            conn.execute(
                "INSERT INTO commit_parents(sha, parent_sha, parent_idx) VALUES (?,?,?)",
                (s, p, idx),
            )

    def add_fc(
        sha: str,
        path: str,
        ct: str = "M",
        add: int = 1,
        rm: int = 1,
        old_path: str | None = None,
        binary: int = 0,
    ) -> None:
        conn.execute(
            "INSERT INTO file_changes(sha,path,change_type,old_path,lines_added,lines_deleted,is_binary)"
            " VALUES (?,?,?,?,?,?,?)",
            (sha, path, ct, old_path, add, rm, binary),
        )

    add_fc(c1, "pkg/a.py")
    add_fc(c2, "pkg/util.py", "A", 2, 0)
    add_fc(c2, "tests/test_a.py")
    add_fc(c3, "pkg/util.py")
    add_fc(c4, "pkg/a.py")
    add_fc(c5, "pkg/helpers.py", "R", 0, 0, old_path="pkg/util.py")
    add_fc(c5, "tests/test_a.py")
    add_fc(c6, "pkg/dead.py", "A", 2, 0)
    add_fc(c7, "pkg/dead.py", "D", 0, 2)
    add_fc(c8, "pkg/data.bin", "A", 0, 0, binary=1)
    add_fc(c9, "pkg/broken.py", "A", 3, 0)
    add_fc(c11, "pkg/mainline.py", "A", 2, 0)
    # merge first-parent change vs c11
    add_fc(c12, "pkg/feat.py", "A", 2, 0)

    def upsert_test(tid: str) -> int:
        conn.execute("INSERT OR IGNORE INTO tests(test_id) VALUES (?)", (tid,))
        return conn.execute("SELECT id FROM tests WHERE test_id=?", (tid,)).fetchone()[0]

    def insert_run(
        sha: str,
        results: list[tuple[str, str, int, int]],
        coverage: list[tuple[str, str, list[int]]] | None = None,
        source: str = "pytest",
    ) -> int:
        cur = conn.execute(
            "INSERT INTO test_runs(sha,source,started_at,finished_at,dirty,diff_hash,input_hash,meta)"
            " VALUES (?,?,?,?,0,NULL,NULL,NULL)",
            (sha, source, 1, 2),
        )
        run_id = cur.lastrowid
        assert run_id is not None
        for tid, outcome, dur, flaky in results:
            pk = upsert_test(tid)
            conn.execute(
                "INSERT INTO test_results(run_id,test_pk,outcome,duration_ms,message,flaky,attempts)"
                " VALUES (?,?,?,?,NULL,?,1)",
                (run_id, pk, outcome, dur, flaky),
            )
        if coverage:
            for tid, path, lines in coverage:
                pk = upsert_test(tid)
                conn.execute(
                    "INSERT INTO test_coverage(run_id,test_pk,path,lines) VALUES (?,?,?,?)",
                    (run_id, pk, path, encode_lines(lines)),
                )
        return int(run_id)

    t_alpha = "tests/test_a.py::test_alpha"
    t_beta = "tests/test_a.py::test_beta"
    t_helper = "tests/test_a.py::test_helper"

    # c0 clean + coverage (on parent of c1)
    insert_run(
        c0,
        [(t_alpha, "pass", 10, 0), (t_beta, "pass", 11, 0)],
        [
            (t_alpha, "pkg/a.py", [1, 2]),
            (t_beta, "pkg/a.py", [4, 5]),
        ],
    )
    # c1: fail alpha
    insert_run(
        c1,
        [(t_alpha, "fail", 12, 0), (t_beta, "pass", 11, 0)],
        [
            (t_alpha, "pkg/a.py", [1, 2]),
            (t_beta, "pkg/a.py", [4, 5]),
        ],
    )
    # extra clean run on c1 → flaky only
    insert_run(
        c1,
        [(t_alpha, "pass", 12, 1), (t_beta, "pass", 11, 0)],
    )
    # c2
    insert_run(
        c2,
        [
            (t_alpha, "pass", 13, 0),
            (t_beta, "pass", 11, 0),
            (t_helper, "pass", 5, 0),
        ],
        [
            (t_helper, "pkg/util.py", [1, 2]),
            (t_alpha, "pkg/a.py", [1, 2]),
        ],
    )
    # c3 junit-only primary (no coverage on this run)
    insert_run(
        c3,
        [
            (t_alpha, "pass", 13, 0),
            (t_beta, "pass", 11, 0),
            (t_helper, "fail", 6, 0),
        ],
        coverage=None,
        source="junit",
    )
    # c4
    insert_run(
        c4,
        [
            (t_alpha, "pass", 14, 0),
            (t_beta, "pass", 11, 0),
            (t_helper, "pass", 6, 0),
        ],
        [(t_alpha, "pkg/a.py", [1, 2, 7])],
    )
    # c5 rename — coverage on new path
    insert_run(
        c5,
        [
            (t_alpha, "pass", 14, 0),
            (t_beta, "pass", 11, 0),
            (t_helper, "pass", 6, 0),
        ],
        [
            (t_helper, "pkg/helpers.py", [1, 2]),
            (t_alpha, "pkg/a.py", [1, 2]),
        ],
    )
    for sha in (c6, c7, c8, c9, c11, c12):
        insert_run(
            sha,
            [
                (t_alpha, "pass", 14, 0),
                (t_beta, "pass", 11, 0),
                (t_helper, "pass", 6, 0),
            ],
        )

    conn.commit()
    conn.close()

    meta = out / "FIXTURE.txt"
    lines = [f"{k}={v}" for k, v in labeled.items()] + shas
    meta.write_text("\n".join(lines) + "\n")
    print(f"fixture ready at {out}")
    print(f"db={db_path}")
    for s in shas:
        print(s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
