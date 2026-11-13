//! Feature dump / show orchestration (emit-then-update).

use std::collections::{HashMap, HashSet};
use std::path::Path;

use crate::error::{Error, Result};
use crate::features::change::{change_size_stats, src_files};
use crate::features::change_rows::last_clean_run_at_or_before;
use crate::features::change_rows::{
    self, ChangeRowsOpts, all_changed_funcs, compute_change_rows, old_sources_for_hunks,
    snapshot_missing_message, test_file_of,
};
use crate::features::cochange::CoChangeState;
use crate::features::coverage::{CovSnapshot, CovSrcPrep, CoverageTracker, decode_lines};
use crate::features::history::HistoryState;
use crate::features::imports::build_import_graph;
use crate::features::snapshot::{find_snapshot_on_lineage, save_snapshot, should_save_snapshot};
use crate::features::writer::{self, FeatureRow};
use crate::git::{DiffHunk, GitRepo, GixRepo, Sha, list_mutant_refs};
use crate::store::Store;

fn load_cov_map(
    store: &Store,
    run_id: i64,
) -> Result<HashMap<String, HashMap<String, HashSet<u32>>>> {
    let rows = store.coverage_rows_for_run(run_id)?;
    let mut by_test: HashMap<String, HashMap<String, HashSet<u32>>> = HashMap::new();
    for (test_id, path, blob) in rows {
        let lines = decode_lines(&blob);
        by_test
            .entry(test_id)
            .or_default()
            .entry(path)
            .or_default()
            .extend(lines);
    }
    Ok(by_test)
}

#[allow(clippy::too_many_arguments)]
fn emit_row_phase_a(
    repo_name: &str,
    change: &str,
    parent: &str,
    commit_idx: i64,
    time: i64,
    srcf: &[String],
    funcs: &[String],
    history: &HistoryState,
    cochange: &CoChangeState,
    imp_dist_for: &dyn Fn(&str) -> i64,
    cov: &CovSnapshot,
    cov_prep: &[CovSrcPrep],
    ch_nfiles: i64,
    ch_nsrc: i64,
    ch_add: i64,
    ch_rm: i64,
    ch_nfunc: i64,
    changed_paths: &HashSet<String>,
    test_id: &str,
    outcome: &str,
    flaky: bool,
) -> FeatureRow {
    let opts = ChangeRowsOpts {
        change: change.to_string(),
        parent: parent.to_string(),
        commit_idx,
        synthetic: 0,
        base_change: String::new(),
        time,
    };
    change_rows::emit_change_row(
        repo_name,
        &opts,
        srcf,
        funcs,
        history,
        cochange,
        imp_dist_for,
        cov,
        cov_prep,
        ch_nfiles,
        ch_nsrc,
        ch_add,
        ch_rm,
        ch_nfunc,
        changed_paths,
        test_id,
        outcome,
        flaky,
    )
}

