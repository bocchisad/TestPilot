//! Persist / restore HistoryState + CoChangeState for fast `features show`.

use serde::{Deserialize, Serialize};

use crate::error::{Error, Result};
use crate::features::cochange::CoChangeState;
use crate::features::history::{HistoryState, TestStats};
use crate::store::Store;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FeatureSnapshot {
    pub history: HistorySer,
    pub cochange: CoChangeSer,
    /// SHA of the coverage source run commit as of this snapshot (parent lineage).
    pub cov_source_sha: Option<String>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct HistorySer {
    pub tests: Vec<(String, TestStatsSer)>,
    pub file_test: Vec<(String, String, i64, i64)>,
    pub func_test: Vec<(String, String, i64, i64)>,
    pub tests_in_file: Vec<(String, Vec<String>)>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TestStatsSer {
    pub obs: i64,
    pub fails: i64,
    pub flaky: i64,
    pub first_ts: Option<i64>,
    pub last_dur_ms: Option<i64>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct CoChangeSer {
    pub events: Vec<CoChangeEventSer>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CoChangeEventSer {
    pub files: Vec<String>,
    pub t: i64,
}

impl From<&HistoryState> for HistorySer {
    fn from(h: &HistoryState) -> Self {
        HistorySer {
            tests: h
                .tests
                .iter()
                .map(|(k, v)| {
                    (
                        k.clone(),
                        TestStatsSer {
                            obs: v.obs,
                            fails: v.fails,
                            flaky: v.flaky,
                            first_ts: v.first_ts,
                            last_dur_ms: v.last_dur_ms,
                        },
                    )
                })
                .collect(),
            file_test: h
                .file_test
                .iter()
                .map(|((f, t), (a, b))| (f.clone(), t.clone(), *a, *b))
                .collect(),
            func_test: h
                .func_test
                .iter()
                .map(|((f, t), (a, b))| (f.clone(), t.clone(), *a, *b))
                .collect(),
            tests_in_file: h
                .tests_in_file
                .iter()
                .map(|(k, v)| (k.clone(), v.iter().cloned().collect()))
                .collect(),
        }
    }
}

impl From<HistorySer> for HistoryState {
    fn from(s: HistorySer) -> Self {
        let mut h = HistoryState::new();
        for (k, v) in s.tests {
            h.tests.insert(
                k,
                TestStats {
                    obs: v.obs,
                    fails: v.fails,
                    flaky: v.flaky,
                    first_ts: v.first_ts,
                    last_dur_ms: v.last_dur_ms,
                },
            );
        }
        for (f, t, a, b) in s.file_test {
            h.file_test.insert((f, t), (a, b));
        }
        for (f, t, a, b) in s.func_test {
            h.func_test.insert((f, t), (a, b));
        }
        for (k, v) in s.tests_in_file {
            h.tests_in_file.insert(k, v.into_iter().collect());
        }
        h
    }
}

impl From<&CoChangeState> for CoChangeSer {
    fn from(c: &CoChangeState) -> Self {
        CoChangeSer {
            events: c
                .events()
                .iter()
                .map(|e| CoChangeEventSer {
                    files: e.files.clone(),
                    t: e.t,
                })
                .collect(),
        }
    }
}

impl From<CoChangeSer> for CoChangeState {
    fn from(s: CoChangeSer) -> Self {
        let mut c = CoChangeState::new();
        for e in s.events {
            c.restore_event(e.files, e.t);
        }
        c
    }
}

pub fn save_snapshot(
    store: &Store,
    sha: &str,
    history: &HistoryState,
    cochange: &CoChangeState,
    cov_source_sha: Option<&str>,
) -> Result<()> {
    let snap = FeatureSnapshot {
        history: HistorySer::from(history),
        cochange: CoChangeSer::from(cochange),
        cov_source_sha: cov_source_sha.map(|s| s.to_string()),
    };
    let history_json =
        serde_json::to_string(&snap.history).map_err(|e| Error::Other(e.to_string()))?;
    let cochange_json =
        serde_json::to_string(&snap.cochange).map_err(|e| Error::Other(e.to_string()))?;
    store.put_feature_history_snapshot(
        sha,
        &history_json,
        &cochange_json,
        snap.cov_source_sha.as_deref(),
    )
}

pub fn load_snapshot(
    store: &Store,
    sha: &str,
) -> Result<Option<(HistoryState, CoChangeState, Option<String>)>> {
    let Some((hj, cj, cov)) = store.get_feature_history_snapshot(sha)? else {
        return Ok(None);
    };
    let history_ser: HistorySer =
        serde_json::from_str(&hj).map_err(|e| Error::Other(e.to_string()))?;
    let cochange_ser: CoChangeSer =
        serde_json::from_str(&cj).map_err(|e| Error::Other(e.to_string()))?;
    Ok(Some((
        HistoryState::from(history_ser),
        CoChangeState::from(cochange_ser),
        cov,
    )))
}

/// Snapshot found on first-parent lineage: `(sha, history, cochange, cov_source_sha)`.
pub type LineageSnapshot = (String, HistoryState, CoChangeState, Option<String>);

/// Walk first-parent chain from `base` (inclusive) until a snapshot is found.
pub fn find_snapshot_on_lineage(store: &Store, base: &str) -> Result<Option<LineageSnapshot>> {
    let mut cur = base.to_string();
    for _ in 0..100_000 {
        if let Some((h, c, cov)) = load_snapshot(store, &cur)? {
            return Ok(Some((cur, h, c, cov)));
        }
        match store.first_parent(&cur)? {
            Some(p) => cur = p,
            None => break,
        }
    }
    Ok(None)
}
