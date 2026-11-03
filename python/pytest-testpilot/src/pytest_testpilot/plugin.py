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
        help="Make TestPilot errors fail the session (non-zero exit after the summary)",
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
    _await_pytest_cov: bool = False
    _store_open_failed: bool = False
    _nodeid_norm_cache: dict[str, str] = field(default_factory=dict)

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
                try:
                    self.store = Store(self._db_path())
                except Exception as exc:
                    self._store_open_failed = True
                    raise exc
        except Exception as exc:
            self._handle_error(exc)

    def _is_xdist_controller(self) -> bool:
        if self.worker:
            return False
        # controller has workerinput absent but xdist loaded with -n
        numproc = getattr(self.config.option, "numprocesses", None)
        return bool(numproc)

    def _pytest_cov_active(self) -> bool:
        # --no-cov wins over addopts=--cov (common IDE debug pattern). Decide from
        # config.option only — cov_source defaults to [] when --cov was not used.
        if getattr(self.config.option, "no_cov", False):
            return False
        return bool(getattr(self.config.option, "cov_source", None))

    def _sysmon_blocks_contexts(self) -> bool:
        return os.environ.get("COVERAGE_CORE", "").lower() == "sysmon"

    def _skip_coverage_collection(self, reason: str) -> None:
        """Stop collecting/switching contexts; leave any pytest-cov tracer alone."""
        if self.coverage_disabled_reason is None:
            self.coverage_disabled_reason = reason
        self.coverage = None
        self.own_coverage = False
        self._await_pytest_cov = False

    def _coverage_from_pytest_cov(self) -> Any | None:
        """Fall back to pytest-cov's CovController.cov when Coverage.current() is unset."""
        pm = self.config.pluginmanager
        if not pm.hasplugin("_cov"):
            return None
        plugin = pm.get_plugin("_cov")
        controller = getattr(plugin, "cov_controller", None)
        if controller is None:
            return None
        return getattr(controller, "cov", None)

    def _reuse_existing_coverage(self) -> bool:
        """Attach to pytest-cov's Coverage. Never start a second tracer."""
        if Coverage is None or self.coverage_disabled_reason:
            return False
        existing = Coverage.current()
        if existing is None:
            existing = self._coverage_from_pytest_cov()
        if existing is None:
            return False
        self.coverage = existing
        self.own_coverage = False
        self._await_pytest_cov = False
        # R4-2: sysmon cannot record dynamic contexts — detach so we never switch_context.
        if not self._coverage_supports_contexts():
            self._skip_coverage_collection(
                "coverage core=sysmon does not support dynamic contexts; "
                "testpilot coverage collection skipped (pytest-cov report left intact)"
            )
        return True

    def _start_own_ctrace(self) -> None:
        # N7: own Coverage with core=ctrace; restore COVERAGE_CORE afterwards so
        # the rest of the session / child processes keep the caller's value.
        assert Coverage is not None
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

    def _start_coverage(self) -> None:
        if Coverage is None:
            raise StoreError("coverage package is required for --testpilot")

        # R4-1: when pytest-cov is disabled (--no-cov), collect with our own tracer.
        if self._pytest_cov_active():
            # NEW-1: never start a second tracer under pytest-cov.
            # R4-2: under sysmon, skip early — do not attach or switch_context.
            if self._sysmon_blocks_contexts():
                self._skip_coverage_collection(
                    "coverage core=sysmon does not support dynamic contexts; "
                    "testpilot coverage collection skipped (pytest-cov report left intact)"
                )
                return
            if self._reuse_existing_coverage():
                return
            self._await_pytest_cov = True
            return

        self._start_own_ctrace()

    def _ensure_coverage_attached(self) -> None:
        if self.coverage_disabled_reason or self.coverage is not None:
            return
        if self._reuse_existing_coverage():
            return
        if self._await_pytest_cov:
            self._skip_coverage_collection(
                "pytest-cov is enabled but no Coverage instance was available; "
                "testpilot coverage collection skipped"
            )

    def pytest_runtest_setup(self, item: pytest.Item) -> None:
        if not self.coverage_disabled_reason:
            self._ensure_coverage_attached()
            # Prefer the live pytest-cov instance if it started after sessionstart.
            if self._pytest_cov_active():
                self._reuse_existing_coverage()
        tid = self._test_id(item)
        # B9: reset duration at setup so only last attempt's setup+call count
        self.phase_duration[tid] = 0.0
        # R4-2: never switch_context when collection was skipped (avoids sysmon warnings).
        if self.coverage is not None and not self.coverage_disabled_reason:
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

    @pytest.hookimpl(wrapper=True, trylast=True)
    def pytest_runtestloop(self, session: pytest.Session):
        # Attach to pytest-cov's Coverage before tests if it started after sessionstart.
        self._ensure_coverage_attached()
        # B5 / NEW-2: trylast wrapper so our after-section runs BEFORE pytest-cov's
        # (default-order) finish(), which stop()+save()s the worker data.
        try:
            result = yield
        except BaseException:
            self.session_failed_hard = True
            raise
        try:
            if not self.coverage_disabled_reason:
                self._ensure_coverage_attached()
                # Re-bind in case pytest-cov replaced the instance after sessionstart.
                if self._pytest_cov_active():
                    self._reuse_existing_coverage()
            if self.worker or not self._is_xdist_controller():
                self._coverage_snapshot = self._collect_coverage_rows()
        except Exception as exc:
            self._handle_error(exc)
            self._coverage_snapshot = []
        return result

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
            # R4-4: controller surfaces a single skip warning from workers.
            "coverage_disabled_reason": self.coverage_disabled_reason,
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
            reason = payload.get("coverage_disabled_reason")
            if reason and self.coverage_disabled_reason is None:
                self.coverage_disabled_reason = reason
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
                    reason = payload.get("coverage_disabled_reason")
                    if reason and self.coverage_disabled_reason is None:
                        self.coverage_disabled_reason = reason
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
        if self._store_open_failed:
            return

        info = collect_gitinfo(self.repo_root)
        if self.store is None:
            try:
                self.store = Store(self._db_path())
            except Exception as exc:
                self._store_open_failed = True
                self._handle_error(exc)
                return
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

    def _coverage_supports_contexts(self) -> bool:
        """sysmon cannot record dynamic contexts (coverage warning no-sysmon-context)."""
        cov = self.coverage
        if cov is None:
            return False
        core = getattr(cov, "_core", None)
        name = type(core).__name__.lower() if core is not None else ""
        if "sysmon" in name or "sysmonitor" in name:
            return False
        # Env may force sysmon even when _core attribute shape differs.
        return not (os.environ.get("COVERAGE_CORE", "").lower() == "sysmon" and not self.own_coverage)

    def _collect_coverage_rows(self) -> list[dict[str, Any]]:
        if self.coverage is None:
            return []
        if not self._coverage_supports_contexts():
            if self.coverage_disabled_reason is None:
                self.coverage_disabled_reason = (
                    "coverage core=sysmon does not support dynamic contexts; "
                    "testpilot coverage collection skipped (pytest-cov report left intact)"
                )
            return []
        if self.own_coverage:
            try:
                self.coverage.stop()
            except Exception as exc:
                self.errors.append(f"coverage.stop: {exc}")
            self.own_coverage = False
        try:
            # Flush tracer into CoverageData while still running (reuse path).
            with contextlib.suppress(Exception):
                collector = getattr(self.coverage, "_collector", None) or getattr(
                    self.coverage, "collector", None
                )
                if collector is not None and hasattr(collector, "flush_data"):
                    collector.flush_data()
            data = self.coverage.get_data()
        except Exception as exc:
            self.errors.append(f"coverage.get_data: {exc}")
            return []
        rows: list[dict[str, Any]] = []
        saw_lines = False
        saw_contexts = False
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
                saw_lines = True
                for ctx in contexts:
                    if not ctx:
                        continue
                    saw_contexts = True
                    # pytest-cov --cov-context=test uses "nodeid|when" — keep nodeid only.
                    ctx_id = ctx.split("|", 1)[0] if "|" in ctx else ctx
                    by_test[ctx_id].append(int(lineno))
            # NEW-3: session-level cache (not per measured file)
            for test_id, lines in by_test.items():
                tid = self._nodeid_norm_cache.get(test_id)
                if tid is None:
                    tid = self._normalize_nodeid(test_id)
                    self._nodeid_norm_cache[test_id] = tid
                rows.append(
                    {
                        "test_id": tid,
                        "path": rel,
                        "lines": encode_lines(lines),
                    }
                )
        if (
            not rows
            and saw_lines
            and not saw_contexts
            and not self.own_coverage
            and self.coverage_disabled_reason is None
        ):
            self.coverage_disabled_reason = (
                "pytest-cov Coverage has no dynamic context data; "
                "testpilot coverage collection skipped"
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
        if msg not in self.errors:
            self.errors.append(msg)
        # Schema mismatch / plugin errors: warn by default; fail only in --testpilot-strict.
        if self.config.getoption("--testpilot-strict"):
            self.force_fail_exit = True
            # Defer non-zero exit to sessionfinish so terminal summary still prints.
