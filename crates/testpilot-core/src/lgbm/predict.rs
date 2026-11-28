//! Raw-score prediction matching LightGBM `NumericalDecision` (tree.h).

use super::parse::{Booster, Tree};
use crate::error::{Error, Result};

const K_CATEGORICAL_MASK: u8 = 1;
const K_DEFAULT_LEFT_MASK: u8 = 2;

/// TreeSHAP contributions for a raw LightGBM score. The contributions are in
/// model feature order and `bias + sum(feature_contributions)` reconstructs
/// the raw score.
#[derive(Debug, Clone, PartialEq)]
pub struct PredictionContributions {
    pub bias: f64,
    pub feature_contributions: Vec<f64>,
}

#[derive(Clone, Copy, Debug)]
struct PathElement {
    feature: i32,
    zero_fraction: f64,
    one_fraction: f64,
    weight: f64,
}

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

/// Compute LightGBM `pred_contrib=True`-style TreeSHAP values for one row.
pub fn predict_contrib(booster: &Booster, features: &[f64]) -> Result<PredictionContributions> {
    if features.len() != booster.feature_names.len() {
        return Err(Error::Other(format!(
            "feature vector length {}: expected {}",
            features.len(),
            booster.feature_names.len()
        )));
    }
    let mut contributions = vec![0.0; features.len()];
    let mut bias = 0.0;
    for tree in &booster.trees {
        if tree.internal_count.is_none() || tree.leaf_count.is_none() {
            return Err(Error::Other(
                "model.txt lacks node counts required for explanations".into(),
            ));
        }
        tree_shap_recursive(
            tree,
            features,
            0,
            Vec::new(),
            1.0,
            1.0,
            -1,
            &mut contributions,
        )?;
        bias += expected_value(tree, 0)?;
    }
    Ok(PredictionContributions {
        bias,
        feature_contributions: contributions,
    })
}

#[allow(clippy::too_many_arguments)] // mirrors the explicit TreeSHAP recursion state
fn tree_shap_recursive(
    tree: &Tree,
    features: &[f64],
    node: i32,
    parent_path: Vec<PathElement>,
    parent_zero_fraction: f64,
    parent_one_fraction: f64,
    parent_feature: i32,
    contributions: &mut [f64],
) -> Result<()> {
    let mut path = parent_path;
    extend_path(
        &mut path,
        parent_zero_fraction,
        parent_one_fraction,
        parent_feature,
    );
    if node < 0 {
        let leaf_value = *tree
            .leaf_value
            .get((!node) as usize)
            .ok_or_else(|| Error::Other(format!("leaf index {} out of range", !node)))?;
        let unique_depth = path.len() - 1;
        for path_index in 1..=unique_depth {
            let feature = path[path_index].feature;
            if feature < 0 || feature as usize >= contributions.len() {
                return Err(Error::Other(format!(
                    "bad TreeSHAP feature index {feature}"
                )));
            }
            let weight = unwound_path_sum(&path, unique_depth, path_index);
            contributions[feature as usize] += weight
                * (path[path_index].one_fraction - path[path_index].zero_fraction)
                * leaf_value;
        }
        return Ok(());
    }

    let node_index = node as usize;
    let feature = *tree
        .split_feature
        .get(node_index)
        .ok_or_else(|| Error::Other(format!("split_feature[{node_index}] missing")))?;
    let (hot, cold) = split_children(tree, features, node_index)?;
    let cover = node_cover(tree, node)?;
    if cover <= 0.0 {
        return Err(Error::Other(format!(
            "nonpositive cover at tree node {node}"
        )));
    }
    let hot_zero = node_cover(tree, hot)? / cover;
    let cold_zero = node_cover(tree, cold)? / cover;

    let (incoming_zero, incoming_one) =
        if let Some(path_index) = path.iter().position(|element| element.feature == feature) {
            let fractions = (
                path[path_index].zero_fraction,
                path[path_index].one_fraction,
            );
            unwind_path(&mut path, path_index);
            fractions
        } else {
            (1.0, 1.0)
        };

    tree_shap_recursive(
        tree,
        features,
        hot,
        path.clone(),
        hot_zero * incoming_zero,
        incoming_one,
        feature,
        contributions,
    )?;
    tree_shap_recursive(
        tree,
        features,
        cold,
        path,
        cold_zero * incoming_zero,
        0.0,
        feature,
        contributions,
    )
}