/// Compute all feature rows for the reachable history (topo old→new).
pub fn compute_dump_rows<R: GitRepo>(
    repo: &R,
    store: &Store,
    repo_name: &str,
) -> Result<Vec<FeatureRow>> {
    let commits = repo.walk_topo_old_to_new(None, None)?;
    let mut history = HistoryState::new();
    let mut cochange = CoChangeState::new();
    let mut cov_tracker = CoverageTracker::new();
    let mut cov_source_sha: Option<String> = None;
    let mut processed: Vec<String> = Vec::new();
    let mut rows = Vec::new();
    let mut commit_idx = 0i64;
    let mut topo_idx = 0usize;

    for meta in &commits {
        let sha = meta.sha.as_str();
        let Some(parent_s) = meta.parents.first().map(|p| p.as_str().to_string()) else {
            // root — may still record coverage for later lag lookups
            if let Ok(Some(run_id)) = store.last_clean_run_with_coverage(sha) {
                if let Ok(m) = load_cov_map(store, run_id) {
                    cov_tracker.record(sha, m);
                    cov_source_sha = Some(sha.to_string());
                }
            }
            if should_save_snapshot(topo_idx, false) {
                let _ = save_snapshot(store, sha, &history, &cochange, cov_source_sha.as_deref());
            }
            processed.push(sha.to_string());
            topo_idx += 1;
            continue;
        };

        let Some(primary_id) = store.last_clean_run_with_results(sha)? else {
            if let Ok(Some(run_id)) = store.last_clean_run_with_coverage(sha) {
                if let Ok(m) = load_cov_map(store, run_id) {
                    cov_tracker.record(sha, m);
                    cov_source_sha = Some(sha.to_string());
                }
            }
            if should_save_snapshot(topo_idx, false) {
                let _ = save_snapshot(store, sha, &history, &cochange, cov_source_sha.as_deref());
            }
            processed.push(sha.to_string());
            topo_idx += 1;
            continue;
        };

        let parent = Sha::from_hex(&parent_s);
        let hunks = repo.diff_hunks(&parent, &meta.sha)?;
        let srcf = src_files(&hunks);
        let old_sources = old_sources_for_hunks(repo, &parent, &hunks)?;
        let funcs = all_changed_funcs(&hunks, &old_sources);
        let (ch_nfiles, ch_nsrc, ch_add, ch_rm, _) = change_size_stats(&hunks);
        let ch_nfunc = funcs.len() as i64;
        let changed_paths: HashSet<String> = hunks.iter().map(|h| h.path.clone()).collect();

        // Coverage: parent lineage among already-processed commits (parent first).
        let mut chain = vec![parent_s.clone()];
        // Walk first-parent chain among processed
        let mut cur = parent_s.clone();
        loop {
            let Some(pp) = store.first_parent(&cur)? else {
                break;
            };
            if !processed.iter().any(|p| p == &pp) && pp != parent_s {
                // still include if we have snapshot
            }
            if chain.iter().any(|c| c == &pp) {
                break;
            }
            chain.push(pp.clone());
            cur = pp;
            if chain.len() > 10_000 {
                break;
            }
        }
        let cov = cov_tracker.lookup(&parent_s, &chain);

        // Import graph at parent
        let paths = repo.list_paths_at_rev(&parent)?;
        let py_paths: Vec<String> = paths.into_iter().filter(|p| p.ends_with(".py")).collect();
        let graph = build_import_graph(repo, store, &parent, &py_paths)?;
        let srcf_owned = srcf.clone();
        let imp_dist_for = |tf: &str| graph.dist(tf, &srcf_owned);

        let results = store.results_for_run(primary_id)?;
        commit_idx += 1;
        let t0 = meta.author_ts;
        let cov_prep = CovSnapshot::prepare_src(&srcf, &hunks, &old_sources);

        for r in &results {
            rows.push(emit_row_phase_a(
                repo_name,
                sha,
                &parent_s,
                commit_idx,
                t0,
                &srcf,
                &funcs,
                &history,
                &cochange,
                &imp_dist_for,
                &cov,
                &cov_prep,
                ch_nfiles,
                ch_nsrc,
                ch_add,
                ch_rm,
                ch_nfunc,
                &changed_paths,
                &r.test_id,
                &r.outcome,
                r.flaky,
            ));
        }

        // --- post-emit updates ---
        for r in &results {
            let tf = test_file_of(&r.test_id);
            history.update_primary(
                &r.test_id,
                &tf,
                &r.outcome,
                r.flaky,
                r.duration_ms,
                t0,
                &srcf,
                &funcs,
            );
        }
        for other_id in store.other_clean_runs(sha, primary_id)? {
            for r in store.results_for_run(other_id)? {
                history.update_flaky_only(&r.test_id, r.flaky);
            }
        }
        let mut files = store.file_paths_for_commit(sha)?;
        if files.is_empty() {
            files = hunks.iter().map(|h| h.path.clone()).collect();
        }
        cochange.record(&files, t0);

        if let Ok(Some(run_id)) = store.last_clean_run_with_coverage(sha) {
            if let Ok(m) = load_cov_map(store, run_id) {
                cov_tracker.record(sha, m);
                cov_source_sha = Some(sha.to_string());
            }
        }
        if should_save_snapshot(topo_idx, true) {
            save_snapshot(store, sha, &history, &cochange, cov_source_sha.as_deref())?;
        }
        processed.push(sha.to_string());
        topo_idx += 1;
    }

    writer::sort_rows(&mut rows);
    Ok(rows)
}

