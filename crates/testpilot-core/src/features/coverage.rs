//! Coverage features from the last clean coverage run on parent lineage.

use std::collections::{HashMap, HashSet};

use crate::codec;
use crate::features::change::{line_set, line_to_def};
use crate::features::python_scan::scan_python;
use crate::git::DiffHunk;

#[derive(Debug, Clone)]
pub struct CovSnapshot {
    /// commit where coverage was recorded
    pub sha: String,
    /// how many processed commits behind base (0 = on base)
    pub lag: i64,
    /// test_id → path → covered line numbers (1-based)
    pub by_test: HashMap<String, HashMap<String, HashSet<u32>>>,
}

impl CovSnapshot {
    pub fn empty() -> Self {
        Self {
            sha: String::new(),
            lag: 0,
            by_test: HashMap::new(),
        }
    }

    pub fn features_for(
        &self,
        test_id: &str,
        srcf: &[String],
        hunks: &[DiffHunk],
        old_sources: &HashMap<String, String>,
        imp_dist: i64,
    ) -> CovFeatures {
        let Some(paths) = self.by_test.get(test_id) else {
            return CovFeatures::unknown(self.lag);
        };
        let mut cov_file = 0i64;
        let mut cov_lines = 0i64;
        let mut cov_func = 0i64;
        let mut cov_breadth = 0i64;
        let mut module_region_hit = false;

        for lines in paths.values() {
            cov_breadth += lines.len() as i64;
        }

        for f in srcf {
            let covered = paths.get(f).cloned().unwrap_or_default();
            if covered.is_empty() {
                continue;
            }
            // cov_file: test covers this src file at all
            cov_file = 1;

            let hunk = hunks.iter().find(|h| h.path == *f);
            let old_changed = hunk
                .map(|h| line_set(&h.old_line_ranges))
                .unwrap_or_default();
            let inter: HashSet<_> = covered.intersection(&old_changed).cloned().collect();
            cov_lines += inter.len() as i64;

            if let Some(src) = old_sources.get(f) {
                let scan = scan_python(src);
                let l2d = line_to_def(&scan.defs);
                let mut funcs: HashSet<String> = HashSet::new();
                for ln in &inter {
                    if let Some(q) = l2d.get(ln) {
                        funcs.insert(q.clone());
                    } else {
                        module_region_hit = true;
                    }
                }
                for ln in &old_changed {
                    if !covered.contains(ln) && !l2d.contains_key(ln) {
                        module_region_hit = true;
                    }
                }
                cov_func += funcs.len() as i64;
            }
        }

        let cov_mod = if module_region_hit && imp_dist < 99 {
            1
        } else {
            0
        };

        CovFeatures {
            cov_known: 1,
            cov_file,
            cov_func,
            cov_lines,
            cov_breadth,
            cov_mod,
            cov_lag: self.lag,
        }
    }
}

#[derive(Debug, Clone, Copy)]
pub struct CovFeatures {
    pub cov_known: i64,
    pub cov_file: i64,
    pub cov_func: i64,
    pub cov_lines: i64,
    pub cov_breadth: i64,
    pub cov_mod: i64,
    pub cov_lag: i64,
}

impl CovFeatures {
    pub fn unknown(lag: i64) -> Self {
        Self {
            cov_known: 0,
            cov_file: 0,
            cov_func: 0,
            cov_lines: 0,
            cov_breadth: 0,
            cov_mod: 0,
            cov_lag: lag,
        }
    }
}

/// Decode a coverage blob into 1-based line numbers.
pub fn decode_lines(blob: &[u8]) -> HashSet<u32> {
    codec::decode_lines(blob)
        .unwrap_or_default()
        .into_iter()
        .collect()
}

/// Tracker: as we process commits topo-order, remember last coverage snapshot sha + lag base.
#[derive(Debug, Default)]
pub struct CoverageTracker {
    /// sha → (run coverage by_test)
    snapshots: HashMap<String, HashMap<String, HashMap<String, HashSet<u32>>>>,
    /// ordered processed shas
    order: Vec<String>,
}

impl CoverageTracker {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn record(&mut self, sha: &str, by_test: HashMap<String, HashMap<String, HashSet<u32>>>) {
        if by_test.is_empty() {
            return;
        }
        self.snapshots.insert(sha.to_string(), by_test);
        if !self.order.iter().any(|s| s == sha) {
            self.order.push(sha.to_string());
        }
    }

    /// Coverage for features of change with parent `base`, searching base then earlier processed ancestors.
    pub fn lookup(&self, base: &str, ancestor_chain: &[String]) -> CovSnapshot {
        for (lag, sha) in ancestor_chain.iter().enumerate() {
            if let Some(by_test) = self.snapshots.get(sha) {
                return CovSnapshot {
                    sha: sha.clone(),
                    lag: lag as i64,
                    by_test: by_test.clone(),
                };
            }
        }
        if let Some(by_test) = self.snapshots.get(base) {
            return CovSnapshot {
                sha: base.to_string(),
                lag: 0,
                by_test: by_test.clone(),
            };
        }
        CovSnapshot::empty()
    }
}
