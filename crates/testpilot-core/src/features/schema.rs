//! Feature column lockfile — single source of truth for dump/show/schema CLI.

pub const FEATURES_VERSION: u32 = 2;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FeatureColumn {
    // service
    Repo,
    Change,
    Parent,
    CommitIdx,
    Time,
    Test,
    Label,
    Flaky,
    Synthetic,
    BaseChange,
    // features
    CcMax,
    CcSum,
    ImpDist,
    ImpDirect,
    NameFile,
    NameJac,
    NameFunc,
    CovKnown,
    CovFile,
    CovFunc,
    CovLines,
    CovBreadth,
    CovMod,
    CovLag,
    TFailRate,
    TFailCnt,
    TObs,
    PfMax,
    PqMax,
    TAgeDays,
    Dur,
    TfileNtests,
    TChanged,
    TFlakyRate,
    ChNfiles,
    ChNsrc,
    ChAdd,
    ChRm,
    ChNfunc,
}

impl FeatureColumn {
    pub const fn name(self) -> &'static str {
        match self {
            Self::Repo => "repo",
            Self::Change => "change",
            Self::Parent => "parent",
            Self::CommitIdx => "commit_idx",
            Self::Time => "time",
            Self::Test => "test",
            Self::Label => "label",
            Self::Flaky => "flaky",
            Self::Synthetic => "synthetic",
            Self::BaseChange => "base_change",
            Self::CcMax => "cc_max",
            Self::CcSum => "cc_sum",
            Self::ImpDist => "imp_dist",
            Self::ImpDirect => "imp_direct",
            Self::NameFile => "name_file",
            Self::NameJac => "name_jac",
            Self::NameFunc => "name_func",
            Self::CovKnown => "cov_known",
            Self::CovFile => "cov_file",
            Self::CovFunc => "cov_func",
            Self::CovLines => "cov_lines",
            Self::CovBreadth => "cov_breadth",
            Self::CovMod => "cov_mod",
            Self::CovLag => "cov_lag",
            Self::TFailRate => "t_fail_rate",
            Self::TFailCnt => "t_fail_cnt",
            Self::TObs => "t_obs",
            Self::PfMax => "pf_max",
            Self::PqMax => "pq_max",
            Self::TAgeDays => "t_age_days",
            Self::Dur => "dur",
            Self::TfileNtests => "tfile_ntests",
            Self::TChanged => "t_changed",
            Self::TFlakyRate => "t_flaky_rate",
            Self::ChNfiles => "ch_nfiles",
            Self::ChNsrc => "ch_nsrc",
            Self::ChAdd => "ch_add",
            Self::ChRm => "ch_rm",
            Self::ChNfunc => "ch_nfunc",
        }
    }

    /// Arrow / Parquet logical type hint for schema JSON.
    pub const fn dtype(self) -> &'static str {
        match self {
            Self::Repo | Self::Change | Self::Parent | Self::Test | Self::BaseChange => "utf8",
            Self::CommitIdx
            | Self::Time
            | Self::Label
            | Self::Flaky
            | Self::Synthetic
            | Self::ImpDist
            | Self::ImpDirect
            | Self::NameFile
            | Self::NameFunc
            | Self::CovKnown
            | Self::CovFile
            | Self::CovFunc
            | Self::CovLines
            | Self::CovBreadth
            | Self::CovMod
            | Self::CovLag
            | Self::TFailCnt
            | Self::TObs
            | Self::TfileNtests
            | Self::TChanged
            | Self::ChNfiles
            | Self::ChNsrc
            | Self::ChAdd
            | Self::ChRm
            | Self::ChNfunc
            | Self::Dur => "int64",
            Self::CcMax
            | Self::CcSum
            | Self::NameJac
            | Self::TFailRate
            | Self::PfMax
            | Self::PqMax
            | Self::TAgeDays
            | Self::TFlakyRate => "float64",
        }
    }
}

/// The 29 model feature names in training/select order (no service columns).
pub const MODEL_FEATURE_NAMES: &[&str] = &[
    "cc_max",
    "cc_sum",
    "imp_dist",
    "imp_direct",
    "name_file",
    "name_jac",
    "name_func",
    "cov_known",
    "cov_file",
    "cov_func",
    "cov_lines",
    "cov_breadth",
    "cov_mod",
    "cov_lag",
    "t_fail_rate",
    "t_fail_cnt",
    "t_obs",
    "pf_max",
    "pq_max",
    "t_age_days",
    "dur",
    "tfile_ntests",
    "t_changed",
    "t_flaky_rate",
    "ch_nfiles",
    "ch_nsrc",
    "ch_add",
    "ch_rm",
    "ch_nfunc",
];

pub const FEATURE_COLUMNS: &[FeatureColumn] = &[
    FeatureColumn::Repo,
    FeatureColumn::Change,
    FeatureColumn::Parent,
    FeatureColumn::CommitIdx,
    FeatureColumn::Time,
    FeatureColumn::Test,
    FeatureColumn::Label,
    FeatureColumn::Flaky,
    FeatureColumn::Synthetic,
    FeatureColumn::BaseChange,
    FeatureColumn::CcMax,
    FeatureColumn::CcSum,
    FeatureColumn::ImpDist,
    FeatureColumn::ImpDirect,
    FeatureColumn::NameFile,
    FeatureColumn::NameJac,
    FeatureColumn::NameFunc,
    FeatureColumn::CovKnown,
    FeatureColumn::CovFile,
    FeatureColumn::CovFunc,
    FeatureColumn::CovLines,
    FeatureColumn::CovBreadth,
    FeatureColumn::CovMod,
    FeatureColumn::CovLag,
    FeatureColumn::TFailRate,
    FeatureColumn::TFailCnt,
    FeatureColumn::TObs,
    FeatureColumn::PfMax,
    FeatureColumn::PqMax,
    FeatureColumn::TAgeDays,
    FeatureColumn::Dur,
    FeatureColumn::TfileNtests,
    FeatureColumn::TChanged,
    FeatureColumn::TFlakyRate,
    FeatureColumn::ChNfiles,
    FeatureColumn::ChNsrc,
    FeatureColumn::ChAdd,
    FeatureColumn::ChRm,
    FeatureColumn::ChNfunc,
];

/// JSON document for `features_schema.json` / `features schema --check`.
pub fn schema_json() -> String {
    let cols: Vec<serde_json::Value> = FEATURE_COLUMNS
        .iter()
        .map(|c| {
            serde_json::json!({
                "name": c.name(),
                "dtype": c.dtype(),
            })
        })
        .collect();
    serde_json::to_string_pretty(&serde_json::json!({
        "features_version": FEATURES_VERSION,
        "columns": cols,
    }))
    .expect("schema json")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn column_names_unique_and_stable_count() {
        let mut names: Vec<_> = FEATURE_COLUMNS.iter().map(|c| c.name()).collect();
        let n = names.len();
        names.sort_unstable();
        names.dedup();
        assert_eq!(names.len(), n);
        assert_eq!(n, 39);
    }
}
