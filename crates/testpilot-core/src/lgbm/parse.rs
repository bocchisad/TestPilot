//! Parse LightGBM v4 text `model.txt` + validate against `model.json` and
//! the schema embedded in the binary.

use std::fs;
use std::io::Write;
use std::path::Path;

use serde_json::Value;
use sha2::{Digest, Sha256};

use crate::error::{Error, Result};
use crate::features::schema::{FEATURES_VERSION, MODEL_FEATURE_NAMES};

/// Bytes of repo-root `features_schema.json` (locked at compile time).
pub const EMBEDDED_FEATURES_SCHEMA: &[u8] = include_bytes!("../../../../features_schema.json");

#[derive(Debug, Clone)]
pub struct ModelMeta {
    pub features_version: u32,
    pub schema_sha256: String,
    pub feature_names: Vec<String>,
    pub objective: String,
    pub best_iteration: usize,
    pub model_sha256: String,
    /// True when useful-on-real gate is null / synthetic-only (A10).
    pub synthetic_only_warning: bool,
}

#[derive(Debug, Clone)]
pub struct Tree {
    pub num_leaves: i32,
    pub split_feature: Vec<i32>,
    pub threshold: Vec<f64>,
    pub decision_type: Vec<u8>,
    pub left_child: Vec<i32>,
    pub right_child: Vec<i32>,
    pub leaf_value: Vec<f64>,
}

#[derive(Debug, Clone)]
pub struct Booster {
    pub meta: ModelMeta,
    pub trees: Vec<Tree>,
    pub feature_names: Vec<String>,
}

pub fn embedded_schema_sha256() -> String {
    hex::encode(Sha256::digest(EMBEDDED_FEATURES_SCHEMA))
}

pub fn load_model_dir(dir: &Path) -> Result<Booster> {
    let txt_path = dir.join("model.txt");
    let json_path = dir.join("model.json");
    let txt = fs::read_to_string(&txt_path)
        .map_err(|e| Error::Other(format!("read {}: {e}", txt_path.display())))?;
    let json_raw = fs::read_to_string(&json_path)
        .map_err(|e| Error::Other(format!("read {}: {e}", json_path.display())))?;
    let model_sha256 = hex::encode(Sha256::digest(txt.as_bytes()));
    let meta_val: Value = serde_json::from_str(&json_raw)
        .map_err(|e| Error::Other(format!("parse model.json: {e}")))?;
    let meta = validate_model_json(&meta_val, &model_sha256)?;
    let (header_names, mut trees) = parse_model_txt(&txt)?;
    if header_names != meta.feature_names {
        return Err(Error::Other(
            "model.txt feature_names do not match model.json".into(),
        ));
    }
    if meta.best_iteration > trees.len() {
        return Err(Error::Other(format!(
            "best_iteration={} exceeds tree count {} in model.txt",
            meta.best_iteration,
            trees.len()
        )));
    }
    trees.truncate(meta.best_iteration);
    Ok(Booster {
        meta,
        trees,
        feature_names: header_names,
    })
}

