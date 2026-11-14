use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};

use anyhow::{Context, Result, bail};
use clap::{Parser, Subcommand};
use testpilot_core::bootstrap::{
    self, BootstrapInputs, DbDumpFingerprint, EMBEDDED_PRIORS_PATH, ResumeDecision, StepStatus,
    decide_resume, default_priors, dump_needs_refresh, estimate_wall, first_parent_shas,
    format_dry_run_report, load_priors_file, resolve_fingerprint, save_state,
    shas_needing_backfill, startup_cleanup, state_path, union_b_set,
};
use testpilot_core::codec;
use testpilot_core::collect_junit::{CollectJunitOpts, collect_junit, resolve_sha};
use testpilot_core::features::{self, FEATURES_VERSION};
use testpilot_core::git::{GitRepo, GixRepo, collect_git, collect_git_shas};
use testpilot_core::lgbm::load_model_dir;
use testpilot_core::select::{self, Budget, SafeOpts, format_lines, format_table};
use testpilot_core::stats;
use testpilot_core::store::{SCHEMA_VERSION, Store};

const VERSION: &str = concat!(
    env!("CARGO_PKG_VERSION"),
    " (",
    env!("TESTPILOT_GIT_SHA"),
    ")"
);

#[derive(Parser, Debug)]
#[command(
    name = "testpilot",
    version = VERSION,
    about = "Offline predictive test selection — data collection"
)]
struct Cli {
    /// Path to SQLite database (default: <git-toplevel>/.testpilot/db.sqlite)
    #[arg(long, global = true)]
    db: Option<PathBuf>,

    #[arg(short, long, action = clap::ArgAction::Count, global = true)]
    verbose: u8,

    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand, Debug)]
enum Commands {
    /// Create .testpilot/ database and .git/info/exclude entry
    Init,
    /// Cold-start: backfill + mutate + train + quality gate (Week 6)
    Bootstrap {
        /// Git repository root (default: cwd toplevel)
        #[arg(long)]
        repo: Option<PathBuf>,
        /// Model + reports directory (default: <repo>/.testpilot/model)
        #[arg(long)]
        out: Option<PathBuf>,
        /// Mutate sample size of C
        #[arg(long = "max-commits", default_value_t = 200)]
        max_commits: u32,
        /// Mutants per sampled commit
        #[arg(long = "mutants-per-commit", default_value_t = 4)]
        mutants_per_commit: u32,
        /// Override last-N first-parent limb of backfill set (still unions sampled C)
        #[arg(long = "backfill-commits")]
        backfill_commits: Option<u32>,
        /// Soft wall-clock budget for the whole bootstrap (seconds)
        #[arg(long = "time-budget")]
        time_budget: Option<u64>,
        /// Mutant pytest concurrency (default 1; max 4)
        #[arg(long, default_value_t = 1)]
        jobs: u32,
        #[arg(long, default_value_t = 42)]
        seed: u64,
        /// Continue from state.json when fingerprint matches (default if state exists)
        #[arg(long, default_value_t = true)]
        resume: bool,
        #[arg(long = "no-resume", default_value_t = false, action = clap::ArgAction::SetTrue)]
        no_resume: bool,
        /// Restart over fingerprint mismatch / dirty out
        #[arg(long, default_value_t = false, action = clap::ArgAction::SetTrue)]
        force: bool,
        /// Print plan + estimates; write nothing
        #[arg(long = "dry-run", default_value_t = false, action = clap::ArgAction::SetTrue)]
        dry_run: bool,
        /// Suite key for time priors (click|jinja)
        #[arg(long, default_value = "click")]
        suite: String,
        #[arg(long = "train-threads", default_value_t = 1)]
        train_threads: u32,
    },
    #[command(subcommand)]
    Collect(CollectCmd),
    /// Feature vectors for (change, test) pairs
    #[command(subcommand)]
    Features(FeaturesCmd),
    /// Print database summary
    Stats {
        #[arg(long)]
        json: bool,
    },
    /// Rank and budget tests for a change using a trained model
    Select {
        #[arg(long)]
        base: String,
        #[arg(long, conflicts_with = "worktree")]
        head: Option<String>,
        /// Diff base against the worktree (dirty)
        #[arg(long)]
        worktree: bool,
        #[arg(long)]
        model: PathBuf,
        #[arg(long, default_value = "lines")]
        format: String,
        #[arg(long)]
        repo: Option<PathBuf>,
        #[arg(long, group = "budget")]
        budget_frac: Option<f64>,
        #[arg(long = "max-tests", group = "budget")]
        max_tests: Option<u32>,
        #[arg(
            long,
            group = "budget",
            help = "Seconds of estimated test duration for ranked picks only (force + safe-added are not charged)"
        )]
        time_budget: Option<f64>,
        #[arg(long = "no-safe", default_value_t = false, action = clap::ArgAction::SetTrue)]
        no_safe: bool,
        #[arg(long = "safe-recent-k", default_value_t = 5)]
        safe_recent_k: usize,
        #[arg(long = "safe-include-covered", default_value_t = false)]
        safe_include_covered: bool,
        #[arg(long = "safe-src-frac-threshold", default_value_t = 0.30)]
        safe_src_frac_threshold: f64,
        #[arg(long = "safe-min-candidates", default_value_t = 50)]
        safe_min_candidates: usize,
        #[arg(long = "safe-non-py-source", default_value_t = true)]
        safe_non_py_source: bool,
        #[arg(
            long = "no-safe-non-py-source",
            default_value_t = false,
            action = clap::ArgAction::SetTrue
        )]
        no_safe_non_py_source: bool,
        #[arg(long = "safe-non-py-allowlist", value_delimiter = ',')]
        safe_non_py_allowlist: Vec<String>,
    },
    #[command(subcommand)]
    Debug(DebugCmd),
}

