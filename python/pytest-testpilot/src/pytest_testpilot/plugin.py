from __future__ import annotations

import contextlib
import json
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from pytest_testpilot.codec import encode_lines
from pytest_testpilot.gitinfo import collect_gitinfo, git_toplevel
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
        default=None,
        metavar="PATH",
        help="SQLite path (use --testpilot-db=PATH). Default: <git-toplevel>/.testpilot/db.sqlite",
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
    worker = getattr(config, "workerinput", None) is not None
    plugin = TestPilotPlugin(config, worker=worker)
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
    repo_root_str: str = ""
    started_at: float = 0.0
    results: dict[str, list[Attempt]] = field(default_factory=lambda: defaultdict(list))
    phase_duration: dict[str, float] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    worker_payloads: list[dict[str, Any]] = field(default_factory=list)
    own_coverage: bool = False
    coverage_disabled_reason: str | None = None
    wrote_run: bool = False
    session_failed_hard: bool = False
    _last_cov_ctx: str | None = None
    force_fail_exit: bool = False

    def _db_path(self) -> Path:
        explicit = self.config.getoption("--testpilot-db")
        if explicit:
            return Path(explicit)
        return self.repo_root / ".testpilot" / "db.sqlite"

    def pytest_sessionstart(self, session: pytest.Session) -> None:
        self.started_at = time.time()
        try:
            self.repo_root = git_toplevel(Path(session.config.rootpath))
        except Exception:
            self.repo_root = Path(session.config.rootpath).resolve()
        self.repo_root_str = str(self.repo_root)
        try:
            # B6: xdist controller must NOT create Coverage
            if self.worker or not self._is_xdist_controller():
                self._start_coverage()
            if not self.worker:
                self.store = Store(self._db_path())
        except Exception as exc:
            self._handle_error(exc)

    def _is_xdist_controller(self) -> bool:
        if self.worker:
            return False
        # controller has workerinput absent but xdist loaded with -n
        numproc = getattr(self.config.option, "numprocesses", None)
        return bool(numproc)

    def _start_coverage(self) -> None:
        if Coverage is None:
            raise StoreError("coverage package is required for --testpilot")
        env_core = os.environ.get("COVERAGE_CORE", "").lower()

        # Reuse an already-running Coverage (e.g. pytest-cov) unless the session
        # asked for sysmon, which cannot do dynamic contexts — then we own ctrace.
        existing = Coverage.current()
        if existing is not None and env_core != "sysmon":
            self.coverage = existing
            self.own_coverage = False
            with contextlib.suppress(Exception):
                self.coverage.set_option("run:core", "ctrace")
            return

        # N7: create our own Coverage with core=ctrace without permanently mutating
        # COVERAGE_CORE for the rest of the session / child processes.
        self.coverage = Coverage(
            data_file=None,
            source=[self.repo_root_str or str(self.repo_root)],
            config_file=False,
        )
        with contextlib.suppress(Exception):
            self.coverage.set_option("run:core", "ctrace")
            self.coverage.set_option("run:dynamic_context", None)
            self.coverage._warn_no_data = False  # type: ignore[attr-defined]
        with contextlib.suppress(Exception):
            self.coverage.disable_warnings("no-data-collected")

        prev = os.environ.get("COVERAGE_CORE")
        os.environ["COVERAGE_CORE"] = "ctrace"
        try:
            self.coverage.start()
        finally:
            if prev is None:
                os.environ.pop("COVERAGE_CORE", None)
            else:
                os.environ["COVERAGE_CORE"] = prev
        self.own_coverage = True

    def pytest_runtest_setup(self, item: pytest.Item) -> None:
        tid = self._test_id(item)
        # B9: reset duration at setup so only last attempt's setup+call count
        self.phase_duration[tid] = 0.0
        if self.coverage is not None:
            ctx = item.nodeid
            # Avoid redundant switch_context when context is unchanged (perf).
            if ctx != self._last_cov_ctx:
                try:
                    self.coverage.switch_context(ctx)
                    self._last_cov_ctx = ctx
                except Exception as exc:
                    self.errors.append(f"switch_context: {exc}")

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(self, item: pytest.Item, call: pytest.CallInfo[Any]):
        outcome = yield
        report = outcome.get_result()
        test_id = self._test_id(item)

        if report.when in ("setup", "call"):
            self.phase_duration[test_id] = self.phase_duration.get(test_id, 0.0) + report.duration

        if report.when == "setup" and report.failed:
            # B8: setup failure → error
            self.results[test_id].append(
                Attempt(
                    outcome="error",
                    duration_ms=self.phase_duration.get(test_id, 0.0) * 1000.0,
                    message=str(report.longrepr)[:4096] if report.longrepr else None,
                )
            )
        elif report.when == "setup" and report.skipped:
            self.results[test_id].append(
                Attempt(
                    outcome="skip",
                    duration_ms=report.duration * 1000.0,
                    message=None,
                )
            )
        elif report.when == "call":
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
                    duration_ms=self.phase_duration.get(test_id, 0.0) * 1000.0,
                    message=msg,
                )
            )
        elif report.when == "teardown" and report.failed:
            # B8: teardown failure overwrites to error
            attempts = self.results.get(test_id)
            if attempts:
                last = attempts[-1]
                attempts[-1] = Attempt(
                    outcome="error",
                    duration_ms=last.duration_ms,
                    message=str(report.longrepr)[:4096] if report.longrepr else last.message,
                )
            else:
                self.results[test_id].append(
                    Attempt(
                        outcome="error",
                        duration_ms=0.0,
                        message=str(report.longrepr)[:4096] if report.longrepr else None,
                    )
                )

    @pytest.hookimpl(hookwrapper=True, trylast=True)
    def pytest_runtestloop(self, session: pytest.Session):
        # B5: read coverage after the loop (after pytest-cov combines), before sessionfinish.
        outcome = yield
        try:
            if outcome.excinfo is not None:
                self.session_failed_hard = True
            if self.worker or not self._is_xdist_controller():
                self._coverage_snapshot = self._collect_coverage_rows()
        except Exception as exc:
            self._handle_error(exc)
            self._coverage_snapshot = []

    @pytest.hookimpl(optionalhook=True)
    def pytest_testnodedown(self, node: Any, error: Any | None) -> None:
        if self.worker:
            return
        payload = getattr(getattr(node, "workeroutput", None), "get", None)
        if callable(payload):
            data = node.workeroutput.get("testpilot")
        else:
            data = (getattr(node, "workeroutput", {}) or {}).get("testpilot")
        if data:
            self.worker_payloads.append(data)

    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        try:
            # Do not write empty run on INTERNALERROR
            if self.session_failed_hard and not self.results and not self.worker_payloads:
                return
            if self.worker:
                self._worker_finish()
            else:
                self._controller_finish(exitstatus)
        except Exception as exc:
            self._handle_error(exc)
        finally:
            if self.own_coverage and self.coverage is not None:
                try:
                    self.coverage.stop()
                except Exception as exc:
                    self.errors.append(f"coverage.stop: {exc}")
            if self.store is not None:
                self.store.close()
                self.store = None
            # N1: stale schema / plugin errors — non-zero exit in strict mode
            if (self.force_fail_exit or (
                self.errors and self.config.getoption("--testpilot-strict")
            )) and exitstatus == 0:
                session.exitstatus = 1

    def pytest_terminal_summary(
        self, terminalreporter: Any, exitstatus: int, config: pytest.Config
    ) -> None:
        for err in self.errors:
            terminalreporter.write_line(f"testpilot: {err}", red=True)
        if self.coverage_disabled_reason:
            terminalreporter.write_line(f"testpilot: {self.coverage_disabled_reason}", yellow=True)

    def _worker_finish(self) -> None:
        coverage_rows = getattr(self, "_coverage_snapshot", None) or self._collect_coverage_rows()
        results = self._collapse_results()
        payload = {
            "results": results,
            "coverage": [
                {
                    "test_id": c["test_id"],
                    "path": c["path"],
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

    def _controller_finish(self, exitstatus: int) -> None:
        t0 = time.perf_counter()
        results = self._collapse_results()
        coverage_rows = list(getattr(self, "_coverage_snapshot", None) or [])

        for payload in self.worker_payloads:
            results.extend(payload.get("results", []))
            for c in payload.get("coverage", []):
                blob = bytes.fromhex(c["lines_hex"])
                coverage_rows.append(
                    {"test_id": c["test_id"], "path": c["path"], "lines": blob}
                )

        tmp = self.repo_root / ".testpilot" / "tmp"
        if tmp.is_dir():
            for path in sorted(tmp.glob("worker-*.jsonl")):
                for line in path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    payload = json.loads(line)
                    results.extend(payload.get("results", []))
                    for c in payload.get("coverage", []):
                        coverage_rows.append(
                            {
                                "test_id": c["test_id"],
                                "path": c["path"],
                                "lines": bytes.fromhex(c["lines_hex"]),
                            }
                        )
                path.unlink(missing_ok=True)

        results = self._collapse_result_dicts(results)
        if not results and exitstatus == 2:
            # INTERNALERROR with nothing collected
            return

        info = collect_gitinfo(self.repo_root)
        if self.store is None:
            self.store = Store(self._db_path())
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
        self.wrote_run = True
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
            try:
                self.coverage.stop()
            except Exception as exc:
                self.errors.append(f"coverage.stop: {exc}")
            self.own_coverage = False
        try:
            data = self.coverage.get_data()
        except Exception as exc:
            self.errors.append(f"coverage.get_data: {exc}")
            return []
        rows: list[dict[str, Any]] = []
        for abs_path in data.measured_files():
            # Skip zip members
            if ".zip/" in abs_path.replace("\\", "/") or abs_path.lower().endswith(".zip"):
                continue
            try:
                rel = str(Path(abs_path).resolve().relative_to(self.repo_root))
            except ValueError:
                continue
            rel = rel.replace("\\", "/")
            try:
                contexts_map = data.contexts_by_lineno(abs_path)
            except Exception:
                continue
            by_test: dict[str, list[int]] = defaultdict(list)
            for lineno, contexts in contexts_map.items():
                for ctx in contexts:
                    if not ctx:
                        continue
                    by_test[ctx].append(int(lineno))
            norm_cache: dict[str, str] = {}
            for test_id, lines in by_test.items():
                tid = norm_cache.get(test_id)
                if tid is None:
                    tid = self._normalize_nodeid(test_id)
                    norm_cache[test_id] = tid
                rows.append(
                    {
                        "test_id": tid,
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
                    # B9: flaky from raw attempt outcomes
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
            # Preserve flaky from already-collapsed rows (raw attempt variance),
            # and also detect variance across worker/controller fragments.
            flaky = any(bool(a.get("flaky")) for a in attempts) or len(set(outcomes)) > 1
            out.append(
                {
                    "test_id": tid,
                    "outcome": last["outcome"],
                    "duration_ms": last["duration_ms"],
                    "message": last.get("message"),
                    "flaky": flaky,
                    "attempts": sum(int(a.get("attempts", 1)) for a in attempts),
                }
            )
        return out

    def _test_id(self, item: pytest.Item) -> str:
        # Cache on the item — called from setup + every makereport phase (perf).
        cached = getattr(item, "_testpilot_tid", None)
        if cached is not None:
            return cached
        path = getattr(item, "path", None) or getattr(item, "fspath", None)
        if path is not None:
            p = Path(str(path))
            if not p.is_absolute():
                p = (Path(self.config.rootpath) / p).resolve()
            try:
                rel = p.relative_to(self.repo_root).as_posix()
            except ValueError:
                try:
                    rel = p.resolve().relative_to(self.repo_root).as_posix()
                except ValueError:
                    rel = p.as_posix()
            node = item.nodeid
            tid = f"{rel}::{node.split('::', 1)[1]}" if "::" in node else rel
        else:
            tid = self._normalize_nodeid(item.nodeid)
        item._testpilot_tid = tid  # type: ignore[attr-defined]
        return tid

    def _normalize_nodeid(self, nodeid: str) -> str:
        if "::" not in nodeid:
            return nodeid
        path_part, rest = nodeid.split("::", 1)
        p = Path(path_part)
        if p.is_absolute():
            try:
                path_part = p.relative_to(self.repo_root).as_posix()
            except ValueError:
                try:
                    path_part = p.resolve().relative_to(self.repo_root).as_posix()
                except ValueError:
                    path_part = p.as_posix()
        else:
            path_part = path_part.replace("\\", "/")
            # relative to rootpath — rebase onto git toplevel
            try:
                abs_p = (Path(self.config.rootpath) / path_part).resolve()
                path_part = abs_p.relative_to(self.repo_root).as_posix()
            except Exception:
                pass
        return f"{path_part}::{rest}"

    def _handle_error(self, exc: BaseException) -> None:
        msg = str(exc) if isinstance(exc, StoreError) else f"{type(exc).__name__}: {exc}"
        self.errors.append(msg)
        if "schema outdated" in msg or "delete .testpilot" in msg:
            self.force_fail_exit = True
        if self.config.getoption("--testpilot-strict"):
            self.force_fail_exit = True
            # Defer non-zero exit to sessionfinish so terminal summary still prints.
