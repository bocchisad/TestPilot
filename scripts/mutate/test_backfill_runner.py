"""Historical runner pins must not suppress all measurements."""
from pathlib import Path
import subprocess

import backfill


def test_old_requirement_runner_is_restored_after_all_installs(tmp_path, monkeypatch):
    (tmp_path/'requirements').mkdir()
    (tmp_path/'requirements/tests.txt').write_text('pytest==6.2.4\npluggy==0.13.1\n')
    calls=[]
    probes=iter([1,0])
    def run(args, **kwargs):
        calls.append(args)
        rc=next(probes) if '-c' in args else 0
        return subprocess.CompletedProcess(args,rc,'','incompatible old pytest' if rc else '')
    monkeypatch.setattr(backfill,'run',run)
    ok, log=backfill.install_project(Path('/venv/bin/pip'),tmp_path)
    assert ok and 'runner override' in log
    req=next(i for i,a in enumerate(calls) if '-r' in a)
    repair=next(i for i,a in enumerate(calls) if 'pytest==8.3.4' in a)
    assert repair>req
    assert 'pluggy>=1.5,<2' in calls[repair]
    assert 'py>=1.11,<2' in calls[repair]


def test_compatible_pinned_runner_is_not_overwritten(tmp_path,monkeypatch):
    calls=[]
    def run(args,**kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args,0,'','')
    monkeypatch.setattr(backfill,'run',run)
    assert backfill.install_project(Path('/venv/bin/pip'),tmp_path)[0]
    assert not any('pytest==8.3.4' in a for a in calls)


def test_rc_one_without_recorded_results_is_not_success(tmp_path,monkeypatch):
    monkeypatch.setattr(backfill,'_has_reusable_real_run',lambda *a: False)
    assert not backfill.backfill_result_ok(1,tmp_path/'db.sqlite','sha')
    monkeypatch.setattr(backfill,'_has_reusable_real_run',lambda *a: True)
    assert backfill.backfill_result_ok(1,tmp_path/'db.sqlite','sha')
    assert not backfill.backfill_result_ok(2,tmp_path/'db.sqlite','sha')


def test_main_rejects_rc_one_before_any_database_result(tmp_path, monkeypatch):
    import sys
    repo=tmp_path/'repo'
    repo.mkdir()
    (repo/'.git').mkdir()
    db=tmp_path/'db.sqlite'
    monkeypatch.setattr(sys,'argv',['backfill','--repo',str(repo),'--db',str(db),'--sha','deadbeef'])
    monkeypatch.setattr(backfill,'_STOP',False)
    monkeypatch.setattr(backfill.signal,'signal',lambda *a: None)
    monkeypatch.setattr(backfill,'install_project',lambda *a:(True,''))
    monkeypatch.setattr(backfill,'_has_reusable_real_run',lambda *a:False)
    monkeypatch.setattr(backfill,'warn_always_failing',lambda *a,**k:[])
    def run(args, **kwargs):
        if '--testpilot' in args:
            return subprocess.CompletedProcess(args,1,'','Traceback before pytest startup')
        return subprocess.CompletedProcess(args,0,'deadbeef\n' if 'rev-parse' in args else '','')
    monkeypatch.setattr(backfill,'run',run)
    assert backfill.main()==1
    stderr=repo/'.testpilot/bootstrap/backfill_logs/deadbeef.stderr.log'
    assert stderr.read_text()=='Traceback before pytest startup'