#[derive(Subcommand, Debug)]
enum FeaturesCmd {
    /// Dump training pairs over history to Parquet
    Dump {
        #[arg(long)]
        out: PathBuf,
        /// Git repository root (default: cwd)
        #[arg(long)]
        repo: Option<PathBuf>,
        /// Also emit mutant leaf rows from refs/testpilot/mutants/**
        #[arg(long, default_value_t = false)]
        include_mutants: bool,
    },
    /// Features for one change (input to week-4 select).
    /// Test list = last clean run at/before --base. Future: `--tests-from FILE`
    /// (`pytest --collect-only -q` output) — week 4, not implemented yet.
    Show {
        #[arg(long)]
        base: String,
        #[arg(long, conflicts_with = "worktree")]
        head: Option<String>,
        /// Diff base against the worktree (dirty)
        #[arg(long)]
        worktree: bool,
        #[arg(long, default_value = "table")]
        format: String,
        #[arg(long)]
        out: Option<PathBuf>,
        /// Git repository root (default: cwd)
        #[arg(long)]
        repo: Option<PathBuf>,
    },
    /// Print or verify the feature column lockfile
    Schema {
        #[arg(long)]
        check: Option<PathBuf>,
    },
}

#[derive(Subcommand, Debug)]
enum CollectCmd {
    /// Walk commits reachable from HEAD (not --all) into the database
    Git {
        #[arg(long)]
        since_sha: Option<String>,
        #[arg(long)]
        since_date: Option<String>,
        #[arg(long)]
        max: Option<usize>,
        #[arg(long, default_value_t = 5000)]
        max_files_per_commit: usize,
        /// Index a single commit SHA (for mutant leaves off HEAD). Repeatable.
        #[arg(long = "sha")]
        shas: Vec<String>,
    },
    /// Ingest JUnit XML files
    Junit {
        glob: String,
        #[arg(long)]
        sha: String,
        #[arg(long)]
        started_at: Option<String>,
        #[arg(long)]
        meta: Option<String>,
        #[arg(long)]
        force: bool,
        /// Mark run as synthetic (mutant bootstrap); excluded from real history paths
        #[arg(long, default_value_t = false)]
        synthetic: bool,
    },
}

#[derive(Subcommand, Debug)]
enum DebugCmd {
    /// Decode coverage blobs
    Coverage {
        #[arg(long)]
        run_id: Option<i64>,
        #[arg(long)]
        sha: Option<String>,
        #[arg(long)]
        test_id: String,
        #[arg(long)]
        path: Option<String>,
    },
}

fn main() {
    if let Err(e) = run() {
        eprintln!("error: {e:#}");
        std::process::exit(1);
    }
}

