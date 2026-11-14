//! Shared by `build.rs` and CLI tests: resolve git SHA + cargo:rerun-if-changed paths
//! in a worktree-safe way (`--git-dir` / `--git-common-dir`).

use std::path::{Path, PathBuf};
use std::process::Command;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GitEmbedMeta {
    pub sha: String,
    pub rerun_paths: Vec<PathBuf>,
}

fn git_stdout(cwd: &Path, args: &[&str]) -> Option<String> {
    let out = Command::new("git")
        .args(args)
        .current_dir(cwd)
        .output()
        .ok()?;
    if !out.status.success() {
        return None;
    }
    let s = String::from_utf8(out.stdout).ok()?;
    let t = s.trim();
    if t.is_empty() {
        None
    } else {
        Some(t.to_string())
    }
}

fn abs_under(cwd: &Path, git_path: &str) -> PathBuf {
    let p = Path::new(git_path);
    if p.is_absolute() {
        p.to_path_buf()
    } else {
        cwd.join(p)
    }
}

/// Resolve short HEAD SHA and paths that should trigger `cargo:rerun-if-changed`.
/// Outside a git checkout → `sha = "unknown"` and empty `rerun_paths`.
pub fn resolve_git_embed(cwd: &Path) -> GitEmbedMeta {
    let Some(git_dir) = git_stdout(cwd, &["rev-parse", "--git-dir"]) else {
        return GitEmbedMeta {
            sha: "unknown".into(),
            rerun_paths: vec![],
        };
    };
    let Some(common_dir) = git_stdout(cwd, &["rev-parse", "--git-common-dir"]) else {
        return GitEmbedMeta {
            sha: "unknown".into(),
            rerun_paths: vec![],
        };
    };
    let git_dir_abs = abs_under(cwd, &git_dir);
    let common_abs = abs_under(cwd, &common_dir);

    let mut rerun_paths = Vec::new();
    let head = git_dir_abs.join("HEAD");
    if head.exists() {
        rerun_paths.push(head.clone());
    }
    let packed = common_abs.join("packed-refs");
    if packed.exists() {
        rerun_paths.push(packed);
    }
    if let Some(sym) = git_stdout(cwd, &["symbolic-ref", "-q", "HEAD"]) {
        // Prefer worktree-local ref, else shared common ref.
        let candidates = [git_dir_abs.join(&sym), common_abs.join(&sym)];
        for c in candidates {
            if c.exists() {
                rerun_paths.push(c);
                break;
            }
        }
    }

    let sha =
        git_stdout(cwd, &["rev-parse", "--short=12", "HEAD"]).unwrap_or_else(|| "unknown".into());

    GitEmbedMeta { sha, rerun_paths }
}

/// Emit `cargo:` directives for the crate that owns `build.rs`.
#[allow(dead_code)] // used from build.rs; included by tests for resolve_* only
pub fn emit_cargo_git_sha(cwd: &Path) {
    let meta = resolve_git_embed(cwd);
    println!("cargo:rustc-env=TESTPILOT_GIT_SHA={}", meta.sha);
    for p in &meta.rerun_paths {
        println!("cargo:rerun-if-changed={}", p.display());
    }
    println!("cargo:rerun-if-env-changed=TESTPILOT_GIT_SHA");
}
