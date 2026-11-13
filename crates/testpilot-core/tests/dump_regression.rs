//! A5: real-only dump matches committed golden; ±`--include-mutants` identical on real slice.

use std::path::PathBuf;
use std::process::Command;

use tempfile::TempDir;
use testpilot_core::features::dump_parquet_ex;
use testpilot_core::git::GixRepo;
use testpilot_core::store::Store;

fn workspace_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..")
}

fn golden_path() -> PathBuf {
    workspace_root().join("tests/fixtures/features_dump/pairs_golden.parquet")
}

fn generate_fixture(repo: &std::path::Path) {
    let script = workspace_root().join("scripts/generate_features_fixture.py");
    let status = Command::new("python3")
        .arg(&script)
        .arg("--out")
        .arg(repo)
        .status()
        .expect("spawn generate_features_fixture.py");
    assert!(
        status.success(),
        "generate_features_fixture.py failed with {status:?}"
    );
}

fn dump_bytes(repo: &std::path::Path, include_mutants: bool) -> Vec<u8> {
    let store = Store::open(&repo.join(".testpilot/db.sqlite")).expect("open db");
    let grepo = GixRepo::open(repo).expect("open repo");
    let repo_name = repo
        .file_name()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_else(|| "fixture".into());
    let tmp = tempfile::NamedTempFile::new().expect("temp parquet");
    if include_mutants {
        dump_parquet_ex(&grepo, &store, &repo_name, tmp.path(), Some(&grepo))
            .expect("dump with mutants");
    } else {
        dump_parquet_ex(&grepo, &store, &repo_name, tmp.path(), None).expect("dump");
    }
    std::fs::read(tmp.path()).expect("read parquet bytes")
}

#[test]
fn dump_matches_committed_golden() {
    let golden = golden_path();
    assert!(
        golden.is_file(),
        "missing golden at {}",
        golden.display()
    );

    let tmp = TempDir::new().unwrap();
    let repo = tmp.path().join("repo");
    generate_fixture(&repo);

    let fresh = dump_bytes(&repo, false);
    let expected = std::fs::read(&golden).expect("read golden");
    assert_eq!(
        fresh.len(),
        expected.len(),
        "dump size drift (regenerate golden only if intentional)"
    );
    assert_eq!(fresh, expected, "dump bytes differ from golden parquet");
}

#[test]
fn dump_real_slice_identical_with_synthetic_in_db() {
    let tmp = TempDir::new().unwrap();
    let repo = tmp.path().join("repo");
    generate_fixture(&repo);

    let baseline = dump_bytes(&repo, false);

    let db_path = repo.join(".testpilot/db.sqlite");
    let conn = rusqlite::Connection::open(&db_path).expect("sqlite");
    let sha: String = conn
        .query_row("SELECT sha FROM commits LIMIT 1", [], |r| r.get(0))
        .expect("commit sha");
    conn.execute(
        "INSERT INTO test_runs(sha,source,started_at,finished_at,dirty,synthetic) VALUES (?,?,1,2,0,1)",
        rusqlite::params![sha, "junit"],
    )
    .expect("insert synthetic run");
    let run_id = conn.last_insert_rowid();
    let test_pk: i64 = conn
        .query_row("SELECT id FROM tests LIMIT 1", [], |r| r.get(0))
        .expect("test pk");
    conn.execute(
        "INSERT INTO test_results(run_id,test_pk,outcome,duration_ms,flaky,attempts) VALUES (?,?,?,?,0,1)",
        rusqlite::params![run_id, test_pk, "fail", 1],
    )
    .expect("insert synthetic result");
    drop(conn);

    let with_mutant_db = dump_bytes(&repo, false);
    let with_flag = dump_bytes(&repo, true);

    assert_eq!(
        baseline, with_mutant_db,
        "synthetic rows in DB must not change Phase A dump"
    );
    assert_eq!(
        baseline, with_flag,
        "include-mutants without refs must not change real slice bytes"
    );
}
