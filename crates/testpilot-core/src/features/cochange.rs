//! Decaying co-change features (`cc_max`, `cc_sum`).

use std::collections::HashSet;

pub const TAU_DAYS: f64 = 180.0;
const MAX_FILES: usize = 50;
const SECS_PER_DAY: f64 = 86400.0;

#[derive(Debug, Clone)]
pub struct CoChangeEvent {
    pub files: Vec<String>,
    pub file_set: HashSet<String>,
    pub t: i64,
}

/// Accumulator of past commits' file sets (updated only after emit).
#[derive(Debug, Default)]
pub struct CoChangeState {
    events: Vec<CoChangeEvent>,
}

impl CoChangeState {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn events(&self) -> &[CoChangeEvent] {
        &self.events
    }

    /// Record a commit's touched paths after features for that commit were emitted.
    pub fn record(&mut self, files: &[String], t: i64) {
        if files.len() > MAX_FILES || files.len() < 2 {
            return;
        }
        self.push_event(files.to_vec(), t);
    }

    /// Restore a previously recorded event (no size filter).
    pub fn restore_event(&mut self, files: Vec<String>, t: i64) {
        self.push_event(files, t);
    }

    fn push_event(&mut self, files: Vec<String>, t: i64) {
        if let Some(last) = self.events.last() {
            if last.t == t && last.files == files {
                return;
            }
        }
        let file_set: HashSet<String> = files.iter().cloned().collect();
        self.events.push(CoChangeEvent { files, file_set, t });
    }

    /// `cc_max`, `cc_sum` for srcf files vs test file `tf` at change time `t0`.
    pub fn score(&self, srcf: &[String], tf: &str, t0: i64) -> (f64, f64) {
        if srcf.is_empty() {
            return (0.0, 0.0);
        }
        let mut max = 0.0_f64;
        let mut sum = 0.0;
        for f in srcf {
            let mut w_tf = 0.0;
            for ev in &self.events {
                if !ev.file_set.contains(f) || !ev.file_set.contains(tf) {
                    continue;
                }
                w_tf += decay(t0, ev.t);
            }
            max = max.max(w_tf);
            sum += w_tf;
        }
        (max, sum)
    }
}

pub fn decay(t0: i64, t: i64) -> f64 {
    let dt_days = ((t0 - t).max(0) as f64) / SECS_PER_DAY;
    (-dt_days / TAU_DAYS).exp()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn decay_at_zero_is_one() {
        assert!((decay(100, 100) - 1.0).abs() < 1e-12);
    }

    #[test]
    fn score_uses_only_recorded_events() {
        let mut st = CoChangeState::new();
        assert_eq!(st.score(&["a.py".into()], "tests/t.py", 10), (0.0, 0.0));
        st.record(&["a.py".into(), "tests/t.py".into()], 0);
        let (mx, sm) = st.score(&["a.py".into()], "tests/t.py", 0);
        assert!((mx - 1.0).abs() < 1e-9);
        assert!((sm - 1.0).abs() < 1e-9);
    }
}
