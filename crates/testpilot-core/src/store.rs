use std::path::Path;

use rusqlite::{Connection, OptionalExtension, Transaction, params};

use crate::error::{Error, Result};

pub const SCHEMA_VERSION: i64 = 2;
pub const SCHEMA_SQL: &str = include_str!("../schema.sql");

fn schema_version_error(found: i64, expected: i64) -> Error {
    if found < expected {
        Error::SchemaOutdated { found, expected }
    } else {
        Error::SchemaTooNew { found, expected }
    }
}

#[derive(Debug, Clone)]
pub struct CommitRow {
    pub sha: String,
    pub author_ts: i64,
    pub message: String,
    pub oversized: bool,
    pub parents: Vec<String>,
}

#[derive(Debug, Clone)]
pub struct FileChangeRow {
    pub sha: String,
    pub path: String,
    pub change_type: String,
    pub old_path: Option<String>,
    pub lines_added: i64,
    pub lines_deleted: i64,
    pub is_binary: bool,
}

#[derive(Debug, Clone)]
pub struct TestRunRow {
    pub sha: String,
    pub source: String,
    pub started_at: i64,
    pub finished_at: Option<i64>,
    pub dirty: bool,
    pub diff_hash: Option<String>,
    pub input_hash: Option<String>,
    pub meta: Option<String>,
}

#[derive(Debug, Clone)]
pub struct TestResultRow {
    pub test_id: String,
    pub outcome: String,
    pub duration_ms: i64,
    pub message: Option<String>,
    pub flaky: bool,
    pub attempts: i64,
}

#[derive(Debug, Clone)]
pub struct CoverageRow {
    pub test_id: String,
    pub path: String,
    pub lines: Vec<u8>,
}

pub struct Store {
    conn: Connection,
}

