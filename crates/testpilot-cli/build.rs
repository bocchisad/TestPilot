#[path = "build_git_sha.rs"]
mod build_git_sha;

use std::env;
use std::path::Path;

fn main() {
    let manifest = env::var("CARGO_MANIFEST_DIR").unwrap_or_else(|_| ".".to_string());
    build_git_sha::emit_cargo_git_sha(Path::new(&manifest));
}
