import textwrap
from pathlib import Path

import pytest

PRICING = '''
def apply_discount(price, pct):
    if pct < 0 or pct > 100:
        raise ValueError("pct out of range")
    return price - price * pct / 100


def bulk_price(qty, unit):
    if qty >= 10:
        return qty * unit * 0.9
    return qty * unit
'''
CART = '''
from shop.pricing import bulk_price


def line_total(qty, unit):
    return round(bulk_price(qty, unit), 2)
'''
STATS = '''
def clamp(x, lo, hi):
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x
'''
STRONG = '''
import pytest
from shop.pricing import apply_discount, bulk_price


def test_discount_values():
    assert apply_discount(100, 0) == 100
    assert apply_discount(200, 50) == 100
    assert apply_discount(100, 100) == 0


def test_discount_range():
    with pytest.raises(ValueError):
        apply_discount(100, -1)
    with pytest.raises(ValueError):
        apply_discount(100, 101)


def test_bulk_threshold():
    assert bulk_price(9, 10) == 90
    assert bulk_price(10, 10) == 90.0
    assert bulk_price(0, 10) == 0
'''
WEAK = '''
from shop.pricing import apply_discount, bulk_price


def test_discount_runs():
    assert apply_discount(100, 10)


def test_bulk_runs():
    assert bulk_price(1, 1)
'''
CART_TEST = '''
from shop.cart import line_total


def test_line_total():
    assert line_total(2, 5) == 10
'''


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(text).lstrip("\n"))


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    write(root, "src/shop/__init__.py", "")
    write(root, "src/shop/pricing.py", PRICING)
    write(root, "src/shop/cart.py", CART)
    write(root, "src/shop/stats.py", STATS)
    write(root, "tests/test_pricing.py", STRONG)
    write(root, "tests/test_cart.py", CART_TEST)
    return root