impl Store {
    pub fn open(path: &Path) -> Result<Self> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let conn = Connection::open(path)?;
        let store = Self { conn };
        store.apply_pragmas()?;
        store.migrate()?;
        Ok(store)
    }

    pub fn open_in_memory() -> Result<Self> {
        let conn = Connection::open_in_memory()?;
        let store = Self { conn };
        store.apply_pragmas()?;
        store.migrate()?;
        Ok(store)
    }

    fn apply_pragmas(&self) -> Result<()> {
        self.conn.pragma_update(None, "journal_mode", "WAL")?;
        self.conn.pragma_update(None, "foreign_keys", true)?;
        self.conn
            .busy_timeout(std::time::Duration::from_millis(5000))?;
        Ok(())
    }

    pub fn migrate(&self) -> Result<()> {
        // N1: if a prior schema_version row exists, validate it *before* applying
        // DDL — CREATE INDEX IF NOT EXISTS on new columns would otherwise fail
        // on a v1 database with a cryptic "no such column" error.
        let existing: Option<i64> = self
            .conn
            .query_row("SELECT version FROM schema_version LIMIT 1", [], |r| {
                r.get(0)
            })
            .optional()
            .unwrap_or(None);

        if let Some(v) = existing {
            if v == SCHEMA_VERSION {
                return Ok(());
            }
            return Err(schema_version_error(v, SCHEMA_VERSION));
        }

        let tx = self.conn.unchecked_transaction()?;
        tx.execute_batch(SCHEMA_SQL)?;

        let version: Option<i64> = tx
            .query_row("SELECT version FROM schema_version LIMIT 1", [], |r| {
                r.get(0)
            })
            .optional()?;

        match version {
            None => {
                tx.execute(
                    "INSERT INTO schema_version(version) VALUES (?1)",
                    params![SCHEMA_VERSION],
                )?;
            }
            Some(v) if v == SCHEMA_VERSION => {}
            Some(v) => {
                return Err(schema_version_error(v, SCHEMA_VERSION));
            }
        }
        tx.commit()?;
        Ok(())
    }

    pub fn connection(&self) -> &Connection {
        &self.conn
    }

    pub fn with_transaction<F, T>(&mut self, f: F) -> Result<T>
    where
        F: FnOnce(&Transaction<'_>) -> Result<T>,
    {
        let tx = self.conn.transaction()?;
        let result = f(&tx)?;
        tx.commit()?;
        Ok(result)
    }

    pub fn commit_exists_on(conn: &Connection, sha: &str) -> Result<bool> {
        let n: i64 = conn.query_row(
            "SELECT COUNT(*) FROM commits WHERE sha = ?1",
            params![sha],
            |r| r.get(0),
        )?;
        Ok(n > 0)
    }

    pub fn commit_exists(&self, sha: &str) -> Result<bool> {
        Self::commit_exists_on(&self.conn, sha)
    }

    /// Insert commit + parents. Returns false if sha already present.
    pub fn insert_commit_on(conn: &Connection, commit: &CommitRow) -> Result<bool> {
        let changed = conn.execute(
            "INSERT OR IGNORE INTO commits(sha, author_ts, message, oversized)
             VALUES (?1, ?2, ?3, ?4)",
            params![
                commit.sha,
                commit.author_ts,
                commit.message,
                commit.oversized as i64
            ],
        )?;
        if changed == 0 {
            return Ok(false);
        }
        for (idx, parent) in commit.parents.iter().enumerate() {
            conn.execute(
                "INSERT INTO commit_parents(sha, parent_sha, parent_idx)
                 VALUES (?1, ?2, ?3)",
                params![commit.sha, parent, idx as i64],
            )?;
        }
        Ok(true)
    }

    pub fn insert_commit(&self, commit: &CommitRow) -> Result<bool> {
        Self::insert_commit_on(&self.conn, commit)
    }

    pub fn insert_file_changes_on(conn: &Connection, changes: &[FileChangeRow]) -> Result<()> {
        for c in changes {
            conn.execute(
                "INSERT INTO file_changes(
                    sha, path, change_type, old_path,
                    lines_added, lines_deleted, is_binary
                 ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
                params![
                    c.sha,
                    c.path,
                    c.change_type,
                    c.old_path,
                    c.lines_added,
                    c.lines_deleted,
                    c.is_binary as i64
                ],
            )?;
        }
        Ok(())
    }

    pub fn insert_file_changes(&self, changes: &[FileChangeRow]) -> Result<()> {
        Self::insert_file_changes_on(&self.conn, changes)
    }

    pub fn upsert_test(conn: &Connection, test_id: &str) -> Result<i64> {
        conn.execute(
            "INSERT OR IGNORE INTO tests(test_id) VALUES (?1)",
            params![test_id],
        )?;
        let id: i64 = conn.query_row(
            "SELECT id FROM tests WHERE test_id = ?1",
            params![test_id],
            |r| r.get(0),
        )?;
        Ok(id)
    }

    pub fn delete_run_by_input_hash(
        conn: &Connection,
        source: &str,
        sha: &str,
        input_hash: &str,
    ) -> Result<Option<i64>> {
        let id: Option<i64> = conn
            .query_row(
                "SELECT id FROM test_runs
                 WHERE source = ?1 AND sha = ?2 AND input_hash = ?3",
                params![source, sha, input_hash],
                |r| r.get(0),
            )
            .optional()?;
        if let Some(run_id) = id {
            conn.execute(
                "DELETE FROM test_coverage WHERE run_id = ?1",
                params![run_id],
            )?;
            conn.execute(
                "DELETE FROM test_results WHERE run_id = ?1",
                params![run_id],
            )?;
            conn.execute("DELETE FROM test_runs WHERE id = ?1", params![run_id])?;
            Ok(Some(run_id))
        } else {
            Ok(None)
        }
    }

    pub fn insert_test_run_on(conn: &Connection, run: &TestRunRow) -> Result<Option<i64>> {
        let result = conn.execute(
            "INSERT INTO test_runs(
                sha, source, started_at, finished_at, dirty, diff_hash, input_hash, meta
             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
            params![
                run.sha,
                run.source,
                run.started_at,
                run.finished_at,
                run.dirty as i64,
                run.diff_hash,
                run.input_hash,
                run.meta
            ],
        );
        match result {
            Ok(_) => Ok(Some(conn.last_insert_rowid())),
            Err(rusqlite::Error::SqliteFailure(e, _))
                if e.code == rusqlite::ErrorCode::ConstraintViolation =>
            {
                Ok(None)
            }
            Err(e) => Err(e.into()),
        }
    }

    pub fn insert_test_run(&self, run: &TestRunRow) -> Result<Option<i64>> {
        Self::insert_test_run_on(&self.conn, run)
    }

    pub fn insert_test_results_on(
        conn: &Connection,
        run_id: i64,
        results: &[TestResultRow],
    ) -> Result<()> {
        for r in results {
            let pk = Self::upsert_test(conn, &r.test_id)?;
            conn.execute(
                "INSERT INTO test_results(
                    run_id, test_pk, outcome, duration_ms, message, flaky, attempts
                 ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
                params![
                    run_id,
                    pk,
                    r.outcome,
                    r.duration_ms,
                    r.message,
                    r.flaky as i64,
                    r.attempts
                ],
            )?;
        }
        Ok(())
    }

    pub fn insert_test_results(&self, run_id: i64, results: &[TestResultRow]) -> Result<()> {
        Self::insert_test_results_on(&self.conn, run_id, results)
    }

    pub fn insert_coverage_on(conn: &Connection, run_id: i64, rows: &[CoverageRow]) -> Result<()> {
        for c in rows {
            let pk = Self::upsert_test(conn, &c.test_id)?;
            conn.execute(
                "INSERT INTO test_coverage(run_id, test_pk, path, lines)
                 VALUES (?1, ?2, ?3, ?4)",
                params![run_id, pk, c.path, c.lines],
            )?;
        }
        Ok(())
    }

    pub fn insert_coverage(&self, run_id: i64, rows: &[CoverageRow]) -> Result<()> {
        Self::insert_coverage_on(&self.conn, run_id, rows)
    }

    /// Insert a full run (results + coverage) in one transaction.
    pub fn insert_run_atomic(
        &mut self,
        run: &TestRunRow,
        results: &[TestResultRow],
        coverage: &[CoverageRow],
        force: bool,
    ) -> Result<Option<i64>> {
        self.with_transaction(|tx| {
            if force {
                if let Some(hash) = run.input_hash.as_deref() {
                    Self::delete_run_by_input_hash(tx, &run.source, &run.sha, hash)?;
                }
            }
            let Some(run_id) = Self::insert_test_run_on(tx, run)? else {
                return Ok(None);
            };
            Self::insert_test_results_on(tx, run_id, results)?;
            Self::insert_coverage_on(tx, run_id, coverage)?;
            Ok(Some(run_id))
        })
    }

    pub fn find_run_by_input_hash(
        &self,
        source: &str,
        sha: &str,
        input_hash: &str,
    ) -> Result<Option<i64>> {
        self.conn
            .query_row(
                "SELECT id FROM test_runs
                 WHERE source = ?1 AND sha = ?2 AND input_hash = ?3",
                params![source, sha, input_hash],
                |r| r.get(0),
            )
            .optional()
            .map_err(Into::into)
    }

    pub fn latest_run_id_for_sha(&self, sha: &str) -> Result<Option<i64>> {
        self.conn
            .query_row(
                "SELECT id FROM test_runs WHERE sha = ?1 ORDER BY id DESC LIMIT 1",
                params![sha],
                |r| r.get(0),
            )
            .optional()
            .map_err(Into::into)
    }

    /// Newest pytest run for `sha` that has at least one coverage row (N5).
    pub fn latest_pytest_run_with_coverage_for_sha(&self, sha: &str) -> Result<Option<i64>> {
        self.conn
            .query_row(
                "SELECT r.id FROM test_runs r
                 WHERE r.sha = ?1 AND r.source = 'pytest'
                   AND EXISTS (SELECT 1 FROM test_coverage c WHERE c.run_id = r.id)
                 ORDER BY r.id DESC LIMIT 1",
                params![sha],
                |r| r.get(0),
            )
            .optional()
            .map_err(Into::into)
    }

    pub fn coverage_for(
        &self,
        run_id: i64,
        test_id: &str,
        path_filter: Option<&str>,
    ) -> Result<Vec<(String, Vec<u8>)>> {
        let mut out = Vec::new();
        if let Some(p) = path_filter {
            let mut stmt = self.conn.prepare(
                "SELECT c.path, c.lines FROM test_coverage c
                 JOIN tests t ON t.id = c.test_pk
                 WHERE c.run_id = ?1 AND t.test_id = ?2 AND c.path = ?3",
            )?;
            let rows = stmt.query_map(params![run_id, test_id, p], |r| {
                Ok((r.get::<_, String>(0)?, r.get::<_, Vec<u8>>(1)?))
            })?;
            for row in rows {
                out.push(row?);
            }
        } else {
            let mut stmt = self.conn.prepare(
                "SELECT c.path, c.lines FROM test_coverage c
                 JOIN tests t ON t.id = c.test_pk
                 WHERE c.run_id = ?1 AND t.test_id = ?2",
            )?;
            let rows = stmt.query_map(params![run_id, test_id], |r| {
                Ok((r.get::<_, String>(0)?, r.get::<_, Vec<u8>>(1)?))
            })?;
            for row in rows {
                out.push(row?);
            }
        }
        Ok(out)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn migrate_idempotent() {
        let store = Store::open_in_memory().unwrap();
        store.migrate().unwrap();
        store.migrate().unwrap();
        let v: i64 = store
            .conn
            .query_row("SELECT version FROM schema_version", [], |r| r.get(0))
            .unwrap();
        assert_eq!(v, SCHEMA_VERSION);
    }

    #[test]
    fn wrong_schema_version_errors() {
        let store = Store::open_in_memory().unwrap();
        store
            .conn
            .execute("UPDATE schema_version SET version = 99", [])
            .unwrap();
        let err = store.migrate().unwrap_err();
        let msg = err.to_string();
        match err {
            Error::SchemaTooNew {
                found: 99,
                expected: SCHEMA_VERSION,
            } => {
                assert!(msg.contains("too new"), "{msg}");
                assert!(!msg.contains("outdated"), "{msg}");
                assert!(msg.contains("upgrade testpilot"), "{msg}");
            }
            other => panic!("unexpected: {other}"),
        }
    }

    #[test]
    fn schema_v1_db_rejected_with_upgrade_hint() {
        // N1: a v1 DB (pre-tests dictionary) must not be silently reused.
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("old.sqlite");
        {
            let conn = Connection::open(&path).unwrap();
            conn.execute_batch(
                "CREATE TABLE schema_version (version INTEGER PRIMARY KEY);
                 INSERT INTO schema_version(version) VALUES (1);
                 CREATE TABLE test_runs (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   sha TEXT NOT NULL,
                   source TEXT NOT NULL,
                   started_at INTEGER NOT NULL,
                   finished_at INTEGER,
                   dirty INTEGER NOT NULL DEFAULT 0,
                   diff_hash TEXT,
                   input_hash TEXT,
                   meta TEXT
                 );
                 CREATE TABLE test_results (
                   run_id INTEGER NOT NULL,
                   test_id TEXT NOT NULL,
                   outcome TEXT NOT NULL,
                   duration_ms INTEGER NOT NULL,
                   message TEXT,
                   flaky INTEGER NOT NULL DEFAULT 0,
                   attempts INTEGER NOT NULL DEFAULT 1,
                   PRIMARY KEY (run_id, test_id)
                 );",
            )
            .unwrap();
        }
        let err = match Store::open(&path) {
            Ok(_) => panic!("expected schema version error"),
            Err(e) => e,
        };
        let msg = err.to_string();
        assert!(
            matches!(
                err,
                Error::SchemaOutdated {
                    found: 1,
                    expected: 2
                }
            ),
            "{err}"
        );
        assert!(msg.contains("outdated"), "{msg}");
        assert!(msg.contains("delete .testpilot"), "{msg}");
    }

    #[test]
    fn insert_commit_ignore_duplicate() {
        let store = Store::open_in_memory().unwrap();
        let c = CommitRow {
            sha: "abc".into(),
            author_ts: 1,
            message: "hi".into(),
            oversized: false,
            parents: vec![],
        };
        assert!(store.insert_commit(&c).unwrap());
        assert!(!store.insert_commit(&c).unwrap());
        let n: i64 = store
            .conn
            .query_row("SELECT COUNT(*) FROM commits", [], |r| r.get(0))
            .unwrap();
        assert_eq!(n, 1);
    }

    #[test]
    fn tests_dictionary_dedups_test_id() {
        let mut store = Store::open_in_memory().unwrap();
        let run = TestRunRow {
            sha: "abc".into(),
            source: "pytest".into(),
            started_at: 1,
            finished_at: Some(2),
            dirty: false,
            diff_hash: None,
            input_hash: None,
            meta: None,
        };
        let results = vec![
            TestResultRow {
                test_id: "t::a".into(),
                outcome: "pass".into(),
                duration_ms: 1,
                message: None,
                flaky: false,
                attempts: 1,
            },
            TestResultRow {
                test_id: "t::b".into(),
                outcome: "fail".into(),
                duration_ms: 2,
                message: None,
                flaky: false,
                attempts: 1,
            },
        ];
        let cov = vec![CoverageRow {
            test_id: "t::a".into(),
            path: "x.py".into(),
            lines: vec![0x01],
        }];
        store
            .insert_run_atomic(&run, &results, &cov, false)
            .unwrap();
        let n: i64 = store
            .conn
            .query_row("SELECT COUNT(*) FROM tests", [], |r| r.get(0))
            .unwrap();
        assert_eq!(n, 2);
        let joined: String = store
            .conn
            .query_row(
                "SELECT t.test_id FROM test_results r JOIN tests t ON t.id = r.test_pk LIMIT 1",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert!(joined.starts_with("t::"));
    }
}
