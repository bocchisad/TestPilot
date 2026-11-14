//! R6: B_set = sampled_C ∪ last B first-parent commits; reuse clean real runs.

use std::collections::BTreeSet;
use std::path::Path;
use std::process::Command;

use crate::error::{Error, Result};
use crate::store::Store;

/// First-parent SHAs from HEAD, newest-first from `git rev-list`, returned oldest-first.
pub fn first_parent_shas(repo: &Path, n: u32) -> Result<Vec<String>> {
    if n == 0 {
        return Ok(vec![]);
    }
    let out = Command::new("git")
        .args(["-C"])
        .arg(repo)
        .args(["rev-list", &format!("-n{n}"), "HEAD"])
        .output()
        .map_err(Error::Io)?;
    if !out.status.success() {
        return Err(Error::Git(format!(
            "rev-list failed: {}",
            String::from_utf8_lossy(&out.stderr)
        )));
    }
    let mut shas: Vec<String> = String::from_utf8_lossy(&out.stdout)
        .lines()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .collect();
    shas.reverse(); // oldest → newest
    Ok(shas)
}

/// Union of first-parent limb and sampled C (order: FP then new C, unique).
pub fn union_b_set(first_parent: &[String], sampled_c: &[String]) -> Vec<String> {
    let mut seen = BTreeSet::new();
    let mut out = Vec::new();
    for s in first_parent.iter().chain(sampled_c.iter()) {
        if seen.insert(s.clone()) {
            out.push(s.clone());
        }
    }
    out
}

/// SHAs in `planned` that still need a pytest backfill (no reusable clean real run).
pub fn shas_needing_backfill(store: &Store, planned: &[String]) -> Result<Vec<String>> {
    let mut need = Vec::new();
    for sha in planned {
        if store.last_clean_run_with_results(sha)?.is_none() {
            need.push(sha.clone());
        }
    }
    Ok(need)
}
