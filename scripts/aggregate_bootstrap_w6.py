#!/usr/bin/env python3
"""Strict W6 measurement export. Raw DB/Parquet/ledgers remain untracked."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile

ABS_BAD = re.compile(r"/(?:home|Users|tmp|private)/")


def load(path: Path) -> dict:
    def reject_constant(value: str):
        raise ValueError(f"non-finite JSON value {value} in {path}")

    value = json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_constant)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object in {path}")
    return value


def forbid_abs(obj: object, path: str) -> None:
    if ABS_BAD.search(json.dumps(obj, ensure_ascii=False)):
        raise ValueError(f"absolute path leak in {path}")


def git_blob_sha1(root: Path, relative_path: Path) -> str:
    if relative_path.is_absolute():
        raise ValueError("script path must be relative to the repository root")
    result = subprocess.run(
        ["git", "hash-object", str(relative_path)],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def git_commit_sha(root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def measurement_provenance(root: Path) -> dict:
    return {
        "measurement_runner": {
            "path": "scripts/measure_bootstrap_w6.sh",
            "git_commit": "d7c73f1e1a8fdf207c8f044ba0f160dbb9288912",
            "git_blob_sha1": "f408b62d49636c259dd6980f725a5fada0311bf7",
        },
        "mutation_ledger": {
            "path": "scripts/mutate_bootstrap.py",
            "git_commit": "d7c73f1e1a8fdf207c8f044ba0f160dbb9288912",
            "git_blob_sha1": "548fcc2e98285d7d6bd2dea29d8e4c4b144b4620",
        },
        "labeling_classifier": {
            "path": "scripts/mutate/run_mutant.py",
            "click_jinja": {
                "git_commit": "6c3fc2c4ffd7fc7599f2313a0dfcb86093b3dbac",
                "git_blob_sha1": "55bacb570a74e4625eddab73ed155698c326db81",
            },
            "attrs_pairs": [
                {
                    "count": 76,
                    "git_commit": "978b50b77513c0ae8ff53ece4c04222cd85a17bd",
                    "git_blob_sha1": "3a6f035b688d6e35f507d07ae4b1211f2ada4d79",
                    "period": "before the attrs classifier fixes",
                },
                {
                    "count": 36,
                    "git_commit": "9e863f1d0283c274c05f49d1124550e5151251b5",
                    "git_blob_sha1": "7cdb0938e52c44b7733cb3b1213048e21069a539",
                    "period": "after the attrs classifier fixes",
                },
            ],
        },
        "aggregator": {
            "path": "scripts/aggregate_bootstrap_w6.py",
            "git_commit": git_commit_sha(root),
            "git_blob_sha1": git_blob_sha1(
                root, Path("scripts/aggregate_bootstrap_w6.py")
            ),
        },
    }


def attach_provenance(path: Path, provenance: dict) -> None:
    if not path.is_file():
        return
    payload = normalize_attrs_run_name(load(path))
    payload["provenance"] = provenance
    path.write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n")


def append_bootstrap_provenance(path: Path, provenance: dict, repo_name: str) -> None:
    if not path.is_file():
        return
    classifier = provenance["labeling_classifier"]
    old, new = classifier["attrs_pairs"]
    section = "\n".join(
        [
            "## Provenance",
            "",
            (
                f"- Mutation classifier (`{classifier['path']}`): attrs {old['count']} pairs at "
                f"`{old['git_commit']}` (blob `{old['git_blob_sha1']}`); {new['count']} pairs at "
                f"`{new['git_commit']}` (blob `{new['git_blob_sha1']}`)."
                if repo_name == "attrs"
                else f"- Mutation classifier (`{classifier['path']}`): commit "
                f"`{classifier['click_jinja']['git_commit']}`, "
                f"blob `{classifier['click_jinja']['git_blob_sha1']}`."
            ),
            f"- Mutation ledger (`{provenance['mutation_ledger']['path']}`): commit "
            f"`{provenance['mutation_ledger']['git_commit']}`, blob "
            f"`{provenance['mutation_ledger']['git_blob_sha1']}`.",
            f"- Aggregator (`{provenance['aggregator']['path']}`): commit "
            f"`{provenance['aggregator']['git_commit']}`, blob "
            f"`{provenance['aggregator']['git_blob_sha1']}`.",
            "",
        ]
    )
    text = path.read_text().replace("attrs-rerun12", "attrs")
    marker = "\n## Provenance\n"
    if marker in text:
        text = text[: text.index(marker)].rstrip() + "\n"
    path.write_text(text.rstrip() + "\n\n" + section)


def normalize_attrs_run_name(value):
    if isinstance(value, dict):
        normalized = {
            key.replace("attrs-rerun12", "attrs")
            if isinstance(key, str)
            else key: normalize_attrs_run_name(item)
            for key, item in value.items()
        }
        if normalized.get("out") == "docs/measurements/bootstrap-attrs-rerun12":
            normalized["out"] = "docs/measurements/bootstrap-attrs"
        if isinstance(normalized.get("repo"), str) and normalized["repo"].endswith(
            "/attrs-rerun12"
        ):
            normalized["repo"] = "attrs"
        return normalized
    if isinstance(value, list):
        return [normalize_attrs_run_name(item) for item in value]
    if isinstance(value, str):
        return value.replace("attrs-rerun12", "attrs")
    return value


def write_loro_interpretation(root: Path, loro: dict, repos: dict) -> None:
    report_path = root / "docs/measurements/loro-w6-click-jinja-attrs/report.json"
    report = load(report_path)
    overall = report.get("paired_delta_ci_test") or {}
    nc, _ = test_nc(report)
    rows = loro["per_repo"]
    cov_macro = sum(row["baselines"]["cov_lines"] for row in rows) / len(rows)
    model_macro = loro["macro_recall@10%"]
    repo_lines = []
    for row in rows:
        name = row["held_out"]
        delta = row["recall@10%"] - row["baselines"]["cov_lines"]
        gate = repos.get(name, {})
        repo_lines.append(
            f"- {name}: model {row['recall@10%']:.3f}, cov_lines "
            f"{row['baselines']['cov_lines']:.3f} (Δ {delta:+.3f}); gate "
            f"`{gate.get('verdict', 'unknown')}` ({gate.get('reason', 'unknown')}, "
            f"n_C={gate.get('test_n_C', 'unknown')})."
        )
    provenance = report.get("provenance", {})
    classifier = provenance.get("labeling_classifier", {})
    attrs_pairs = classifier.get("attrs_pairs", [])
    labeling_text = ", ".join(
        f"{row['count']} pairs at {row['git_commit'][:7]} "
        f"(blob {row['git_blob_sha1']})"
        for row in attrs_pairs
    )
    section = "\n".join(
        [
            "## Week 6 interpretation",
            "",
            f"- LORO macro recall@10%: model {model_macro:.3f} vs cov_lines {cov_macro:.3f} "
            f"(Δ {model_macro - cov_macro:+.3f}).",
            *repo_lines,
            f"- Pooled TEST vs cov_lines: Δ {overall.get('mean', 0):+.3f}, "
            f"95% CI [{overall.get('ci95', [0, 0])[0]:.3f}, "
            f"{overall.get('ci95', [0, 0])[1]:.3f}], n={overall.get('n', 'unknown')}, n_C={nc}.",
            "- Aggregate bootstrap verdict is `use_model` on the pooled synthetic TEST gate; "
            "that result is distinct from per-repository gates and does not change the global default.",
            "- Conclusion: the model is approximately tied with cov_lines; cov_lines has the "
            "strongest feature gain. Keep cov_lines as the default.",
            "- Limitation: the dry-run estimate underpredicted measured wall time by 32–42%. "
            "Click and attrs each had TEST n_C=5 (<10); widening TEST is deferred to Week 7.",
            "- `env.date_utc` is the environment fingerprint captured before the measurement runs, "
            "not the run start time.",
            "",
            "## Provenance",
            "",
            f"- Click/Jinja classifier: commit `{classifier.get('click_jinja', {}).get('git_commit', 'unknown')}`, "
            f"blob `{classifier.get('click_jinja', {}).get('git_blob_sha1', 'unknown')}`.",
            f"- Attrs labels: {labeling_text} (76 of 112 pairs use the pre-fix classifier; "
            "36 use the updated classifier).",
            f"- Mutation ledger: `{provenance.get('mutation_ledger', {}).get('path', 'unknown')}`, "
            f"commit `{provenance.get('mutation_ledger', {}).get('git_commit', 'unknown')}`, "
            f"blob `{provenance.get('mutation_ledger', {}).get('git_blob_sha1', 'unknown')}`.",
            f"- Aggregator: `{provenance.get('aggregator', {}).get('path', 'unknown')}`, "
            f"commit `{provenance.get('aggregator', {}).get('git_commit', 'unknown')}`, "
            f"blob `{provenance.get('aggregator', {}).get('git_blob_sha1', 'unknown')}`.",
            f"- Measurement runner: `{provenance.get('measurement_runner', {}).get('path', 'unknown')}`, "
            f"commit `{provenance.get('measurement_runner', {}).get('git_commit', 'unknown')}`, "
            f"blob `{provenance.get('measurement_runner', {}).get('git_blob_sha1', 'unknown')}`.",
            "",
        ]
    )
    text = report_path.with_name("report.md").read_text()
    marker = "## Week 6 interpretation\n"
    if marker in text:
        start = text.index(marker)
        diagnostics = text.find("\n## Diagnostics\n", start)
        text = (
            text[:start] + text[diagnostics + 1 :]
            if diagnostics >= 0
            else text[:start].rstrip() + "\n"
        )
    text = text.replace("attrs-rerun12", "attrs")
    text = text.replace(
        "# TestPilot training report\n", "# TestPilot training report\n\n" + section
    )
    report_path.with_name("report.md").write_text(text)


def interpretation(loro: dict, repos: dict, report: dict) -> dict:
    rows = loro["per_repo"]
    cov_macro = sum(row["baselines"]["cov_lines"] for row in rows) / len(rows)
    overall = report.get("paired_delta_ci_test") or {}
    nc, _ = test_nc(report)
    return {
        "loro_macro_recall_at_10pct": loro["macro_recall@10%"],
        "cov_lines_macro_recall_at_10pct": cov_macro,
        "delta_vs_cov_lines": loro["macro_recall@10%"] - cov_macro,
        "per_repo": [
            {
                "repo": row["held_out"],
                "model_recall_at_10pct": row["recall@10%"],
                "cov_lines_recall_at_10pct": row["baselines"]["cov_lines"],
                "delta_vs_cov_lines": row["recall@10%"] - row["baselines"]["cov_lines"],
                "gate": repos.get(row["held_out"], {}).get("verdict"),
                "gate_reason": repos.get(row["held_out"], {}).get("reason"),
                "test_n_C": repos.get(row["held_out"], {}).get("test_n_C"),
            }
            for row in rows
        ],
        "pooled_test_delta": overall.get("mean"),
        "pooled_test_ci95": overall.get("ci95"),
        "pooled_test_n": overall.get("n"),
        "pooled_test_n_C": nc,
        "default_ranker": "cov_lines",
        "conclusion": "model approximately ties cov_lines; cov_lines dominates feature gain",
        "limitations": [
            "dry-run underpredicted measured wall time by 32–42%",
            "Click and attrs have TEST n_C=5 (<10); widen TEST in Week 7",
        ],
    }


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
    collection_error_shas = []
    with sqlite3.connect(repo / '.testpilot/db.sqlite') as con:
        for pair, event in sorted(latest.items()):
            sha = event['m_sha']
            status = event['status']
            if status not in {'complete', 'timeout', 'collection_error', 'infrastructure_failure'}:
                raise ValueError(f'unknown mutation outcome status: {status!r}')
            if status == 'timeout':
                timeout_pairs.append(pair)
                timeout_shas.append(sha)
                continue
            if status == 'infrastructure_failure':
                raise ValueError('infrastructure failure in completed measurement')
            non_timeout.append(sha)
            if status == 'collection_error':
                collection.append(pair)
                collection_error_shas.append(sha)
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
            'collection_error_pairs': collection, 'collection_error_shas': sorted(set(collection_error_shas)),
            'pairs': sorted(latest),
            'attempts_recorded': len(events)}


def compare_level_b(gold: dict, second: dict) -> dict:
    if not gold['m_shas_non_timeout'] or not second['m_shas_non_timeout']:
        raise ValueError('level-b cannot compare empty mutant sets')
    excluded = set(gold['timeout_shas']) | set(second['timeout_shas'])
    left = set(gold['m_shas_non_timeout']) - excluded
    right = set(second['m_shas_non_timeout']) - excluded
    common = left & right
    label_match = True
    n_compared = 0
    left_collection_errors = set(gold.get('collection_error_shas', []))
    right_collection_errors = set(second.get('collection_error_shas', []))
    for sha in common:
        left_is_collection_error = sha in left_collection_errors
        right_is_collection_error = sha in right_collection_errors
        if left_is_collection_error or right_is_collection_error:
            label_match = label_match and left_is_collection_error == right_is_collection_error
            continue
        a = gold['label_details'].get(sha)
        b = second['label_details'].get(sha)
        if a is None or b is None:
            raise ValueError(f'missing label details for comparable mutant {sha}')
        if not all(isinstance(row.get(key), list) for row in (a, b) for key in ('new_fail_tests', 'flaky_tests')):
            raise ValueError(f'malformed label details for comparable mutant {sha}')
        flaky = set(a['flaky_tests']) | set(b['flaky_tests'])
        label_match = label_match and bool(set(a['new_fail_tests'])-flaky) == bool(set(b['new_fail_tests'])-flaky)
        n_compared += 1
    return {'gold': gold, 'run_b': second, 'excluded_timeout_shas': sorted(excluded),
            'sha_match': left == right, 'label_match': label_match,
            'n_compared': n_compared, 'n_collection_error_compared': sum(
                sha in left_collection_errors and sha in right_collection_errors for sha in common
            )}


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
        rows = loro.get('per_repo')
        held = {row.get('held_out') for row in rows} if isinstance(rows, list) and all(isinstance(row, dict) for row in rows) else set()
        if loro.get('skipped') is not False or held != repos or not isinstance(rows, list) or len(rows) != 3:
            raise ValueError('LORO did not produce exactly three held-out evaluations')
        recalls = []
        for row in rows:
            recall = row.get('recall@10%')
            n_changes = row.get('n_changes')
            baselines = row.get('baselines')
            if (not isinstance(n_changes, int) or isinstance(n_changes, bool) or n_changes <= 0
                    or not isinstance(recall, (int, float)) or isinstance(recall, bool)
                    or not math.isfinite(recall) or not 0 <= recall <= 1
                    or not isinstance(baselines, dict)
                    or set(baselines) != {'cov_lines', 'cc_max', 'random'}
                    or any(not isinstance(value, (int, float)) or isinstance(value, bool)
                           or not math.isfinite(value) or not 0 <= value <= 1
                           for value in baselines.values())):
                raise ValueError(f"LORO row for {row.get('held_out')} is missing valid metrics")
            recalls.append(float(recall))
        macro = loro.get('macro_recall@10%')
        if (not isinstance(macro, (int, float)) or isinstance(macro, bool)
                or not math.isfinite(macro) or not 0 <= macro <= 1
                or not math.isclose(float(macro), sum(recalls) / len(recalls), rel_tol=1e-9, abs_tol=1e-9)):
            raise ValueError('LORO macro recall is missing or inconsistent with held-out rows')
        forbid_abs(report, 'LORO report.json')
        for filename in ('report.json', 'report.md', 'model.json', 'bootstrap_report.json'):
            if (stage/filename).is_file():
                if filename.endswith('.json'):
                    forbid_abs(load(stage/filename), filename)
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
    loro = normalize_attrs_run_name(run_loro(root))
    provenance = measurement_provenance(root)
    for name in ('click', 'jinja', 'attrs'):
        for filename in ('bootstrap_report.json', 'model.json', 'report.json'):
            attach_provenance(root/'docs/measurements'/f'bootstrap-{name}'/filename, provenance)
        append_bootstrap_provenance(
            root/'docs/measurements'/f'bootstrap-{name}'/'report.md', provenance, name
        )
    loro_dir = root/'docs/measurements/loro-w6-click-jinja-attrs'
    for filename in ('report.json', 'model.json', 'bootstrap_report.json'):
        attach_provenance(loro_dir/filename, provenance)
    write_loro_interpretation(root, loro, per)
    loro_interpretation = interpretation(loro, per, load(loro_dir/'report.json'))
    host = dict(line.split('=', 1) for line in (root/'docs/measurements/env-host.txt').read_text().splitlines() if '=' in line)
    dry_path = root/'docs/measurements/dry-run-estimates.json'
    dry = load(dry_path)
    for name in ('click', 'jinja', 'attrs'):
        per[name]['estimate_s'] = dry[name]
        per[name]['actual_step_wall_s'] = sum(per[name]['step_timings_s'].values())
    actual = sum(v['actual_step_wall_s'] for k,v in per.items() if k != 'click_level_b') + sum(b_report['step_timings_s'].values())
    dry.update(actual_s=args.bootstrap_wall_s, actual_bootstrap_step_wall_s=actual, reason='full_runs_completed')
    dry_path.write_text(json.dumps(dry, indent=2, allow_nan=False)+'\n')
    summary = {'pins': {name: dict(zip(('sha','tag'), getattr(args, 'pins_'+name).split(':',1))) for name in ('click','jinja','attrs')},
               'env': {**host, 'pip_freeze':'docs/measurements/env-pip-freeze.txt', 'training':load(root/'docs/measurements/env-training-versions.json')},
               'dry_run_estimates':dry, 'repos':per, 'level_b':level, 'loro':loro,
               'n_C_widths_citation': {'path':'docs/measurements/mut2-80x5-click-jinja.json', 'n_C_test_over_N':load(root/'docs/measurements/mut2-80x5-click-jinja.json').get('n_C_test_over_N'), 'diagnostics':load(root/'docs/measurements/mut2-80x5-click-jinja.json').get('diagnostics')}}
    summary['interpretation'] = loro_interpretation
    summary['provenance'] = provenance
    forbid_abs(summary, 'bootstrap-w6-click-jinja-attrs.json')
    (root/'docs/measurements/bootstrap-w6-click-jinja-attrs.json').write_text(json.dumps(summary,indent=2,sort_keys=True,allow_nan=False)+'\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
