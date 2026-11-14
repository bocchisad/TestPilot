use std::fs;
use std::path::Path;

use serde::{Deserialize, Serialize};

use super::fingerprint::Fingerprint;
use crate::error::{Error, Result};

const EMBEDDED_PRIORS: &str =
    include_str!("../../../../docs/measurements/bootstrap_time_priors.json");

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SuiteSeconds {
    pub click: f64,
    pub jinja: f64,
    #[serde(default)]
    pub measured: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BootstrapPriors {
    pub mutate_s_per_mutant: SuiteSeconds,
    pub backfill_s_per_commit: SuiteSeconds,
    pub collect_git_s: SuiteSeconds,
    pub dump_train_s: SuiteSeconds,
}

#[derive(Debug, Clone)]
pub struct TimeEstimate {
    pub collect_git_s: f64,
    pub backfill_s: f64,
    pub backfill_unmeasured: bool,
    pub backfill_shas_hat: u32,
    pub mutate_s: f64,
    pub mutate_jobs: u32,
    pub dump_train_s: f64,
    pub total_s: f64,
    pub suite: String,
}

pub fn default_priors() -> BootstrapPriors {
    serde_json::from_str(EMBEDDED_PRIORS).expect("embedded bootstrap_time_priors.json")
}

pub fn load_priors_file(path: &Path) -> Result<BootstrapPriors> {
    let raw = fs::read_to_string(path).map_err(Error::Io)?;
    serde_json::from_str(&raw).map_err(|e| Error::Other(format!("priors.json: {e}")))
}

fn suite_pick(s: &SuiteSeconds, suite: &str) -> f64 {
    match suite {
        "jinja" => s.jinja,
        _ => s.click,
    }
}

/// Rough wall-clock estimate. `|B_set|` hat ≈ N + 20 (union last-20 FP).
pub fn estimate_wall(
    n: u32,
    k: u32,
    jobs: u32,
    suite: &str,
    priors: &BootstrapPriors,
) -> TimeEstimate {
    let jobs = jobs.max(1);
    let b_hat = n.saturating_add(20);
    let collect = suite_pick(&priors.collect_git_s, suite);
    let backfill_per = suite_pick(&priors.backfill_s_per_commit, suite);
    let backfill = backfill_per * f64::from(b_hat);
    let mutants = f64::from(n.saturating_mul(k));
    let mutate = suite_pick(&priors.mutate_s_per_mutant, suite) * mutants / f64::from(jobs);
    let dump_train = suite_pick(&priors.dump_train_s, suite);
    TimeEstimate {
        collect_git_s: collect,
        backfill_s: backfill,
        backfill_unmeasured: !priors.backfill_s_per_commit.measured,
        backfill_shas_hat: b_hat,
        mutate_s: mutate,
        mutate_jobs: jobs,
        dump_train_s: dump_train,
        total_s: collect + backfill + mutate + dump_train,
        suite: suite.to_string(),
    }
}

fn fmt_hours(secs: f64) -> String {
    if secs < 120.0 {
        format!("{secs:.0}s")
    } else if secs < 7200.0 {
        format!("{:.1} min", secs / 60.0)
    } else {
        format!("{:.1} h", secs / 3600.0)
    }
}

pub fn format_dry_run_report(
    fp: &Fingerprint,
    b_set_hat: u32,
    est: &TimeEstimate,
    resume_allowed: bool,
) -> String {
    let mut out = String::new();
    out.push_str("testpilot bootstrap --dry-run\n");
    out.push_str(&format!(
        "fingerprint: head={} N={} K={} seed={} B_policy={} bin={} schema_v={}\n",
        fp.head_sha, fp.n, fp.k, fp.seed, fp.b_policy, fp.bin_version, fp.schema_version
    ));
    out.push_str(&format!(
        "plan: |B_set|≈{b_set_hat}  N×K={}  jobs={}  suite={}\n",
        fp.n.saturating_mul(fp.k),
        est.mutate_jobs,
        est.suite
    ));
    out.push_str(&format!(
        "estimate collect_git: {}\n",
        fmt_hours(est.collect_git_s)
    ));
    let um = if est.backfill_unmeasured {
        " unmeasured=true"
    } else {
        ""
    };
    out.push_str(&format!(
        "estimate backfill: {} (≈{} SHAs needing run){um}\n",
        fmt_hours(est.backfill_s),
        est.backfill_shas_hat
    ));
    out.push_str(&format!(
        "estimate mutate: {} (jobs={})\n",
        fmt_hours(est.mutate_s),
        est.mutate_jobs
    ));
    out.push_str(&format!(
        "estimate dump+train: {}\n",
        fmt_hours(est.dump_train_s)
    ));
    out.push_str(&format!("estimate total: {}\n", fmt_hours(est.total_s)));
    out.push_str(&format!(
        "resume: {}\n",
        if resume_allowed {
            "allowed (fingerprint matches or no state)"
        } else {
            "would refuse (fingerprint mismatch; use --force)"
        }
    ));
    out
}