pub fn dump_parquet<R: GitRepo>(
    repo: &R,
    store: &Store,
    repo_name: &str,
    out: &Path,
) -> Result<usize> {
    dump_parquet_ex(repo, store, repo_name, out, None)
}

/// Dump Phase A (real walk). If `mutant_repo` is `Some`, also emit Phase B mutant leaves
/// from `refs/testpilot/mutants/**` without updating HistoryState / cochange / snapshots.
pub fn dump_parquet_ex<R: GitRepo>(
    repo: &R,
    store: &Store,
    repo_name: &str,
    out: &Path,
    mutant_repo: Option<&GixRepo>,
) -> Result<usize> {
    let mut rows = compute_dump_rows(repo, store, repo_name)?;
    if let Some(grepo) = mutant_repo {
        append_mutant_leaf_rows(grepo, store, repo_name, &mut rows)?;
    }
    let n = rows.len();
    writer::write_parquet(out, &rows)?;
    Ok(n)
}

/// Detect pytest collection-failure phantoms stored as module-level error cases.
fn is_collection_failure_result(r: &crate::store::TestResultRow) -> bool {
    if r.outcome != "error" && r.outcome != "fail" {
        return false;
    }
    let msg = r.message.as_deref().unwrap_or("").to_ascii_lowercase();
    if msg.contains("collection failure")
        || msg.contains("error collecting")
        || msg.contains("failed to import")
    {
        return true;
    }
    // Module-level id: no `::` separator (e.g. `tests.test_basic`).
    if !r.test_id.contains("::") && !r.test_id.contains('/') {
        return true;
    }
    false
}

fn is_collection_error_run(results: &[crate::store::TestResultRow], n_parent: usize) -> bool {
    if results.iter().any(is_collection_failure_result) {
        return true;
    }
    if n_parent > 0 && results.len() * 2 < n_parent {
        return true;
    }
    false
}

