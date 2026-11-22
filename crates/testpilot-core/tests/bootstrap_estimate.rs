use testpilot_core::bootstrap::{BootstrapPriors, default_priors, estimate_wall};

fn near(actual: f64, expected: f64) {
    assert!(
        (actual - expected).abs() < 0.02,
        "expected {expected}, got {actual}"
    );
}

#[test]
fn calibrated_steps_match_click_and_jinja_week6_reconstruction() {
    let priors = default_priors();
    assert!(!priors.features_dump_s_per_eligible.is_measured("attrs"));
    assert!(!priors.train_s.is_measured("attrs"));
    let click = estimate_wall(200, 4, 2, "click", &priors, 200);
    near(click.total_s, 1608.31);
    near(click.features_dump_s, 119.70);
    near(click.eligible_hat, 92.96);
    near(click.train_s, 8.55);

    let jinja = estimate_wall(200, 4, 2, "jinja", &priors, 200);
    near(jinja.total_s, 2776.40);
    near(jinja.features_dump_s, 729.80);
    near(jinja.eligible_hat, 141.89);
    near(jinja.train_s, 7.08);
}

#[test]
fn backfill_count_changes_only_backfill_component() {
    let priors = default_priors();
    let mut estimate = estimate_wall(200, 4, 2, "click", &priors, 200);
    let original_mutate = estimate.mutate_s;
    estimate.remaining(&priors, 30, 5, 0);
    near(estimate.backfill_s, 149.86);
    near(estimate.mutate_s, original_mutate);
    assert_eq!(estimate.backfill_shas_hat, 30);
    assert_eq!(estimate.backfill_reused, 5);
}

#[test]
fn k_and_completed_attempts_scale_attempted_and_eligible_hats() {
    let priors = default_priors();
    let k4 = estimate_wall(200, 4, 2, "click", &priors, 200);
    let k2 = estimate_wall(200, 2, 2, "click", &priors, 200);
    near(k2.attempted_hat, k4.attempted_hat / 2.0);
    near(k2.eligible_hat, k4.eligible_hat / 2.0);

    let mut resumed = k4;
    resumed.remaining(&priors, 200, 0, 20);
    near(resumed.attempted_hat, 100.0);
    near(resumed.eligible_hat, 100.0 * 110.0 / 142.0);
}

#[test]
fn zero_eligible_ratio_produces_zero_dump_time_without_nan() {
    let mut priors = default_priors();
    priors.eligible_per_attempted.insert("click".into(), 0.0);
    let estimate = estimate_wall(200, 4, 2, "click", &priors, 200);
    assert_eq!(estimate.eligible_hat, 0.0);
    assert_eq!(estimate.features_dump_s, 0.0);
    assert!(estimate.total_s.is_finite());
}

#[test]
fn legacy_dump_train_prior_remains_loadable() {
    let raw = r#"{
      "mutate_s_per_mutant":{"click":3.0,"jinja":4.0},
      "backfill_s_per_commit":{"click":2.0,"jinja":2.0},
      "collect_git_s":{"click":1.0,"jinja":1.0},
      "dump_train_s":{"click":7.0,"jinja":8.0},
      "attempted_per_commit":{"click":0.5,"jinja":1.0}
    }"#;
    let priors: BootstrapPriors = serde_json::from_str(raw).expect("legacy priors parse");
    near(priors.train_s.click, 7.0);
    assert_eq!(priors.features_dump_s_per_eligible.click, 0.0);
    assert!(priors.eligible_per_attempted.is_empty());
}
