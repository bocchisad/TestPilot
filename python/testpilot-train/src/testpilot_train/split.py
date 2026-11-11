"""Chronological and leave-one-repo-out splits."""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl


@dataclass(frozen=True)
class RepoSplit:
    repo: str
    train_changes: tuple[str, ...]
    val_changes: tuple[str, ...]
    test_changes: tuple[str, ...]
    train_commit_idx: tuple[int, int]
    val_commit_idx: tuple[int, int]
    test_commit_idx: tuple[int, int]


@dataclass(frozen=True)
class SplitPlan:
    by_repo: tuple[RepoSplit, ...]

    def changes_for(self, slice_name: str) -> set[tuple[str, str]]:
        out: set[tuple[str, str]] = set()
        for rs in self.by_repo:
            ch = getattr(rs, f"{slice_name}_changes")
            for c in ch:
                out.add((rs.repo, c))
        return out


def _ordered_changes(frame: pl.DataFrame, repo: str) -> list[tuple[str, int]]:
    sub = (
        frame.filter(pl.col("repo") == repo)
        .select(["change", "commit_idx"])
        .unique()
        .sort(["commit_idx", "change"])
    )
    return list(zip(sub["change"].to_list(), sub["commit_idx"].to_list(), strict=True))


def _unique_commit_idxs(frame: pl.DataFrame, repo: str) -> list[int]:
    sub = (
        frame.filter(pl.col("repo") == repo)
        .select("commit_idx")
        .unique()
        .sort("commit_idx")
    )
    return sub["commit_idx"].to_list()


def _partition_by_commit_idx(
    ordered: list[tuple[str, int]], unique_idxs: list[int], i_train: int, i_val: int
) -> tuple[list[tuple[str, int]], list[tuple[str, int]], list[tuple[str, int]]]:
    train_i = set(unique_idxs[:i_train])
    val_i = set(unique_idxs[i_train:i_val])
    test_i = set(unique_idxs[i_val:])
    train = [(c, i) for c, i in ordered if i in train_i]
    val = [(c, i) for c, i in ordered if i in val_i]
    test = [(c, i) for c, i in ordered if i in test_i]
    return train, val, test


def _repo_split_from_chunks(
    repo: str,
    train: list[tuple[str, int]],
    val: list[tuple[str, int]],
    test: list[tuple[str, int]],
) -> RepoSplit:
    assert train and val and test

    def rng(chunk: list[tuple[str, int]]) -> tuple[int, int]:
        idxs = [c for _, c in chunk]
        return (min(idxs), max(idxs))

    rs = RepoSplit(
        repo=repo,
        train_changes=tuple(c for c, _ in train),
        val_changes=tuple(c for c, _ in val),
        test_changes=tuple(c for c, _ in test),
        train_commit_idx=rng(train),
        val_commit_idx=rng(val),
        test_commit_idx=rng(test),
    )
    assert max(i for _, i in train) < min(i for _, i in val) < min(i for _, i in test)
    return rs


def chrono_split(frame: pl.DataFrame, min_changes: int = 10) -> SplitPlan:
    """Partition ordered unique ``commit_idx`` values 70/15/15; all changes per idx stay together."""
    repos = sorted(frame["repo"].unique().to_list())
    parts: list[RepoSplit] = []
    for repo in repos:
        unique_idxs = _unique_commit_idxs(frame, repo)
        ordered = _ordered_changes(frame, repo)
        n = len(unique_idxs)
        if n < min_changes:
            raise ValueError(f"repo {repo!r}: only {n} commit_idx values (need >= {min_changes})")
        i_train = int(0.70 * n)
        i_val = int(0.85 * n)
        train, val, test = _partition_by_commit_idx(ordered, unique_idxs, i_train, i_val)
        parts.append(_repo_split_from_chunks(repo, train, val, test))
    return SplitPlan(by_repo=tuple(parts))


def _chrono_split_by_changes(frame: pl.DataFrame, min_changes: int = 10) -> SplitPlan:
    """Legacy: partition unique (change, commit_idx) rows by change count (pre-M8)."""
    repos = sorted(frame["repo"].unique().to_list())
    parts: list[RepoSplit] = []
    for repo in repos:
        ordered = _ordered_changes(frame, repo)
        n = len(ordered)
        if n < min_changes:
            raise ValueError(f"repo {repo!r}: only {n} changes (need >= {min_changes})")
        i_train = int(0.70 * n)
        i_val = int(0.85 * n)
        train, val, test = ordered[:i_train], ordered[i_train:i_val], ordered[i_val:]
        parts.append(_repo_split_from_chunks(repo, train, val, test))
    return SplitPlan(by_repo=tuple(parts))


def filter_slice(frame: pl.DataFrame, plan: SplitPlan, slice_name: str) -> pl.DataFrame:
    wanted = plan.changes_for(slice_name)
    repos = [r for r, _ in wanted]
    changes = [c for _, c in wanted]
    keys = pl.DataFrame({"repo": repos, "change": changes})
    return frame.join(keys, on=["repo", "change"], how="semi")


def split_to_json(plan: SplitPlan) -> dict:
    return {
        rs.repo: {
            "train": list(rs.train_commit_idx),
            "val": list(rs.val_commit_idx),
            "test": list(rs.test_commit_idx),
            "n_changes": {
                "train": len(rs.train_changes),
                "val": len(rs.val_changes),
                "test": len(rs.test_changes),
            },
        }
        for rs in plan.by_repo
    }


def loro_train_val(
    frame: pl.DataFrame, plan: SplitPlan, held_out: str
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Train/val from all repos except held_out; test = held_out chrono test third.

    Early stopping uses the concatenated chrono val of the *other* repos.
    """
    repos = [rs.repo for rs in plan.by_repo]
    if held_out not in repos:
        raise ValueError(f"held_out repo {held_out!r} not in plan")
    if len(repos) < 2:
        raise ValueError("LORO requires ≥2 repos")

    train_parts: list[pl.DataFrame] = []
    val_parts: list[pl.DataFrame] = []
    for rs in plan.by_repo:
        if rs.repo == held_out:
            continue
        sub = frame.filter(pl.col("repo") == rs.repo)
        train_parts.append(
            sub.filter(pl.col("change").is_in(list(rs.train_changes)))
        )
        val_parts.append(sub.filter(pl.col("change").is_in(list(rs.val_changes))))
    held = next(rs for rs in plan.by_repo if rs.repo == held_out)
    test_df = frame.filter(
        (pl.col("repo") == held_out) & pl.col("change").is_in(list(held.test_changes))
    )
    return pl.concat(train_parts), pl.concat(val_parts), test_df