/// Phase B: emit mutant feature rows from frozen snapshot at C. Never updates history.
fn append_mutant_leaf_rows(
    repo: &GixRepo,
    store: &Store,
    repo_name: &str,
    rows: &mut Vec<FeatureRow>,
) -> Result<()> {
    let mutants = list_mutant_refs(repo)?;
    if mutants.is_empty() {
        return Ok(());
    }

    let mut commit_idx_by_c: HashMap<String, i64> = HashMap::new();
    for r in rows.iter() {
        commit_idx_by_c
            .entry(r.change.clone())
            .or_insert(r.commit_idx);
    }

    for (m_sha, c_sha, _i) in mutants {
        let Some(&commit_idx) = commit_idx_by_c.get(&c_sha) else {
            tracing::warn!(
                mutant = %m_sha,
                base = %c_sha,
                "skip mutant: base change has no dump rows (no commit_idx)"
            );
            continue;
        };
        let Some(run_id) = store.last_synthetic_run_with_results(&m_sha)? else {
            tracing::warn!(mutant = %m_sha, "skip mutant: no synthetic results run");
            continue;
        };

        let (history, cochange, cov_source_sha) = match find_snapshot_on_lineage(store, &c_sha)? {
            Some((snap_sha, h, c, cov)) if snap_sha == c_sha => (h, c, cov),
            Some((snap_sha, mut h, mut c, mut cov)) => {
                catch_up_topo(repo, store, &snap_sha, &c_sha, &mut h, &mut c, &mut cov)?;
                (h, c, cov)
            }
            None => {
                return Err(Error::Other(snapshot_missing_message(&c_sha)));
            }
        };

        let c = Sha::from_hex(&c_sha);
        let m = Sha::from_hex(&m_sha);
        let hunks = repo.diff_hunks(&c, &m)?;

        let mut chain = vec![c_sha.clone()];
        let mut cur = c_sha.clone();
        loop {
            let Some(pp) = store.first_parent(&cur)? else {
                break;
            };
            if chain.iter().any(|x| x == &pp) {
                break;
            }
            chain.push(pp.clone());
            cur = pp;
            if chain.len() > 10_000 {
                break;
            }
        }
        // Coverage from ≤ C only (non-synthetic runs via store helpers).
        let mut cov_tracker = CoverageTracker::new();
        if let Some(ref src) = cov_source_sha {
            if let Ok(Some(rid)) = store.last_clean_run_with_coverage(src) {
                if let Ok(map) = load_cov_map(store, rid) {
                    cov_tracker.record(src, map);
                }
            }
        }
        for anc in &chain {
            if let Ok(Some(rid)) = store.last_clean_run_with_coverage(anc) {
                if let Ok(map) = load_cov_map(store, rid) {
                    cov_tracker.record(anc, map);
                }
            }
        }
        let cov = cov_tracker.lookup(&c_sha, &chain);

        let results = store.results_for_run(run_id)?;
        let n_parent = store
            .last_clean_run_with_results(&c_sha)?
            .map(|rid| store.results_for_run(rid).map(|r| r.len()))
            .transpose()?
            .unwrap_or(0);
        if is_collection_error_run(&results, n_parent) {
            tracing::warn!(
                mutant = %m_sha,
                n_results = results.len(),
                n_parent,
                "skip mutant: collection_error"
            );
            continue;
        }

        let meta = repo.commit_meta_for(&m)?;
        let opts = ChangeRowsOpts {
            change: m_sha.clone(),
            parent: c_sha.clone(),
            commit_idx,
            synthetic: 1,
            base_change: c_sha.clone(),
            time: meta.author_ts,
        };
        let mut leaf = compute_change_rows(
            repo, store, repo_name, &c, &m, &hunks, &history, &cochange, &cov, &results, &opts,
        )?;
        rows.append(&mut leaf);
        // Intentionally no history / cochange / cov_tracker / snapshot updates.
    }

    writer::sort_rows(rows);
    Ok(())
}

fn show_timings_enabled() -> bool {
    std::env::var_os("TESTPILOT_SHOW_TIMINGS").is_some()
}

fn mark(t0: std::time::Instant) -> f64 {
    t0.elapsed().as_secs_f64() * 1000.0
}

/// Feature rows for one change with an explicit test list (show / select).
#[allow(clippy::too_many_arguments)]
pub fn feature_rows_for_change<R: GitRepo>(
    repo: &R,
    store: &Store,
    repo_name: &str,
    base: &Sha,
    head: &Sha,
    hunks: &[DiffHunk],
    results: &[crate::store::TestResultRow],
    head_time: i64,
) -> Result<Vec<FeatureRow>> {
    let base_s = base.as_str().to_string();
    let (history, cochange, cov_source_sha) = match find_snapshot_on_lineage(store, &base_s)? {
        Some((snap_sha, h, c, cov)) if snap_sha == base_s => (h, c, cov),
        Some((snap_sha, mut h, mut c, mut cov)) => {
            catch_up_topo(repo, store, &snap_sha, &base_s, &mut h, &mut c, &mut cov)?;
            (h, c, cov)
        }
        None => {
            return Err(Error::Other(snapshot_missing_message(&base_s)));
        }
    };
    let cov = resolve_cov_from_source(store, &base_s, cov_source_sha.as_deref())?;
    let opts = ChangeRowsOpts {
        change: head.as_str().to_string(),
        parent: base_s.clone(),
        commit_idx: 0,
        synthetic: 0,
        base_change: String::new(),
        time: head_time,
    };
    compute_change_rows(
        repo, store, repo_name, base, head, hunks, &history, &cochange, &cov, results, &opts,
    )
}

