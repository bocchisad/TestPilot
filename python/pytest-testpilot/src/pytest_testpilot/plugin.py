from __future__ import annotations

import json
import os
import time
import warnings
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from pytest_testpilot.codec import encode_lines
from pytest_testpilot.gitinfo import collect_gitinfo
from pytest_testpilot.store import Store, StoreError

try:
    from coverage import Coverage
except ImportError:  # pragma: no cover
    Coverage = None  # type: ignore[misc, assignment]


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("testpilot")
    group.addoption(
        "--testpilot",
        action="store_true",
        default=False,
        help="Record outcomes and coverage into TestPilot SQLite",
    )
    group.addoption(
        "--testpilot-db",
        action="store",
        default=".testpilot/db.sqlite",
        help="Path to TestPilot SQLite database",
    )
    group.addoption(
        "--testpilot-strict",
        action="store_true",
        default=False,
        help="Fail the session if TestPilot collection errors",
    )


def pytest_configure(config: pytest.Config) -> None:
    if not config.getoption("--testpilot"):
        return
    if getattr(config, "workerinput", None) is not None:
        # xdist worker
        plugin = TestPilotPlugin(config, worker=True)
    else:
        plugin = TestPilotPlugin(config, worker=False)
    config.pluginmanager.register(plugin, "testpilot-plugin")


@dataclass
class Attempt:
    outcome: str
    duration_ms: float
    message: str | None = None


