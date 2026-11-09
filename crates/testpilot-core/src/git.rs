//! Git history collection via gix. Public API uses [`Sha`], never `gix::ObjectId`.
//!
//! Walk is **reachable from HEAD only** (not `--all`). Ordering is a Kahn topological
//! sort (parents before children; author time as tie-break).

use std::collections::{HashMap, HashSet, VecDeque};
use std::ops::Range;
use std::path::{Path, PathBuf};

use bytes::Bytes;
use gix::diff::blob::Algorithm;
use gix::diff::blob::intern::InternedInput;
use gix::diff::blob::sink::{Counter, Sink};
use gix::object::tree::diff::{Action, Change};

use crate::error::{Error, Result};
use crate::store::{CommitRow, FileChangeRow, Store};

/// Opaque hex commit id — gix types stay inside [`GixRepo`].
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct Sha(String);

impl Sha {
    pub fn as_str(&self) -> &str {
        &self.0
    }

    pub fn from_hex(s: impl Into<String>) -> Self {
        Self(s.into())
    }
}

impl std::fmt::Display for Sha {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        self.0.fmt(f)
    }
}

#[derive(Debug, Clone)]
pub struct CommitMeta {
    pub sha: Sha,
    pub author_ts: i64,
    pub message: String,
    pub parents: Vec<Sha>,
}

#[derive(Debug, Clone)]
pub struct FileChange {
    pub path: String,
    pub change_type: char, // A/M/D/R
    pub old_path: Option<String>,
    pub lines_added: i64,
    pub lines_deleted: i64,
    pub is_binary: bool,
}

/// Inclusive 1-based line range `[start, end]` (both ends inclusive). Empty if start > end.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LineRange {
    pub start: u32,
    pub end: u32,
}

#[derive(Debug, Clone)]
pub struct DiffHunk {
    pub path: String,
    pub old_path: Option<String>,
    pub change_type: char,
    /// Changed lines on the old side (1-based inclusive). Empty for pure additions.
    pub old_line_ranges: Vec<LineRange>,
    /// Changed lines on the new side (1-based inclusive). Empty for pure deletions.
    pub new_line_ranges: Vec<LineRange>,
    pub is_binary: bool,
}

pub trait GitRepo {
    fn rev_parse(&self, rev: &str) -> Result<Sha>;
    /// All commits reachable from HEAD (optionally excluding `since_sha` and its ancestors),
    /// in Kahn topo order (parents before children). Does **not** apply `--max`.
    fn walk_topo_old_to_new(
        &self,
        since_sha: Option<&str>,
        since_date: Option<i64>,
    ) -> Result<Vec<CommitMeta>>;
    fn diff_against_first_parent(&self, sha: &Sha) -> Result<Vec<FileChange>>;
    /// Blob bytes at `path` in tree of `sha`, or `None` if missing / not a blob.
    fn file_at_rev(&self, sha: &Sha, path: &str) -> Result<Option<Bytes>>;
    /// Structured hunks for `old_sha..new_sha` (trees). Oversized/binary → empty ranges + `is_binary`.
    fn diff_hunks(&self, old_sha: &Sha, new_sha: &Sha) -> Result<Vec<DiffHunk>>;
    /// All blob paths under the commit tree (files only).
    fn list_paths_at_rev(&self, sha: &Sha) -> Result<Vec<String>>;
}

pub struct GixRepo {
    repo: gix::Repository,
    #[allow(dead_code)]
    path: PathBuf,
}

impl GixRepo {
    pub fn open(path: &Path) -> Result<Self> {
        let repo = gix::discover(path).map_err(|e| Error::Git(e.to_string()))?;
        Ok(Self {
            path: path.to_path_buf(),
            repo,
        })
    }

