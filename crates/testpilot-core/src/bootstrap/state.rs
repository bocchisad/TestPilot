use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use super::dump_fp::DbDumpFingerprint;
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
    /// Overall run status (`interrupted` on Ctrl-C).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub status: Option<String>,
    /// SHAs successfully backfilled or reused.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub backfill_done_shas: Vec<String>,
    /// Completed mutant pairs as `"C_sha:i"`.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub mutate_completed_pairs: Vec<String>,
    /// DB fingerprint when features_dump last succeeded (R8).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub dump_fingerprint: Option<DbDumpFingerprint>,
    /// Planned |B_set| and n_eligible after collect/backfill (report fields).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub n_eligible: Option<u32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub n_actual: Option<u32>,
    /// Accumulated wall seconds per step (sums across resume attempts).
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub step_timings_s: BTreeMap<String, f64>,
    /// Backfill accounting for the last completed backfill step.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub n_backfill_new: Option<u32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub n_backfill_reused: Option<u32>,
    /// Mutate counters from last successful mutate JSON (for report / rates).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub n_mutants_attempted: Option<u32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub n_timeout: Option<u32>,
    /// Set when mutate is skipped (e.g. `no_eligible_C`); step still marked done.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub mutate_skip_reason: Option<String>,
}

/// Add `secs` into `state.step_timings_s[step]` (resume-safe sum).
pub fn add_step_timing(state: &mut BootstrapState, step: &str, secs: f64) {
    *state.step_timings_s.entry(step.to_string()).or_insert(0.0) += secs;
}

/// Steps that are not yet `done`/`skipped` at process start (resume list).
pub fn pending_steps(state: &BootstrapState) -> Vec<String> {
    STEP_IDS
        .iter()
        .filter(|id| {
            !matches!(
                state.steps.get(**id),
                Some(StepStatus::Done) | Some(StepStatus::Skipped)
            )
        })
        .map(|s| (*s).to_string())
        .collect()
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
        status: None,
        backfill_done_shas: Vec::new(),
        mutate_completed_pairs: Vec::new(),
        dump_fingerprint: None,
        n_eligible: None,
        n_actual: None,
        step_timings_s: BTreeMap::new(),
        n_backfill_new: None,
        n_backfill_reused: None,
        n_mutants_attempted: None,
        n_timeout: None,
        mutate_skip_reason: None,
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

/// Injected fault for tests: fail after writing `.tmp`, before rename.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SaveFault {
    AfterTmpWrite,
}

/// Atomic write: `state.json.tmp` next to the final path, then rename.
pub fn save_state(repo: &Path, state: &BootstrapState) -> Result<()> {
    save_state_with_fault(repo, state, None)
}

/// Same as [`save_state`], with an optional fault injection point for tests.
pub fn save_state_with_fault(
    repo: &Path,
    state: &BootstrapState,
    fault: Option<SaveFault>,
) -> Result<()> {
    let dir = bootstrap_dir(repo);
    fs::create_dir_all(&dir)?;
    let path = state_path(repo);
    let tmp = dir.join("state.json.tmp");
    let raw = serde_json::to_string_pretty(state)
        .map_err(|e| Error::Other(format!("serialize state: {e}")))?;
    fs::write(&tmp, &raw)?;
    if fault == Some(SaveFault::AfterTmpWrite) {
        return Err(Error::Other(
            "injected fault after state.json.tmp write (before rename)".into(),
        ));
    }
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
