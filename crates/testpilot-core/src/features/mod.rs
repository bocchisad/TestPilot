//! Feature computation for (change, test) pairs.

pub mod change;
pub mod change_rows;
pub mod cochange;
pub mod coverage;
pub mod dump;
pub mod history;
pub mod imports;
#[cfg(test)]
mod leak_test;
pub mod model_vec;
pub mod names;
pub mod python_scan;
pub mod schema;
pub mod snapshot;
pub mod writer;

pub use change_rows::{
    ChangeRowsOpts, CleanRunRef, compute_change_rows, import_graph_seeds,
    last_clean_run_ref_at_or_before, snapshot_missing_message, test_file_of,
};
pub use dump::{
    compute_dump_rows, compute_show_rows, dump_parquet, dump_parquet_ex, feature_rows_for_change,
    show_format,
};
pub use model_vec::model_feature_vector;
pub use python_scan::{DefSpan, ImportEdge, ScanResult, scan_python};
pub use schema::{FEATURE_COLUMNS, FEATURES_VERSION, FeatureColumn, schema_json};
pub use writer::{FeatureRow, sort_rows, write_parquet};
