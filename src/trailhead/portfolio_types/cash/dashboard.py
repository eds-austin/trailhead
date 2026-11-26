"""Cash → DashboardSummary (pure: no Qt, no SQL)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from ...core.cash import TRUE_UP_STALE_DAYS, CashSnapshot
from ...core.models import CashTxnKind, Portfolio
from ...core.money import ZERO
from ...core.stats import Period
from ...ui import format as fmt
from ..base import AttentionItem, DashboardSummary


def _when(d: date) -> str:
    return f"{d:%b} {d.day}"


def attention_items(snap: CashSnapshot) -> list[AttentionItem]:
    out: list[AttentionItem] = []
    for a in snap.open_accounts:
        if a.balance < 0:
            out.append(AttentionItem("negative", f"{a.account.name}: balance below zero",
                                     f"{fmt.money(a.balance)} · record a deposit or true it up", (0, a.account.name)))
    props = snap.proposals()
    if len(props) >= 3:
        total = sum((p.amount for p in props), ZERO)
        out.append(AttentionItem("info", f"{len(props)} interest payments to confirm · {fmt.money(total)}",
                                 "Cash accounts", (5,)))
    else:
        for p in props:
            a = snap.get(p.account_id)
            name = a.account.name if a else "Cash"
            out.append(AttentionItem("info", f"{name}: confirm interest ~{fmt.money(p.amount)}",
                                     f"Paid {_when(p.period_end)}", (5, p.period_end, name)))
    for a in snap.stale_true_ups():
        t = a.last_true_up
        when = f"last {_when(t.txn_date)}" if t else "never trued up"
        out.append(AttentionItem("warning", f"{a.account.name}: true up the balance",
                                 f"{when} · over {TRUE_UP_STALE_DAYS} days", (3, a.account.name)))
    return out


def interest_curve(snap: CashSnapshot, period: Period) -> list[tuple[date, Decimal]]:
    """Cumulative interest received within the period, one point per payment day (0 at the start)."""
    by_day: dict[date, Decimal] = {}
    for a in snap.accounts:
        for f in a.flows:
            if f.kind == CashTxnKind.INTEREST and period.contains(f.date) and f.date <= snap.today:
                by_day[f.date] = by_day.get(f.date, ZERO) + f.amount
    if not by_day:
        return []
    start = period.start or min(by_day)
    out, total = [(start, ZERO)], ZERO
    for d in sorted(by_day):
        total += by_day[d]
        out.append((d, total))
    if out[-1][0] < snap.today:
        out.append((snap.today, total))
    return out


def build_summary(portfolio: Portfolio, snap: CashSnapshot, period: Period) -> DashboardSummary:
    n = len(snap.open_accounts)
    sub = f"{n} account{'s' if n != 1 else ''}" if n else "No accounts"
    apy = snap.weighted_apy
    if n and apy is not None:
        sub += f" · {fmt.pct(apy, 2)} APY"
    interest = snap.interest_in(period)
    return DashboardSummary(
        portfolio=portfolio,
        subtitle=sub,
        value=snap.balance,
        day_change=None,
        period_pl=interest,
        unrealized_pl=None,
        income=interest,
        open_risk=None,
        pl_curve=interest_curve(snap, period),
        holdings=[],
        attention=attention_items(snap),
    )
