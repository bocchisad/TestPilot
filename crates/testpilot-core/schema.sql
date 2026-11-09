CREATE TABLE IF NOT EXISTS schema_version (
  version INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS commits (
  sha       TEXT PRIMARY KEY,
  author_ts INTEGER NOT NULL,
  message   TEXT NOT NULL,
  oversized INTEGER NOT NULL DEFAULT 0 CHECK (oversized IN (0,1))
);

CREATE TABLE IF NOT EXISTS commit_parents (
  sha        TEXT NOT NULL REFERENCES commits(sha),
  parent_sha TEXT NOT NULL,
  parent_idx INTEGER NOT NULL,
  PRIMARY KEY (sha, parent_idx)
);

CREATE TABLE IF NOT EXISTS file_changes (
  sha           TEXT NOT NULL REFERENCES commits(sha),
  path          TEXT NOT NULL,
  change_type   TEXT NOT NULL CHECK (change_type IN ('A','M','D','R')),
  old_path      TEXT,
  lines_added   INTEGER NOT NULL DEFAULT 0,
  lines_deleted INTEGER NOT NULL DEFAULT 0,
  is_binary     INTEGER NOT NULL DEFAULT 0 CHECK (is_binary IN (0,1)),
  PRIMARY KEY (sha, path)
);

CREATE TABLE IF NOT EXISTS test_runs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  sha         TEXT NOT NULL,
  source      TEXT NOT NULL CHECK (source IN ('junit','pytest')),
  started_at  INTEGER NOT NULL,
  finished_at INTEGER,
  dirty       INTEGER NOT NULL DEFAULT 0 CHECK (dirty IN (0,1)),
  diff_hash   TEXT,
  input_hash  TEXT,
  meta        TEXT
);

CREATE TABLE IF NOT EXISTS tests (
  id      INTEGER PRIMARY KEY,
  test_id TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS test_results (
  run_id      INTEGER NOT NULL REFERENCES test_runs(id),
  test_pk     INTEGER NOT NULL REFERENCES tests(id),
  outcome     TEXT NOT NULL CHECK (outcome IN ('pass','fail','error','skip')),
  duration_ms INTEGER NOT NULL,
  message     TEXT,
  flaky       INTEGER NOT NULL DEFAULT 0 CHECK (flaky IN (0,1)),
  attempts    INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (run_id, test_pk)
);

CREATE TABLE IF NOT EXISTS test_coverage (
  run_id  INTEGER NOT NULL REFERENCES test_runs(id),
  test_pk INTEGER NOT NULL REFERENCES tests(id),
  path    TEXT NOT NULL,
  lines   BLOB NOT NULL,
  PRIMARY KEY (run_id, test_pk, path)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_file_changes_path ON file_changes(path);
CREATE INDEX IF NOT EXISTS idx_test_results_test_pk ON test_results(test_pk);
CREATE INDEX IF NOT EXISTS idx_test_runs_sha ON test_runs(sha);
CREATE INDEX IF NOT EXISTS idx_test_coverage_path ON test_coverage(path);

CREATE UNIQUE INDEX IF NOT EXISTS idx_test_runs_dedup
  ON test_runs(source, sha, input_hash)
  WHERE input_hash IS NOT NULL;

-- v3: parse cache for Python imports/defs (blob content-addressed). Additive only.
CREATE TABLE IF NOT EXISTS python_blob_cache (
  blob_sha TEXT PRIMARY KEY,
  imports_json TEXT NOT NULL,
  defs_json TEXT NOT NULL
);

-- v4: incremental feature history snapshot after each dump commit (for fast show).
CREATE TABLE IF NOT EXISTS feature_history_snapshot (
  sha TEXT PRIMARY KEY,
  history_json TEXT NOT NULL,
  cochange_json TEXT NOT NULL,
  cov_source_sha TEXT
);
