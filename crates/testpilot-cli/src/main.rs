use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use anyhow::{Context, Result, bail};
use clap::{Parser, Subcommand};
use testpilot_core::codec;
use testpilot_core::collect_junit::{CollectJunitOpts, collect_junit};
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
    /// Path to SQLite database
    #[arg(long, global = true, default_value = ".testpilot/db.sqlite")]
    db: PathBuf,

    #[arg(short, long, action = clap::ArgAction::Count, global = true)]
    verbose: u8,

    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand, Debug)]
enum Commands {
    /// Create .testpilot/ database and gitignore entry
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
    /// Walk git history into the database
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
        Commands::Init => cmd_init(&cli.db)?,
        Commands::Collect(CollectCmd::Git {
            since_sha,
            since_date,
            max,
            max_files_per_commit,
        }) => {
            let store = Store::open(&cli.db).context("open db")?;
            let repo = GixRepo::open(Path::new(".")).context("open git repo")?;
            let since_ts = since_date.as_deref().map(parse_date).transpose()?;
            let stats = collect_git(
                &repo,
                &store,
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
            let store = Store::open(&cli.db).context("open db")?;
            let started = started_at.as_deref().map(parse_started_at).transpose()?;
            let st = collect_junit(
                &store,
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
            let store = Store::open(&cli.db).context("open db")?;
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
            let store = Store::open(&cli.db).context("open db")?;
            let rid = match (run_id, sha) {
                (Some(id), _) => id,
                (None, Some(sha)) => store
                    .latest_run_id_for_sha(&sha)?
                    .with_context(|| format!("no test_runs for sha {sha}"))?,
                (None, None) => bail!("provide --run-id or --sha"),
            };
            debug_coverage(&store, rid, &test_id, path.as_deref())?;
        }
    }
    Ok(())
}

fn cmd_init(db: &Path) -> Result<()> {
    let dir = db
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new(".testpilot"));
    std::fs::create_dir_all(dir)?;
    let _ = Store::open(db)?;
    ensure_gitignore(".gitignore")?;
    println!("initialized {}", db.display());
    Ok(())
}

fn ensure_gitignore(path: &str) -> Result<()> {
    let entry = ".testpilot/";
    if Path::new(path).exists() {
        let contents = std::fs::read_to_string(path)?;
        if contents
            .lines()
            .any(|l| l.trim() == entry || l.trim() == ".testpilot")
        {
            return Ok(());
        }
        let mut contents = contents;
        if !contents.ends_with('\n') && !contents.is_empty() {
            contents.push('\n');
        }
        contents.push_str(entry);
        contents.push('\n');
        std::fs::write(path, contents)?;
    } else {
        std::fs::write(path, format!("{entry}\n"))?;
    }
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
    let level = match verbose {
        0 => "info",
        1 => "debug",
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
    // YYYY-MM-DD → start of day UTC
    let mut parts = s.split('-');
    let y: i32 = parts.next().context("bad date")?.parse()?;
    let m: u32 = parts.next().context("bad date")?.parse()?;
    let d: u32 = parts.next().context("bad date")?.parse()?;
    // reuse junit-less: approximate via days
    Ok(civil_to_unix(y as i64, m as i64, d as i64))
}

fn parse_started_at(s: &str) -> Result<i64> {
    if let Ok(n) = s.parse::<i64>() {
        return Ok(n);
    }
    parse_date(s).or_else(|_| {
        // RFC3339 date-time without chrono: YYYY-MM-DDTHH:MM:SS
        let (date, time) = s.split_once('T').context("bad started-at")?;
        let mut d = date.split('-');
        let y: i64 = d.next().unwrap().parse()?;
        let mo: i64 = d.next().unwrap().parse()?;
        let day: i64 = d.next().unwrap().parse()?;
        let time = time.trim_end_matches('Z');
        let mut t = time.split(':');
        let h: i64 = t.next().unwrap().parse()?;
        let mi: i64 = t.next().unwrap().parse()?;
        let sec: i64 = t
            .next()
            .and_then(|x| x.parse::<f64>().ok())
            .map(|x| x as i64)
            .unwrap_or(0);
        Ok(civil_to_unix(y, mo, day) + h * 3600 + mi * 60 + sec)
    })
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

#[allow(dead_code)]
fn now_secs() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}
