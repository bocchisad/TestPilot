//! Feature dump / show orchestration (emit-then-update).

use std::collections::{HashMap, HashSet};
use std::path::Path;

use crate::error::Result;
use crate::features::change::{change_size_stats, changed_functions, is_test_path, src_files};
use crate::features::cochange::CoChangeState;
use crate::features::coverage::{CovFeatures, CoverageTracker, decode_lines};
use crate::features::history::HistoryState;
use crate::features::imports::build_import_graph;
use crate::features::names::name_features;
use crate::features::writer::{self, FeatureRow};
use crate::git::{DiffHunk, GitRepo, Sha};
use crate::store::Store;

/// Infer test file path from a pytest-style node id (`path::…`).
pub fn test_file_of(test_id: &str) -> String {
    test_id
        .split("::")
        .next()
        .unwrap_or(test_id)
        .replace('\\', "/")
}

fn label_of(outcome: &str) -> i64 {
    if matches!(outcome, "fail" | "error") {
        1
    } else {
        0
    }
}

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

fn old_sources_for_hunks<R: GitRepo>(
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

fn all_changed_funcs(hunks: &[DiffHunk], old_sources: &HashMap<String, String>) -> Vec<String> {
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

struct EmitCtx<'a> {
    repo_name: &'a str,
    change: &'a str,
    parent: &'a str,
    commit_idx: i64,
    time: i64,
    hunks: &'a [DiffHunk],
    srcf: &'a [String],
    funcs: &'a [String],
    old_sources: &'a HashMap<String, String>,
    history: &'a HistoryState,
    cochange: &'a CoChangeState,
    imp_dist_for: &'a dyn Fn(&str) -> i64,
    cov: &'a crate::features::coverage::CovSnapshot,
    ch_nfiles: i64,
    ch_nsrc: i64,
    ch_add: i64,
    ch_rm: i64,
    ch_nfunc: i64,
    changed_paths: &'a HashSet<String>,
}

fn emit_row(ctx: &EmitCtx<'_>, test_id: &str, outcome: &str, flaky: bool) -> FeatureRow {
    let tf = test_file_of(test_id);
    let (cc_max, cc_sum) = ctx.cochange.score(ctx.srcf, &tf, ctx.time);
    let imp_dist = (ctx.imp_dist_for)(&tf);
    let imp_direct = if imp_dist == 1 { 1 } else { 0 };
    let (name_file, name_jac, name_func) = name_features(ctx.srcf, ctx.funcs, test_id, &tf);
    let CovFeatures {
        cov_known,
        cov_file,
        cov_func,
        cov_lines,
        cov_breadth,
        cov_mod,
        cov_lag,
    } = ctx
        .cov
        .features_for(test_id, ctx.srcf, ctx.hunks, ctx.old_sources, imp_dist);

    FeatureRow {
        repo: ctx.repo_name.to_string(),
        change: ctx.change.to_string(),
        parent: ctx.parent.to_string(),
        commit_idx: ctx.commit_idx,
        time: ctx.time,
        test: test_id.to_string(),
        label: label_of(outcome),
        flaky: if flaky { 1 } else { 0 },
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
        t_fail_rate: ctx.history.t_fail_rate(test_id),
        t_fail_cnt: ctx.history.t_fail_cnt(test_id),
        t_obs: ctx.history.t_obs(test_id),
        pf_max: ctx.history.pf_max(ctx.srcf, test_id),
        pq_max: ctx.history.pq_max(ctx.funcs, test_id),
        t_age_days: ctx.history.t_age_days(test_id, ctx.time),
        dur: ctx.history.dur(test_id),
        tfile_ntests: ctx.history.tfile_ntests(&tf),
        t_changed: if ctx.changed_paths.contains(&tf) {
            1
        } else {
            0
        },
        t_flaky_rate: ctx.history.t_flaky_rate(test_id),
        ch_nfiles: ctx.ch_nfiles,
        ch_nsrc: ctx.ch_nsrc,
        ch_add: ctx.ch_add,
        ch_rm: ctx.ch_rm,
        ch_nfunc: ctx.ch_nfunc,
    }
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
    let mut processed: Vec<String> = Vec::new();
    let mut rows = Vec::new();
    let mut commit_idx = 0i64;

    for meta in &commits {
        let sha = meta.sha.as_str();
        let Some(parent_s) = meta.parents.first().map(|p| p.as_str().to_string()) else {
            // root — may still record coverage for later lag lookups
            if let Ok(Some(run_id)) = store.last_clean_run_with_coverage(sha) {
                if let Ok(m) = load_cov_map(store, run_id) {
                    cov_tracker.record(sha, m);
                }
            }
            processed.push(sha.to_string());
            continue;
        };

        let Some(primary_id) = store.last_clean_run_with_results(sha)? else {
            if let Ok(Some(run_id)) = store.last_clean_run_with_coverage(sha) {
                if let Ok(m) = load_cov_map(store, run_id) {
                    cov_tracker.record(sha, m);
                }
            }
            processed.push(sha.to_string());
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

        for r in &results {
            let ctx = EmitCtx {
                repo_name,
                change: sha,
                parent: &parent_s,
                commit_idx,
                time: t0,
                hunks: &hunks,
                srcf: &srcf,
                funcs: &funcs,
                old_sources: &old_sources,
                history: &history,
                cochange: &cochange,
                imp_dist_for: &imp_dist_for,
                cov: &cov,
                ch_nfiles,
                ch_nsrc,
                ch_add,
                ch_rm,
                ch_nfunc,
                changed_paths: &changed_paths,
            };
            rows.push(emit_row(&ctx, &r.test_id, &r.outcome, r.flaky));
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
            }
        }
        processed.push(sha.to_string());
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
    let rows = compute_dump_rows(repo, store, repo_name)?;
    let n = rows.len();
    writer::write_parquet(out, &rows)?;
    Ok(n)
}

/// Features for a single change `base..head` (or worktree — caller supplies hunks).
pub fn compute_show_rows<R: GitRepo>(
    repo: &R,
    store: &Store,
    repo_name: &str,
    base: &Sha,
    head: &Sha,
    hunks: &[DiffHunk],
) -> Result<Vec<FeatureRow>> {
    // Build history by dumping up through base (exclusive of head change itself).
    // Simpler approach: full dump filter is wrong; walk until base inclusive for counters,
    // then emit for base..head without updating.
    let commits = repo.walk_topo_old_to_new(None, None)?;
    let mut history = HistoryState::new();
    let mut cochange = CoChangeState::new();
    let mut cov_tracker = CoverageTracker::new();
    let mut processed: Vec<String> = Vec::new();
    let base_s = base.as_str().to_string();

    for meta in &commits {
        let sha = meta.sha.as_str();
        // Stop updating after we've processed `base`.
        let past_base = processed.iter().any(|p| p == &base_s) && sha != base_s;

        if let Some(parent) = meta.parents.first() {
            if let Some(primary_id) = store.last_clean_run_with_results(sha)? {
                if !past_base {
                    let parent_sha = parent.clone();
                    let hs = repo.diff_hunks(&parent_sha, &meta.sha).unwrap_or_default();
                    let srcf = src_files(&hs);
                    let old_sources = old_sources_for_hunks(repo, &parent_sha, &hs)?;
                    let funcs = all_changed_funcs(&hs, &old_sources);
                    let results = store.results_for_run(primary_id)?;
                    for r in &results {
                        let tf = test_file_of(&r.test_id);
                        history.update_primary(
                            &r.test_id,
                            &tf,
                            &r.outcome,
                            r.flaky,
                            r.duration_ms,
                            meta.author_ts,
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
                    cochange.record(&files, meta.author_ts);
                }
            }
        }
        if let Ok(Some(run_id)) = store.last_clean_run_with_coverage(sha) {
            if !past_base {
                if let Ok(m) = load_cov_map(store, run_id) {
                    cov_tracker.record(sha, m);
                }
            }
        }
        processed.push(sha.to_string());
        if sha == base_s {
            // continue loop to mark past_base on next iters
        }
    }

    // Tests: last clean run at base or earlier
    let mut test_run = store.last_clean_run_with_results(base.as_str())?;
    if test_run.is_none() {
        let mut cur = base.as_str().to_string();
        for _ in 0..10_000 {
            let Some(p) = store.first_parent(&cur)? else {
                break;
            };
            test_run = store.last_clean_run_with_results(&p)?;
            if test_run.is_some() {
                break;
            }
            cur = p;
        }
    }
    let Some(run_id) = test_run else {
        return Ok(vec![]);
    };
    let results = store.results_for_run(run_id)?;

    let srcf = src_files(hunks);
    let old_sources = old_sources_for_hunks(repo, base, hunks)?;
    let funcs = all_changed_funcs(hunks, &old_sources);
    let (ch_nfiles, ch_nsrc, ch_add, ch_rm, _) = change_size_stats(hunks);
    let ch_nfunc = funcs.len() as i64;
    let changed_paths: HashSet<String> = hunks.iter().map(|h| h.path.clone()).collect();

    let mut chain = vec![base_s.clone()];
    let mut cur = base_s.clone();
    for _ in 0..10_000 {
        let Some(pp) = store.first_parent(&cur)? else {
            break;
        };
        chain.push(pp.clone());
        cur = pp;
    }
    let cov = cov_tracker.lookup(&base_s, &chain);

    let paths = repo.list_paths_at_rev(base)?;
    let py_paths: Vec<String> = paths.into_iter().filter(|p| p.ends_with(".py")).collect();
    let graph = build_import_graph(repo, store, base, &py_paths)?;
    let srcf_owned = srcf.clone();
    let imp_dist_for = |tf: &str| graph.dist(tf, &srcf_owned);

    let t0 = store
        .commit_author_ts(head.as_str())?
        .or(store.commit_author_ts(base.as_str())?)
        .unwrap_or(0);

    let mut rows = Vec::new();
    for r in &results {
        let ctx = EmitCtx {
            repo_name,
            change: head.as_str(),
            parent: base.as_str(),
            commit_idx: 0,
            time: t0,
            hunks,
            srcf: &srcf,
            funcs: &funcs,
            old_sources: &old_sources,
            history: &history,
            cochange: &cochange,
            imp_dist_for: &imp_dist_for,
            cov: &cov,
            ch_nfiles,
            ch_nsrc,
            ch_add,
            ch_rm,
            ch_nfunc,
            changed_paths: &changed_paths,
        };
        rows.push(emit_row(&ctx, &r.test_id, &r.outcome, r.flaky));
    }
    writer::sort_rows(&mut rows);
    Ok(rows)
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
