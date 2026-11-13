//! W2-8/W2-9: `features show` must not replay history from root or touch commits after base.

use std::cell::Cell;
use std::cell::RefCell;
use std::rc::Rc;

use bytes::Bytes;
use testpilot_core::features::{compute_dump_rows, compute_show_rows};
use testpilot_core::git::{CommitMeta, DiffHunk, FileChange, GitRepo, LineRange, Sha};
use testpilot_core::store::{Store, TestResultRow, TestRunRow};

struct CountingRepo {
    commits: Vec<CommitMeta>,
    forbidden: Rc<RefCell<HashSetWrap>>,
    walk_calls: Rc<Cell<usize>>,
    base: String,
    /// When true, walk_topo is allowed (dump). Show must leave this false.
    allow_walk: Cell<bool>,
}

#[derive(Default)]
struct HashSetWrap {
    diff_hunks_new: Vec<String>,
    file_at_rev: Vec<String>,
    list_paths: Vec<String>,
}

impl CountingRepo {
    fn note_forbidden(&self, sha: &str, sink: impl FnOnce(&mut HashSetWrap, String)) {
        if sha == self.base {
            return;
        }
        let bi = self.base.parse::<usize>().unwrap_or(0);
        if let Ok(i) = sha.parse::<usize>() {
            if i > bi {
                sink(&mut self.forbidden.borrow_mut(), sha.to_string());
            }
        }
    }
}

impl GitRepo for CountingRepo {
    fn rev_parse(&self, rev: &str) -> testpilot_core::error::Result<Sha> {
        Ok(Sha::from_hex(rev.to_string()))
    }

    fn commit_meta_for(&self, sha: &Sha) -> testpilot_core::error::Result<CommitMeta> {
        self.commits
            .iter()
            .find(|c| c.sha.as_str() == sha.as_str())
            .cloned()
            .ok_or_else(|| testpilot_core::error::Error::Git(format!("unknown sha {}", sha)))
    }

    fn walk_topo_old_to_new(
        &self,
        _since_sha: Option<&str>,
        _since_date: Option<i64>,
    ) -> testpilot_core::error::Result<Vec<CommitMeta>> {
        self.walk_calls.set(self.walk_calls.get() + 1);
        assert!(
            self.allow_walk.get(),
            "show must not call walk_topo_old_to_new (full history materialization)"
        );
        Ok(self.commits.clone())
    }

    fn diff_against_first_parent(
        &self,
        _sha: &Sha,
    ) -> testpilot_core::error::Result<Vec<FileChange>> {
        Ok(vec![])
    }

    fn file_at_rev(&self, sha: &Sha, _path: &str) -> testpilot_core::error::Result<Option<Bytes>> {
        self.note_forbidden(sha.as_str(), |s, x| s.file_at_rev.push(x));
        Ok(Some(Bytes::from_static(b"def f():\n    return 1\n")))
    }

    fn diff_hunks(
        &self,
        _old_sha: &Sha,
        new_sha: &Sha,
    ) -> testpilot_core::error::Result<Vec<DiffHunk>> {
        self.note_forbidden(new_sha.as_str(), |s, x| s.diff_hunks_new.push(x));
        Ok(vec![DiffHunk {
            path: "pkg/a.py".into(),
            old_path: None,
            change_type: 'M',
            old_line_ranges: vec![LineRange { start: 1, end: 1 }],
            new_line_ranges: vec![LineRange { start: 1, end: 1 }],
            is_binary: false,
        }])
    }

    fn list_paths_at_rev(&self, sha: &Sha) -> testpilot_core::error::Result<Vec<String>> {
        self.note_forbidden(sha.as_str(), |s, x| s.list_paths.push(x));
        Ok(vec!["pkg/a.py".into(), "tests/test_a.py".into()])
    }
}

fn sha_n(n: usize) -> String {
    format!("{n:04}")
}

fn seed_run(store: &mut Store, i: usize) {
    let run = TestRunRow {
        sha: sha_n(i),
        source: "pytest".into(),
        started_at: 1,
        finished_at: Some(2),
        dirty: false,
        diff_hash: None,
        input_hash: None,
        meta: None,
        synthetic: false,
    };
    let results = vec![TestResultRow {
        test_id: "tests/test_a.py::test_alpha".into(),
        outcome: "pass".into(),
        duration_ms: 5,
        message: None,
        flaky: false,
        attempts: 1,
    }];
    store.insert_run_atomic(&run, &results, &[], false).unwrap();
}