fn run() -> Result<()> {
    let cli = Cli::parse();
    init_tracing(cli.verbose);

    match cli.command {
        Commands::Init => cmd_init(cli.db.as_deref())?,
        Commands::Bootstrap {
            repo,
            out,
            max_commits,
            mutants_per_commit,
            backfill_commits,
            time_budget,
            jobs,
            seed,
            resume,
            no_resume,
            force,
            dry_run,
            suite,
            train_threads,
        } => cmd_bootstrap(BootstrapCli {
            db: cli.db.clone(),
            repo,
            out,
            max_commits,
            mutants_per_commit,
            backfill_commits,
            time_budget,
            jobs,
            seed,
            resume: resume && !no_resume,
            force,
            dry_run,
            suite,
            train_threads,
        })?,
        Commands::Collect(CollectCmd::Git {
            since_sha,
            since_date,
            max,
            max_files_per_commit,
            shas,
        }) => {
            let db = resolve_db_path(cli.db.as_deref())?;
            let mut store = Store::open(&db).context("open db")?;
            let repo = GixRepo::open(Path::new(".")).context("open git repo")?;
            let stats = if !shas.is_empty() {
                let refs: Vec<&str> = shas.iter().map(|s| s.as_str()).collect();
                collect_git_shas(&repo, &mut store, &refs, max_files_per_commit)?
            } else {
                let since_ts = since_date.as_deref().map(parse_date).transpose()?;
                collect_git(
                    &repo,
                    &mut store,
                    since_sha.as_deref(),
                    since_ts,
                    max,
                    max_files_per_commit,
                )?
            };
            println!(
                "collected {} new commits (skipped {}, oversized {})",
                stats.inserted, stats.skipped, stats.oversized
            );
        }
        Commands::Collect(CollectCmd::Junit {
            glob,
            sha,
            started_at,
            meta,
            force,
            synthetic,
        }) => {
            let db = resolve_db_path(cli.db.as_deref())?;
            let mut store = Store::open(&db).context("open db")?;
            let started = started_at.as_deref().map(parse_started_at).transpose()?;
            let st = collect_junit(
                &mut store,
                CollectJunitOpts {
                    glob: &glob,
                    sha: &sha,
                    started_at: started,
                    meta,
                    force,
                    cwd: Path::new("."),
                    synthetic,
                },
            )?;
            if st.skipped_dedup {
                println!("skipped duplicate junit ingest ({} files)", st.files);
            } else {
                println!(
                    "ingested {} tests from {} files (run_id={:?})",
                    st.tests, st.files, st.run_id
                );
            }
        }
        Commands::Features(FeaturesCmd::Dump {
            out,
            repo,
            include_mutants,
        }) => {
            let repo_path = resolve_repo_path(repo.as_deref())?;
            let db = resolve_db_path_for(cli.db.as_deref(), &repo_path)?;
            let store = Store::open(&db).context("open db")?;
            let grepo = GixRepo::open(&repo_path).context("open git repo")?;
            let repo_name = repo_path
                .file_name()
                .map(|s| s.to_string_lossy().into_owned())
                .unwrap_or_else(|| ".".into());
            let mutant = if include_mutants { Some(&grepo) } else { None };
            let n = features::dump_parquet_ex(&grepo, &store, &repo_name, &out, mutant)
                .context("features dump")?;
            println!(
                "wrote {n} rows (features_version={FEATURES_VERSION}) to {}",
                out.display()
            );
        }
        Commands::Features(FeaturesCmd::Show {
            base,
            head,
            worktree,
            format,
            out,
            repo,
        }) => {
            let repo_path = resolve_repo_path(repo.as_deref())?;
            let db = resolve_db_path_for(cli.db.as_deref(), &repo_path)?;
            let store = Store::open(&db).context("open db")?;
            let grepo = GixRepo::open(&repo_path).context("open git repo")?;
            let base_sha = grepo.rev_parse(&base).context("resolve --base")?;
            let (head_sha, hunks) = if worktree {
                let head_sha = grepo.rev_parse("HEAD").context("resolve HEAD")?;
                let hunks = grepo.diff_worktree(&base_sha).context("worktree diff")?;
                (head_sha, hunks)
            } else {
                let head = head.context("provide --head <rev> or --worktree")?;
                let head_sha = grepo.rev_parse(&head).context("resolve --head")?;
                let hunks = grepo
                    .diff_hunks(&base_sha, &head_sha)
                    .context("diff hunks")?;
                (head_sha, hunks)
            };
            let repo_name = repo_path
                .file_name()
                .map(|s| s.to_string_lossy().into_owned())
                .unwrap_or_else(|| ".".into());
            let rows = features::compute_show_rows(
                &grepo, &store, &repo_name, &base_sha, &head_sha, &hunks,
            )
            .context("features show")?;
            let text = features::show_format(&rows, &format, out.as_deref())?;
            println!("{text}");
        }
        Commands::Features(FeaturesCmd::Schema { check }) => {
            let json = features::schema_json();
            if let Some(path) = check {
                let existing = std::fs::read_to_string(&path)
                    .with_context(|| format!("read {}", path.display()))?;
                let expected: serde_json::Value = serde_json::from_str(&json)?;
                let found: serde_json::Value = serde_json::from_str(&existing)
                    .with_context(|| format!("parse {}", path.display()))?;
                if expected != found {
                    bail!(
                        "features schema mismatch for {} (FEATURES_VERSION={FEATURES_VERSION})",
                        path.display()
                    );
                }
                println!(
                    "ok: {} matches FEATURES_VERSION={FEATURES_VERSION}",
                    path.display()
                );
            } else {
                println!("{json}");
            }
        }
        Commands::Stats { json } => {
            let db = resolve_db_path(cli.db.as_deref())?;
            let store = Store::open(&db).context("open db")?;
            let s = stats::compute(&store)?;
            if json {
                println!("{}", serde_json::to_string_pretty(&s)?);
            } else {
                print!("{}", stats::format_human(&s));
            }
        }
        Commands::Select {
            base,
            head,
            worktree,
            model,
            format,
            repo,
            budget_frac,
            max_tests,
            time_budget,
            no_safe,
            safe_recent_k,
            safe_include_covered,
            safe_src_frac_threshold,
            safe_min_candidates,
            safe_non_py_source,
            no_safe_non_py_source,
            safe_non_py_allowlist,
        } => {
            let budget = parse_select_budget(budget_frac, max_tests, time_budget)?;
            let safe_opts = SafeOpts {
                enabled: !no_safe,
                recent_k: safe_recent_k,
                include_covered: safe_include_covered,
                src_frac_threshold: safe_src_frac_threshold,
                min_candidates: safe_min_candidates,
                non_py_source: safe_non_py_source && !no_safe_non_py_source,
                non_py_allowlist: safe_non_py_allowlist,
            };
            let repo_path = resolve_repo_path(repo.as_deref())?;
            let db = resolve_db_path_for(cli.db.as_deref(), &repo_path)?;
            let store = Store::open(&db).context("open db")?;
            let grepo = GixRepo::open(&repo_path).context("open git repo")?;
            let base_sha = grepo.rev_parse(&base).context("resolve --base")?;
            let worktree_mode = worktree || head.as_deref() == Some("WORKTREE");
            let (head_sha, head_label, hunks) = if worktree_mode {
                let head_sha = grepo.rev_parse("HEAD").context("resolve HEAD")?;
                let hunks = grepo.diff_worktree(&base_sha).context("worktree diff")?;
                (head_sha, "WORKTREE".to_string(), hunks)
            } else {
                let head_rev = head.context("provide --head <rev> or --worktree")?;
                let head_sha = grepo.rev_parse(&head_rev).context("resolve --head")?;
                let hunks = grepo
                    .diff_hunks(&base_sha, &head_sha)
                    .context("diff hunks")?;
                let label = head_sha.as_str().to_string();
                (head_sha, label, hunks)
            };
            let repo_name = repo_path
                .file_name()
                .map(|s| s.to_string_lossy().into_owned())
                .unwrap_or_else(|| ".".into());
            let booster = load_model_dir(&model).context("load model")?;
            let out = select::run_select(
                &grepo,
                Some(&grepo),
                &store,
                &repo_name,
                &booster,
                &base_sha,
                &head_sha,
                &head_label,
                &hunks,
                worktree_mode,
                &budget,
                &safe_opts,
            )
            .context("select")?;
            for w in &out.stderr_warnings {
                eprintln!("{w}");
            }
            let text = match format.as_str() {
                "json" => serde_json::to_string_pretty(&out.payload)?,
                "table" => format_table(&out.payload.tests),
                "lines" => format_lines(&out.payload.tests),
                other => bail!("unknown --format {other:?}; use json|table|lines"),
            };
            println!("{text}");
        }
        Commands::Debug(DebugCmd::Coverage {
            run_id,
            sha,
            test_id,
            path,
        }) => {
            let db = resolve_db_path(cli.db.as_deref())?;
            let store = Store::open(&db).context("open db")?;
            let rid = match (run_id, sha) {
                (Some(id), _) => id,
                (None, Some(rev)) => {
                    let resolved = resolve_sha(Path::new("."), &rev)?;
                    store
                        .latest_pytest_run_with_coverage_for_sha(&resolved)?
                        .with_context(|| {
                            format!(
                                "no pytest run with coverage for sha {resolved}; \
                                 pass --run-id to inspect a specific run"
                            )
                        })?
                }
                (None, None) => bail!("provide --run-id or --sha"),
            };
            debug_coverage(&store, rid, &test_id, path.as_deref())?;
        }
    }
    Ok(())
}

