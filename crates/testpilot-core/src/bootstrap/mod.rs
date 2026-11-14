//! Week-6 bootstrap: state machine, fingerprint, cleanup, dry-run estimates.

mod cleanup;
mod estimate;
mod fingerprint;
mod state;

pub use cleanup::{CleanupReport, startup_cleanup};
pub use estimate::{
    TimeEstimate, default_priors, estimate_wall, format_dry_run_report, load_priors_file,
};
pub use fingerprint::{BootstrapInputs, Fingerprint, resolve_fingerprint, schema_sql_sha256};
pub use state::{
    BootstrapState, ResumeDecision, StepStatus, decide_resume, load_or_init_state, load_state,
    save_state, state_path,
};