    /// Diff `base` commit tree against the current worktree (dirty files).
    pub fn diff_worktree(&self, base: &Sha) -> Result<Vec<DiffHunk>> {
        let workdir = self
            .repo
            .work_dir()
            .ok_or_else(|| Error::Git("repository has no worktree".into()))?;
        // Prefer `git diff/ls-files` for the candidate set (avoids walking huge trees).
        let mut candidates: HashSet<String> = HashSet::new();
        let base_hex = base.as_str();
        if let Ok(out) = std::process::Command::new("git")
            .args(["diff", "--name-only", base_hex])
            .current_dir(workdir)
            .output()
        {
            for line in String::from_utf8_lossy(&out.stdout).lines() {
                if !line.is_empty() {
                    candidates.insert(line.to_string());
                }
            }
        }
        if let Ok(out) = std::process::Command::new("git")
            .args(["ls-files", "--others", "--exclude-standard"])
            .current_dir(workdir)
            .output()
        {
            for line in String::from_utf8_lossy(&out.stdout).lines() {
                if !line.is_empty() {
                    candidates.insert(line.to_string());
                }
            }
        }
        // Also catch deletions of tracked base files removed from disk but missed by diff.
        for path in self.list_paths_at_rev(base)? {
            let wt = workdir.join(&path);
            if !wt.exists() {
                candidates.insert(path);
            }
        }

        let mut hunks = Vec::new();
        for path in candidates {
            let old: Option<Vec<u8>> = self.file_at_rev(base, &path)?.map(|b| b.to_vec());
            let wt_path = workdir.join(&path);
            let new: Option<Vec<u8>> = if wt_path.is_file() {
                Some(std::fs::read(&wt_path).map_err(|e| Error::Other(e.to_string()))?)
            } else {
                None
            };
            match (old, new) {
                (None, None) => {}
                (None, Some(n)) => {
                    let (ranges, binary) = ranges_from_bytes(&n);
                    hunks.push(DiffHunk {
                        path,
                        old_path: None,
                        change_type: 'A',
                        old_line_ranges: vec![],
                        new_line_ranges: ranges,
                        is_binary: binary,
                    });
                }
                (Some(o), None) => {
                    let (ranges, binary) = ranges_from_bytes(&o);
                    hunks.push(DiffHunk {
                        path,
                        old_path: None,
                        change_type: 'D',
                        old_line_ranges: ranges,
                        new_line_ranges: vec![],
                        is_binary: binary,
                    });
                }
                (Some(o), Some(n)) => {
                    if o == n {
                        continue;
                    }
                    if o.contains(&0) || n.contains(&0) {
                        hunks.push(DiffHunk {
                            path,
                            old_path: None,
                            change_type: 'M',
                            old_line_ranges: vec![],
                            new_line_ranges: vec![],
                            is_binary: true,
                        });
                        continue;
                    }
                    let input = InternedInput::new(o.as_slice(), n.as_slice());
                    let (old_r, new_r) = gix::diff::blob::diff(
                        Algorithm::Histogram,
                        &input,
                        RangeCollector::default(),
                    );
                    hunks.push(DiffHunk {
                        path,
                        old_path: None,
                        change_type: 'M',
                        old_line_ranges: old_r,
                        new_line_ranges: new_r,
                        is_binary: false,
                    });
                }
            }
        }
        hunks.sort_by(|a, b| a.path.cmp(&b.path));
        Ok(hunks)
    }

    fn oid_to_sha(id: gix::ObjectId) -> Sha {
        Sha::from_hex(id.to_hex().to_string())
    }

    fn parse_oid(&self, s: &str) -> Result<gix::ObjectId> {
        self.repo
            .rev_parse_single(s)
            .map(|r| r.detach())
            .map_err(|e| Error::Git(e.to_string()))
    }

    fn commit_meta(&self, id: gix::ObjectId) -> Result<CommitMeta> {
        let commit = self
            .repo
            .find_commit(id)
            .map_err(|e| Error::Git(e.to_string()))?;
        // B11: author time, not committer
        let author_ts = commit.author().map(|a| a.time.seconds).unwrap_or(0);
        let message = commit
            .message_raw()
            .map(|m| String::from_utf8_lossy(m).trim().to_string())
            .unwrap_or_default();
        let parents: Vec<Sha> = commit
            .parent_ids()
            .map(|id| Self::oid_to_sha(id.detach()))
            .collect();
        Ok(CommitMeta {
            sha: Self::oid_to_sha(id),
            author_ts,
            message,
            parents,
        })
    }

