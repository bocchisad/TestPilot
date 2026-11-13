//! Select acceptance tests (empty diff, snapshot, force, deletions, determinism).

use std::cell::Cell;
use std::collections::HashMap;
use std::path::PathBuf;
use std::rc::Rc;

use bytes::Bytes;
use testpilot_core::features::{compute_dump_rows, snapshot_missing_message};
use testpilot_core::git::{CommitMeta, DiffHunk, FileChange, GitRepo, LineRange, Sha};
use testpilot_core::lgbm::load_model_dir;
use testpilot_core::select::{Budget, format_lines, run_select};
use testpilot_core::store::{Store, TestResultRow, TestRunRow};

fn fixture_model() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../tests/fixtures/reference_model")
}

struct SelectFixtureRepo {
    files: HashMap<(String, String), String>,
    commits: Vec<CommitMeta>,
    walk_calls: Rc<Cell<usize>>,
}

impl SelectFixtureRepo {
    fn insert_file(&mut self, rev: &str, path: &str, src: &str) {
        self.files
            .insert((rev.to_string(), path.to_string()), src.to_string());
    }
}

impl GitRepo for SelectFixtureRepo {
    fn rev_parse(&self, rev: &str) -> testpilot_core::error::Result<Sha> {
        Ok(Sha::from_hex(rev.to_string()))
    }

    fn commit_meta_for(&self, sha: &Sha) -> testpilot_core::error::Result<CommitMeta> {
        self.commits
            .iter()
            .find(|c| c.sha.as_str() == sha.as_str())
            .cloned()
            .ok_or_else(|| testpilot_core::error::Error::Git(format!("unknown {sha}")))
    }

    fn walk_topo_old_to_new(
        &self,
        _: Option<&str>,
        _: Option<i64>,
    ) -> testpilot_core::error::Result<Vec<CommitMeta>> {
        self.walk_calls.set(self.walk_calls.get() + 1);
        Ok(self.commits.clone())
    }

    fn diff_against_first_parent(&self, _: &Sha) -> testpilot_core::error::Result<Vec<FileChange>> {
        Ok(vec![])
    }

    fn file_at_rev(&self, sha: &Sha, path: &str) -> testpilot_core::error::Result<Option<Bytes>> {
        Ok(self
            .files
            .get(&(sha.as_str().to_string(), path.to_string()))
            .map(|s| Bytes::from(s.clone())))
    }

    fn diff_hunks(&self, _old: &Sha, _new: &Sha) -> testpilot_core::error::Result<Vec<DiffHunk>> {
        Ok(vec![DiffHunk {
            path: "pkg/a.py".into(),
            old_path: None,
            change_type: 'M',
            old_line_ranges: vec![LineRange { start: 2, end: 2 }],
            new_line_ranges: vec![LineRange { start: 2, end: 2 }],
            is_binary: false,
        }])
    }

    fn list_paths_at_rev(&self, _: &Sha) -> testpilot_core::error::Result<Vec<String>> {
        Ok(vec!["pkg/a.py".into(), "tests/test_many.py".into()])
    }
}

fn many_tests_source(n: usize) -> String {
    let mut s = String::new();
    for i in 0..n {
        s.push_str(&format!("def test_{i}():\n    assert {i} == {i}\n\n"));
    }
    s.push_str("def test_touched():\n    x = 1\n    return x\n");
    s
}