@dataclass
class TestPilotPlugin:
    config: pytest.Config
    worker: bool = False
    store: Store | None = None
    coverage: Any = None
    repo_root: Path = field(default_factory=Path.cwd)
    started_at: float = 0.0
    results: dict[str, list[Attempt]] = field(default_factory=lambda: defaultdict(list))
    setup_start: dict[str, float] = field(default_factory=dict)
    call_durations: dict[str, float] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    worker_payloads: list[dict[str, Any]] = field(default_factory=list)
    own_coverage: bool = False

    def pytest_sessionstart(self, session: pytest.Session) -> None:
        self.started_at = time.time()
        self.repo_root = Path(session.config.rootpath).resolve()
        try:
            if not self.worker:
                self.store = Store(self.config.getoption("--testpilot-db"))
            self._start_coverage()
        except Exception as exc:
            self._handle_error(exc)

    def _start_coverage(self) -> None:
        if Coverage is None:
            raise StoreError("coverage package is required for --testpilot")
        # Cooperate with pytest-cov: reuse active coverage if present.
        # sysmon core (Python 3.12+) does not support dynamic contexts — force ctrace.
        os.environ.setdefault("COVERAGE_CORE", "ctrace")
        existing = Coverage.current()
        if existing is not None:
            self.coverage = existing
            self.own_coverage = False
            return
        self.coverage = Coverage(
            data_file=None,
            source=[str(self.repo_root)],
            config_file=False,
        )
        self.coverage.start()
        self.own_coverage = True

    def pytest_runtest_setup(self, item: pytest.Item) -> None:
        self.setup_start[item.nodeid] = time.perf_counter()
        if self.coverage is not None:
            try:
                self.coverage.switch_context(item.nodeid)
            except Exception as exc:
                warnings.warn(f"testpilot: switch_context failed: {exc}", stacklevel=2)

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(self, item: pytest.Item, call: pytest.CallInfo[Any]):
        outcome = yield
        report = outcome.get_result()
        test_id = self._test_id(item)
        if report.when == "setup":
            # duration includes setup; accumulate later with call
            self.call_durations.setdefault(test_id, 0.0)
            self.call_durations[test_id] += report.duration
        elif report.when == "call":
            self.call_durations.setdefault(test_id, 0.0)
            self.call_durations[test_id] += report.duration
            if report.passed:
                oc = "pass"
            elif report.failed:
                oc = "fail"
            elif report.skipped:
                oc = "skip"
            else:
                oc = "error"
            msg = None
            if report.failed or oc == "error":
                msg = str(report.longrepr)[:4096] if report.longrepr else None
            self.results[test_id].append(
                Attempt(
                    outcome=oc,
                    duration_ms=self.call_durations.get(test_id, 0.0) * 1000.0,
                    message=msg,
                )
            )
        elif report.when == "teardown" and report.skipped and test_id not in self.results:
            # skipped during setup
            pass

        # Skipped at setup
        if report.when == "setup" and report.skipped:
            self.results[test_id].append(
                Attempt(
                    outcome="skip",
                    duration_ms=report.duration * 1000.0,
                    message=None,
                )
            )

    def pytest_testnodedown(self, node: Any, error: Any | None) -> None:
        if self.worker:
            return
        payload = getattr(node, "workeroutput", {}).get("testpilot")
        if payload:
            self.worker_payloads.append(payload)

    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        try:
            if self.worker:
                self._worker_finish()
            else:
                self._controller_finish()
        except Exception as exc:
            self._handle_error(exc)
        finally:
            if self.own_coverage and self.coverage is not None:
                try:
                    self.coverage.stop()
                except Exception as exc:
                    warnings.warn(f"testpilot: coverage.stop failed: {exc}", stacklevel=2)
            if self.store is not None:
                self.store.close()

    def pytest_terminal_summary(self, terminalreporter: Any, exitstatus: int, config: pytest.Config) -> None:
        if self.errors and not config.getoption("--testpilot-strict"):
            for err in self.errors:
                terminalreporter.write_line(f"testpilot warning: {err}", red=True)

    def _worker_finish(self) -> None:
        coverage_rows = self._collect_coverage_rows()
        results = self._collapse_results()
        payload = {
            "results": results,
            "coverage": [
                {
                    "test_id": c["test_id"],
                    "path": c["path"],
                    "lines": list(c["lines"]),  # bytes → list for JSON; re-encode on master
                    "lines_hex": c["lines"].hex(),
                }
                for c in coverage_rows
            ],
        }
        workeroutput = getattr(self.config, "workeroutput", None)
        if workeroutput is not None:
            workeroutput["testpilot"] = payload
        else:
            tmp = self.repo_root / ".testpilot" / "tmp"
            tmp.mkdir(parents=True, exist_ok=True)
            worker_id = getattr(self.config, "workerinput", {}).get("workerid", "gw0")
            path = tmp / f"worker-{worker_id}.jsonl"
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload) + "\n")

    def _controller_finish(self) -> None:
        t0 = time.perf_counter()
        results = self._collapse_results()
        coverage_rows = self._collect_coverage_rows()

        # Merge worker payloads
        for payload in self.worker_payloads:
            for r in payload.get("results", []):
                results.append(r)
            for c in payload.get("coverage", []):
                blob = bytes.fromhex(c["lines_hex"]) if "lines_hex" in c else bytes(c["lines"])
                coverage_rows.append(
                    {"test_id": c["test_id"], "path": c["path"], "lines": blob}
                )

        # Fallback JSONL merge
        tmp = self.repo_root / ".testpilot" / "tmp"
        if tmp.is_dir():
            for path in sorted(tmp.glob("worker-*.jsonl")):
                for line in path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    payload = json.loads(line)
                    results.extend(payload.get("results", []))
                    for c in payload.get("coverage", []):
                        blob = bytes.fromhex(c["lines_hex"]) if "lines_hex" in c else bytes(c["lines"])
                        coverage_rows.append(
                            {"test_id": c["test_id"], "path": c["path"], "lines": blob}
                        )
                path.unlink(missing_ok=True)

        # Re-collapse after merge
        results = self._collapse_result_dicts(results)

        info = collect_gitinfo(self.repo_root)
        if self.store is None:
            self.store = Store(self.config.getoption("--testpilot-db"))
        finished = int(time.time())
        run_id = self.store.insert_run(
            sha=info.sha,
            source="pytest",
            started_at=int(self.started_at),
            finished_at=finished,
            dirty=info.dirty,
            diff_hash=info.diff_hash,
            input_hash=None,
            meta=None,
            results=results,
            coverage=coverage_rows,
        )
        collect_ms = (time.perf_counter() - t0) * 1000
        session_s = time.time() - self.started_at
        print(
            f"\ntestpilot: wrote run_id={run_id} tests={len(results)} "
            f"coverage_rows={len(coverage_rows)} dirty={int(info.dirty)} "
            f"session={session_s:.2f}s collect={collect_ms:.0f}ms"
        )

    def _collect_coverage_rows(self) -> list[dict[str, Any]]:
        if self.coverage is None:
            return []
        if self.own_coverage:
            self.coverage.stop()
            self.own_coverage = False
        data = self.coverage.get_data()
        rows: list[dict[str, Any]] = []
        # contexts_by_lineno: file -> {lineno: [contexts]}
        measured = data.measured_files()
        for abs_path in measured:
            try:
                rel = str(Path(abs_path).resolve().relative_to(self.repo_root))
            except ValueError:
                continue  # outside repo / site-packages
            rel = rel.replace("\\", "/")
            contexts_map = data.contexts_by_lineno(abs_path)
            by_test: dict[str, list[int]] = defaultdict(list)
            for lineno, contexts in contexts_map.items():
                for ctx in contexts:
                    if not ctx:
                        continue
                    # ctx is nodeid from switch_context / dynamic test_function
                    by_test[ctx].append(int(lineno))
            for test_id, lines in by_test.items():
                # Map coverage context (nodeid) to our normalized test_id when possible
                rows.append(
                    {
                        "test_id": self._normalize_nodeid(test_id),
                        "path": rel,
                        "lines": encode_lines(lines),
                    }
                )
        return rows

    def _collapse_results(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for test_id, attempts in self.results.items():
            outcomes = [a.outcome for a in attempts]
            last = attempts[-1]
            out.append(
                {
                    "test_id": test_id,
                    "outcome": last.outcome,
                    "duration_ms": round(last.duration_ms),
                    "message": last.message,
                    "flaky": len(set(outcomes)) > 1,
                    "attempts": len(attempts),
                }
            )
        return out

    def _collapse_result_dicts(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
        order: list[str] = []
        for r in rows:
            tid = r["test_id"]
            if tid not in by_id:
                order.append(tid)
            by_id[tid].append(r)
        out = []
        for tid in order:
            attempts = by_id[tid]
            outcomes = [a["outcome"] for a in attempts]
            last = attempts[-1]
            out.append(
                {
                    "test_id": tid,
                    "outcome": last["outcome"],
                    "duration_ms": last["duration_ms"],
                    "message": last.get("message"),
                    "flaky": len(set(outcomes)) > 1,
                    "attempts": sum(int(a.get("attempts", 1)) for a in attempts),
                }
            )
        return out

    def _test_id(self, item: pytest.Item) -> str:
        path = getattr(item, "path", None) or getattr(item, "fspath", None)
        if path is not None:
            p = Path(str(path)).resolve()
            try:
                rel = p.relative_to(self.repo_root).as_posix()
            except ValueError:
                rel = Path(str(path)).as_posix()
            node = item.nodeid
            # nodeid is like path::rest — replace path portion
            if "::" in node:
                _, rest = node.split("::", 1)
                return f"{rel}::{rest}"
            return rel
        return self._normalize_nodeid(item.nodeid)

    def _normalize_nodeid(self, nodeid: str) -> str:
        if "::" not in nodeid:
            return nodeid
        path_part, rest = nodeid.split("::", 1)
        p = Path(path_part)
        if p.is_absolute():
            try:
                path_part = p.resolve().relative_to(self.repo_root).as_posix()
            except ValueError:
                path_part = p.as_posix()
        else:
            path_part = path_part.replace("\\", "/")
        return f"{path_part}::{rest}"

    def _handle_error(self, exc: BaseException) -> None:
        msg = f"{type(exc).__name__}: {exc}"
        self.errors.append(msg)
        if self.config.getoption("--testpilot-strict"):
            raise RuntimeError(msg) from exc
        warnings.warn(f"testpilot: {msg}", stacklevel=2)
