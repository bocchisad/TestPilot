//! T4 R10: ranker=cov_lines order + model_sha256 = sha256(model.json).

use std::fs;
use std::path::PathBuf;

use sha2::{Digest, Sha256};
use tempfile::tempdir;
use testpilot_core::features::schema::{FEATURES_VERSION, MODEL_FEATURE_NAMES};
use testpilot_core::lgbm::{Ranker, embedded_schema_sha256, load_model_dir};

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..")
}

fn baseline_model_json(extra: serde_json::Value) -> serde_json::Value {
    let mut base = serde_json::json!({
        "features_version": FEATURES_VERSION,
        "schema_sha256": embedded_schema_sha256(),
        "feature_names": MODEL_FEATURE_NAMES,
        "objective": "binary",
        "best_iteration": 1,
        "predict_raw_score": true,
        "ranker": "cov_lines",
        "bootstrap": {
            "verdict": "use_baseline",
            "reason": "insufficient_test_C",
            "gate": "synthetic_test_vs_cov_lines_cluster_C"
        },
        "constraints": { "categorical": false, "linear_tree": false }
    });
    if let Some(obj) = extra.as_object() {
        for (k, v) in obj {
            base[k] = v.clone();
        }
    }
    base
}

#[test]
fn load_baseline_only_hashes_model_json_bytes() {
    let dir = tempdir().unwrap();
    let model = dir.path().join("model");
    fs::create_dir_all(&model).unwrap();
    let raw =
        serde_json::to_string_pretty(&baseline_model_json(serde_json::json!({}))).unwrap() + "\n";
    fs::write(model.join("model.json"), &raw).unwrap();
    assert!(!model.join("model.txt").exists());

    let booster = load_model_dir(&model).expect("baseline-only load");
    assert_eq!(booster.meta.ranker, Ranker::CovLines);
    assert_eq!(
        booster.meta.model_sha256,
        format!("{:x}", Sha256::digest(raw.as_bytes()))
    );
}

#[test]
fn cov_lines_with_real_model_txt_still_hashes_json() {
    let dir = tempdir().unwrap();
    let model = dir.path().join("model");
    fs::create_dir_all(&model).unwrap();
    let fixture_txt = repo_root().join("tests/fixtures/reference_model/model.txt");
    assert!(fixture_txt.is_file(), "missing {fixture_txt:?}");
    fs::copy(&fixture_txt, model.join("model.txt")).unwrap();
    // best_iteration must fit truncated parse; reference has 50 trees — use 1 for speed of validate.
    let payload = baseline_model_json(serde_json::json!({ "best_iteration": 1 }));
    let raw = serde_json::to_string_pretty(&payload).unwrap() + "\n";
    fs::write(model.join("model.json"), &raw).unwrap();

    let booster = load_model_dir(&model).expect("load with model.txt");
    assert_eq!(booster.meta.ranker, Ranker::CovLines);
    assert!(!booster.trees.is_empty(), "expected trees from model.txt");
    assert_eq!(
        booster.meta.model_sha256,
        format!("{:x}", Sha256::digest(raw.as_bytes())),
        "R10: cov_lines hashes model.json bytes even when model.txt exists"
    );
}

#[test]
fn null_ci95_loads_as_baseline_without_panic() {
    let dir = tempdir().unwrap();
    let model = dir.path().join("model");
    fs::create_dir_all(&model).unwrap();
    let payload = baseline_model_json(serde_json::json!({
        "paired_delta_ci_synthetic_test_cov_lines": {
            "mean": null,
            "ci95": [null, null],
            "n": 0,
            "n_C": 0,
            "n_mutants": 0
        },
        "bootstrap": {
            "verdict": "use_baseline",
            "reason": "no_test_signal",
            "gate": "synthetic_test_vs_cov_lines_cluster_C"
        }
    }));
    let raw = serde_json::to_string_pretty(&payload).unwrap() + "\n";
    fs::write(model.join("model.json"), &raw).unwrap();
    let booster = load_model_dir(&model).expect("null ci must not panic");
    assert_eq!(booster.meta.ranker, Ranker::CovLines);
    assert_eq!(
        booster.meta.bootstrap.as_ref().map(|b| b.verdict.as_str()),
        Some("use_baseline")
    );
}