    fn collect_reachable(&self, tip: gix::ObjectId) -> Result<HashMap<String, CommitMeta>> {
        let walk = self
            .repo
            .rev_walk([tip])
            .all()
            .map_err(|e| Error::Git(e.to_string()))?;
        let mut map = HashMap::new();
        for info in walk {
            let info = info.map_err(|e| Error::Git(e.to_string()))?;
            let meta = self.commit_meta(info.id)?;
            map.insert(meta.sha.as_str().to_string(), meta);
        }
        Ok(map)
    }
}

impl GitRepo for GixRepo {
    fn rev_parse(&self, rev: &str) -> Result<Sha> {
        Ok(Self::oid_to_sha(self.parse_oid(rev)?))
    }

    fn walk_topo_old_to_new(
        &self,
        since_sha: Option<&str>,
        since_date: Option<i64>,
    ) -> Result<Vec<CommitMeta>> {
        let head = self
            .repo
            .head_id()
            .map_err(|e| Error::Git(e.to_string()))?
            .detach();

        let mut reachable = self.collect_reachable(head)?;

        // B3: since exclusive — drop since and everything reachable from it.
        if let Some(since) = since_sha {
            let since_oid = self.parse_oid(since)?;
            let hidden = self.collect_reachable(since_oid)?;
            for sha in hidden.keys() {
                reachable.remove(sha);
            }
        }

        if let Some(min_ts) = since_date {
            reachable.retain(|_, m| m.author_ts >= min_ts);
        }

        Ok(kahn_topo_sort(reachable))
    }

    fn diff_against_first_parent(&self, sha: &Sha) -> Result<Vec<FileChange>> {
        let oid = self.parse_oid(sha.as_str())?;
        let commit = self
            .repo
            .find_commit(oid)
            .map_err(|e| Error::Git(e.to_string()))?;
        let tree_id = commit.tree_id().map_err(|e| Error::Git(e.to_string()))?;

        let parent_tree_id = match commit.parent_ids().next() {
            Some(pid) => {
                let p = self
                    .repo
                    .find_commit(pid)
                    .map_err(|e| Error::Git(e.to_string()))?;
                Some(p.tree_id().map_err(|e| Error::Git(e.to_string()))?)
            }
            None => None,
        };

        let mut changes = Vec::new();

        if let Some(ptid) = parent_tree_id {
            let parent = self
                .repo
                .find_tree(ptid)
                .map_err(|e| Error::Git(e.to_string()))?;
            let ours = self
                .repo
                .find_tree(tree_id)
                .map_err(|e| Error::Git(e.to_string()))?;

            parent
                .changes()
                .map_err(|e| Error::Git(e.to_string()))?
                .options(|opts| {
                    opts.track_path().track_rewrites(Some(Default::default()));
                })
                .for_each_to_obtain_tree(&ours, |change| {
                    match change {
                        Change::Addition {
                            location,
                            entry_mode,
                            id,
                            ..
                        } => {
                            if !entry_mode.is_tree() {
                                let (added, deleted, binary) = line_stats_new(&id);
                                changes.push(FileChange {
                                    path: location.to_string(),
                                    change_type: 'A',
                                    old_path: None,
                                    lines_added: added,
                                    lines_deleted: deleted,
                                    is_binary: binary,
                                });
                            }
                        }
                        Change::Deletion {
                            location,
                            entry_mode,
                            id,
                            ..
                        } => {
                            if !entry_mode.is_tree() {
                                let (added, deleted, binary) = line_stats_old(&id);
                                changes.push(FileChange {
                                    path: location.to_string(),
                                    change_type: 'D',
                                    old_path: None,
                                    lines_added: added,
                                    lines_deleted: deleted,
                                    is_binary: binary,
                                });
                            }
                        }
                        Change::Modification {
                            location,
                            previous_id,
                            id,
                            entry_mode,
                            previous_entry_mode,
                            ..
                        } => {
                            if !entry_mode.is_tree() && !previous_entry_mode.is_tree() {
                                let (added, deleted, binary) = line_stats_mod(&previous_id, &id);
                                changes.push(FileChange {
                                    path: location.to_string(),
                                    change_type: 'M',
                                    old_path: None,
                                    lines_added: added,
                                    lines_deleted: deleted,
                                    is_binary: binary,
                                });
                            }
                        }
                        Change::Rewrite {
                            source_location,
                            location,
                            source_id,
                            id,
                            entry_mode,
                            source_entry_mode,
                            ..
                        } => {
                            if !entry_mode.is_tree() && !source_entry_mode.is_tree() {
                                let (added, deleted, binary) = line_stats_mod(&source_id, &id);
                                changes.push(FileChange {
                                    path: location.to_string(),
                                    change_type: 'R',
                                    old_path: Some(source_location.to_string()),
                                    lines_added: added,
                                    lines_deleted: deleted,
                                    is_binary: binary,
                                });
                            }
                        }
                    }
                    Ok::<Action, std::io::Error>(Action::Continue)
                })
                .map_err(|e| Error::Git(format!("{e:?}")))?;
        } else {
            let ours = self
                .repo
                .find_tree(tree_id)
                .map_err(|e| Error::Git(e.to_string()))?;
            let mut recorder = gix::traverse::tree::Recorder::default();
            ours.traverse()
                .breadthfirst(&mut recorder)
                .map_err(|e| Error::Git(e.to_string()))?;
            for entry in recorder.records {
                if !(entry.mode.is_blob() || entry.mode.is_executable() || entry.mode.is_link()) {
                    continue;
                }
                let path = String::from_utf8_lossy(&entry.filepath).to_string();
                let (added, _d, binary) = match self.repo.find_blob(entry.oid) {
                    Ok(blob) => count_lines_as_addition(blob.data.as_slice()),
                    Err(_) => (0, 0, false),
                };
                changes.push(FileChange {
                    path,
                    change_type: 'A',
                    old_path: None,
                    lines_added: added,
                    lines_deleted: 0,
                    is_binary: binary,
                });
            }
        }

        Ok(changes)
    }

