use std::process::Command;

use tempfile::TempDir;

fn bin() -> std::path::PathBuf {
    // Compile-time path injected by cargo for integration tests.
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

fn git_out(cwd: &std::path::Path, args: &[&str]) -> String {
    let out = Command::new("git")
        .args(args)
        .current_dir(cwd)
        .output()
        .unwrap();
    assert!(out.status.success(), "git {args:?}");
    String::from_utf8_lossy(&out.stdout).trim().to_string()
}

#[test]
fn init_from_subdirectory_writes_toplevel_exclude() {
    // N3
    let tmp = TempDir::new().unwrap();
    let root = tmp.path();
    git(root, &["init", "-b", "main"]);
    git(root, &["config", "user.email", "t@e.com"]);
    git(root, &["config", "user.name", "T"]);
    std::fs::write(root.join("f.txt"), "x\n").unwrap();
    git(root, &["add", "f.txt"]);
    git(root, &["commit", "-m", "i"]);
    let sub = root.join("sub");
    std::fs::create_dir_all(&sub).unwrap();

    let out = Command::new(bin())
        .arg("init")
        .current_dir(&sub)
        .output()
        .unwrap();
    assert!(
        out.status.success(),
        "stdout={} stderr={}",
        String::from_utf8_lossy(&out.stdout),
        String::from_utf8_lossy(&out.stderr)
    );

    let db = root.join(".testpilot/db.sqlite");
    assert!(
        db.is_file(),
        "db must live at git toplevel, got missing {db:?}"
    );
    assert!(
        !sub.join(".git").exists(),
        "must not create a nested .git under sub/"
    );
    let exclude = root.join(".git/info/exclude");
    let text = std::fs::read_to_string(&exclude).unwrap();
    assert!(
        text.lines().any(|l| l.trim() == ".testpilot/"),
        "exclude missing .testpilot/: {text}"
    );
}

#[test]
fn init_in_git_worktree_uses_git_path_exclude() {
    // N3 worktree (.git is a file)
    let tmp = TempDir::new().unwrap();
    let main = tmp.path().join("main");
    let wt = tmp.path().join("wt");
    std::fs::create_dir_all(&main).unwrap();
    git(&main, &["init", "-b", "main"]);
    git(&main, &["config", "user.email", "t@e.com"]);
    git(&main, &["config", "user.name", "T"]);
    std::fs::write(main.join("f.txt"), "x\n").unwrap();
    git(&main, &["add", "f.txt"]);
    git(&main, &["commit", "-m", "i"]);
    git(&main, &["branch", "wt-branch"]);
    git(
        &main,
        &["worktree", "add", wt.to_str().unwrap(), "wt-branch"],
    );
    assert!(wt.join(".git").is_file(), "worktree .git should be a file");

    let out = Command::new(bin())
        .arg("init")
        .current_dir(&wt)
        .output()
        .unwrap();
    assert!(
        out.status.success(),
        "stdout={} stderr={}",
        String::from_utf8_lossy(&out.stdout),
        String::from_utf8_lossy(&out.stderr)
    );
    assert!(wt.join(".testpilot/db.sqlite").is_file());
    let exclude = git_out(&wt, &["rev-parse", "--git-path", "info/exclude"]);
    let exclude_path = if std::path::Path::new(&exclude).is_absolute() {
        std::path::PathBuf::from(&exclude)
    } else {
        wt.join(&exclude)
    };
    let text = std::fs::read_to_string(&exclude_path).unwrap();
    assert!(
        text.lines().any(|l| l.trim() == ".testpilot/"),
        "worktree exclude missing entry: {text} at {exclude_path:?}"
    );
}

#[test]
fn debug_coverage_prefers_pytest_run_with_coverage() {
    // N5
    use testpilot_core::store::{CoverageRow, Store, TestResultRow, TestRunRow};

    let tmp = TempDir::new().unwrap();
    let root = tmp.path();
    git(root, &["init", "-b", "main"]);
    git(root, &["config", "user.email", "t@e.com"]);
    git(root, &["config", "user.name", "T"]);
    std::fs::write(root.join("f.txt"), "x\n").unwrap();
    git(root, &["add", "f.txt"]);
    git(root, &["commit", "-m", "i"]);
    let sha = git_out(root, &["rev-parse", "HEAD"]);

    let db = root.join(".testpilot/db.sqlite");
    let mut store = Store::open(&db).unwrap();
    // older pytest run with coverage
    let pytest_id = store
        .insert_run_atomic(
            &TestRunRow {
                sha: sha.clone(),
                source: "pytest".into(),
                started_at: 1,
                finished_at: Some(2),
                dirty: false,
                diff_hash: None,
                input_hash: None,
                meta: None,
                synthetic: false,
                partial: false,
            },
            &[TestResultRow {
                test_id: "t::test_a".into(),
                outcome: "pass".into(),
                duration_ms: 1,
                message: None,
                flaky: false,
                attempts: 1,
            }],
            &[CoverageRow {
                test_id: "t::test_a".into(),
                path: "f.txt".into(),
                lines: vec![0x01, 0x01],
            }],
            false,
        )
        .unwrap()
        .unwrap();
    // newer junit run without coverage — must NOT be selected
    let _junit_id = store
        .insert_run_atomic(
            &TestRunRow {
                sha: sha.clone(),
                source: "junit".into(),
                started_at: 3,
                finished_at: Some(4),
                dirty: false,
                diff_hash: None,
                input_hash: Some("deadbeef".into()),
                meta: None,
                synthetic: false,
                partial: false,
            },
            &[TestResultRow {
                test_id: "t::test_a".into(),
                outcome: "pass".into(),
                duration_ms: 1,
                message: None,
                flaky: false,
                attempts: 1,
            }],
            &[],
            false,
        )
        .unwrap()
        .unwrap();
    drop(store);

    let out = Command::new(bin())
        .args([
            "debug",
            "coverage",
            "--db",
            db.to_str().unwrap(),
            "--sha",
            &sha,
            "--test-id",
            "t::test_a",
        ])
        .current_dir(root)
        .output()
        .unwrap();
    assert!(
        out.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&out.stderr)
    );
    let stdout = String::from_utf8_lossy(&out.stdout);
    assert!(
        stdout.contains("f.txt"),
        "expected coverage from pytest run {pytest_id}, got: {stdout}"
    );
}

