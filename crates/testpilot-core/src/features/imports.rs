//! Import-graph distance features + python_blob_cache.

use std::collections::{HashMap, HashSet, VecDeque};
use std::path::Path;

use sha2::{Digest, Sha256};

use crate::error::Result;
use crate::features::python_scan::{ImportEdge, ScanResult, scan_python};
use crate::git::{GitRepo, Sha};
use crate::store::Store;

pub const MAX_IMPORT_DIST: i64 = 6;
pub const NO_PATH: i64 = 99;

#[derive(Debug, Default)]
pub struct ImportGraph {
    /// path → set of imported resolved paths
    edges: HashMap<String, HashSet<String>>,
}

impl ImportGraph {
    pub fn from_edges(edges: HashMap<String, HashSet<String>>) -> Self {
        Self { edges }
    }

    pub fn dist(&self, from: &str, targets: &[String]) -> i64 {
        if targets.is_empty() {
            return NO_PATH;
        }
        let target_set: HashSet<&str> = targets.iter().map(|s| s.as_str()).collect();
        if target_set.contains(from) {
            return 0;
        }
        let mut q = VecDeque::new();
        let mut seen = HashSet::new();
        q.push_back((from.to_string(), 0i64));
        seen.insert(from.to_string());
        while let Some((node, d)) = q.pop_front() {
            if d >= MAX_IMPORT_DIST {
                continue;
            }
            let Some(nbrs) = self.edges.get(&node) else {
                continue;
            };
            for n in nbrs {
                if seen.contains(n) {
                    continue;
                }
                let nd = d + 1;
                if target_set.contains(n.as_str()) {
                    return nd;
                }
                seen.insert(n.clone());
                q.push_back((n.clone(), nd));
            }
        }
        NO_PATH
    }
}

/// Build import graph by scanning every path in `paths` (full dump).
pub fn build_import_graph<R: GitRepo>(
    repo: &R,
    store: &Store,
    sha: &Sha,
    paths: &[String],
) -> Result<ImportGraph> {
    build_import_graph_seeded(repo, store, sha, paths, paths)
}

/// Scan only `seeds` (and BFS-discovered import targets up to [`MAX_IMPORT_DIST`]).
/// `index_paths` supplies the module index / resolution universe (usually all `.py` at rev).
pub fn build_import_graph_seeded<R: GitRepo>(
    repo: &R,
    store: &Store,
    sha: &Sha,
    seeds: &[String],
    index_paths: &[String],
) -> Result<ImportGraph> {
    let path_set: HashSet<String> = index_paths.iter().cloned().collect();
    let modules = index_modules(index_paths);
    let mut edges: HashMap<String, HashSet<String>> = HashMap::new();
    let mut queue: VecDeque<(String, i64)> = seeds
        .iter()
        .filter(|p| p.ends_with(".py"))
        .map(|p| (p.clone(), 0i64))
        .collect();
    let mut enqueued: HashSet<String> = seeds.iter().cloned().collect();

    while let Some((path, depth)) = queue.pop_front() {
        if edges.contains_key(&path) {
            continue;
        }
        let Some(bytes) = repo.file_at_rev(sha, &path)? else {
            edges.insert(path, HashSet::new());
            continue;
        };
        let blob_sha = hex::encode(Sha256::digest(&bytes));
        let scan = cached_scan(store, &blob_sha, &bytes)?;
        let mut nbrs = HashSet::new();
        for edge in &scan.imports {
            if let Some(resolved) = resolve_import(&path, edge, &modules, &path_set) {
                if depth < MAX_IMPORT_DIST && enqueued.insert(resolved.clone()) {
                    queue.push_back((resolved.clone(), depth + 1));
                }
                nbrs.insert(resolved);
            }
        }
        edges.insert(path, nbrs);
    }
    Ok(ImportGraph::from_edges(edges))
}

