use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use super::fingerprint::Fingerprint;
use crate::error::{Error, Result};

pub const STEP_IDS: &[&str] = &[
    "cleanup",
    "init",
    "collect_git",
    "backfill",
    "mutate",
    "features_dump",
    "train",
    "gate",
    "finalize",
];

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StepStatus {
    Pending,
    Done,
    Interrupted,
    Skipped,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BootstrapState {
    pub fingerprint: Fingerprint,
    pub steps: BTreeMap<String, StepStatus>,
}

#[derive(Debug)]
pub enum ResumeDecision {
    Fresh(BootstrapState),
    Continue(BootstrapState),
    Refuse { hint: String },
}

pub fn state_path(repo: &Path) -> PathBuf {
    repo.join(".testpilot/bootstrap/state.json")
}

pub fn bootstrap_dir(repo: &Path) -> PathBuf {
    repo.join(".testpilot/bootstrap")
}

fn empty_steps() -> BTreeMap<String, StepStatus> {
    STEP_IDS
        .iter()
        .map(|id| ((*id).to_string(), StepStatus::Pending))
        .collect()
}

pub fn load_or_init_state(
    _repo: &Path,
    fingerprint: Fingerprint,
    _force: bool,
) -> Result<BootstrapState> {
    Ok(BootstrapState {
        fingerprint,
        steps: empty_steps(),
    })
}

pub fn load_state(repo: &Path) -> Result<Option<BootstrapState>> {
    let path = state_path(repo);
    if !path.exists() {
        return Ok(None);
    }
    let raw = fs::read_to_string(&path)?;
    let state: BootstrapState =
        serde_json::from_str(&raw).map_err(|e| Error::Other(format!("state.json: {e}")))?;
    Ok(Some(state))
}

/// Atomic write: `state.json.tmp` next to the final path, then rename.
pub fn save_state(repo: &Path, state: &BootstrapState) -> Result<()> {
    let dir = bootstrap_dir(repo);
    fs::create_dir_all(&dir)?;
    let path = state_path(repo);
    let tmp = dir.join("state.json.tmp");
    let raw = serde_json::to_string_pretty(state)
        .map_err(|e| Error::Other(format!("serialize state: {e}")))?;
    fs::write(&tmp, &raw)?;
    fs::rename(&tmp, &path)?;
    // Best-effort: if rename somehow left a tmp (cross-fs), remove it.
    let _ = fs::remove_file(&tmp);
    Ok(())
}

/// Decide how to proceed given an existing `state.json` (if any) and the current fingerprint.
pub fn decide_resume(repo: &Path, current: &Fingerprint, force: bool) -> Result<ResumeDecision> {
    match load_state(repo)? {
        None => Ok(ResumeDecision::Fresh(load_or_init_state(
            repo,
            current.clone(),
            force,
        )?)),
        Some(existing) if existing.fingerprint == *current => {
            Ok(ResumeDecision::Continue(existing))
        }
        Some(_existing) if force => Ok(ResumeDecision::Fresh(load_or_init_state(
            repo,
            current.clone(),
            true,
        )?)),
        Some(existing) => Ok(ResumeDecision::Refuse {
            hint: format!(
                "bootstrap state fingerprint mismatch (stored N={} K={} head={}…; \
                 current N={} K={} head={}…). Use --force to restart, or match flags/HEAD.",
                existing.fingerprint.n,
                existing.fingerprint.k,
                truncate_sha(&existing.fingerprint.head_sha),
                current.n,
                current.k,
                truncate_sha(&current.head_sha),
            ),
        }),
    }
}

fn truncate_sha(s: &str) -> String {
    if s.len() <= 12 {
        s.to_string()
    } else {
        format!("{}…", &s[..12])
    }
}