    fn file_at_rev(&self, sha: &Sha, path: &str) -> Result<Option<Bytes>> {
        let oid = self.parse_oid(sha.as_str())?;
        let commit = self
            .repo
            .find_commit(oid)
            .map_err(|e| Error::Git(e.to_string()))?;
        let tree = commit.tree().map_err(|e| Error::Git(e.to_string()))?;
        let entry = tree
            .lookup_entry_by_path(path)
            .map_err(|e| Error::Git(e.to_string()))?;
        let Some(entry) = entry else {
            return Ok(None);
        };
        if !entry.mode().is_blob() && !entry.mode().is_executable() && !entry.mode().is_link() {
            return Ok(None);
        }
        let blob = self
            .repo
            .find_blob(entry.id())
            .map_err(|e| Error::Git(e.to_string()))?;
        Ok(Some(Bytes::copy_from_slice(blob.data.as_slice())))
    }

    fn diff_hunks(&self, old_sha: &Sha, new_sha: &Sha) -> Result<Vec<DiffHunk>> {
        let old_oid = self.parse_oid(old_sha.as_str())?;
        let new_oid = self.parse_oid(new_sha.as_str())?;
        let old_commit = self
            .repo
            .find_commit(old_oid)
            .map_err(|e| Error::Git(e.to_string()))?;
        let new_commit = self
            .repo
            .find_commit(new_oid)
            .map_err(|e| Error::Git(e.to_string()))?;
        let old_tree = self
            .repo
            .find_tree(
                old_commit
                    .tree_id()
                    .map_err(|e| Error::Git(e.to_string()))?,
            )
            .map_err(|e| Error::Git(e.to_string()))?;
        let new_tree = self
            .repo
            .find_tree(
                new_commit
                    .tree_id()
                    .map_err(|e| Error::Git(e.to_string()))?,
            )
            .map_err(|e| Error::Git(e.to_string()))?;

        let mut hunks = Vec::new();
        old_tree
            .changes()
            .map_err(|e| Error::Git(e.to_string()))?
            .options(|opts| {
                opts.track_path().track_rewrites(Some(Default::default()));
            })
            .for_each_to_obtain_tree(&new_tree, |change| {
                match change {
                    Change::Addition {
                        location,
                        entry_mode,
                        id,
                        ..
                    } => {
                        if !entry_mode.is_tree() {
                            let (ranges, binary) = hunk_ranges_new(&id);
                            hunks.push(DiffHunk {
                                path: location.to_string(),
                                old_path: None,
                                change_type: 'A',
                                old_line_ranges: vec![],
                                new_line_ranges: ranges,
                                is_binary: binary,
                            });
                        }
                    }
                    Change::Deletion {
                        location,
                        entry_mode,
                        id,
                        ..
                    } => {
                        if !entry_mode.is_tree() {
                            let (ranges, binary) = hunk_ranges_old(&id);
                            hunks.push(DiffHunk {
                                path: location.to_string(),
                                old_path: None,
                                change_type: 'D',
                                old_line_ranges: ranges,
                                new_line_ranges: vec![],
                                is_binary: binary,
                            });
                        }
                    }
                    Change::Modification {
                        location,
                        previous_id,
                        id,
                        entry_mode,
                        previous_entry_mode,
                        ..
                    } => {
                        if !entry_mode.is_tree() && !previous_entry_mode.is_tree() {
                            let (old_r, new_r, binary) = hunk_ranges_mod(&previous_id, &id);
                            hunks.push(DiffHunk {
                                path: location.to_string(),
                                old_path: None,
                                change_type: 'M',
                                old_line_ranges: old_r,
                                new_line_ranges: new_r,
                                is_binary: binary,
                            });
                        }
                    }
                    Change::Rewrite {
                        source_location,
                        location,
                        source_id,
                        id,
                        entry_mode,
                        source_entry_mode,
                        ..
                    } => {
                        if !entry_mode.is_tree() && !source_entry_mode.is_tree() {
                            let (old_r, new_r, binary) = hunk_ranges_mod(&source_id, &id);
                            hunks.push(DiffHunk {
                                path: location.to_string(),
                                old_path: Some(source_location.to_string()),
                                change_type: 'R',
                                old_line_ranges: old_r,
                                new_line_ranges: new_r,
                                is_binary: binary,
                            });
                        }
                    }
                }
                Ok::<Action, std::io::Error>(Action::Continue)
            })
            .map_err(|e| Error::Git(format!("{e:?}")))?;
        Ok(hunks)
    }

