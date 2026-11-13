from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from pytest_testpilot.codec import encode_lines
from pytest_testpilot.gitinfo import collect_gitinfo, git_toplevel
from pytest_testpilot.select import (
    SELECT_TIMEOUT_DEFAULT,
    SelectPlan,
    apply_order,
    bare_test_id,
    build_select_cmd,
    compute_shadow_meta,
    format_select_summary_line,
    invoke_select_json,
    plan_selection,
    resolve_base,
    resolve_testpilot_bin,
    run_auto_index,
    selection_rows_for_db,
    validate_budget_options,
)
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
    group.addoption(
        "--testpilot-select",
        action="store_true",
        default=False,
        help="Run testpilot select before tests (fail-open on errors)",
    )
    group.addoption(
        "--testpilot-budget-frac",
        action="store",
        default=None,
        type=float,
        metavar="F",
        help="Select budget as fraction of candidates (requires --testpilot-select)",
    )
    group.addoption(
        "--testpilot-max-tests",
        action="store",
        default=None,
        type=int,
        metavar="N",
        help="Select budget: maximum number of tests",
    )
    group.addoption(
        "--testpilot-time-budget",
        action="store",
        default=None,
        type=float,
        metavar="S",
        help="Select time budget in seconds (ranked picks only)",
    )
    group.addoption(
        "--testpilot-base",
        action="store",
        default=None,
        metavar="REV",
        help="Base revision for select (default: merge-base or HEAD~1)",
    )
    group.addoption(
        "--testpilot-model",
        action="store",
        default=None,
        metavar="DIR",
        help="LightGBM model directory for select",
    )
    group.addoption(
        "--testpilot-no-safe",
        action="store_true",
        default=False,
        help="Pass --no-safe to testpilot select",
    )
    group.addoption(
        "--testpilot-order",
        action="store_true",
        default=False,
        help="Reorder kept tests by select rank",
    )
    group.addoption(
        "--testpilot-shadow",
        action="store_true",
        default=False,
        help="Run all tests but record would-be selection",
    )
    group.addoption(
        "--testpilot-auto-index",
        action="store_true",
        default=False,
        help="Before select: run collect git + features dump (default off)",
    )
    group.addoption(
        "--testpilot-select-timeout",
        action="store",
        default=SELECT_TIMEOUT_DEFAULT,
        type=float,
        metavar="SECONDS",
        help="Timeout for testpilot select subprocess (default 10)",
    )


def pytest_configure(config: pytest.Config) -> None:
    if config.getoption("--testpilot"):
        validate_budget_options(config)
    elif config.getoption("--testpilot-select") or config.getoption("--testpilot-shadow"):
        raise pytest.UsageError("--testpilot-select/--testpilot-shadow require --testpilot")
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


