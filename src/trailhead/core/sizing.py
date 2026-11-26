"""Position sizing and trade warnings (PLAN §6.4)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from .money import floor_int

RISK_TOLERANCE = Decimal("1.05")


class WarningCode(StrEnum):
    EXCEEDS_MAX = "EXCEEDS_MAX"
    EXCEEDS_RISK = "EXCEEDS_RISK"
    STOP_INVALID = "STOP_INVALID"


@dataclass(frozen=True)
class SizingWarning:
    code: WarningCode
    message: str
    blocking: bool = False


def risk_per_trade(max_amount: Decimal, risk_pct: Decimal) -> Decimal:
    return max_amount * risk_pct


def suggested_shares(risk: Decimal, price: Decimal, stop: Decimal) -> int:
    """floor(risk_per_trade / (price − stop)); 0 when the stop is invalid."""
    r = price - stop
    if r <= 0 or risk <= 0:
        return 0
    return floor_int(risk / r)


@dataclass(frozen=True)
class BuyCheck:
    r_per_share: Decimal | None
    risk_dollars: Decimal | None
    risk_pct_of_max: Decimal | None
    cost: Decimal
    deployed_after: Decimal
    warnings: list[SizingWarning]

    @property
    def blocking(self) -> bool:
        return any(w.blocking for w in self.warnings)


def _money(v: Decimal) -> str:
    return f"${v:,.0f}" if abs(v) >= 1000 else f"${v:,.2f}"


def check_buy(*, price: Decimal, stop: Decimal | None, shares: Decimal, deployed: Decimal, max_amount: Decimal,
              risk_per_trade: Decimal) -> BuyCheck:
    """Sizing warnings for a BUY. Available cash is deliberately not checked (a trade may exceed cash)."""
    warnings: list[SizingWarning] = []
    cost = shares * price
    r = None if stop is None else price - stop
    risk = None if r is None else shares * r
    if stop is not None and stop >= price:
        warnings.append(SizingWarning(WarningCode.STOP_INVALID, "Initial stop must be below the price", True))
    deployed_after = deployed + cost
    if deployed_after > max_amount:
        warnings.append(SizingWarning(
            WarningCode.EXCEEDS_MAX,
            f"Deployed would be {_money(deployed_after)} > max {_money(max_amount)}"))
    if risk is not None and r is not None and r > 0 and risk > risk_per_trade * RISK_TOLERANCE:
        warnings.append(SizingWarning(
            WarningCode.EXCEEDS_RISK, f"Risk {_money(risk)} exceeds the per-trade budget {_money(risk_per_trade)}"))
    risk_pct = risk / max_amount if risk is not None and max_amount > 0 else None
    return BuyCheck(r, risk, risk_pct, cost, deployed_after, warnings)
