use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use crate::error::{Error, Result};

#[derive(Debug, Default)]
pub struct CleanupReport {
    pub pruned_worktrees: bool,
    pub removed_wt_dirs: usize,
    pub removed_git_worktrees: usize,
}

fn git_worktree_list(repo: &Path) -> Result<Vec<(PathBuf, String)>> {
    let out = Command::new("git")
        .args(["-C"])
        .arg(repo)
        .args(["worktree", "list", "--porcelain"])
        .output()
        .map_err(Error::Io)?;
    if !out.status.success() {
        return Ok(vec![]);
    }
    let text = String::from_utf8_lossy(&out.stdout);
    let mut rows = Vec::new();
    for block in text.split("\n\n") {
        for line in block.lines() {
            if let Some(rest) = line.strip_prefix("worktree ") {
                rows.push((PathBuf::from(rest), String::new()));
                break;
            }
        }
    }
    Ok(rows)
}

fn is_bootstrap_worktree(path: &Path) -> bool {
    let s = path.to_string_lossy();
    s.contains("/.testpilot/bootstrap/wt-")
        || s.contains(".testpilot/bootstrap/wt-")
        || s.contains("/tp-mutate-")
        || s.contains("/tp-backfill-")
        || s.contains("/wt-backfill-")
        || s.contains("/wt-mutate-")
}

/// R9: prune + remove stale bootstrap worktrees (repo-local and legacy /tmp).
/// Symlinks under `.testpilot/bootstrap/wt-*` are unlinked only (target intact).
pub fn startup_cleanup(repo: &Path) -> Result<CleanupReport> {
    let mut report = CleanupReport::default();

    // Force-remove known bootstrap worktrees registered with git.
    if let Ok(list) = git_worktree_list(repo) {
        for (path, _) in list {
            if path == repo {
                continue;
            }
            if is_bootstrap_worktree(&path) {
                let _ = Command::new("git")
                    .args(["-C"])
                    .arg(repo)
                    .args(["worktree", "remove", "--force"])
                    .arg(&path)
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .status();
                if path.exists() {
                    let _ = fs::remove_dir_all(&path);
                }
                report.removed_git_worktrees += 1;
            }
        }
    }

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
            // wt-* (incl. wt-backfill-*, wt-mutate-*)
            if !name.starts_with("wt-") {
                continue;
            }
            let path = entry.path();
            let ft = entry.file_type().map_err(Error::Io)?;
            if ft.is_symlink() {
                fs::remove_file(&path).map_err(Error::Io)?;
                report.removed_wt_dirs += 1;
            } else if ft.is_dir() {
                // Best-effort detach from git first (silence "not a working tree").
                let _ = Command::new("git")
                    .args(["-C"])
                    .arg(repo)
                    .args(["worktree", "remove", "--force"])
                    .arg(&path)
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .status();
                fs::remove_dir_all(&path).map_err(Error::Io)?;
                report.removed_wt_dirs += 1;
            } else if ft.is_file() {
                fs::remove_file(&path).map_err(Error::Io)?;
                report.removed_wt_dirs += 1;
            }
        }
    }
    Ok(report)
}