fn validate_model_json(v: &Value, model_sha256: &str) -> Result<ModelMeta> {
    let features_version = v
        .get("features_version")
        .and_then(|x| x.as_u64())
        .ok_or_else(|| Error::Other("model.json missing features_version".into()))?
        as u32;
    if features_version != FEATURES_VERSION {
        return Err(Error::Other(format!(
            "features_version mismatch: model={features_version} embedded={FEATURES_VERSION}"
        )));
    }
    let schema_sha256 = v
        .get("schema_sha256")
        .and_then(|x| x.as_str())
        .ok_or_else(|| Error::Other("model.json missing schema_sha256".into()))?
        .to_string();
    let embedded = embedded_schema_sha256();
    if schema_sha256 != embedded {
        return Err(Error::Other(format!(
            "schema_sha256 mismatch: model.json={schema_sha256} embedded={embedded}"
        )));
    }
    let feature_names: Vec<String> = v
        .get("feature_names")
        .and_then(|x| x.as_array())
        .ok_or_else(|| Error::Other("model.json missing feature_names".into()))?
        .iter()
        .filter_map(|x| x.as_str().map(str::to_string))
        .collect();
    if feature_names.len() != MODEL_FEATURE_NAMES.len() {
        return Err(Error::Other(format!(
            "feature_names length {}: expected {}",
            feature_names.len(),
            MODEL_FEATURE_NAMES.len()
        )));
    }
    for (i, (a, b)) in feature_names
        .iter()
        .zip(MODEL_FEATURE_NAMES.iter())
        .enumerate()
    {
        if a != *b {
            return Err(Error::Other(format!(
                "feature_names[{i}] mismatch: model={a} embedded={b}"
            )));
        }
    }
    let objective = v
        .get("objective")
        .and_then(|x| x.as_str())
        .ok_or_else(|| Error::Other("model.json missing objective".into()))?
        .to_string();
    let obj_ok = objective == "binary"
        || objective.starts_with("binary ")
        || objective == "lambdarank"
        || objective.starts_with("lambdarank");
    if !obj_ok {
        return Err(Error::Other(format!(
            "unsupported objective {objective:?}; expected binary or lambdarank"
        )));
    }
    if let Some(c) = v.get("constraints") {
        if c.get("categorical") == Some(&Value::Bool(true)) {
            return Err(Error::Other(
                "model uses categorical features (unsupported)".into(),
            ));
        }
        if c.get("linear_tree") == Some(&Value::Bool(true)) {
            return Err(Error::Other("model uses linear_tree (unsupported)".into()));
        }
    }
    if let Some(prs) = v.get("predict_raw_score") {
        if prs != &Value::Bool(true) {
            let _ = writeln!(
                std::io::stderr(),
                "warning: model.json predict_raw_score is not true; still using raw margins"
            );
        }
    }
    let best_iteration = v
        .get("best_iteration")
        .and_then(|x| x.as_u64())
        .ok_or_else(|| Error::Other("model.json missing best_iteration".into()))?
        as usize;
    if best_iteration == 0 {
        return Err(Error::Other("best_iteration must be positive".into()));
    }

    let synthetic_only_warning = matches!(v.get("useful_on_val"), Some(Value::Null))
        || matches!(v.get("useful_on_real_val"), Some(Value::Null))
        || (v.get("useful_on_val_label").and_then(|x| x.as_str()) == Some("useful_on_real_val")
            && matches!(v.get("useful_on_val"), Some(Value::Null)));

    Ok(ModelMeta {
        features_version,
        schema_sha256,
        feature_names,
        objective,
        best_iteration,
        model_sha256: model_sha256.to_string(),
        synthetic_only_warning,
    })
}

fn parse_model_txt(text: &str) -> Result<(Vec<String>, Vec<Tree>)> {
    let mut feature_names: Option<Vec<String>> = None;
    let mut num_class: Option<i32> = None;
    let mut num_tree_per_iteration: Option<i32> = None;
    let mut trees: Vec<Tree> = Vec::new();
    let mut cur: Option<TreeBuilder> = None;

    for raw in text.lines() {
        let line = raw.trim_end();
        if line.is_empty() {
            continue;
        }
        if let Some(rest) = line.strip_prefix("Tree=") {
            if let Some(b) = cur.take() {
                trees.push(b.finish()?);
            }
            let _idx: i32 = rest
                .parse()
                .map_err(|_| Error::Other(format!("bad Tree= line: {line}")))?;
            cur = Some(TreeBuilder::default());
            continue;
        }
        if let Some(b) = cur.as_mut() {
            b.feed(line)?;
            continue;
        }
        // header
        if let Some(v) = line.strip_prefix("num_class=") {
            num_class = Some(parse_i32(v, "num_class")?);
        } else if let Some(v) = line.strip_prefix("num_tree_per_iteration=") {
            num_tree_per_iteration = Some(parse_i32(v, "num_tree_per_iteration")?);
        } else if let Some(v) = line.strip_prefix("feature_names=") {
            feature_names = Some(v.split_whitespace().map(str::to_string).collect());
        } else if let Some(v) = line.strip_prefix("objective=") {
            let o = v.trim();
            if o.contains("multiclass") {
                return Err(Error::Other(format!(
                    "unsupported objective in model.txt: {o}"
                )));
            }
        }
    }
    if let Some(b) = cur.take() {
        trees.push(b.finish()?);
    }
    if num_class != Some(1) {
        return Err(Error::Other(format!(
            "num_class must be 1, got {num_class:?}"
        )));
    }
    if num_tree_per_iteration != Some(1) {
        return Err(Error::Other(format!(
            "num_tree_per_iteration must be 1, got {num_tree_per_iteration:?}"
        )));
    }
    let names =
        feature_names.ok_or_else(|| Error::Other("model.txt missing feature_names".into()))?;
    Ok((names, trees))
}

