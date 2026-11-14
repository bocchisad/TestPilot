//! Budgeted test selection: candidates, force-include, LightGBM raw scores.

use std::collections::{HashMap, HashSet};

use serde::Serialize;

use crate::error::{Error, Result};
use crate::features::change::{is_test_path, line_set, src_files};
use crate::features::change_rows::{last_clean_run_ref_at_or_before, test_file_of};
use crate::features::dump::feature_rows_for_change;
use crate::features::model_vec::model_feature_vector;
use crate::features::python_scan::{DefSpan, ScanResult, scan_python};
use crate::features::schema::FEATURES_VERSION;
use crate::git::{DiffHunk, GitRepo, GixRepo, Sha};
use crate::lgbm::{Booster, predict_raw};
use crate::store::{Store, TestResultRow};

const BASE_RUN_LAG_WARN: i64 = 20;

/// Per-test reason precedence: fallback_all > new_or_changed > recent_fail > flaky > covered > ranked.
#[derive(Debug, Clone)]
pub struct SafeOpts {
    pub enabled: bool,
    pub recent_k: usize,
    pub include_covered: bool,
    pub src_frac_threshold: f64,
    pub min_candidates: usize,
    pub non_py_source: bool,
    pub non_py_allowlist: Vec<String>,
}

impl Default for SafeOpts {
    fn default() -> Self {
        Self {
            enabled: true,
            recent_k: 5,
            include_covered: false,
            src_frac_threshold: 0.30,
            min_candidates: 50,
            non_py_source: true,
            non_py_allowlist: Vec::new(),
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub enum Budget {
    Frac(f64),
    Count(u32),
    Time(f64),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SelectReason {
    FallbackAll,
    NewOrChanged,
    RecentFail,
    Flaky,
    Covered,
    Ranked,
}

impl SelectReason {
    fn as_str(&self) -> &'static str {
        match self {
            Self::FallbackAll => "fallback_all",
            Self::NewOrChanged => "new_or_changed",
            Self::RecentFail => "recent_fail",
            Self::Flaky => "flaky",
            Self::Covered => "covered",
            Self::Ranked => "ranked",
        }
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct SafeThresholdsJson {
    pub recent_k: usize,
    pub src_frac: f64,
    pub min_candidates: usize,
    pub include_covered: bool,
    pub non_py_source: bool,
}

#[derive(Debug, Clone, Serialize)]
pub struct SafeJson {
    pub enabled: bool,
    pub fallback_all: bool,
    pub reasons: Vec<String>,
    pub thresholds: SafeThresholdsJson,
}

#[derive(Debug, Clone, Serialize)]
pub struct BudgetJson {
    pub kind: String,
    pub value: f64,
}

#[derive(Debug, Clone, Serialize)]
pub struct SelectedTestJson {
    pub test_id: String,
    pub score: f64,
    pub rank: u32,
    pub dur: i64,
    pub reason: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct SelectJson {
    pub model_sha256: String,
    pub features_version: u32,
    pub base: String,
    pub head: String,
    pub base_run_sha: String,
    pub n_candidates: usize,
    pub n_selected: usize,
    pub n_forced: usize,
    pub n_safe_added: usize,
    pub n_ranked: usize,
    pub budget: BudgetJson,
    pub safe: SafeJson,
    pub tests: Vec<SelectedTestJson>,
}

fn safe_thresholds_json(opts: &SafeOpts) -> SafeThresholdsJson {
    SafeThresholdsJson {
        recent_k: opts.recent_k,
        src_frac: opts.src_frac_threshold,
        min_candidates: opts.min_candidates,
        include_covered: opts.include_covered,
        non_py_source: opts.non_py_source,
    }
}

fn safe_json_disabled(opts: &SafeOpts) -> SafeJson {
    SafeJson {
        enabled: false,
        fallback_all: false,
        reasons: vec![],
        thresholds: safe_thresholds_json(opts),
    }
}

#[derive(Debug, Clone)]
pub struct SelectOutput {
    pub payload: SelectJson,
    pub stderr_warnings: Vec<String>,
}

pub fn budget_json(b: &Budget) -> BudgetJson {
    match b {
        Budget::Frac(f) => BudgetJson {
            kind: "frac".into(),
            value: *f,
        },
        Budget::Count(n) => BudgetJson {
            kind: "count".into(),
            value: *n as f64,
        },
        Budget::Time(s) => BudgetJson {
            kind: "time".into(),
            value: *s,
        },
    }
}

pub fn format_lines(tests: &[SelectedTestJson]) -> String {
    tests
        .iter()
        .map(|t| t.test_id.as_str())
        .collect::<Vec<_>>()
        .join("\n")
}

pub fn format_table(tests: &[SelectedTestJson]) -> String {
    let mut lines = vec!["rank score dur reason test_id".to_string()];
    for t in tests {
        lines.push(format!(
            "{} {:.6} {} {} {}",
            t.rank, t.score, t.dur, t.reason, t.test_id
        ));
    }
    lines.join("\n")
}

#[allow(clippy::too_many_arguments)]
pub fn run_select<R: GitRepo>(
    repo: &R,
    gix: Option<&GixRepo>,
    store: &Store,
    repo_name: &str,
    booster: &Booster,
    base: &Sha,
    head: &Sha,
    head_label: &str,
    hunks: &[DiffHunk],
    worktree: bool,
    budget: &Budget,
    safe: &SafeOpts,
) -> Result<SelectOutput> {
    let mut stderr_warnings = Vec::new();
    if booster.meta.synthetic_only_warning {
        stderr_warnings.push("warning: model validated on synthetic only".to_string());
    }

    if hunks.is_empty() {
        stderr_warnings.push("warning: empty diff; selecting 0 tests".into());
        let payload = SelectJson {
            model_sha256: booster.meta.model_sha256.clone(),
            features_version: FEATURES_VERSION,
            base: base.as_str().to_string(),
            head: head_label.to_string(),
            base_run_sha: String::new(),
            n_candidates: 0,
            n_selected: 0,
            n_forced: 0,
            n_safe_added: 0,
            n_ranked: 0,
            budget: budget_json(budget),
            safe: if safe.enabled {
                SafeJson {
                    enabled: true,
                    fallback_all: false,
                    reasons: vec![],
                    thresholds: safe_thresholds_json(safe),
                }
            } else {
                safe_json_disabled(safe)
            },
            tests: vec![],
        };
        return Ok(SelectOutput {
            payload,
            stderr_warnings,
        });
    }

    let head_time = if worktree {
        repo.commit_meta_for(head)?.author_ts
    } else {
        store.commit_author_ts(head.as_str())?.unwrap_or(0)
    };

    let clean_ref = last_clean_run_ref_at_or_before(store, base.as_str())?;
    let base_run_sha = clean_ref
        .as_ref()
        .map(|r| r.sha.clone())
        .unwrap_or_default();
    if let Some(ref run_ref) = clean_ref {
        if let Some(dist) = first_parent_distance(store, &run_ref.sha, base.as_str())? {
            if dist > BASE_RUN_LAG_WARN {
                stderr_warnings.push(format!(
                    "warning: base_run_sha {} is {dist} first-parent commits behind base",
                    run_ref.sha
                ));
            }
        }
    }

    let base_ids: HashSet<String> = clean_ref
        .as_ref()
        .map(|r| {
            store
                .results_for_run(r.run_id)
                .map(|rows| rows.into_iter().map(|x| x.test_id).collect())
        })
        .transpose()?
        .unwrap_or_default();

    let mut base_results: Vec<TestResultRow> = clean_ref
        .as_ref()
        .map(|r| store.results_for_run(r.run_id))
        .transpose()?
        .unwrap_or_default();

    base_results = filter_present_on_head(repo, gix, base, head, worktree, hunks, base_results)?;

    let deleted_files: HashSet<String> = hunks
        .iter()
        .filter(|h| h.change_type == 'D')
        .map(|h| h.path.clone())
        .collect();

    let new_ids = discover_new_tests(repo, gix, head, worktree, hunks, &base_ids)?;
    let mut present_base: HashSet<String> =
        base_results.iter().map(|r| r.test_id.clone()).collect();
    for tid in &new_ids {
        if present_base.insert(tid.clone()) {
            base_results.push(synthetic_pass(tid));
        }
    }

    let forced = force_flags(
        repo,
        gix,
        head,
        worktree,
        hunks,
        &base_ids,
        &present_base,
        &deleted_files,
    )?;

    let rows = feature_rows_for_change(
        repo,
        store,
        repo_name,
        base,
        head,
        hunks,
        &base_results,
        head_time,
    )?;

    let covered_from_rows: HashSet<String> = rows
        .iter()
        .filter(|r| r.cov_lines > 0 || r.cov_func > 0)
        .map(|r| r.test.clone())
        .collect();

    let mut scored: Vec<ScoredCandidate> = Vec::with_capacity(rows.len());
    for row in rows {
        let feats = model_feature_vector(&row);
        let score = predict_raw(booster, &feats)?;
        scored.push(ScoredCandidate {
            test_id: row.test.clone(),
            score,
            dur: row.dur,
            forced: forced.get(&row.test).copied().unwrap_or(false),
        });
    }

    scored.sort_by(|a, b| {
        b.score
            .partial_cmp(&a.score)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| a.test_id.cmp(&b.test_id))
    });

    let n_candidates = scored.len();
    let forced_ids: HashSet<String> = scored
        .iter()
        .filter(|s| s.forced)
        .map(|s| s.test_id.clone())
        .collect();
    let n_forced = forced_ids.len();

    let fallback_reasons = if safe.enabled {
        fallback_all_reasons(repo, head, hunks, n_candidates, safe)?
    } else {
        vec![]
    };
    let fallback_all = !fallback_reasons.is_empty();

    let safe_json = if safe.enabled {
        SafeJson {
            enabled: true,
            fallback_all,
            reasons: fallback_reasons.clone(),
            thresholds: safe_thresholds_json(safe),
        }
    } else {
        safe_json_disabled(safe)
    };

    let covered_ids: HashSet<String> = if safe.enabled && safe.include_covered && !fallback_all {
        covered_from_rows
    } else {
        HashSet::new()
    };

    let (recent_fail_ids, flaky_ids) = if safe.enabled && !fallback_all {
        recent_fail_and_flaky(store, base.as_str(), safe.recent_k)?
    } else {
        (HashSet::new(), HashSet::new())
    };

    let candidate_ids: HashSet<String> = scored.iter().map(|s| s.test_id.clone()).collect();
    let recent_fail_ids: HashSet<String> = recent_fail_ids
        .into_iter()
        .filter(|id| candidate_ids.contains(id))
        .collect();
    let flaky_ids: HashSet<String> = flaky_ids
        .into_iter()
        .filter(|id| candidate_ids.contains(id))
        .collect();

    let mut safe_reasons: HashMap<String, SelectReason> = HashMap::new();
    if !fallback_all && safe.enabled {
        for id in &recent_fail_ids {
            if !forced_ids.contains(id) {
                safe_reasons.insert(id.clone(), SelectReason::RecentFail);
            }
        }
        for id in &flaky_ids {
            if !forced_ids.contains(id) {
                safe_reasons
                    .entry(id.clone())
                    .or_insert(SelectReason::Flaky);
            }
        }
        for id in &covered_ids {
            if !forced_ids.contains(id) && !safe_reasons.contains_key(id) {
                safe_reasons.insert(id.clone(), SelectReason::Covered);
            }
        }
    }

    let n_safe_added = if fallback_all || !safe.enabled {
        0
    } else {
        safe_reasons.len()
    };

    let selected = if fallback_all {
        scored.clone()
    } else {
        apply_budget(
            &scored,
            budget,
            &forced_ids,
            &safe_reasons.keys().cloned().collect::<HashSet<_>>(),
        )
    };

    let tests_json: Vec<SelectedTestJson> = selected
        .iter()
        .enumerate()
        .map(|(i, t)| {
            let reason = if fallback_all {
                SelectReason::FallbackAll
            } else if forced_ids.contains(&t.test_id) {
                SelectReason::NewOrChanged
            } else if let Some(r) = safe_reasons.get(&t.test_id) {
                r.clone()
            } else {
                SelectReason::Ranked
            };
            SelectedTestJson {
                test_id: t.test_id.clone(),
                score: t.score,
                rank: (i + 1) as u32,
                dur: t.dur,
                reason: reason.as_str().to_string(),
            }
        })
        .collect();

    let n_ranked = if fallback_all {
        0
    } else {
        tests_json
            .iter()
            .filter(|t| t.reason == SelectReason::Ranked.as_str())
            .count()
    };

    let payload = SelectJson {
        model_sha256: booster.meta.model_sha256.clone(),
        features_version: FEATURES_VERSION,
        base: base.as_str().to_string(),
        head: head_label.to_string(),
        base_run_sha,
        n_candidates,
        n_selected: tests_json.len(),
        n_forced,
        n_safe_added,
        n_ranked,
        budget: budget_json(budget),
        safe: safe_json,
        tests: tests_json,
    };

    Ok(SelectOutput {
        payload,
        stderr_warnings,
    })
}

#[derive(Debug, Clone)]
struct ScoredCandidate {
    test_id: String,
    score: f64,
    dur: i64,
    forced: bool,
}

fn synthetic_pass(test_id: &str) -> TestResultRow {
    TestResultRow {
        test_id: test_id.to_string(),
        outcome: "pass".into(),
        duration_ms: -1,
        message: None,
        flaky: false,
        attempts: 1,
    }
}

fn first_parent_distance(store: &Store, from_sha: &str, to_sha: &str) -> Result<Option<i64>> {
    let mut cur = to_sha.to_string();
    for d in 0..=100_000 {
        if cur == from_sha {
            return Ok(Some(d));
        }
        cur = match store.first_parent(&cur)? {
            Some(p) => p,
            None => return Ok(None),
        };
    }
    Ok(None)
}

fn read_source<R: GitRepo>(
    repo: &R,
    gix: Option<&GixRepo>,
    head: &Sha,
    worktree: bool,
    path: &str,
) -> Result<Option<String>> {
    let bytes = if worktree {
        let Some(g) = gix else {
            return Err(Error::Other(
                "worktree mode requires an on-disk git repository".into(),
            ));
        };
        g.file_bytes_at_view(head, true, path)?
    } else {
        repo.file_at_rev(head, path)?.map(|b| b.to_vec())
    };
    Ok(bytes.map(|b| String::from_utf8_lossy(&b).into_owned()))
}

/// Strip a trailing pytest parametrization suffix `[…]` from a nodeid.
///
/// Find the first `[` in the string, then take the last `::` in the prefix
/// *before* that bracket (so `[::]` inside params cannot move the split point).
/// Strip at the first `[` after that `::`.
pub fn strip_pytest_params(test_id: &str) -> &str {
    let Some(first_bracket) = test_id.find('[') else {
        return test_id;
    };
    if !test_id[first_bracket..].contains(']') {
        return test_id;
    }
    let before = &test_id[..first_bracket];
    let after_sep = before.rfind("::").map(|i| i + 2).unwrap_or(0);
    if let Some(rel) = test_id[after_sep..].find('[') {
        let i = after_sep + rel;
        if test_id[i..].contains(']') {
            return &test_id[..i];
        }
    }
    test_id
}

/// Family key for matching bare defs to parametrized children: strip `[…]`.
fn family_key(test_id: &str) -> &str {
    strip_pytest_params(test_id)
}

/// True if `base_ids` already covers this id (exact or any `[…]` sibling / bare parent).
fn base_covers_family(base_ids: &HashSet<String>, tid: &str) -> bool {
    let key = family_key(tid);
    base_ids.iter().any(|b| family_key(b) == key)
}

fn file_touched_in_hunks(hunks: &[DiffHunk], path: &str) -> bool {
    hunks.iter().any(|h| h.path == path && h.change_type != 'A')
}

fn filter_present_on_head<R: GitRepo>(
    repo: &R,
    gix: Option<&GixRepo>,
    base: &Sha,
    head: &Sha,
    worktree: bool,
    hunks: &[DiffHunk],
    mut results: Vec<TestResultRow>,
) -> Result<Vec<TestResultRow>> {
    let deleted: HashSet<String> = hunks
        .iter()
        .filter(|h| h.change_type == 'D')
        .map(|h| h.path.clone())
        .collect();
    let mut head_cache: HashMap<String, Option<ScanResult>> = HashMap::new();
    let mut base_cache: HashMap<String, Option<ScanResult>> = HashMap::new();
    results.retain(|r| {
        let tf = test_file_of(&r.test_id);
        if deleted.contains(&tf) {
            return false;
        }
        let head_scan = head_cache.entry(tf.clone()).or_insert_with(|| {
            read_source(repo, gix, head, worktree, &tf)
                .ok()
                .flatten()
                .map(|s| scan_python(&s))
        });
        // File gone on head (not listed as 'D' but missing) → drop.
        let Some(head_scan) = head_scan.as_ref() else {
            return false;
        };
        if qualname_exists(&r.test_id, &head_scan.defs) {
            return true;
        }
        // Scan miss while the file exists: keep unless we can prove the def was
        // removed (present on base scan, absent on head, file touched in hunks).
        if !file_touched_in_hunks(hunks, &tf) {
            return true;
        }
        let base_scan = base_cache.entry(tf.clone()).or_insert_with(|| {
            // Base is always a commit rev (never worktree overlay).
            read_source(repo, gix, base, false, &tf)
                .ok()
                .flatten()
                .map(|s| scan_python(&s))
        });
        let Some(base_scan) = base_scan.as_ref() else {
            return true;
        };
        if qualname_exists(&r.test_id, &base_scan.defs) {
            // Was on base, gone on head, file changed → confident removal.
            return false;
        }
        // Neither side sees the def (scanner gap / exotic nodeid) → keep.
        true
    });
    Ok(results)
}

fn discover_new_tests<R: GitRepo>(
    repo: &R,
    gix: Option<&GixRepo>,
    head: &Sha,
    worktree: bool,
    hunks: &[DiffHunk],
    base_ids: &HashSet<String>,
) -> Result<Vec<String>> {
    let mut seen_paths: HashSet<String> = HashSet::new();
    let mut out = Vec::new();
    for h in hunks {
        if !h.path.ends_with(".py") || !is_test_path(&h.path) || h.change_type == 'D' {
            continue;
        }
        if !seen_paths.insert(h.path.clone()) {
            continue;
        }
        let Some(src) = read_source(repo, gix, head, worktree, &h.path)? else {
            continue;
        };
        for tid in test_ids_from_source(&h.path, &src) {
            // Bare id from scan; not "new" if base already has this def or any `[…]` child.
            if !base_covers_family(base_ids, &tid) {
                out.push(tid);
            }
        }
    }
    out.sort();
    out.dedup();
    Ok(out)
}

fn test_ids_from_source(path: &str, src: &str) -> Vec<String> {
    let scan = scan_python(src);
    let mut out = Vec::new();
    for d in &scan.defs {
        if d.kind == "class" {
            continue;
        }
        let leaf = d.qualname.rsplit('.').next().unwrap_or(&d.qualname);
        if !leaf.starts_with("test_") {
            continue;
        }
        // Emit bare pytest-style nodeid (Class.method → Class::method). No param expansion.
        let suffix = d.qualname.replace('.', "::");
        out.push(format!("{path}::{suffix}"));
    }
    out
}

/// Map a pytest nodeid to `(path, scan qualname)`. Strips `[…]`; `Class::meth` ↔ `Class.meth`.
fn qualname_from_test_id(test_id: &str) -> Option<(String, String)> {
    let stripped = strip_pytest_params(test_id);
    let (path, rest) = stripped.split_once("::")?;
    let qualname = rest.replace("::", ".");
    Some((path.to_string(), qualname))
}

fn qualname_exists(test_id: &str, defs: &[DefSpan]) -> bool {
    let Some((_path, qual)) = qualname_from_test_id(test_id) else {
        return false;
    };
    defs.iter().any(|d| d.kind != "class" && d.qualname == qual)
}

/// Head/new-side changed lines for `path` (added or modified on head).
fn new_changed_lines_for_path(hunks: &[DiffHunk], path: &str) -> HashSet<u32> {
    let mut lines = HashSet::new();
    for h in hunks.iter().filter(|h| h.path == path) {
        lines.extend(line_set(&h.new_line_ranges));
    }
    lines
}

/// Base/old-side deleted or modified lines for `path`.
/// Used when attributing removals to base defs (not for forcing head neighbors).
#[allow(dead_code)] // exercised in unit tests; keep as the old-side counterpart of `new_*`
fn old_changed_lines_for_path(hunks: &[DiffHunk], path: &str) -> HashSet<u32> {
    let mut lines = HashSet::new();
    for h in hunks.iter().filter(|h| h.path == path) {
        lines.extend(line_set(&h.old_line_ranges));
    }
    lines
}

fn def_touches_lines(def: &DefSpan, changed: &HashSet<u32>) -> bool {
    if def.kind == "class" {
        return false;
    }
    (def.start_line as u32..def.end_line as u32).any(|ln| changed.contains(&ln))
}

#[allow(clippy::too_many_arguments)]
fn force_flags<R: GitRepo>(
    repo: &R,
    gix: Option<&GixRepo>,
    head: &Sha,
    worktree: bool,
    hunks: &[DiffHunk],
    base_ids: &HashSet<String>,
    candidates: &HashSet<String>,
    deleted_files: &HashSet<String>,
) -> Result<HashMap<String, bool>> {
    let mut head_cache: HashMap<String, Option<ScanResult>> = HashMap::new();
    let mut out = HashMap::new();
    for tid in candidates {
        let tf = test_file_of(tid);
        if deleted_files.contains(&tf) {
            continue;
        }
        if !base_covers_family(base_ids, tid) {
            out.insert(tid.clone(), true);
            continue;
        }
        let Some((_p, qual)) = qualname_from_test_id(tid) else {
            continue;
        };

        let head_scan = head_cache.entry(tf.clone()).or_insert_with(|| {
            read_source(repo, gix, head, worktree, &tf)
                .ok()
                .flatten()
                .map(|s| scan_python(&s))
        });
        let Some(scan) = head_scan.as_ref() else {
            continue;
        };
        let Some(def) = scan
            .defs
            .iter()
            .find(|d| d.kind != "class" && d.qualname == qual)
        else {
            // Absent on head: not force-included. Removals are attributed via
            // old-side lines in filter_present / tests — never mix old-side
            // line numbers into a neighboring head def's span.
            continue;
        };
        // Existing def: intersect with head/new-side lines only.
        let changed = new_changed_lines_for_path(hunks, &tf);
        if def_touches_lines(def, &changed) {
            out.insert(tid.clone(), true);
        }
    }
    Ok(out)
}

fn dur_seconds(dur_ms: i64, median_ms: f64) -> f64 {
    if dur_ms >= 0 {
        dur_ms as f64 / 1000.0
    } else if median_ms >= 0.0 {
        median_ms / 1000.0
    } else {
        1.0
    }
}

fn median_known_dur_ms(scored: &[ScoredCandidate]) -> f64 {
    let mut known: Vec<i64> = scored
        .iter()
        .filter(|s| s.dur >= 0)
        .map(|s| s.dur)
        .collect();
    if known.is_empty() {
        return -1.0;
    }
    known.sort();
    let mid = known.len() / 2;
    if known.len() % 2 == 0 {
        (known[mid - 1] + known[mid]) as f64 / 2.0
    } else {
        known[mid] as f64
    }
}

fn target_count(n_candidates: usize, budget: &Budget) -> Option<usize> {
    match budget {
        Budget::Frac(f) => Some((*f * n_candidates as f64).ceil().max(1.0) as usize),
        Budget::Count(n) => Some(*n as usize),
        Budget::Time(_) => None,
    }
}

fn ancestry_shas_at_or_before(store: &Store, base: &str) -> Result<Vec<String>> {
    let mut out = Vec::new();
    let mut cur = base.to_string();
    loop {
        out.push(cur.clone());
        cur = match store.first_parent(&cur)? {
            Some(p) => p,
            None => break,
        };
    }
    Ok(out)
}

fn recent_fail_and_flaky(
    store: &Store,
    base: &str,
    k: usize,
) -> Result<(HashSet<String>, HashSet<String>)> {
    let ancestry = ancestry_shas_at_or_before(store, base)?;
    let run_ids = store.recent_finished_runs_on_shas(&ancestry, k)?;
    let mut recent_fail = HashSet::new();
    let mut flaky = HashSet::new();
    for run_id in run_ids {
        let results = store.results_for_run(run_id)?;
        for r in results {
            if matches!(r.outcome.as_str(), "fail" | "error") {
                recent_fail.insert(r.test_id.clone());
            }
            if r.flaky {
                flaky.insert(r.test_id);
            }
        }
    }
    Ok((recent_fail, flaky))
}

fn normalize_path(path: &str) -> String {
    path.replace('\\', "/")
}

fn glob_matches(path: &str, pattern: &str) -> bool {
    glob::Pattern::new(pattern)
        .ok()
        .is_some_and(|p| p.matches(path))
}

fn path_on_docs_allowlist(path: &str, extra: &[String]) -> bool {
    let p = normalize_path(path);
    const DEFAULTS: &[&str] = &["*.md", "*.rst", "docs/**", "CHANGES*", "LICENSE*"];
    if DEFAULTS.iter().any(|pat| glob_matches(&p, pat)) {
        return true;
    }
    extra.iter().any(|pat| glob_matches(&p, pat))
}

fn path_matches_config_surface(path: &str) -> bool {
    let p = normalize_path(path);
    const PATTERNS: &[&str] = &[
        "**/conftest.py",
        "pytest.ini",
        "pyproject.toml",
        "setup.cfg",
        "requirements*.txt",
        "requirements*.in",
        "poetry.lock",
        "uv.lock",
        "Pipfile.lock",
        "pdm.lock",
        ".github/workflows/**",
    ];
    PATTERNS.iter().any(|pat| glob_matches(&p, pat))
}

/// True if `path` sits inside a Python source package tree at `head`.
///
/// Root-level files (`.pre-commit-config.yaml`, lockfiles, …) are not packages.
/// Paths under `src/` or under a top-level dir that contains non-test `.py` files
/// (e.g. `pkg/templates/x.html` next to `pkg/a.py`) count.
/// Top-level dirs that are never treated as installable source packages
/// (examples, docs benches, demos — even if they contain `.py`).
const NON_SOURCE_TOP_LEVEL: &[&str] = &[
    "docs",
    "tests",
    ".github",
    "examples",
    "example",
    "docs_src",
    "benchmarks",
    "benchmark",
    "tutorial",
    "tutorials",
    "demo",
    "demos",
    "samples",
    "sample",
];

fn is_inside_source_package(path: &str, all_paths: &[String]) -> bool {
    let p = normalize_path(path);
    if !p.contains('/') {
        return false;
    }
    if p.starts_with("src/") {
        return true;
    }
    let top = p.split('/').next().unwrap_or("");
    if top.is_empty() || top.starts_with('.') || NON_SOURCE_TOP_LEVEL.contains(&top) {
        return false;
    }
    let prefix = format!("{top}/");
    all_paths.iter().any(|ap| {
        let a = normalize_path(ap);
        a.starts_with(&prefix) && a.ends_with(".py") && !is_test_path(&a)
    })
}

fn fallback_all_reasons<R: GitRepo>(
    repo: &R,
    head: &Sha,
    hunks: &[DiffHunk],
    n_candidates: usize,
    safe: &SafeOpts,
) -> Result<Vec<String>> {
    let mut reasons = Vec::new();
    if hunks
        .iter()
        .any(|h| h.change_type != 'D' && path_matches_config_surface(&h.path))
    {
        reasons.push("config_surface".into());
    }
    if n_candidates < safe.min_candidates {
        reasons.push("few_candidates".into());
    }
    let changed_src = src_files(hunks);
    let all_paths = repo.list_paths_at_rev(head)?;
    let n_all_src_py = all_paths
        .iter()
        .filter(|p| p.ends_with(".py") && !is_test_path(p))
        .count();
    if n_all_src_py > 0 {
        let frac = changed_src.len() as f64 / n_all_src_py as f64;
        if frac > safe.src_frac_threshold {
            reasons.push("src_frac".into());
        }
    }
    if safe.non_py_source {
        let non_py_hit = hunks.iter().any(|h| {
            if h.change_type == 'D' {
                return false;
            }
            let p = normalize_path(&h.path);
            // Do not double-count config_surface paths as non_py_source.
            if path_matches_config_surface(&p) {
                return false;
            }
            !is_test_path(&p)
                && !p.ends_with(".py")
                && !path_on_docs_allowlist(&p, &safe.non_py_allowlist)
                && is_inside_source_package(&p, &all_paths)
        });
        if non_py_hit {
            reasons.push("non_py_source".into());
        }
    }
    Ok(reasons)
}

fn apply_budget(
    scored: &[ScoredCandidate],
    budget: &Budget,
    forced_set: &HashSet<String>,
    safe_set: &HashSet<String>,
) -> Vec<ScoredCandidate> {
    if scored.is_empty() {
        return vec![];
    }
    let median = median_known_dur_ms(scored);
    let priority: HashSet<String> = forced_set.iter().chain(safe_set.iter()).cloned().collect();

    let mut selected: Vec<ScoredCandidate> = scored
        .iter()
        .filter(|s| priority.contains(&s.test_id))
        .cloned()
        .collect();
    selected.sort_by(|a, b| {
        b.score
            .partial_cmp(&a.score)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| a.test_id.cmp(&b.test_id))
    });

    match budget {
        Budget::Time(seconds) => {
            let mut used: f64 = 0.0;
            for cand in scored {
                if priority.contains(&cand.test_id) {
                    continue;
                }
                let d = dur_seconds(cand.dur, median);
                if selected.is_empty() || used + d <= *seconds {
                    used += d;
                    selected.push(cand.clone());
                }
            }
        }
        Budget::Frac(_) | Budget::Count(_) => {
            let cap = target_count(scored.len(), budget).unwrap_or(scored.len());
            for cand in scored {
                if priority.contains(&cand.test_id) {
                    continue;
                }
                if selected.len() >= cap {
                    break;
                }
                selected.push(cand.clone());
            }
        }
    }

    selected.sort_by(|a, b| {
        b.score
            .partial_cmp(&a.score)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| a.test_id.cmp(&b.test_id))
    });
    selected
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cand(id: &str, score: f64, dur: i64, forced: bool) -> ScoredCandidate {
        ScoredCandidate {
            test_id: id.into(),
            score,
            dur,
            forced,
        }
    }

    #[test]
    fn budget_frac_one_selects_all() {
        let scored = vec![cand("a", 1.0, 10, false), cand("b", 0.5, 10, false)];
        let sel = apply_budget(
            &scored,
            &Budget::Frac(1.0),
            &HashSet::new(),
            &HashSet::new(),
        );
        assert_eq!(sel.len(), 2);
    }

    #[test]
    fn time_budget_always_picks_at_least_one() {
        let scored = vec![cand("a", 1.0, 5000, false)];
        let sel = apply_budget(
            &scored,
            &Budget::Time(0.001),
            &HashSet::new(),
            &HashSet::new(),
        );
        assert_eq!(sel.len(), 1);
    }

    #[test]
    fn forced_exceeds_count_cap() {
        let scored = vec![
            cand("f1", 2.0, 1, true),
            cand("f2", 1.5, 1, true),
            cand("r1", 1.0, 1, false),
        ];
        let forced: HashSet<String> = ["f1", "f2"].into_iter().map(String::from).collect();
        let sel = apply_budget(&scored, &Budget::Count(1), &forced, &HashSet::new());
        assert_eq!(sel.len(), 2);
        assert!(sel.iter().all(|s| s.test_id.starts_with('f')));
    }

    #[test]
    fn time_budget_does_not_charge_forced_or_safe() {
        let scored = vec![
            cand("f1", 2.0, 5000, true),
            cand("s1", 1.5, 5000, false),
            cand("r1", 1.0, 5000, false),
        ];
        let forced: HashSet<String> = ["f1"].into_iter().map(String::from).collect();
        let safe: HashSet<String> = ["s1"].into_iter().map(String::from).collect();
        let sel = apply_budget(&scored, &Budget::Time(0.001), &forced, &safe);
        assert!(sel.iter().any(|s| s.test_id == "f1"));
        assert!(sel.iter().any(|s| s.test_id == "s1"));
        assert_eq!(sel.len(), 2);
    }

    #[test]
    fn config_surface_and_docs_allowlist_globs() {
        assert!(path_matches_config_surface("pyproject.toml"));
        assert!(path_matches_config_surface(".github/workflows/ci.yml"));
        assert!(!path_matches_config_surface("Cargo.lock"));
        assert!(path_on_docs_allowlist("docs/guide.md", &[]));
        assert!(path_on_docs_allowlist("LICENSE", &[]));
        assert!(!path_on_docs_allowlist("src/pkg/templates/x.html", &[]));
    }

    #[test]
    fn dur_sentinel_uses_median() {
        let scored = vec![
            cand("a", 1.0, 1000, false),
            cand("b", 0.9, 3000, false),
            cand("c", 0.8, -1, false),
        ];
        assert!((dur_seconds(-1, median_known_dur_ms(&scored)) - 2.0).abs() < 1e-9);
    }

    #[test]
    fn test_ids_from_pytest_defs() {
        let src =
            "class TestFoo:\n    def test_bar(self):\n        pass\n\ndef test_top():\n    pass\n";
        let ids = test_ids_from_source("tests/t.py", src);
        assert!(ids.contains(&"tests/t.py::TestFoo::test_bar".to_string()));
        assert!(ids.contains(&"tests/t.py::test_top".to_string()));
    }

    #[test]
    fn strip_pytest_params_and_class_qual() {
        assert_eq!(
            strip_pytest_params("tests/t.py::test_foo[a-b]"),
            "tests/t.py::test_foo"
        );
        assert_eq!(
            strip_pytest_params("tests/t.py::TestCls::test_m[1]"),
            "tests/t.py::TestCls::test_m"
        );
        assert_eq!(
            strip_pytest_params("tests/t.py::test_plain"),
            "tests/t.py::test_plain"
        );
        // `[::]` inside params must not move the `::` split point.
        assert_eq!(strip_pytest_params("t.py::test_x[a::b]"), "t.py::test_x");
        assert_eq!(
            strip_pytest_params("tests/t.py::TestCls::test_m[x::y]"),
            "tests/t.py::TestCls::test_m"
        );
        let (path, qual) = qualname_from_test_id("tests/t.py::TestCls::test_m[x]").unwrap();
        assert_eq!(path, "tests/t.py");
        assert_eq!(qual, "TestCls.test_m");
        let mut base = HashSet::new();
        base.insert("tests/t.py::test_foo[1]".into());
        base.insert("tests/t.py::test_foo[2]".into());
        assert!(base_covers_family(&base, "tests/t.py::test_foo"));
        assert!(base_covers_family(&base, "tests/t.py::test_foo[9]"));
        assert!(!base_covers_family(&base, "tests/t.py::test_other"));
    }

    #[test]
    fn hunk_sides_old_lines_do_not_touch_head_neighbor() {
        // Pure deletion: old lines 10–20, no new lines. Head neighbor spans 10–15
        // after the shift — must not count as touched (new-side empty).
        let hunks = vec![DiffHunk {
            path: "tests/t.py".into(),
            old_path: None,
            change_type: 'M',
            old_line_ranges: vec![crate::git::LineRange { start: 10, end: 20 }],
            new_line_ranges: vec![],
            is_binary: false,
        }];
        assert!(new_changed_lines_for_path(&hunks, "tests/t.py").is_empty());
        let old = old_changed_lines_for_path(&hunks, "tests/t.py");
        assert!(old.contains(&10) && old.contains(&20));
        let neighbor = DefSpan {
            qualname: "test_neighbor".into(),
            kind: "def".into(),
            start_line: 10,
            end_line: 16,
        };
        assert!(!def_touches_lines(
            &neighbor,
            &new_changed_lines_for_path(&hunks, "tests/t.py")
        ));
        // Old-side attribution is for the deleted def only:
        let deleted = DefSpan {
            qualname: "test_deleted".into(),
            kind: "def".into(),
            start_line: 10,
            end_line: 21,
        };
        assert!(def_touches_lines(&deleted, &old));
    }
}
