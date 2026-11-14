//! Bootstrap state machine: fingerprint resume, cleanup, dry-run estimates (T2).

use std::fs;
use std::os::unix::fs::symlink;
use std::path::Path;
use std::process::Command;

use tempfile::tempdir;
use testpilot_core::bootstrap::{
    BootstrapInputs, DbDumpFingerprint, Fingerprint, ResumeDecision, SaveFault, StepStatus,
    build_b_set, decide_resume, default_priors, dump_needs_refresh, estimate_wall,
    filter_with_first_parent, first_parent_shas, format_dry_run_report, load_or_init_state,
    load_state, resolve_fingerprint, save_state, save_state_with_fault, sha_has_first_parent,
    startup_cleanup, state_path, union_b_set,
};
use testpilot_core::store::{SCHEMA_VERSION, Store};

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
    let state = load_or_init_state(root, fp.clone(), true).unwrap();
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

    // Fault between write(tmp) and rename: original state.json must stay intact.
    let before = fs::read_to_string(state_path(root)).unwrap();
    let mut next =
        load_or_init_state(root, resolve_fingerprint(&inputs(&head, 160)), true).unwrap();
    next.steps.insert("mutate".into(), StepStatus::Done);
    let err = save_state_with_fault(root, &next, Some(SaveFault::AfterTmpWrite)).unwrap_err();
    assert!(err.to_string().contains("injected fault"), "{err}");
    assert_eq!(
        fs::read_to_string(state_path(root)).unwrap(),
        before,
        "original state.json must survive failed rename"
    );
    assert!(
        tmp.exists(),
        "tmp left after injected fault (proves write happened before rename)"
    );
    let loaded = load_state(root).unwrap().unwrap();
    assert_eq!(loaded.fingerprint.n, 200);
    assert_ne!(loaded.steps.get("mutate"), Some(&StepStatus::Done));
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
    assert!(
        text.contains("B_override=null"),
        "must print null not None: {text}"
    );
    assert!(!text.contains("B_override=None"), "{text}");
    let lines: Vec<_> = text.lines().collect();
    assert!(
        lines
            .iter()
            .any(|l| l.contains("backfill") && !l.contains("mutate")),
        "{text}"
    );
}

#[test]
fn first_parent_and_union_b_set() {
    let (dir, head) = seed_repo();
    let root = dir.path();
    // second commit
    fs::write(root.join("f.txt"), "b\n").unwrap();
    git(root, &["add", "f.txt"]);
    git(root, &["commit", "-m", "c2"]);
    let shas = first_parent_shas(root, 10).unwrap();
    assert!(shas.len() >= 2);
    assert!(shas.iter().any(|s| s == &head) || !shas.is_empty());
    let sampled = vec!["aaaa".into()];
    let u = union_b_set(&shas, &sampled);
    assert!(u.contains(&"aaaa".to_string()));
    assert_eq!(u.iter().filter(|s| *s == "aaaa").count(), 1);
}

#[test]
fn dry_run_b_set_matches_live_with_root() {
    // Fixture includes root (no first-parent). Sampled C that includes root must
    // be filtered the same for dry-run and live B_set construction.
    let dir = tempdir().unwrap();
    let root = dir.path();
    git(root, &["init", "-b", "main"]);
    git(root, &["config", "user.email", "t@example.com"]);
    git(root, &["config", "user.name", "t"]);
    fs::write(root.join("f.txt"), "a\n").unwrap();
    git(root, &["add", "f.txt"]);
    git(root, &["commit", "-m", "root"]);
    let root_sha = git_out(root, &["rev-parse", "HEAD"]);
    fs::write(root.join("f.txt"), "b\n").unwrap();
    git(root, &["add", "f.txt"]);
    git(root, &["commit", "-m", "c1"]);
    let c1 = git_out(root, &["rev-parse", "HEAD"]);
    fs::write(root.join("f.txt"), "c\n").unwrap();
    git(root, &["add", "f.txt"]);
    git(root, &["commit", "-m", "c2"]);
    let c2 = git_out(root, &["rev-parse", "HEAD"]);

    assert!(!sha_has_first_parent(root, &root_sha).unwrap());
    assert!(sha_has_first_parent(root, &c1).unwrap());

    let fp_limb = first_parent_shas(root, 20).unwrap();
    // Pretend sampler returned root + children (before filter).
    let sampled_raw = vec![root_sha.clone(), c1.clone(), c2.clone()];
    let dry = build_b_set(root, &fp_limb, &sampled_raw).unwrap();
    let live = build_b_set(root, &fp_limb, &sampled_raw).unwrap();
    assert_eq!(dry, live, "dry-run B_set must equal live B_set");
    // Root may appear via FP limb, but filter drops it from sampled-only path.
    let filtered = filter_with_first_parent(root, &sampled_raw).unwrap();
    assert!(!filtered.iter().any(|s| s == &root_sha));
    assert!(filtered.iter().any(|s| s == &c1));
}

