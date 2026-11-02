use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use sha2::{Digest, Sha256};

use crate::error::{Error, Result};
use crate::junit::{self, collapse_reruns};
use crate::store::{Store, TestRunRow};

#[derive(Debug)]
pub struct CollectJunitOpts<'a> {
    pub glob: &'a str,
    pub sha: &'a str,
    pub started_at: Option<i64>,
    pub meta: Option<String>,
    pub force: bool,
    pub cwd: &'a Path,
}

#[derive(Debug)]
pub struct CollectJunitStats {
    pub run_id: Option<i64>,
    pub files: usize,
    pub tests: usize,
    pub skipped_dedup: bool,
}

pub fn collect_junit(store: &Store, opts: CollectJunitOpts<'_>) -> Result<CollectJunitStats> {
    let mut paths: Vec<PathBuf> = glob::glob(&opts.cwd.join(opts.glob).to_string_lossy())
        .map_err(|e| Error::Other(e.to_string()))?
        .filter_map(|r| r.ok())
        .collect();
    if paths.is_empty() {
        // also try glob as-is (absolute / relative from cwd)
        paths = glob::glob(opts.glob)
            .map_err(|e| Error::Other(e.to_string()))?
            .filter_map(|r| r.ok())
            .collect();
    }
    paths.sort();
    if paths.is_empty() {
        return Err(Error::Other(format!(
            "no files matched glob {:?}",
            opts.glob
        )));
    }

    let mut hasher = Sha256::new();
    hasher.update(b"junit\0");
    hasher.update(opts.sha.as_bytes());
    hasher.update(b"\0");
    for p in &paths {
        hasher.update(p.to_string_lossy().as_bytes());
        hasher.update(b"\0");
        hasher.update(&std::fs::read(p)?);
        hasher.update(b"\0");
    }
    let input_hash = hex::encode(hasher.finalize());

    if !opts.force {
        if let Some(id) = store.find_run_by_input_hash("junit", opts.sha, &input_hash)? {
            tracing::info!(run_id = id, "skipping junit ingest (duplicate input_hash)");
            return Ok(CollectJunitStats {
                run_id: Some(id),
                files: paths.len(),
                tests: 0,
                skipped_dedup: true,
            });
        }
    }

    let mut all_raw = Vec::new();
    let mut xml_started: Option<i64> = None;
    for p in &paths {
        let parsed = junit::parse_file(p)?;
        if xml_started.is_none() {
            xml_started = parsed.started_at;
        }
        all_raw.extend(parsed.results);
    }
    let results = collapse_reruns(&all_raw);

    let started_at = opts.started_at.or(xml_started).unwrap_or_else(now_secs);
    let finished_at = now_secs();

    let run = TestRunRow {
        sha: opts.sha.to_string(),
        source: "junit".into(),
        started_at,
        finished_at: Some(finished_at),
        dirty: false,
        diff_hash: None,
        input_hash: Some(input_hash),
        meta: opts.meta,
    };

    let run_id = match store.insert_test_run(&run)? {
        Some(id) => id,
        None => {
            tracing::info!("skipping junit ingest (unique constraint)");
            return Ok(CollectJunitStats {
                run_id: None,
                files: paths.len(),
                tests: 0,
                skipped_dedup: true,
            });
        }
    };
    store.insert_test_results(run_id, &results)?;

    Ok(CollectJunitStats {
        run_id: Some(run_id),
        files: paths.len(),
        tests: results.len(),
        skipped_dedup: false,
    })
}

fn now_secs() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}
