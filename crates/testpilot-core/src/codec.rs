//! Coverage line blob: `0x01` + delta-encoded unsigned varints of sorted unique u32s.

use crate::error::{Error, Result};

pub const COVERAGE_VERSION: u8 = 0x01;

/// Encode sorted-unique line numbers into a versioned blob.
pub fn encode_lines(lines: &[u32]) -> Vec<u8> {
    let mut sorted: Vec<u32> = lines.to_vec();
    sorted.sort_unstable();
    sorted.dedup();

    let mut out = Vec::with_capacity(1 + sorted.len() * 2);
    out.push(COVERAGE_VERSION);

    let mut prev = 0u32;
    for (i, &line) in sorted.iter().enumerate() {
        let delta = if i == 0 {
            line
        } else {
            line.saturating_sub(prev)
        };
        write_varint(&mut out, delta);
        prev = line;
    }
    out
}

/// Decode a coverage blob into sorted unique line numbers.
pub fn decode_lines(blob: &[u8]) -> Result<Vec<u32>> {
    if blob.is_empty() {
        return Err(Error::Codec("empty blob".into()));
    }
    if blob[0] != COVERAGE_VERSION {
        return Err(Error::Codec(format!(
            "unsupported coverage version {:#x}",
            blob[0]
        )));
    }
    if blob.len() == 1 {
        return Ok(Vec::new());
    }

    let mut lines = Vec::new();
    let mut prev = 0u32;
    let mut i = 1usize;
    let mut first = true;
    while i < blob.len() {
        let (delta, next) = read_varint(blob, i)?;
        i = next;
        let line = if first {
            first = false;
            delta
        } else {
            prev.checked_add(delta)
                .ok_or_else(|| Error::Codec("line number overflow".into()))?
        };
        lines.push(line);
        prev = line;
    }
    Ok(lines)
}

fn write_varint(out: &mut Vec<u8>, mut value: u32) {
    loop {
        let mut byte = (value & 0x7f) as u8;
        value >>= 7;
        if value != 0 {
            byte |= 0x80;
            out.push(byte);
        } else {
            out.push(byte);
            break;
        }
    }
}

fn read_varint(buf: &[u8], mut i: usize) -> Result<(u32, usize)> {
    let mut result = 0u32;
    let mut shift = 0u32;
    loop {
        if i >= buf.len() {
            return Err(Error::Codec("truncated varint".into()));
        }
        let byte = buf[i];
        i += 1;
        // Reject overflow before shifting bits off a u32 (shift >= 28).
        if shift >= 28 && (byte & 0x80 != 0 || (shift == 28 && byte > 0x0f)) {
            return Err(Error::Codec("varint overflow".into()));
        }
        result |= u32::from(byte & 0x7f)
            .checked_shl(shift)
            .ok_or_else(|| Error::Codec("varint overflow".into()))?;
        if byte & 0x80 == 0 {
            return Ok((result, i));
        }
        shift = shift
            .checked_add(7)
            .ok_or_else(|| Error::Codec("varint overflow".into()))?;
        if shift >= 32 {
            return Err(Error::Codec("varint overflow".into()));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde::Deserialize;
    use std::path::PathBuf;

    #[test]
    fn empty_is_version_only() {
        let blob = encode_lines(&[]);
        assert_eq!(blob, vec![COVERAGE_VERSION]);
        assert_eq!(decode_lines(&blob).unwrap(), Vec::<u32>::new());
    }

    #[test]
    fn roundtrip_sparse() {
        let lines = vec![1, 2, 3, 10, 100, 1000];
        let blob = encode_lines(&lines);
        assert_eq!(decode_lines(&blob).unwrap(), lines);
    }

    #[test]
    fn dedups_and_sorts() {
        let blob = encode_lines(&[5, 1, 5, 3, 1]);
        assert_eq!(decode_lines(&blob).unwrap(), vec![1, 3, 5]);
    }

    #[derive(Deserialize)]
    struct Vector {
        #[serde(default)]
        lines: Vec<u32>,
        hex: String,
        #[serde(default)]
        error: Option<String>,
    }

    #[test]
    fn shared_vectors() {
        let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../tests/fixtures/coverage_vectors.json");
        let data = std::fs::read_to_string(&path).expect("coverage_vectors.json");
        let vectors: Vec<Vector> = serde_json::from_str(&data).unwrap();
        for v in vectors {
            let blob = hex::decode(&v.hex).expect("hex");
            if let Some(kind) = &v.error {
                let err = decode_lines(&blob).unwrap_err();
                assert!(
                    format!("{err}").contains(kind.as_str())
                        || matches!(
                            err,
                            Error::Codec(ref m) if m.contains(kind.as_str()) || m.contains("varint")
                        ),
                    "{err} should mention {kind}"
                );
                continue;
            }
            let encoded = encode_lines(&v.lines);
            assert_eq!(
                hex::encode(&encoded),
                v.hex,
                "encode mismatch for {:?}",
                v.lines
            );
            assert_eq!(decode_lines(&blob).unwrap(), {
                let mut s = v.lines.clone();
                s.sort_unstable();
                s.dedup();
                s
            });
        }
    }

    #[test]
    fn varint_overflow_errors() {
        // Version + continuation bytes that would overflow u32.
        let blob = vec![COVERAGE_VERSION, 0xff, 0xff, 0xff, 0xff, 0xff];
        let err = decode_lines(&blob).unwrap_err();
        assert!(
            matches!(err, Error::Codec(ref m) if m.contains("overflow") || m.contains("varint")),
            "{err}"
        );
    }
}
