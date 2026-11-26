"""Decimal helpers. All domain math uses Decimal; floats are converted via str()."""

from __future__ import annotations

import math
from decimal import ROUND_DOWN, ROUND_FLOOR, ROUND_HALF_UP, Decimal, InvalidOperation

ZERO = Decimal(0)
ONE = Decimal(1)
CENT = Decimal("0.01")


def D(value) -> Decimal:  # noqa: N802 - conventional short name
    """Convert int/str/float/Decimal to Decimal. Floats go through repr to avoid binary noise."""
    if isinstance(value, Decimal):
        return value
    if value is None:
        raise ValueError("None is not a number")
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise ValueError(f"Not a finite number: {value}")
        return Decimal(repr(value))
    if isinstance(value, bool):
        raise TypeError("bool is not a number")
    try:
        return Decimal(str(value).strip())
    except InvalidOperation as e:
        raise ValueError(f"Not a number: {value!r}") from e


def D_or_none(value) -> Decimal | None:  # noqa: N802
    if value is None or value == "":
        return None
    try:
        return D(value)
    except (ValueError, TypeError):
        return None


def cents(v: Decimal) -> Decimal:
    return v.quantize(CENT, rounding=ROUND_HALF_UP)


def round_to(v: Decimal, places: int) -> Decimal:
    return v.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def floor_int(v: Decimal) -> int:
    return int(v.to_integral_value(rounding=ROUND_FLOOR))


def trunc_int(v: Decimal) -> int:
    return int(v.to_integral_value(rounding=ROUND_DOWN))


def safe_div(a: Decimal | None, b: Decimal | None) -> Decimal | None:
    """a / b, or None when either is missing or b is zero (never #DIV/0!)."""
    if a is None or b is None or b == 0:
        return None
    return a / b


def dsum(values) -> Decimal:
    total = ZERO
    for v in values:
        total += v
    return total
