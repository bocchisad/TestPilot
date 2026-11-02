//! Streaming JUnit XML parser (pytest, Surefire, Gradle, nested suites).

use std::io::BufRead;
use std::path::Path;

use quick_xml::Reader;
use quick_xml::events::{BytesStart, Event};

use crate::error::{Error, Result};
use crate::store::TestResultRow;

const MESSAGE_LIMIT: usize = 4096;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ParsedJunit {
    /// Unix seconds from suite `timestamp` attribute, if any.
    pub started_at: Option<i64>,
    pub results: Vec<RawTestCase>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RawTestCase {
    pub test_id: String,
    pub outcome: String,
    pub duration_ms: i64,
    pub message: Option<String>,
}

#[derive(Default)]
struct TestCaseBuilder {
    classname: String,
    name: String,
    file: Option<String>,
    duration_ms: i64,
    outcome: String,
    message: Option<String>,
    capture_message: bool,
    message_buf: String,
}

impl TestCaseBuilder {
    fn new() -> Self {
        Self {
            outcome: "pass".into(),
            ..Default::default()
        }
    }

    fn finish(self) -> RawTestCase {
        RawTestCase {
            test_id: normalize_test_id(&self.classname, &self.name, self.file.as_deref()),
            outcome: self.outcome,
            duration_ms: self.duration_ms,
            message: self.message,
        }
    }
}

pub fn parse_file(path: &Path) -> Result<ParsedJunit> {
    let file = std::fs::File::open(path)?;
    parse_reader(std::io::BufReader::new(file))
}

pub fn parse_str(xml: &str) -> Result<ParsedJunit> {
    parse_reader(std::io::Cursor::new(xml.as_bytes()))
}

pub fn parse_reader<R: BufRead>(reader: R) -> Result<ParsedJunit> {
    let mut xml = Reader::from_reader(reader);
    xml.config_mut().trim_text(true);

    let mut buf = Vec::new();
    let mut started_at: Option<i64> = None;
    let mut results = Vec::new();
    let mut current: Option<TestCaseBuilder> = None;

    loop {
        match xml.read_event_into(&mut buf) {
            Ok(Event::Start(ref e)) => {
                handle_start(e, &mut started_at, &mut current, &mut results, false)?;
            }
            Ok(Event::Empty(ref e)) => {
                handle_start(e, &mut started_at, &mut current, &mut results, true)?;
            }
            Ok(Event::Text(t)) => {
                if let Some(tc) = current.as_mut() {
                    if tc.capture_message {
                        let text = t.unescape().map_err(|err| Error::Junit(err.to_string()))?;
                        if !tc.message_buf.is_empty() && !text.is_empty() {
                            tc.message_buf.push('\n');
                        }
                        tc.message_buf.push_str(&text);
                    }
                }
            }
            Ok(Event::CData(t)) => {
                if let Some(tc) = current.as_mut() {
                    if tc.capture_message {
                        let text = String::from_utf8_lossy(&t);
                        if !tc.message_buf.is_empty() && !text.is_empty() {
                            tc.message_buf.push('\n');
                        }
                        tc.message_buf.push_str(&text);
                    }
                }
            }
            Ok(Event::End(ref e)) => {
                let tag = local_name(e.name().as_ref());
                match tag.as_str() {
                    "failure" | "error" => {
                        if let Some(tc) = current.as_mut() {
                            if tc.capture_message {
                                tc.message = Some(truncate_message(&tc.message_buf));
                                tc.capture_message = false;
                                tc.message_buf.clear();
                            }
                        }
                    }
                    "testcase" => {
                        if let Some(tc) = current.take() {
                            results.push(tc.finish());
                        }
                    }
                    _ => {}
                }
            }
            Ok(Event::Eof) => break,
            Err(e) => return Err(Error::Junit(e.to_string())),
            _ => {}
        }
        buf.clear();
    }

    Ok(ParsedJunit {
        started_at,
        results,
    })
}

fn handle_start(
    e: &BytesStart<'_>,
    started_at: &mut Option<i64>,
    current: &mut Option<TestCaseBuilder>,
    results: &mut Vec<RawTestCase>,
    empty: bool,
) -> Result<()> {
    let tag = local_name(e.name().as_ref());
    let attrs = read_attrs(e)?;
    match tag.as_str() {
        "testsuites" | "testsuite" => {
            apply_suite_timestamp(started_at, &attrs);
        }
        "testcase" => {
            let mut tc = TestCaseBuilder::new();
            fill_testcase(&mut tc, &attrs);
            if empty {
                results.push(tc.finish());
            } else {
                *current = Some(tc);
            }
        }
        "failure" => {
            if let Some(tc) = current.as_mut() {
                tc.outcome = "fail".into();
                if empty {
                    for (k, v) in &attrs {
                        if k == "message" {
                            tc.message = Some(truncate_message(v));
                        }
                    }
                } else {
                    tc.capture_message = true;
                    tc.message_buf.clear();
                    for (k, v) in &attrs {
                        if k == "message" {
                            tc.message_buf.push_str(v);
                        }
                    }
                }
            }
        }
        "error" => {
            if let Some(tc) = current.as_mut() {
                tc.outcome = "error".into();
                if empty {
                    for (k, v) in &attrs {
                        if k == "message" {
                            tc.message = Some(truncate_message(v));
                        }
                    }
                } else {
                    tc.capture_message = true;
                    tc.message_buf.clear();
                    for (k, v) in &attrs {
                        if k == "message" {
                            tc.message_buf.push_str(v);
                        }
                    }
                }
            }
        }
        "skipped" | "skip" => {
            if let Some(tc) = current.as_mut() {
                tc.outcome = "skip".into();
            }
        }
        _ => {}
    }
    Ok(())
}

fn local_name(raw: &[u8]) -> String {
    let name = String::from_utf8_lossy(raw);
    name.rsplit(':').next().unwrap_or(&name).to_string()
}

fn read_attrs(e: &BytesStart<'_>) -> Result<Vec<(String, String)>> {
    let mut out = Vec::new();
    for a in e.attributes() {
        let a = a.map_err(|err| Error::Junit(err.to_string()))?;
        let key = String::from_utf8_lossy(a.key.as_ref()).to_string();
        let val = a
            .unescape_value()
            .map_err(|err| Error::Junit(err.to_string()))?
            .to_string();
        out.push((key, val));
    }
    Ok(out)
}

fn apply_suite_timestamp(started_at: &mut Option<i64>, attrs: &[(String, String)]) {
    if started_at.is_some() {
        return;
    }
    for (k, v) in attrs {
        if k == "timestamp" {
            *started_at = parse_timestamp(v);
        }
    }
}

fn fill_testcase(tc: &mut TestCaseBuilder, attrs: &[(String, String)]) {
    for (k, v) in attrs {
        match k.as_str() {
            "classname" => tc.classname = v.clone(),
            "name" => tc.name = v.clone(),
            "file" => tc.file = Some(v.clone()),
            "time" => tc.duration_ms = parse_time_ms(v),
            _ => {}
        }
    }
}

/// Collapse multiple attempts for the same test_id: keep last, set attempts/flaky.
pub fn collapse_reruns(raw: &[RawTestCase]) -> Vec<TestResultRow> {
    use std::collections::HashMap;
    let mut order: Vec<String> = Vec::new();
    let mut map: HashMap<String, (TestResultRow, Vec<String>)> = HashMap::new();

    for r in raw {
        let entry = map.entry(r.test_id.clone()).or_insert_with(|| {
            order.push(r.test_id.clone());
            (
                TestResultRow {
                    test_id: r.test_id.clone(),
                    outcome: r.outcome.clone(),
                    duration_ms: r.duration_ms,
                    message: r.message.clone(),
                    flaky: false,
                    attempts: 0,
                },
                Vec::new(),
            )
        });
        entry.1.push(r.outcome.clone());
        entry.0.outcome = r.outcome.clone();
        entry.0.duration_ms = r.duration_ms;
        entry.0.message = r.message.clone();
        entry.0.attempts += 1;
    }

    order
        .into_iter()
        .filter_map(|id| {
            map.remove(&id).map(|(mut row, outcomes)| {
                let first = outcomes.first().cloned().unwrap_or_default();
                row.flaky = outcomes.iter().any(|o| o != &first);
                row
            })
        })
        .collect()
}

pub fn normalize_test_id(classname: &str, name: &str, file: Option<&str>) -> String {
    if let Some(f) = file {
        let path = f.replace('\\', "/");
        if path.ends_with(".py") || path.contains('/') {
            // file + simple ClassName (no dots)
            if !classname.is_empty()
                && !classname.contains('/')
                && !classname.contains("::")
                && !classname.contains('.')
                && classname != name
            {
                return format!("{path}::{classname}::{name}");
            }
            // file + module.Class → keep Class
            if let Some(cls) = classname.rsplit('.').next() {
                if looks_like_class(cls) && classname.contains('.') {
                    return format!("{path}::{cls}::{name}");
                }
            }
            return format!("{path}::{name}");
        }
    }

    if classname.contains("::") || classname.contains('/') || classname.ends_with(".py") {
        if classname.is_empty() {
            return name.to_string();
        }
        return format!("{classname}::{name}");
    }

    // pytest module path without file attr: tests.test_math / tests.test_math.TestCls
    if looks_like_pytest_module(classname) {
        return pytest_module_to_id(classname, name);
    }

    if classname.is_empty() {
        name.to_string()
    } else {
        format!("{classname}#{name}")
    }
}

fn looks_like_class(s: &str) -> bool {
    let mut chars = s.chars();
    matches!(chars.next(), Some('A'..='Z'))
        && s.chars().all(|c| c.is_ascii_alphanumeric() || c == '_')
}

fn looks_like_pytest_module(classname: &str) -> bool {
    if classname.is_empty() || classname.contains('/') || classname.contains('#') {
        return false;
    }
    // Java packages usually have CamelCase class as last segment AND often deeper nesting;
    // treat as pytest if any segment starts with "test" or the name looks like a test function.
    classname
        .split('.')
        .all(|p| !p.is_empty() && p.chars().all(|c| c.is_ascii_alphanumeric() || c == '_'))
        && (classname.contains("test") || classname.split('.').any(|p| p.starts_with("test")))
}

fn pytest_module_to_id(classname: &str, name: &str) -> String {
    let parts: Vec<&str> = classname.split('.').collect();
    if let Some((last, rest)) = parts.split_last() {
        if looks_like_class(last) && !rest.is_empty() {
            let path = format!("{}.py", rest.join("/"));
            return format!("{path}::{last}::{name}");
        }
    }
    format!("{}.py::{name}", parts.join("/"))
}

fn parse_time_ms(s: &str) -> i64 {
    s.parse::<f64>()
        .map(|secs| (secs * 1000.0).round() as i64)
        .unwrap_or(0)
}

fn parse_timestamp(s: &str) -> Option<i64> {
    if let Ok(n) = s.parse::<i64>() {
        return Some(n);
    }
    let s = s.trim_end_matches('Z');
    let (date, time) = s.split_once('T')?;
    let mut d = date.split('-');
    let y: i64 = d.next()?.parse().ok()?;
    let mo: i64 = d.next()?.parse().ok()?;
    let day: i64 = d.next()?.parse().ok()?;
    let time = time.split(['+', '-']).next()?;
    let mut t = time.split(':');
    let h: i64 = t.next()?.parse().ok()?;
    let mi: i64 = t.next()?.parse().ok()?;
    let sec: i64 = t
        .next()
        .and_then(|x| x.parse::<f64>().ok())
        .map(|x| x as i64)
        .unwrap_or(0);
    Some(days_from_civil(y, mo, day) * 86400 + h * 3600 + mi * 60 + sec)
}

fn days_from_civil(y: i64, m: i64, d: i64) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = y - era * 400;
    let mp = if m > 2 { m - 3 } else { m + 9 };
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146097 + doe - 719468
}

