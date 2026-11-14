//! CLI: bootstrap dry-run writes nothing; fingerprint refuse; cleanup; init exclude.

use std::collections::BTreeMap;
use std::fs;
use std::os::unix::fs::symlink;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::SystemTime;

use sha2::{Digest, Sha256};
use tempfile::TempDir;

fn bin() -> PathBuf {
    PathBuf::from(env!("CARGO_BIN_EXE_testpilot"))
}

fn git(cwd: &Path, args: &[&str]) {
    let st = Command::new("git")
        .args(args)
        .current_dir(cwd)
        .env("GIT_AUTHOR_NAME", "T")
        .env("GIT_AUTHOR_EMAIL", "t@e.com")
        .env("GIT_COMMITTER_NAME", "T")
        .env("GIT_COMMITTER_EMAIL", "t@e.com")
        .status()
        .unwrap();
    assert!(st.success(), "git {args:?}");
}

fn git_out(cwd: &Path, args: &[&str]) -> String {
    let out = Command::new("git")
        .args(args)
        .current_dir(cwd)
        .output()
        .unwrap();
    assert!(out.status.success(), "git {args:?}");
    String::from_utf8_lossy(&out.stdout).trim().to_string()
}

fn seed_repo() -> TempDir {
    let tmp = TempDir::new().unwrap();
    let root = tmp.path();
    git(root, &["init", "-b", "main"]);
    git(root, &["config", "user.email", "t@e.com"]);
    git(root, &["config", "user.name", "T"]);
    fs::write(root.join("f.txt"), "x\n").unwrap();
    git(root, &["add", "f.txt"]);
    git(root, &["commit", "-m", "i"]);
    tmp
}

fn tree_snapshot(root: &Path) -> BTreeMap<PathBuf, String> {
    let mut out = BTreeMap::new();
    fn walk(base: &Path, dir: &Path, out: &mut BTreeMap<PathBuf, String>) {
        let Ok(rd) = fs::read_dir(dir) else {
            return;
        };
        for ent in rd.flatten() {
            let path = ent.path();
            let rel = path.strip_prefix(base).unwrap_or(&path).to_path_buf();
            let ft = match ent.file_type() {
                Ok(t) => t,
                Err(_) => continue,
            };
            if ft.is_symlink() {
                let target = fs::read_link(&path).unwrap_or_default();
                let meta = fs::symlink_metadata(&path).ok();
                let mtime = meta
                    .and_then(|m| m.modified().ok())
                    .unwrap_or(SystemTime::UNIX_EPOCH);
                out.insert(rel, format!("symlink:{target:?}|{mtime:?}"));
            } else if ft.is_dir() {
                walk(base, &path, out);
            } else if ft.is_file() {
                let bytes = fs::read(&path).unwrap_or_default();
                let meta = fs::metadata(&path).ok();
                let mtime = meta
                    .and_then(|m| m.modified().ok())
                    .unwrap_or(SystemTime::UNIX_EPOCH);
                let mut h = Sha256::new();
                h.update(&bytes);
                out.insert(
                    rel,
                    format!("{}|{mtime:?}|{}", bytes.len(), hex::encode(h.finalize())),
                );
            }
        }
    }
    walk(root, root, &mut out);
    out
}

#[test]
fn dry_run_writes_nothing_and_prints_priors_source() {
    let tmp = seed_repo();
    let root = tmp.path();
    let out = Command::new(bin())
        .args([
            "bootstrap",
            "--dry-run",
            "--repo",
            root.to_str().unwrap(),
            "--max-commits",
            "200",
            "--mutants-per-commit",
            "4",
        ])
        .output()
        .unwrap();
    assert!(
        out.status.success(),
        "stdout={} stderr={}",
        String::from_utf8_lossy(&out.stdout),
        String::from_utf8_lossy(&out.stderr)
    );
    let stdout = String::from_utf8_lossy(&out.stdout);
    assert!(stdout.contains("backfill"), "{stdout}");
    assert!(stdout.contains("|B_set|≈200"), "{stdout}");
    assert!(
        stdout.contains("docs/measurements/bootstrap_time_priors.json"),
        "{stdout}"
    );
    assert!(stdout.contains("B_override=null"), "{stdout}");
    assert!(
        !root.join(".testpilot").exists(),
        "dry-run must not create .testpilot/"
    );
    assert!(
        !root.join(".testpilot/bootstrap/state.json").exists(),
        "dry-run must not create state.json"
    );
}

