//! W2-1: `features show` must not scan commits strictly after `--base`.

use std::cell::RefCell;
use std::rc::Rc;

use bytes::Bytes;
use testpilot_core::features::compute_show_rows;
use testpilot_core::git::{CommitMeta, DiffHunk, FileChange, GitRepo, LineRange, Sha};
use testpilot_core::store::{Store, TestResultRow, TestRunRow};

struct CountingRepo {
    commits: Vec<CommitMeta>,
    /// SHAs strictly after base that must not appear in diff_hunks / file_at_rev / list_paths.
    forbidden: Rc<RefCell<HashSetWrap>>,
    base: String,
}

#[derive(Default)]
struct HashSetWrap {
    diff_hunks_new: Vec<String>,
    file_at_rev: Vec<String>,
    list_paths: Vec<String>,
}

impl CountingRepo {
    fn note_forbidden(&self, sha: &str, sink: impl FnOnce(&mut HashSetWrap, String)) {
        if sha != self.base {
            // topo: hex padded index; forbid if index > base index
            let bi = self.base.parse::<usize>().unwrap_or(0);
            if let Ok(i) = sha.parse::<usize>() {
                if i > bi {
                    sink(&mut self.forbidden.borrow_mut(), sha.to_string());
                }
            }
        }
    }
}

impl GitRepo for CountingRepo {
    fn rev_parse(&self, rev: &str) -> testpilot_core::error::Result<Sha> {
        Ok(Sha::from_hex(rev.to_string()))
    }

    fn walk_topo_old_to_new(
        &self,
        _since_sha: Option<&str>,
        _since_date: Option<i64>,
    ) -> testpilot_core::error::Result<Vec<CommitMeta>> {
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

#[test]
fn show_does_not_scan_commits_after_base() {
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
    let repo = CountingRepo {
        commits,
        forbidden: forbidden.clone(),
        base: sha_n(BASE),
    };

    let mut store = Store::open_in_memory().unwrap();
    // Seed commits + one clean run on BASE so show has tests.
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
    // Runs on a few early commits + base so history updates have work.
    for i in [1usize, 50, BASE] {
        let run = TestRunRow {
            sha: sha_n(i),
            source: "pytest".into(),
            started_at: 1,
            finished_at: Some(2),
            dirty: false,
            diff_hash: None,
            input_hash: None,
            meta: None,
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

    let _rows = compute_show_rows(&repo, &store, "fixture", &base, &head, &hunks).unwrap();

    let f = forbidden.borrow();
    assert!(
        f.diff_hunks_new.is_empty(),
        "diff_hunks must not target commits after base; got {:?}",
        f.diff_hunks_new
    );
    assert!(
        f.file_at_rev.is_empty(),
        "file_at_rev must not touch commits after base; got {:?}",
        f.file_at_rev
    );
    assert!(
        f.list_paths.is_empty(),
        "list_paths_at_rev must not touch commits after base; got {:?}",
        f.list_paths
    );
}
