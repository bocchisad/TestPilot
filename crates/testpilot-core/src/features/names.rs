//! Name-token features (`name_file`, `name_jac`, `name_func`).

use std::collections::HashSet;

const STOPWORDS: &[&str] = &["test", "tests", "src", "py", "init"];

/// Tokenize an identifier / path / test id.
/// Matches the lockfile regex intent: `[A-Za-z][a-z0-9]+|[A-Z]+(?![a-z])|\d+`
/// (implemented without lookaround so the default `regex` crate works).
pub fn tokenize(s: &str) -> HashSet<String> {
    let normalized = s.replace('_', " ");
    let chars: Vec<char> = normalized.chars().collect();
    let mut out = HashSet::new();
    let mut i = 0usize;
    while i < chars.len() {
        let c = chars[i];
        if c.is_ascii_digit() {
            let start = i;
            i += 1;
            while i < chars.len() && chars[i].is_ascii_digit() {
                i += 1;
            }
            push_tok(&mut out, &chars[start..i]);
        } else if c.is_ascii_uppercase() {
            let start = i;
            i += 1;
            while i < chars.len() && chars[i].is_ascii_uppercase() {
                i += 1;
            }
            // `[A-Z]+(?![a-z])` — if next is lowercase, last upper starts the next word.
            if i < chars.len() && chars[i].is_ascii_lowercase() && i - start > 1 {
                push_tok(&mut out, &chars[start..i - 1]);
                i -= 1;
            } else if i < chars.len() && chars[i].is_ascii_lowercase() {
                // single upper + lowercase run → `[A-Za-z][a-z0-9]+`
                while i < chars.len()
                    && (chars[i].is_ascii_lowercase() || chars[i].is_ascii_digit())
                {
                    i += 1;
                }
                push_tok(&mut out, &chars[start..i]);
            } else {
                push_tok(&mut out, &chars[start..i]);
            }
        } else if c.is_ascii_lowercase() || c.is_ascii_alphabetic() {
            let start = i;
            i += 1;
            while i < chars.len() && (chars[i].is_ascii_lowercase() || chars[i].is_ascii_digit()) {
                i += 1;
            }
            push_tok(&mut out, &chars[start..i]);
        } else {
            i += 1;
        }
    }
    out
}

fn push_tok(out: &mut HashSet<String>, slice: &[char]) {
    if slice.is_empty() {
        return;
    }
    let t: String = slice.iter().collect::<String>().to_ascii_lowercase();
    if t.len() >= 3 && !STOPWORDS.contains(&t.as_str()) {
        out.insert(t);
    }
}

pub fn name_features(
    srcf: &[String],
    changed_funcs: &[String],
    test_id: &str,
    test_file: &str,
) -> (i64, f64, i64) {
    let mut src_tokens: HashSet<String> = HashSet::new();
    for p in srcf {
        src_tokens.extend(tokenize(p));
        if let Some(base) = std::path::Path::new(p).file_stem().and_then(|s| s.to_str()) {
            src_tokens.extend(tokenize(base));
        }
    }
    let mut test_tokens = tokenize(test_id);
    test_tokens.extend(tokenize(test_file));
    if let Some(base) = std::path::Path::new(test_file)
        .file_stem()
        .and_then(|s| s.to_str())
    {
        test_tokens.extend(tokenize(base));
    }

    let inter: HashSet<_> = src_tokens.intersection(&test_tokens).cloned().collect();
    let name_file = if inter.is_empty() { 0i64 } else { 1 };
    let name_jac = if src_tokens.is_empty() {
        0.0
    } else {
        inter.len() as f64 / src_tokens.len() as f64
    };

    let mut name_func = 0i64;
    for q in changed_funcs {
        // path::Class.method → take last component; skip <module>
        let name = q.rsplit("::").next().unwrap_or(q);
        let short = name.rsplit('.').next().unwrap_or(name);
        if short == "<module>" || short.len() < 3 {
            continue;
        }
        let lower = short.to_ascii_lowercase();
        if test_id.to_ascii_lowercase().contains(&lower) {
            name_func = 1;
            break;
        }
    }
    (name_file, name_jac, name_func)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tokenize_splits_camel_and_snake() {
        let t = tokenize("FooBar_baz");
        assert!(t.contains("foo"));
        assert!(t.contains("bar"));
        assert!(t.contains("baz"));
        assert!(!t.contains("test"));
    }
}
