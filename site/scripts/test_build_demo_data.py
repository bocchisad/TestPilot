#!/usr/bin/env python3
"""Tests for site/scripts/build-demo-data. Fixtures stay in temp dirs."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build-demo-data"


def run_script(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
    )


def git(repo: Path, *args: str) -> str:
    out = subprocess.check_output(["git", "-C", str(repo), *args], text=True)
    return out.strip()


def init_repo(message: str = "fixture subject") -> tuple[Path, str]:
    raw = tempfile.mkdtemp()
    repo = Path(raw)
    subprocess.check_call(["git", "-C", str(repo), "init", "-q"])
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "Fixture")
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    git(repo, "add", "a.txt")
    git(repo, "commit", "-q", "-m", message)
    sha = git(repo, "rev-parse", "HEAD")
    git(repo, "remote", "add", "origin", "https://github.com/pallets/click.git")
    return repo, sha


def ranking(sha: str, *, ranker: str | None, tests: list[dict] | None = None, n_selected: int | None = None) -> dict:
    rows = tests or [
        {"test_id": "t::a", "score": 0.1, "rank": 1, "dur": -1, "reason": "ranked"},
        {"test_id": "t::b", "score": 0.2, "rank": 2, "dur": -1, "reason": "ranked"},
    ]
    body: dict = {
        "model_sha256": "abc",
        "features_version": 2,
        "base": sha,
        "head": sha,
        "base_run_sha": sha,
        "n_candidates": 4,
        "n_selected": len(rows) if n_selected is None else n_selected,
        "n_forced": 0,
        "n_safe_added": 0,
        "n_ranked": len(rows),
        "budget": {"kind": "frac", "value": 0.1},
        "safe": {
            "enabled": True,
            "fallback_all": False,
            "reasons": [],
            "thresholds": {
                "recent_k": 5,
                "src_frac": 0.3,
                "min_candidates": 50,
                "include_covered": False,
                "non_py_source": True,
            },
        },
        "tests": rows,
    }
    if ranker is not None:
        body["ranker"] = ranker
    return body


def write_input(repo: Path, sha: str, ranker_in_select: str | None, model_ranker: str | None) -> Path:
    inp = Path(tempfile.mkdtemp())
    (inp / "source.json").write_text(
        json.dumps(
            {
                "target_repo": "https://github.com/pallets/click",
                "target_sha": sha,
                "testpilot_version": "0.1.0 (fixture)",
                "generated_at": "2026-11-18T20:57:55Z",
                "clone": str(repo),
            }
        ),
        encoding="utf-8",
    )
    model: dict = {"schema_sha256": "deadbeef"}
    if model_ranker is not None:
        model["ranker"] = model_ranker
    (inp / "model.json").write_text(json.dumps(model), encoding="utf-8")
    (inp / "report.json").write_text("{}", encoding="utf-8")
    rank_dir = inp / "rankings"
    rank_dir.mkdir()
    (rank_dir / f"{sha}.json").write_text(
        json.dumps(ranking(sha, ranker=ranker_in_select)),
        encoding="utf-8",
    )
    return inp


class BuildDemoDataTest(unittest.TestCase):
    def test_placeholder(self) -> None:
        out = Path(tempfile.mkdtemp()) / "demo.json"
        result = run_script(["--placeholder", "--out", str(out)])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(out.read_text(encoding="utf-8")),
            {"placeholder": True, "source": None, "cases": []},
        )

    def test_missing_rankings_leaves_output(self) -> None:
        out = Path(tempfile.mkdtemp()) / "demo.json"
        out.write_text('{"keep":true}\n', encoding="utf-8")
        before = out.read_bytes()
        inp = Path(tempfile.mkdtemp())
        result = run_script(["--input", str(inp), "--out", str(out)])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(out.read_bytes(), before)

    def test_selected_count_mismatch(self) -> None:
        repo, sha = init_repo()
        inp = write_input(repo, sha, "model", "model")
        payload = ranking(sha, ranker="model", n_selected=9)
        (inp / "rankings" / f"{sha}.json").write_text(json.dumps(payload), encoding="utf-8")
        out = Path(tempfile.mkdtemp()) / "demo.json"
        result = run_script(["--input", str(inp), "--out", str(out)])
        self.assertEqual(result.returncode, 2)
        self.assertFalse(out.exists())

    def test_filename_must_match_head(self) -> None:
        repo, sha = init_repo()
        inp = write_input(repo, sha, "model", "model")
        path = inp / "rankings" / f"{sha}.json"
        path.rename(inp / "rankings" / ("0" * 40 + ".json"))
        out = Path(tempfile.mkdtemp()) / "demo.json"
        result = run_script(["--input", str(inp), "--out", str(out)])
        self.assertEqual(result.returncode, 2, result.stderr)

    def test_with_ranker_field(self) -> None:
        repo, sha = init_repo()
        inp = write_input(repo, sha, "model", "model")
        out = Path(tempfile.mkdtemp()) / "demo.json"
        result = run_script(["--input", str(inp), "--out", str(out)])
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertFalse(data["placeholder"])
        case = data["cases"][0]
        self.assertEqual(case["score"] if False else case["tests"][0]["score"], "0.1")
        self.assertIsNone(case["saved_ms"])
        self.assertIsNone(case["caught"])
        self.assertFalse(case["synthetic"])
        self.assertEqual(case["subject"], "fixture subject")
        self.assertEqual(case["ranker"], "model")

    def test_missing_select_ranker_uses_model(self) -> None:
        repo, sha = init_repo()
        inp = write_input(repo, sha, None, "cov_lines")
        out = Path(tempfile.mkdtemp()) / "demo.json"
        result = run_script(["--input", str(inp), "--out", str(out)])
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(data["cases"][0]["ranker"], "cov_lines")

    def test_ranker_required_somewhere(self) -> None:
        repo, sha = init_repo()
        inp = write_input(repo, sha, None, None)
        out = Path(tempfile.mkdtemp()) / "demo.json"
        result = run_script(["--input", str(inp), "--out", str(out)])
        self.assertEqual(result.returncode, 2)
        self.assertFalse(out.exists())

    def test_ranker_mismatch(self) -> None:
        repo, sha = init_repo()
        inp = write_input(repo, sha, "model", "cov_lines")
        out = Path(tempfile.mkdtemp()) / "demo.json"
        result = run_script(["--input", str(inp), "--out", str(out)])
        self.assertEqual(result.returncode, 2)
        self.assertFalse(out.exists())

    def test_synthetic_ref(self) -> None:
        repo, sha = init_repo()
        git(repo, "update-ref", "refs/testpilot/mutants/c/0", sha)
        inp = write_input(repo, sha, "model", "model")
        out = Path(tempfile.mkdtemp()) / "demo.json"
        result = run_script(["--input", str(inp), "--out", str(out)])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(out.read_text(encoding="utf-8"))["cases"][0]["synthetic"])

        (repo / "b.txt").write_text("b\n", encoding="utf-8")
        git(repo, "add", "b.txt")
        git(repo, "commit", "-q", "-m", "real follow-up")
        real = git(repo, "rev-parse", "HEAD")
        # source head must match clone HEAD
        source = json.loads((inp / "source.json").read_text(encoding="utf-8"))
        source["target_sha"] = real
        (inp / "source.json").write_text(json.dumps(source), encoding="utf-8")
        (inp / "rankings" / f"{sha}.json").rename(inp / "rankings" / f"{real}.json")
        payload = ranking(real, ranker="model")
        (inp / "rankings" / f"{real}.json").write_text(json.dumps(payload), encoding="utf-8")
        out2 = Path(tempfile.mkdtemp()) / "demo.json"
        result = run_script(["--input", str(inp), "--out", str(out2)])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(out2.read_text(encoding="utf-8"))["cases"][0]["synthetic"])

    def test_saved_ms(self) -> None:
        repo, sha = init_repo()
        inp = write_input(repo, sha, "model", "model")
        (inp / "candidates").mkdir()
        (inp / "candidates" / f"{sha}.json").write_text(
            json.dumps(
                {
                    "tests": [
                        {"test_id": "t::a", "dur": 5},
                        {"test_id": "t::c", "dur": 1000},
                    ]
                }
            ),
            encoding="utf-8",
        )
        out = Path(tempfile.mkdtemp()) / "demo.json"
        result = run_script(["--input", str(inp), "--out", str(out)])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["cases"][0]["saved_ms"], 1000)

    def test_saved_ms_unknown_dur(self) -> None:
        repo, sha = init_repo()
        inp = write_input(repo, sha, "model", "model")
        (inp / "candidates").mkdir()
        (inp / "candidates" / f"{sha}.json").write_text(
            json.dumps({"tests": [{"test_id": "t::c", "dur": -1}]}),
            encoding="utf-8",
        )
        out = Path(tempfile.mkdtemp()) / "demo.json"
        result = run_script(["--input", str(inp), "--out", str(out)])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(json.loads(out.read_text(encoding="utf-8"))["cases"][0]["saved_ms"])

    def test_caught_true(self) -> None:
        repo, sha = init_repo()
        inp = write_input(repo, sha, "model", "model")
        (inp / "outcomes").mkdir()
        (inp / "outcomes" / f"{sha}.json").write_text(
            json.dumps(
                {
                    "complete": True,
                    "tests": {"t::a": "fail", "t::b": "pass"},
                    "base": {"complete": True, "tests": {"t::a": "pass", "t::b": "pass"}},
                }
            ),
            encoding="utf-8",
        )
        out = Path(tempfile.mkdtemp()) / "demo.json"
        result = run_script(["--input", str(inp), "--out", str(out)])
        self.assertEqual(result.returncode, 0, result.stderr)
        case = json.loads(out.read_text(encoding="utf-8"))["cases"][0]
        self.assertTrue(case["caught"])
        self.assertIsNone(case["caught_reason"])
        self.assertEqual(case["new_fail_n"], 1)
        self.assertEqual(case["new_fail_selected"], 1)
        self.assertEqual(case["new_fail_tests"], ["t::a"])
        self.assertEqual(case["budget_frac"], 0.1)

    def test_uncaught_shared_failure_not_selected_is_false(self) -> None:
        repo, sha = init_repo()
        inp = write_input(repo, sha, "model", "model")
        ranking_path = inp / "rankings" / f"{sha}.json"
        ranking_doc = json.loads(ranking_path.read_text(encoding="utf-8"))
        ranking_doc["tests"] = [
            {"test_id": "t::b", "score": 0.2, "rank": 1, "dur": -1, "reason": "ranked"}
        ]
        ranking_doc["n_selected"] = 1
        ranking_doc["n_ranked"] = 1
        ranking_path.write_text(json.dumps(ranking_doc), encoding="utf-8")
        (inp / "outcomes").mkdir()
        (inp / "outcomes" / f"{sha}.json").write_text(
            json.dumps(
                {
                    "complete": True,
                    "tests": {"t::a": "fail", "t::b": "pass"},
                    "base": {"complete": True, "tests": {"t::a": "pass", "t::b": "pass"}},
                }
            ),
            encoding="utf-8",
        )
        out = Path(tempfile.mkdtemp()) / "demo.json"
        result = run_script(["--input", str(inp), "--out", str(out)])
        self.assertEqual(result.returncode, 0, result.stderr)
        case = json.loads(out.read_text(encoding="utf-8"))["cases"][0]
        self.assertFalse(case["caught"])
        self.assertIsNone(case["caught_reason"])
        self.assertEqual(case["new_fail_n"], 1)
        self.assertEqual(case["new_fail_selected"], 0)
        self.assertEqual(case["new_fail_tests"], ["t::a"])

    def test_caught_incomplete_is_null(self) -> None:
        repo, sha = init_repo()
        inp = write_input(repo, sha, "model", "model")
        (inp / "outcomes").mkdir()
        (inp / "outcomes" / f"{sha}.json").write_text(
            json.dumps({"complete": False, "tests": {"t::a": "pass", "t::b": "pass"}}),
            encoding="utf-8",
        )
        out = Path(tempfile.mkdtemp()) / "demo.json"
        result = run_script(["--input", str(inp), "--out", str(out)])
        self.assertEqual(result.returncode, 0, result.stderr)
        case = json.loads(out.read_text(encoding="utf-8"))["cases"][0]
        self.assertIsNone(case["caught"])
        self.assertEqual(case["caught_reason"], "no_outcomes")

    def test_failure_absent_from_base_is_counted_separately(self) -> None:
        repo, sha = init_repo()
        inp = write_input(repo, sha, "model", "model")
        (inp / "outcomes").mkdir()
        (inp / "outcomes" / f"{sha}.json").write_text(
            json.dumps(
                {
                    "complete": True,
                    "tests": {"t::a": "pass", "t::b": "pass", "t::missed": "error"},
                    "base": {"complete": True, "tests": {"t::a": "pass"}},
                }
            ),
            encoding="utf-8",
        )
        out = Path(tempfile.mkdtemp()) / "demo.json"
        result = run_script(["--input", str(inp), "--out", str(out)])
        self.assertEqual(result.returncode, 0, result.stderr)
        case = json.loads(out.read_text(encoding="utf-8"))["cases"][0]
        self.assertIsNone(case["caught"])
        self.assertEqual(case["caught_reason"], "nothing_to_catch")
        self.assertEqual(case["new_fail_n"], 0)
        self.assertEqual(case["new_fail_selected"], 0)
        self.assertEqual(case["new_fail_tests"], [])
        self.assertEqual(case["missing_from_base_test_count"], 1)
        self.assertEqual(case["missing_from_base_tests"], ["t::missed"])

    def test_caught_only_flaky(self) -> None:
        repo, sha = init_repo()
        inp = write_input(repo, sha, "model", "model")
        (inp / "outcomes").mkdir()
        (inp / "outcomes" / f"{sha}.json").write_text(
            json.dumps(
                {
                    "complete": True,
                    "tests": {
                        "t::a": {"outcome": "error", "flaky": True},
                        "t::b": "pass",
                        "t::old": "fail",
                    },
                    "base": {"complete": True, "tests": {"t::a": "error", "t::old": "fail"}},
                }
            ),
            encoding="utf-8",
        )
        out = Path(tempfile.mkdtemp()) / "demo.json"
        result = run_script(["--input", str(inp), "--out", str(out)])
        self.assertEqual(result.returncode, 0, result.stderr)
        case = json.loads(out.read_text(encoding="utf-8"))["cases"][0]
        self.assertIsNone(case["caught"])
        self.assertEqual(case["caught_reason"], "only_flaky")
        self.assertEqual(case["new_fail_tests"], [])

    def test_persistent_failure_is_nothing_to_catch(self) -> None:
        repo, sha = init_repo()
        inp = write_input(repo, sha, "model", "model")
        (inp / "outcomes").mkdir()
        (inp / "outcomes" / f"{sha}.json").write_text(
            json.dumps(
                {
                    "complete": True,
                    "tests": {"t::a": "fail", "t::b": "pass"},
                    "base": {"complete": True, "tests": {"t::a": "fail", "t::b": "pass"}},
                }
            ),
            encoding="utf-8",
        )
        out = Path(tempfile.mkdtemp()) / "demo.json"
        result = run_script(["--input", str(inp), "--out", str(out)])
        self.assertEqual(result.returncode, 0, result.stderr)
        case = json.loads(out.read_text(encoding="utf-8"))["cases"][0]
        self.assertIsNone(case["caught"])
        self.assertEqual(case["caught_reason"], "nothing_to_catch")
        self.assertEqual(case["new_fail_n"], 0)
        self.assertEqual(case["new_fail_tests"], [])

    def test_budget_frac_comes_from_select(self) -> None:
        repo, sha = init_repo()
        inp = write_input(repo, sha, "model", "model")
        payload = ranking(sha, ranker="model")
        payload["budget"]["value"] = 0.25
        (inp / "rankings" / f"{sha}.json").write_text(json.dumps(payload), encoding="utf-8")
        out = Path(tempfile.mkdtemp()) / "demo.json"
        result = run_script(["--input", str(inp), "--out", str(out)])
        self.assertEqual(result.returncode, 0, result.stderr)
        case = json.loads(out.read_text(encoding="utf-8"))["cases"][0]
        self.assertEqual(case["budget_frac"], 0.25)
        self.assertEqual(case["new_fail_tests"], [])

    def test_local_origin_keeps_public_url_and_stamps_generated_at(self) -> None:
        repo, sha = init_repo()
        git(repo, "remote", "set-url", "origin", "/tmp/local-click.git")
        inp = write_input(repo, sha, "model", "model")
        source = json.loads((inp / "source.json").read_text(encoding="utf-8"))
        source["model_measured_at"] = "2026-11-20T06:59:03Z"
        (inp / "source.json").write_text(json.dumps(source), encoding="utf-8")
        out = Path(tempfile.mkdtemp()) / "demo.json"
        before = datetime.now(timezone.utc) - timedelta(seconds=2)
        result = run_script(["--input", str(inp), "--out", str(out)])
        after = datetime.now(timezone.utc) + timedelta(seconds=2)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(data["source"]["target_repo"], "https://github.com/pallets/click")
        self.assertNotEqual(data["source"]["generated_at"], "2026-11-18T20:57:55Z")
        stamped = datetime.strptime(data["source"]["generated_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        self.assertGreaterEqual(stamped, before)
        self.assertLessEqual(stamped, after)
        self.assertEqual(data["source"]["model_measured_at"], "2026-11-20T06:59:03Z")

    def test_missing_commit_leaves_output(self) -> None:
        repo, sha = init_repo()
        inp = write_input(repo, sha, "model", "model")
        missing = "0123456789abcdef0123456789abcdef01234567"
        payload = ranking(missing, ranker="model")
        (inp / "rankings" / f"{sha}.json").unlink()
        (inp / "rankings" / f"{missing}.json").write_text(json.dumps(payload), encoding="utf-8")
        out = Path(tempfile.mkdtemp()) / "demo.json"
        out.write_text('{"keep":true}\n', encoding="utf-8")
        before = out.read_bytes()
        result = run_script(["--input", str(inp), "--out", str(out)])
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(out.read_bytes(), before)
        self.assertIn("cat-file", result.stderr)


if __name__ == "__main__":
    unittest.main()
