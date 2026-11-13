//! Print JSON array of non-class def qualnames for a Python file (scan coverage checks).

use std::env;
use std::fs;
use std::process;

use testpilot_core::features::python_scan::scan_python;

fn main() {
    let path = env::args().nth(1).unwrap_or_else(|| {
        eprintln!("usage: scan_file <path.py>");
        process::exit(2);
    });
    let src = fs::read_to_string(&path).unwrap_or_else(|e| {
        eprintln!("read {path}: {e}");
        process::exit(1);
    });
    let scan = scan_python(&src);
    let names: Vec<&str> = scan
        .defs
        .iter()
        .filter(|d| d.kind != "class")
        .map(|d| d.qualname.as_str())
        .collect();
    println!("{}", serde_json::to_string(&names).expect("json"));
}
