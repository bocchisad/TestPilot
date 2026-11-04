//! Feature computation for (change, test) pairs.

pub mod change;
pub mod cochange;
pub mod coverage;
pub mod dump;
pub mod history;
pub mod imports;
pub mod names;
pub mod python_scan;
pub mod schema;
pub mod writer;

pub use dump::{compute_dump_rows, compute_show_rows, dump_parquet, show_format};
pub use python_scan::{DefSpan, ImportEdge, ScanResult, scan_python};
pub use schema::{FEATURE_COLUMNS, FEATURES_VERSION, FeatureColumn, schema_json};
pub use writer::{FeatureRow, sort_rows, write_parquet};