fn truncate_message(s: &str) -> String {
    if s.len() <= MESSAGE_LIMIT {
        s.to_string()
    } else {
        let mut t = s.chars().take(MESSAGE_LIMIT).collect::<String>();
        t.push('…');
        t
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn fixture(name: &str) -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../tests/fixtures/junit")
            .join(name)
    }

    #[test]
    fn parse_pytest_fixture() {
        let p = parse_file(&fixture("pytest.xml")).unwrap();
        assert!(!p.results.is_empty());
        assert!(p.results.iter().any(|r| r.outcome == "pass"));
        assert!(p.results.iter().any(|r| r.outcome == "fail"));
        assert!(p.results.iter().any(|r| r.outcome == "skip"));
        assert!(p.results.iter().all(|r| r.test_id.contains(".py::")));
    }

    #[test]
    fn parse_surefire() {
        let p = parse_file(&fixture("surefire.xml")).unwrap();
        assert!(p.results.iter().any(|r| r.test_id.contains('#')));
    }

    #[test]
    fn parse_gradle() {
        let p = parse_file(&fixture("gradle.xml")).unwrap();
        assert!(!p.results.is_empty());
    }

    #[test]
    fn parse_nested() {
        let p = parse_file(&fixture("nested.xml")).unwrap();
        assert!(p.results.len() >= 2);
    }

    #[test]
    fn malformed_errors() {
        let err = parse_file(&fixture("malformed.xml")).unwrap_err();
        assert!(matches!(err, Error::Junit(_)));
    }

    #[test]
    fn missing_time_is_zero() {
        let xml = r#"<?xml version="1.0"?>
        <testsuite><testcase classname="a.B" name="c"/></testsuite>"#;
        let p = parse_str(xml).unwrap();
        assert_eq!(p.results[0].duration_ms, 0);
        assert_eq!(p.results[0].test_id, "a.B#c");
    }

    #[test]
    fn collapse_flaky() {
        let raw = vec![
            RawTestCase {
                test_id: "t".into(),
                outcome: "fail".into(),
                duration_ms: 1,
                message: None,
            },
            RawTestCase {
                test_id: "t".into(),
                outcome: "pass".into(),
                duration_ms: 2,
                message: None,
            },
        ];
        let rows = collapse_reruns(&raw);
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].outcome, "pass");
        assert_eq!(rows[0].attempts, 2);
        assert!(rows[0].flaky);
    }

    #[test]
    fn timestamp_from_suite() {
        let xml = r#"<?xml version="1.0"?>
        <testsuite timestamp="2024-01-02T03:04:05">
          <testcase classname="a.B" name="c" time="0.1"/>
        </testsuite>"#;
        let p = parse_str(xml).unwrap();
        assert!(p.started_at.is_some());
    }

    #[test]
    fn pytest_module_classname_without_file() {
        assert_eq!(
            normalize_test_id("tests.test_math", "test_add", None),
            "tests/test_math.py::test_add"
        );
        assert_eq!(
            normalize_test_id("tests.test_math.TestFoo", "test_add", None),
            "tests/test_math.py::TestFoo::test_add"
        );
    }
}
