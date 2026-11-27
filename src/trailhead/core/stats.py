"""Performance statistics (PLAN §6.3). The trade unit is a closed lot (optionally a closed position)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum

from .money import ZERO, dsum, safe_div
from .positions import Lot, Position


class PeriodKey(StrEnum):
    MTD = "MTD"
    QTD = "QTD"
    YTD = "YTD"
    L12M = "L12M"
    ALL = "ALL"
    CUSTOM = "CUSTOM"


PERIOD_LABELS = {
    PeriodKey.MTD: "MTD", PeriodKey.QTD: "QTD", PeriodKey.YTD: "YTD",
    PeriodKey.L12M: "Last 12 months", PeriodKey.ALL: "All", PeriodKey.CUSTOM: "Custom…",
}


@dataclass(frozen=True)
class Period:
    start: date | None = None
    end: date | None = None

    def contains(self, d: date | None) -> bool:
        if d is None:
            return False
        return (self.start is None or d >= self.start) and (self.end is None or d <= self.end)


def period_for(key: PeriodKey | str, today: date, custom: tuple[date | None, date | None] | None = None) -> Period:
    key = PeriodKey(key)
    if key == PeriodKey.MTD:
        return Period(today.replace(day=1), today)
    if key == PeriodKey.QTD:
        q_month = 3 * ((today.month - 1) // 3) + 1
        return Period(today.replace(month=q_month, day=1), today)
    if key == PeriodKey.YTD:
        return Period(today.replace(month=1, day=1), today)
    if key == PeriodKey.L12M:
        try:
            start = today.replace(year=today.year - 1) + timedelta(days=1)
        except ValueError:  # Feb 29
            start = today.replace(year=today.year - 1, day=28) + timedelta(days=1)
        return Period(start, today)
    if key == PeriodKey.CUSTOM and custom:
        return Period(custom[0], custom[1])
    return Period()


@dataclass(frozen=True)
class TradeUnit:
    """One closed trade: realized P/L and realized R."""

    pl: Decimal
    r: Decimal | None
    close_date: date | None
    label: str = ""


@dataclass(frozen=True)
class TradeStats:
    n: int
    wins: int
    losses: int
    win_pct: Decimal | None
    profit_factor: Decimal | None  # None ("—") when there are no losses
    expectancy: Decimal | None  # mean realized R
    stdev: Decimal | None  # sample stdev of R (n−1); None when n < 2
    r_over_stdev: Decimal | None
    sqn: Decimal | None  # Van Tharp's System Quality Number: √min(N, 100) × expectancy ÷ stdev
    gross_profit: Decimal
    gross_loss: Decimal
    total_pl: Decimal
    avg_win: Decimal | None
    avg_loss: Decimal | None


def closed_lot_units(lots: Iterable[Lot], period: Period | None = None) -> list[TradeUnit]:
    out = []
    for lot in lots:
        if not lot.is_closed:
            continue
        if period is not None and not period.contains(lot.close_date):
            continue
        out.append(TradeUnit(lot.realized_pl, lot.realized_r, lot.close_date, f"{lot.symbol} #{lot.no}"))
    return out


def closed_position_units(positions: Iterable[Position], period: Period | None = None) -> list[TradeUnit]:
    out = []
    for p in positions:
        if not p.is_closed:
            continue
        if period is not None and not period.contains(p.close_date):
            continue
        out.append(TradeUnit(p.realized_pl, p.realized_r, p.close_date, p.symbol))
    return out


SQN_N_CAP = 100  # Tharp caps N so a long record doesn't inflate the score
SQN_MIN_TRADES = 30  # Tharp: fewer trades than this and the SQN isn't reliable
# (lower bound, rating), checked from the top. Tharp's scale; below 0 the system loses money.
SQN_RATINGS: list[tuple[Decimal, str]] = [
    (Decimal(7), "Holy grail"), (Decimal(5), "Superb"), (Decimal(3), "Excellent"), (Decimal("2.5"), "Good"),
    (Decimal(2), "Average"), (Decimal("1.6"), "Below avg"), (Decimal(0), "Poor"),
]


def _sqn(expectancy: Decimal | None, stdev: Decimal | None, n: int) -> Decimal | None:
    if expectancy is None or not stdev:
        return None
    return Decimal(min(n, SQN_N_CAP)).sqrt() * expectancy / stdev


def sqn_rating(sqn: Decimal | None) -> str | None:
    if sqn is None:
        return None
    for bound, name in SQN_RATINGS:
        if sqn >= bound:
            return name
    return "Losing"


def trade_stats(units: Sequence[TradeUnit]) -> TradeStats:
    wins = [u for u in units if u.pl > 0]
    losses = [u for u in units if u.pl < 0]
    gp = dsum(u.pl for u in wins)
    gl = dsum(u.pl for u in losses)
    decided = len(wins) + len(losses)
    rs = [u.r for u in units if u.r is not None]
    n = len(rs)
    expectancy = dsum(rs) / n if n else None
    stdev = None
    if n >= 2 and expectancy is not None:
        var = dsum((r - expectancy) ** 2 for r in rs) / (n - 1)
        stdev = var.sqrt()
    return TradeStats(
        n=len(units),
        wins=len(wins),
        losses=len(losses),
        win_pct=safe_div(Decimal(len(wins)), Decimal(decided)) if decided else None,
        profit_factor=safe_div(gp, abs(gl)) if losses else None,
        expectancy=expectancy,
        stdev=stdev,
        r_over_stdev=safe_div(expectancy, stdev),
        sqn=_sqn(expectancy, stdev, n),
        gross_profit=gp,
        gross_loss=gl,
        total_pl=dsum(u.pl for u in units),
        avg_win=gp / len(wins) if wins else None,
        avg_loss=gl / len(losses) if losses else None,
    )


def realized_in_period(lots: Iterable[Lot], period: Period | None = None) -> tuple[Decimal, int]:
    """Σ realized P/L of all sells dated in the period, and the number of sells."""
    total, n = ZERO, 0
    for lot in lots:
        for s in lot.sells:
            if period is None or period.contains(s.trade_date):
                total += s.shares * (s.price - lot.entry)
                n += 1
    return total, n


def r_histogram(rs: Iterable[Decimal], bin_width: Decimal = Decimal("0.5")) -> dict[Decimal, int]:
    """Bin realized R values into bins of bin_width (key = bin lower edge)."""
    out: dict[Decimal, int] = {}
    for r in rs:
        edge = (r / bin_width).to_integral_value(rounding="ROUND_FLOOR") * bin_width
        out[edge] = out.get(edge, 0) + 1
    return dict(sorted(out.items()))