fn seed_snapshot_repo(n_commits: usize) -> (SelectFixtureRepo, Store) {
    let walk_calls = Rc::new(Cell::new(0usize));
    let mut commits = Vec::new();
    for i in 0..n_commits {
        commits.push(CommitMeta {
            sha: Sha::from_hex(format!("{i:04}")),
            author_ts: i as i64,
            message: format!("c{i}"),
            parents: if i == 0 {
                vec![]
            } else {
                vec![Sha::from_hex(format!("{:04}", i - 1))]
            },
        });
    }
    let mut repo = SelectFixtureRepo {
        files: HashMap::new(),
        commits,
        walk_calls: walk_calls.clone(),
    };
    repo.insert_file("0099", "pkg/a.py", "def f():\n    return 1\n");
    repo.insert_file("0100", "tests/test_many.py", &many_tests_source(100));
    let mut store = Store::open_in_memory().unwrap();
    for i in 0..n_commits {
        let parents = if i == 0 {
            vec![]
        } else {
            vec![format!("{:04}", i - 1)]
        };
        store
            .insert_commit(&testpilot_core::store::CommitRow {
                sha: format!("{:04}", i),
                author_ts: i as i64,
                message: format!("c{i}"),
                oversized: false,
                parents,
            })
            .unwrap();
    }
    let run = TestRunRow {
        sha: "0050".into(),
        source: "pytest".into(),
        started_at: 1,
        finished_at: Some(2),
        dirty: false,
        diff_hash: None,
        input_hash: None,
        meta: None,
        synthetic: false,
    };
    let mut ids = Vec::new();
    for i in 0..100 {
        ids.push(TestResultRow {
            test_id: format!("tests/test_many.py::test_{i}"),
            outcome: "pass".into(),
            duration_ms: 10,
            message: None,
            flaky: false,
            attempts: 1,
        });
    }
    ids.push(TestResultRow {
        test_id: "tests/test_many.py::test_touched".into(),
        outcome: "pass".into(),
        duration_ms: 10,
        message: None,
        flaky: false,
        attempts: 1,
    });
    store.insert_run_atomic(&run, &ids, &[], false).unwrap();
    repo.allow_walk();
    let _ = compute_dump_rows(&repo, &store, "fixture").unwrap();
    (repo, store)
}

impl SelectFixtureRepo {
    fn allow_walk(&self) {
        // walk_calls only used by dump; no flag needed — always allow in fixture
        let _ = self.walk_calls.get();
    }
}

#[test]
fn empty_diff_selects_zero_with_warning() {
    let booster = load_model_dir(&fixture_model()).unwrap();
    let (repo, store) = seed_snapshot_repo(120);
    let base = Sha::from_hex("0100");
    let head = Sha::from_hex("0101");
    let out = run_select(
        &repo,
        None,
        &store,
        "fixture",
        &booster,
        &base,
        &head,
        "0101",
        &[],
        false,
        &Budget::Frac(0.5),
    )
    .unwrap();
    assert_eq!(out.payload.n_selected, 0);
    assert!(out.stderr_warnings.iter().any(|w| w.contains("empty diff")));
}

#[test]
fn missing_snapshot_errors_with_collect_hint() {
    let booster = load_model_dir(&fixture_model()).unwrap();
    let mut repo = SelectFixtureRepo {
        files: HashMap::new(),
        commits: vec![CommitMeta {
            sha: Sha::from_hex("0001"),
            author_ts: 1,
            message: "c".into(),
            parents: vec![],
        }],
        walk_calls: Rc::new(Cell::new(0)),
    };
    repo.insert_file("0001", "pkg/a.py", "x=1\n");
    let store = Store::open_in_memory().unwrap();
    store
        .insert_commit(&testpilot_core::store::CommitRow {
            sha: "0001".into(),
            author_ts: 1,
            message: "c".into(),
            oversized: false,
            parents: vec![],
        })
        .unwrap();
    let base = Sha::from_hex("0001");
    let head = Sha::from_hex("0001");
    let hunks = vec![DiffHunk {
        path: "pkg/a.py".into(),
        old_path: None,
        change_type: 'M',
        old_line_ranges: vec![LineRange { start: 1, end: 1 }],
        new_line_ranges: vec![LineRange { start: 1, end: 1 }],
        is_binary: false,
    }];
    let err = run_select(
        &repo,
        None,
        &store,
        "fixture",
        &booster,
        &base,
        &head,
        "0001",
        &hunks,
        false,
        &Budget::Count(5),
    )
    .unwrap_err();
    let msg = format!("{err}");
    assert_eq!(msg, snapshot_missing_message("0001"));
}

