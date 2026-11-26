"""Pure alert engine (PLAN §9): evaluate(snapshot, settings) → alerts. No Qt, no I/O."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from ..core.r_engine import ActionKind
from ..core.snapshot import PortfolioSnapshot
from ..ui import format as fmt


class AlertKind(StrEnum):
    STOP_HIT = "STOP_HIT"
    TARGET_REACHED = "TARGET_REACHED"
    NEAR_STOP = "NEAR_STOP"
    EARNINGS_SOON = "EARNINGS_SOON"


SEVERITY = {AlertKind.STOP_HIT: "critical", AlertKind.NEAR_STOP: "warning", AlertKind.TARGET_REACHED: "info",
            AlertKind.EARNINGS_SOON: "info"}
SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


@dataclass(frozen=True)
class AlertSettings:
    near_stop_pct: Decimal = Decimal("0.02")
    earnings_days: int = 7


@dataclass(frozen=True)
class Alert:
    kind: AlertKind
    portfolio_id: int
    symbol: str
    lot_id: int | None
    level: Decimal | None
    title: str
    message: str

    @property
    def severity(self) -> str:
        return SEVERITY[self.kind]


def date_level(d: date) -> Decimal:
    """Encode a date as a NUMERIC alert level (yyyymmdd) so dedup works per earnings date."""
    return Decimal(d.year * 10000 + d.month * 100 + d.day)


def evaluate(snap: PortfolioSnapshot, settings: AlertSettings = AlertSettings()) -> list[Alert]:
    pid = snap.portfolio.id
    assert pid is not None
    out: list[Alert] = []
    for pv in snap.open_positions:
        for lv in pv.open_views:
            lot, st, last = lv.lot, lv.state, lv.last
            stop = lot.current_stop
            if last is None:
                continue
            if last <= stop:
                out.append(Alert(
                    AlertKind.STOP_HIT, pid, pv.symbol, lot.id, stop, f"{pv.symbol} — Stop hit",
                    f"Last {fmt.price(last)} ≤ stop {fmt.price(stop)}. Sell remaining {fmt.shares(lv.remaining)} sh."))
            elif last <= stop * (1 + settings.near_stop_pct):
                out.append(Alert(
                    AlertKind.NEAR_STOP, pid, pv.symbol, lot.id, stop, f"{pv.symbol} — Near stop",
                    f"Last {fmt.price(last)} is within {fmt.pct(settings.near_stop_pct)} of stop {fmt.price(stop)}."))
            pending = [a for a in st.actions if a.kind in (ActionKind.SELL_TRANCHE, ActionKind.RAISE_STOP)]
            if st.reached.index >= 1 and pending:
                trigger = lot.ladder.trigger(st.reached)
                todo = []
                for a in pending:
                    if a.kind == ActionKind.SELL_TRANCHE:
                        todo.append(f"sell {fmt.shares(a.shares)} sh")
                    else:
                        todo.append(f"raise stop → {fmt.price(a.price)}")
                out.append(Alert(
                    AlertKind.TARGET_REACHED, pid, pv.symbol, lot.id, trigger,
                    f"{pv.symbol} — {st.reached.label} target reached",
                    f"Lot #{lot.no} reached {fmt.price(trigger)} ({st.reached.name}). Suggested: {', '.join(todo)}."))
        d = pv.market.next_earnings
        days = pv.earnings_soon_days
        if d is not None and days is not None and 0 <= days <= settings.earnings_days:
            when = "today" if days == 0 else "tomorrow" if days == 1 else f"in {days} days"
            out.append(Alert(AlertKind.EARNINGS_SOON, pid, pv.symbol, None, date_level(d),
                             f"{pv.symbol} — Earnings {when}", f"Earnings on {d.isoformat()} ({when})."))
    out.sort(key=lambda a: (SEVERITY_ORDER[a.severity], a.symbol))
    return out


def evaluate_holdings(snap, settings: AlertSettings = AlertSettings()) -> list[Alert]:
    """Long-term Holdings: optional stop and target levels (no ladder, no earnings alerts)."""
    pid = snap.portfolio.id
    assert pid is not None
    out: list[Alert] = []
    for h in snap.open_holdings:
        if h.last is None or not h.open_lots:
            continue
        lot_id = h.open_lots[0].id  # dedup per holding and level
        if h.stop_hit:
            out.append(Alert(AlertKind.STOP_HIT, pid, h.symbol, lot_id, h.stop, f"{h.symbol} — Stop hit",
                             f"Last {fmt.price(h.last)} ≤ stop {fmt.price(h.stop)} · {fmt.shares(h.open_shares)} sh"
                             " held."))
        elif h.stop is not None and h.last <= h.stop * (1 + settings.near_stop_pct):
            out.append(Alert(AlertKind.NEAR_STOP, pid, h.symbol, lot_id, h.stop, f"{h.symbol} — Near stop",
                             f"Last {fmt.price(h.last)} is within {fmt.pct(settings.near_stop_pct)} of stop "
                             f"{fmt.price(h.stop)}."))
        if h.target_hit:
            out.append(Alert(AlertKind.TARGET_REACHED, pid, h.symbol, lot_id, h.target,
                             f"{h.symbol} — Target reached",
                             f"Last {fmt.price(h.last)} ≥ target {fmt.price(h.target)}."))
    out.sort(key=lambda a: (SEVERITY_ORDER[a.severity], a.symbol))
    return out
