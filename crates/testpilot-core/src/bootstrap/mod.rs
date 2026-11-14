//! Week-6 bootstrap: state machine, fingerprint, cleanup, dry-run estimates.

mod bset;
mod cleanup;
mod dump_fp;
mod estimate;
mod fingerprint;
mod state;

pub use bset::{
    build_b_set, filter_with_first_parent, first_parent_shas, sha_has_first_parent,
    shas_needing_backfill, union_b_set,
};
pub use cleanup::{CleanupReport, startup_cleanup};
pub use dump_fp::{DbDumpFingerprint, dump_needs_refresh};
pub use estimate::{
    EMBEDDED_PRIORS_PATH, TimeEstimate, default_priors, estimate_wall, format_dry_run_report,
    load_priors_file,
};
pub use fingerprint::{BootstrapInputs, Fingerprint, resolve_fingerprint, schema_sql_sha256};
pub use state::{
    BootstrapState, ResumeDecision, SaveFault, StepStatus, decide_resume, load_or_init_state,
    load_state, save_state, save_state_with_fault, state_path,
};