#[test]
fn dry_run_uses_local_priors_json_without_touching_tree() {
    let tmp = seed_repo();
    let root = tmp.path();
    let priors_dir = root.join(".testpilot/bootstrap");
    fs::create_dir_all(&priors_dir).unwrap();
    // Extreme backfill seconds so estimate text clearly differs from embedded (120s).
    let custom = r#"{
      "mutate_s_per_mutant": {"click": 1.0, "jinja": 1.0, "measured": true},
      "backfill_s_per_commit": {"click": 9999.0, "jinja": 9999.0, "measured": true},
      "collect_git_s": {"click": 1.0, "jinja": 1.0, "measured": true},
      "dump_train_s": {"click": 1.0, "jinja": 1.0, "measured": true}
    }"#;
    let priors_path = priors_dir.join("priors.json");
    fs::write(&priors_path, custom).unwrap();
    let before = tree_snapshot(root);

    let out = Command::new(bin())
        .args([
            "bootstrap",
            "--dry-run",
            "--repo",
            root.to_str().unwrap(),
            "--max-commits",
            "10",
            "--backfill-commits",
            "5",
        ])
        .output()
        .unwrap();
    assert!(
        out.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&out.stderr)
    );
    let stdout = String::from_utf8_lossy(&out.stdout);
    assert!(
        stdout.contains(priors_path.to_str().unwrap())
            || stdout.contains(".testpilot/bootstrap/priors.json"),
        "must cite local priors path: {stdout}"
    );
    // --backfill-commits 5 → limb=5 × 9999s ≈ large hours; measured (no unmeasured=true).
    assert!(
        stdout.contains("|B_set|≈5") || stdout.contains("≈5 SHAs"),
        "{stdout}"
    );
    assert!(
        stdout.contains("estimate backfill:") && !stdout.contains("unmeasured=true"),
        "{stdout}"
    );
    assert!(
        stdout.contains(" h") || stdout.contains("min"),
        "custom huge backfill should show as hours/min: {stdout}"
    );
    let after = tree_snapshot(root);
    assert_eq!(
        before, after,
        "dry-run must not modify tree when priors exist"
    );
}

#[test]
fn dry_run_backfill_commits_consistent_b_set() {
    let tmp = seed_repo();
    let root = tmp.path();
    let out = Command::new(bin())
        .args([
            "bootstrap",
            "--dry-run",
            "--repo",
            root.to_str().unwrap(),
            "--max-commits",
            "200",
            "--backfill-commits",
            "30",
        ])
        .output()
        .unwrap();
    assert!(out.status.success());
    let stdout = String::from_utf8_lossy(&out.stdout);
    // Cold path: --backfill-commits overrides limb (not N+30).
    assert!(stdout.contains("|B_set|≈30"), "{stdout}");
    assert!(stdout.contains("≈30 SHAs"), "{stdout}");
    assert!(!stdout.contains("≈230 SHAs"), "{stdout}");
}

#[test]
fn dry_run_does_not_remove_stale_wt_or_change_files() {
    let tmp = seed_repo();
    let root = tmp.path();
    let wt = root.join(".testpilot/bootstrap/wt-stale");
    fs::create_dir_all(&wt).unwrap();
    fs::write(wt.join("x"), "y").unwrap();
    let before = tree_snapshot(root);

    let out = Command::new(bin())
        .args(["bootstrap", "--dry-run", "--repo", root.to_str().unwrap()])
        .output()
        .unwrap();
    assert!(out.status.success());
    assert!(wt.exists(), "dry-run must not delete stale wt-*");
    let after = tree_snapshot(root);
    assert_eq!(
        before, after,
        "dry-run must not change any files (incl .git)"
    );
}

