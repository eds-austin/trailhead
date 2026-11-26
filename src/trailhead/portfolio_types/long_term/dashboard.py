"""Long-term Holdings → DashboardSummary (pure: no Qt, no SQL)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from ...core.equity import PLPoint
from ...core.holdings import HoldingsSnapshot
from ...core.metals import display_symbol
from ...core.stats import Period
from ...ui import format as fmt
from ..base import AttentionItem, DashboardSummary, Holding

EX_DIV_HORIZON_DAYS = 14  # "coming up" window for ex-dividend dates on the Dashboard


def _when(d: date) -> str:
    return f"{d:%b} {d.day}"


def attention_items(snap: HoldingsSnapshot, pending: Sequence = (),
                    ex_div_days: int = EX_DIV_HORIZON_DAYS) -> list[AttentionItem]:
    out: list[AttentionItem] = []
    hits = 0
    for h in snap.open_holdings:
        if h.stop_hit:
            hits += 1
            out.append(AttentionItem("negative", f"{display_symbol(h.symbol)}: stop hit",
                                     f"Last {fmt.price(h.last)} ≤ stop {fmt.price(h.stop)}", (0, h.symbol)))
        elif h.target_hit:
            hits += 1
            out.append(AttentionItem("warning", f"{display_symbol(h.symbol)}: target reached",
                                     f"Last {fmt.price(h.last)} ≥ target {fmt.price(h.target)}", (1, h.symbol)))
    stale = snap.stale_symbols
    if stale:
        names = ", ".join(display_symbol(x) for x in stale)
        out.append(AttentionItem("warning", f"No price for {names}", "Refresh prices or set one manually",
                                 (4,)))
    if len(pending) >= 3:  # one line, not a wall of them
        total = sum((d.amount for d in pending), Decimal(0))
        syms = sorted({d.symbol for d in pending})
        out.append(AttentionItem("info", f"{len(pending)} dividends to confirm · {fmt.money(total)}",
                                 ", ".join(syms[:6]) + (" …" if len(syms) > 6 else ""), (5,)))
    else:
        for d in pending:
            out.append(AttentionItem("info", f"{d.symbol}: confirm dividend {fmt.money(d.amount)}",
                                     f"Ex-date {_when(d.ex_date)}", (5, d.ex_date, d.symbol)))
    for h, nd in snap.upcoming_dividends(ex_div_days):
        if nd.ex_date is None or nd.estimated:
            continue
        days = (nd.ex_date - snap.today).days
        rel = "today" if days == 0 else "tomorrow" if days == 1 else f"in {days} days"
        out.append(AttentionItem("info", f"{h.symbol}: ex-dividend {_when(nd.ex_date)}", rel,
                                 (6, nd.ex_date, h.symbol)))
    if snap.open_holdings and hits == 0 and not stale:
        n = len(snap.open_holdings)
        out.append(AttentionItem("positive", "No stops or targets hit",
                                 f"All {n} holding{'s' if n != 1 else ''} on plan", (7,)))
    return out


def build_summary(snap: HoldingsSnapshot, curve: Sequence[PLPoint], period: Period,
                  pending: Sequence = ()) -> DashboardSummary:
    open_h = snap.open_holdings
    n = len(open_h)
    has_prices = any(h.last is not None for h in open_h)
    income, _ = snap.income_between(period.start, period.end)
    sub = f"{n} holding{'s' if n != 1 else ''}" if n else "No holdings"
    if n and snap.est_income:
        sub += f" · {fmt.compact_money(snap.est_income)}/yr income"
    return DashboardSummary(
        portfolio=snap.portfolio,
        subtitle=sub,
        value=snap.value,
        day_change=snap.day_change if open_h else None,
        period_pl=curve[-1].total if curve else Decimal(0),
        unrealized_pl=snap.unrealized_pl if has_prices else None,
        income=income,
        open_risk=None,  # no R / stop-based risk budget in a long-term portfolio
        pl_curve=[(p.date, p.total) for p in curve],
        holdings=[Holding(display_symbol(h.symbol), h.market_value, h.day_change, h.day_pct,
                          h.market.sector or h.asset_type)
                  for h in open_h],
        attention=attention_items(snap, pending),
    )