    fn list_paths_at_rev(&self, sha: &Sha) -> Result<Vec<String>> {
        let oid = self.parse_oid(sha.as_str())?;
        let commit = self
            .repo
            .find_commit(oid)
            .map_err(|e| Error::Git(e.to_string()))?;
        let tree = commit.tree().map_err(|e| Error::Git(e.to_string()))?;
        let mut recorder = gix::traverse::tree::Recorder::default();
        tree.traverse()
            .breadthfirst(&mut recorder)
            .map_err(|e| Error::Git(e.to_string()))?;
        let mut paths = Vec::new();
        for entry in recorder.records {
            if !(entry.mode.is_blob() || entry.mode.is_executable() || entry.mode.is_link()) {
                continue;
            }
            paths.push(String::from_utf8_lossy(&entry.filepath).to_string());
        }
        paths.sort();
        Ok(paths)
    }
}

#[derive(Default)]
struct RangeCollector {
    old: Vec<LineRange>,
    new: Vec<LineRange>,
}

impl Sink for RangeCollector {
    type Out = (Vec<LineRange>, Vec<LineRange>);

    fn process_change(&mut self, before: Range<u32>, after: Range<u32>) {
        if before.start < before.end {
            self.old.push(LineRange {
                start: before.start + 1,
                end: before.end,
            });
        }
        if after.start < after.end {
            self.new.push(LineRange {
                start: after.start + 1,
                end: after.end,
            });
        }
    }

    fn finish(self) -> Self::Out {
        (self.old, self.new)
    }
}

fn hunk_ranges_new(id: &gix::Id<'_>) -> (Vec<LineRange>, bool) {
    match id.object().ok().and_then(|o| o.try_into_blob().ok()) {
        Some(blob) => {
            if blob.data.contains(&0) {
                return (vec![], true);
            }
            let n = String::from_utf8_lossy(blob.data.as_slice())
                .lines()
                .count() as u32;
            if n == 0 {
                (vec![], false)
            } else {
                (vec![LineRange { start: 1, end: n }], false)
            }
        }
        None => (vec![], false),
    }
}

fn hunk_ranges_old(id: &gix::Id<'_>) -> (Vec<LineRange>, bool) {
    hunk_ranges_new(id)
}

