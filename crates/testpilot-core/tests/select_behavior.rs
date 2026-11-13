//! Select acceptance tests (empty diff, snapshot, force, deletions, determinism).

use std::cell::Cell;
use std::collections::HashMap;
use std::path::PathBuf;
use std::rc::Rc;

use bytes::Bytes;
use testpilot_core::features::{compute_dump_rows, snapshot_missing_message};
use testpilot_core::git::{CommitMeta, DiffHunk, FileChange, GitRepo, LineRange, Sha};
use testpilot_core::lgbm::load_model_dir;
use testpilot_core::select::{Budget, SafeOpts, format_lines, run_select};
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

    fn list_paths_at_rev(&self, sha: &Sha) -> testpilot_core::error::Result<Vec<String>> {
        let rev = sha.as_str();
        let mut paths: Vec<String> = self
            .files
            .keys()
            .filter(|(r, _)| r == rev)
            .map(|(_, p)| p.clone())
            .collect();
        if paths.is_empty() {
            paths = vec!["pkg/a.py".into(), "tests/test_many.py".into()];
        }
        paths.sort();
        paths.dedup();
        Ok(paths)
    }
}

fn week4_safe() -> SafeOpts {
    SafeOpts {
        enabled: false,
        ..SafeOpts::default()
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
    repo.insert_file("0100", "pkg/a.py", "def f():\n    return 2\n");
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
        partial: false,
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
        &week4_safe(),
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
        &week4_safe(),
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
        &week4_safe(),
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
        &week4_safe(),
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
        partial: false,
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
        &week4_safe(),
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
                partial: false,
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
        &week4_safe(),
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
        &week4_safe(),
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
        &week4_safe(),
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
        &week4_safe(),
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
        &week4_safe(),
    )
    .unwrap();
    let ja = serde_json::to_string(&a.payload).unwrap();
    let jb = serde_json::to_string(&b.payload).unwrap();
    assert_eq!(ja, jb);
    let la = format_lines(&a.payload.tests);
    let lb = format_lines(&b.payload.tests);
    assert_eq!(la, lb);
}

fn pkg_a_hunk() -> DiffHunk {
    DiffHunk {
        path: "pkg/a.py".into(),
        old_path: None,
        change_type: 'M',
        old_line_ranges: vec![LineRange { start: 1, end: 1 }],
        new_line_ranges: vec![LineRange { start: 1, end: 1 }],
        is_binary: false,
    }
}

fn run_fixture_select(
    repo: &SelectFixtureRepo,
    store: &Store,
    booster: &testpilot_core::lgbm::Booster,
    base: &Sha,
    head: &Sha,
    hunks: &[DiffHunk],
    budget: &Budget,
    safe: &SafeOpts,
) -> testpilot_core::select::SelectOutput {
    run_select(
        repo,
        None,
        store,
        "fixture",
        booster,
        base,
        head,
        head.as_str(),
        hunks,
        false,
        budget,
        safe,
    )
    .unwrap()
}

#[test]
fn no_safe_json_week4_keys_plus_safe_block_only() {
    let booster = load_model_dir(&fixture_model()).unwrap();
    let (repo, store) = seed_snapshot_repo(120);
    let base = Sha::from_hex("0099");
    let head = Sha::from_hex("0100");
    let out = run_fixture_select(
        &repo,
        &store,
        &booster,
        &base,
        &head,
        &[pkg_a_hunk()],
        &Budget::Frac(0.2),
        &week4_safe(),
    );
    assert!(!out.payload.safe.enabled);
    assert!(!out.payload.safe.fallback_all);
    assert!(out.payload.safe.reasons.is_empty());
    assert_eq!(out.payload.n_safe_added, 0);
    let v: serde_json::Value = serde_json::to_value(&out.payload).unwrap();
    let obj = v.as_object().unwrap();
    let week4_keys = [
        "model_sha256",
        "features_version",
        "base",
        "head",
        "base_run_sha",
        "n_candidates",
        "n_selected",
        "n_forced",
        "budget",
        "tests",
    ];
    for k in week4_keys {
        assert!(obj.contains_key(k), "missing week-4 key {k}");
    }
    let allowed_extra = ["safe", "n_safe_added", "n_ranked"];
    for k in obj.keys() {
        let ok = week4_keys.contains(&k.as_str()) || allowed_extra.contains(&k.as_str());
        assert!(ok, "unexpected JSON key {k}");
    }
}

#[test]
fn fallback_all_config_surface() {
    let booster = load_model_dir(&fixture_model()).unwrap();
    let (repo, store) = seed_snapshot_repo(120);
    let base = Sha::from_hex("0099");
    let head = Sha::from_hex("0100");
    let hunks = vec![DiffHunk {
        path: "pyproject.toml".into(),
        old_path: None,
        change_type: 'M',
        old_line_ranges: vec![LineRange { start: 1, end: 1 }],
        new_line_ranges: vec![LineRange { start: 1, end: 1 }],
        is_binary: false,
    }];
    let out = run_fixture_select(
        &repo,
        &store,
        &booster,
        &base,
        &head,
        &hunks,
        &Budget::Count(3),
        &SafeOpts::default(),
    );
    assert!(out.payload.safe.fallback_all);
    assert!(out
        .payload
        .safe
        .reasons
        .contains(&"config_surface".to_string()));
    assert_eq!(out.payload.n_selected, out.payload.n_candidates);
    assert!(
        out.payload
            .tests
            .iter()
            .all(|t| t.reason == "fallback_all")
    );
}