#[test]
fn fingerprint_mismatch_refuses_resume() {
    let tmp = seed_repo();
    let root = tmp.path();
    let ok = Command::new(bin())
        .args([
            "bootstrap",
            "--repo",
            root.to_str().unwrap(),
            "--max-commits",
            "200",
        ])
        .output()
        .unwrap();
    assert!(
        ok.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&ok.stderr)
    );
    assert!(root.join(".testpilot/bootstrap/state.json").is_file());

    let bad = Command::new(bin())
        .args([
            "bootstrap",
            "--repo",
            root.to_str().unwrap(),
            "--max-commits",
            "160",
        ])
        .output()
        .unwrap();
    assert!(!bad.status.success());
    let err = format!(
        "{}{}",
        String::from_utf8_lossy(&bad.stdout),
        String::from_utf8_lossy(&bad.stderr)
    );
    assert!(err.contains("--force"), "{err}");
}

#[test]
fn force_restarts_after_mismatch() {
    let tmp = seed_repo();
    let root = tmp.path();
    assert!(
        Command::new(bin())
            .args([
                "bootstrap",
                "--repo",
                root.to_str().unwrap(),
                "--max-commits",
                "200"
            ])
            .status()
            .unwrap()
            .success()
    );
    let out = Command::new(bin())
        .args([
            "bootstrap",
            "--repo",
            root.to_str().unwrap(),
            "--max-commits",
            "160",
            "--force",
        ])
        .output()
        .unwrap();
    assert!(
        out.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&out.stderr)
    );
    let raw = fs::read_to_string(root.join(".testpilot/bootstrap/state.json")).unwrap();
    assert!(
        raw.contains("\"N\": 160") || raw.contains("\"N\":160"),
        "{raw}"
    );
}

#[test]
fn no_resume_without_force_refuses_existing_state() {
    let tmp = seed_repo();
    let root = tmp.path();
    assert!(
        Command::new(bin())
            .args(["bootstrap", "--repo", root.to_str().unwrap()])
            .status()
            .unwrap()
            .success()
    );
    let bad = Command::new(bin())
        .args(["bootstrap", "--repo", root.to_str().unwrap(), "--no-resume"])
        .output()
        .unwrap();
    assert!(!bad.status.success());
    let err = format!(
        "{}{}",
        String::from_utf8_lossy(&bad.stdout),
        String::from_utf8_lossy(&bad.stderr)
    );
    assert!(
        err.contains("state.json") || err.contains("--force"),
        "{err}"
    );
}

#[test]
fn no_resume_with_force_resets_steps_pending() {
    let tmp = seed_repo();
    let root = tmp.path();
    assert!(
        Command::new(bin())
            .args(["bootstrap", "--repo", root.to_str().unwrap()])
            .status()
            .unwrap()
            .success()
    );
    // Mark a step done in place.
    let path = root.join(".testpilot/bootstrap/state.json");
    let mut v: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(&path).unwrap()).unwrap();
    v["steps"]["mutate"] = serde_json::json!("done");
    fs::write(&path, serde_json::to_string_pretty(&v).unwrap()).unwrap();

    let out = Command::new(bin())
        .args([
            "bootstrap",
            "--repo",
            root.to_str().unwrap(),
            "--no-resume",
            "--force",
        ])
        .output()
        .unwrap();
    assert!(
        out.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&out.stderr)
    );
    let raw = fs::read_to_string(&path).unwrap();
    let v: serde_json::Value = serde_json::from_str(&raw).unwrap();
    // Fresh --force restarts markers; pipeline may then advance cleanup/init/collect
    // and skip backfill/mutate/dump on non-Python fixture repos.
    assert_eq!(v["steps"]["cleanup"].as_str(), Some("done"), "{raw}");
    assert_eq!(v["steps"]["init"].as_str(), Some("done"), "{raw}");
    for step in [
        "backfill",
        "mutate",
        "features_dump",
        "train",
        "gate",
        "finalize",
    ] {
        let st = v["steps"][step].as_str();
        assert!(
            st == Some("pending") || st == Some("skipped"),
            "step {step} unexpected {st:?}: {raw}"
        );
    }
}