fn split_children(tree: &Tree, features: &[f64], node: usize) -> Result<(i32, i32)> {
    let dt = *tree
        .decision_type
        .get(node)
        .ok_or_else(|| Error::Other(format!("decision_type[{node}] missing")))?;
    if dt & K_CATEGORICAL_MASK != 0 {
        return Err(Error::Other("categorical split at explain time".into()));
    }
    let feature = *tree
        .split_feature
        .get(node)
        .ok_or_else(|| Error::Other(format!("split_feature[{node}] missing")))?;
    if feature < 0 || feature as usize >= features.len() {
        return Err(Error::Other(format!("bad split_feature {feature}")));
    }
    let mut value = features[feature as usize];
    let missing = missing_type(dt);
    if value.is_nan() && missing != 2 {
        value = 0.0;
    }
    let go_left = if (missing == 1 && is_zero(value)) || (missing == 2 && value.is_nan()) {
        dt & K_DEFAULT_LEFT_MASK != 0
    } else {
        value
            <= *tree
                .threshold
                .get(node)
                .ok_or_else(|| Error::Other(format!("threshold[{node}] missing")))?
    };
    let left = *tree
        .left_child
        .get(node)
        .ok_or_else(|| Error::Other(format!("left_child[{node}] missing")))?;
    let right = *tree
        .right_child
        .get(node)
        .ok_or_else(|| Error::Other(format!("right_child[{node}] missing")))?;
    Ok(if go_left {
        (left, right)
    } else {
        (right, left)
    })
}

fn node_cover(tree: &Tree, node: i32) -> Result<f64> {
    let counts = if node < 0 {
        tree.leaf_count.as_ref().ok_or_else(|| {
            Error::Other("model.txt lacks leaf_count required for explanations".into())
        })?
    } else {
        tree.internal_count.as_ref().ok_or_else(|| {
            Error::Other("model.txt lacks internal_count required for explanations".into())
        })?
    };
    let index = if node < 0 {
        (!node) as usize
    } else {
        node as usize
    };
    counts
        .get(index)
        .copied()
        .ok_or_else(|| Error::Other(format!("node count index {index} out of range")))
}

fn expected_value(tree: &Tree, node: i32) -> Result<f64> {
    if node < 0 {
        return tree
            .leaf_value
            .get((!node) as usize)
            .copied()
            .ok_or_else(|| Error::Other(format!("leaf index {} out of range", !node)));
    }
    let index = node as usize;
    let left = *tree
        .left_child
        .get(index)
        .ok_or_else(|| Error::Other(format!("left_child[{index}] missing")))?;
    let right = *tree
        .right_child
        .get(index)
        .ok_or_else(|| Error::Other(format!("right_child[{index}] missing")))?;
    let cover = node_cover(tree, node)?;
    if cover <= 0.0 {
        return Err(Error::Other(format!(
            "nonpositive cover at tree node {node}"
        )));
    }
    Ok((node_cover(tree, left)? * expected_value(tree, left)?
        + node_cover(tree, right)? * expected_value(tree, right)?)
        / cover)
}

fn extend_path(path: &mut Vec<PathElement>, zero_fraction: f64, one_fraction: f64, feature: i32) {
    let depth = path.len();
    path.push(PathElement {
        feature,
        zero_fraction,
        one_fraction,
        weight: if depth == 0 { 1.0 } else { 0.0 },
    });
    for i in (0..depth).rev() {
        path[i + 1].weight += one_fraction * path[i].weight * (i + 1) as f64 / (depth + 1) as f64;
        path[i].weight = zero_fraction * path[i].weight * (depth - i) as f64 / (depth + 1) as f64;
    }
}

fn unwind_path(path: &mut Vec<PathElement>, path_index: usize) {
    let depth = path.len() - 1;
    let one_fraction = path[path_index].one_fraction;
    let zero_fraction = path[path_index].zero_fraction;
    let mut next_one_portion = path[depth].weight;
    for i in (0..depth).rev() {
        if one_fraction != 0.0 {
            let saved = path[i].weight;
            path[i].weight =
                next_one_portion * (depth + 1) as f64 / ((i + 1) as f64 * one_fraction);
            next_one_portion =
                saved - path[i].weight * zero_fraction * (depth - i) as f64 / (depth + 1) as f64;
        } else if zero_fraction != 0.0 && depth != i {
            path[i].weight =
                path[i].weight * (depth + 1) as f64 / (zero_fraction * (depth - i) as f64);
        }
    }
    for i in path_index..depth {
        path[i].feature = path[i + 1].feature;
        path[i].zero_fraction = path[i + 1].zero_fraction;
        path[i].one_fraction = path[i + 1].one_fraction;
    }
    path.truncate(depth);
}

fn unwound_path_sum(path: &[PathElement], depth: usize, path_index: usize) -> f64 {
    let one_fraction = path[path_index].one_fraction;
    let zero_fraction = path[path_index].zero_fraction;
    let mut total = 0.0;
    let mut next_one_portion = path[depth].weight;
    for i in (0..depth).rev() {
        if one_fraction != 0.0 {
            let tmp = next_one_portion * (depth + 1) as f64 / ((i + 1) as f64 * one_fraction);
            total += tmp;
            next_one_portion =
                path[i].weight - tmp * zero_fraction * (depth - i) as f64 / (depth + 1) as f64;
        } else if zero_fraction != 0.0 && depth != i {
            total += path[i].weight * (depth + 1) as f64 / (zero_fraction * (depth - i) as f64);
        }
    }
    total
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
