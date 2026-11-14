//! R8: features_dump "done" keyed by DB fingerprint, not mtime.

use serde::{Deserialize, Serialize};

use crate::error::Result;
use crate::store::Store;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DbDumpFingerprint {
    pub max_run_id: i64,
    pub n_real_runs: i64,
    pub n_synthetic_runs: i64,
}

impl DbDumpFingerprint {
    pub fn from_store(store: &Store) -> Result<Self> {
        let conn = store.connection();
        let max_run_id: i64 =
            conn.query_row("SELECT COALESCE(MAX(id), 0) FROM test_runs", [], |r| {
                r.get(0)
            })?;
        let n_real_runs: i64 = conn.query_row(
            "SELECT COUNT(*) FROM test_runs WHERE IFNULL(synthetic, 0) = 0",
            [],
            |r| r.get(0),
        )?;
        let n_synthetic_runs: i64 = conn.query_row(
            "SELECT COUNT(*) FROM test_runs WHERE IFNULL(synthetic, 0) = 1",
            [],
            |r| r.get(0),
        )?;
        Ok(Self {
            max_run_id,
            n_real_runs,
            n_synthetic_runs,
        })
    }
}

/// Dump is stale when parquet is missing or stored fingerprint ≠ current DB.
pub fn dump_needs_refresh(
    parquet_exists: bool,
    stored: Option<&DbDumpFingerprint>,
    current: &DbDumpFingerprint,
) -> bool {
    if !parquet_exists {
        return true;
    }
    match stored {
        None => true,
        Some(s) => s != current,
    }
}
