//! Worktree-safe git SHA embedding (build.rs helper).

#[path = "../build_git_sha.rs"]
mod build_git_sha;

use std::fs;
use std::process::Command;

use build_git_sha::resolve_git_embed;
use tempfile::tempdir;

fn git(cwd: &std::path::Path, args: &[&str]) {
    let st = Command::new("git")
        .args(args)
        .current_dir(cwd)
        .status()
        .expect("git");
    assert!(st.success(), "git {args:?} failed");
}

fn git_out(cwd: &std::path::Path, args: &[&str]) -> String {
    let out = Command::new("git")
        .args(args)
        .current_dir(cwd)
        .output()
        .expect("git");
    assert!(out.status.success(), "git {args:?}");
    String::from_utf8(out.stdout).unwrap().trim().to_string()
}

#[test]
fn outside_git_is_unknown() {
    let dir = tempdir().unwrap();
    let meta = resolve_git_embed(dir.path());
    assert_eq!(meta.sha, "unknown");
    assert!(meta.rerun_paths.is_empty());
}

#[test]
fn plain_repo_embeds_short_sha_and_rerun_paths() {
    let dir = tempdir().unwrap();
    let root = dir.path();
    git(root, &["init"]);
    git(root, &["config", "user.email", "t@example.com"]);
    git(root, &["config", "user.name", "t"]);
    fs::write(root.join("f.txt"), "a\n").unwrap();
    git(root, &["add", "f.txt"]);
    git(root, &["commit", "-m", "c1"]);
    let short = git_out(root, &["rev-parse", "--short=12", "HEAD"]);

    let meta = resolve_git_embed(root);
    assert_eq!(meta.sha, short);
    assert!(
        meta.rerun_paths.iter().any(|p| p.ends_with("HEAD")),
        "missing HEAD: {:?}",
        meta.rerun_paths
    );
    assert!(
        meta.rerun_paths
            .iter()
            .any(|p| p.to_string_lossy().contains("refs/heads/")),
        "missing branch ref: {:?}",
        meta.rerun_paths
    );
}

#[test]
fn worktree_layout_resolves_same_sha() {
    let dir = tempdir().unwrap();
    let root = dir.path().join("main");
    let wt = dir.path().join("wt");
    fs::create_dir_all(&root).unwrap();
    git(&root, &["init"]);
    git(&root, &["config", "user.email", "t@example.com"]);
    git(&root, &["config", "user.name", "t"]);
    fs::write(root.join("f.txt"), "a\n").unwrap();
    git(&root, &["add", "f.txt"]);
    git(&root, &["commit", "-m", "c1"]);
    git(&root, &["branch", "feature"]);
    git(&root, &["worktree", "add", wt.to_str().unwrap(), "feature"]);
    let short = git_out(&root, &["rev-parse", "--short=12", "HEAD"]);

    let from_main = resolve_git_embed(&root);
    let from_wt = resolve_git_embed(&wt);
    assert_eq!(from_main.sha, short);
    assert_eq!(from_wt.sha, short);
    // Worktree HEAD lives under .git/worktrees/<name>/HEAD — must be watched.
    assert!(
        from_wt
            .rerun_paths
            .iter()
            .any(|p| p.to_string_lossy().contains("worktrees") && p.ends_with("HEAD")),
        "worktree must rerun on its own HEAD: {:?}",
        from_wt.rerun_paths
    );
    // Common packed-refs (may or may not exist yet) — at least branch ref or HEAD.
    assert!(!from_wt.rerun_paths.is_empty());
}
