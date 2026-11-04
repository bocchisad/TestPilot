use std::process::Command;

use tempfile::TempDir;
use testpilot_core::git::{GitRepo, GixRepo, Sha, collect_git, collect_git_ex};
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

fn git_out(cwd: &std::path::Path, args: &[&str]) -> String {
    let out = Command::new("git")
        .args(args)
        .current_dir(cwd)
        .env("GIT_AUTHOR_NAME", "Test")
        .env("GIT_AUTHOR_EMAIL", "test@example.com")
        .env("GIT_COMMITTER_NAME", "Other")
        .env("GIT_COMMITTER_EMAIL", "other@example.com")
        .output()
        .expect("git");
    assert!(out.status.success(), "git {args:?} failed");
    String::from_utf8_lossy(&out.stdout).trim().to_string()
}

fn write(cwd: &std::path::Path, rel: &str, body: &str) {
    let path = cwd.join(rel);
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).unwrap();
    }
    std::fs::write(path, body).unwrap();
}

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
    let mut store = Store::open(&db).unwrap();
    let repo = GixRepo::open(repo_path).unwrap();

    let s1 = collect_git(&repo, &mut store, None, None, Some(2), 5000).unwrap();
    assert_eq!(s1.inserted, 2);
    let n1: i64 = store
        .connection()
        .query_row("SELECT COUNT(*) FROM commits", [], |r| r.get(0))
        .unwrap();
    assert_eq!(n1, 2);

    let s2 = collect_git(&repo, &mut store, None, None, None, 5000).unwrap();
    assert!(s2.inserted >= 1);
    assert_eq!(s2.skipped, 2);
    let n2: i64 = store
        .connection()
        .query_row("SELECT COUNT(*) FROM commits", [], |r| r.get(0))
        .unwrap();
    assert!(n2 >= 5);

    let renames: i64 = store
        .connection()
        .query_row(
            "SELECT COUNT(*) FROM file_changes WHERE change_type = 'R'",
            [],
            |r| r.get(0),
        )
        .unwrap();
    assert!(renames >= 1, "expected rename in file_changes");

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

    let s3 = collect_git(&repo, &mut store, None, None, None, 5000).unwrap();
    assert_eq!(s3.inserted, 0);
    let n3: i64 = store
        .connection()
        .query_row("SELECT COUNT(*) FROM commits", [], |r| r.get(0))
        .unwrap();
    assert_eq!(n3, n2);
}

/// B2: parents always inserted before children; max+full == full.
#[test]
fn topo_order_parents_before_children_and_max_completes() {
    let tmp = TempDir::new().unwrap();
    build_repo(tmp.path());
    let mut store = Store::open(&tmp.path().join("db.sqlite")).unwrap();
    let repo = GixRepo::open(tmp.path()).unwrap();

    collect_git(&repo, &mut store, None, None, Some(2), 5000).unwrap();
    collect_git(&repo, &mut store, None, None, None, 5000).unwrap();

    let mut store_full = Store::open(&tmp.path().join("db2.sqlite")).unwrap();
    collect_git(&repo, &mut store_full, None, None, None, 5000).unwrap();

    let n1: i64 = store
        .connection()
        .query_row("SELECT COUNT(*) FROM commits", [], |r| r.get(0))
        .unwrap();
    let n2: i64 = store_full
        .connection()
        .query_row("SELECT COUNT(*) FROM commits", [], |r| r.get(0))
        .unwrap();
    assert_eq!(n1, n2);

    // rowid order reflects insert order ≈ topo order
    let rows: Vec<(String, i64)> = {
        let mut stmt = store
            .connection()
            .prepare("SELECT sha, rowid FROM commits ORDER BY rowid")
            .unwrap();
        stmt.query_map([], |r| Ok((r.get(0)?, r.get(1)?)))
            .unwrap()
            .map(|r| r.unwrap())
            .collect()
    };
    let pos: std::collections::HashMap<_, _> = rows.iter().map(|(s, i)| (s.clone(), *i)).collect();
    let mut stmt = store
        .connection()
        .prepare("SELECT sha, parent_sha FROM commit_parents")
        .unwrap();
    let edges: Vec<(String, String)> = stmt
        .query_map([], |r| Ok((r.get(0)?, r.get(1)?)))
        .unwrap()
        .map(|r| r.unwrap())
        .collect();
    for (child, parent) in edges {
        if let (Some(&pc), Some(&pp)) = (pos.get(&child), pos.get(&parent)) {
            assert!(
                pp < pc,
                "parent {parent} must be inserted before child {child}"
            );
        }
    }
}

