use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::store::{SCHEMA_SQL, SCHEMA_VERSION};

pub const B_POLICY_DEFAULT: &str = "sampled_C_union_last_20_fp";

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Fingerprint {
    pub head_sha: String,
    pub n: u32,
    pub k: u32,
    pub seed: u64,
    pub b_policy: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub b_override: Option<u32>,
    pub bin_version: String,
    pub schema_version: i64,
    pub schema_sql_sha256: String,
}

#[derive(Debug, Clone)]
pub struct BootstrapInputs {
    pub head_sha: String,
    pub n: u32,
    pub k: u32,
    pub seed: u64,
    pub b_override: Option<u32>,
    pub bin_version: String,
    pub schema_version: i64,
}

pub fn schema_sql_sha256() -> String {
    let dig = Sha256::digest(SCHEMA_SQL.as_bytes());
    hex::encode(dig)
}

pub fn resolve_fingerprint(inputs: &BootstrapInputs) -> Fingerprint {
    let (b_policy, b_override) = match inputs.b_override {
        Some(b) => (format!("sampled_C_union_last_{b}_fp"), Some(b)),
        None => (B_POLICY_DEFAULT.to_string(), None),
    };
    Fingerprint {
        head_sha: inputs.head_sha.clone(),
        n: inputs.n,
        k: inputs.k,
        seed: inputs.seed,
        b_policy,
        b_override,
        bin_version: inputs.bin_version.clone(),
        schema_version: inputs.schema_version,
        schema_sql_sha256: schema_sql_sha256(),
    }
}

impl Default for BootstrapInputs {
    fn default() -> Self {
        Self {
            head_sha: String::new(),
            n: 200,
            k: 4,
            seed: 42,
            b_override: None,
            bin_version: String::new(),
            schema_version: SCHEMA_VERSION,
        }
    }
}
