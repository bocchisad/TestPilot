from pkg.calc import add, mul, clamp


def test_add():
    assert add(2, 3) == 5


def test_mul():
    assert mul(2, 4) == 8


def test_clamp():
    assert clamp(-1) == 0
    assert clamp(50) == 50
    assert clamp(50, lo=10, hi=40) == 40