#[test]
fn one_def_touch_sets_n_forced_one() {
    let booster = load_model_dir(&fixture_model()).unwrap();
    let (mut repo, store) = seed_snapshot_repo(120);
    repo.insert_file("0100", "tests/test_many.py", &many_tests_source(100));
    let base = Sha::from_hex("0099");
    let head = Sha::from_hex("0100");
    let hunks = vec![DiffHunk {
        path: "tests/test_many.py".into(),
        old_path: None,
        change_type: 'M',
        old_line_ranges: vec![LineRange {
            start: 304,
            end: 304,
        }],
        new_line_ranges: vec![LineRange {
            start: 304,
            end: 304,
        }],
        is_binary: false,
    }];
    let out = run_select(
        &repo,
        None,
        &store,
        "fixture",
        &booster,
        &base,
        &head,
        "0100",
        &hunks,
        false,
        &Budget::Count(5),
    )
    .unwrap();
    assert_eq!(out.payload.n_forced, 1);
}

#[test]
fn deleted_test_not_in_candidates() {
    let booster = load_model_dir(&fixture_model()).unwrap();
    let (repo, store) = seed_snapshot_repo(120);
    let base = Sha::from_hex("0099");
    let head = Sha::from_hex("0100");
    let hunks = vec![DiffHunk {
        path: "tests/test_many.py".into(),
        old_path: None,
        change_type: 'D',
        old_line_ranges: vec![LineRange { start: 1, end: 999 }],
        new_line_ranges: vec![],
        is_binary: false,
    }];
    let out = run_select(
        &repo,
        None,
        &store,
        "fixture",
        &booster,
        &base,
        &head,
        "0100",
        &hunks,
        false,
        &Budget::Frac(1.0),
    )
    .unwrap();
    assert_eq!(out.payload.n_candidates, 0);
}

#[test]
fn parametrized_nodeids_stay_in_candidates() {
    let booster = load_model_dir(&fixture_model()).unwrap();
    let (mut repo, mut store) = seed_snapshot_repo(120);
    let param_src = "\
import pytest\n\
@pytest.mark.parametrize(\"x\", [1, 2, 3])\n\
def test_p(x):\n\
    assert x\n\
\ndef test_plain():\n\
    assert True\n\
";
    repo.insert_file("0099", "tests/test_param.py", param_src);
    repo.insert_file("0100", "tests/test_param.py", param_src);
    repo.insert_file("0100", "tests/test_many.py", &many_tests_source(100));
    let run = TestRunRow {
        sha: "0050".into(),
        source: "pytest".into(),
        started_at: 1,
        finished_at: Some(2),
        dirty: false,
        diff_hash: None,
        input_hash: None,
        meta: None,
        synthetic: false,
    };
    // Overwrite isn't easy; insert a newer clean run at 0051 with parametrized ids.
    store
        .insert_commit(&testpilot_core::store::CommitRow {
            sha: "0051".into(),
            author_ts: 51,
            message: "c51".into(),
            oversized: false,
            parents: vec!["0050".into()],
        })
        .unwrap();
    let ids = vec![
        TestResultRow {
            test_id: "tests/test_param.py::test_p[1]".into(),
            outcome: "pass".into(),
            duration_ms: 10,
            message: None,
            flaky: false,
            attempts: 1,
        },
        TestResultRow {
            test_id: "tests/test_param.py::test_p[2]".into(),
            outcome: "pass".into(),
            duration_ms: 10,
            message: None,
            flaky: false,
            attempts: 1,
        },
        TestResultRow {
            test_id: "tests/test_param.py::test_p[3]".into(),
            outcome: "pass".into(),
            duration_ms: 10,
            message: None,
            flaky: false,
            attempts: 1,
        },
        TestResultRow {
            test_id: "tests/test_param.py::test_plain".into(),
            outcome: "pass".into(),
            duration_ms: 10,
            message: None,
            flaky: false,
            attempts: 1,
        },
    ];
    let mut run51 = run;
    run51.sha = "0051".into();
    store.insert_run_atomic(&run51, &ids, &[], false).unwrap();
    let base = Sha::from_hex("0099");
    let head = Sha::from_hex("0100");
    let hunks = vec![DiffHunk {
        path: "pkg/a.py".into(),
        old_path: None,
        change_type: 'M',
        old_line_ranges: vec![LineRange { start: 1, end: 1 }],
        new_line_ranges: vec![LineRange { start: 1, end: 1 }],
        is_binary: false,
    }];
    let out = run_select(
        &repo,
        None,
        &store,
        "fixture",
        &booster,
        &base,
        &head,
        "0100",
        &hunks,
        false,
        &Budget::Frac(1.0),
    )
    .unwrap();
    let selected: Vec<_> = out
        .payload
        .tests
        .iter()
        .map(|t| t.test_id.as_str())
        .collect();
    // Full frac → all candidates selected; parametrized ids must remain.
    assert!(
        selected
            .iter()
            .any(|t| *t == "tests/test_param.py::test_p[1]"),
        "missing parametrized child: {selected:?}"
    );
    assert!(
        selected
            .iter()
            .any(|t| *t == "tests/test_param.py::test_p[2]"),
        "{selected:?}"
    );
    assert!(
        selected
            .iter()
            .any(|t| *t == "tests/test_param.py::test_p[3]"),
        "{selected:?}"
    );
    // Bare name must not be forced as "new" when `[…]` children exist in base.
    assert_eq!(out.payload.n_forced, 0);
    assert!(!selected.iter().any(|t| *t == "tests/test_param.py::test_p"));
}

