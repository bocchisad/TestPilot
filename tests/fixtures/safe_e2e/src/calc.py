"""Tiny module for safe-mode e2e (regression injected on second commit)."""


def add(a: int, b: int) -> int:
    return a + b


def sub(a: int, b: int) -> int:
    return a - b


def mul(a: int, b: int) -> int:
    return a * b


def div(a: int, b: int) -> int:
    return a // b


def clamp(x: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, x))


def parity(n: int) -> int:
    return n % 2