#[test]
fn fallback_all_few_candidates() {
    let booster = load_model_dir(&fixture_model()).unwrap();
    let (repo, store) = seed_snapshot_repo(120);
    let safe = SafeOpts {
        min_candidates: 200,
        ..SafeOpts::default()
    };
    let out = run_fixture_select(
        &repo,
        &store,
        &booster,
        &Sha::from_hex("0099"),
        &Sha::from_hex("0100"),
        &[pkg_a_hunk()],
        &Budget::Count(5),
        &safe,
    );
    assert!(
        out.payload
            .safe
            .reasons
            .contains(&"few_candidates".to_string())
    );
}

#[test]
fn fallback_all_src_frac() {
    let booster = load_model_dir(&fixture_model()).unwrap();
    let (repo, store) = seed_snapshot_repo(120);
    let out = run_fixture_select(
        &repo,
        &store,
        &booster,
        &Sha::from_hex("0099"),
        &Sha::from_hex("0100"),
        &[pkg_a_hunk()],
        &Budget::Count(5),
        &SafeOpts::default(),
    );
    assert!(
        out.payload
            .safe
            .reasons
            .contains(&"src_frac".to_string())
    );
}

#[test]
fn fallback_all_non_py_source() {
    let booster = load_model_dir(&fixture_model()).unwrap();
    let (mut repo, store) = seed_snapshot_repo(120);
    repo.insert_file("0100", "src/pkg/templates/x.html", "<html/>\n");
    let hunks = vec![DiffHunk {
        path: "src/pkg/templates/x.html".into(),
        old_path: None,
        change_type: 'M',
        old_line_ranges: vec![LineRange { start: 1, end: 1 }],
        new_line_ranges: vec![LineRange { start: 1, end: 1 }],
        is_binary: false,
    }];
    let out = run_fixture_select(
        &repo,
        &store,
        &booster,
        &Sha::from_hex("0099"),
        &Sha::from_hex("0100"),
        &hunks,
        &Budget::Count(5),
        &SafeOpts::default(),
    );
    assert!(
        out.payload
            .safe
            .reasons
            .contains(&"non_py_source".to_string())
    );
}

fn full_many_run_results(fail_test: Option<&str>, outcome: &str) -> Vec<TestResultRow> {
    let mut ids = Vec::new();
    for i in 0..100 {
        let tid = format!("tests/test_many.py::test_{i}");
        let oc = if fail_test == Some(tid.as_str()) {
            outcome
        } else {
            "pass"
        };
        ids.push(TestResultRow {
            test_id: tid,
            outcome: oc.into(),
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
    ids
}

fn insert_full_run(store: &mut Store, sha: &str, results: &[TestResultRow]) {
    store
        .insert_run_atomic(
            &TestRunRow {
                sha: sha.into(),
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
            results,
            &[],
            false,
        )
        .unwrap();
}

#[test]
fn recent_fail_uses_base_ancestor_not_head() {
    let booster = load_model_dir(&fixture_model()).unwrap();
    let (repo, mut store) = seed_snapshot_repo(120);
    insert_full_run(
        &mut store,
        "0098",
        &full_many_run_results(Some("tests/test_many.py::test_1"), "fail"),
    );
    insert_full_run(
        &mut store,
        "0100",
        &full_many_run_results(Some("tests/test_many.py::test_0"), "fail"),
    );
    let safe = SafeOpts {
        src_frac_threshold: 2.0,
        ..SafeOpts::default()
    };
    let out = run_fixture_select(
        &repo,
        &store,
        &booster,
        &Sha::from_hex("0099"),
        &Sha::from_hex("0100"),
        &[pkg_a_hunk()],
        &Budget::Frac(1.0),
        &safe,
    );
    let reason = |id: &str| {
        out.payload
            .tests
            .iter()
            .find(|t| t.test_id == id)
            .map(|t| t.reason.as_str())
    };
    assert_eq!(
        reason("tests/test_many.py::test_1"),
        Some("recent_fail"),
        "base~1 failure must be recent_fail"
    );
    assert_ne!(
        reason("tests/test_many.py::test_0"),
        Some("recent_fail"),
        "head-only failure must not be recent_fail"
    );
}

#[test]
fn partial_select_run_does_not_change_no_safe_json() {
    let booster = load_model_dir(&fixture_model()).unwrap();
    let (repo, mut store) = seed_snapshot_repo(120);
    let base = Sha::from_hex("0099");
    let head = Sha::from_hex("0100");
    let before = run_fixture_select(
        &repo,
        &store,
        &booster,
        &base,
        &head,
        &[pkg_a_hunk()],
        &Budget::Frac(0.2),
        &week4_safe(),
    );
    let before_json = serde_json::to_string(&before.payload).unwrap();
    // Simulate a successful select subset recorded on the same base history.
    store
        .insert_run_atomic(
            &TestRunRow {
                sha: "0050".into(),
                source: "pytest".into(),
                started_at: 10,
                finished_at: Some(11),
                dirty: false,
                diff_hash: None,
                input_hash: Some("partial-select".into()),
                meta: None,
                synthetic: false,
                partial: true,
            },
            &full_many_run_results(None, "pass")[..3],
            &[],
            false,
        )
        .unwrap();
    let after = run_fixture_select(
        &repo,
        &store,
        &booster,
        &base,
        &head,
        &[pkg_a_hunk()],
        &Budget::Frac(0.2),
        &week4_safe(),
    );
    let after_json = serde_json::to_string(&after.payload).unwrap();
    assert_eq!(
        before_json, after_json,
        "partial=1 select run must not change subsequent --no-safe select JSON"
    );
}
