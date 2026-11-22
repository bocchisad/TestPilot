use std::collections::BTreeMap;
use std::fs;
use std::path::Path;

use serde::{Deserialize, Serialize};

use super::fingerprint::Fingerprint;
use crate::error::{Error, Result};

const EMBEDDED_PRIORS: &str =
    include_str!("../../../../docs/measurements/bootstrap_time_priors.json");

/// In-repo path of the embedded default priors (for dry-run attribution).
pub const EMBEDDED_PRIORS_PATH: &str = "docs/measurements/bootstrap_time_priors.json";

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct SuiteSeconds {
    pub click: f64,
    pub jinja: f64,
    /// Optional attrs hat; when 0 / absent, dry-run falls back to jinja.
    #[serde(default)]
    pub attrs: f64,
    #[serde(default)]
    pub measured: bool,
    #[serde(default)]
    pub metadata: BTreeMap<String, serde_json::Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BootstrapPriors {
    pub mutate_s_per_mutant: SuiteSeconds,
    pub backfill_s_per_commit: SuiteSeconds,
    pub collect_git_s: SuiteSeconds,
    #[serde(alias = "dump_train_s")]
    pub train_s: SuiteSeconds,
    #[serde(default)]
    pub features_dump_s_per_eligible: SuiteSeconds,
    #[serde(default)]
    pub eligible_per_attempted: BTreeMap<String, f64>,
    #[serde(default)]
    pub attempted_per_commit: BTreeMap<String, f64>,
}

#[derive(Debug, Clone)]
pub struct TimeEstimate {
    pub collect_git_s: f64,
    pub backfill_s: f64,
    pub backfill_unmeasured: bool,
    /// Hat for cold-start SHAs needing run ≈ `limb_n` (`max(N,20)` or `--backfill-commits`).
    pub backfill_shas_hat: u32,
    pub mutate_s: f64,
    pub mutate_jobs: u32,
    pub eligible_hat: f64,
    pub eligible_per_attempted: f64,
    pub features_dump_s: f64,
    pub train_s: f64,
    pub total_s: f64,
    pub suite: String,
    pub attempted_hat: f64,
    pub backfill_reused: u32,
    pub calibration_jobs: Option<u64>,
    pub density: f64,
}

pub fn default_priors() -> BootstrapPriors {
    serde_json::from_str(EMBEDDED_PRIORS).expect("embedded bootstrap_time_priors.json")
}

pub fn load_priors_file(path: &Path) -> Result<BootstrapPriors> {
    let raw = fs::read_to_string(path).map_err(Error::Io)?;
    serde_json::from_str(&raw).map_err(|e| Error::Other(format!("priors.json: {e}")))
}

pub fn suite_pick(s: &SuiteSeconds, suite: &str) -> f64 {
    match suite {
        "jinja" => s.jinja,
        "attrs" => s.attrs,
        _ => s.click,
    }
}

/// Rough wall-clock estimate.
/// Cold-start hat: `limb_n = max(N, 20)` (or `--backfill-commits`) SHAs needing run — not N+20.
pub fn estimate_wall(
    n: u32,
    k: u32,
    jobs: u32,
    suite: &str,
    priors: &BootstrapPriors,
    limb_n: u32,
) -> TimeEstimate {
    let jobs = jobs.max(1);
    // Clean-start n_new ≈ FP limb length (caller passes max(N,20) or override).
    let b_hat = limb_n;
    let collect = suite_pick(&priors.collect_git_s, suite);
    let backfill_per = suite_pick(&priors.backfill_s_per_commit, suite);
    let backfill = backfill_per * f64::from(b_hat);
    let density = priors
        .attempted_per_commit
        .get(suite)
        .copied()
        .unwrap_or(1.0);
    let mutants = f64::from(n.min(limb_n)) * density * f64::from(k) / 4.0;
    let mutate = suite_pick(&priors.mutate_s_per_mutant, suite) * mutants;
    let eligible_per_attempted = priors
        .eligible_per_attempted
        .get(suite)
        .copied()
        .unwrap_or(0.0);
    let eligible_hat = mutants * eligible_per_attempted;
    let features_dump = eligible_hat * suite_pick(&priors.features_dump_s_per_eligible, suite);
    let train = suite_pick(&priors.train_s, suite);
    TimeEstimate {
        collect_git_s: collect,
        backfill_s: backfill,
        backfill_unmeasured: !priors.backfill_s_per_commit.is_measured(suite),
        backfill_shas_hat: b_hat,
        mutate_s: mutate,
        mutate_jobs: jobs,
        eligible_hat,
        eligible_per_attempted,
        features_dump_s: features_dump,
        train_s: train,
        total_s: collect + backfill + mutate + features_dump + train,
        suite: suite.to_string(),
        attempted_hat: mutants,
        backfill_reused: 0,
        calibration_jobs: priors
            .mutate_s_per_mutant
            .metadata
            .get(suite)
            .and_then(|v| v["jobs"].as_u64()),
        density,
    }
}

impl SuiteSeconds {
    pub fn is_measured(&self, suite: &str) -> bool {
        self.metadata
            .get(suite)
            .and_then(|v| v["measured"].as_bool())
            .unwrap_or(false)
    }
    pub fn record(&mut self, suite: &str, rate: f64, samples: u32, jobs: u32) {
        match suite {
            "jinja" => self.jinja = rate,
            "attrs" => self.attrs = rate,
            _ => self.click = rate,
        }
        self.metadata.insert(suite.into(), serde_json::json!({"measured":true,"source":"local bootstrap step wall / count","n_samples":samples,"jobs":jobs,"confidence":"low"}));
        self.measured = false; // legacy global marker cannot describe individual suites
    }
}

impl TimeEstimate {
    pub fn remaining(&mut self, priors: &BootstrapPriors, new: u32, reused: u32, completed: u32) {
        self.backfill_shas_hat = new;
        self.backfill_reused = reused;
        self.backfill_s = f64::from(new) * suite_pick(&priors.backfill_s_per_commit, &self.suite);
        self.attempted_hat = (self.attempted_hat - f64::from(completed)).max(0.0);
        self.mutate_s = self.attempted_hat * suite_pick(&priors.mutate_s_per_mutant, &self.suite);
        self.eligible_hat = self.attempted_hat * self.eligible_per_attempted;
        self.features_dump_s =
            self.eligible_hat * suite_pick(&priors.features_dump_s_per_eligible, &self.suite);
        self.train_s = suite_pick(&priors.train_s, &self.suite);
        self.total_s = self.collect_git_s
            + self.backfill_s
            + self.mutate_s
            + self.features_dump_s
            + self.train_s;
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
    est: &TimeEstimate,
    resume_allowed: bool,
    priors_source: &str,
) -> String {
    let mut out = String::new();
    out.push_str("testpilot bootstrap --dry-run\n");
    let b_override = match fp.b_override {
        Some(n) => n.to_string(),
        None => "null".to_string(),
    };
    out.push_str(&format!(
        "fingerprint: head={} N={} K={} seed={} B_policy={} B_override={b_override} bin={} schema_v={}\n",
        fp.head_sha, fp.n, fp.k, fp.seed, fp.b_policy, fp.bin_version, fp.schema_version
    ));
    out.push_str(&format!("priors: {priors_source}\n"));
    out.push_str(&format!(
        "plan: |B_set|≈{}  N×K={}  jobs={}  suite={}\n",
        est.backfill_shas_hat + est.backfill_reused,
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
        "estimate features_dump: {} (≈{:.3} eligible commits)\n",
        fmt_hours(est.features_dump_s),
        est.eligible_hat
    ));
    out.push_str(&format!("estimate train: {}\n", fmt_hours(est.train_s)));
    out.push_str(&format!("estimate total: {}\n", fmt_hours(est.total_s)));
    out.push_str(&format!(
        "components_s: collect={} backfill={} mutate={} features_dump={} train={}\n",
        est.collect_git_s, est.backfill_s, est.mutate_s, est.features_dump_s, est.train_s
    ));
    out.push_str(&format!("attempted_hat={} eligible_hat={} eligible_per_attempted={} density_per_history_commit={} calibration_K=4 calibration_jobs={} target_jobs={} n_reused={}\n", est.attempted_hat, est.eligible_hat, est.eligible_per_attempted, est.density, est.calibration_jobs.map(|n| n.to_string()).unwrap_or_else(|| "null".into()), est.mutate_jobs, est.backfill_reused));
    out.push_str("assumptions: per-suite W6 observed wall rates; attempted_hat=min(N,B)×density×K/4; eligible_hat=attempted_hat×W6 eligible/attempted; features_dump_hat=eligible_hat×W6 features_dump_s/eligible; calibration wall rates already include their observed jobs; attrs feature-dump/eligibility coefficients are cross-suite proxies, not attrs fit; worker-local setup unmeasured; legacy priors without density use 1 attempted/history-commit at K=4\n");
    out.push_str(&format!("total_s={:.3}\n", est.total_s));
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