fn hunk_ranges_mod(old: &gix::Id<'_>, new: &gix::Id<'_>) -> (Vec<LineRange>, Vec<LineRange>, bool) {
    let old_blob = old.object().ok().and_then(|o| o.try_into_blob().ok());
    let new_blob = new.object().ok().and_then(|o| o.try_into_blob().ok());
    match (old_blob, new_blob) {
        (Some(o), Some(n)) => {
            if o.data.contains(&0) || n.data.contains(&0) {
                return (vec![], vec![], true);
            }
            let input = InternedInput::new(o.data.as_slice(), n.data.as_slice());
            let (old_r, new_r) =
                gix::diff::blob::diff(Algorithm::Histogram, &input, RangeCollector::default());
            (old_r, new_r, false)
        }
        (None, Some(n)) => {
            if n.data.contains(&0) {
                return (vec![], vec![], true);
            }
            let nlines = String::from_utf8_lossy(n.data.as_slice()).lines().count() as u32;
            let r = if nlines == 0 {
                vec![]
            } else {
                vec![LineRange {
                    start: 1,
                    end: nlines,
                }]
            };
            (vec![], r, false)
        }
        (Some(o), None) => {
            if o.data.contains(&0) {
                return (vec![], vec![], true);
            }
            let nlines = String::from_utf8_lossy(o.data.as_slice()).lines().count() as u32;
            let r = if nlines == 0 {
                vec![]
            } else {
                vec![LineRange {
                    start: 1,
                    end: nlines,
                }]
            };
            (r, vec![], false)
        }
        _ => (vec![], vec![], false),
    }
}

/// Kahn topological sort: parents before children; tie-break by author_ts then sha.
fn kahn_topo_sort(commits: HashMap<String, CommitMeta>) -> Vec<CommitMeta> {
    if commits.is_empty() {
        return Vec::new();
    }
    let in_set: HashSet<String> = commits.keys().cloned().collect();
    let mut indegree: HashMap<String, usize> = HashMap::new();
    let mut children: HashMap<String, Vec<String>> = HashMap::new();

    for (sha, meta) in &commits {
        indegree.entry(sha.clone()).or_insert(0);
        for p in &meta.parents {
            let ps = p.as_str().to_string();
            if in_set.contains(&ps) {
                *indegree.entry(sha.clone()).or_insert(0) += 1;
                children.entry(ps).or_default().push(sha.clone());
            }
        }
    }

    let mut ready: Vec<String> = indegree
        .iter()
        .filter(|(_, d)| **d == 0)
        .map(|(s, _)| s.clone())
        .collect();
    ready.sort_by(|a, b| {
        let ta = commits[a].author_ts;
        let tb = commits[b].author_ts;
        ta.cmp(&tb).then_with(|| a.cmp(b))
    });
    let mut queue: VecDeque<String> = ready.into();

    let mut ordered = Vec::with_capacity(commits.len());
    while let Some(sha) = queue.pop_front() {
        // Keep queue sorted by inserting children in sorted position — simpler: collect then sort batch.
        ordered.push(sha.clone());
        if let Some(kids) = children.get(&sha) {
            let mut newly = Vec::new();
            for kid in kids {
                if let Some(d) = indegree.get_mut(kid) {
                    *d = d.saturating_sub(1);
                    if *d == 0 {
                        newly.push(kid.clone());
                    }
                }
            }
            newly.sort_by(|a, b| {
                let ta = commits[a].author_ts;
                let tb = commits[b].author_ts;
                ta.cmp(&tb).then_with(|| a.cmp(b))
            });
            // Merge newly into queue keeping order (queue may already have items).
            let mut merged: Vec<String> = queue.drain(..).collect();
            merged.extend(newly);
            merged.sort_by(|a, b| {
                let ta = commits[a].author_ts;
                let tb = commits[b].author_ts;
                ta.cmp(&tb).then_with(|| a.cmp(b))
            });
            queue.extend(merged);
        }
    }

    // Cycles / missing edges: append leftovers sorted by time
    if ordered.len() < commits.len() {
        let mut rest: Vec<_> = commits
            .keys()
            .filter(|s| !ordered.iter().any(|o| o == *s))
            .cloned()
            .collect();
        rest.sort_by(|a, b| {
            commits[a]
                .author_ts
                .cmp(&commits[b].author_ts)
                .then_with(|| a.cmp(b))
        });
        ordered.extend(rest);
    }

    ordered
        .into_iter()
        .filter_map(|s| commits.get(&s).cloned())
        .collect()
}

