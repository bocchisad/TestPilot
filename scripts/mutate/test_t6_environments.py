"""T6: exercise real pytest children; pip setup is replaced with offline venv setup."""
from __future__ import annotations

import subprocess
import sys
import threading
import venv
from pathlib import Path

import pytest

from mutate.test_bootstrap_t3 import _fake_site, _git, _seed_eligible
from mutate.run_mutant import MutantRunResult
import mutate.run_mutant as rm
import mutate_bootstrap as mb


@pytest.fixture(autouse=True)
def reset_stop_flags():
    mb._STOP.clear()
    mb._HARD_STOP.clear()
    yield
    mb._STOP.clear()
    mb._HARD_STOP.clear()


def setup_tasks(tmp_path, monkeypatch, *, count=2):
    repo, db, _ = _seed_eligible(tmp_path)
    (repo / "test_worker.py").write_text("def test_ok():\n    assert True\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "tests")
    sha = _git(repo, "rev-parse", "HEAD")
    monkeypatch.setattr(mb, "sample_commits", lambda *a, **k: type("S", (), {
        "n_eligible_C": 1, "sampled": [type("E", (), {"sha": sha, "duration_sec": 0.1})()]
    })())
    monkeypatch.setattr(mb, "_candidate_mutants", lambda *a, **k: ([
        ("pkg/__init__.py", _fake_site(i), f"VALUE = {i}\n") for i in range(count)
    ], 0))
    monkeypatch.setattr(mb, "collect_git_sha", lambda *a: type("R", (), {"ok": True})())
    monkeypatch.setattr(mb, "synthetic_run_exists", lambda *a: False)
    def offline_setup(cmd, **kwargs):
        if cmd[1:3] == ["-m", "venv"]:
            root = Path(cmd[-1])
            venv.EnvBuilder(system_site_packages=True).create(root)
            site = subprocess.check_output([str(root / "bin/python"), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"], text=True).strip()
            # Runner dependencies live in its venv, not base system site-packages.
            import sysconfig
            Path(site, "runner-deps.pth").write_text(sysconfig.get_path("purelib") + "\n")
            script = root / "bin/pytest"
            script.write_text(f'#!{root}/bin/python\nimport pytest\nraise SystemExit(pytest.console_main())\n')
            script.chmod(0o755)
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(mb, "run", offline_setup)
    return repo, db, sha


def invoke(repo, db, tmp_path, **kw):
    return mb.run_bootstrap(repo, db, commits_n=1, mutants_k=2, seed=42,
        timeout_sec=20, python=Path(sys.executable), plugin=None, pytest_args="-q",
        work_root=tmp_path / "workers", **kw)


def test_installs_serialized_real_pytests_overlap_and_import_own_worktree(tmp_path, monkeypatch):
    repo, db, _ = setup_tasks(tmp_path, monkeypatch)
    marker = tmp_path / "children"
    marker.mkdir()
    (repo / "test_worker.py").write_text(f'''import pathlib, time, pkg

def test_own_project_and_overlap():
    root = pathlib.Path.cwd().resolve()
    assert pathlib.Path(pkg.__file__).resolve() == root / "pkg/__init__.py"
    marker = pathlib.Path({str(marker)!r})
    (marker / str(pkg.VALUE)).write_text(str(root))
    deadline = time.monotonic() + 10
    while len(list(marker.iterdir())) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(list(marker.iterdir())) == 2, "pytest children did not overlap"
''')
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "overlap")
    sha = _git(repo, "rev-parse", "HEAD")
    monkeypatch.setattr(mb, "sample_commits", lambda *a, **k: type("S", (), {
        "n_eligible_C": 1, "sampled": [type("E", (), {"sha": sha, "duration_sec": .1})()]
    })())
    active = 0
    guard = threading.Lock()
    first_entered = threading.Event()
    release_first = threading.Event()
    installations = []
    def install(pip, wt):
        nonlocal active
        with guard:
            active += 1
            assert active == 1, "install_project calls overlapped"
            index = len(installations)
            installations.append(pip)
        if index == 0:
            first_entered.set()
            assert release_first.wait(10)
        py = pip.parent / "python"
        site = subprocess.check_output([str(py), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"], text=True).strip()
        Path(site, "project.pth").write_text(str(wt) + "\n")
        with guard:
            active -= 1
        return True, ""
    monkeypatch.setattr(mb, "install_project", install)
    monkeypatch.setattr(rm, "synthetic_run_exists", lambda *a: False)
    monkeypatch.setattr(rm, "run_test_count", lambda *a, **k: 0)
    monkeypatch.setattr(rm, "parent_fail_set", lambda *a: set())
    monkeypatch.setattr(rm, "run_fail_set", lambda *a, **k: set())
    monkeypatch.setattr(rm, "collect_junit_synthetic", lambda *a, **k: (True, ""))
    details = []
    actual = mb.run_mutant
    def observe(*a, **k):
        r = actual(*a, **k)
        details.append(r)
        return r
    monkeypatch.setattr(mb, "run_mutant", observe)
    result = []
    thread = threading.Thread(target=lambda: result.append(invoke(repo, db, tmp_path, jobs=2)))
    thread.start()
    try:
        assert first_entered.wait(10)
        # First installation deliberately stays active while the other worker is ready.
        release_first.set()
        thread.join(30)
        assert not thread.is_alive()
        assert result and result[0].failure is None, result
        assert result[0].n_equivalent == 2, [(r.log_tail, r.collection_error) for r in details]
        assert len({str(p) for p in installations}) == 2
        assert len(list(marker.iterdir())) == 2
    finally:
        release_first.set()
        thread.join(30)


def test_infrastructure_failure_keeps_successful_pairs_and_retries_failed(tmp_path, monkeypatch):
    repo, db, sha = setup_tasks(tmp_path, monkeypatch)
    calls = []
    def mutant(*a, **kw):
        calls.append(kw["mutant_index"])
        if kw["mutant_index"] == 1:
            return MutantRunResult(ok=False, log_tail="pytest executable missing")
        return MutantRunResult(ok=True, collect_ok=True)
    monkeypatch.setattr(mb, "run_mutant", mutant)
    first = invoke(repo, db, tmp_path, jobs=1)
    assert first.failure and "pytest executable missing" in first.failure
    assert not first.interrupted
    assert first.completed_pairs == [f"{sha}:0"]
    assert first.n_equivalent == 1
    calls.clear()
    monkeypatch.setattr(mb, "run_mutant", lambda *a, **k: calls.append(k["mutant_index"]) or MutantRunResult(ok=True, collect_ok=True))
    second = invoke(repo, db, tmp_path, jobs=1, completed_pairs=set(first.completed_pairs))
    assert calls == [1]
    assert second.failure is None
    assert set(second.completed_pairs) == {f"{sha}:0", f"{sha}:1"}


@pytest.mark.parametrize("failure", ["missing", "install"])
def test_failed_setup_cleans_worktree_without_traceback(tmp_path, monkeypatch, failure):
    repo, db, sha = _seed_eligible(tmp_path)
    monkeypatch.setattr(rm, "synthetic_run_exists", lambda *a: False)
    monkeypatch.setattr(rm, "_pytest_warn_args", lambda *a: [])
    result = rm.run_mutant(repo, db, sha, parent_sha=sha,
        pytest_bin=tmp_path / "missing-pytest", python_bin=Path(sys.executable),
        work_dir=tmp_path / "run", pip_bin=tmp_path / "pip",
        install_project_cb=(lambda *a: (False, "install failed")) if failure=="install" else None)
    assert not result.ok
    assert "install failed" in result.log_tail or "pytest executable missing" in result.log_tail
    assert not (tmp_path / "run" / f"wt-{sha[:12]}").exists()


def test_install_lock_excludes_a_waiting_second_install_and_releases_on_error(monkeypatch):
    attempted_second = threading.Event()
    first_inside = threading.Event()
    mutex = threading.Lock()
    count_guard = threading.Lock()
    enters = 0
    class ObservedLock:
        def __enter__(self):
            nonlocal enters
            with count_guard:
                enters += 1
                if enters == 2:
                    attempted_second.set()
            mutex.acquire()
        def __exit__(self, *exc):
            mutex.release()
    active = 0
    calls = []
    def install(pip, wt):
        nonlocal active
        active += 1
        try:
            assert active == 1, "parallel install_project"
            calls.append(str(wt))
            if str(wt) == "first":
                first_inside.set()
                assert attempted_second.wait(5), "second task never attempted lock"
                raise OSError("injected install error")
            return True, ""
        finally:
            active -= 1
    monkeypatch.setattr(mb, "install_project", install)
    errors = []
    lock = ObservedLock()
    def call(name):
        try:
            mb.install_project_serialized(lock, Path("pip"), Path(name))
        except Exception as exc:
            errors.append(str(exc))
    one = threading.Thread(target=call, args=("first",))
    two = threading.Thread(target=call, args=("second",))
    one.start()
    assert first_inside.wait(5)
    two.start()
    one.join(10)
    two.join(10)
    assert not one.is_alive() and not two.is_alive()
    assert calls == ["first", "second"]
    assert errors == ["injected install error"]


def test_unfinished_runs_not_eligible_but_finished_failures_are(tmp_path):
    import sqlite3
    from mutate.sample_commits import sample_commits
    repo, db, _ = _seed_eligible(tmp_path)
    con = sqlite3.connect(db)
    con.execute("UPDATE test_runs SET finished_at=NULL")
    con.commit()
    assert sample_commits(db, 2, 42, repo=repo).n_eligible_C == 0
    con.execute("UPDATE test_runs SET finished_at=started_at+1")
    con.execute("UPDATE test_results SET outcome='fail'")
    con.commit()
    con.close()
    assert sample_commits(db, 2, 42, repo=repo).n_eligible_C == 1


def test_outcome_ledger_records_timeout_then_completed_retry_without_marking_timeout_done(tmp_path, monkeypatch):
    import json
    repo, db, sha = setup_tasks(tmp_path, monkeypatch)
    pairs = tmp_path / 'mutate_pairs.jsonl'
    def mutant(*a, **kw):
        if kw['mutant_index'] == 1:
            return MutantRunResult(ok=False, timed_out=True)
        return MutantRunResult(ok=True, collect_ok=True)
    monkeypatch.setattr(mb, 'run_mutant', mutant)
    first = invoke(repo, db, tmp_path, jobs=1, pairs_out=pairs)
    assert first.n_timeout == 1
    assert first.completed_pairs == [f'{sha}:0']
    completed = [json.loads(line)['pair'] for line in pairs.read_text().splitlines()]
    assert completed == [f'{sha}:0']
    ledger = pairs.with_name('mutate_outcomes.jsonl')
    assert [json.loads(line)['status'] for line in ledger.read_text().splitlines()] == ['complete', 'timeout']
    monkeypatch.setattr(mb, 'run_mutant', lambda *a, **kw: MutantRunResult(ok=True, collect_ok=True))
    second = invoke(repo, db, tmp_path, jobs=1, pairs_out=pairs, completed_pairs=set(first.completed_pairs))
    assert second.n_timeout == 0 and len(second.completed_pairs) == 2
    assert [json.loads(line)['status'] for line in ledger.read_text().splitlines()] == ['complete', 'timeout', 'complete']
