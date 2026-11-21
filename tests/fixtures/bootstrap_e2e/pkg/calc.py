def add(a: int, b: int) -> int:
    return a + b


def mul(a: int, b: int) -> int:
    return a * b


def clamp(x: int, lo: int = 0, hi: int = 100) -> int:
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x