fn cached_scan(store: &Store, blob_sha: &str, bytes: &[u8]) -> Result<ScanResult> {
    if let Some((imports_json, defs_json)) = store.get_python_blob_cache(blob_sha)? {
        let imports = serde_json::from_str(&imports_json).unwrap_or_default();
        let defs = serde_json::from_str(&defs_json).unwrap_or_default();
        return Ok(ScanResult { imports, defs });
    }
    let src = String::from_utf8_lossy(bytes);
    let scan = scan_python(&src);
    let imports_json = serde_json::to_string(&scan.imports).unwrap_or_else(|_| "[]".into());
    let defs_json = serde_json::to_string(&scan.defs).unwrap_or_else(|_| "[]".into());
    let _ = store.put_python_blob_cache(blob_sha, &imports_json, &defs_json);
    Ok(scan)
}

/// Map dotted module candidates → repo-relative `.py` paths.
fn index_modules(paths: &[String]) -> HashMap<String, String> {
    let mut map = HashMap::new();
    for p in paths {
        if !p.ends_with(".py") {
            continue;
        }
        let no_ext = p.trim_end_matches(".py").replace('\\', "/");
        let mod_name = if no_ext.ends_with("/__init__") {
            no_ext.trim_end_matches("/__init__").replace('/', ".")
        } else {
            no_ext.replace('/', ".")
        };
        // Prefer stripping common roots
        for prefix in ["src.", "lib.", ""] {
            let key = if prefix.is_empty() {
                mod_name.clone()
            } else if let Some(rest) = mod_name.strip_prefix(prefix) {
                rest.to_string()
            } else {
                continue;
            };
            map.entry(key).or_insert_with(|| p.clone());
        }
        map.entry(mod_name).or_insert_with(|| p.clone());
    }
    map
}

fn resolve_import(
    from_path: &str,
    edge: &ImportEdge,
    modules: &HashMap<String, String>,
    path_set: &HashSet<String>,
) -> Option<String> {
    let candidate = if edge.relative {
        resolve_relative(from_path, edge.level, &edge.module)
    } else {
        edge.module.clone()
    };
    if candidate.is_empty() && edge.names.is_empty() {
        return None;
    }
    // `from pkg import mod` may resolve to pkg.mod
    let mut tries = vec![candidate.clone()];
    for name in &edge.names {
        if !candidate.is_empty() {
            tries.push(format!("{candidate}.{name}"));
        } else {
            tries.push(name.clone());
        }
    }
    for t in tries {
        if let Some(p) = modules.get(&t) {
            if path_set.contains(p) {
                return Some(p.clone());
            }
        }
        // Direct path guess
        let as_path = format!("{}.py", t.replace('.', "/"));
        if path_set.contains(&as_path) {
            return Some(as_path);
        }
        let as_init = format!("{}/__init__.py", t.replace('.', "/"));
        if path_set.contains(&as_init) {
            return Some(as_init);
        }
        for root in ["src/", ""] {
            let p1 = format!("{root}{as_path}");
            if path_set.contains(&p1) {
                return Some(p1);
            }
            let p2 = format!("{root}{as_init}");
            if path_set.contains(&p2) {
                return Some(p2);
            }
        }
    }
    None
}

fn resolve_relative(from_path: &str, level: u32, module: &str) -> String {
    let from = from_path.replace('\\', "/");
    let dir = Path::new(&from)
        .parent()
        .map(|p| p.to_string_lossy().to_string())
        .unwrap_or_default();
    let mut parts: Vec<&str> = if dir.is_empty() {
        vec![]
    } else {
        dir.split('/').collect()
    };
    // level=1 → current package (dir of file); level=2 → parent, etc.
    let up = level.saturating_sub(1) as usize;
    for _ in 0..up {
        parts.pop();
    }
    let mut dotted: Vec<String> = parts.into_iter().map(|s| s.to_string()).collect();
    if !module.is_empty() {
        for seg in module.split('.') {
            if !seg.is_empty() {
                dotted.push(seg.to_string());
            }
        }
    }
    dotted.join(".")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bfs_direct_edge() {
        let mut edges = HashMap::new();
        edges.insert("tests/t.py".into(), HashSet::from(["pkg/a.py".into()]));
        let g = ImportGraph::from_edges(edges);
        assert_eq!(g.dist("tests/t.py", &["pkg/a.py".into()]), 1);
        assert_eq!(g.dist("tests/t.py", &["pkg/b.py".into()]), NO_PATH);
    }
}
