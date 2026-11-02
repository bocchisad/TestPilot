use std::process::Command;

use tempfile::TempDir;
use testpilot_core::git::{GixRepo, collect_git};
use testpilot_core::store::Store;

fn git(cwd: &std::path::Path, args: &[&str]) {
    let status = Command::new("git")
        .args(args)
        .current_dir(cwd)
        .env("GIT_AUTHOR_NAME", "Test")
        .env("GIT_AUTHOR_EMAIL", "test@example.com")
        .env("GIT_COMMITTER_NAME", "Test")
        .env("GIT_COMMITTER_EMAIL", "test@example.com")
        .status()
        .expect("git");
    assert!(status.success(), "git {args:?} failed");
}

fn write(cwd: &std::path::Path, rel: &str, body: &str) {
    let path = cwd.join(rel);
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).unwrap();
    }
    std::fs::write(path, body).unwrap();
}

/// Build: add → modify → rename → branch+merge → delete (5 commits on main lineage plus merge).
fn build_repo(dir: &std::path::Path) {
    git(dir, &["init", "-b", "main"]);
    git(dir, &["config", "user.email", "test@example.com"]);
    git(dir, &["config", "user.name", "Test"]);

    write(dir, "a.txt", "one\n");
    git(dir, &["add", "a.txt"]);
    git(dir, &["commit", "-m", "add"]);

    write(dir, "a.txt", "one\ntwo\n");
    git(dir, &["add", "a.txt"]);
    git(dir, &["commit", "-m", "modify"]);

    git(dir, &["mv", "a.txt", "b.txt"]);
    git(dir, &["commit", "-m", "rename"]);

    git(dir, &["checkout", "-b", "feature"]);
    write(dir, "feat.txt", "feat\n");
    git(dir, &["add", "feat.txt"]);
    git(dir, &["commit", "-m", "feature"]);
    git(dir, &["checkout", "main"]);
    write(dir, "main.txt", "main\n");
    git(dir, &["add", "main.txt"]);
    git(dir, &["commit", "-m", "on-main"]);
    git(dir, &["merge", "feature", "-m", "merge"]);

    git(dir, &["rm", "main.txt"]);
    git(dir, &["commit", "-m", "delete"]);
}

#[test]
fn collect_git_incremental_and_idempotent() {
    let tmp = TempDir::new().unwrap();
    let repo_path = tmp.path();
    build_repo(repo_path);

    let db = tmp.path().join("db.sqlite");
    let store = Store::open(&db).unwrap();
    let repo = GixRepo::open(repo_path).unwrap();

    let s1 = collect_git(&repo, &store, None, None, Some(2), 5000).unwrap();
    assert_eq!(s1.inserted, 2);
    let n1: i64 = store
        .connection()
        .query_row("SELECT COUNT(*) FROM commits", [], |r| r.get(0))
        .unwrap();
    assert_eq!(n1, 2);

    let s2 = collect_git(&repo, &store, None, None, None, 5000).unwrap();
    assert!(s2.inserted >= 1);
    assert_eq!(s2.skipped, 2);
    let n2: i64 = store
        .connection()
        .query_row("SELECT COUNT(*) FROM commits", [], |r| r.get(0))
        .unwrap();
    assert!(n2 >= 5);

    // rename present
    let renames: i64 = store
        .connection()
        .query_row(
            "SELECT COUNT(*) FROM file_changes WHERE change_type = 'R'",
            [],
            |r| r.get(0),
        )
        .unwrap();
    assert!(renames >= 1, "expected rename in file_changes");

    // merge has two parents
    let merges: i64 = store
        .connection()
        .query_row(
            "SELECT COUNT(*) FROM (
                SELECT sha FROM commit_parents GROUP BY sha HAVING COUNT(*) > 1
             )",
            [],
            |r| r.get(0),
        )
        .unwrap();
    assert!(merges >= 1);

    let s3 = collect_git(&repo, &store, None, None, None, 5000).unwrap();
    assert_eq!(s3.inserted, 0);
    let n3: i64 = store
        .connection()
        .query_row("SELECT COUNT(*) FROM commits", [], |r| r.get(0))
        .unwrap();
    assert_eq!(n3, n2);
}

#[test]
fn oversized_sets_flag_without_file_changes() {
    let tmp = TempDir::new().unwrap();
    let repo_path = tmp.path();
    git(repo_path, &["init", "-b", "main"]);
    git(repo_path, &["config", "user.email", "t@e.com"]);
    git(repo_path, &["config", "user.name", "T"]);
    for i in 0..5 {
        write(repo_path, &format!("f{i}.txt"), "x\n");
    }
    git(repo_path, &["add", "."]);
    git(repo_path, &["commit", "-m", "many"]);

    let store = Store::open(&tmp.path().join("db.sqlite")).unwrap();
    let repo = GixRepo::open(repo_path).unwrap();
    let st = collect_git(&repo, &store, None, None, None, 2).unwrap();
    assert_eq!(st.oversized, 1);
    let oversized: i64 = store
        .connection()
        .query_row("SELECT oversized FROM commits LIMIT 1", [], |r| r.get(0))
        .unwrap();
    assert_eq!(oversized, 1);
    let fc: i64 = store
        .connection()
        .query_row("SELECT COUNT(*) FROM file_changes", [], |r| r.get(0))
        .unwrap();
    assert_eq!(fc, 0);
}
