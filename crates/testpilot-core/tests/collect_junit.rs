use std::process::Command;

use tempfile::TempDir;
use testpilot_core::collect_junit::{CollectJunitOpts, collect_junit};
use testpilot_core::store::Store;

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
    assert!(st.success());
}

#[test]
fn force_replaces_existing_run() {
    let tmp = TempDir::new().unwrap();
    git(tmp.path(), &["init", "-b", "main"]);
    git(tmp.path(), &["config", "user.email", "t@e.com"]);
    git(tmp.path(), &["config", "user.name", "T"]);
    std::fs::write(tmp.path().join("f.txt"), "x").unwrap();
    git(tmp.path(), &["add", "."]);
    git(tmp.path(), &["commit", "-m", "i"]);

    let xml = tmp.path().join("j.xml");
    std::fs::write(
        &xml,
        r#"<?xml version="1.0"?><testsuite timestamp="2024-06-01T12:00:00+03:00">
           <testcase classname="tests.t" name="test_a" time="0.1"/></testsuite>"#,
    )
    .unwrap();

    let mut store = Store::open(&tmp.path().join("db.sqlite")).unwrap();
    let opts = |force: bool| CollectJunitOpts {
        glob: "j.xml",
        sha: "HEAD",
        started_at: None,
        meta: None,
        force,
        cwd: tmp.path(),
    };
    let s1 = collect_junit(&mut store, opts(false)).unwrap();
    assert!(!s1.skipped_dedup);
    let id1 = s1.run_id.unwrap();
    let s2 = collect_junit(&mut store, opts(false)).unwrap();
    assert!(s2.skipped_dedup);
    let s3 = collect_junit(&mut store, opts(true)).unwrap();
    assert!(!s3.skipped_dedup);
    let id3 = s3.run_id.unwrap();
    assert_ne!(id1, id3);
    let n: i64 = store
        .connection()
        .query_row("SELECT COUNT(*) FROM test_runs", [], |r| r.get(0))
        .unwrap();
    assert_eq!(n, 1, "force must replace, not accumulate");
}

#[test]
fn sha_head_resolves_to_full_hex() {
    let tmp = TempDir::new().unwrap();
    git(tmp.path(), &["init", "-b", "main"]);
    git(tmp.path(), &["config", "user.email", "t@e.com"]);
    git(tmp.path(), &["config", "user.name", "T"]);
    std::fs::write(tmp.path().join("f.txt"), "x").unwrap();
    git(tmp.path(), &["add", "."]);
    git(tmp.path(), &["commit", "-m", "i"]);
    let head = Command::new("git")
        .args(["rev-parse", "HEAD"])
        .current_dir(tmp.path())
        .output()
        .unwrap();
    let head = String::from_utf8_lossy(&head.stdout).trim().to_string();

    std::fs::write(
        tmp.path().join("j.xml"),
        r#"<?xml version="1.0"?><testsuite>
           <testcase classname="tests.t" name="test_a" time="0.1"/></testsuite>"#,
    )
    .unwrap();
    let mut store = Store::open(&tmp.path().join("db.sqlite")).unwrap();
    collect_junit(
        &mut store,
        CollectJunitOpts {
            glob: "j.xml",
            sha: "HEAD",
            started_at: None,
            meta: None,
            force: false,
            cwd: tmp.path(),
        },
    )
    .unwrap();
    let sha: String = store
        .connection()
        .query_row("SELECT sha FROM test_runs", [], |r| r.get(0))
        .unwrap();
    assert_eq!(sha, head);
    assert_ne!(sha, "HEAD");
}

#[test]
fn input_hash_ignores_paths_uses_contents() {
    let tmp = TempDir::new().unwrap();
    git(tmp.path(), &["init", "-b", "main"]);
    git(tmp.path(), &["config", "user.email", "t@e.com"]);
    git(tmp.path(), &["config", "user.name", "T"]);
    std::fs::write(tmp.path().join("f.txt"), "x").unwrap();
    git(tmp.path(), &["add", "."]);
    git(tmp.path(), &["commit", "-m", "i"]);

    let body = r#"<?xml version="1.0"?><testsuite>
        <testcase classname="tests.t" name="test_a" time="0.1"/></testsuite>"#;
    std::fs::create_dir_all(tmp.path().join("a")).unwrap();
    std::fs::create_dir_all(tmp.path().join("b")).unwrap();
    std::fs::write(tmp.path().join("a/one.xml"), body).unwrap();
    std::fs::write(tmp.path().join("b/two.xml"), body).unwrap();

    let mut store = Store::open(&tmp.path().join("db.sqlite")).unwrap();
    let s1 = collect_junit(
        &mut store,
        CollectJunitOpts {
            glob: "a/one.xml",
            sha: "HEAD",
            started_at: None,
            meta: None,
            force: false,
            cwd: tmp.path(),
        },
    )
    .unwrap();
    let s2 = collect_junit(
        &mut store,
        CollectJunitOpts {
            glob: "b/two.xml",
            sha: "HEAD",
            started_at: None,
            meta: None,
            force: false,
            cwd: tmp.path(),
        },
    )
    .unwrap();
    assert!(
        s2.skipped_dedup,
        "same contents → same input_hash regardless of path"
    );
    assert_eq!(s1.run_id, s2.run_id);
}

#[test]
fn timestamp_offset_applied_on_ingest() {
    let tmp = TempDir::new().unwrap();
    git(tmp.path(), &["init", "-b", "main"]);
    git(tmp.path(), &["config", "user.email", "t@e.com"]);
    git(tmp.path(), &["config", "user.name", "T"]);
    std::fs::write(tmp.path().join("f.txt"), "x").unwrap();
    git(tmp.path(), &["add", "."]);
    git(tmp.path(), &["commit", "-m", "i"]);
    std::fs::write(
        tmp.path().join("j.xml"),
        r#"<?xml version="1.0"?><testsuite timestamp="2024-06-01T12:00:00+03:00">
           <testcase classname="tests.t" name="test_a" time="0.1"/></testsuite>"#,
    )
    .unwrap();
    let mut store = Store::open(&tmp.path().join("db.sqlite")).unwrap();
    collect_junit(
        &mut store,
        CollectJunitOpts {
            glob: "j.xml",
            sha: "HEAD",
            started_at: None,
            meta: None,
            force: false,
            cwd: tmp.path(),
        },
    )
    .unwrap();
    let started: i64 = store
        .connection()
        .query_row("SELECT started_at FROM test_runs", [], |r| r.get(0))
        .unwrap();
    let expected = testpilot_core::junit::parse_timestamp("2024-06-01T09:00:00Z").unwrap();
    assert_eq!(started, expected);
}
