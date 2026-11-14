use std::path::Path;

use rusqlite::{Connection, OptionalExtension, Transaction, params};

use crate::error::{Error, Result};

pub const SCHEMA_VERSION: i64 = 7;
pub const SCHEMA_SQL: &str = include_str!("../schema.sql");

/// Additive migration from collection schema v2 → features cache v3.
const MIGRATE_2_TO_3: &str = r#"
CREATE TABLE IF NOT EXISTS python_blob_cache (
  blob_sha TEXT PRIMARY KEY,
  imports_json TEXT NOT NULL,
  defs_json TEXT NOT NULL
);
"#;

/// Additive migration v3 → v4: feature history snapshots for fast show (JSON; superseded by v5).
const MIGRATE_3_TO_4: &str = r#"
CREATE TABLE IF NOT EXISTS feature_history_snapshot (
  sha TEXT PRIMARY KEY,
  history_json TEXT NOT NULL,
  cochange_json TEXT NOT NULL,
  cov_source_sha TEXT
);
"#;

/// v4 → v5: compact binary snapshots (drop JSON table; re-dump to refill).
const MIGRATE_4_TO_5: &str = r#"
DROP TABLE IF EXISTS feature_history_snapshot;
CREATE TABLE feature_history_snapshot (
  sha TEXT PRIMARY KEY,
  payload BLOB NOT NULL,
  cov_source_sha TEXT
);
"#;

/// v5 → v6: flag mutant bootstrap runs so real query paths ignore them.
/// Applied only when the column is missing (fresh SCHEMA_SQL already has it).
const MIGRATE_5_TO_6_ADD: &str = r#"
ALTER TABLE test_runs ADD COLUMN synthetic INTEGER NOT NULL DEFAULT 0
  CHECK (synthetic IN (0,1));
"#;

/// v6 → v7: partial runs + selection table.
const MIGRATE_6_TO_7_PARTIAL: &str = r#"
ALTER TABLE test_runs ADD COLUMN partial INTEGER NOT NULL DEFAULT 0
  CHECK (partial IN (0,1));
"#;

const MIGRATE_6_TO_7_SELECTION: &str = r#"
CREATE TABLE IF NOT EXISTS test_run_selection (
  run_id      INTEGER NOT NULL REFERENCES test_runs(id),
  test_pk     INTEGER NOT NULL REFERENCES tests(id),
  selected    INTEGER NOT NULL CHECK (selected IN (0,1)),
  reason      TEXT NOT NULL,
  score       REAL,
  rank        INTEGER,
  budget_kind TEXT,
  budget_value REAL,
  model_sha256 TEXT,
  safe_enabled INTEGER NOT NULL DEFAULT 0 CHECK (safe_enabled IN (0,1)),
  PRIMARY KEY (run_id, test_pk)
);
CREATE INDEX IF NOT EXISTS idx_test_run_selection_run
  ON test_run_selection(run_id);
