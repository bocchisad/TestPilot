use thiserror::Error;

#[derive(Debug, Error)]
pub enum Error {
    #[error("sqlite: {0}")]
    Sqlite(#[from] rusqlite::Error),

    #[error(
        "schema outdated (version {found}; need {expected}): delete .testpilot and run `testpilot init`"
    )]
    UnsupportedSchema { found: i64, expected: i64 },

    #[error("schema_version missing; database may be corrupt")]
    MissingSchemaVersion,

    #[error("junit parse error: {0}")]
    Junit(String),

    #[error("coverage codec: {0}")]
    Codec(String),

    #[error("git: {0}")]
    Git(String),

    #[error("io: {0}")]
    Io(#[from] std::io::Error),

    #[error("{0}")]
    Other(String),
}

pub type Result<T> = std::result::Result<T, Error>;
