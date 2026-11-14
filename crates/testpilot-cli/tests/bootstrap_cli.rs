//! CLI: bootstrap dry-run writes nothing; fingerprint refuse; cleanup.

use std::fs;
use std::process::Command;

use tempfile::TempDir;

fn bin() -> std::path::PathBuf {
    std::path::PathBuf::from(env!("CARGO_BIN_EXE_testpilot"))
}

fn git(cwd: &std::path::Path, args: &[&str]) {
    let st = Command::new("git")
        .args(args)
        .current_dir(cwd)
        .env("GIT_AUTHOR_NAME", "T")
        .env("GIT_AUTHOR_EMAIL", "t@e.com")
        .env("GIT_COMMITTER_NAME", "T")
        .env("GIT_COMMITTER_EMAIL", "t@e.com")
        .status()
        .unwrap();
    assert!(st.success(), "git {args:?}");
}

fn seed_repo() -> TempDir {
    let tmp = TempDir::new().unwrap();
    let root = tmp.path();
    git(root, &["init", "-b", "main"]);
    git(root, &["config", "user.email", "t@e.com"]);
    git(root, &["config", "user.name", "T"]);
    fs::write(root.join("f.txt"), "x\n").unwrap();
    git(root, &["add", "f.txt"]);
    git(root, &["commit", "-m", "i"]);
    tmp
}

#[test]
fn dry_run_writes_nothing() {
    let tmp = seed_repo();
    let root = tmp.path();
    let out = Command::new(bin())
        .args([
            "bootstrap",
            "--dry-run",
            "--repo",
            root.to_str().unwrap(),
            "--max-commits",
            "200",
            "--mutants-per-commit",
            "4",
        ])
        .output()
        .unwrap();
    assert!(
        out.status.success(),
        "stdout={} stderr={}",
        String::from_utf8_lossy(&out.stdout),
        String::from_utf8_lossy(&out.stderr)
    );
    let stdout = String::from_utf8_lossy(&out.stdout);
    assert!(stdout.contains("backfill"), "{stdout}");
    assert!(stdout.contains("unmeasured"), "{stdout}");
    assert!(
        !root.join(".testpilot").exists(),
        "dry-run must not create .testpilot/"
    );
}

#[test]
fn fingerprint_mismatch_refuses_resume() {
    let tmp = seed_repo();
    let root = tmp.path();
    let ok = Command::new(bin())
        .args([
            "bootstrap",
            "--repo",
            root.to_str().unwrap(),
            "--max-commits",
            "200",
        ])
        .output()
        .unwrap();
    assert!(
        ok.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&ok.stderr)
    );
    assert!(root.join(".testpilot/bootstrap/state.json").is_file());

    let bad = Command::new(bin())
        .args([
            "bootstrap",
            "--repo",
            root.to_str().unwrap(),
            "--max-commits",
            "160",
        ])
        .output()
        .unwrap();
    assert!(!bad.status.success());
    let err = format!(
        "{}{}",
        String::from_utf8_lossy(&bad.stdout),
        String::from_utf8_lossy(&bad.stderr)
    );
    assert!(err.contains("--force"), "{err}");
}

#[test]
fn force_restarts_after_mismatch() {
    let tmp = seed_repo();
    let root = tmp.path();
    assert!(
        Command::new(bin())
            .args([
                "bootstrap",
                "--repo",
                root.to_str().unwrap(),
                "--max-commits",
                "200"
            ])
            .status()
            .unwrap()
            .success()
    );
    let out = Command::new(bin())
        .args([
            "bootstrap",
            "--repo",
            root.to_str().unwrap(),
            "--max-commits",
            "160",
            "--force",
        ])
        .output()
        .unwrap();
    assert!(
        out.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&out.stderr)
    );
    let raw = fs::read_to_string(root.join(".testpilot/bootstrap/state.json")).unwrap();
    assert!(
        raw.contains("\"n\": 160") || raw.contains("\"n\":160"),
        "{raw}"
    );
}

#[test]
fn startup_removes_stale_wt() {
    let tmp = seed_repo();
    let root = tmp.path();
    let wt = root.join(".testpilot/bootstrap/wt-stale");
    fs::create_dir_all(&wt).unwrap();
    fs::write(wt.join("x"), "y").unwrap();
    assert!(
        Command::new(bin())
            .args(["bootstrap", "--repo", root.to_str().unwrap()])
            .status()
            .unwrap()
            .success()
    );
    assert!(!wt.exists());
}