fn parse_select_budget(
    frac: Option<f64>,
    max_tests: Option<u32>,
    time_budget: Option<f64>,
) -> Result<Budget> {
    let n = [frac.is_some(), max_tests.is_some(), time_budget.is_some()]
        .into_iter()
        .filter(|x| *x)
        .count();
    if n != 1 {
        bail!("exactly one of --budget-frac, --max-tests, or --time-budget is required");
    }
    if let Some(f) = frac {
        if !(f > 0.0 && f <= 1.0) {
            bail!("--budget-frac must be in (0, 1], got {f}");
        }
        return Ok(Budget::Frac(f));
    }
    if let Some(n) = max_tests {
        if n == 0 {
            bail!("--max-tests must be ≥ 1");
        }
        return Ok(Budget::Count(n));
    }
    if let Some(s) = time_budget {
        if s <= 0.0 {
            bail!("--time-budget must be > 0");
        }
        return Ok(Budget::Time(s));
    }
    unreachable!()
}

fn resolve_db_path(explicit: Option<&Path>) -> Result<PathBuf> {
    resolve_db_path_for(explicit, Path::new("."))
}

fn resolve_repo_path(explicit: Option<&Path>) -> Result<PathBuf> {
    if let Some(p) = explicit {
        return Ok(p.to_path_buf());
    }
    if let Ok(root) = git_toplevel() {
        return Ok(root);
    }
    Ok(PathBuf::from("."))
}

