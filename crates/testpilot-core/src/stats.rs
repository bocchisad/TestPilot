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
    // Exclude synthetic (mutant) runs from all aggregate stats.
    let runs: i64 = conn.query_row(
        "SELECT COUNT(*) FROM test_runs WHERE IFNULL(synthetic,0)=0",
        [],
        |r| r.get(0),
    )?;
    let distinct_tests: i64 = conn.query_row("SELECT COUNT(*) FROM tests", [], |r| r.get(0))?;
    let failures: i64 = conn.query_row(
        "SELECT COUNT(*) FROM test_results r
         JOIN test_runs u ON u.id = r.run_id
         WHERE IFNULL(u.synthetic,0)=0 AND r.outcome IN ('fail','error')",
        [],
        |r| r.get(0),
    )?;

    let mut top_slowest = Vec::new();
    let mut stmt = conn.prepare(
        "SELECT t.test_id, AVG(r.duration_ms) AS avg_ms
         FROM test_results r
         JOIN tests t ON t.id = r.test_pk
         JOIN test_runs u ON u.id = r.run_id
         WHERE IFNULL(u.synthetic,0)=0
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
         JOIN test_runs u ON u.id = r.run_id
         WHERE IFNULL(u.synthetic,0)=0
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::store::{CommitRow, Store, TestResultRow, TestRunRow};

    #[test]
    fn synthetic_runs_excluded_from_stats() {
        let mut store = Store::open_in_memory().unwrap();
        let sha = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        store
            .insert_commit(&CommitRow {
                sha: sha.into(),
                author_ts: 1,
                message: "c".into(),
                oversized: false,
                parents: vec![],
            })
            .unwrap();
        let real = TestRunRow {
            sha: sha.into(),
            source: "pytest".into(),
            started_at: 1,
            finished_at: Some(2),
            dirty: false,
            diff_hash: None,
            input_hash: Some("real".into()),
            meta: None,
            synthetic: false,
        };
        let syn = TestRunRow {
            sha: sha.into(),
            source: "junit".into(),
            started_at: 3,
            finished_at: Some(4),
            dirty: false,
            diff_hash: None,
            input_hash: Some("syn".into()),
            meta: None,
            synthetic: true,
        };
        let pass = vec![TestResultRow {
            test_id: "t::ok".into(),
            outcome: "pass".into(),
            duration_ms: 10,
            message: None,
            flaky: false,
            attempts: 1,
        }];
        let fail = vec![TestResultRow {
            test_id: "t::bad".into(),
            outcome: "fail".into(),
            duration_ms: 99,
            message: None,
            flaky: false,
            attempts: 1,
        }];
        store.insert_run_atomic(&real, &pass, &[], false).unwrap();
        let before = compute(&store).unwrap();
        store.insert_run_atomic(&syn, &fail, &[], false).unwrap();
        let after = compute(&store).unwrap();
        assert_eq!(before.runs, after.runs);
        assert_eq!(before.failures, after.failures);
        assert_eq!(before.top_slowest.len(), after.top_slowest.len());
        assert_eq!(
            before.top_flaky_failing.len(),
            after.top_flaky_failing.len()
        );
    }
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