@dataclass(eq=False)
class TestPilotPlugin:
    """eq=False keeps identity hashing — pytest fixture registry requires hashable plugins."""

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
    select_plan: SelectPlan | None = None
    _all_collected_items: list[pytest.Item] = field(default_factory=list)

    def _db_path(self) -> Path:
        explicit = self.config.getoption("--testpilot-db")
        if explicit:
            return Path(explicit)
        return self.repo_root / ".testpilot" / "db.sqlite"

    def pytest_collection_modifyitems(
        self, session: pytest.Session, config: pytest.Config, items: list[pytest.Item]
    ) -> None:
        if self.worker or not (
            config.getoption("--testpilot-select") or config.getoption("--testpilot-shadow")
        ):
            return
        self._all_collected_items = list(items)
        self.select_plan = self._run_select_modifyitems(session, items)

    def _run_select_modifyitems(
        self, session: pytest.Session, items: list[pytest.Item]
    ) -> SelectPlan:
        if not self.repo_root_str:
            self.repo_root = self._resolve_repo_root(session)
            self.repo_root_str = str(self.repo_root)
        shadow = self.config.getoption("--testpilot-shadow")
        order = self.config.getoption("--testpilot-order")
        plan = SelectPlan(shadow=shadow, order=order)

        bin_path = resolve_testpilot_bin()
        if bin_path is None:
            plan.plugin_error = "no_bin"
            plan.kept = list(items)
            self.errors.append("testpilot select: no testpilot binary (fail-open)")
            return plan

        model_opt = self.config.getoption("--testpilot-model")
        if not model_opt:
            plan.plugin_error = "model"
            plan.kept = list(items)
            return plan
        model = Path(model_opt)
        if not model.is_dir():
            plan.plugin_error = "model"
            plan.kept = list(items)
            return plan

        try:
            base = resolve_base(self.repo_root, self.config.getoption("--testpilot-base"))
        except Exception:
            plan.plugin_error = "exit"
            plan.kept = list(items)
            return plan
        plan.base = base

        db_path = self._db_path()
        if self.config.getoption("--testpilot-auto-index"):
            err = run_auto_index(bin_path, self.repo_root, db_path)
            if err:
                plan.plugin_error = err
                plan.kept = list(items)
                return plan

        cmd = build_select_cmd(
            bin_path,
            db_path=db_path,
            base_sha=base.sha,
            model=model,
            budget_frac=self.config.getoption("--testpilot-budget-frac"),
            max_tests=self.config.getoption("--testpilot-max-tests"),
            time_budget=self.config.getoption("--testpilot-time-budget"),
            no_safe=self.config.getoption("--testpilot-no-safe"),
            worktree=True,
        )
        timeout = float(self.config.getoption("--testpilot-select-timeout"))
        payload, perr = invoke_select_json(cmd, self.repo_root, timeout)
        if perr or payload is None:
            plan.plugin_error = perr or "json"
            plan.kept = list(items)
            self.errors.append(f"testpilot select: fail-open ({plan.plugin_error})")
            return plan

        plan = plan_selection(
            items,
            payload,
            shadow=shadow,
            order=order,
            test_id_fn=self._test_id,
        )
        plan.base = base
        apply_order(plan, self._test_id)

        if shadow:
            pass
        elif plan.deselected:
            session.config.hook.pytest_deselected(items=plan.deselected)
            items[:] = list(plan.kept)
        elif order and not plan.fallback_all:
            items[:] = list(plan.kept)

        return plan

    def _resolve_repo_root(self, session: pytest.Session) -> Path:
        """Prefer cwd's git toplevel so nested pytest rootdir discovery cannot
        steal a parent monorepo (e.g. temp fixture under TestPilot checkout)."""
        for start in (Path.cwd(), Path(session.config.rootpath)):
            try:
                return git_toplevel(start)
            except Exception:
                continue
        return Path(session.config.rootpath).resolve()

    def pytest_sessionstart(self, session: pytest.Session) -> None:
        self.started_at = time.time()
        self.repo_root = self._resolve_repo_root(session)
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
            # R4-6: whether contexts work is decided after attach via
            # cov._core.supports_dynamic_contexts (not COVERAGE_CORE env alone).
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
        # xdist controller never collects coverage — do not attach to DistMaster's
        # Coverage here or the controller would invent coverage_disabled_reason
        # without workeroutput (R4-7).
        collect_here = self.worker or not self._is_xdist_controller()
        if collect_here and not self.coverage_disabled_reason:
            self._ensure_coverage_attached()
        # B5 / NEW-2: trylast wrapper so our after-section runs BEFORE pytest-cov's
        # (default-order) finish(), which stop()+save()s the worker data.
        try:
            result = yield
        except BaseException:
            self.session_failed_hard = True
            raise
        try:
            if collect_here:
                if not self.coverage_disabled_reason:
                    self._ensure_coverage_attached()
                    # Re-bind in case pytest-cov replaced the instance after sessionstart.
                    if self._pytest_cov_active():
                        self._reuse_existing_coverage()
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
            if not err.startswith("testpilot select:"):
                terminalreporter.write_line(f"testpilot: {err}", red=True)
        if self.coverage_disabled_reason:
            terminalreporter.write_line(f"testpilot: {self.coverage_disabled_reason}", yellow=True)
        plan = self.select_plan
        if plan is not None:
            line = format_select_summary_line(plan)
            if plan.plugin_error:
                terminalreporter.write_line(line, yellow=True)
            else:
                terminalreporter.write_line(line)
            if plan.shadow:
                sh = plan.summary
                ratio = sh.get("shadow_caught_fail")
                ratio_s = (
                    f"{ratio:.4f}" if isinstance(ratio, (int, float)) else str(ratio)
                )
                terminalreporter.write_line(
                    f"testpilot shadow: would_select={sh.get('n_selected_ids')} "
                    f"collected={sh.get('n_collected_items')} "
                    f"shadow_caught_fail={ratio_s}",
                )

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

        reasons_from_workers: list[str] = []
        for payload in self.worker_payloads:
            results.extend(payload.get("results", []))
            reason = payload.get("coverage_disabled_reason")
            if reason:
                reasons_from_workers.append(reason)
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
                    if reason:
                        reasons_from_workers.append(reason)
                    for c in payload.get("coverage", []):
                        coverage_rows.append(
                            {
                                "test_id": c["test_id"],
                                "path": c["path"],
                                "lines": bytes.fromhex(c["lines_hex"]),
                            }
                        )
                path.unlink(missing_ok=True)

        # R4-5: idle workers may report a skip reason; only warn if nobody wrote rows.
        # Clearing self.coverage_disabled_reason when rows exist is intentional: the
        # xdist controller does not collect coverage itself, so any reason already on
        # self would be stale/wrong next to real worker rows (prefer the data).
        if coverage_rows:
            self.coverage_disabled_reason = None
        elif self.coverage_disabled_reason is None and reasons_from_workers:
            self.coverage_disabled_reason = reasons_from_workers[0]

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
        meta_obj: dict[str, Any] | None = None
        partial = False
        plan = self.select_plan
        if plan is not None:
            meta_obj = {"select": plan.summary}
            if plan.base:
                meta_obj["base"] = {"sha": plan.base.sha, "label": plan.base.label}
            if plan.plugin_error:
                meta_obj["plugin_error"] = plan.plugin_error
            if plan.shadow and plan.payload:
                would = {
                    bare_test_id(r["test_id"])
                    for r in (plan.payload.get("tests") or [])
                    if isinstance(r.get("test_id"), str)
                }
                for r in plan.payload.get("tests") or []:
                    tid = r.get("test_id")
                    if isinstance(tid, str):
                        would.add(tid)
                # fallback_all means the suite would have run every candidate;
                # for shadow metrics treat kept/collected as would-select.
                if plan.fallback_all:
                    for item in self._all_collected_items:
                        tid = self._test_id(item)
                        would.add(tid)
                        would.add(bare_test_id(tid))
                shadow_meta = compute_shadow_meta(
                    self._db_path(),
                    plan.base.sha if plan.base else info.sha,
                    results,
                    would,
                )
                meta_obj.update(shadow_meta)
                plan.summary.update(shadow_meta)
            if (
                not plan.shadow
                and not plan.fallback_all
                and not plan.plugin_error
                and plan.ok
            ):
                partial = True
        meta_json = json.dumps(meta_obj) if meta_obj else None

        run_id = self.store.insert_run(
            sha=info.sha,
            source="pytest",
            started_at=int(self.started_at),
            finished_at=finished,
            dirty=info.dirty,
            diff_hash=info.diff_hash,
            input_hash=None,
            meta=meta_json,
            results=results,
            coverage=coverage_rows,
            partial=partial,
        )
        if plan is not None and not plan.plugin_error and self._all_collected_items:
            rows = selection_rows_for_db(plan, self._all_collected_items)
            if rows:
                first = rows[0]
                self.store.insert_selection(
                    run_id,
                    rows,
                    budget_kind=first.get("budget_kind"),
                    budget_value=first.get("budget_value"),
                    model_sha256=first.get("model_sha256"),
                    safe_enabled=bool(first.get("safe_enabled")),
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
        """Whether the attached Coverage can record dynamic contexts.

        R4-6: prefer ``cov._core.supports_dynamic_contexts`` (sysmon sets this
        False). ``type(cov._core).__name__`` is always ``Core`` in coverage 7.x
        and must not be used. Fall back to COVERAGE_CORE env when the attribute
        is missing (older coverage).
        """
        cov = self.coverage
        if cov is None:
            return False
        core = getattr(cov, "_core", None)
        if core is not None and hasattr(core, "supports_dynamic_contexts"):
            return bool(core.supports_dynamic_contexts)
        return not (
            os.environ.get("COVERAGE_CORE", "").lower() == "sysmon"
            and not self.own_coverage
            and hasattr(sys, "monitoring")
        )

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
