use std::path::Path;

use rusqlite::{Connection, OptionalExtension, params};

use crate::error::{Error, Result};

pub const SCHEMA_VERSION: i64 = 1;
pub const SCHEMA_SQL: &str = include_str!("../schema.sql");

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
                return Err(Error::UnsupportedSchema {
                    found: v,
                    expected: SCHEMA_VERSION,
                });
            }
        }
        tx.commit()?;
        Ok(())
    }

    pub fn connection(&self) -> &Connection {
        &self.conn
    }

    pub fn insert_commit(&self, commit: &CommitRow) -> Result<bool> {
        let changed = self.conn.execute(
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
            self.conn.execute(
                "INSERT INTO commit_parents(sha, parent_sha, parent_idx)
                 VALUES (?1, ?2, ?3)",
                params![commit.sha, parent, idx as i64],
            )?;
        }
        Ok(true)
    }

    pub fn insert_file_changes(&self, changes: &[FileChangeRow]) -> Result<()> {
        for c in changes {
            self.conn.execute(
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

    pub fn commit_exists(&self, sha: &str) -> Result<bool> {
        let n: i64 = self.conn.query_row(
            "SELECT COUNT(*) FROM commits WHERE sha = ?1",
            params![sha],
            |r| r.get(0),
        )?;
        Ok(n > 0)
    }

    /// Insert a test run. Returns `None` if unique dedup index rejects (unless caller
    /// already checked). On UNIQUE conflict returns Ok(None).
    pub fn insert_test_run(&self, run: &TestRunRow) -> Result<Option<i64>> {
        let result = self.conn.execute(
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
            Ok(_) => Ok(Some(self.conn.last_insert_rowid())),
            Err(rusqlite::Error::SqliteFailure(e, _))
                if e.code == rusqlite::ErrorCode::ConstraintViolation =>
            {
                Ok(None)
            }
            Err(e) => Err(e.into()),
        }
    }

    pub fn insert_test_results(&self, run_id: i64, results: &[TestResultRow]) -> Result<()> {
        for r in results {
            self.conn.execute(
                "INSERT INTO test_results(
                    run_id, test_id, outcome, duration_ms, message, flaky, attempts
                 ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
                params![
                    run_id,
                    r.test_id,
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

    pub fn insert_coverage(&self, run_id: i64, rows: &[CoverageRow]) -> Result<()> {
        for c in rows {
            self.conn.execute(
                "INSERT INTO test_coverage(run_id, test_id, path, lines)
                 VALUES (?1, ?2, ?3, ?4)",
                params![run_id, c.test_id, c.path, c.lines],
            )?;
        }
        Ok(())
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

    pub fn with_transaction<F, T>(&mut self, f: F) -> Result<T>
    where
        F: FnOnce(&Connection) -> Result<T>,
    {
        let tx = self.conn.transaction()?;
        let result = f(&tx)?;
        tx.commit()?;
        Ok(result)
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
                "SELECT path, lines FROM test_coverage
                 WHERE run_id = ?1 AND test_id = ?2 AND path = ?3",
            )?;
            let rows = stmt.query_map(params![run_id, test_id, p], |r| {
                Ok((r.get::<_, String>(0)?, r.get::<_, Vec<u8>>(1)?))
            })?;
            for row in rows {
                out.push(row?);
            }
        } else {
            let mut stmt = self.conn.prepare(
                "SELECT path, lines FROM test_coverage
                 WHERE run_id = ?1 AND test_id = ?2",
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
        match err {
            Error::UnsupportedSchema {
                found: 99,
                expected: SCHEMA_VERSION,
            } => {}
            other => panic!("unexpected: {other}"),
        }
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
}