fn ranges_from_bytes(data: &[u8]) -> (Vec<LineRange>, bool) {
    if data.contains(&0) {
        return (vec![], true);
    }
    let nlines = String::from_utf8_lossy(data).lines().count() as u32;
    if nlines == 0 {
        (vec![], false)
    } else {
        (
            vec![LineRange {
                start: 1,
                end: nlines,
            }],
            false,
        )
    }
}

fn count_lines_as_addition(data: &[u8]) -> (i64, i64, bool) {
    if data.contains(&0) {
        return (0, 0, true);
    }
    let n = String::from_utf8_lossy(data).lines().count() as i64;
    (n, 0, false)
}

fn line_stats_new(id: &gix::Id<'_>) -> (i64, i64, bool) {
    match id.object().ok().and_then(|o| o.try_into_blob().ok()) {
        Some(blob) => {
            let (a, _, b) = count_lines_as_addition(blob.data.as_slice());
            (a, 0, b)
        }
        None => (0, 0, false),
    }
}

fn line_stats_old(id: &gix::Id<'_>) -> (i64, i64, bool) {
    match id.object().ok().and_then(|o| o.try_into_blob().ok()) {
        Some(blob) => {
            let (d, _, b) = count_lines_as_addition(blob.data.as_slice());
            (0, d, b)
        }
        None => (0, 0, false),
    }
}

/// B12: hunk-based counts via Histogram + Counter (matches git --numstat closely).
fn line_stats_mod(old: &gix::Id<'_>, new: &gix::Id<'_>) -> (i64, i64, bool) {
    let old_blob = old.object().ok().and_then(|o| o.try_into_blob().ok());
    let new_blob = new.object().ok().and_then(|o| o.try_into_blob().ok());
    match (old_blob, new_blob) {
        (Some(o), Some(n)) => {
            if o.data.contains(&0) || n.data.contains(&0) {
                return (0, 0, true);
            }
            let input = InternedInput::new(o.data.as_slice(), n.data.as_slice());
            let counts = gix::diff::blob::diff(Algorithm::Histogram, &input, Counter::default());
            (counts.insertions as i64, counts.removals as i64, false)
        }
        (None, Some(n)) => {
            let (a, _, b) = count_lines_as_addition(n.data.as_slice());
            (a, 0, b)
        }
        (Some(o), None) => {
            let (d, _, b) = count_lines_as_addition(o.data.as_slice());
            (0, d, b)
        }
        _ => (0, 0, false),
    }
}

/// Collect commits into the store. `--max` limits newly inserted commits in topo order.
pub fn collect_git<R: GitRepo>(
    repo: &R,
    store: &mut Store,
    since_sha: Option<&str>,
    since_date: Option<i64>,
    max: Option<usize>,
    max_files_per_commit: usize,
) -> Result<CollectGitStats> {
    collect_git_ex(
        repo,
        store,
        since_sha,
        since_date,
        max,
        max_files_per_commit,
        200,
        None,
    )
}

