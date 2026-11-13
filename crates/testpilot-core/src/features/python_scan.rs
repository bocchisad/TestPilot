//! Lightweight Python scanner for imports and def/class spans.
//! Does not panic on broken syntax; skips constructs it cannot parse.

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ImportEdge {
    /// Module path as written (e.g. `os.path`, `.foo`, `pkg.sub`).
    pub module: String,
    /// True if `from .` / `from ..` style relative import.
    pub relative: bool,
    /// Number of leading dots for relative imports (`from ...x` → 3).
    pub level: u32,
    /// Imported names (`from x import a, b`); empty for `import x`.
    pub names: Vec<String>,
    pub line: usize,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DefSpan {
    /// Qualname within the file: `Class.method`, `func`, or nested `Outer.Inner.f`.
    pub qualname: String,
    /// `def` / `async def` / `class`.
    pub kind: String,
    /// Inclusive start line (1-based), including decorators.
    pub start_line: usize,
    /// Exclusive end line (1-based), first line after the block.
    pub end_line: usize,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct ScanResult {
    pub imports: Vec<ImportEdge>,
    pub defs: Vec<DefSpan>,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum StringKind {
    None,
    Single,
    Double,
    TripleSingle,
    TripleDouble,
}

struct Scanner<'a> {
    src: &'a str,
    /// Logical lines after joining `\` continuations and tracking strings.
    lines: Vec<String>,
    /// Original 1-based line number for each logical line start.
    line_nos: Vec<usize>,
}

impl<'a> Scanner<'a> {
    fn new(src: &'a str) -> Self {
        let mut lines = Vec::new();
        let mut line_nos = Vec::new();
        let mut buf = String::new();
        let mut start_no = 1usize;
        let mut in_string = StringKind::None;
        let mut escape = false;
        let mut physical = 1usize;

        let chars: Vec<char> = src.chars().collect();
        let mut i = 0usize;
        while i < chars.len() {
            let c = chars[i];
            if in_string == StringKind::None {
                // Comments: ignore quotes until end of physical line (e.g. Parameter's).
                if c == '#' {
                    while i < chars.len() && chars[i] != '\n' {
                        buf.push(chars[i]);
                        i += 1;
                    }
                    continue;
                }
                // Triple quotes
                if i + 2 < chars.len()
                    && ((c == '"' && chars[i + 1] == '"' && chars[i + 2] == '"')
                        || (c == '\'' && chars[i + 1] == '\'' && chars[i + 2] == '\''))
                {
                    in_string = if c == '"' {
                        StringKind::TripleDouble
                    } else {
                        StringKind::TripleSingle
                    };
                    buf.push(c);
                    buf.push(chars[i + 1]);
                    buf.push(chars[i + 2]);
                    i += 3;
                    continue;
                }
                if c == '"' || c == '\'' {
                    in_string = if c == '"' {
                        StringKind::Double
                    } else {
                        StringKind::Single
                    };
                    buf.push(c);
                    i += 1;
                    continue;
                }
                if c == '\\' && i + 1 < chars.len() && chars[i + 1] == '\n' {
                    // Line continuation outside strings — join physical lines.
                    i += 2;
                    physical += 1;
                    continue;
                }
                if c == '\n' {
                    lines.push(std::mem::take(&mut buf));
                    line_nos.push(start_no);
                    physical += 1;
                    start_no = physical;
                    i += 1;
                    continue;
                }
                buf.push(c);
                i += 1;
                continue;
            }

            // Inside a string
            if escape {
                buf.push(c);
                escape = false;
                i += 1;
                continue;
            }
            if c == '\\' && matches!(in_string, StringKind::Single | StringKind::Double) {
                buf.push(c);
                escape = true;
                i += 1;
                continue;
            }
            if c == '\n' {
                buf.push(c);
                physical += 1;
                i += 1;
                continue;
            }
            match in_string {
                StringKind::TripleDouble
                    if c == '"'
                        && i + 2 < chars.len()
                        && chars[i + 1] == '"'
                        && chars[i + 2] == '"' =>
                {
                    buf.push('"');
                    buf.push('"');
                    buf.push('"');
                    in_string = StringKind::None;
                    i += 3;
                }
                StringKind::TripleSingle
                    if c == '\''
                        && i + 2 < chars.len()
                        && chars[i + 1] == '\''
                        && chars[i + 2] == '\'' =>
                {
                    buf.push('\'');
                    buf.push('\'');
                    buf.push('\'');
                    in_string = StringKind::None;
                    i += 3;
                }
                StringKind::Double if c == '"' => {
                    buf.push(c);
                    in_string = StringKind::None;
                    i += 1;
                }
                StringKind::Single if c == '\'' => {
                    buf.push(c);
                    in_string = StringKind::None;
                    i += 1;
                }
                _ => {
                    buf.push(c);
                    i += 1;
                }
            }
        }
        if !buf.is_empty() || !lines.is_empty() || src.is_empty() {
            lines.push(buf);
            line_nos.push(start_no);
        }
        Self {
            src,
            lines,
            line_nos,
        }
    }

    fn scan(&self) -> ScanResult {
        let mut imports = Vec::new();
        let mut defs = Vec::new();
        let mut stack: Vec<(usize, String, String, usize, usize)> = Vec::new();
        // stack: (indent, kind, name, start_line, decorator_start)
        let mut pending_decorators_start: Option<usize> = None;

        let n = self.lines.len();
        let mut idx = 0usize;
        while idx < n {
            let raw = &self.lines[idx];
            let line_no = self.line_nos[idx];
            let indent = leading_indent(raw);
            let trimmed = raw.trim();

            if trimmed.is_empty() || trimmed.starts_with('#') {
                idx += 1;
                continue;
            }

            // Close finished blocks by indentation. Decorators at indent I belong to a
            // new def/class at I, so they must close siblings/nested frames first
            // (including nested `@click.command()` defs inside a prior test).
            while let Some((ind, _, _, _, _)) = stack.last() {
                if indent <= *ind {
                    let (ind0, kind, name, start, dec_start) = stack.pop().unwrap();
                    let end = line_no;
                    let qual = stack
                        .iter()
                        .map(|(_, _, n, _, _)| n.as_str())
                        .chain(std::iter::once(name.as_str()))
                        .collect::<Vec<_>>()
                        .join(".");
                    let _ = ind0;
                    defs.push(DefSpan {
                        qualname: qual,
                        kind,
                        start_line: dec_start.min(start),
                        end_line: end,
                    });
                } else {
                    break;
                }
            }

            if trimmed.starts_with('@') {
                if pending_decorators_start.is_none() {
                    pending_decorators_start = Some(line_no);
                }
                idx += 1;
                continue;
            }

            if let Some(rest) = trimmed.strip_prefix("async def ") {
                let name = first_ident(rest).unwrap_or("?");
                let dec = pending_decorators_start.take().unwrap_or(line_no);
                stack.push((indent, "async def".into(), name.to_string(), line_no, dec));
                idx += 1;
                continue;
            }
            if let Some(rest) = trimmed.strip_prefix("def ") {
                let name = first_ident(rest).unwrap_or("?");
                let dec = pending_decorators_start.take().unwrap_or(line_no);
                stack.push((indent, "def".into(), name.to_string(), line_no, dec));
                idx += 1;
                continue;
            }
            if let Some(rest) = trimmed.strip_prefix("class ") {
                let name = first_ident(rest).unwrap_or("?");
                let dec = pending_decorators_start.take().unwrap_or(line_no);
                stack.push((indent, "class".into(), name.to_string(), line_no, dec));
                idx += 1;
                continue;
            }

            pending_decorators_start = None;

            // Collect continued import (parentheses)
            let (stmt, consumed) = self.collect_paren_stmt(idx);
            let stmt_trim = stmt.trim();
            if stmt_trim.starts_with("import ") || stmt_trim.starts_with("from ") {
                if let Some(edge) = parse_import(stmt_trim, line_no) {
                    imports.push(edge);
                }
            }
            idx += consumed.max(1);
        }

        // Close remaining stack at EOF
        let eof = self.line_nos.last().copied().unwrap_or(1).saturating_add(1);
        while let Some((_, kind, name, start, dec_start)) = stack.pop() {
            let qual = stack
                .iter()
                .map(|(_, _, n, _, _)| n.as_str())
                .chain(std::iter::once(name.as_str()))
                .collect::<Vec<_>>()
                .join(".");
            defs.push(DefSpan {
                qualname: qual,
                kind,
                start_line: dec_start.min(start),
                end_line: eof,
            });
        }

        let _ = self.src;
        ScanResult { imports, defs }
    }

    fn collect_paren_stmt(&self, start: usize) -> (String, usize) {
        let mut out = self.lines[start].clone();
        let mut depth = paren_delta(&self.lines[start]);
        let mut i = 1usize;
        while depth > 0 && start + i < self.lines.len() {
            out.push(' ');
            out.push_str(self.lines[start + i].trim());
            depth += paren_delta(&self.lines[start + i]);
            i += 1;
        }
        (out, i)
    }
}

fn leading_indent(s: &str) -> usize {
    s.chars().take_while(|c| *c == ' ' || *c == '\t').count()
}

fn first_ident(s: &str) -> Option<&str> {
    let s = s.trim_start();
    let end = s
        .char_indices()
        .find(|(_, c)| !c.is_ascii_alphanumeric() && *c != '_')
        .map(|(i, _)| i)
        .unwrap_or(s.len());
    if end == 0 { None } else { Some(&s[..end]) }
}

fn paren_delta(s: &str) -> i32 {
    let mut d = 0i32;
    let mut in_s = StringKind::None;
    let mut esc = false;
    let chars: Vec<char> = s.chars().collect();
    let mut i = 0;
    while i < chars.len() {
        let c = chars[i];
        if in_s == StringKind::None {
            // Comments must not contribute parens or open strings (Parameter's, &#34;).
            if c == '#' {
                break;
            }
            if c == '(' {
                d += 1;
                i += 1;
                continue;
            }
            if c == ')' {
                d -= 1;
                i += 1;
                continue;
            }
            // Triple quotes before single — otherwise """ is empty Double + new Double
            // and an embedded '"' (jinja `"""{{ '<">&'|escape }}"""`) desyncs depth.
            if i + 2 < chars.len()
                && ((c == '"' && chars[i + 1] == '"' && chars[i + 2] == '"')
                    || (c == '\'' && chars[i + 1] == '\'' && chars[i + 2] == '\''))
            {
                in_s = if c == '"' {
                    StringKind::TripleDouble
                } else {
                    StringKind::TripleSingle
                };
                i += 3;
                continue;
            }
            if c == '"' || c == '\'' {
                in_s = if c == '"' {
                    StringKind::Double
                } else {
                    StringKind::Single
                };
            }
            i += 1;
            continue;
        }
        if esc {
            esc = false;
            i += 1;
            continue;
        }
        if c == '\\' && matches!(in_s, StringKind::Single | StringKind::Double) {
            esc = true;
            i += 1;
            continue;
        }
        match in_s {
            StringKind::TripleDouble
                if c == '"'
                    && i + 2 < chars.len()
                    && chars[i + 1] == '"'
                    && chars[i + 2] == '"' =>
            {
                in_s = StringKind::None;
                i += 3;
                continue;
            }
            StringKind::TripleSingle
                if c == '\''
                    && i + 2 < chars.len()
                    && chars[i + 1] == '\''
                    && chars[i + 2] == '\'' =>
            {
                in_s = StringKind::None;
                i += 3;
                continue;
            }
            StringKind::Double if c == '"' => {
                in_s = StringKind::None;
            }
            StringKind::Single if c == '\'' => {
                in_s = StringKind::None;
            }
            _ => {}
        }
        i += 1;
    }
    d
}

fn parse_import(stmt: &str, line: usize) -> Option<ImportEdge> {
    let stmt = stmt.trim().trim_end_matches('\\').trim();
    if let Some(rest) = stmt.strip_prefix("from ") {
        let rest = rest.trim();
        let mut level = 0u32;
        let bytes = rest.as_bytes();
        let mut i = 0;
        while i < bytes.len() && bytes[i] == b'.' {
            level += 1;
            i += 1;
        }
        let rest = &rest[i..];
        let (module, after) = if let Some(idx) = rest.find(" import ") {
            (&rest[..idx], rest[idx + " import ".len()..].trim())
        } else {
            return None;
        };
        let module = module.trim().to_string();
        let names = split_import_names(after);
        return Some(ImportEdge {
            module,
            relative: level > 0,
            level,
            names,
            line,
        });
    }
    if let Some(rest) = stmt.strip_prefix("import ") {
        let first = rest.split(',').next()?.trim();
        let module = first.split_whitespace().next()?.to_string();
        return Some(ImportEdge {
            module,
            relative: false,
            level: 0,
            names: vec![],
            line,
        });
    }
    None
}

fn split_import_names(s: &str) -> Vec<String> {
    let s = s.trim().trim_start_matches('(').trim_end_matches(')');
    s.split(',')
        .map(|p| {
            let p = p.trim();
            p.split_whitespace()
                .next()
                .unwrap_or("")
                .trim_matches(',')
                .to_string()
        })
        .filter(|p| !p.is_empty() && p != "(" && p != ")")
        .collect()
}

/// Scan Python source for imports and definition spans.
pub fn scan_python(src: &str) -> ScanResult {
    Scanner::new(src).scan()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn simple_import_and_def() {
        let r = scan_python("import os\n\ndef foo():\n    return 1\n");
        assert_eq!(r.imports.len(), 1);
        assert_eq!(r.imports[0].module, "os");
        assert!(
            r.defs
                .iter()
                .any(|d| d.qualname == "foo" && d.kind == "def")
        );
    }

    #[test]
    fn multiline_from_import_parens() {
        let src = "from pkg.mod import (\n    a,\n    b,\n)\n";
        let r = scan_python(src);
        assert_eq!(r.imports.len(), 1);
        assert_eq!(r.imports[0].module, "pkg.mod");
        assert!(r.imports[0].names.contains(&"a".into()));
        assert!(r.imports[0].names.contains(&"b".into()));
    }

    #[test]
    fn backslash_continuation_import() {
        let src = "from pkg.mod import a, \\\n    b\n";
        let r = scan_python(src);
        assert_eq!(r.imports.len(), 1);
        assert!(r.imports[0].names.contains(&"a".into()));
        assert!(r.imports[0].names.contains(&"b".into()));
    }

    #[test]
    fn async_def() {
        let r = scan_python("async def run():\n    pass\n");
        assert!(
            r.defs
                .iter()
                .any(|d| d.qualname == "run" && d.kind == "async def")
        );
    }

    #[test]
    fn decorators_in_span() {
        let src = "@deco\n@other\ndef f():\n    pass\n";
        let r = scan_python(src);
        let d = r.defs.iter().find(|d| d.qualname == "f").unwrap();
        assert_eq!(d.start_line, 1);
    }

    #[test]
    fn nested_class_method() {
        let src = "class A:\n    def meth(self):\n        pass\n";
        let r = scan_python(src);
        assert!(
            r.defs
                .iter()
                .any(|d| d.qualname == "A" && d.kind == "class")
        );
        assert!(
            r.defs
                .iter()
                .any(|d| d.qualname == "A.meth" && d.kind == "def")
        );
    }

    #[test]
    fn def_inside_triple_quotes_ignored() {
        let src = "s = \"\"\"\ndef not_real():\n    pass\n\"\"\"\n\ndef real():\n    pass\n";
        let r = scan_python(src);
        assert!(!r.defs.iter().any(|d| d.qualname == "not_real"));
        assert!(r.defs.iter().any(|d| d.qualname == "real"));
    }

    #[test]
    fn imports_inside_type_checking_and_try_counted() {
        let src = "\
if TYPE_CHECKING:\n\
    from typing import List\n\
try:\n\
    import optional_pkg\n\
except ImportError:\n\
    pass\n";
        let r = scan_python(src);
        assert!(r.imports.iter().any(|i| i.module == "typing"));
        assert!(r.imports.iter().any(|i| i.module == "optional_pkg"));
    }

    #[test]
    fn broken_syntax_does_not_panic() {
        let r = scan_python("def (\nclass\nimport\n");
        let _ = r;
    }

    #[test]
    fn relative_import() {
        let r = scan_python("from . import sibling\nfrom ..pkg import x\n");
        assert_eq!(r.imports.len(), 2);
        assert!(r.imports[0].relative && r.imports[0].level == 1);
        assert!(r.imports[1].relative && r.imports[1].level == 2);
    }

    #[test]
    fn nested_click_command_does_not_swallow_following_tests() {
        // Regression: click tests/test_basic.py shape — nested @click.command inside a
        // test, apostrophe in a `#` comment, then later top-level tests.
        let src = "\
def test_boolean_conversion():\n\
    @click.command()\n\
    @click.option(\"--foo\", is_flag=True)\n\
    def cli(foo):\n\
        click.echo(str(foo))\n\
    result = runner.invoke(cli)\n\
    assert result.exit_code == 0\n\
\n\
# Non-string defaults are process as strings by the default Parameter's type.\n\
@pytest.mark.parametrize(\n\
    (\"default\", \"args\"),\n\
    (\n\
        (True, [\"--upper\"]),\n\
    ),\n\
)\n\
def test_flag_value_dual_options():\n\
    @click.command()\n\
    def cli():\n\
        pass\n\
    assert True\n\
\n\
def test_choice_argument():\n\
    assert True\n\
\n\
@pytest.mark.parametrize(\"x\", [1, 2])\n\
def test_after_param():\n\
    assert x\n\
";
        let r = scan_python(src);
        let names: Vec<_> = r
            .defs
            .iter()
            .filter(|d| d.kind != "class")
            .map(|d| d.qualname.as_str())
            .collect();
        assert!(
            names.contains(&"test_boolean_conversion"),
            "missing outer test: {names:?}"
        );
        assert!(
            names.iter().any(|n| n.ends_with("cli")),
            "missing nested cli: {names:?}"
        );
        assert!(
            names.contains(&"test_flag_value_dual_options"),
            "comment apostrophe swallowed following def: {names:?}"
        );
        assert!(
            names.contains(&"test_choice_argument"),
            "swallowed following test: {names:?}"
        );
        assert!(
            names.contains(&"test_after_param"),
            "swallowed parametrized test after nested decorators: {names:?}"
        );
    }

    #[test]
    fn triple_quote_with_embedded_quote_does_not_swallow_following_defs() {
        // Regression: paren_delta treated """ as empty Double + Double, then an
        // embedded '"' (jinja test_escape shape) left depth>0 and collect_paren_stmt
        // swallowed every later def in the class.
        let src = "\
class TestFilter:\n\
    def test_escape(self, env):\n\
        tmpl = env.from_string(\"\"\"{{ '<\">&'|escape }}\"\"\")\n\
        out = tmpl.render()\n\
        assert out == \"&lt;&#34;&gt;&amp;\"\n\
\n\
    @pytest.mark.parametrize(\"chars\", [None, \".\"])\n\
    def test_trim(self, env, chars):\n\
        assert True\n\
\n\
    def test_striptags(self, env):\n\
        foo = '  <a href=\"#\">'\n\
        assert foo\n\
";
        let r = scan_python(src);
        let names: Vec<_> = r
            .defs
            .iter()
            .filter(|d| d.kind != "class")
            .map(|d| d.qualname.as_str())
            .collect();
        assert!(
            names.iter().any(|n| n.ends_with("test_escape")),
            "missing escape: {names:?}"
        );
        assert!(
            names.iter().any(|n| n.ends_with("test_trim")),
            "swallowed after triple-quote: {names:?}"
        );
        assert!(
            names.iter().any(|n| n.ends_with("test_striptags")),
            "swallowed after &#34; / href: {names:?}"
        );
        assert_eq!(
            names.len(),
            3,
            "extra/missing defs (paren swallow?): {names:?}"
        );
    }

    #[test]
    fn jinja_test_filters_finds_all_testfilter_methods() {
        let path = concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../.cache/repos/jinja/tests/test_filters.py"
        );
        let Ok(src) = std::fs::read_to_string(path) else {
            return;
        };
        let r = scan_python(&src);
        let methods: Vec<_> = r
            .defs
            .iter()
            .filter(|d| d.qualname.starts_with("TestFilter.") && d.kind != "class")
            .map(|d| d.qualname.as_str())
            .collect();
        assert!(
            methods.len() >= 90,
            "only {} TestFilter methods (expected ~99)",
            methods.len()
        );
        assert!(methods.contains(&"TestFilter.test_escape"));
        assert!(methods.contains(&"TestFilter.test_trim"));
        assert!(methods.contains(&"TestFilter.test_striptags"));
    }

    #[test]
    fn click_test_basic_finds_choice_argument() {
        let path = concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../.cache/repos/click/tests/test_basic.py"
        );
        let Ok(src) = std::fs::read_to_string(path) else {
            // Optional local fixture; skip when cache is absent.
            return;
        };
        let r = scan_python(&src);
        let names: std::collections::HashSet<_> = r
            .defs
            .iter()
            .filter(|d| d.kind != "class")
            .map(|d| d.qualname.as_str())
            .collect();
        let top: Vec<_> = names
            .iter()
            .filter(|n| !n.contains('.') && n.starts_with("test_"))
            .collect();
        assert!(
            names.contains("test_boolean_conversion"),
            "missing test_boolean_conversion"
        );
        assert!(
            names.contains("test_choice_argument"),
            "missing test_choice_argument; top={top:?}"
        );
        assert!(names.contains("test_flag_value_dual_options"));
        assert!(
            top.len() >= 40,
            "only {} top-level tests: {top:?}",
            top.len()
        );
    }
}
