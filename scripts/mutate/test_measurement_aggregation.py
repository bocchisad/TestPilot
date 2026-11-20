"""Small adversarial fixtures for the full-measurement harness."""
import json
from pathlib import Path
import sqlite3

import pytest

from aggregate_bootstrap_w6 import collect_mutants, compare_level_b, paired_ci, test_nc as counts, run_loro
from mutate.run_mutant import MutantRunResult
from mutate_bootstrap import _append_outcome

ROOT = Path(__file__).resolve().parents[2]


def test_synthetic_ci_is_not_replaced_by_unrelated_metrics_and_zeros_survive():
    assert paired_ci({'paired_delta_ci_test': {'mean': .5, 'ci95': [.2,.8]}}) == (None,None,None)
    assert counts({'paired_delta_ci_synthetic_test_cov_lines': {'n_C':0,'n_mutants':0}}) == (0,0)
    assert paired_ci({'paired_delta_ci_synthetic_test_cov_lines': {'mean':0,'ci95':[0,0]}}) == (0,0,0)


def seed(repo):
    boot = repo/'.testpilot/bootstrap'
    boot.mkdir(parents=True)
    con = sqlite3.connect(repo/'.testpilot/db.sqlite')
    con.executescript((ROOT/'crates/testpilot-core/schema.sql').read_text())
    for i, test in enumerate(('persistent','new','flaky'), 1):
        con.execute('INSERT INTO tests(id,test_id) VALUES(?,?)',(i,test))
    for run, sha, synthetic in ((1,'C',0),(2,'M',1)):
        con.execute("INSERT INTO test_runs(id,sha,source,started_at,finished_at,synthetic) VALUES(?,?,'pytest',0,1,?)",(run,sha,synthetic))
    for run, test, flaky in ((1,1,0),(2,1,0),(2,3,1)):
        con.execute("INSERT INTO test_results(run_id,test_pk,outcome,duration_ms,flaky) VALUES(?,?,'fail',1,?)",(run,test,flaky))
    con.commit()
    return con, boot


def test_labels_subtract_parent_and_flaky_and_timeouts_are_explicit(tmp_path):
    con, boot = seed(tmp_path)
    pairs = boot/'mutate_pairs.jsonl'
    _append_outcome(pairs, 'C:0', 'M', MutantRunResult(ok=True,collect_ok=True))
    _append_outcome(pairs, 'C:1', 'T', MutantRunResult(ok=False,timed_out=True))
    assert not pairs.exists(), 'timeout ledger must not mark a pair completed'
    result = collect_mutants(tmp_path)
    assert result['labels'] == {'M':'survive'}
    assert result['timeout_pairs'] == ['C:1']
    con.execute("INSERT INTO test_results(run_id,test_pk,outcome,duration_ms) VALUES(2,2,'fail',1)")
    con.commit()
    assert collect_mutants(tmp_path)['labels'] == {'M':'kill'}
    con.close()


def test_unknown_mutation_outcome_is_not_labeled_as_a_completed_mutant(tmp_path):
    _con, boot = seed(tmp_path)
    (boot/'mutate_outcomes.jsonl').write_text(
        json.dumps({'pair':'C:0','m_sha':'M','status':'unexpected'})+'\n'
    )
    with pytest.raises(ValueError, match='unknown mutation outcome'):
        collect_mutants(tmp_path)


def test_level_b_rejects_missing_label_evidence_for_non_error_mutants():
    row = {'m_shas_non_timeout':['M'], 'timeout_shas':[], 'label_details':{}}
    with pytest.raises(ValueError, match='missing label details'):
        compare_level_b(row, row)


def test_level_b_only_ignores_explicitly_matching_collection_errors():
    row = {'m_shas_non_timeout':['M'], 'timeout_shas':[], 'label_details':{},
           'collection_error_shas':['M']}
    result = compare_level_b(row, row)
    assert result['label_match'] and result['n_compared'] == 0
    assert result['n_collection_error_compared'] == 1
    mismatched = {**row, 'collection_error_shas':[]}
    result = compare_level_b(row, mismatched)
    assert not result['label_match']


