//! 29-dimensional model feature vector from a [`FeatureRow`].

use crate::features::schema::MODEL_FEATURE_NAMES;
use crate::features::writer::FeatureRow;

pub fn model_feature_vector(row: &FeatureRow) -> Vec<f64> {
    MODEL_FEATURE_NAMES
        .iter()
        .map(|name| feature_at(row, name))
        .collect()
}

fn feature_at(row: &FeatureRow, name: &str) -> f64 {
    match name {
        "cc_max" => row.cc_max,
        "cc_sum" => row.cc_sum,
        "imp_dist" => row.imp_dist as f64,
        "imp_direct" => row.imp_direct as f64,
        "name_file" => row.name_file as f64,
        "name_jac" => row.name_jac,
        "name_func" => row.name_func as f64,
        "cov_known" => row.cov_known as f64,
        "cov_file" => row.cov_file as f64,
        "cov_func" => row.cov_func as f64,
        "cov_lines" => row.cov_lines as f64,
        "cov_breadth" => row.cov_breadth as f64,
        "cov_mod" => row.cov_mod as f64,
        "cov_lag" => row.cov_lag as f64,
        "t_fail_rate" => row.t_fail_rate,
        "t_fail_cnt" => row.t_fail_cnt as f64,
        "t_obs" => row.t_obs as f64,
        "pf_max" => row.pf_max,
        "pq_max" => row.pq_max,
        "t_age_days" => row.t_age_days,
        "dur" => row.dur as f64,
        "tfile_ntests" => row.tfile_ntests as f64,
        "t_changed" => row.t_changed as f64,
        "t_flaky_rate" => row.t_flaky_rate,
        "ch_nfiles" => row.ch_nfiles as f64,
        "ch_nsrc" => row.ch_nsrc as f64,
        "ch_add" => row.ch_add as f64,
        "ch_rm" => row.ch_rm as f64,
        "ch_nfunc" => row.ch_nfunc as f64,
        _ => 0.0,
    }
}
