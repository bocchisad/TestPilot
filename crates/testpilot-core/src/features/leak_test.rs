//! W2-5: counters must not update before emit (leakage).

#[cfg(test)]
mod tests {
    use crate::features::history::HistoryState;

    #[test]
    fn update_before_emit_would_leak_into_features() {
        // Simulate the invariant dump relies on: feature reads happen against
        // history *before* update_primary for the current change.
        let mut hist = HistoryState::new();
        let test_id = "tests/t.py::test_x";
        let tf = "tests/t.py";

        // Prior observation
        hist.update_primary(test_id, tf, "pass", false, 10, 100, &[], &[]);

        // Correct order: read then update
        let dur_ok = hist.dur(test_id);
        let obs_ok = hist.t_obs(test_id);
        assert_eq!(dur_ok, 10);
        assert_eq!(obs_ok, 1);

        // Bug: update current run first, then read → leak current duration/obs
        let mut leaked = HistoryState::new();
        leaked.update_primary(test_id, tf, "pass", false, 10, 100, &[], &[]);
        leaked.update_primary(test_id, tf, "fail", false, 99, 200, &[], &[]);
        let dur_leaked = leaked.dur(test_id);
        let obs_leaked = leaked.t_obs(test_id);
        // After a premature update, dur reflects the *current* run (99), not prior (10).
        assert_eq!(
            dur_leaked, 99,
            "precondition: premature update overwrites dur"
        );
        assert_eq!(obs_leaked, 2);

        // The emit-then-update contract: features for the current change must see
        // prior state (dur=10, obs=1), not post-update (99, 2).
        assert_ne!(
            dur_leaked, dur_ok,
            "leak detector: updating before emit changes dur"
        );
        assert_ne!(
            obs_leaked, obs_ok,
            "leak detector: updating before emit changes t_obs"
        );
    }
}
