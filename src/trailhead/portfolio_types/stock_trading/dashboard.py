"""Stock-trading portfolio → DashboardSummary (pure: no Qt, no SQL)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from ...core.equity import PLPoint
from ...core.models import CashKind
from ...core.r_engine import ActionKind
from ...core.snapshot import PortfolioSnapshot
from ...core.stats import Period
from ...ui import format as fmt
from ..base import AttentionItem, DashboardSummary, Holding

EARNINGS_HORIZON_DAYS = 60  # "coming up" window on the Dashboard


def _when(d: date) -> str:
    return f"{d:%b} {d.day}"


def attention_items(snap: PortfolioSnapshot, open_risk_warn: Decimal,
                    earnings_days: int = EARNINGS_HORIZON_DAYS) -> list[AttentionItem]:
    out: list[AttentionItem] = []
    open_pos = snap.open_positions
    n_actions = 0
    for pv in open_pos:
        a = pv.top_action
        if a is None:
            continue
        n_actions += 1
        if a.kind == ActionKind.STOP_HIT:
            out.append(AttentionItem("negative", f"{pv.symbol}: stop hit", f"Sell {fmt.shares(a.shares)} sh",
                                     (0, pv.symbol)))
        elif a.kind == ActionKind.SELL_TRANCHE:
            what = f"{a.milestone} target reached" if a.milestone else "Target reached"
            out.append(AttentionItem("warning", f"{pv.symbol}: sell {fmt.shares(a.shares)} sh", what,
                                     (1, pv.symbol)))
        else:
            out.append(AttentionItem("warning", f"{pv.symbol}: raise stop to {fmt.price(a.price)}",
                                     "Ladder stop moved up", (2, pv.symbol)))
    risk_pct = snap.open_risk_pct
    if risk_pct is not None and risk_pct > open_risk_warn:
        out.append(AttentionItem("warning", f"Open risk {fmt.pct(risk_pct)} is above your {fmt.pct(open_risk_warn)}"
                                 " limit", "Raise stops or trim", (3,)))
    stale = snap.stale_symbols
    if stale:
        out.append(AttentionItem("warning", f"No price for {', '.join(stale)}", "Refresh prices or set one manually",
                                 (4,)))
    soon = sorted((pv.market.next_earnings, pv.symbol) for pv in open_pos
                  if pv.earnings_soon_days is not None and 0 <= pv.earnings_soon_days <= earnings_days)
    for d, sym in soon:
        days = (d - snap.today).days
        rel = "today" if days == 0 else "tomorrow" if days == 1 else f"in {days} days"
        out.append(AttentionItem("info", f"{sym}: earnings {_when(d)}", rel, (5, d, sym)))
    if open_pos and n_actions == 0:
        n = len(open_pos)
        out.append(AttentionItem("positive", "No sells or stop raises due",
                                 f"All {n} position{'s' if n != 1 else ''} on plan", (6,)))
    return out


def build_summary(snap: PortfolioSnapshot, curve: Sequence[PLPoint], period: Period,
                  open_risk_warn: Decimal = Decimal("0.06"),
                  earnings_days: int = EARNINGS_HORIZON_DAYS) -> DashboardSummary:
    open_pos = snap.open_positions
    has_prices = any(p.last is not None for p in open_pos)
    income = sum((e.amount for e in snap.cash_events
                  if e.kind == CashKind.DIVIDEND and period.contains(e.event_date)), Decimal(0))
    risk_pct = snap.open_risk_pct
    n = len(open_pos)
    return DashboardSummary(
        portfolio=snap.portfolio,
        subtitle=f"{n} open position{'s' if n != 1 else ''}" if n else "No open positions",
        value=snap.value,
        day_change=snap.day_change if open_pos else None,
        period_pl=curve[-1].total if curve else Decimal(0),
        unrealized_pl=snap.unrealized_pl if has_prices else None,
        income=income,
        open_risk=snap.open_risk if open_pos else None,
        open_risk_pct=risk_pct if open_pos else None,
        open_risk_warn=risk_pct is not None and risk_pct > open_risk_warn,
        pl_curve=[(p.date, p.total) for p in curve],
        holdings=[Holding(pv.symbol, pv.market_value, pv.day_change, pv.day_pct, pv.market.sector)
                  for pv in open_pos],
        attention=attention_items(snap, open_risk_warn, earnings_days),
    )