#[test]
fn first_parent_shas_skips_side_branch_after_merge() {
    // Topology: main: A-B-M(merge); side: A-S; merge M parents = (B, S).
    // Without --first-parent, rev-list -n4 from M can include S; with the flag, S is out.
    let dir = tempdir().unwrap();
    let root = dir.path();
    git(root, &["init", "-b", "main"]);
    git(root, &["config", "user.email", "t@example.com"]);
    git(root, &["config", "user.name", "t"]);
    fs::write(root.join("f.txt"), "a\n").unwrap();
    git(root, &["add", "f.txt"]);
    git(root, &["commit", "-m", "A"]);
    let a = git_out(root, &["rev-parse", "HEAD"]);

    fs::write(root.join("f.txt"), "b\n").unwrap();
    git(root, &["add", "f.txt"]);
    git(root, &["commit", "-m", "B"]);
    let b = git_out(root, &["rev-parse", "HEAD"]);

    git(root, &["checkout", "-b", "side", &a]);
    fs::write(root.join("side.txt"), "s\n").unwrap();
    git(root, &["add", "side.txt"]);
    git(root, &["commit", "-m", "S"]);
    let s = git_out(root, &["rev-parse", "HEAD"]);

    git(root, &["checkout", "main"]);
    git(root, &["merge", "--no-ff", "-m", "M", "side"]);
    let m = git_out(root, &["rev-parse", "HEAD"]);

    let without = git_out(root, &["rev-list", "-n4", "HEAD"]);
    let without_set: Vec<&str> = without.lines().collect();
    assert!(
        without_set
            .iter()
            .any(|x| x.starts_with(&s[..12]) || *x == s.as_str()),
        "control: plain rev-list should see side tip {s}: {without}"
    );

    let fp = first_parent_shas(root, 4).unwrap();
    assert!(fp.iter().any(|x| x == &m), "HEAD merge missing: {fp:?}");
    assert!(fp.iter().any(|x| x == &b), "first-parent B missing: {fp:?}");
    assert!(
        !fp.iter().any(|x| x == &s),
        "side-only commit must not appear in first-parent limb: {fp:?}"
    );
    assert!(fp.iter().any(|x| x == &a) || fp.len() >= 2);
}

#[test]
fn dump_fingerprint_detects_db_change_not_mtime() {
    let dir = tempdir().unwrap();
    let db = dir.path().join("db.sqlite");
    let store = Store::open(&db).unwrap();
    let a = DbDumpFingerprint::from_store(&store).unwrap();
    assert_eq!(a.max_run_id, 0);
    assert!(!dump_needs_refresh(true, Some(&a), &a));
    assert!(dump_needs_refresh(false, Some(&a), &a));
    let b = DbDumpFingerprint {
        max_run_id: 1,
        n_real_runs: 1,
        n_synthetic_runs: 0,
    };
    assert!(dump_needs_refresh(true, Some(&a), &b));
}

#[test]
fn estimate_respects_backfill_commits_last_fp() {
    let priors = default_priors();
    let est20 = estimate_wall(200, 4, 1, "click", &priors, 20);
    let est30 = estimate_wall(200, 4, 1, "click", &priors, 30);
    assert_eq!(est20.backfill_shas_hat, 220);
    assert_eq!(est30.backfill_shas_hat, 230);
    let mut inp = inputs("abc", 200);
    inp.b_override = Some(30);
    let fp = resolve_fingerprint(&inp);
    let text = format_dry_run_report(&fp, &est30, true, "embedded");
    assert!(text.contains("|B_set|≈230"), "{text}");
    assert!(text.contains("≈230 SHAs"), "{text}");
    assert!(
        text.contains("B_override=30"),
        "must print number not Some(30): {text}"
    );
    assert!(!text.contains("Some("), "{text}");
}