fn resolve_db_path_for(explicit: Option<&Path>, repo: &Path) -> Result<PathBuf> {
    if let Some(p) = explicit {
        return Ok(p.to_path_buf());
    }
    // Prefer .testpilot under the given repo path.
    let under_repo = repo.join(".testpilot/db.sqlite");
    if under_repo.exists() || explicit.is_none() {
        // If repo is a git root, prefer its .testpilot even if not yet created.
        if repo.join(".git").exists() || repo.join(".git").is_file() {
            return Ok(under_repo);
        }
    }
    if let Ok(root) = git_toplevel_at(repo) {
        return Ok(root.join(".testpilot/db.sqlite"));
    }
    if let Ok(root) = git_toplevel() {
        return Ok(root.join(".testpilot/db.sqlite"));
    }
    Ok(under_repo)
}

fn git_toplevel_at(cwd: &Path) -> Result<PathBuf> {
    let out = Command::new("git")
        .args(["rev-parse", "--show-toplevel"])
        .current_dir(cwd)
        .output()
        .context("git rev-parse --show-toplevel")?;
    if !out.status.success() {
        bail!(
            "not a git repository: {}",
            String::from_utf8_lossy(&out.stderr).trim()
        );
    }
    Ok(PathBuf::from(String::from_utf8_lossy(&out.stdout).trim()))
}

fn git_toplevel() -> Result<PathBuf> {
    let out = Command::new("git")
        .args(["rev-parse", "--show-toplevel"])
        .output()
        .context("git rev-parse --show-toplevel")?;
    if !out.status.success() {
        bail!(
            "not a git repository: {}",
            String::from_utf8_lossy(&out.stderr).trim()
        );
    }
    Ok(PathBuf::from(String::from_utf8_lossy(&out.stdout).trim()))
}

fn git_exclude_path() -> Result<PathBuf> {
    let out = Command::new("git")
        .args(["rev-parse", "--git-path", "info/exclude"])
        .output()
        .context("git rev-parse --git-path info/exclude")?;
    if !out.status.success() {
        bail!(
            "cannot resolve git exclude path: {}",
            String::from_utf8_lossy(&out.stderr).trim()
        );
    }
    let rel = String::from_utf8_lossy(&out.stdout).trim().to_string();
    let path = PathBuf::from(&rel);
    if path.is_absolute() {
        Ok(path)
    } else {
        Ok(std::env::current_dir()?.join(path))
    }
}

struct BootstrapCli {
    db: Option<PathBuf>,
    repo: Option<PathBuf>,
    out: Option<PathBuf>,
    max_commits: u32,
    mutants_per_commit: u32,
    backfill_commits: Option<u32>,
    time_budget: Option<u64>,
    jobs: u32,
    seed: u64,
    resume: bool,
    force: bool,
    dry_run: bool,
    suite: String,
    train_threads: u32,
}

