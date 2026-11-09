//! Incremental per-test / per-file / per-func history (fail rates, dur, flaky, age).

use std::collections::HashMap;

#[derive(Debug, Default, Clone)]
pub struct TestStats {
    pub obs: i64,
    pub fails: i64,
    pub flaky: i64,
    pub first_ts: Option<i64>,
    pub last_dur_ms: Option<i64>,
}

#[derive(Debug, Default)]
pub struct HistoryState {
    pub tests: HashMap<String, TestStats>,
    /// test_id → file → (fails, samples)
    pub file_test: HashMap<String, HashMap<String, (i64, i64)>>,
    /// test_id → func_qual → (fails, samples)
    pub func_test: HashMap<String, HashMap<String, (i64, i64)>>,
    /// test_file → distinct tests observed
    pub tests_in_file: HashMap<String, std::collections::HashSet<String>>,
}

impl HistoryState {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn t_fail_rate(&self, test_id: &str) -> f64 {
        match self.tests.get(test_id) {
            Some(s) if s.obs > 0 => s.fails as f64 / s.obs as f64,
            _ => 0.0,
        }
    }

    pub fn t_fail_cnt(&self, test_id: &str) -> i64 {
        self.tests.get(test_id).map(|s| s.fails).unwrap_or(0)
    }

    pub fn t_obs(&self, test_id: &str) -> i64 {
        self.tests.get(test_id).map(|s| s.obs).unwrap_or(0)
    }

    pub fn t_flaky_rate(&self, test_id: &str) -> f64 {
        match self.tests.get(test_id) {
            Some(s) if s.obs > 0 => s.flaky as f64 / s.obs as f64,
            _ => 0.0,
        }
    }

    pub fn t_age_days(&self, test_id: &str, t0: i64) -> f64 {
        match self.tests.get(test_id).and_then(|s| s.first_ts) {
            Some(first) => ((t0 - first).max(0) as f64) / 86400.0,
            None => 0.0,
        }
    }

    /// Last duration from a strictly earlier observation; else -1.
    pub fn dur(&self, test_id: &str) -> i64 {
        self.tests
            .get(test_id)
            .and_then(|s| s.last_dur_ms)
            .unwrap_or(-1)
    }

    pub fn tfile_ntests(&self, test_file: &str) -> i64 {
        self.tests_in_file
            .get(test_file)
            .map(|s| s.len() as i64)
            .unwrap_or(0)
    }

    pub fn pf_max(&self, srcf: &[String], test_id: &str) -> f64 {
        let Some(by_file) = self.file_test.get(test_id) else {
            return -1.0;
        };
        let mut best: Option<f64> = None;
        for f in srcf {
            if let Some(&(fails, samples)) = by_file.get(f) {
                if samples > 0 {
                    let r = fails as f64 / samples as f64;
                    best = Some(best.map_or(r, |b| b.max(r)));
                }
            }
        }
        best.unwrap_or(-1.0)
    }

    pub fn pq_max(&self, funcs: &[String], test_id: &str) -> f64 {
        let Some(by_func) = self.func_test.get(test_id) else {
            return -1.0;
        };
        let mut best: Option<f64> = None;
        for q in funcs {
            if let Some(&(fails, samples)) = by_func.get(q) {
                if samples > 0 {
                    let r = fails as f64 / samples as f64;
                    best = Some(best.map_or(r, |b| b.max(r)));
                }
            }
        }
        best.unwrap_or(-1.0)
    }

    /// Flat rows for snapshot serialization: (file, test, fails, samples).
    pub fn file_test_rows(&self) -> Vec<(String, String, i64, i64)> {
        let mut rows = Vec::new();
        for (test, by_file) in &self.file_test {
            for (file, (fails, samples)) in by_file {
                rows.push((file.clone(), test.clone(), *fails, *samples));
            }
        }
        rows
    }

    /// Flat rows for snapshot serialization: (func, test, fails, samples).
    pub fn func_test_rows(&self) -> Vec<(String, String, i64, i64)> {
        let mut rows = Vec::new();
        for (test, by_func) in &self.func_test {
            for (func, (fails, samples)) in by_func {
                rows.push((func.clone(), test.clone(), *fails, *samples));
            }
        }
        rows
    }

    pub fn insert_file_test(&mut self, file: String, test: String, fails: i64, samples: i64) {
        self.file_test
            .entry(test)
            .or_default()
            .insert(file, (fails, samples));
    }

    pub fn insert_func_test(&mut self, func: String, test: String, fails: i64, samples: i64) {
        self.func_test
            .entry(test)
            .or_default()
            .insert(func, (fails, samples));
    }

    /// Primary-run update (counts as an observation for fail/obs/dur/age/file/func).
    #[allow(clippy::too_many_arguments)]
    pub fn update_primary(
        &mut self,
        test_id: &str,
        test_file: &str,
        outcome: &str,
        flaky: bool,
        duration_ms: i64,
        ts: i64,
        srcf: &[String],
        funcs: &[String],
    ) {
        let failed = matches!(outcome, "fail" | "error");
        let st = self.tests.entry(test_id.to_string()).or_default();
        st.obs += 1;
        if failed {
            st.fails += 1;
        }
        if flaky {
            st.flaky += 1;
        }
        if st.first_ts.is_none() {
            st.first_ts = Some(ts);
        }
        st.last_dur_ms = Some(duration_ms);

        self.tests_in_file
            .entry(test_file.to_string())
            .or_default()
            .insert(test_id.to_string());

        let by_file = self.file_test.entry(test_id.to_string()).or_default();
        for f in srcf {
            let e = by_file.entry(f.clone()).or_insert((0, 0));
            e.1 += 1;
            if failed {
                e.0 += 1;
            }
        }
        let by_func = self.func_test.entry(test_id.to_string()).or_default();
        for q in funcs {
            let e = by_func.entry(q.clone()).or_insert((0, 0));
            e.1 += 1;
            if failed {
                e.0 += 1;
            }
        }
    }

    /// Extra clean runs on the same commit: flaky statistics only (still increments obs for flaky rate denom).
    pub fn update_flaky_only(&mut self, test_id: &str, flaky: bool) {
        let st = self.tests.entry(test_id.to_string()).or_default();
        st.obs += 1;
        if flaky {
            st.flaky += 1;
        }
    }
}
