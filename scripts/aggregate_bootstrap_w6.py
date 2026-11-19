#!/usr/bin/env python3
"""Strict W6 measurement export. Raw DB/Parquet/ledgers remain untracked."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile

ABS_BAD = re.compile(r"/(?:home|Users|tmp|private)/")


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def forbid_abs(obj: object, path: str) -> None:
    if ABS_BAD.search(json.dumps(obj, ensure_ascii=False)):
        raise ValueError(f"absolute path leak in {path}")


def paired_ci(train: dict) -> tuple:
    value = train.get("paired_delta_ci_synthetic_test_cov_lines")
    if not isinstance(value, dict):
        return None, None, None
    ci = value.get("ci95", [None, None])
    return value.get("mean"), ci[0], ci[1]


def test_nc(train: dict) -> tuple:
    ci = train.get("paired_delta_ci_synthetic_test_cov_lines") or {}
    diag = train.get("diagnostics") or {}
    return (ci.get("n_C", diag.get("mutant_test_distinct_base_change")),
            ci.get("n_mutants", diag.get("mutant_test_new_fail_changes")))


def run_details(con, sha, synthetic):
    row = con.execute("""SELECT id FROM test_runs WHERE sha=? AND synthetic=? AND dirty=0
        AND partial=0 AND finished_at IS NOT NULL ORDER BY id DESC LIMIT 1""", (sha, synthetic)).fetchone()
    if row is None:
        raise ValueError(f"missing finished run for {sha}")
    rows = con.execute("""SELECT t.test_id, r.outcome, r.flaky FROM test_results r
        JOIN tests t ON t.id=r.test_pk WHERE r.run_id=?""", (row[0],)).fetchall()
    return ({t for t, outcome, flaky in rows if outcome in ('fail', 'error') and not flaky},
            {t for t, _, flaky in rows if flaky})


def collect_mutants(repo: Path) -> dict:
    boot = repo / '.testpilot/bootstrap'
    ledger = boot / 'mutate_outcomes.jsonl'
    if not ledger.is_file():
        raise ValueError('missing explicit mutation outcome ledger')
    events = [json.loads(line) for line in ledger.read_text().splitlines() if line.strip()]
    latest = {row['pair']: row for row in events}
    completed_path = boot / 'mutate_pairs.jsonl'
    if completed_path.is_file():
        completed = {json.loads(line)['pair'] for line in completed_path.read_text().splitlines() if line.strip()}
        if completed - latest.keys():
            raise ValueError('completed mutant missing from explicit outcome ledger')
    labels, details, non_timeout, timeout_pairs, timeout_shas, collection = {}, {}, [], [], [], []
    with sqlite3.connect(repo / '.testpilot/db.sqlite') as con:
        for pair, event in sorted(latest.items()):
            sha = event['m_sha']
            status = event['status']
            if status == 'timeout':
                timeout_pairs.append(pair)
                timeout_shas.append(sha)
                continue
            if status == 'infrastructure_failure':
                raise ValueError('infrastructure failure in completed measurement')
            non_timeout.append(sha)
            if status == 'collection_error':
                collection.append(pair)
                continue
            base, _ = pair.rsplit(':', 1)
            fails_c, flaky_c = run_details(con, base, 0)
            fails_m, flaky_m = run_details(con, sha, 1)
            flaky = flaky_c | flaky_m | set(event.get('flaky_tests', []))
            new = (fails_m - fails_c) - flaky
            labels[sha] = 'kill' if new else 'survive'
            details[sha] = {'new_fail_tests': sorted(new), 'flaky_tests': sorted(flaky)}
    return {'m_shas_non_timeout': sorted(set(non_timeout)), 'labels': labels, 'label_details': details,
            'timeout_pairs': timeout_pairs, 'timeout_shas': sorted(set(timeout_shas)),
            'collection_error_pairs': collection, 'pairs': sorted(latest),
            'attempts_recorded': len(events)}


def compare_level_b(gold: dict, second: dict) -> dict:
    if not gold['m_shas_non_timeout'] or not second['m_shas_non_timeout']:
        raise ValueError('level-b cannot compare empty mutant sets')
    excluded = set(gold['timeout_shas']) | set(second['timeout_shas'])
    left = set(gold['m_shas_non_timeout']) - excluded
    right = set(second['m_shas_non_timeout']) - excluded
    common = left & right
    label_match = True
    for sha in common:
        a = gold['label_details'].get(sha)
        b = second['label_details'].get(sha)
        if a is None or b is None:
            label_match = label_match and a == b
            continue
        flaky = set(a['flaky_tests']) | set(b['flaky_tests'])
        label_match = label_match and bool(set(a['new_fail_tests'])-flaky) == bool(set(b['new_fail_tests'])-flaky)
    return {'gold': gold, 'run_b': second, 'excluded_timeout_shas': sorted(excluded),
            'sha_match': left == right, 'label_match': label_match,
            'n_compared': len(common)}


def repo_block(root: Path, name: str) -> dict:
    out = root / 'docs/measurements' / f'bootstrap-{name}'
    br, model = load(out/'bootstrap_report.json'), load(out/'model.json')
    train = load(out/'report.json') if (out/'report.json').is_file() else model
    mean, lo, hi = paired_ci(train)
    n_c, n_m = test_nc(train)
    boot = model.get('bootstrap') or {}
    keys = ('step_timings_s', 'n_backfill_new', 'n_backfill_reused', 'n_backfill_unavailable', 'backfill_s_per_commit',
            'backfill_s_per_commit_reason', 'mutate_s_per_mutant', 'mutate_s_per_mutant_reason',
            'n_mutants_attempted', 'n_timeout', 'timeout_rate', 'n_eligible', 'n_actual', 'steps_resumed',
            'mutate_environments', 'mutate_counters_scope')
    result = {key: br.get(key) for key in keys}
    result.update(tip_repo=name, verdict=boot.get('verdict'), reason=boot.get('reason'),
                  ranker=model.get('ranker'), test_n_C=n_c, test_n_mutants=n_m,
                  paired_delta_mean=mean, ci_low=lo, ci_high=hi)
    if br.get('n_backfill_reused') != 0:
        raise ValueError(f'{name}: measurement did not start clean')
    for filename in ('bootstrap_report.json', 'model.json', 'report.json'):
        if (out/filename).is_file():
            forbid_abs(load(out/filename), filename)
    return result


def run_loro(root: Path) -> dict:
    # Keep each original Parquet's schema/version metadata intact.
    paths = [root/'docs/measurements'/f'bootstrap-{name}'/'pairs.parquet'
             for name in ('click', 'jinja', 'attrs')]
    if not all(path.is_file() for path in paths):
        raise ValueError('LORO requires all three original Parquet files')
    import pyarrow.parquet as pq
    repos = set()
    for path in paths:
        values = pq.read_table(path, columns=['repo'])['repo'].unique().to_pylist()
        if len(values) != 1:
            raise ValueError('measurement Parquet must contain exactly one repo')
        repos.update(values)
    if len(repos) != 3:
        raise ValueError('LORO requires three distinct repositories')
    out = root/'docs/measurements/loro-w6-click-jinja-attrs'
    out.mkdir(parents=True, exist_ok=True)
    # A failed process cannot reuse stale report.json from an earlier attempt.
    with tempfile.TemporaryDirectory(prefix='loro-', dir=root/'.cache') as staging:
        stage = Path(staging)
        command = [sys.executable, '-m', 'testpilot_train.cli', 'train', '--data',
                   *map(str, paths), '--out', str(stage), '--seed', '42']
        result = subprocess.run(command, cwd=root, text=True, capture_output=True)
        (out/'train.stdout.log').write_text(result.stdout)
        (out/'train.stderr.log').write_text(result.stderr)
        if result.returncode != 0:
            raise ValueError('LORO train failed; see retained stdout/stderr logs')
        report = load(stage/'report.json')
        loro = report.get('loro') or {}
        held = {row.get('held_out') for row in loro.get('per_repo', [])}
        if loro.get('skipped') is not False or held != repos or len(loro.get('per_repo', [])) != 3:
            raise ValueError('LORO did not produce exactly three held-out evaluations')
        forbid_abs(report, 'LORO report.json')
        for filename in ('report.json', 'report.md', 'model.json', 'bootstrap_report.json'):
            if (stage/filename).is_file():
                (out/filename).write_bytes((stage/filename).read_bytes())
    return loro


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--repos-root', type=Path, required=True)
    ap.add_argument('--level-b-out', type=Path, required=True)
    ap.add_argument('--bootstrap-wall-s', type=float, required=True)
    for name in ('click', 'jinja', 'attrs'):
        ap.add_argument(f'--pins-{name}', required=True)
    args = ap.parse_args()
    root = args.root
    per = {name: repo_block(root, name) for name in ('click', 'jinja', 'attrs')}
    gold_path = root/'docs/measurements/bootstrap-click/level_b_gold.json'
    if not gold_path.is_file():
        raise ValueError('missing first click gold snapshot')
    second = collect_mutants(args.repos_root/'click')
    level = compare_level_b(load(gold_path), second)
    if not level['sha_match'] or not level['label_match'] or not level['n_compared']:
        raise ValueError('level-b mutant sets or nonflaky labels differ')
    b_report = load(args.level_b_out/'bootstrap_report.json')
    per['click_level_b'] = {k: b_report.get(k) for k in ('step_timings_s', 'n_timeout', 'timeout_rate', 'n_actual')}
    loro = run_loro(root)
    host = dict(line.split('=', 1) for line in (root/'docs/measurements/env-host.txt').read_text().splitlines() if '=' in line)
    dry_path = root/'docs/measurements/dry-run-estimates.json'
    dry = load(dry_path)
    for name in ('click', 'jinja', 'attrs'):
        per[name]['estimate_s'] = dry[name]
        per[name]['actual_step_wall_s'] = sum(per[name]['step_timings_s'].values())
    actual = sum(v['actual_step_wall_s'] for k,v in per.items() if k != 'click_level_b') + sum(b_report['step_timings_s'].values())
    dry.update(actual_s=args.bootstrap_wall_s, actual_bootstrap_step_wall_s=actual, reason='full_runs_completed')
    dry_path.write_text(json.dumps(dry, indent=2)+'\n')
    summary = {'pins': {name: dict(zip(('sha','tag'), getattr(args, 'pins_'+name).split(':',1))) for name in ('click','jinja','attrs')},
               'env': {**host, 'pip_freeze':'docs/measurements/env-pip-freeze.txt', 'training':load(root/'docs/measurements/env-training-versions.json')},
               'dry_run_estimates':dry, 'repos':per, 'level_b':level, 'loro':loro,
               'n_C_widths_citation': {'path':'docs/measurements/mut2-80x5-click-jinja.json', 'n_C_test_over_N':load(root/'docs/measurements/mut2-80x5-click-jinja.json').get('n_C_test_over_N'), 'diagnostics':load(root/'docs/measurements/mut2-80x5-click-jinja.json').get('diagnostics')}}
    forbid_abs(summary, 'bootstrap-w6-click-jinja-attrs.json')
    (root/'docs/measurements/bootstrap-w6-click-jinja-attrs.json').write_text(json.dumps(summary,indent=2,sort_keys=True,allow_nan=False)+'\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