"#;

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
    /// Mutant bootstrap run (1) must not feed real history/coverage/show paths.
    pub synthetic: bool,
    /// Select subset run (1) must not feed base_run / dump / recent_fail history.
    pub partial: bool,
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
            // Additive upgrades only; v1 still requires delete.
            if (2..=6).contains(&v) && SCHEMA_VERSION == 7 {
                let tx = self.conn.unchecked_transaction()?;
                if v == 2 {
                    tx.execute_batch(MIGRATE_2_TO_3)?;
                }
                if v <= 3 {
                    tx.execute_batch(MIGRATE_3_TO_4)?;
                }
                if v <= 4 {
                    tx.execute_batch(MIGRATE_4_TO_5)?;
                }
                if v <= 5 {
                    let has_syn: i64 = tx.query_row(
                        "SELECT COUNT(*) FROM pragma_table_info('test_runs') WHERE name='synthetic'",
                        [],
                        |r| r.get(0),
                    )?;
                    if has_syn == 0 {
                        tx.execute_batch(MIGRATE_5_TO_6_ADD)?;
                    }
                }
                if v <= 6 {
                    let has_partial: i64 = tx.query_row(
                        "SELECT COUNT(*) FROM pragma_table_info('test_runs') WHERE name='partial'",
                        [],
                        |r| r.get(0),
                    )?;
                    if has_partial == 0 {
                        tx.execute_batch(MIGRATE_6_TO_7_PARTIAL)?;
                    }
                    tx.execute_batch(MIGRATE_6_TO_7_SELECTION)?;
                }
                tx.execute(
                    "UPDATE schema_version SET version = ?1",
                    params![SCHEMA_VERSION],
                )?;
                tx.commit()?;
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
                sha, source, started_at, finished_at, dirty, diff_hash, input_hash, meta,
                synthetic, partial
             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
            params![
                run.sha,
                run.source,
                run.started_at,
                run.finished_at,
                run.dirty as i64,
                run.diff_hash,
                run.input_hash,
                run.meta,
                run.synthetic as i64,
                run.partial as i64
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

    /// Newest pytest run for `sha` that has at least one coverage row (N5).
    /// Excludes synthetic (mutant) and partial (select) runs.
    pub fn latest_pytest_run_with_coverage_for_sha(&self, sha: &str) -> Result<Option<i64>> {
        self.conn
            .query_row(
                "SELECT r.id FROM test_runs r
                 WHERE r.sha = ?1 AND r.source = 'pytest'
                   AND r.synthetic = 0 AND r.partial = 0
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

    pub fn get_python_blob_cache(&self, blob_sha: &str) -> Result<Option<(String, String)>> {
        self.conn
            .query_row(
                "SELECT imports_json, defs_json FROM python_blob_cache WHERE blob_sha = ?1",
                params![blob_sha],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .optional()
            .map_err(Into::into)
    }

    pub fn put_python_blob_cache(
        &self,
        blob_sha: &str,
        imports_json: &str,
        defs_json: &str,
    ) -> Result<()> {
        self.conn.execute(
            "INSERT OR REPLACE INTO python_blob_cache(blob_sha, imports_json, defs_json)
             VALUES (?1, ?2, ?3)",
            params![blob_sha, imports_json, defs_json],
        )?;
        Ok(())
    }

    pub fn get_feature_history_snapshot(
        &self,
        sha: &str,
    ) -> Result<Option<(Vec<u8>, Option<String>)>> {
        self.conn
            .query_row(
                "SELECT payload, cov_source_sha
                 FROM feature_history_snapshot WHERE sha = ?1",
                params![sha],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .optional()
            .map_err(Into::into)
    }

    pub fn put_feature_history_snapshot(
        &self,
        sha: &str,
        payload: &[u8],
        cov_source_sha: Option<&str>,
    ) -> Result<()> {
        self.conn.execute(
            "INSERT OR REPLACE INTO feature_history_snapshot(
                sha, payload, cov_source_sha
             ) VALUES (?1, ?2, ?3)",
            params![sha, payload, cov_source_sha],
        )?;
        Ok(())
    }

    pub fn clear_feature_history_snapshots(&self) -> Result<()> {
        self.conn
            .execute("DELETE FROM feature_history_snapshot", [])?;
        Ok(())
    }

    pub fn count_feature_history_snapshots(&self) -> Result<i64> {
        self.conn
            .query_row("SELECT COUNT(*) FROM feature_history_snapshot", [], |r| {
                r.get(0)
            })
            .map_err(Into::into)
    }

    /// All parent SHAs for `sha` (any parent_idx), for ancestor walks.
    pub fn all_parents(&self, sha: &str) -> Result<Vec<String>> {
        let mut stmt = self
            .conn
            .prepare("SELECT parent_sha FROM commit_parents WHERE sha = ?1 ORDER BY parent_idx")?;
        let rows = stmt.query_map(params![sha], |r| r.get(0))?;
        let mut out = Vec::new();
        for row in rows {
            out.push(row?);
        }
        Ok(out)
    }

    /// Last clean **non-synthetic, non-partial** finished run on `sha` with ≥1 test_results.
    pub fn last_clean_run_with_results(&self, sha: &str) -> Result<Option<i64>> {
        self.conn
            .query_row(
                "SELECT r.id FROM test_runs r
                 WHERE r.sha = ?1 AND r.dirty = 0 AND r.synthetic = 0 AND r.partial = 0
                   AND r.finished_at IS NOT NULL
                   AND EXISTS (SELECT 1 FROM test_results tr WHERE tr.run_id = r.id)
                 ORDER BY r.id DESC LIMIT 1",
                params![sha],
                |r| r.get(0),
            )
            .optional()
            .map_err(Into::into)
    }

    /// Other clean **non-synthetic, non-partial** finished runs on `sha` (excluding `primary_id`).
    pub fn other_clean_runs(&self, sha: &str, primary_id: i64) -> Result<Vec<i64>> {
        let mut stmt = self.conn.prepare(
            "SELECT r.id FROM test_runs r
             WHERE r.sha = ?1 AND r.dirty = 0 AND r.synthetic = 0 AND r.partial = 0
               AND r.finished_at IS NOT NULL AND r.id != ?2
               AND EXISTS (SELECT 1 FROM test_results tr WHERE tr.run_id = r.id)
             ORDER BY r.id ASC",
        )?;
        let rows = stmt.query_map(params![sha, primary_id], |r| r.get(0))?;
        let mut out = Vec::new();
        for row in rows {
            out.push(row?);
        }
        Ok(out)
    }

    /// Last clean **non-synthetic, non-partial** finished run with ≥1 coverage row on `sha`.
    pub fn last_clean_run_with_coverage(&self, sha: &str) -> Result<Option<i64>> {
        self.conn
            .query_row(
                "SELECT r.id FROM test_runs r
                 WHERE r.sha = ?1 AND r.dirty = 0 AND r.synthetic = 0 AND r.partial = 0
                   AND r.finished_at IS NOT NULL
                   AND EXISTS (SELECT 1 FROM test_coverage c WHERE c.run_id = r.id)
                 ORDER BY r.id DESC LIMIT 1",
                params![sha],
                |r| r.get(0),
            )
            .optional()
            .map_err(Into::into)
    }

    /// Last synthetic (mutant) run with results on `sha` — Phase B dump only.
    pub fn last_synthetic_run_with_results(&self, sha: &str) -> Result<Option<i64>> {
        self.conn
            .query_row(
                "SELECT r.id FROM test_runs r
                 WHERE r.sha = ?1 AND r.dirty = 0 AND r.synthetic = 1
                   AND EXISTS (SELECT 1 FROM test_results tr WHERE tr.run_id = r.id)
                 ORDER BY r.id DESC LIMIT 1",
                params![sha],
                |r| r.get(0),
            )
            .optional()
            .map_err(Into::into)
    }

    pub fn results_for_run(&self, run_id: i64) -> Result<Vec<TestResultRow>> {
        let mut stmt = self.conn.prepare(
            "SELECT t.test_id, r.outcome, r.duration_ms, r.message, r.flaky, r.attempts
             FROM test_results r
             JOIN tests t ON t.id = r.test_pk
             WHERE r.run_id = ?1
             ORDER BY t.test_id",
        )?;
        let rows = stmt.query_map(params![run_id], |r| {
            Ok(TestResultRow {
                test_id: r.get(0)?,
                outcome: r.get(1)?,
                duration_ms: r.get(2)?,
                message: r.get(3)?,
                flaky: r.get::<_, i64>(4)? != 0,
                attempts: r.get(5)?,
            })
        })?;
        let mut out = Vec::new();
        for row in rows {
            out.push(row?);
        }
        Ok(out)
    }

    /// Newest finished real (non-synthetic, non-partial) runs on `ancestry_shas`, up to `limit`.
    pub fn recent_finished_runs_on_shas(
        &self,
        ancestry_shas: &[String],
        limit: usize,
    ) -> Result<Vec<i64>> {
        if ancestry_shas.is_empty() || limit == 0 {
            return Ok(vec![]);
        }
        let placeholders = std::iter::repeat("?")
            .take(ancestry_shas.len())
            .collect::<Vec<_>>()
            .join(", ");
        let sql = format!(
            "SELECT r.id FROM test_runs r
             WHERE r.sha IN ({placeholders})
               AND r.synthetic = 0 AND r.partial = 0
               AND r.finished_at IS NOT NULL
             ORDER BY r.finished_at DESC, r.id DESC
             LIMIT ?"
        );
        let mut params: Vec<Box<dyn rusqlite::types::ToSql>> = ancestry_shas
            .iter()
            .map(|s| Box::new(s.clone()) as Box<dyn rusqlite::types::ToSql>)
            .collect();
        params.push(Box::new(limit as i64));
        let param_refs: Vec<&dyn rusqlite::types::ToSql> =
            params.iter().map(|p| p.as_ref()).collect();
        let mut stmt = self.conn.prepare(&sql)?;
        let rows = stmt.query_map(param_refs.as_slice(), |r| r.get(0))?;
        let mut out = Vec::new();
        for row in rows {
            out.push(row?);
        }
        Ok(out)
    }

    /// All coverage rows for a run: (test_id, path, lines_blob).
    pub fn coverage_rows_for_run(&self, run_id: i64) -> Result<Vec<(String, String, Vec<u8>)>> {
        let mut stmt = self.conn.prepare(
            "SELECT t.test_id, c.path, c.lines
             FROM test_coverage c
             JOIN tests t ON t.id = c.test_pk
             WHERE c.run_id = ?1",
        )?;
        let rows = stmt.query_map(params![run_id], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?;
        let mut out = Vec::new();
        for row in rows {
            out.push(row?);
        }
        Ok(out)
    }

    pub fn first_parent(&self, sha: &str) -> Result<Option<String>> {
        self.conn
            .query_row(
                "SELECT parent_sha FROM commit_parents
                 WHERE sha = ?1 AND parent_idx = 0",
                params![sha],
                |r| r.get(0),
            )
            .optional()
            .map_err(Into::into)
    }

    pub fn commit_author_ts(&self, sha: &str) -> Result<Option<i64>> {
        self.conn
            .query_row(
                "SELECT author_ts FROM commits WHERE sha = ?1",
                params![sha],
                |r| r.get(0),
            )
            .optional()
            .map_err(Into::into)
    }

    pub fn file_paths_for_commit(&self, sha: &str) -> Result<Vec<String>> {
        let mut stmt = self
            .conn
            .prepare("SELECT path FROM file_changes WHERE sha = ?1 ORDER BY path")?;
        let rows = stmt.query_map(params![sha], |r| r.get(0))?;
        let mut out = Vec::new();
        for row in rows {
            out.push(row?);
        }
        Ok(out)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn synthetic_runs_excluded_from_clean_queries() {
        let mut store = Store::open_in_memory().unwrap();
        let real = TestRunRow {
            sha: "abc".into(),
            source: "junit".into(),
            started_at: 1,
            finished_at: Some(2),
            dirty: false,
            diff_hash: None,
            input_hash: Some("real".into()),
            meta: None,
            synthetic: false,
            partial: false,
        };
        let syn = TestRunRow {
            sha: "abc".into(),
            source: "junit".into(),
            started_at: 3,
            finished_at: Some(4),
            dirty: false,
            diff_hash: None,
            input_hash: Some("syn".into()),
            meta: None,
            synthetic: true,
            partial: false,
        };
        let results = vec![TestResultRow {
            test_id: "t::a".into(),
            outcome: "pass".into(),
            duration_ms: 1,
            message: None,
            flaky: false,
            attempts: 1,
        }];
        let cov = vec![CoverageRow {
            test_id: "t::a".into(),
            path: "x.py".into(),
            lines: vec![0x01],
        }];
        let real_id = store
            .insert_run_atomic(&real, &results, &cov, false)
            .unwrap()
            .unwrap();
        let syn_id = store
            .insert_run_atomic(&syn, &results, &cov, false)
            .unwrap()
            .unwrap();
        assert!(syn_id > real_id);
        assert_eq!(
            store.last_clean_run_with_results("abc").unwrap(),
            Some(real_id)
        );
        assert_eq!(
            store.last_clean_run_with_coverage("abc").unwrap(),
            Some(real_id)
        );
        assert_eq!(
            store.last_synthetic_run_with_results("abc").unwrap(),
            Some(syn_id)
        );
        assert!(store.other_clean_runs("abc", real_id).unwrap().is_empty());
    }

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
                    expected: 7
                }
            ),
            "{err}"
        );
        assert!(msg.contains("outdated"), "{msg}");
        assert!(msg.contains("delete .testpilot"), "{msg}");
    }

    #[test]
    fn schema_v2_upgrades_in_place_to_v7() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("v2.sqlite");
        {
            let conn = Connection::open(&path).unwrap();
            // Minimal v2: version row + enough that open's migrate only needs cache DDL.
            // Need test_runs for v5→v6 ALTER.
            conn.execute_batch(
                "CREATE TABLE schema_version (version INTEGER PRIMARY KEY);
                 INSERT INTO schema_version(version) VALUES (2);
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
                 );",
            )
            .unwrap();
        }
        let store = Store::open(&path).unwrap();
        let v: i64 = store
            .conn
            .query_row("SELECT version FROM schema_version", [], |r| r.get(0))
            .unwrap();
        assert_eq!(v, 7);
        let n: i64 = store
            .conn
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master WHERE name='python_blob_cache'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(n, 1);
        let n2: i64 = store
            .conn
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master WHERE name='feature_history_snapshot'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(n2, 1);
        let cols: String = store
            .conn
            .query_row(
                "SELECT sql FROM sqlite_master WHERE name='feature_history_snapshot'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert!(cols.contains("payload"), "{cols}");
        let has_sel: i64 = store
            .conn
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master WHERE name='test_run_selection'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(has_sel, 1);
    }

    #[test]
    fn schema_v4_upgrades_in_place_to_v7() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("v4.sqlite");
        {
            let conn = Connection::open(&path).unwrap();
            conn.execute_batch(
                "CREATE TABLE schema_version (version INTEGER PRIMARY KEY);
                 INSERT INTO schema_version(version) VALUES (4);
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
                 CREATE TABLE feature_history_snapshot (
                   sha TEXT PRIMARY KEY,
                   history_json TEXT NOT NULL,
                   cochange_json TEXT NOT NULL,
                   cov_source_sha TEXT
                 );",
            )
            .unwrap();
        }
        let store = Store::open(&path).unwrap();
        let v: i64 = store
            .conn
            .query_row("SELECT version FROM schema_version", [], |r| r.get(0))
            .unwrap();
        assert_eq!(v, 7);
        let cols: String = store
            .conn
            .query_row(
                "SELECT sql FROM sqlite_master WHERE name='feature_history_snapshot'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert!(cols.contains("payload"), "{cols}");
        assert!(!cols.contains("history_json"), "{cols}");
        let has_syn: i64 = store
            .conn
            .query_row(
                "SELECT COUNT(*) FROM pragma_table_info('test_runs') WHERE name='synthetic'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(has_syn, 1);
        let has_partial: i64 = store
            .conn
            .query_row(
                "SELECT COUNT(*) FROM pragma_table_info('test_runs') WHERE name='partial'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(has_partial, 1);
    }

    #[test]
    fn schema_v5_upgrades_in_place_to_v7() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("v5.sqlite");
        {
            let conn = Connection::open(&path).unwrap();
            conn.execute_batch(
                "CREATE TABLE schema_version (version INTEGER PRIMARY KEY);
                 INSERT INTO schema_version(version) VALUES (5);
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
                 CREATE TABLE feature_history_snapshot (
                   sha TEXT PRIMARY KEY,
                   payload BLOB NOT NULL,
                   cov_source_sha TEXT
                 );",
            )
            .unwrap();
        }
        let store = Store::open(&path).unwrap();
        let v: i64 = store
            .conn
            .query_row("SELECT version FROM schema_version", [], |r| r.get(0))
            .unwrap();
        assert_eq!(v, 7);
    }

    #[test]
    fn schema_v6_upgrades_in_place_to_v7() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("v6.sqlite");
        {
            let conn = Connection::open(&path).unwrap();
            conn.execute_batch(
                "CREATE TABLE schema_version (version INTEGER PRIMARY KEY);
                 INSERT INTO schema_version(version) VALUES (6);
                 CREATE TABLE test_runs (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   sha TEXT NOT NULL,
                   source TEXT NOT NULL,
                   started_at INTEGER NOT NULL,
                   finished_at INTEGER,
                   dirty INTEGER NOT NULL DEFAULT 0,
                   diff_hash TEXT,
                   input_hash TEXT,
                   meta TEXT,
                   synthetic INTEGER NOT NULL DEFAULT 0
                 );
                 CREATE TABLE tests (
                   id INTEGER PRIMARY KEY,
                   test_id TEXT NOT NULL UNIQUE
                 );",
            )
            .unwrap();
        }
        let store = Store::open(&path).unwrap();
        let v: i64 = store
            .conn
            .query_row("SELECT version FROM schema_version", [], |r| r.get(0))
            .unwrap();
        assert_eq!(v, 7);
        let has_partial: i64 = store
            .conn
            .query_row(
                "SELECT COUNT(*) FROM pragma_table_info('test_runs') WHERE name='partial'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(has_partial, 1);
        let has_sel: i64 = store
            .conn
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master WHERE name='test_run_selection'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(has_sel, 1);
    }

    #[test]
    fn partial_runs_excluded_from_clean_queries() {
        let mut store = Store::open_in_memory().unwrap();
        let full = TestRunRow {
            sha: "abc".into(),
            source: "junit".into(),
            started_at: 1,
            finished_at: Some(2),
            dirty: false,
            diff_hash: None,
            input_hash: Some("full".into()),
            meta: None,
            synthetic: false,
            partial: false,
        };
        let partial = TestRunRow {
            sha: "abc".into(),
            source: "junit".into(),
            started_at: 3,
            finished_at: Some(4),
            dirty: false,
            diff_hash: None,
            input_hash: Some("part".into()),
            meta: None,
            synthetic: false,
            partial: true,
        };
        let results = vec![TestResultRow {
            test_id: "t::a".into(),
            outcome: "pass".into(),
            duration_ms: 1,
            message: None,
            flaky: false,
            attempts: 1,
        }];
        let cov = vec![CoverageRow {
            test_id: "t::a".into(),
            path: "x.py".into(),
            lines: vec![0x01],
        }];
        let full_id = store
            .insert_run_atomic(&full, &results, &cov, false)
            .unwrap()
            .unwrap();
        let part_id = store
            .insert_run_atomic(&partial, &results, &cov, false)
            .unwrap()
            .unwrap();
        assert!(part_id > full_id);
        assert_eq!(
            store.last_clean_run_with_results("abc").unwrap(),
            Some(full_id)
        );
        assert_eq!(
            store.last_clean_run_with_coverage("abc").unwrap(),
            Some(full_id)
        );
        assert!(store.other_clean_runs("abc", full_id).unwrap().is_empty());
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
            synthetic: false,
            partial: false,
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
