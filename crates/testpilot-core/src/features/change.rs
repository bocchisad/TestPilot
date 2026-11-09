//! Change-side helpers: srcf/tf classification and hunk → function mapping.

use std::collections::{HashMap, HashSet};

use crate::features::python_scan::{DefSpan, scan_python};
use crate::git::{DiffHunk, LineRange};

/// True if path looks like a test file (basename has `test` or path contains `/tests/`).
pub fn is_test_path(path: &str) -> bool {
    let p = path.replace('\\', "/");
    if p.contains("/tests/") || p.starts_with("tests/") {
        return true;
    }
    let base = p.rsplit('/').next().unwrap_or(&p).to_ascii_lowercase();
    base.contains("test")
}

/// Changed `.py` paths that are not tests.
pub fn src_files(hunks: &[DiffHunk]) -> Vec<String> {
    let mut out: Vec<String> = hunks
        .iter()
        .filter(|h| h.path.ends_with(".py") && !is_test_path(&h.path))
        .map(|h| h.path.clone())
        .collect();
    out.sort();
    out.dedup();
    out
}

pub fn line_set(ranges: &[LineRange]) -> HashSet<u32> {
    let mut s = HashSet::new();
    for r in ranges {
        if r.start <= r.end {
            for ln in r.start..=r.end {
                s.insert(ln);
            }
        }
    }
    s
}

/// Qualnames of defs whose span intersects old-side changed lines.
/// Format: `path::Class.method` or `path::<module>` for module-level changes.
pub fn changed_functions(
    path: &str,
    old_source: Option<&str>,
    old_ranges: &[LineRange],
) -> Vec<String> {
    let changed = line_set(old_ranges);
    if changed.is_empty() {
        return vec![];
    }
    let Some(src) = old_source else {
        return vec![format!("{path}::<module>")];
    };
    let scan = scan_python(src);
    let mut hit: Vec<String> = Vec::new();
    let mut covered_by_def = HashSet::new();
    for d in &scan.defs {
        if d.kind == "class" {
            continue;
        }
        let mut intersects = false;
        for ln in d.start_line as u32..d.end_line as u32 {
            if changed.contains(&ln) {
                intersects = true;
                covered_by_def.insert(ln);
            }
        }
        if intersects {
            hit.push(format!("{path}::{}", d.qualname));
        }
    }
    // Module-level (imports / class body / top-level) changed lines not in any def.
    let module_hit = changed.iter().any(|ln| !covered_by_def.contains(ln));
    if module_hit {
        hit.push(format!("{path}::<module>"));
    }
    hit.sort();
    hit.dedup();
    hit
}

/// Map line → innermost def span covering it (for coverage / pq).
pub fn line_to_def(defs: &[DefSpan]) -> HashMap<u32, String> {
    let mut map = HashMap::new();
    let mut ordered = defs.to_vec();
    // Prefer narrower (later nested) spans: sort by width ascending so they overwrite.
    ordered.sort_by_key(|d| d.end_line.saturating_sub(d.start_line));
    for d in ordered {
        if d.kind == "class" {
            continue;
        }
        for ln in d.start_line as u32..d.end_line as u32 {
            map.insert(ln, d.qualname.clone());
        }
    }
    map
}

pub fn change_size_stats(hunks: &[DiffHunk]) -> (i64, i64, i64, i64, i64) {
    let ch_nfiles = hunks.len() as i64;
    let srcf = src_files(hunks);
    let ch_nsrc = srcf.len() as i64;
    let mut ch_add = 0i64;
    let mut ch_rm = 0i64;
    for h in hunks {
        ch_add += line_set(&h.new_line_ranges).len() as i64;
        ch_rm += line_set(&h.old_line_ranges).len() as i64;
    }
    (ch_nfiles, ch_nsrc, ch_add, ch_rm, 0) // ch_nfunc filled by caller
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_path_heuristic() {
        assert!(is_test_path("tests/test_foo.py"));
        assert!(is_test_path("pkg/test_bar.py"));
        assert!(!is_test_path("pkg/util.py"));
    }
}
