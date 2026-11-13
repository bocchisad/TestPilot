//! LightGBM raw_score parity vs exact-f64 CSV fixture (≤ 1e-6).

use std::fs;
use std::path::PathBuf;

use testpilot_core::features::schema::MODEL_FEATURE_NAMES;
use testpilot_core::lgbm::{load_model_dir, predict_raw};

fn fixture_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../tests/fixtures/reference_model")
}

fn parse_f64_cell(s: &str) -> f64 {
    // CSV cells are Python `repr(float)` — Rust `parse` accepts the same decimal form.
    s.parse::<f64>()
        .unwrap_or_else(|e| panic!("bad f64 {s:?}: {e}"))
}

#[test]
fn raw_score_parity_max_abs_err_le_1e_6() {
    let dir = fixture_dir();
    let booster = load_model_dir(&dir).expect("load model");
    assert_eq!(booster.meta.best_iteration, booster.trees.len());

    let csv = fs::read_to_string(dir.join("parity_scores.csv")).expect("parity csv");
    let mut lines = csv.lines();
    let header = lines.next().expect("header");
    let cols: Vec<&str> = header.split(',').collect();
    assert_eq!(&cols[3], &"raw_score");
    for (i, name) in MODEL_FEATURE_NAMES.iter().enumerate() {
        assert_eq!(cols[4 + i], *name, "column {}", 4 + i);
    }

    let mut max_abs_err = 0.0_f64;
    let mut n = 0_usize;
    let mut feats = vec![0.0; MODEL_FEATURE_NAMES.len()];
    for line in lines {
        if line.is_empty() {
            continue;
        }
        let cells: Vec<&str> = line.split(',').collect();
        assert_eq!(cells.len(), cols.len());
        let expected = parse_f64_cell(cells[3]);
        for i in 0..MODEL_FEATURE_NAMES.len() {
            feats[i] = parse_f64_cell(cells[4 + i]);
        }
        let got = predict_raw(&booster, &feats).expect("predict");
        let err = (got - expected).abs();
        if err > max_abs_err {
            max_abs_err = err;
        }
        n += 1;
    }
    eprintln!("max_abs_err={max_abs_err} n={n}");
    assert!(n > 0);
    assert!(
        max_abs_err <= 1e-6,
        "max_abs_err={max_abs_err} exceeds 1e-6 (n={n})"
    );
}

#[test]
fn rejects_wrong_schema_sha256() {
    let dir = fixture_dir();
    let tmp = tempfile::tempdir().unwrap();
    fs::copy(dir.join("model.txt"), tmp.path().join("model.txt")).unwrap();
    let mut meta: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(dir.join("model.json")).unwrap()).unwrap();
    meta["schema_sha256"] = serde_json::json!("0".repeat(64));
    fs::write(
        tmp.path().join("model.json"),
        serde_json::to_string_pretty(&meta).unwrap(),
    )
    .unwrap();
    let err = load_model_dir(tmp.path()).unwrap_err();
    let msg = format!("{err}");
    assert!(
        msg.contains("schema_sha256"),
        "expected schema_sha256 error, got {msg}"
    );
}

#[test]
fn rejects_best_iteration_too_large() {
    let dir = fixture_dir();
    let tmp = tempfile::tempdir().unwrap();
    fs::copy(dir.join("model.txt"), tmp.path().join("model.txt")).unwrap();
    let mut meta: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(dir.join("model.json")).unwrap()).unwrap();
    meta["best_iteration"] = serde_json::json!(10_000);
    fs::write(
        tmp.path().join("model.json"),
        serde_json::to_string_pretty(&meta).unwrap(),
    )
    .unwrap();
    let err = load_model_dir(tmp.path()).unwrap_err();
    let msg = format!("{err}");
    assert!(
        msg.contains("best_iteration"),
        "expected best_iteration error, got {msg}"
    );
}
