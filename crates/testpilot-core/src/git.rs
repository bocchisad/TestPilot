//! Git history collection via gix. Public API uses [`Sha`], never `gix::ObjectId`.

use std::path::{Path, PathBuf};

use gix::object::tree::diff::{Action, Change};

use crate::error::{Error, Result};
use crate::store::{CommitRow, FileChangeRow};

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

pub trait GitRepo {
    fn rev_parse(&self, rev: &str) -> Result<Sha>;
    fn walk_topo_old_to_new(
        &self,
        since_sha: Option<&str>,
        since_date: Option<i64>,
        max: Option<usize>,
    ) -> Result<Vec<CommitMeta>>;
    fn diff_against_first_parent(&self, sha: &Sha) -> Result<Vec<FileChange>>;
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
        let author_ts = commit.time().map(|t| t.seconds).unwrap_or(0);
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
}

impl GitRepo for GixRepo {
    fn rev_parse(&self, rev: &str) -> Result<Sha> {
        Ok(Self::oid_to_sha(self.parse_oid(rev)?))
    }

    fn walk_topo_old_to_new(
        &self,
        since_sha: Option<&str>,
        since_date: Option<i64>,
        max: Option<usize>,
    ) -> Result<Vec<CommitMeta>> {
        let head = self
            .repo
            .head_id()
            .map_err(|e| Error::Git(e.to_string()))?
            .detach();

        let since_oid = since_sha.map(|s| self.parse_oid(s)).transpose()?;

        // Collect ancestors, then order oldest→newest by commit time with parent-before-child constraint.
        let walk = self
            .repo
            .rev_walk([head])
            .sorting(gix::revision::walk::Sorting::ByCommitTime(
                gix::traverse::commit::simple::CommitTimeOrder::OldestFirst,
            ))
            .all()
            .map_err(|e| Error::Git(e.to_string()))?;

        let mut commits = Vec::new();
        let mut seen_since = since_oid.is_none();
        for info in walk {
            let info = info.map_err(|e| Error::Git(e.to_string()))?;
            let id = info.id;
            if let Some(stop) = since_oid {
                if id == stop {
                    seen_since = true;
                } else if !seen_since {
                    // Still walking toward since; with OldestFirst we include from root.
                    // If since_sha is set, skip commits until we hit it, then take the rest.
                }
            }
            let meta = self.commit_meta(id)?;
            if let Some(min_ts) = since_date {
                if meta.author_ts < min_ts {
                    continue;
                }
            }
            commits.push(meta);
            // With OldestFirst, once we pass since_sha we keep going to HEAD.
        }

        if let Some(stop) = since_oid {
            let stop_hex = stop.to_hex().to_string();
            if let Some(pos) = commits.iter().position(|c| c.sha.as_str() == stop_hex) {
                commits = commits.split_off(pos);
            } else {
                return Err(Error::Git(format!(
                    "since-sha {stop_hex} not found in history"
                )));
            }
        }

        if let Some(n) = max {
            commits.truncate(n);
        }

        Ok(commits)
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
                    Ok(blob) => count_lines(blob.data.as_slice()),
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
}

fn count_lines(data: &[u8]) -> (i64, i64, bool) {
    if data.contains(&0) {
        return (0, 0, true);
    }
    let n = String::from_utf8_lossy(data).lines().count() as i64;
    (n, 0, false)
}

fn line_stats_new(id: &gix::Id<'_>) -> (i64, i64, bool) {
    match id.object().ok().and_then(|o| o.try_into_blob().ok()) {
        Some(blob) => {
            let (a, _, b) = count_lines(blob.data.as_slice());
            (a, 0, b)
        }
        None => (0, 0, false),
    }
}

fn line_stats_old(id: &gix::Id<'_>) -> (i64, i64, bool) {
    match id.object().ok().and_then(|o| o.try_into_blob().ok()) {
        Some(blob) => {
            let (d, _, b) = count_lines(blob.data.as_slice());
            (0, d, b)
        }
        None => (0, 0, false),
    }
}

fn line_stats_mod(old: &gix::Id<'_>, new: &gix::Id<'_>) -> (i64, i64, bool) {
    let old_blob = old.object().ok().and_then(|o| o.try_into_blob().ok());
    let new_blob = new.object().ok().and_then(|o| o.try_into_blob().ok());
    match (old_blob, new_blob) {
        (Some(o), Some(n)) => {
            if o.data.contains(&0) || n.data.contains(&0) {
                return (0, 0, true);
            }
            // Approximate: count lines present only in new / only in old via set diff of line strings.
            // Good enough for week-1; TODO: proper hunk-based counts via gix_diff::blob.
            let old_lines: std::collections::HashSet<&str> =
                std::str::from_utf8(&o.data).unwrap_or("").lines().collect();
            let new_lines: std::collections::HashSet<&str> =
                std::str::from_utf8(&n.data).unwrap_or("").lines().collect();
            let added = new_lines.difference(&old_lines).count() as i64;
            let deleted = old_lines.difference(&new_lines).count() as i64;
            (added, deleted, false)
        }
        _ => (0, 0, false),
    }
}

/// Collect commits from `repo` into `store`, respecting filters.
pub fn collect_git<R: GitRepo>(
    repo: &R,
    store: &crate::store::Store,
    since_sha: Option<&str>,
    since_date: Option<i64>,
    max: Option<usize>,
    max_files_per_commit: usize,
) -> Result<CollectGitStats> {
    let commits = repo.walk_topo_old_to_new(since_sha, since_date, max)?;
    let mut inserted = 0usize;
    let mut skipped = 0usize;
    let mut oversized = 0usize;

    for chunk in commits.chunks(200) {
        for meta in chunk {
            if store.commit_exists(meta.sha.as_str())? {
                skipped += 1;
                continue;
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
            let row = CommitRow {
                sha: meta.sha.as_str().to_string(),
                author_ts: meta.author_ts,
                message: meta.message.clone(),
                oversized: is_oversized,
                parents: meta
                    .parents
                    .iter()
                    .map(|p| p.as_str().to_string())
                    .collect(),
            };
            if !store.insert_commit(&row)? {
                skipped += 1;
                continue;
            }
            if !is_oversized {
                let rows: Vec<FileChangeRow> = changes
                    .into_iter()
                    .map(|c| FileChangeRow {
                        sha: meta.sha.as_str().to_string(),
                        path: c.path,
                        change_type: c.change_type.to_string(),
                        old_path: c.old_path,
                        lines_added: c.lines_added,
                        lines_deleted: c.lines_deleted,
                        is_binary: c.is_binary,
                    })
                    .collect();
                store.insert_file_changes(&rows)?;
            }
            inserted += 1;
        }
    }

    Ok(CollectGitStats {
        inserted,
        skipped,
        oversized,
    })
}

#[derive(Debug, Default)]
pub struct CollectGitStats {
    pub inserted: usize,
    pub skipped: usize,
    pub oversized: usize,
}
