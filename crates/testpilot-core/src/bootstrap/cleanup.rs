use std::fs;
use std::path::Path;
use std::process::Command;

use crate::error::{Error, Result};

#[derive(Debug, Default)]
pub struct CleanupReport {
    pub pruned_worktrees: bool,
    pub removed_wt_dirs: usize,
}

/// R9: `git worktree prune` + delete stale `.testpilot/bootstrap/wt-*`.
pub fn startup_cleanup(repo: &Path) -> Result<CleanupReport> {
    let mut report = CleanupReport::default();

    let status = Command::new("git")
        .args(["-C"])
        .arg(repo)
        .args(["worktree", "prune"])
        .status()
        .map_err(Error::Io)?;
    report.pruned_worktrees = status.success();

    let boot = repo.join(".testpilot/bootstrap");
    if boot.is_dir() {
        for entry in fs::read_dir(&boot).map_err(Error::Io)? {
            let entry = entry.map_err(Error::Io)?;
            let name = entry.file_name();
            let name = name.to_string_lossy();
            if name.starts_with("wt-") && entry.path().is_dir() {
                fs::remove_dir_all(entry.path()).map_err(Error::Io)?;
                report.removed_wt_dirs += 1;
            }
        }
    }
    Ok(report)
}