#[test]
fn deleting_one_def_does_not_force_neighbor() {
    let booster = load_model_dir(&fixture_model()).unwrap();
    let (mut repo, store) = seed_snapshot_repo(120);
    let base_src = "\
def test_choice_argument():\n\
    assert True\n\
\ndef test_choice_argument_enum():\n\
    assert True\n\
";
    let head_src = "\
def test_choice_argument_enum():\n\
    assert True\n\
";
    repo.insert_file("0099", "tests/test_choice.py", base_src);
    repo.insert_file("0100", "tests/test_choice.py", head_src);
    // Also keep many_tests so last clean run still resolves; inject choice ids via
    // extending the 0050 run is hard — seed a dedicated run at 0052.
    let mut store = store;
    store
        .insert_commit(&testpilot_core::store::CommitRow {
            sha: "0052".into(),
            author_ts: 52,
            message: "c52".into(),
            oversized: false,
            parents: vec!["0051".into()],
        })
        .unwrap();
    let ids = vec![
        TestResultRow {
            test_id: "tests/test_choice.py::test_choice_argument".into(),
            outcome: "pass".into(),
            duration_ms: 10,
            message: None,
            flaky: false,
            attempts: 1,
        },
        TestResultRow {
            test_id: "tests/test_choice.py::test_choice_argument_enum".into(),
            outcome: "pass".into(),
            duration_ms: 10,
            message: None,
            flaky: false,
            attempts: 1,
        },
    ];
    store
        .insert_run_atomic(
            &TestRunRow {
                sha: "0052".into(),
                source: "pytest".into(),
                started_at: 1,
                finished_at: Some(2),
                dirty: false,
                diff_hash: None,
                input_hash: None,
                meta: None,
                synthetic: false,
            },
            &ids,
            &[],
            false,
        )
        .unwrap();
    let base = Sha::from_hex("0099");
    let head = Sha::from_hex("0100");
    let hunks = vec![DiffHunk {
        path: "tests/test_choice.py".into(),
        old_path: None,
        change_type: 'M',
        old_line_ranges: vec![LineRange { start: 1, end: 3 }],
        new_line_ranges: vec![],
        is_binary: false,
    }];
    let out = run_select(
        &repo,
        None,
        &store,
        "fixture",
        &booster,
        &base,
        &head,
        "0100",
        &hunks,
        false,
        &Budget::Frac(1.0),
    )
    .unwrap();
    let ids: Vec<_> = out
        .payload
        .tests
        .iter()
        .map(|t| t.test_id.as_str())
        .collect();
    assert!(
        !ids.iter()
            .any(|t| *t == "tests/test_choice.py::test_choice_argument"),
        "deleted def still selected: {ids:?}"
    );
    assert!(
        ids.iter()
            .any(|t| *t == "tests/test_choice.py::test_choice_argument_enum"),
        "neighbor missing: {ids:?}"
    );
    assert_eq!(
        out.payload.n_forced, 0,
        "deleting a def must not force the next def (was n_forced={})",
        out.payload.n_forced
    );
}

