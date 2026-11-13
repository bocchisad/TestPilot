//! Shared (change, test) feature rows for show / select / dump leaf helpers.

use std::collections::{HashMap, HashSet};

use crate::error::Result;
use crate::features::change::{change_size_stats, changed_functions, is_test_path, src_files};
use crate::features::cochange::CoChangeState;
use crate::features::coverage::{CovFeatures, CovSnapshot};
use crate::features::history::HistoryState;
use crate::features::imports::build_import_graph_seeded;
use crate::features::names::name_features;
use crate::features::writer::{self, FeatureRow};
use crate::git::{DiffHunk, GitRepo, Sha};
use crate::store::{Store, TestResultRow};

fn label_of(outcome: &str) -> i64 {
    if matches!(outcome, "fail" | "error") {
        1
    } else {
        0
    }
}

pub(crate) fn old_sources_for_hunks<R: GitRepo>(
    repo: &R,
    parent: &Sha,
    hunks: &[DiffHunk],
) -> Result<HashMap<String, String>> {
    let mut map = HashMap::new();
    for h in hunks {
        let old_path = h.old_path.as_deref().unwrap_or(&h.path);
        if let Some(bytes) = repo.file_at_rev(parent, old_path)? {
            map.insert(h.path.clone(), String::from_utf8_lossy(&bytes).into_owned());
        }
    }
    Ok(map)
}