fn cmd_bootstrap(opts: BootstrapCli) -> Result<()> {
    if opts.jobs > 4 {
        bail!("--jobs max is 4 (got {})", opts.jobs);
    }
    if opts.train_threads > 1 {
        eprintln!(
            "warning: --train-threads={} may make model_sha256 non-deterministic (level-a hash)",
            opts.train_threads
        );
    }

    let repo = match opts.repo {
        Some(p) => {
            let abs = if p.is_absolute() {
                p
            } else {
                std::env::current_dir()?.join(p)
            };
            git_toplevel_at(&abs).with_context(|| format!("git toplevel at {}", abs.display()))?
        }
        None => git_toplevel().context("testpilot bootstrap requires a git repository")?,
    };

    let head = {
        let out = Command::new("git")
            .args(["-C"])
            .arg(&repo)
            .args(["rev-parse", "HEAD"])
            .output()
            .context("git rev-parse HEAD")?;
        if !out.status.success() {
            bail!("git rev-parse HEAD failed");
        }
        String::from_utf8_lossy(&out.stdout).trim().to_string()
    };

    let inputs = BootstrapInputs {
        head_sha: head,
        n: opts.max_commits,
        k: opts.mutants_per_commit,
        seed: opts.seed,
        b_override: opts.backfill_commits,
        bin_version: VERSION.to_string(),
        schema_version: SCHEMA_VERSION,
    };
    let fp = resolve_fingerprint(&inputs);

    let local_priors = repo.join(".testpilot/bootstrap/priors.json");
    let (priors, priors_source) = if local_priors.is_file() {
        (
            load_priors_file(&local_priors).context("load local priors.json")?,
            local_priors.display().to_string(),
        )
    } else {
        (
            default_priors(),
            format!("{EMBEDDED_PRIORS_PATH} (embedded)"),
        )
    };
    let last_fp = opts.backfill_commits.unwrap_or(20);
    // Hat uses requested last_fp; git may have fewer commits available.
    let last_fp_available = first_parent_shas(&repo, last_fp)
        .map(|v| v.len() as u32)
        .unwrap_or(last_fp);
    let est = estimate_wall(
        opts.max_commits,
        opts.mutants_per_commit,
        opts.jobs,
        &opts.suite,
        &priors,
        last_fp,
    );

    if opts.dry_run {
        let resume_allowed = !matches!(
            decide_resume(&repo, &fp, false)?,
            ResumeDecision::Refuse { .. }
        );
        print!(
            "{}",
            format_dry_run_report(&fp, &est, resume_allowed, &priors_source)
        );
        println!(
            "last_fp_available={last_fp_available} requested={last_fp} (|B_set| after collect = sampled_C ∪ first-parent limb)"
        );
        if let Some(tb) = opts.time_budget {
            println!("time-budget flag: {tb}s (not applied in dry-run)");
        }
        if let Some(ref out) = opts.out {
            println!("out: {}", out.display());
        }
        // Explicit: no writes / no cleanup under --dry-run.
        return Ok(());
    }

    let interrupted = Arc::new(AtomicBool::new(false));
    {
        let flag = Arc::clone(&interrupted);
        let _ = ctrlc::set_handler(move || {
            flag.store(true, Ordering::SeqCst);
        });
    }

    let _cleanup = startup_cleanup(&repo).context("bootstrap startup cleanup")?;

    let decision = if opts.resume {
        decide_resume(&repo, &fp, opts.force)?
    } else if opts.force || !state_path(&repo).exists() {
        ResumeDecision::Fresh(bootstrap::load_or_init_state(&repo, fp.clone(), true)?)
    } else {
        bail!("state.json exists; pass --resume (default) or --force / --no-resume with --force");
    };

    let mut state = match decision {
        ResumeDecision::Refuse { hint } => bail!("{hint}"),
        ResumeDecision::Fresh(s) | ResumeDecision::Continue(s) => s,
    };
    state.steps.insert("cleanup".into(), StepStatus::Done);

    // Ensure exclude + DB (never touches .gitignore).
    let db = resolve_db_path_for(opts.db.as_deref(), &repo)?;
    if let Some(parent) = db.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let exclude = {
        let out = Command::new("git")
            .args(["-C"])
            .arg(&repo)
            .args(["rev-parse", "--git-path", "info/exclude"])
            .output()
            .context("git exclude path")?;
        if !out.status.success() {
            bail!("cannot resolve info/exclude");
        }
        let rel = String::from_utf8_lossy(&out.stdout).trim().to_string();
        let path = PathBuf::from(&rel);
        if path.is_absolute() {
            path
        } else {
            repo.join(path)
        }
    };
    if let Some(parent) = exclude.parent() {
        std::fs::create_dir_all(parent)?;
    }
    ensure_git_exclude_at(&exclude, ".testpilot/")?;
    let mut store = Store::open(&db).context("open/create bootstrap db")?;
    state.steps.insert("init".into(), StepStatus::Done);
    save_state(&repo, &state)?;

    let check_intr = |state: &mut bootstrap::BootstrapState, step: &str| -> Result<()> {
        if interrupted.load(Ordering::SeqCst) {
            state.status = Some("interrupted".into());
            state.steps.insert(step.into(), StepStatus::Interrupted);
            save_state(&repo, state)?;
            bail!("bootstrap interrupted (Ctrl-C); state.status=interrupted");
        }
        Ok(())
    };

    // --- collect_git ---
    check_intr(&mut state, "collect_git")?;
    if state.steps.get("collect_git") != Some(&StepStatus::Done) {
        let grepo = GixRepo::open(&repo).context("open git repo for collect")?;
        let stats =
            collect_git(&grepo, &mut store, None, None, None, 5000).context("collect git")?;
        println!(
            "collect_git: inserted={} skipped={} oversized={}",
            stats.inserted, stats.skipped, stats.oversized
        );
        state.steps.insert("collect_git".into(), StepStatus::Done);
        save_state(&repo, &state)?;
    }

    let has_py = repo.join("pyproject.toml").is_file() || repo.join("setup.py").is_file();
    let out_dir = opts
        .out
        .clone()
        .unwrap_or_else(|| repo.join(".testpilot/model"));
    let parquet = out_dir.join("pairs.parquet");
    let scripts = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../scripts");

    if !has_py {
        println!(
            "no pyproject.toml/setup.py in {}; skipping backfill/mutate/dump (init+collect only)",
            repo.display()
        );
        for step in [
            "backfill",
            "mutate",
            "features_dump",
            "train",
            "gate",
            "finalize",
        ] {
            state
                .steps
                .entry(step.into())
                .and_modify(|s| {
                    if *s == StepStatus::Pending {
                        *s = StepStatus::Skipped;
                    }
                })
                .or_insert(StepStatus::Skipped);
        }
        save_state(&repo, &state)?;
        println!("bootstrap state ready at {}", state_path(&repo).display());
        let _ = opts.time_budget;
        return Ok(());
    }

    // --- backfill (R6): last_fp first, then sampled C gaps ---
    check_intr(&mut state, "backfill")?;
    if state.steps.get("backfill") != Some(&StepStatus::Done) {
        let fp_limb = first_parent_shas(&repo, last_fp)?;
        // Seed limb so mutate sampling has clean runs.
        run_backfill_shas(&scripts, &repo, &db, &fp_limb, None)?;
        state.backfill_done_shas.extend(fp_limb.iter().cloned());

        let sample = sample_commits_py(&scripts, &db, opts.max_commits, opts.seed)?;
        state.n_eligible = Some(sample.n_eligible);
        state.n_actual = Some(sample.sampled.len() as u32);
        println!(
            "sample: n_eligible={} n_actual={}",
            sample.n_eligible,
            sample.sampled.len()
        );
        let b_set = union_b_set(&fp_limb, &sample.sampled);
        let need = shas_needing_backfill(&store, &b_set)?;
        if !need.is_empty() {
            run_backfill_shas(&scripts, &repo, &db, &need, None)?;
        }
        for s in &b_set {
            if !state.backfill_done_shas.iter().any(|x| x == s) {
                state.backfill_done_shas.push(s.clone());
            }
        }
        state.steps.insert("backfill".into(), StepStatus::Done);
        save_state(&repo, &state)?;
    }

    // --- mutate ---
    check_intr(&mut state, "mutate")?;
    if state.steps.get("mutate") != Some(&StepStatus::Done) {
        let n = state.n_actual.unwrap_or(opts.max_commits).max(1);
        run_mutate(
            &scripts,
            &repo,
            &db,
            n,
            opts.mutants_per_commit,
            opts.seed,
            opts.jobs,
            None,
        )?;
        state.steps.insert("mutate".into(), StepStatus::Done);
        save_state(&repo, &state)?;
    }

    // --- features dump (R8 fingerprint) ---
    check_intr(&mut state, "features_dump")?;
    let cur_fp = DbDumpFingerprint::from_store(&store)?;
    let need_dump = dump_needs_refresh(parquet.is_file(), state.dump_fingerprint.as_ref(), &cur_fp);
    if state.steps.get("features_dump") != Some(&StepStatus::Done) || need_dump {
        std::fs::create_dir_all(&out_dir)?;
        let grepo = GixRepo::open(&repo)?;
        let repo_name = repo
            .file_name()
            .and_then(|s| s.to_str())
            .unwrap_or("repo")
            .to_string();
        features::dump_parquet_ex(&grepo, &store, &repo_name, &parquet, Some(&grepo))
            .context("features dump --include-mutants")?;
        state.dump_fingerprint = Some(cur_fp);
        state.steps.insert("features_dump".into(), StepStatus::Done);
        save_state(&repo, &state)?;
        println!("features_dump: {}", parquet.display());
    }

    // train/gate/finalize → T4
    for step in ["train", "gate", "finalize"] {
        if state.steps.get(step) == Some(&StepStatus::Pending) {
            println!("{step}: deferred to Week-6 T4");
        }
    }
    save_state(&repo, &state)?;
    println!(
        "bootstrap pipeline through dump ready at {}",
        state_path(&repo).display()
    );
    let _ = opts.time_budget;
    Ok(())
}