#[test]
fn deleted_one_def_not_whole_file() {
    let booster = load_model_dir(&fixture_model()).unwrap();
    let (mut repo, store) = seed_snapshot_repo(120);
    let full = many_tests_source(100);
    let mut without_5 = String::new();
    for i in 0..100 {
        if i == 5 {
            continue;
        }
        without_5.push_str(&format!("def test_{i}():\n    assert {i} == {i}\n\n"));
    }
    without_5.push_str("def test_touched():\n    x = 1\n    return x\n");
    repo.insert_file("0099", "tests/test_many.py", &full);
    repo.insert_file("0100", "tests/test_many.py", &without_5);
    let base = Sha::from_hex("0099");
    let head = Sha::from_hex("0100");
    // Body of test_5 was around lines 16–18 in the full file (3 lines per test).
    let hunks = vec![DiffHunk {
        path: "tests/test_many.py".into(),
        old_path: None,
        change_type: 'M',
        old_line_ranges: vec![LineRange { start: 16, end: 18 }],
        new_line_ranges: vec![],
        is_binary: false,
    }];
    let out = run_select(
        &repo,
        None,
        &store,
        "fixture",
        &booster,
        &base,
        &head,
        "0100",
        &hunks,
        false,
        &Budget::Frac(1.0),
    )
    .unwrap();
    let ids: Vec<_> = out
        .payload
        .tests
        .iter()
        .map(|t| t.test_id.as_str())
        .collect();
    assert!(
        !ids.iter().any(|t| *t == "tests/test_many.py::test_5"),
        "deleted def still present: {ids:?}"
    );
    assert!(
        ids.iter().any(|t| *t == "tests/test_many.py::test_0"),
        "sibling should remain: {ids:?}"
    );
    assert_eq!(out.payload.n_candidates, 100); // 101 base − test_5
}

#[test]
fn lines_subset_of_candidate_nodeids() {
    let booster = load_model_dir(&fixture_model()).unwrap();
    let (repo, store) = seed_snapshot_repo(120);
    let base = Sha::from_hex("0099");
    let head = Sha::from_hex("0100");
    let hunks = vec![DiffHunk {
        path: "pkg/a.py".into(),
        old_path: None,
        change_type: 'M',
        old_line_ranges: vec![LineRange { start: 1, end: 1 }],
        new_line_ranges: vec![LineRange { start: 1, end: 1 }],
        is_binary: false,
    }];
    let out = run_select(
        &repo,
        None,
        &store,
        "fixture",
        &booster,
        &base,
        &head,
        "0100",
        &hunks,
        false,
        &Budget::Frac(0.2),
    )
    .unwrap();
    let known: std::collections::HashSet<String> = (0..100)
        .map(|i| format!("tests/test_many.py::test_{i}"))
        .chain(std::iter::once("tests/test_many.py::test_touched".into()))
        .collect();
    let lines = format_lines(&out.payload.tests);
    for line in lines.lines() {
        assert!(
            known.contains(line),
            "lines output not a pytest nodeid from fixture: {line}"
        );
    }
}

#[test]
fn json_and_lines_deterministic() {
    let booster = load_model_dir(&fixture_model()).unwrap();
    let (repo, store) = seed_snapshot_repo(120);
    let base = Sha::from_hex("0099");
    let head = Sha::from_hex("0100");
    let hunks = vec![DiffHunk {
        path: "pkg/a.py".into(),
        old_path: None,
        change_type: 'M',
        old_line_ranges: vec![LineRange { start: 1, end: 1 }],
        new_line_ranges: vec![LineRange { start: 1, end: 1 }],
        is_binary: false,
    }];
    let a = run_select(
        &repo,
        None,
        &store,
        "fixture",
        &booster,
        &base,
        &head,
        "0100",
        &hunks,
        false,
        &Budget::Frac(0.2),
    )
    .unwrap();
    let b = run_select(
        &repo,
        None,
        &store,
        "fixture",
        &booster,
        &base,
        &head,
        "0100",
        &hunks,
        false,
        &Budget::Frac(0.2),
    )
    .unwrap();
    let ja = serde_json::to_string(&a.payload).unwrap();
    let jb = serde_json::to_string(&b.payload).unwrap();
    assert_eq!(ja, jb);
    let la = format_lines(&a.payload.tests);
    let lb = format_lines(&b.payload.tests);
    assert_eq!(la, lb);
}