#[test]
fn show_uses_snapshot_not_full_walk_and_ignores_post_base_runs() {
    const N: usize = 220;
    const BASE: usize = 100;
    const HEAD: usize = 219;

    let mut commits = Vec::new();
    for i in 0..N {
        let parents = if i == 0 {
            vec![]
        } else {
            vec![Sha::from_hex(sha_n(i - 1))]
        };
        commits.push(CommitMeta {
            sha: Sha::from_hex(sha_n(i)),
            author_ts: i as i64,
            message: format!("c{i}"),
            parents,
        });
    }

    let forbidden = Rc::new(RefCell::new(HashSetWrap::default()));
    let walk_calls = Rc::new(Cell::new(0usize));
    let repo = CountingRepo {
        commits,
        forbidden: forbidden.clone(),
        walk_calls: walk_calls.clone(),
        base: sha_n(BASE),
        allow_walk: Cell::new(true),
    };

    let mut store = Store::open_in_memory().unwrap();
    for i in 0..N {
        let parents = if i == 0 { vec![] } else { vec![sha_n(i - 1)] };
        let _ = store.insert_commit(&testpilot_core::store::CommitRow {
            sha: sha_n(i),
            author_ts: i as i64,
            message: format!("c{i}"),
            oversized: false,
            parents,
        });
    }
    // Runs before/at base (for history) AND a clean run after base (W2-9).
    for i in [1usize, 50, BASE, HEAD] {
        seed_run(&mut store, i);
    }

    // Dump writes feature_history_snapshot rows (may walk topo).
    repo.allow_walk.set(true);
    walk_calls.set(0);
    let _ = compute_dump_rows(&repo, &store, "fixture").unwrap();
    assert!(walk_calls.get() >= 1, "dump should walk topo");

    // Show must not walk topo or touch commits after base.
    repo.allow_walk.set(false);
    walk_calls.set(0);
    forbidden.borrow_mut().diff_hunks_new.clear();
    forbidden.borrow_mut().file_at_rev.clear();
    forbidden.borrow_mut().list_paths.clear();

    let base = Sha::from_hex(sha_n(BASE));
    let head = Sha::from_hex(sha_n(HEAD));
    let hunks = vec![DiffHunk {
        path: "pkg/a.py".into(),
        old_path: None,
        change_type: 'M',
        old_line_ranges: vec![LineRange { start: 1, end: 1 }],
        new_line_ranges: vec![LineRange { start: 1, end: 1 }],
        is_binary: false,
    }];

    let rows = compute_show_rows(&repo, &store, "fixture", &base, &head, &hunks).unwrap();
    assert!(!rows.is_empty());
    assert_eq!(
        walk_calls.get(),
        0,
        "show must not call walk_topo_old_to_new"
    );

    let f = forbidden.borrow();
    assert!(
        f.diff_hunks_new.is_empty(),
        "diff_hunks must not target commits after base; got {:?} \
         (W2-9: a post-base run exists — removing snapshot path would fail here)",
        f.diff_hunks_new
    );
    let head_s = sha_n(HEAD);
    assert!(
        f.file_at_rev.iter().all(|x| *x == head_s),
        "file_at_rev after base must be only head (import graph); got {:?}",
        f.file_at_rev
    );
    assert!(
        f.list_paths.iter().all(|x| *x == head_s),
        "list_paths_at_rev after base must be only head; got {:?}",
        f.list_paths
    );
}