fn bundled_scripts(scripts: &Path) -> Result<PathBuf> {
    let p = scripts
        .canonicalize()
        .with_context(|| format!("scripts dir {}", scripts.display()))?;
    Ok(p)
}

struct SampleOut {
    n_eligible: u32,
    sampled: Vec<String>,
}

fn sample_commits_py(scripts: &Path, db: &Path, n: u32, seed: u64) -> Result<SampleOut> {
    let scripts = bundled_scripts(scripts)?;
    let code = format!(
        r#"
import json, sys
sys.path.insert(0, {scripts:?})
from mutate.sample_commits import sample_commits
from pathlib import Path
r = sample_commits(Path({db:?}), {n}, {seed})
print(json.dumps({{"n_eligible": r.n_eligible_C, "sampled": [e.sha for e in r.sampled]}}))
"#
    );
    let out = Command::new("python3")
        .args(["-c", &code])
        .output()
        .context("sample_commits python")?;
    if !out.status.success() {
        bail!(
            "sample_commits failed: {}",
            String::from_utf8_lossy(&out.stderr)
        );
    }
    let v: serde_json::Value =
        serde_json::from_slice(&out.stdout).context("parse sample_commits JSON")?;
    let n_eligible = v["n_eligible"].as_u64().unwrap_or(0) as u32;
    let sampled = v["sampled"]
        .as_array()
        .unwrap_or(&vec![])
        .iter()
        .filter_map(|x| x.as_str().map(|s| s.to_string()))
        .collect();
    Ok(SampleOut {
        n_eligible,
        sampled,
    })
}

