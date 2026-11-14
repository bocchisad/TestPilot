//! Bootstrap state machine: fingerprint resume, cleanup, dry-run estimates (T2).

use std::fs;
use std::os::unix::fs::symlink;
use std::path::Path;
use std::process::Command;

use tempfile::tempdir;
use testpilot_core::bootstrap::{
    BootstrapInputs, Fingerprint, ResumeDecision, StepStatus, decide_resume, default_priors,
    estimate_wall, format_dry_run_report, load_or_init_state, load_state, resolve_fingerprint,
    save_state, startup_cleanup, state_path,
};
use testpilot_core::store::SCHEMA_VERSION;

fn git(cwd: &Path, args: &[&str]) {
    let st = Command::new("git")
        .args(args)
        .current_dir(cwd)
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
    assert!(out.status.success());
    String::from_utf8(out.stdout).unwrap().trim().to_string()
}

fn seed_repo() -> (tempfile::TempDir, String) {
    let dir = tempdir().unwrap();
    let root = dir.path();
    git(root, &["init"]);
    git(root, &["config", "user.email", "t@example.com"]);
    git(root, &["config", "user.name", "t"]);
    fs::write(root.join("f.txt"), "a\n").unwrap();
    git(root, &["add", "f.txt"]);
    git(root, &["commit", "-m", "c1"]);
    let head = git_out(root, &["rev-parse", "HEAD"]);
    (dir, head)
}

fn inputs(head: &str, n: u32) -> BootstrapInputs {
    BootstrapInputs {
        head_sha: head.to_string(),
        n,
        k: 4,
        seed: 42,
        b_override: None,
        bin_version: "0.1.0 (deadbeef)".into(),
        schema_version: SCHEMA_VERSION,
    }
}

#[test]
fn resume_refuses_fingerprint_mismatch_without_force() {
    let (dir, head) = seed_repo();
    let root = dir.path();
    let fp = resolve_fingerprint(&inputs(&head, 200));
    let state = load_or_init_state(root, fp.clone(), true).unwrap();
    save_state(root, &state).unwrap();

    let changed = resolve_fingerprint(&inputs(&head, 160));
    match decide_resume(root, &changed, false).unwrap() {
        ResumeDecision::Refuse { hint } => {
            assert!(hint.contains("--force"), "{hint}");
        }
        other => panic!("expected Refuse, got {other:?}"),
    }
}

#[test]
fn resume_force_replaces_state_on_mismatch() {
    let (dir, head) = seed_repo();
    let root = dir.path();
    let fp = resolve_fingerprint(&inputs(&head, 200));
    let mut state = load_or_init_state(root, fp, true).unwrap();
    state.steps.insert("mutate".into(), StepStatus::Done);
    save_state(root, &state).unwrap();

    let changed = resolve_fingerprint(&inputs(&head, 160));
    match decide_resume(root, &changed, true).unwrap() {
        ResumeDecision::Fresh(s) => {
            assert_eq!(s.fingerprint.n, 160);
            assert!(s.steps.values().all(|st| *st == StepStatus::Pending));
        }
        other => panic!("expected Fresh under --force, got {other:?}"),
    }
}

#[test]
fn resume_continues_when_fingerprint_matches() {
    let (dir, head) = seed_repo();
    let root = dir.path();
    let fp = resolve_fingerprint(&inputs(&head, 200));
    let mut state = load_or_init_state(root, fp.clone(), true).unwrap();
    state.steps.insert("cleanup".into(), StepStatus::Done);
    save_state(root, &state).unwrap();

    match decide_resume(root, &fp, false).unwrap() {
        ResumeDecision::Continue(s) => {
            assert_eq!(s.steps.get("cleanup"), Some(&StepStatus::Done));
        }
        other => panic!("expected Continue, got {other:?}"),
    }
}

#[test]
fn save_state_is_atomic_no_tmp_left_valid_json() {
    let (dir, head) = seed_repo();
    let root = dir.path();
    let fp = resolve_fingerprint(&inputs(&head, 200));
    let state = load_or_init_state(root, fp, true).unwrap();
    save_state(root, &state).unwrap();

    let tmp = root.join(".testpilot/bootstrap/state.json.tmp");
    assert!(!tmp.exists(), "tmp must be gone after save");
    let raw = fs::read_to_string(state_path(root)).unwrap();
    let v: serde_json::Value = serde_json::from_str(&raw).expect("valid JSON");
    assert!(v.get("fingerprint").is_some());
    assert!(v["fingerprint"].get("N").is_some(), "spec keys: {raw}");
    assert!(v["fingerprint"].get("K").is_some(), "{raw}");
    assert!(v["fingerprint"].get("B_policy").is_some(), "{raw}");
    assert!(
        v["fingerprint"].get("B_override").is_some(),
        "B_override present even when null: {raw}"
    );
    assert!(v["fingerprint"]["B_override"].is_null(), "{raw}");
}

