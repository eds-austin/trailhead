"""Display formatting for numbers. Pure Python (no Qt) so it is easy to test.

Rules (DESIGN_SYSTEM.md §3): true minus sign U+2212, never parentheses; empty/undefined → em dash.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

DASH = "—"
MINUS = "−"

Num = Decimal | float | int | None


def _dec(v: Num) -> Decimal | None:
    if v is None:
        return None
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return None
        v = Decimal(repr(v))
    elif isinstance(v, int):
        v = Decimal(v)
    try:
        if v.is_nan() or v.is_infinite():
            return None
    except (AttributeError, InvalidOperation):
        return None
    return v


def _q(v: Decimal, places: int) -> Decimal:
    return v.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def _grouped(v: Decimal, places: int) -> str:
    q = _q(abs(v), places)
    return f"{q:,.{places}f}"


def _sign(v: Decimal, places: int, *, plus: bool) -> str:
    q = _q(v, places)
    if q < 0:
        return MINUS
    if q > 0 and plus:
        return "+"
    return ""


def money(v: Num, places: int = 2) -> str:
    """$12,345.67 / −$1,234.00."""
    d = _dec(v)
    if d is None:
        return DASH
    return f"{_sign(d, places, plus=False)}${_grouped(d, places)}"


def signed_money(v: Num, places: int = 2) -> str:
    """+$153.00 / −$48.20 / $0.00."""
    d = _dec(v)
    if d is None:
        return DASH
    return f"{_sign(d, places, plus=True)}${_grouped(d, places)}"


def compact_money(v: Num) -> str:
    """$1.2M / $12.3K for tight spaces (axis labels)."""
    d = _dec(v)
    if d is None:
        return DASH
    a = abs(d)
    s = MINUS if d < 0 else ""
    if a >= 1_000_000:
        return f"{s}${a / 1_000_000:.1f}M"
    if a >= 10_000:
        return f"{s}${a / 1000:.1f}K"
    return f"{s}${_grouped(a, 0)}"


def price_places(v: Num) -> int:
    d = _dec(v)
    return 4 if d is not None and abs(d) < 1 and d != 0 else 2


def price(v: Num) -> str:
    """2 decimals, 4 under $1. No currency symbol (column context)."""
    d = _dec(v)
    if d is None:
        return DASH
    places = price_places(d)
    return f"{_sign(d, places, plus=False)}{_grouped(d, places)}"


def number(v: Num, places: int = 2, *, signed: bool = False) -> str:
    d = _dec(v)
    if d is None:
        return DASH
    return f"{_sign(d, places, plus=signed)}{_grouped(d, places)}"


def pct(v: Num, places: int = 1) -> str:
    """Fraction → percent: 0.032 → 3.2%."""
    d = _dec(v)
    if d is None:
        return DASH
    d = d * 100
    return f"{_sign(d, places, plus=False)}{_grouped(d, places)}%"


def signed_pct(v: Num, places: int = 1) -> str:
    d = _dec(v)
    if d is None:
        return DASH
    d = d * 100
    return f"{_sign(d, places, plus=True)}{_grouped(d, places)}%"


def r_mult(v: Num, *, signed: bool = True) -> str:
    """+1.05R / −0.42R."""
    d = _dec(v)
    if d is None:
        return DASH
    return f"{_sign(d, 2, plus=signed)}{_grouped(d, 2)}R"


def shares(v: Num) -> str:
    d = _dec(v)
    if d is None:
        return DASH
    if d == d.to_integral_value():
        return f"{_sign(d, 0, plus=False)}{_grouped(d, 0)}"
    return f"{_sign(d, 4, plus=False)}{abs(d).normalize():,f}"


def ratio(v: Num, places: int = 2) -> str:
    return number(v, places)


def short_date(v: date | datetime | str | None) -> str:
    """MM-DD within the current year context is decided by the caller; here ISO short."""
    if v is None or v == "":
        return DASH
    if isinstance(v, str):
        v = date.fromisoformat(v[:10])
    return v.strftime("%Y-%m-%d")


def month_day(v: date | str | None) -> str:
    if v is None or v == "":
        return DASH
    if isinstance(v, str):
        v = date.fromisoformat(v[:10])
    return f"{v.month}/{v.day}"


def is_negative(v: Num) -> bool:
    d = _dec(v)
    return d is not None and d < 0


def sign_of(v: Num, places: int = 2) -> int:
    d = _dec(v)
    if d is None:
        return 0
    q = _q(d, places)
    return (q > 0) - (q < 0)
