//! Decaying co-change features (`cc_max`, `cc_sum`).

pub const TAU_DAYS: f64 = 180.0;
const MAX_FILES: usize = 50;
const SECS_PER_DAY: f64 = 86400.0;

#[derive(Debug, Clone)]
struct CoChangeEvent {
    files: Vec<String>,
    t: i64,
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

    /// Record a commit's touched paths after features for that commit were emitted.
    pub fn record(&mut self, files: &[String], t: i64) {
        if files.len() > MAX_FILES || files.len() < 2 {
            return;
        }
        self.events.push(CoChangeEvent {
            files: files.to_vec(),
            t,
        });
    }

    /// `cc_max`, `cc_sum` for srcf files vs test file `tf` at change time `t0`.
    pub fn score(&self, srcf: &[String], tf: &str, t0: i64) -> (f64, f64) {
        if srcf.is_empty() {
            return (0.0, 0.0);
        }
        let mut per_src: Vec<f64> = Vec::with_capacity(srcf.len());
        let mut sum = 0.0;
        for f in srcf {
            let mut w_tf = 0.0;
            for ev in &self.events {
                if !ev.files.iter().any(|p| p == f) {
                    continue;
                }
                if !ev.files.iter().any(|p| p == tf) {
                    continue;
                }
                w_tf += decay(t0, ev.t);
            }
            per_src.push(w_tf);
            sum += w_tf;
        }
        let max = per_src.into_iter().fold(0.0_f64, f64::max);
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
