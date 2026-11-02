use serde::Serialize;

use crate::error::Result;
use crate::store::Store;

#[derive(Debug, Serialize)]
pub struct Stats {
    pub commits: i64,
    pub runs: i64,
    pub distinct_tests: i64,
    pub failures: i64,
    pub top_slowest: Vec<TestStat>,
    pub top_flaky_failing: Vec<FailStat>,
}

#[derive(Debug, Serialize)]
pub struct TestStat {
    pub test_id: String,
    pub avg_duration_ms: f64,
}

#[derive(Debug, Serialize)]
pub struct FailStat {
    pub test_id: String,
    pub fail_count: i64,
    pub total: i64,
}

pub fn compute(store: &Store) -> Result<Stats> {
    let conn = store.connection();
    let commits: i64 = conn.query_row("SELECT COUNT(*) FROM commits", [], |r| r.get(0))?;
    let runs: i64 = conn.query_row("SELECT COUNT(*) FROM test_runs", [], |r| r.get(0))?;
    let distinct_tests: i64 = conn.query_row("SELECT COUNT(*) FROM tests", [], |r| r.get(0))?;
    let failures: i64 = conn.query_row(
        "SELECT COUNT(*) FROM test_results WHERE outcome IN ('fail','error')",
        [],
        |r| r.get(0),
    )?;

    let mut top_slowest = Vec::new();
    let mut stmt = conn.prepare(
        "SELECT t.test_id, AVG(r.duration_ms) AS avg_ms
         FROM test_results r
         JOIN tests t ON t.id = r.test_pk
         GROUP BY t.test_id
         ORDER BY avg_ms DESC
         LIMIT 10",
    )?;
    let rows = stmt.query_map([], |r| {
        Ok(TestStat {
            test_id: r.get(0)?,
            avg_duration_ms: r.get(1)?,
        })
    })?;
    for row in rows {
        top_slowest.push(row?);
    }

    let mut top_flaky_failing = Vec::new();
    let mut stmt = conn.prepare(
        "SELECT t.test_id,
                SUM(CASE WHEN r.outcome IN ('fail','error') THEN 1 ELSE 0 END) AS fails,
                COUNT(*) AS total
         FROM test_results r
         JOIN tests t ON t.id = r.test_pk
         GROUP BY t.test_id
         HAVING fails > 0
         ORDER BY fails DESC, total DESC
         LIMIT 10",
    )?;
    let rows = stmt.query_map([], |r| {
        Ok(FailStat {
            test_id: r.get(0)?,
            fail_count: r.get(1)?,
            total: r.get(2)?,
        })
    })?;
    for row in rows {
        top_flaky_failing.push(row?);
    }

    Ok(Stats {
        commits,
        runs,
        distinct_tests,
        failures,
        top_slowest,
        top_flaky_failing,
    })
}

pub fn format_human(stats: &Stats) -> String {
    let mut out = String::new();
    out.push_str(&format!("commits:         {}\n", stats.commits));
    out.push_str(&format!("test runs:       {}\n", stats.runs));
    out.push_str(&format!("distinct tests:  {}\n", stats.distinct_tests));
    out.push_str(&format!("failures/errors: {}\n", stats.failures));
    out.push_str("\nTop 10 slowest tests (avg duration_ms):\n");
    for (i, t) in stats.top_slowest.iter().enumerate() {
        out.push_str(&format!(
            "  {:2}. {:>10.1}  {}\n",
            i + 1,
            t.avg_duration_ms,
            t.test_id
        ));
    }
    out.push_str("\nTop 10 most failing tests:\n");
    for (i, t) in stats.top_flaky_failing.iter().enumerate() {
        out.push_str(&format!(
            "  {:2}. {:>4}/{:<4}  {}\n",
            i + 1,
            t.fail_count,
            t.total,
            t.test_id
        ));
    }
    out
}
