import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from calc import add, clamp, div, mul, parity, sub  # noqa: E402


def test_add():
    assert add(2, 3) == 5


def test_sub():
    assert sub(7, 4) == 3


def test_mul():
    assert mul(3, 4) == 12


def test_div():
    assert div(10, 2) == 5


def test_clamp_low():
    assert clamp(-5, 0, 10) == 0


def test_clamp_high():
    assert clamp(99, 0, 10) == 10


def test_parity_even():
    assert parity(4) == 0


def test_parity_odd():
    assert parity(5) == 1