/// B3: --since-sha HEAD~10 on linear history = exactly 10 commits including HEAD.
#[test]
fn since_sha_exclusive_counts_from_head() {
    let tmp = TempDir::new().unwrap();
    let dir = tmp.path();
    git(dir, &["init", "-b", "main"]);
    git(dir, &["config", "user.email", "t@e.com"]);
    git(dir, &["config", "user.name", "T"]);
    for i in 0..15 {
        write(dir, "f.txt", &format!("{i}\n"));
        git(dir, &["add", "f.txt"]);
        git(dir, &["commit", "-m", &format!("c{i}")]);
        // ensure distinct author times
        std::thread::sleep(std::time::Duration::from_millis(20));
    }
    let since = git_out(dir, &["rev-parse", "HEAD~10"]);
    let head = git_out(dir, &["rev-parse", "HEAD"]);

    let mut store = Store::open(&dir.join("db.sqlite")).unwrap();
    let repo = GixRepo::open(dir).unwrap();
    let st = collect_git(&repo, &mut store, Some(&since), None, None, 5000).unwrap();
    assert_eq!(st.inserted, 10, "expected 10 commits after since exclusive");
    let has_head: i64 = store
        .connection()
        .query_row(
            "SELECT COUNT(*) FROM commits WHERE sha = ?1",
            rusqlite::params![head],
            |r| r.get(0),
        )
        .unwrap();
    assert_eq!(has_head, 1);
    let has_since: i64 = store
        .connection()
        .query_row(
            "SELECT COUNT(*) FROM commits WHERE sha = ?1",
            rusqlite::params![since],
            |r| r.get(0),
        )
        .unwrap();
    assert_eq!(has_since, 0, "since itself must be excluded");
}

/// B11: author_ts uses author time, not committer.
#[test]
fn author_ts_uses_author_not_committer() {
    let tmp = TempDir::new().unwrap();
    let dir = tmp.path();
    git(dir, &["init", "-b", "main"]);
    git(dir, &["config", "user.email", "t@e.com"]);
    git(dir, &["config", "user.name", "T"]);
    write(dir, "a.txt", "x\n");
    git(dir, &["add", "a.txt"]);
    let status = Command::new("git")
        .args(["commit", "-m", "x"])
        .current_dir(dir)
        .env("GIT_AUTHOR_DATE", "1000000000 +0000")
        .env("GIT_COMMITTER_DATE", "1500000000 +0000")
        .env("GIT_AUTHOR_NAME", "A")
        .env("GIT_AUTHOR_EMAIL", "a@a.com")
        .env("GIT_COMMITTER_NAME", "C")
        .env("GIT_COMMITTER_EMAIL", "c@c.com")
        .status()
        .unwrap();
    assert!(status.success());

    let mut store = Store::open(&dir.join("db.sqlite")).unwrap();
    let repo = GixRepo::open(dir).unwrap();
    collect_git(&repo, &mut store, None, None, None, 5000).unwrap();
    let ts: i64 = store
        .connection()
        .query_row("SELECT author_ts FROM commits", [], |r| r.get(0))
        .unwrap();
    assert_eq!(ts, 1_000_000_000);
}

