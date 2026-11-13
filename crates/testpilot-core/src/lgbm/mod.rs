//! Pure-Rust LightGBM text-model loader and raw_score prediction.
//!
//! No FFI. Supports numeric trees only (`num_cat=0`, no `linear_tree`).

mod parse;
mod predict;

pub use parse::{Booster, ModelMeta, load_model_dir};
pub use predict::predict_raw;
