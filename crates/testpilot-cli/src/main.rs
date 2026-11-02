use std::path::{Path, PathBuf};
use std::process::Command;

use anyhow::{Context, Result, bail};
use clap::{Parser, Subcommand};
use testpilot_core::codec;
use testpilot_core::collect_junit::{CollectJunitOpts, collect_junit, resolve_sha};
use testpilot_core::git::{GixRepo, collect_git};
use testpilot_core::stats;
use testpilot_core::store::Store;

#[derive(Parser, Debug)]
#[command(
    name = "testpilot",
    version,
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
    #[command(subcommand)]
    Collect(CollectCmd),
    /// Print database summary
    Stats {
        #[arg(long)]
        json: bool,
    },
    #[command(subcommand)]
    Debug(DebugCmd),
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
        Commands::Collect(CollectCmd::Git {
            since_sha,
            since_date,
            max,
            max_files_per_commit,
        }) => {
            let db = resolve_db_path(cli.db.as_deref())?;
            let mut store = Store::open(&db).context("open db")?;
            let repo = GixRepo::open(Path::new(".")).context("open git repo")?;
            let since_ts = since_date.as_deref().map(parse_date).transpose()?;
            let stats = collect_git(
                &repo,
                &mut store,
                since_sha.as_deref(),
                since_ts,
                max,
                max_files_per_commit,
            )?;
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

fn resolve_db_path(explicit: Option<&Path>) -> Result<PathBuf> {
    if let Some(p) = explicit {
        return Ok(p.to_path_buf());
    }
    if let Ok(root) = git_toplevel() {
        return Ok(root.join(".testpilot/db.sqlite"));
    }
    Ok(PathBuf::from(".testpilot/db.sqlite"))
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
