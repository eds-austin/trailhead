"""Cross-portfolio aggregation for the Dashboard (pure: no Qt, no SQL)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from ..core.models import Portfolio
from .base import AttentionItem, DashboardSummary, Holding

ZERO = Decimal(0)
TONE_ORDER = {"negative": 0, "warning": 1, "info": 2, "positive": 3}


def _sum_known(vals) -> Decimal | None:
    known = [v for v in vals if v is not None]
    return sum(known, ZERO) if known else None


@dataclass(frozen=True)
class Totals:
    count: int
    value: Decimal
    day_change: Decimal | None
    period_pl: Decimal | None
    unrealized_pl: Decimal | None
    income: Decimal
    open_risk: Decimal | None
    open_risk_warn: bool

    @property
    def day_change_pct(self) -> Decimal | None:
        if self.day_change is None:
            return None
        prev = self.value - self.day_change
        return self.day_change / prev if prev else None


def totals(rows: Sequence[DashboardSummary]) -> Totals:
    return Totals(
        count=len(rows),
        value=sum((r.value for r in rows), ZERO),
        day_change=_sum_known(r.day_change for r in rows),
        period_pl=_sum_known(r.period_pl for r in rows),
        unrealized_pl=_sum_known(r.unrealized_pl for r in rows),
        income=sum((r.income for r in rows), ZERO),
        open_risk=_sum_known(r.open_risk for r in rows),
        open_risk_warn=any(r.open_risk_warn for r in rows),
    )


def share(row: DashboardSummary, total_value: Decimal) -> Decimal | None:
    return row.value / total_value if total_value else None


def combine_curves(curves: Sequence[Sequence[tuple[date, Decimal]]]) -> list[tuple[date, Decimal]]:
    """Sum cumulative curves on the union of their dates. Each curve is 0 before its first point and carries its
    last value forward after its last point (or across days it has no point for)."""
    curves = [c for c in curves if c]
    if not curves:
        return []
    days = sorted({d for c in curves for d, _ in c})
    maps = [dict(c) for c in curves]
    last = [ZERO] * len(curves)
    out = []
    for d in days:
        for i, m in enumerate(maps):
            if d in m:
                last[i] = m[d]
        out.append((d, sum(last, ZERO)))
    return out


def top_moves(rows: Sequence[DashboardSummary], n: int = 5) -> list[tuple[Holding, Portfolio]]:
    """The n holdings with the largest % move today (either direction), listed gainers first."""
    moved = [(h, r.portfolio) for r in rows for h in r.holdings if h.day_pct is not None and h.day_pct != 0]
    moved.sort(key=lambda x: abs(x[0].day_pct), reverse=True)  # type: ignore[arg-type]
    return sorted(moved[:n], key=lambda x: x[0].day_pct, reverse=True)  # type: ignore[arg-type,return-value]


def sector_allocation(rows: Sequence[DashboardSummary]) -> tuple[list[tuple[str, Decimal]], list[str]]:
    """(sector, market value) largest first over every holding, and the names of the portfolios included."""
    by_sector: dict[str, Decimal] = defaultdict(lambda: ZERO)
    names: list[str] = []
    for r in rows:
        if not r.holdings:
            continue
        names.append(r.portfolio.name)
        for h in r.holdings:
            by_sector[h.sector or "Unknown"] += h.market_value
    items = sorted(((s, v) for s, v in by_sector.items() if v > 0), key=lambda x: x[1], reverse=True)
    return items, names


def attention(rows: Sequence[DashboardSummary]) -> list[tuple[AttentionItem, Portfolio]]:
    items = [(a, r.portfolio) for r in rows for a in r.attention]
    items.sort(key=lambda x: (TONE_ORDER.get(x[0].tone, 9), x[0].sort_key, x[1].sort_order, x[1].id or 0))
    return items