#[test]
fn show_without_snapshots_errors_clearly() {
    // W2-12: deleting snapshots must not silently invent first-parent history.
    const N: usize = 40;
    const BASE: usize = 20;
    const HEAD: usize = 30;

    let mut commits = Vec::new();
    for i in 0..N {
        // Introduce a merge at 15: parents 14 and 10 (second parent brings side history).
        let parents = if i == 0 {
            vec![]
        } else if i == 15 {
            vec![Sha::from_hex(sha_n(14)), Sha::from_hex(sha_n(10))]
        } else {
            vec![Sha::from_hex(sha_n(i - 1))]
        };
        commits.push(CommitMeta {
            sha: Sha::from_hex(sha_n(i)),
            author_ts: i as i64,
            message: format!("c{i}"),
            parents,
        });
    }

    let repo = CountingRepo {
        commits,
        forbidden: Rc::new(RefCell::new(HashSetWrap::default())),
        walk_calls: Rc::new(Cell::new(0usize)),
        base: sha_n(BASE),
        allow_walk: Cell::new(true),
    };
    let mut store = Store::open_in_memory().unwrap();
    for i in 0..N {
        let parents = if i == 0 {
            vec![]
        } else if i == 15 {
            vec![sha_n(14), sha_n(10)]
        } else {
            vec![sha_n(i - 1)]
        };
        let _ = store.insert_commit(&testpilot_core::store::CommitRow {
            sha: sha_n(i),
            author_ts: i as i64,
            message: format!("c{i}"),
            oversized: false,
            parents,
        });
    }
    for i in [5usize, 12, BASE] {
        seed_run(&mut store, i);
    }

    let with_snap = {
        repo.allow_walk.set(true);
        let _ = compute_dump_rows(&repo, &store, "fixture").unwrap();
        assert!(store.count_feature_history_snapshots().unwrap() > 0);
        repo.allow_walk.set(false);
        let base = Sha::from_hex(sha_n(BASE));
        let head = Sha::from_hex(sha_n(HEAD));
        let hunks = vec![DiffHunk {
            path: "pkg/a.py".into(),
            old_path: None,
            change_type: 'M',
            old_line_ranges: vec![LineRange { start: 1, end: 1 }],
            new_line_ranges: vec![LineRange { start: 1, end: 1 }],
            is_binary: false,
        }];
        compute_show_rows(&repo, &store, "fixture", &base, &head, &hunks).unwrap()
    };
    assert!(!with_snap.is_empty());

    store.clear_feature_history_snapshots().unwrap();
    repo.allow_walk.set(false);
    let base = Sha::from_hex(sha_n(BASE));
    let head = Sha::from_hex(sha_n(HEAD));
    let hunks = vec![DiffHunk {
        path: "pkg/a.py".into(),
        old_path: None,
        change_type: 'M',
        old_line_ranges: vec![LineRange { start: 1, end: 1 }],
        new_line_ranges: vec![LineRange { start: 1, end: 1 }],
        is_binary: false,
    }];
    let err = compute_show_rows(&repo, &store, "fixture", &base, &head, &hunks).unwrap_err();
    let msg = err.to_string();
    assert!(
        msg.contains("no feature snapshot")
            && msg.contains("collect git")
            && msg.contains("features dump"),
        "expected A9 snapshot hint, got: {msg}"
    );
}

#[test]
fn show_base_behind_head_uses_last_clean_run_at_or_before_base() {
    // W2-15: base far behind head still returns tests from last clean run ≤ base.
    const N: usize = 30;
    const RUN_AT: usize = 5;
    const BASE: usize = 20;
    const HEAD: usize = 29;

    let mut commits = Vec::new();
    for i in 0..N {
        let parents = if i == 0 {
            vec![]
        } else {
            vec![Sha::from_hex(sha_n(i - 1))]
        };
        commits.push(CommitMeta {
            sha: Sha::from_hex(sha_n(i)),
            author_ts: i as i64,
            message: format!("c{i}"),
            parents,
        });
    }
    let repo = CountingRepo {
        commits,
        forbidden: Rc::new(RefCell::new(HashSetWrap::default())),
        walk_calls: Rc::new(Cell::new(0usize)),
        base: sha_n(BASE),
        allow_walk: Cell::new(true),
    };
    let mut store = Store::open_in_memory().unwrap();
    for i in 0..N {
        let parents = if i == 0 { vec![] } else { vec![sha_n(i - 1)] };
        let _ = store.insert_commit(&testpilot_core::store::CommitRow {
            sha: sha_n(i),
            author_ts: i as i64,
            message: format!("c{i}"),
            oversized: false,
            parents,
        });
    }
    seed_run(&mut store, RUN_AT);
    // No runs between RUN_AT and HEAD — show(base=20) must still find run at 5.
    let _ = compute_dump_rows(&repo, &store, "fixture").unwrap();

    repo.allow_walk.set(false);
    let base = Sha::from_hex(sha_n(BASE));
    let head = Sha::from_hex(sha_n(HEAD));
    let hunks = vec![DiffHunk {
        path: "pkg/a.py".into(),
        old_path: None,
        change_type: 'M',
        old_line_ranges: vec![LineRange { start: 1, end: 1 }],
        new_line_ranges: vec![LineRange { start: 1, end: 1 }],
        is_binary: false,
    }];
    let rows = compute_show_rows(&repo, &store, "fixture", &base, &head, &hunks).unwrap();
    assert!(
        !rows.is_empty(),
        "expected tests from clean run at {RUN_AT} (≤ base {BASE})"
    );
    assert_eq!(rows[0].test, "tests/test_a.py::test_alpha");
}