/// Features for a single change `base..head` (or worktree — caller supplies hunks).
///
/// Loads a dump-time history snapshot at `base` (or nearest first-parent ancestor
/// plus topo catch-up). Missing snapshots are an error (W2-12): never silently invent
/// first-parent-only history.
pub fn compute_show_rows<R: GitRepo>(
    repo: &R,
    store: &Store,
    repo_name: &str,
    base: &Sha,
    head: &Sha,
    hunks: &[DiffHunk],
) -> Result<Vec<FeatureRow>> {
    let base_s = base.as_str().to_string();
    let timed = show_timings_enabled();
    let t_all = std::time::Instant::now();

    let t = std::time::Instant::now();
    let ms_snap = mark(t);

    let t = std::time::Instant::now();
    let Some(run_id) = last_clean_run_at_or_before(store, &base_s)? else {
        return Ok(vec![]);
    };
    let results = store.results_for_run(run_id)?;
    let ms_tests = mark(t);

    let t0 = store
        .commit_author_ts(head.as_str())?
        .or(store.commit_author_ts(base.as_str())?)
        .unwrap_or(0);

    let t = std::time::Instant::now();
    let rows = feature_rows_for_change(repo, store, repo_name, base, head, hunks, &results, t0)?;
    let ms_emit = mark(t);

    if timed {
        eprintln!(
            "show timings_ms: snapshot={ms_snap:.1} tests={ms_tests:.1} \
             emit={ms_emit:.1} total={:.1} rows={}",
            mark(t_all),
            rows.len()
        );
    }

    Ok(rows)
}

fn ancestor_set(store: &Store, tip: &str) -> Result<HashSet<String>> {
    let mut out = HashSet::new();
    let mut stack = vec![tip.to_string()];
    while let Some(sha) = stack.pop() {
        if !out.insert(sha.clone()) {
            continue;
        }
        for p in store.all_parents(&sha)? {
            stack.push(p);
        }
    }
    Ok(out)
}

/// Replay dump-style history updates for topo commits after `after_sha` up to `until_sha`,
/// restricted to ancestors of `until_sha` (includes `until_sha`, excludes `after_sha`).
fn catch_up_topo<R: GitRepo>(
    repo: &R,
    store: &Store,
    after_sha: &str,
    until_sha: &str,
    history: &mut HistoryState,
    cochange: &mut CoChangeState,
    cov_source_sha: &mut Option<String>,
) -> Result<()> {
    let ancestors = ancestor_set(store, until_sha)?;
    // Walk only the segment after the snapshot tip — still bounded by every-K gap in practice.
    let commits = repo.walk_topo_old_to_new(Some(after_sha), None)?;
    for meta in commits {
        let sha = meta.sha.as_str();
        if !ancestors.contains(sha) {
            continue;
        }
        apply_history_update_for_commit(repo, store, sha, history, cochange, cov_source_sha)?;
        if sha == until_sha {
            break;
        }
    }
    Ok(())
}

fn apply_history_update_for_commit<R: GitRepo>(
    repo: &R,
    store: &Store,
    sha: &str,
    history: &mut HistoryState,
    cochange: &mut CoChangeState,
    cov_source_sha: &mut Option<String>,
) -> Result<()> {
    if let Some(primary_id) = store.last_clean_run_with_results(sha)? {
        let parent = store
            .first_parent(sha)?
            .map(Sha::from_hex)
            .unwrap_or_else(|| Sha::from_hex(sha.to_string()));
        let meta_sha = Sha::from_hex(sha);
        let hs = repo.diff_hunks(&parent, &meta_sha).unwrap_or_default();
        let srcf = src_files(&hs);
        let old_sources = old_sources_for_hunks(repo, &parent, &hs)?;
        let funcs = all_changed_funcs(&hs, &old_sources);
        let results = store.results_for_run(primary_id)?;
        let t0 = store.commit_author_ts(sha)?.unwrap_or(0);
        for r in &results {
            let tf = test_file_of(&r.test_id);
            history.update_primary(
                &r.test_id,
                &tf,
                &r.outcome,
                r.flaky,
                r.duration_ms,
                t0,
                &srcf,
                &funcs,
            );
        }
        for other_id in store.other_clean_runs(sha, primary_id)? {
            for r in store.results_for_run(other_id)? {
                history.update_flaky_only(&r.test_id, r.flaky);
            }
        }
        let mut files = store.file_paths_for_commit(sha)?;
        if files.is_empty() {
            files = hs.iter().map(|h| h.path.clone()).collect();
        }
        cochange.record(&files, t0);
    }
    if let Ok(Some(_)) = store.last_clean_run_with_coverage(sha) {
        *cov_source_sha = Some(sha.to_string());
    }
    Ok(())
}

