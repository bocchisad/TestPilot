import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from collect_real_bugfix_pairs import _adds_test_behavior, scan_repository


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def commit(repo: Path, message: str, files: dict[str, str]) -> str:
    for name, content in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    git(repo, "add", "--all")
    git(
        repo,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-m",
        message,
    )
    return git(repo, "rev-parse", "HEAD")


def test_full_history_scan_counts_message_and_changes_candidates(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    commit(
        repo,
        "initial",
        {"src/pkg.py": "old = 0\n", "tests/test_pkg.py": "def test_pkg(): pass\n"},
    )
    bug_fix = commit(
        repo,
        "Fix package behavior; fixes #42",
        {
            "src/pkg.py": "new = 1\n",
            "tests/test_pkg.py": "def test_pkg(): assert True\n",
        },
    )
    changes_only = commit(repo, "Document release", {"CHANGES.md": "Fixed behavior.\n"})
    typo = commit(repo, "Fix typo in docs", {"docs/readme.md": "fixed\n"})

    result = scan_repository("sample", repo)

    assert result["history_commit_count"] == 4
    assert result["candidate_count"] == 3
    rows = {row["commit"]: row for row in result["candidate_commits"]}
    assert rows[bug_fix]["match_reasons"] == ["message_pattern"]
    assert rows[bug_fix]["has_source_and_test_changes"]
    assert rows[bug_fix]["has_test_behavior_addition"]
    assert rows[changes_only]["match_reasons"] == ["changes_file"]
    assert not rows[changes_only]["has_source_and_test_changes"]
    assert rows[typo]["match_reasons"] == ["message_pattern"]
    complete, error = _adds_test_behavior(repo, "missing-parent", bug_fix)
    assert complete is None
    assert error