#[test]
fn corrupt_state_errors_with_state_json() {
    let tmp = seed_repo();
    let root = tmp.path();
    fs::create_dir_all(root.join(".testpilot/bootstrap")).unwrap();
    fs::write(
        root.join(".testpilot/bootstrap/state.json"),
        "{\"fingerprint\":",
    )
    .unwrap();
    let out = Command::new(bin())
        .args(["bootstrap", "--repo", root.to_str().unwrap()])
        .output()
        .unwrap();
    assert!(!out.status.success());
    let err = format!(
        "{}{}",
        String::from_utf8_lossy(&out.stdout),
        String::from_utf8_lossy(&out.stderr)
    );
    assert!(err.contains("state.json"), "{err}");
}

#[test]
fn startup_removes_stale_wt() {
    let tmp = seed_repo();
    let root = tmp.path();
    let wt = root.join(".testpilot/bootstrap/wt-stale");
    fs::create_dir_all(&wt).unwrap();
    fs::write(wt.join("x"), "y").unwrap();
    assert!(
        Command::new(bin())
            .args(["bootstrap", "--repo", root.to_str().unwrap()])
            .status()
            .unwrap()
            .success()
    );
    assert!(!wt.exists());
}

#[test]
fn cleanup_symlink_wt_leaves_external_target() {
    let tmp = seed_repo();
    let root = tmp.path();
    let outside = tmp.path().join("outside_keep");
    fs::create_dir_all(&outside).unwrap();
    fs::write(outside.join("payload"), "alive\n").unwrap();
    let boot = root.join(".testpilot/bootstrap");
    fs::create_dir_all(&boot).unwrap();
    symlink(&outside, boot.join("wt-out")).unwrap();

    assert!(
        Command::new(bin())
            .args(["bootstrap", "--repo", root.to_str().unwrap()])
            .status()
            .unwrap()
            .success()
    );
    assert!(!boot.join("wt-out").exists());
    assert_eq!(
        fs::read_to_string(outside.join("payload")).unwrap(),
        "alive\n"
    );
}

#[test]
fn init_exclude_only_idempotent_not_gitignore_clean_status() {
    let tmp = seed_repo();
    let root = tmp.path();
    for _ in 0..2 {
        let out = Command::new(bin())
            .arg("init")
            .current_dir(root)
            .output()
            .unwrap();
        assert!(
            out.status.success(),
            "{}",
            String::from_utf8_lossy(&out.stderr)
        );
    }
    let exclude = root.join(".git/info/exclude");
    let text = fs::read_to_string(&exclude).unwrap();
    let count = text.lines().filter(|l| l.trim() == ".testpilot/").count();
    assert_eq!(count, 1, "must be idempotent: {text}");
    assert!(
        !root.join(".gitignore").exists()
            || !fs::read_to_string(root.join(".gitignore"))
                .unwrap_or_default()
                .contains(".testpilot"),
        ".testpilot must not be in .gitignore"
    );
    // DB already created by init; status must stay clean (exclude hides .testpilot/).
    let porcelain = git_out(root, &["status", "--porcelain"]);
    assert!(
        porcelain.is_empty(),
        "git status must be clean after DB under .testpilot/: {porcelain}"
    );
}

