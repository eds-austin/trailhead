"""Cumulative P/L curve for a stock-trading portfolio (PLAN §7.5, revised). Pure Python.

A stock-trading portfolio tracks positions, not an account balance, so the curve shows
realized + unrealized P/L + dividends over time instead of cash + market value.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from .models import CashEvent, CashKind, Side, Trade
from .money import ZERO


@dataclass(frozen=True)
class PLPoint:
    date: date
    total: Decimal  # realized + unrealized + dividends
    realized: Decimal
    unrealized: Decimal
    dividends: Decimal
    market_value: Decimal


def pl_curve(trades: Sequence[Trade], cash_events: Sequence[CashEvent], closes: Mapping[str, Mapping[date, Decimal]],
             end: date, start: date | None = None, last_prices: Mapping[str, Decimal] | None = None,
             weekdays_only: bool = True, basis_cuts: Sequence = ()) -> list[PLPoint]:
    """Daily cumulative P/L. Unrealized uses each day's close (carried forward; a symbol with no close yet is
    valued at cost). On `end`, `last_prices` override the close. With `start`, values are rebased so the curve
    shows P/L earned within the period (first point = 0). `basis_cuts` (core.spinoffs.BasisCut) move cost out of a
    parent on a spin-off's ex-date; the new company's lot is a BUY that day."""
    if not trades:
        return []
    entry = {t.id: t.price for t in trades if t.side == Side.BUY}
    by_day: dict[date, list[Trade]] = defaultdict(list)
    for t in sorted(trades, key=lambda t: (t.trade_date, t.side != Side.BUY, t.id or 0)):
        by_day[t.trade_date].append(t)
    divs_by_day: dict[date, Decimal] = defaultdict(lambda: ZERO)
    for e in cash_events:
        if e.kind == CashKind.DIVIDEND:
            divs_by_day[e.event_date] += e.amount
    first = min(min(by_day), min(divs_by_day, default=min(by_day)))
    shares: dict[str, Decimal] = defaultdict(lambda: ZERO)
    cost: dict[str, Decimal] = defaultdict(lambda: ZERO)
    last_close: dict[str, Decimal] = {}
    cuts_by_day: dict[date, list] = defaultdict(list)
    for c in basis_cuts:
        cuts_by_day[c.on].append(c)
    cut_entry: dict[int, Decimal] = defaultdict(lambda: ZERO)
    realized = dividends = ZERO
    base: PLPoint | None = None
    out: list[PLPoint] = []
    d = first
    while d <= end:
        for c in cuts_by_day.get(d, ()):
            cost[c.symbol] -= c.amount
            cut_entry[c.lot_id] += c.per_share
        for t in by_day.get(d, ()):
            if t.side == Side.BUY:
                shares[t.symbol] += t.shares
                cost[t.symbol] += t.shares * t.price
            else:
                e = entry.get(t.lot_id, t.price) - cut_entry[t.lot_id]  # type: ignore[index]
                shares[t.symbol] -= t.shares
                cost[t.symbol] -= t.shares * e
                realized += t.shares * (t.price - e)
        dividends += divs_by_day.get(d, ZERO)
        for sym in shares:
            c = closes.get(sym, {}).get(d)
            if c is not None:
                last_close[sym] = c
        if d == end and last_prices:
            for sym, px in last_prices.items():
                if sym in shares:
                    last_close[sym] = px
        mv = ZERO
        open_cost = ZERO
        for sym, q in shares.items():
            if q <= 0:
                continue
            px = last_close.get(sym)
            mv += q * px if px is not None else cost[sym]
            open_cost += cost[sym]
        unreal = mv - open_cost
        pt = PLPoint(d, realized + unreal + dividends, realized, unreal, dividends, mv)
        if start is not None and d < start:
            base = pt
        elif not weekdays_only or d.weekday() < 5 or d == end:
            out.append(pt)
        d += timedelta(days=1)
    if base is not None:
        out = [PLPoint(p.date, p.total - base.total, p.realized - base.realized, p.unrealized - base.unrealized,
                       p.dividends - base.dividends, p.market_value) for p in out]
    return out
