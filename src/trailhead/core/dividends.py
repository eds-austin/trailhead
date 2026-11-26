"""Dividend proposals (PLAN §8): for each ex-date on which the portfolio held shares, propose a cash event."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from .models import CashEvent, CashKind, Dividend, Side, Trade
from .money import ZERO, cents


@dataclass(frozen=True)
class DividendProposal:
    symbol: str
    ex_date: date
    pay_date: date | None
    amount_per_share: Decimal
    shares: Decimal
    source: str

    @property
    def amount(self) -> Decimal:
        return cents(self.shares * self.amount_per_share)


def shares_held(trades: Iterable[Trade], symbol: str, at_end_of: date) -> Decimal:
    """Shares of symbol held at the end of the given day."""
    total = ZERO
    for t in trades:
        if t.symbol == symbol and t.trade_date <= at_end_of:
            total += t.shares if t.side == Side.BUY else -t.shares
    return max(total, ZERO)


def pending_dividends(trades: list[Trade], cash_events: Iterable[CashEvent], dividends: Iterable[Dividend],
                      dismissed: set[tuple[str, date]] = frozenset(), today: date | None = None  # type: ignore[assignment]
                      ) -> list[DividendProposal]:
    """Dividends whose ex-date found shares held (end of the day before the ex-date) and no linked cash event."""
    linked = {(e.symbol, e.dividend_ex_date) for e in cash_events if e.kind == CashKind.DIVIDEND}
    out = []
    for d in dividends:
        if (d.symbol, d.ex_date) in linked or (d.symbol, d.ex_date) in dismissed:
            continue
        if today is not None and d.ex_date > today:
            continue
        held = shares_held(trades, d.symbol, d.ex_date - timedelta(days=1))
        if held > 0:
            out.append(DividendProposal(d.symbol, d.ex_date, d.pay_date, d.amount_per_share, held, d.source))
    return sorted(out, key=lambda p: (p.ex_date, p.symbol))