#[test]
fn corrupt_state_json_errors_with_state_json_not_panic() {
    let (dir, _head) = seed_repo();
    let root = dir.path();
    fs::create_dir_all(root.join(".testpilot/bootstrap")).unwrap();
    fs::write(state_path(root), "{\"fingerprint\":").unwrap(); // truncated
    let err = load_state(root).unwrap_err();
    let msg = err.to_string();
    assert!(msg.contains("state.json"), "{msg}");
}

#[test]
fn startup_cleanup_prunes_stale_wt_dirs() {
    let (dir, _head) = seed_repo();
    let root = dir.path();
    let boot = root.join(".testpilot/bootstrap");
    fs::create_dir_all(&boot).unwrap();
    let stale = boot.join("wt-dead");
    fs::create_dir_all(&stale).unwrap();
    fs::write(stale.join("x"), "y").unwrap();
    fs::write(boot.join("state.json"), "{}\n").unwrap();

    let report = startup_cleanup(root).unwrap();
    assert!(!stale.exists());
    assert!(boot.join("state.json").exists());
    assert!(report.removed_wt_dirs >= 1);
}

#[test]
fn startup_cleanup_symlink_wt_unlinks_only_leaves_target() {
    let (dir, _head) = seed_repo();
    let root = dir.path();
    let outside = dir.path().join("outside_target");
    fs::create_dir_all(&outside).unwrap();
    let marker = outside.join("keep_me");
    fs::write(&marker, "safe\n").unwrap();

    let boot = root.join(".testpilot/bootstrap");
    fs::create_dir_all(&boot).unwrap();
    let link = boot.join("wt-linked");
    symlink(&outside, &link).unwrap();

    let report = startup_cleanup(root).unwrap();
    assert!(!link.exists(), "symlink removed");
    assert!(marker.exists(), "external target must survive");
    assert_eq!(fs::read_to_string(&marker).unwrap(), "safe\n");
    assert!(report.removed_wt_dirs >= 1);
}

#[test]
fn dry_run_report_lists_backfill_separately_as_unmeasured() {
    let priors = default_priors();
    let est = estimate_wall(200, 4, 2, "click", &priors, 20);
    let fp = Fingerprint {
        head_sha: "abc".into(),
        n: 200,
        k: 4,
        seed: 42,
        b_policy: "sampled_C_union_last_20_fp".into(),
        b_override: None,
        bin_version: "0.1.0 (x)".into(),
        schema_version: SCHEMA_VERSION,
        schema_sql_sha256: "00".into(),
    };
    let text = format_dry_run_report(
        &fp,
        &est,
        true,
        "docs/measurements/bootstrap_time_priors.json (embedded)",
    );
    assert!(text.contains("backfill"), "{text}");
    assert!(text.contains("unmeasured"), "{text}");
    assert!(text.contains("priors:"), "{text}");
    assert!(text.contains("bootstrap_time_priors.json"), "{text}");
    let lines: Vec<_> = text.lines().collect();
    assert!(
        lines
            .iter()
            .any(|l| l.contains("backfill") && !l.contains("mutate")),
        "{text}"
    );
}

#[test]
fn estimate_respects_backfill_commits_last_fp() {
    let priors = default_priors();
    let est20 = estimate_wall(200, 4, 1, "click", &priors, 20);
    let est30 = estimate_wall(200, 4, 1, "click", &priors, 30);
    assert_eq!(est20.backfill_shas_hat, 220);
    assert_eq!(est30.backfill_shas_hat, 230);
    let fp = resolve_fingerprint(&inputs("abc", 200));
    let text = format_dry_run_report(&fp, &est30, true, "embedded");
    assert!(text.contains("|B_set|≈230"), "{text}");
    assert!(text.contains("≈230 SHAs"), "{text}");
}

#[test]
fn dry_run_path_does_not_create_state_file() {
    let (dir, head) = seed_repo();
    let root = dir.path();
    let fp = resolve_fingerprint(&inputs(&head, 200));
    let priors = default_priors();
    let est = estimate_wall(200, 4, 1, "click", &priors, 20);
    let _ = format_dry_run_report(&fp, &est, false, "embedded");
    assert!(!state_path(root).exists());
}
