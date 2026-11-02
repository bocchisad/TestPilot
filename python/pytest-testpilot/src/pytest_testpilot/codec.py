"""Coverage line blob: 0x01 + delta-encoded unsigned varints of sorted unique u32s."""

from __future__ import annotations

COVERAGE_VERSION = 0x01


def encode_lines(lines: list[int] | tuple[int, ...]) -> bytes:
    sorted_unique = sorted({int(x) for x in lines})
    out = bytearray([COVERAGE_VERSION])
    prev = 0
    for i, line in enumerate(sorted_unique):
        delta = line if i == 0 else line - prev
        _write_varint(out, delta)
        prev = line
    return bytes(out)


def decode_lines(blob: bytes) -> list[int]:
    if not blob:
        raise ValueError("empty blob")
    if blob[0] != COVERAGE_VERSION:
        raise ValueError(f"unsupported coverage version {blob[0]:#x}")
    if len(blob) == 1:
        return []
    lines: list[int] = []
    prev = 0
    i = 1
    first = True
    while i < len(blob):
        delta, i = _read_varint(blob, i)
        line = delta if first else prev + delta
        first = False
        lines.append(line)
        prev = line
    return lines


def _write_varint(out: bytearray, value: int) -> None:
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            break


def _read_varint(buf: bytes, i: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        if i >= len(buf):
            raise ValueError("truncated varint")
        byte = buf[i]
        i += 1
        if shift >= 28 and (byte & 0x80 or (shift == 28 and byte > 0x0F)):
            raise ValueError("varint overflow")
        if shift >= 32:
            raise ValueError("varint overflow")
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return result, i
        shift += 7
        if shift >= 32:
            raise ValueError("varint overflow")