#[test]
fn mutate_skips_with_no_eligible_c_when_resample_empty() {
    // Root commit adds .py (not eligible — no first-parent). Later commits are
    // README-only → after backfill + re-sample, n_eligible=0 → mutate done with
    // reason=no_eligible_C (not an error, not max(1)).
    let tmp = TempDir::new().unwrap();
    let root = tmp.path();
    git(root, &["init", "-b", "main"]);
    git(root, &["config", "user.email", "t@e.com"]);
    git(root, &["config", "user.name", "T"]);
    fs::write(
        root.join("pyproject.toml"),
        "[project]\nname='t'\nversion='0'\n[tool.pytest.ini_options]\n",
    )
    .unwrap();
    fs::create_dir_all(root.join("pkg")).unwrap();
    fs::write(root.join("pkg/__init__.py"), "x = 1\n").unwrap();
    fs::create_dir_all(root.join("tests")).unwrap();
    fs::write(root.join("tests/test_x.py"), "def test_ok():\n    assert True\n").unwrap();
    git(root, &["add", "."]);
    git(root, &["commit", "-m", "root"]);
    for i in 0..3 {
        fs::write(root.join("README.md"), format!("n={i}\n")).unwrap();
        git(root, &["add", "README.md"]);
        git(root, &["commit", "-m", &format!("docs {i}")]);
    }

    let out_dir = root.join("model-out");
    let out = Command::new(bin())
        .args([
            "bootstrap",
            "--repo",
            root.to_str().unwrap(),
            "--out",
            out_dir.to_str().unwrap(),
            "--max-commits",
            "2",
            "--backfill-commits",
            "3",
            "--mutants-per-commit",
            "1",
            "--force",
        ])
        .output()
        .unwrap();
    assert!(
        out.status.success(),
        "stdout={} stderr={}",
        String::from_utf8_lossy(&out.stdout),
        String::from_utf8_lossy(&out.stderr)
    );
    let stdout = String::from_utf8_lossy(&out.stdout);
    assert!(
        stdout.contains("no_eligible_C") || stdout.contains("mutate: skipped"),
        "{stdout}"
    );
    let report: serde_json::Value = serde_json::from_str(
        &fs::read_to_string(out_dir.join("bootstrap_report.json")).unwrap(),
    )
    .unwrap();
    assert_eq!(
        report["mutate_skip_reason"].as_str(),
        Some("no_eligible_C"),
        "{report}"
    );
    assert_eq!(report["n_actual"].as_u64().unwrap_or(99), 0, "{report}");
    let state: serde_json::Value = serde_json::from_str(
        &fs::read_to_string(root.join(".testpilot/bootstrap/state.json")).unwrap(),
    )
    .unwrap();
    assert_eq!(state["steps"]["mutate"], "done", "{state}");
}

#[test]
fn init_from_worktree_writes_common_dir_exclude() {
    let tmp = TempDir::new().unwrap();
    let main = tmp.path().join("main");
    let wt = tmp.path().join("wt");
    fs::create_dir_all(&main).unwrap();
    git(&main, &["init", "-b", "main"]);
    git(&main, &["config", "user.email", "t@e.com"]);
    git(&main, &["config", "user.name", "T"]);
    fs::write(main.join("f.txt"), "x\n").unwrap();
    git(&main, &["add", "f.txt"]);
    git(&main, &["commit", "-m", "i"]);
    git(&main, &["branch", "wt-branch"]);
    git(
        &main,
        &["worktree", "add", wt.to_str().unwrap(), "wt-branch"],
    );

    let out = Command::new(bin())
        .arg("init")
        .current_dir(&wt)
        .output()
        .unwrap();
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );

    // Common exclude via --git-common-dir
    let common = git_out(&wt, &["rev-parse", "--git-common-dir"]);
    let common_path = if Path::new(&common).is_absolute() {
        PathBuf::from(&common)
    } else {
        wt.join(&common)
    };
    let exclude = common_path.join("info/exclude");
    let text = fs::read_to_string(&exclude).unwrap();
    assert!(
        text.lines().any(|l| l.trim() == ".testpilot/"),
        "common exclude missing .testpilot/: {exclude:?} {text}"
    );
}
