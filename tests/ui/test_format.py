from decimal import Decimal

from trailhead.ui import format as fmt


def test_money():
    assert fmt.money(Decimal("12345.674")) == "$12,345.67"
    assert fmt.money(Decimal("-1234")) == "−$1,234.00"
    assert fmt.money(None) == "—"
    assert fmt.money(float("nan")) == "—"


def test_signed():
    assert fmt.signed_money(Decimal("153")) == "+$153.00"
    assert fmt.signed_money(Decimal("-48.2")) == "−$48.20"
    assert fmt.signed_money(Decimal("0")) == "$0.00"
    assert fmt.r_mult(Decimal("1.0479")) == "+1.05R"
    assert fmt.r_mult(Decimal("-0.42")) == "−0.42R"
    assert fmt.signed_pct(Decimal("0.032")) == "+3.2%"


def test_price_and_shares():
    assert fmt.price(Decimal("43.18")) == "43.18"
    assert fmt.price(Decimal("0.51234")) == "0.5123"
    assert fmt.shares(Decimal("1500")) == "1,500"
    assert fmt.pct(Decimal("0.0412"), 2) == "4.12%"