/// B12: lines match git diff-tree -M --numstat.
#[test]
fn line_counts_match_git_numstat() {
    let tmp = TempDir::new().unwrap();
    let dir = tmp.path();
    git(dir, &["init", "-b", "main"]);
    git(dir, &["config", "user.email", "t@e.com"]);
    git(dir, &["config", "user.name", "T"]);
    write(dir, "a.txt", "one\ntwo\nthree\n");
    git(dir, &["add", "a.txt"]);
    git(dir, &["commit", "-m", "base"]);
    write(dir, "a.txt", "one\ntwo changed\nthree\nfour\n");
    git(dir, &["add", "a.txt"]);
    git(dir, &["commit", "-m", "edit"]);

    let mut store = Store::open(&dir.join("db.sqlite")).unwrap();
    let repo = GixRepo::open(dir).unwrap();
    collect_git(&repo, &mut store, None, None, None, 5000).unwrap();

    let head = git_out(dir, &["rev-parse", "HEAD"]);
    let numstat = git_out(dir, &["diff-tree", "-M", "--numstat", "-r", &head]);
    // numstat line like: "1\t1\ta.txt"
    let mut expected = None;
    for line in numstat.lines() {
        let parts: Vec<_> = line.split('\t').collect();
        if parts.len() >= 3 && parts[2] == "a.txt" {
            expected = Some((
                parts[0].parse::<i64>().unwrap(),
                parts[1].parse::<i64>().unwrap(),
            ));
        }
    }
    let (ea, ed) = expected.expect("numstat for a.txt");
    let (aa, ad): (i64, i64) = store
        .connection()
        .query_row(
            "SELECT lines_added, lines_deleted FROM file_changes WHERE sha = ?1 AND path = 'a.txt'",
            rusqlite::params![head],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )
        .unwrap();
    assert_eq!((aa, ad), (ea, ed), "hunk counts must match git --numstat");
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

    let mut store = Store::open(&tmp.path().join("db.sqlite")).unwrap();
    let repo = GixRepo::open(repo_path).unwrap();
    let st = collect_git(&repo, &mut store, None, None, None, 2).unwrap();
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

/// B4: interrupt mid-batch after commit insert but before file_changes; the
/// whole batch must roll back. A second collect must leave no orphan commits.
#[test]
fn batch_transaction_rolls_back_on_mid_batch_failure() {
    let tmp = TempDir::new().unwrap();
    build_repo(tmp.path());
    let db = tmp.path().join("db.sqlite");
    let mut store = Store::open(&db).unwrap();
    let repo = GixRepo::open(tmp.path()).unwrap();

    let err = collect_git_ex(
        &repo,
        &mut store,
        None,
        None,
        None,
        5000,
        200,
        Some(2), // fail after 2 commit writes inside the batch
    )
    .unwrap_err();
    assert!(err.to_string().contains("injected mid-batch"), "{err}");

    let commits: i64 = store
        .connection()
        .query_row("SELECT COUNT(*) FROM commits", [], |r| r.get(0))
        .unwrap();
    assert_eq!(commits, 0, "failed batch must not leave partial commits");

    // Retry without injection — full collect succeeds and has no orphans.
    collect_git(&repo, &mut store, None, None, None, 5000).unwrap();
    let orphans: i64 = store
        .connection()
        .query_row(
            "SELECT COUNT(*) FROM commits c
             WHERE c.oversized = 0
               AND NOT EXISTS (SELECT 1 FROM file_changes f WHERE f.sha = c.sha)",
            [],
            |r| r.get(0),
        )
        .unwrap();
    assert_eq!(
        orphans, 0,
        "non-oversized commits must have file_changes after re-collect"
    );
}

#[test]
fn file_at_rev_and_diff_hunks() {
    let tmp = TempDir::new().unwrap();
    let dir = tmp.path();
    git(dir, &["init", "-b", "main"]);
    git(dir, &["config", "user.email", "test@example.com"]);
    git(dir, &["config", "user.name", "Test"]);
    write(dir, "x.py", "a\nb\nc\n");
    git(dir, &["add", "x.py"]);
    git(dir, &["commit", "-m", "one"]);
    let c1 = git_out(dir, &["rev-parse", "HEAD"]);
    write(dir, "x.py", "a\nB\nc\n");
    git(dir, &["add", "x.py"]);
    git(dir, &["commit", "-m", "two"]);
    let c2 = git_out(dir, &["rev-parse", "HEAD"]);

    let repo = GixRepo::open(dir).unwrap();
    let old = Sha::from_hex(&c1);
    let new = Sha::from_hex(&c2);
    let blob = repo.file_at_rev(&old, "x.py").unwrap().expect("blob");
    assert_eq!(std::str::from_utf8(&blob).unwrap(), "a\nb\nc\n");
    assert!(repo.file_at_rev(&old, "missing.py").unwrap().is_none());

    let hunks = repo.diff_hunks(&old, &new).unwrap();
    assert_eq!(hunks.len(), 1);
    assert_eq!(hunks[0].path, "x.py");
    assert_eq!(hunks[0].change_type, 'M');
    assert!(!hunks[0].old_line_ranges.is_empty() || !hunks[0].new_line_ranges.is_empty());

    let paths = repo.list_paths_at_rev(&new).unwrap();
    assert!(paths.iter().any(|p| p == "x.py"));
}