#[test]
fn debug_coverage_errors_when_no_pytest_coverage() {
    use testpilot_core::store::{Store, TestResultRow, TestRunRow};

    let tmp = TempDir::new().unwrap();
    let root = tmp.path();
    git(root, &["init", "-b", "main"]);
    git(root, &["config", "user.email", "t@e.com"]);
    git(root, &["config", "user.name", "T"]);
    std::fs::write(root.join("f.txt"), "x\n").unwrap();
    git(root, &["add", "f.txt"]);
    git(root, &["commit", "-m", "i"]);
    let sha = git_out(root, &["rev-parse", "HEAD"]);

    let db = root.join("db.sqlite");
    let mut store = Store::open(&db).unwrap();
    store
        .insert_run_atomic(
            &TestRunRow {
                sha: sha.clone(),
                source: "junit".into(),
                started_at: 1,
                finished_at: Some(2),
                dirty: false,
                diff_hash: None,
                input_hash: Some("abcd".into()),
                meta: None,
                synthetic: false,
                partial: false,
            },
            &[TestResultRow {
                test_id: "t::a".into(),
                outcome: "pass".into(),
                duration_ms: 1,
                message: None,
                flaky: false,
                attempts: 1,
            }],
            &[],
            false,
        )
        .unwrap();
    drop(store);

    let out = Command::new(bin())
        .args([
            "debug",
            "coverage",
            "--db",
            db.to_str().unwrap(),
            "--sha",
            &sha,
            "--test-id",
            "t::a",
        ])
        .current_dir(root)
        .output()
        .unwrap();
    assert!(!out.status.success());
    let err = String::from_utf8_lossy(&out.stderr);
    assert!(
        err.contains("no pytest run with coverage") || err.contains("coverage"),
        "{err}"
    );
}
