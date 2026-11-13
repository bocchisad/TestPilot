//! Budgeted test selection: candidates, force-include, LightGBM raw scores.

use std::collections::{HashMap, HashSet};

use serde::Serialize;

use crate::error::{Error, Result};
use crate::features::change::{is_test_path, line_set};
use crate::features::change_rows::{last_clean_run_ref_at_or_before, test_file_of};
use crate::features::dump::feature_rows_for_change;
use crate::features::model_vec::model_feature_vector;
use crate::features::python_scan::{DefSpan, ScanResult, scan_python};
use crate::features::schema::FEATURES_VERSION;
use crate::git::{DiffHunk, GitRepo, GixRepo, Sha};
use crate::lgbm::{Booster, predict_raw};
use crate::store::{Store, TestResultRow};

const BASE_RUN_LAG_WARN: i64 = 20;

#[derive(Debug, Clone, PartialEq)]
pub enum Budget {
    Frac(f64),
    Count(u32),
    Time(f64),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SelectReason {
    NewOrChanged,
    Ranked,
}

impl SelectReason {
    fn as_str(&self) -> &'static str {
        match self {
            Self::NewOrChanged => "new_or_changed",
            Self::Ranked => "ranked",
        }
    }
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
    pub budget: BudgetJson,
    pub tests: Vec<SelectedTestJson>,
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
            budget: budget_json(budget),
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

    base_results = filter_present_on_head(repo, gix, head, worktree, hunks, base_results)?;

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

    let selected = apply_budget(&scored, budget);
    let n_forced = selected.iter().filter(|t| t.forced).count();
    let tests_json: Vec<SelectedTestJson> = selected
        .iter()
        .enumerate()
        .map(|(i, t)| SelectedTestJson {
            test_id: t.test_id.clone(),
            score: t.score,
            rank: (i + 1) as u32,
            dur: t.dur,
            reason: if t.forced {
                SelectReason::NewOrChanged.as_str()
            } else {
                SelectReason::Ranked.as_str()
            }
            .to_string(),
        })
        .collect();

    let payload = SelectJson {
        model_sha256: booster.meta.model_sha256.clone(),
        features_version: FEATURES_VERSION,
        base: base.as_str().to_string(),
        head: head_label.to_string(),
        base_run_sha,
        n_candidates: scored.len(),
        n_selected: tests_json.len(),
        n_forced,
        budget: budget_json(budget),
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

fn filter_present_on_head<R: GitRepo>(
    repo: &R,
    gix: Option<&GixRepo>,
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
    let mut cache: HashMap<String, Option<ScanResult>> = HashMap::new();
    results.retain(|r| {
        let tf = test_file_of(&r.test_id);
        if deleted.contains(&tf) {
            return false;
        }
        let scan = cache.entry(tf.clone()).or_insert_with(|| {
            read_source(repo, gix, head, worktree, &tf)
                .ok()
                .flatten()
                .map(|s| scan_python(&s))
        });
        let Some(scan) = scan else {
            return false;
        };
        qualname_exists(&r.test_id, &scan.defs)
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
            if !base_ids.contains(&tid) {
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
        let suffix = d.qualname.replace('.', "::");
        out.push(format!("{path}::{suffix}"));
    }
    out
}

fn qualname_from_test_id(test_id: &str) -> Option<(String, String)> {
    let (path, rest) = test_id.split_once("::")?;
    let qualname = rest.replace("::", ".");
    Some((path.to_string(), qualname))
}

fn qualname_exists(test_id: &str, defs: &[DefSpan]) -> bool {
    let Some((_path, qual)) = qualname_from_test_id(test_id) else {
        return false;
    };
    defs.iter().any(|d| d.kind != "class" && d.qualname == qual)
}

fn changed_lines_for_path(hunks: &[DiffHunk], path: &str) -> HashSet<u32> {
    let mut lines = HashSet::new();
    for h in hunks.iter().filter(|h| h.path == path) {
        lines.extend(line_set(&h.new_line_ranges));
        lines.extend(line_set(&h.old_line_ranges));
    }
    lines
}

fn def_touches_hunk(def: &DefSpan, changed: &HashSet<u32>) -> bool {
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
    let mut scan_cache: HashMap<String, Option<ScanResult>> = HashMap::new();
    let mut out = HashMap::new();
    for tid in candidates {
        let tf = test_file_of(tid);
        if deleted_files.contains(&tf) {
            continue;
        }
        if !base_ids.contains(tid) {
            out.insert(tid.clone(), true);
            continue;
        }
        let scan = scan_cache.entry(tf.clone()).or_insert_with(|| {
            read_source(repo, gix, head, worktree, &tf)
                .ok()
                .flatten()
                .map(|s| scan_python(&s))
        });
        let Some(scan) = scan else {
            continue;
        };
        let Some((_p, qual)) = qualname_from_test_id(tid) else {
            continue;
        };
        let Some(def) = scan.defs.iter().find(|d| d.qualname == qual) else {
            continue;
        };
        let changed = changed_lines_for_path(hunks, &tf);
        if def_touches_hunk(def, &changed) {
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

fn apply_budget(scored: &[ScoredCandidate], budget: &Budget) -> Vec<ScoredCandidate> {
    if scored.is_empty() {
        return vec![];
    }
    let median = median_known_dur_ms(scored);
    let mut forced: Vec<ScoredCandidate> = scored.iter().filter(|s| s.forced).cloned().collect();
    forced.sort_by(|a, b| {
        b.score
            .partial_cmp(&a.score)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| a.test_id.cmp(&b.test_id))
    });
    let forced_set: HashSet<String> = forced.iter().map(|s| s.test_id.clone()).collect();

    let mut selected = forced.clone();

    match budget {
        Budget::Time(seconds) => {
            let mut used: f64 = forced.iter().map(|s| dur_seconds(s.dur, median)).sum();
            for cand in scored {
                if forced_set.contains(&cand.test_id) {
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
                if forced_set.contains(&cand.test_id) {
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
        let sel = apply_budget(&scored, &Budget::Frac(1.0));
        assert_eq!(sel.len(), 2);
    }

    #[test]
    fn time_budget_always_picks_at_least_one() {
        let scored = vec![cand("a", 1.0, 5000, false)];
        let sel = apply_budget(&scored, &Budget::Time(0.001));
        assert_eq!(sel.len(), 1);
    }

    #[test]
    fn forced_exceeds_count_cap() {
        let scored = vec![
            cand("f1", 2.0, 1, true),
            cand("f2", 1.5, 1, true),
            cand("r1", 1.0, 1, false),
        ];
        let sel = apply_budget(&scored, &Budget::Count(1));
        assert_eq!(sel.len(), 2);
        assert!(sel.iter().all(|s| s.test_id.starts_with('f')));
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
}