pub(crate) fn all_changed_funcs(
    hunks: &[DiffHunk],
    old_sources: &HashMap<String, String>,
) -> Vec<String> {
    let mut funcs = Vec::new();
    for h in hunks {
        if !h.path.ends_with(".py") || is_test_path(&h.path) {
            continue;
        }
        let src = old_sources.get(&h.path).map(|s| s.as_str());
        funcs.extend(changed_functions(&h.path, src, &h.old_line_ranges));
    }
    funcs.sort();
    funcs.dedup();
    funcs
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn emit_change_row(
    repo_name: &str,
    opts: &ChangeRowsOpts,
    srcf: &[String],
    funcs: &[String],
    history: &HistoryState,
    cochange: &CoChangeState,
    imp_dist_for: &dyn Fn(&str) -> i64,
    cov: &CovSnapshot,
    cov_prep: &[crate::features::coverage::CovSrcPrep],
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
    let tf = test_file_of(test_id);
    let (cc_max, cc_sum) = cochange.score(srcf, &tf, opts.time);
    let imp_dist = imp_dist_for(&tf);
    let imp_direct = if imp_dist == 1 { 1 } else { 0 };
    let (name_file, name_jac, name_func) = name_features(srcf, funcs, test_id, &tf);
    let CovFeatures {
        cov_known,
        cov_file,
        cov_func,
        cov_lines,
        cov_breadth,
        cov_mod,
        cov_lag,
    } = cov.features_for(test_id, cov_prep, imp_dist);

    FeatureRow {
        repo: repo_name.to_string(),
        change: opts.change.clone(),
        parent: opts.parent.clone(),
        commit_idx: opts.commit_idx,
        time: opts.time,
        test: test_id.to_string(),
        label: label_of(outcome),
        flaky: if flaky { 1 } else { 0 },
        synthetic: opts.synthetic,
        base_change: opts.base_change.clone(),
        cc_max,
        cc_sum,
        imp_dist,
        imp_direct,
        name_file,
        name_jac,
        name_func,
        cov_known,
        cov_file,
        cov_func,
        cov_lines,
        cov_breadth,
        cov_mod,
        cov_lag,
        t_fail_rate: history.t_fail_rate(test_id),
        t_fail_cnt: history.t_fail_cnt(test_id),
        t_obs: history.t_obs(test_id),
        pf_max: history.pf_max(srcf, test_id),
        pq_max: history.pq_max(funcs, test_id),
        t_age_days: history.t_age_days(test_id, opts.time),
        dur: history.dur(test_id),
        tfile_ntests: history.tfile_ntests(&tf),
        t_changed: if changed_paths.contains(&tf) { 1 } else { 0 },
        t_flaky_rate: history.t_flaky_rate(test_id),
        ch_nfiles,
        ch_nsrc,
        ch_add,
        ch_rm,
        ch_nfunc,
    }
}

/// Hard error when `feature_history_snapshot` is missing at `base` (A9).
pub fn snapshot_missing_message(base: &str) -> String {
    format!("no feature snapshot for {base}; run: testpilot collect git && testpilot features dump")
}

/// Row metadata for a single change `parent..change` feature emit.
#[derive(Debug, Clone)]
pub struct ChangeRowsOpts {
    /// Row `change` column (the commit being scored, usually `head`).
    pub change: String,
    /// Row `parent` column (history snapshot base).
    pub parent: String,
    pub commit_idx: i64,
    pub synthetic: i64,
    pub base_change: String,
    /// Author time of `head` (worktree: HEAD commit, not wall clock).
    pub time: i64,
}

/// Infer test file path from a pytest-style node id (`path::…`).
pub fn test_file_of(test_id: &str) -> String {
    test_id
        .split("::")
        .next()
        .unwrap_or(test_id)
        .replace('\\', "/")
}

/// Seeds for head-seeded import graph (Phase B / change rows).
pub fn import_graph_seeds(
    srcf: &[String],
    hunks: &[DiffHunk],
    test_ids: impl IntoIterator<Item = impl AsRef<str>>,
) -> Vec<String> {
    let mut seed_set: HashSet<String> = srcf.iter().cloned().collect();
    for tid in test_ids {
        seed_set.insert(test_file_of(tid.as_ref()));
    }
    for h in hunks {
        if h.path.ends_with(".py") {
            seed_set.insert(h.path.clone());
        }
    }
    seed_set.into_iter().collect()
}

/// Build import-distance lookup at `graph_rev` (head for show/select; mutant tip for Phase B).
pub fn imp_dist_cache_at_rev<R: GitRepo>(
    repo: &R,
    store: &Store,
    graph_rev: &Sha,
    srcf: &[String],
    seeds: &[String],
    test_ids: &[String],
) -> Result<HashMap<String, i64>> {
    let paths = repo.list_paths_at_rev(graph_rev)?;
    let py_index: Vec<String> = paths.into_iter().filter(|p| p.ends_with(".py")).collect();
    let graph = build_import_graph_seeded(repo, store, graph_rev, seeds, &py_index)?;
    let mut dist_cache: HashMap<String, i64> = HashMap::new();
    for tf in test_ids {
        dist_cache
            .entry(tf.clone())
            .or_insert_with(|| graph.dist(tf, srcf));
    }
    Ok(dist_cache)
}

/// Feature rows for one change using a frozen history snapshot and test outcomes at `base`.
#[allow(clippy::too_many_arguments)]
pub fn compute_change_rows<R: GitRepo>(
    repo: &R,
    store: &Store,
    repo_name: &str,
    base: &Sha,
    head: &Sha,
    hunks: &[DiffHunk],
    history: &HistoryState,
    cochange: &CoChangeState,
    cov: &CovSnapshot,
    results: &[TestResultRow],
    opts: &ChangeRowsOpts,
) -> Result<Vec<FeatureRow>> {
    let srcf = src_files(hunks);
    let old_sources = old_sources_for_hunks(repo, base, hunks)?;
    let funcs = all_changed_funcs(hunks, &old_sources);
    let (ch_nfiles, ch_nsrc, ch_add, ch_rm, _) = change_size_stats(hunks);
    let ch_nfunc = funcs.len() as i64;
    let changed_paths: HashSet<String> = hunks.iter().map(|h| h.path.clone()).collect();
    let cov_prep = CovSnapshot::prepare_src(&srcf, hunks, &old_sources);

    let test_files: Vec<String> = results.iter().map(|r| test_file_of(&r.test_id)).collect();
    let seeds = import_graph_seeds(&srcf, hunks, results.iter().map(|r| &r.test_id));
    let dist_cache = imp_dist_cache_at_rev(repo, store, head, &srcf, &seeds, &test_files)?;
    let imp_dist_for = |tf: &str| dist_cache.get(tf).copied().unwrap_or(99);

    let mut rows = Vec::with_capacity(results.len());
    for r in results {
        rows.push(emit_change_row(
            repo_name,
            opts,
            &srcf,
            &funcs,
            history,
            cochange,
            &imp_dist_for,
            cov,
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
    writer::sort_rows(&mut rows);
    Ok(rows)
}

/// Run used for the candidate base test list (`base_run_sha` in select JSON).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CleanRunRef {
    pub run_id: i64,
    pub sha: String,
}

/// Last clean run on `base` or an ancestor (merge-aware).
pub fn last_clean_run_at_or_before(store: &Store, base: &str) -> Result<Option<i64>> {
    Ok(last_clean_run_ref_at_or_before(store, base)?.map(|r| r.run_id))
}

pub fn last_clean_run_ref_at_or_before(store: &Store, base: &str) -> Result<Option<CleanRunRef>> {
    if let Some(run_id) = store.last_clean_run_with_results(base)? {
        return Ok(Some(CleanRunRef {
            run_id,
            sha: base.to_string(),
        }));
    }
    let mut cur = base.to_string();
    for _ in 0..100_000 {
        let Some(p) = store.first_parent(&cur)? else {
            break;
        };
        if let Some(run_id) = store.last_clean_run_with_results(&p)? {
            return Ok(Some(CleanRunRef { run_id, sha: p }));
        }
        cur = p;
    }

    let mut best: Option<(i64, i64, String)> = None;
    let mut stack = vec![base.to_string()];
    let mut seen = HashSet::new();
    while let Some(sha) = stack.pop() {
        if !seen.insert(sha.clone()) {
            continue;
        }
        if let Some(run_id) = store.last_clean_run_with_results(&sha)? {
            let ts = store.commit_author_ts(&sha)?.unwrap_or(0);
            best = Some(match best {
                Some((bts, brid, bsha)) if bts > ts || (bts == ts && brid > run_id) => {
                    (bts, brid, bsha)
                }
                _ => (ts, run_id, sha.clone()),
            });
        }
        for p in store.all_parents(&sha)? {
            stack.push(p);
        }
    }
    Ok(best.map(|(_, id, sha)| CleanRunRef { run_id: id, sha }))
}