def test_level_b_excludes_union_of_timeouts_and_flaky_tests():
    a = {'m_shas_non_timeout':['A','B'], 'timeout_shas':['C'], 'label_details':{
        'A':{'new_fail_tests':['unstable'],'flaky_tests':[]},'B':{'new_fail_tests':[],'flaky_tests':[]}}}
    b = {'m_shas_non_timeout':['A','C'], 'timeout_shas':['B'], 'label_details':{
        'A':{'new_fail_tests':[],'flaky_tests':['unstable']},'C':{'new_fail_tests':[],'flaky_tests':[]}}}
    result = compare_level_b(a,b)
    assert result['sha_match'] and result['label_match'] and result['n_compared']==1
    with pytest.raises(ValueError,match='empty'):
        compare_level_b({**a,'m_shas_non_timeout':[]},b)


def make_three_parquets(root):
    import pyarrow as pa
    import pyarrow.parquet as pq
    for name in ('click','jinja','attrs'):
        path=root/'docs/measurements'/f'bootstrap-{name}'/'pairs.parquet'
        path.parent.mkdir(parents=True)
        table=pa.table({'repo':[name]}).replace_schema_metadata({'features_version':'1'})
        pq.write_table(table,path)
    (root/'.cache').mkdir()


def test_loro_failure_cannot_reuse_stale_report_and_keeps_original_parquets(tmp_path,monkeypatch):
    import subprocess
    import pyarrow.parquet as pq
    import aggregate_bootstrap_w6 as agg
    make_three_parquets(tmp_path)
    stale=tmp_path/'docs/measurements/loro-w6-click-jinja-attrs'
    stale.mkdir()
    (stale/'report.json').write_text(json.dumps({'loro':{'skipped':False,'per_repo':[{'held_out':x} for x in ('click','jinja','attrs')]}}))
    def fail(command, **kwargs):
        paths=command[command.index('--data')+1:command.index('--out')]
        assert len(paths)==3
        assert all(pq.read_metadata(p).metadata[b'features_version']==b'1' for p in paths)
        return subprocess.CompletedProcess(command,1,'','intentional failure')
    monkeypatch.setattr(agg.subprocess,'run',fail)
    with pytest.raises(ValueError,match='train failed'):
        run_loro(tmp_path)


def test_loro_requires_all_three_distinct_held_out_results(tmp_path,monkeypatch):
    import subprocess
    import aggregate_bootstrap_w6 as agg
    make_three_parquets(tmp_path)
    def partial(command, **kwargs):
        out=Path(command[command.index('--out')+1])
        (out/'report.json').write_text(json.dumps({'loro':{'skipped':False,'per_repo':[{'held_out':'click'},{'held_out':'jinja'}]}}))
        return subprocess.CompletedProcess(command,0,'','')
    monkeypatch.setattr(agg.subprocess,'run',partial)
    with pytest.raises(ValueError,match='exactly three'):
        run_loro(tmp_path)


def test_loro_rejects_held_out_rows_without_metrics(tmp_path,monkeypatch):
    import subprocess
    import aggregate_bootstrap_w6 as agg
    make_three_parquets(tmp_path)
    def partial(command, **kwargs):
        out=Path(command[command.index('--out')+1])
        rows=[{'held_out':x} for x in ('click','jinja','attrs')]
        (out/'report.json').write_text(json.dumps({'loro':{'skipped':False,'per_repo':rows}}))
        return subprocess.CompletedProcess(command,0,'','')
    monkeypatch.setattr(agg.subprocess,'run',partial)
    with pytest.raises(ValueError,match='metrics'):
        run_loro(tmp_path)


def test_loro_rejects_absolute_paths_in_every_exported_json(tmp_path,monkeypatch):
    import subprocess
    import aggregate_bootstrap_w6 as agg
    make_three_parquets(tmp_path)
    def complete(command, **kwargs):
        out=Path(command[command.index('--out')+1])
        rows=[{'held_out':x,'n_changes':1,'recall@10%':0.5,'objective':'binary',
               'best_iteration':1,'baselines':{'cov_lines':0.4,'cc_max':0.3,'random':0.1}}
              for x in ('click','jinja','attrs')]
        payload={'loro':{'skipped':False,'per_repo':rows,'macro_recall@10%':0.5}}
        (out/'report.json').write_text(json.dumps(payload))
        (out/'model.json').write_text(json.dumps({'source':'/Users/private-person/project'}))
        return subprocess.CompletedProcess(command,0,'','')
    monkeypatch.setattr(agg.subprocess,'run',complete)
    with pytest.raises(ValueError,match='absolute path'):
        run_loro(tmp_path)


def test_strict_json_loader_rejects_non_finite_constants(tmp_path):
    path=tmp_path/'report.json'
    path.write_text('{"metric": NaN}')
    with pytest.raises(ValueError,match='non-finite'):
        import aggregate_bootstrap_w6 as agg
        agg.load(path)