/// Like [`collect_git`], with injectable batch size and mid-batch failure (B4 tests).
#[allow(clippy::too_many_arguments)]
pub fn collect_git_ex<R: GitRepo>(
    repo: &R,
    store: &mut Store,
    since_sha: Option<&str>,
    since_date: Option<i64>,
    max: Option<usize>,
    max_files_per_commit: usize,
    batch_size: usize,
    fail_after_commit_writes: Option<usize>,
) -> Result<CollectGitStats> {
    let commits = repo.walk_topo_old_to_new(since_sha, since_date)?;
    let mut inserted = 0usize;
    let mut skipped = 0usize;
    let mut oversized = 0usize;
    let mut writes_done = 0usize;

    // Process in batches; each batch is one transaction (B4).
    let mut pending: Vec<(CommitMeta, Vec<FileChange>, bool)> = Vec::new();
    let batch_size = batch_size.max(1);

    for meta in commits {
        if store.commit_exists(meta.sha.as_str())? {
            skipped += 1;
            continue;
        }
        if let Some(limit) = max {
            if inserted + pending.len() >= limit {
                break;
            }
        }
        let mut changes = repo.diff_against_first_parent(&meta.sha)?;
        let is_oversized = changes.len() > max_files_per_commit;
        if is_oversized {
            tracing::warn!(
                sha = %meta.sha,
                files = changes.len(),
                limit = max_files_per_commit,
                "commit exceeds max-files-per-commit; storing without file_changes"
            );
            changes.clear();
            oversized += 1;
        }
        pending.push((meta, changes, is_oversized));

        if pending.len() >= batch_size {
            let n = flush_batch(
                store,
                &mut pending,
                &mut writes_done,
                fail_after_commit_writes,
            )?;
            inserted += n;
        }
    }
    inserted += flush_batch(
        store,
        &mut pending,
        &mut writes_done,
        fail_after_commit_writes,
    )?;

    Ok(CollectGitStats {
        inserted,
        skipped,
        oversized,
    })
}

fn flush_batch(
    store: &mut Store,
    pending: &mut Vec<(CommitMeta, Vec<FileChange>, bool)>,
    writes_done: &mut usize,
    fail_after_commit_writes: Option<usize>,
) -> Result<usize> {
    if pending.is_empty() {
        return Ok(0);
    }
    let batch = std::mem::take(pending);
    store.with_transaction(|tx| {
        let mut n = 0usize;
        for (meta, changes, is_oversized) in &batch {
            if Store::commit_exists_on(tx, meta.sha.as_str())? {
                continue;
            }
            let row = CommitRow {
                sha: meta.sha.as_str().to_string(),
                author_ts: meta.author_ts,
                message: meta.message.clone(),
                oversized: *is_oversized,
                parents: meta
                    .parents
                    .iter()
                    .map(|p| p.as_str().to_string())
                    .collect(),
            };
            // Insert commit+parents first; file_changes follow. A mid-batch failure
            // must roll back the whole transaction (B4).
            if !Store::insert_commit_on(tx, &row)? {
                continue;
            }
            *writes_done += 1;
            if fail_after_commit_writes == Some(*writes_done) {
                return Err(Error::Other("injected mid-batch failure".into()));
            }
            if !is_oversized {
                let rows: Vec<FileChangeRow> = changes
                    .iter()
                    .map(|c| FileChangeRow {
                        sha: meta.sha.as_str().to_string(),
                        path: c.path.clone(),
                        change_type: c.change_type.to_string(),
                        old_path: c.old_path.clone(),
                        lines_added: c.lines_added,
                        lines_deleted: c.lines_deleted,
                        is_binary: c.is_binary,
                    })
                    .collect();
                Store::insert_file_changes_on(tx, &rows)?;
            }
            n += 1;
        }
        Ok(n)
    })
}

#[derive(Debug, Default)]
pub struct CollectGitStats {
    pub inserted: usize,
    pub skipped: usize,
    pub oversized: usize,
}

#[cfg(test)]
mod kahn_tests {
    use super::*;

    #[test]
    fn kahn_parents_before_children() {
        let mut map = HashMap::new();
        map.insert(
            "c".into(),
            CommitMeta {
                sha: Sha::from_hex("c"),
                author_ts: 3,
                message: "c".into(),
                parents: vec![Sha::from_hex("b")],
            },
        );
        map.insert(
            "b".into(),
            CommitMeta {
                sha: Sha::from_hex("b"),
                author_ts: 2,
                message: "b".into(),
                parents: vec![Sha::from_hex("a")],
            },
        );
        map.insert(
            "a".into(),
            CommitMeta {
                sha: Sha::from_hex("a"),
                author_ts: 1,
                message: "a".into(),
                parents: vec![],
            },
        );
        let ordered = kahn_topo_sort(map);
        let pos: HashMap<_, _> = ordered
            .iter()
            .enumerate()
            .map(|(i, m)| (m.sha.as_str().to_string(), i))
            .collect();
        assert!(pos["a"] < pos["b"]);
        assert!(pos["b"] < pos["c"]);
    }
}