fn run_backfill_shas(
    scripts: &Path,
    repo: &Path,
    db: &Path,
    shas: &[String],
    python: Option<&Path>,
) -> Result<()> {
    if shas.is_empty() {
        return Ok(());
    }
    let scripts = bundled_scripts(scripts)?;
    let backfill = scripts.join("backfill.py");
    let mut cmd = Command::new("python3");
    cmd.arg(&backfill)
        .arg("--repo")
        .arg(repo)
        .arg("--db")
        .arg(db);
    for s in shas {
        cmd.arg("--sha").arg(s);
    }
    if let Some(py) = python {
        cmd.arg("--python").arg(py);
    }
    let st = cmd.status().context("run backfill.py")?;
    if !st.success() {
        bail!("backfill.py failed with {st}");
    }
    Ok(())
}

#[allow(clippy::too_many_arguments)]
fn run_mutate(
    scripts: &Path,
    repo: &Path,
    db: &Path,
    n: u32,
    k: u32,
    seed: u64,
    _jobs: u32,
    python: Option<&Path>,
) -> Result<()> {
    let scripts = bundled_scripts(scripts)?;
    let script = scripts.join("mutate_bootstrap.py");
    let mut cmd = Command::new("python3");
    cmd.arg(&script)
        .arg("--repo")
        .arg(repo)
        .arg("--db")
        .arg(db)
        .arg("--commits")
        .arg(n.to_string())
        .arg("--mutants-per-commit")
        .arg(k.to_string())
        .arg("--seed")
        .arg(seed.to_string())
        .arg("--json");
    if let Some(py) = python {
        cmd.arg("--python").arg(py);
    }
    let st = cmd.status().context("run mutate_bootstrap.py")?;
    if !st.success() {
        bail!("mutate_bootstrap.py failed with {st}");
    }
    Ok(())
}

fn cmd_init(explicit_db: Option<&Path>) -> Result<()> {
    // N3: resolve git paths before creating the DB; never mkdir a fake .git/.
    let toplevel = git_toplevel().context("testpilot init requires a git repository")?;
    let exclude = git_exclude_path().context("resolve info/exclude")?;
    if let Some(parent) = exclude.parent() {
        // Create info/ under the real git dir (works for worktrees where .git is a file).
        std::fs::create_dir_all(parent).with_context(|| format!("create {}", parent.display()))?;
    }
    ensure_git_exclude_at(&exclude, ".testpilot/")?;

    let db = match explicit_db {
        Some(p) => p.to_path_buf(),
        None => toplevel.join(".testpilot/db.sqlite"),
    };
    if let Some(parent) = db.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let _ = Store::open(&db)?;
    println!("initialized {}", db.display());
    Ok(())
}

fn ensure_git_exclude_at(exclude: &Path, entry: &str) -> Result<()> {
    let contents = if exclude.exists() {
        std::fs::read_to_string(exclude)?
    } else {
        String::new()
    };
    if contents
        .lines()
        .any(|l| l.trim() == entry || l.trim() == entry.trim_end_matches('/'))
    {
        return Ok(());
    }
    let mut contents = contents;
    if !contents.ends_with('\n') && !contents.is_empty() {
        contents.push('\n');
    }
    contents.push_str(entry);
    if !entry.ends_with('\n') {
        contents.push('\n');
    }
    std::fs::write(exclude, contents)?;
    Ok(())
}

fn debug_coverage(
    store: &Store,
    run_id: i64,
    test_id: &str,
    path_filter: Option<&str>,
) -> Result<()> {
    let rows = store.coverage_for(run_id, test_id, path_filter)?;
    if rows.is_empty() {
        println!("(no coverage rows)");
        return Ok(());
    }
    for (path, blob) in rows {
        let lines = codec::decode_lines(&blob)?;
        println!("{path}: {lines:?}");
    }
    Ok(())
}

fn init_tracing(verbose: u8) {
    // Default: warn only; -v → info, -vv → debug, -vvv → trace
    let level = match verbose {
        0 => "warn",
        1 => "info",
        2 => "debug",
        _ => "trace",
    };
    let filter = tracing_subscriber::EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new(level));
    let _ = tracing_subscriber::fmt()
        .with_env_filter(filter)
        .with_writer(std::io::stderr)
        .try_init();
}

fn parse_date(s: &str) -> Result<i64> {
    if let Ok(n) = s.parse::<i64>() {
        return Ok(n);
    }
    let mut parts = s.split('-');
    let y: i64 = parts.next().context("bad date")?.parse()?;
    let m: i64 = parts.next().context("bad date")?.parse()?;
    let d: i64 = parts.next().context("bad date")?.parse()?;
    Ok(civil_to_unix(y, m, d))
}

fn parse_started_at(s: &str) -> Result<i64> {
    if let Ok(n) = s.parse::<i64>() {
        return Ok(n);
    }
    testpilot_core::junit::parse_timestamp(s).context("bad started-at")
}

fn civil_to_unix(y: i64, m: i64, d: i64) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = y - era * 400;
    let mp = if m > 2 { m - 3 } else { m + 9 };
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    (era * 146097 + doe - 719468) * 86400
}