fn parse_i32(s: &str, what: &str) -> Result<i32> {
    s.trim()
        .parse()
        .map_err(|_| Error::Other(format!("bad {what}: {s}")))
}

#[derive(Default)]
struct TreeBuilder {
    num_leaves: Option<i32>,
    num_cat: Option<i32>,
    split_feature: Option<Vec<i32>>,
    threshold: Option<Vec<f64>>,
    decision_type: Option<Vec<u8>>,
    left_child: Option<Vec<i32>>,
    right_child: Option<Vec<i32>>,
    leaf_value: Option<Vec<f64>>,
    is_linear: Option<i32>,
}

impl TreeBuilder {
    fn feed(&mut self, line: &str) -> Result<()> {
        if let Some(v) = line.strip_prefix("num_leaves=") {
            self.num_leaves = Some(parse_i32(v, "num_leaves")?);
        } else if let Some(v) = line.strip_prefix("num_cat=") {
            self.num_cat = Some(parse_i32(v, "num_cat")?);
        } else if let Some(v) = line.strip_prefix("split_feature=") {
            self.split_feature = Some(parse_i32_list(v)?);
        } else if let Some(v) = line.strip_prefix("threshold=") {
            self.threshold = Some(parse_f64_list(v)?);
        } else if let Some(v) = line.strip_prefix("decision_type=") {
            self.decision_type = Some(parse_u8_list(v)?);
        } else if let Some(v) = line.strip_prefix("left_child=") {
            self.left_child = Some(parse_i32_list(v)?);
        } else if let Some(v) = line.strip_prefix("right_child=") {
            self.right_child = Some(parse_i32_list(v)?);
        } else if let Some(v) = line.strip_prefix("leaf_value=") {
            self.leaf_value = Some(parse_f64_list(v)?);
        } else if let Some(v) = line.strip_prefix("is_linear=") {
            self.is_linear = Some(parse_i32(v, "is_linear")?);
        }
        Ok(())
    }

    fn finish(self) -> Result<Tree> {
        let num_leaves = self
            .num_leaves
            .ok_or_else(|| Error::Other("tree missing num_leaves".into()))?;
        let num_cat = self.num_cat.unwrap_or(0);
        if num_cat != 0 {
            return Err(Error::Other(format!(
                "categorical trees unsupported (num_cat={num_cat})"
            )));
        }
        if self.is_linear == Some(1) {
            return Err(Error::Other("linear_tree unsupported (is_linear=1)".into()));
        }
        let split_feature = self
            .split_feature
            .ok_or_else(|| Error::Other("tree missing split_feature".into()))?;
        let threshold = self
            .threshold
            .ok_or_else(|| Error::Other("tree missing threshold".into()))?;
        let decision_type = self
            .decision_type
            .ok_or_else(|| Error::Other("tree missing decision_type".into()))?;
        let left_child = self
            .left_child
            .ok_or_else(|| Error::Other("tree missing left_child".into()))?;
        let right_child = self
            .right_child
            .ok_or_else(|| Error::Other("tree missing right_child".into()))?;
        let leaf_value = self
            .leaf_value
            .ok_or_else(|| Error::Other("tree missing leaf_value".into()))?;
        for &dt in &decision_type {
            if dt & 1 != 0 {
                return Err(Error::Other(
                    "categorical split bit set in decision_type".into(),
                ));
            }
        }
        Ok(Tree {
            num_leaves,
            split_feature,
            threshold,
            decision_type,
            left_child,
            right_child,
            leaf_value,
        })
    }
}

fn parse_i32_list(s: &str) -> Result<Vec<i32>> {
    s.split_whitespace()
        .map(|x| {
            x.parse()
                .map_err(|_| Error::Other(format!("bad i32 in list: {x}")))
        })
        .collect()
}

fn parse_u8_list(s: &str) -> Result<Vec<u8>> {
    s.split_whitespace()
        .map(|x| {
            x.parse()
                .map_err(|_| Error::Other(format!("bad u8 in list: {x}")))
        })
        .collect()
}

fn parse_f64_list(s: &str) -> Result<Vec<f64>> {
    s.split_whitespace()
        .map(|x| {
            x.parse()
                .map_err(|_| Error::Other(format!("bad f64 in list: {x}")))
        })
        .collect()
}
