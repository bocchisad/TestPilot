//! Persist / restore HistoryState + CoChangeState for fast `features show`.

use serde::{Deserialize, Serialize};

use crate::error::{Error, Result};
use crate::features::cochange::CoChangeState;
use crate::features::history::{HistoryState, TestStats};
use crate::store::Store;

/// Magic prefix for zstd(postcard(FeatureSnapshot)) payloads.
const SNAP_MAGIC: &[u8] = b"TPZS";

/// Optional stride for sparse snapshots (1 = every commit). Compression is the
/// primary size lever; K>1 requires topo catch-up on show.
pub const SNAPSHOT_EVERY_K: usize = 1;

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
        // Sort keys so postcard payloads are byte-stable across runs (P3).
        let mut tests: Vec<_> = h
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
            .collect();
        tests.sort_by(|a, b| a.0.cmp(&b.0));

        let mut file_test = h.file_test_rows();
        file_test.sort();
        let mut func_test = h.func_test_rows();
        func_test.sort();

        let mut tests_in_file: Vec<_> = h
            .tests_in_file
            .iter()
            .map(|(k, v)| {
                let mut ids: Vec<_> = v.iter().cloned().collect();
                ids.sort();
                (k.clone(), ids)
            })
            .collect();
        tests_in_file.sort_by(|a, b| a.0.cmp(&b.0));

        HistorySer {
            tests,
            file_test,
            func_test,
            tests_in_file,
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
            h.insert_file_test(f, t, a, b);
        }
        for (f, t, a, b) in s.func_test {
            h.insert_func_test(f, t, a, b);
        }
        for (k, v) in s.tests_in_file {
            h.tests_in_file.insert(k, v.into_iter().collect());
        }
        h
    }
}

impl From<&CoChangeState> for CoChangeSer {
    fn from(c: &CoChangeState) -> Self {
        let mut events: Vec<_> = c
            .events()
            .iter()
            .map(|e| {
                let mut files = e.files.clone();
                files.sort();
                CoChangeEventSer { files, t: e.t }
            })
            .collect();
        events.sort_by(|a, b| a.t.cmp(&b.t).then_with(|| a.files.cmp(&b.files)));
        CoChangeSer { events }
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

fn encode_payload(snap: &FeatureSnapshot) -> Result<Vec<u8>> {
    let raw = postcard::to_allocvec(snap).map_err(|e| Error::Other(e.to_string()))?;
    let compressed =
        zstd::encode_all(raw.as_slice(), 3).map_err(|e| Error::Other(e.to_string()))?;
    let mut out = Vec::with_capacity(SNAP_MAGIC.len() + compressed.len());
    out.extend_from_slice(SNAP_MAGIC);
    out.extend_from_slice(&compressed);
    Ok(out)
}

fn decode_payload(blob: &[u8]) -> Result<FeatureSnapshot> {
    if let Some(rest) = blob.strip_prefix(SNAP_MAGIC) {
        let raw = zstd::decode_all(rest).map_err(|e| Error::Other(e.to_string()))?;
        return postcard::from_bytes(&raw).map_err(|e| Error::Other(e.to_string()));
    }
    // Legacy v4 JSON pair was stored as history_json TEXT; callers migrate via re-dump.
    Err(Error::Other(
        "unsupported feature snapshot format (re-run `testpilot features dump`)".into(),
    ))
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
    let payload = encode_payload(&snap)?;
    store.put_feature_history_snapshot(sha, &payload, snap.cov_source_sha.as_deref())
}

pub fn load_snapshot(
    store: &Store,
    sha: &str,
) -> Result<Option<(HistoryState, CoChangeState, Option<String>)>> {
    let Some((payload, cov)) = store.get_feature_history_snapshot(sha)? else {
        return Ok(None);
    };
    let snap = decode_payload(&payload)?;
    Ok(Some((
        HistoryState::from(snap.history),
        CoChangeState::from(snap.cochange),
        cov.or(snap.cov_source_sha),
    )))
}

/// Whether dump should persist a snapshot after processing this topo index.
pub fn should_save_snapshot(topo_idx: usize, had_primary_run: bool) -> bool {
    // K==1 → every commit (default). Larger K is for experiments; keep catch-up correct.
    let k = std::cmp::max(SNAPSHOT_EVERY_K, 1);
    if k == 1 {
        return true;
    }
    had_primary_run || topo_idx.rem_euclid(k) == 0
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
