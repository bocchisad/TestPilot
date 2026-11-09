//! Feature dump / show orchestration (emit-then-update).

use std::collections::{HashMap, HashSet};
use std::path::Path;

use crate::error::{Error, Result};
use crate::features::change::{change_size_stats, changed_functions, is_test_path, src_files};
use crate::features::cochange::CoChangeState;
use crate::features::coverage::{
    CovFeatures, CovSnapshot, CovSrcPrep, CoverageTracker, decode_lines,
};
use crate::features::history::HistoryState;
use crate::features::imports::{build_import_graph, build_import_graph_seeded};
use crate::features::names::name_features;
use crate::features::snapshot::{find_snapshot_on_lineage, save_snapshot, should_save_snapshot};
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
    srcf: &'a [String],
    funcs: &'a [String],
    history: &'a HistoryState,
    cochange: &'a CoChangeState,
    imp_dist_for: &'a dyn Fn(&str) -> i64,
    cov: &'a CovSnapshot,
    cov_prep: &'a [CovSrcPrep],
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
    } = ctx.cov.features_for(test_id, ctx.cov_prep, imp_dist);

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
            let ctx = EmitCtx {
                repo_name,
                change: sha,
                parent: &parent_s,
                commit_idx,
                time: t0,
                srcf: &srcf,
                funcs: &funcs,
                history: &history,
                cochange: &cochange,
                imp_dist_for: &imp_dist_for,
                cov: &cov,
                cov_prep: &cov_prep,
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
    let rows = compute_dump_rows(repo, store, repo_name)?;
    let n = rows.len();
    writer::write_parquet(out, &rows)?;
    Ok(n)
}

fn show_timings_enabled() -> bool {
    std::env::var_os("TESTPILOT_SHOW_TIMINGS").is_some()
}

fn mark(t0: std::time::Instant) -> f64 {
    t0.elapsed().as_secs_f64() * 1000.0
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
    let (history, cochange, cov_source_sha) = match find_snapshot_on_lineage(store, &base_s)? {
        Some((snap_sha, h, c, cov)) if snap_sha == base_s => (h, c, cov),
        Some((snap_sha, mut h, mut c, mut cov)) => {
            // Short topo catch-up: same emit-then-update rules as dump, only for
            // ancestors of `base` after `snap_sha` (W2-12 option b).
            catch_up_topo(repo, store, &snap_sha, &base_s, &mut h, &mut c, &mut cov)?;
            (h, c, cov)
        }
        None => {
            return Err(Error::Other(format!(
                "no feature snapshot for {base_s}; run `testpilot features dump` first"
            )));
        }
    };
    let ms_snap = mark(t);

    // Tests: last clean run at base or any ancestor (all parents), preferring newest author_ts.
    let t = std::time::Instant::now();
    let Some(run_id) = last_clean_run_at_or_before(store, &base_s)? else {
        return Ok(vec![]);
    };
    let results = store.results_for_run(run_id)?;
    let ms_tests = mark(t);

    let t = std::time::Instant::now();
    let srcf = src_files(hunks);
    let old_sources = old_sources_for_hunks(repo, base, hunks)?;
    let funcs = all_changed_funcs(hunks, &old_sources);
    let (ch_nfiles, ch_nsrc, ch_add, ch_rm, _) = change_size_stats(hunks);
    let ch_nfunc = funcs.len() as i64;
    let changed_paths: HashSet<String> = hunks.iter().map(|h| h.path.clone()).collect();
    let ms_diff = mark(t);

    let t = std::time::Instant::now();
    let cov = resolve_cov_from_source(store, &base_s, cov_source_sha.as_deref())?;
    let cov_prep = CovSnapshot::prepare_src(&srcf, hunks, &old_sources);
    let ms_cov = mark(t);

    let t = std::time::Instant::now();
    let all_paths = repo.list_paths_at_rev(base)?;
    let py_index: Vec<String> = all_paths
        .into_iter()
        .filter(|p| p.ends_with(".py"))
        .collect();
    let mut seeds: HashSet<String> = srcf.iter().cloned().collect();
    for r in &results {
        seeds.insert(test_file_of(&r.test_id));
    }
    for h in hunks {
        if h.path.ends_with(".py") {
            seeds.insert(h.path.clone());
        }
    }
    let seed_list: Vec<String> = seeds.into_iter().collect();
    let graph = build_import_graph_seeded(repo, store, base, &seed_list, &py_index)?;
    let mut dist_cache: HashMap<String, i64> = HashMap::new();
    for r in &results {
        let tf = test_file_of(&r.test_id);
        dist_cache
            .entry(tf.clone())
            .or_insert_with(|| graph.dist(&tf, &srcf));
    }
    let imp_dist_for = |tf: &str| dist_cache.get(tf).copied().unwrap_or(99);
    let ms_imports = mark(t);

    let t0 = store
        .commit_author_ts(head.as_str())?
        .or(store.commit_author_ts(base.as_str())?)
        .unwrap_or(0);

    let t = std::time::Instant::now();
    let mut rows = Vec::with_capacity(results.len());
    for r in &results {
        let ctx = EmitCtx {
            repo_name,
            change: head.as_str(),
            parent: base.as_str(),
            commit_idx: 0,
            time: t0,
            srcf: &srcf,
            funcs: &funcs,
            history: &history,
            cochange: &cochange,
            imp_dist_for: &imp_dist_for,
            cov: &cov,
            cov_prep: &cov_prep,
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
    let ms_emit = mark(t);

    if timed {
        eprintln!(
            "show timings_ms: snapshot={ms_snap:.1} tests={ms_tests:.1} diff={ms_diff:.1} \
             cov={ms_cov:.1} imports={ms_imports:.1} emit={ms_emit:.1} total={:.1} rows={}",
            mark(t_all),
            rows.len()
        );
    }

    Ok(rows)
}

/// Last clean run on `base` or an ancestor.
///
/// Fast path: exact sha, then first-parent chain. If still none, BFS over all parents
/// (merge second-parent lineage) and pick the newest by author_ts.
fn last_clean_run_at_or_before(store: &Store, base: &str) -> Result<Option<i64>> {
    if let Some(run_id) = store.last_clean_run_with_results(base)? {
        return Ok(Some(run_id));
    }
    let mut cur = base.to_string();
    for _ in 0..100_000 {
        let Some(p) = store.first_parent(&cur)? else {
            break;
        };
        if let Some(run_id) = store.last_clean_run_with_results(&p)? {
            return Ok(Some(run_id));
        }
        cur = p;
    }

    let mut best: Option<(i64, i64)> = None; // (author_ts, run_id)
    let mut stack = vec![base.to_string()];
    let mut seen = HashSet::new();
    while let Some(sha) = stack.pop() {
        if !seen.insert(sha.clone()) {
            continue;
        }
        if let Some(run_id) = store.last_clean_run_with_results(&sha)? {
            let ts = store.commit_author_ts(&sha)?.unwrap_or(0);
            best = Some(match best {
                Some((bts, brid)) if bts > ts || (bts == ts && brid > run_id) => (bts, brid),
                _ => (ts, run_id),
            });
        }
        for p in store.all_parents(&sha)? {
            stack.push(p);
        }
    }
    Ok(best.map(|(_, id)| id))
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
