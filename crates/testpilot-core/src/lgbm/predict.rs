//! Raw-score prediction matching LightGBM `NumericalDecision` (tree.h).

use super::parse::{Booster, Tree};
use crate::error::{Error, Result};

const K_CATEGORICAL_MASK: u8 = 1;
const K_DEFAULT_LEFT_MASK: u8 = 2;

#[inline]
fn missing_type(decision_type: u8) -> u8 {
    (decision_type >> 2) & 3
}

#[inline]
fn is_zero(fval: f64) -> bool {
    fval > -1e-35 && fval <= 1e-35
}

/// Predict raw margin for one row (`features.len()` must equal booster feature count).
pub fn predict_raw(booster: &Booster, features: &[f64]) -> Result<f64> {
    if features.len() != booster.feature_names.len() {
        return Err(Error::Other(format!(
            "feature vector length {}: expected {}",
            features.len(),
            booster.feature_names.len()
        )));
    }
    let mut score = 0.0;
    for tree in &booster.trees {
        score += predict_tree(tree, features)?;
    }
    Ok(score)
}

fn predict_tree(tree: &Tree, features: &[f64]) -> Result<f64> {
    let mut node: i32 = 0;
    loop {
        if node < 0 {
            // leaf index = ~node (bitwise not) in LightGBM
            let leaf = !node;
            let leaf_usize = leaf as usize;
            return tree
                .leaf_value
                .get(leaf_usize)
                .copied()
                .ok_or_else(|| Error::Other(format!("leaf index {leaf} out of range")));
        }
        let n = node as usize;
        let dt = *tree
            .decision_type
            .get(n)
            .ok_or_else(|| Error::Other(format!("decision_type[{n}] missing")))?;
        if dt & K_CATEGORICAL_MASK != 0 {
            return Err(Error::Other("categorical split at predict time".into()));
        }
        let feat_idx = *tree
            .split_feature
            .get(n)
            .ok_or_else(|| Error::Other(format!("split_feature[{n}] missing")))?;
        if feat_idx < 0 || feat_idx as usize >= features.len() {
            return Err(Error::Other(format!("bad split_feature {feat_idx}")));
        }
        let mut fval = features[feat_idx as usize];
        let missing = missing_type(dt);
        // MissingType::None = 0, Zero = 1, NaN = 2
        if fval.is_nan() && missing != 2 {
            fval = 0.0;
        }
        let go_left = if (missing == 1 && is_zero(fval)) || (missing == 2 && fval.is_nan()) {
            dt & K_DEFAULT_LEFT_MASK != 0
        } else {
            let thr = *tree
                .threshold
                .get(n)
                .ok_or_else(|| Error::Other(format!("threshold[{n}] missing")))?;
            fval <= thr
        };
        node = if go_left {
            *tree
                .left_child
                .get(n)
                .ok_or_else(|| Error::Other(format!("left_child[{n}] missing")))?
        } else {
            *tree
                .right_child
                .get(n)
                .ok_or_else(|| Error::Other(format!("right_child[{n}] missing")))?
        };
    }
}