fn resolve_cov_from_source(
    store: &Store,
    base: &str,
    cov_source_sha: Option<&str>,
) -> Result<crate::features::coverage::CovSnapshot> {
    use crate::features::coverage::CovSnapshot;

    // Prefer dump-recorded source; else walk first-parent for a cov run.
    let mut lag = 0i64;
    if let Some(src) = cov_source_sha {
        // lag = first-parent distance base → src
        let mut cur = base.to_string();
        let mut found_lag = None;
        for d in 0..100_000 {
            if cur == src {
                found_lag = Some(d);
                break;
            }
            match store.first_parent(&cur)? {
                Some(p) => cur = p,
                None => break,
            }
        }
        if let Some(l) = found_lag {
            if let Some(run_id) = store.last_clean_run_with_coverage(src)? {
                return Ok(CovSnapshot {
                    sha: src.to_string(),
                    lag: l,
                    by_test: load_cov_map(store, run_id)?,
                });
            }
        }
    }
    let mut cur = base.to_string();
    for _ in 0..100_000 {
        if let Some(run_id) = store.last_clean_run_with_coverage(&cur)? {
            return Ok(CovSnapshot {
                sha: cur,
                lag,
                by_test: load_cov_map(store, run_id)?,
            });
        }
        match store.first_parent(&cur)? {
            Some(p) => {
                cur = p;
                lag += 1;
            }
            None => break,
        }
    }
    Ok(CovSnapshot::empty())
}

pub fn show_format(rows: &[FeatureRow], format: &str, out: Option<&Path>) -> Result<String> {
    match format {
        "json" => writer::rows_to_json(rows),
        "parquet" => {
            let path = out.ok_or_else(|| {
                crate::error::Error::Other("--out required for parquet format".into())
            })?;
            writer::write_parquet(path, rows)?;
            Ok(format!("wrote {} rows to {}", rows.len(), path.display()))
        }
        _ => Ok(writer::rows_to_table(rows)),
    }
}

#[cfg(test)]
mod collection_error_tests {
    use super::{is_collection_error_run, is_collection_failure_result};
    use crate::store::TestResultRow;

    fn row(test_id: &str, outcome: &str, message: Option<&str>) -> TestResultRow {
        TestResultRow {
            test_id: test_id.into(),
            outcome: outcome.into(),
            duration_ms: 0,
            message: message.map(str::to_string),
            flaky: false,
            attempts: 1,
        }
    }

    #[test]
    fn module_phantom_is_collection_failure() {
        let r = row("tests.test_basic", "error", Some("collection failure"));
        assert!(is_collection_failure_result(&r));
    }

    #[test]
    fn dotted_module_id_without_colon_is_collection() {
        let r = row("tests.test_api", "error", Some("ImportError"));
        assert!(is_collection_failure_result(&r));
    }

    #[test]
    fn normal_test_failure_not_collection() {
        let r = row(
            "tests/test_basic.py::test_ok",
            "fail",
            Some("assert 1 == 2"),
        );
        assert!(!is_collection_failure_result(&r));
    }

    #[test]
    fn half_test_count_vs_parent_is_collection_run() {
        let results = vec![row("tests/a.py::t1", "pass", None)];
        assert!(is_collection_error_run(&results, 4)); // 1 < 50% of 4
        assert!(!is_collection_error_run(&results, 2)); // 1 >= 50% of 2
    }
}
