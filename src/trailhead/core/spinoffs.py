"""Spin-offs (pure). When a company spins off a new one (FDX → FDXF), part of the parent's cost basis moves to the
new shares: the parent lots held on the ex-date get a per-share basis cut, and the new shares are a BUY lot
(stored, so it can be sold) dated on the ex-date with the moved cost. Total value and return don't jump.

Sells before the ex-date keep the original cost. The provider's adjustment of the parent's earlier prices and
per-share dividends (Yahoo records FDX's spin-off as a 1.241 'split') is undone with `price_factor`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal

from .models import Dividend
from .money import ZERO, dsum

ONE = Decimal(1)


@dataclass(frozen=True)
class Spinoff:
    parent: str
    child: str
    ex_date: date
    cost_pct: Decimal  # fraction of the parent's basis moved to the child (0.194)
    price_factor: Decimal | None = None
    child_trade_id: int | None = None
    portfolio_id: int | None = None


@dataclass(frozen=True)
class BasisCut:
    lot_id: int
    symbol: str
    on: date
    per_share: Decimal
    shares: Decimal  # lot shares held on the ex-date

    @property
    def amount(self) -> Decimal:
        return self.per_share * self.shares


def default_cost_pct(price_factor: Decimal) -> Decimal:
    """The provider's price factor implies the child's share of value: 1 − 1/1.241 = 19.4%."""
    return (ONE - ONE / price_factor).quantize(Decimal("0.0001"))


def basis_cuts(lots: Iterable, spinoffs: Sequence[Spinoff]) -> list[BasisCut]:
    """Per-lot cost cuts for parent lots held at the end of the day before each spin-off."""
    out = []
    for s in sorted(spinoffs, key=lambda x: x.ex_date):
        day = s.ex_date - timedelta(days=1)
        for lot in lots:
            if lot.symbol != s.parent or lot.entry_date > day:
                continue
            held = lot.shares - dsum(x.shares for x in lot.sells if x.trade_date <= day)
            if held <= 0:
                continue
            per = lot.entry_at(day) * s.cost_pct
            cut = BasisCut(lot.id, lot.symbol, s.ex_date, per, held)
            lot.basis_cuts.append((cut.on, cut.per_share, cut.shares))
            out.append(cut)
    return out


def restore_dividends(divs: Iterable[Dividend], spinoffs: Sequence[Spinoff]) -> list[Dividend]:
    """Undo the provider's spin-off adjustment of the parent's earlier per-share dividends."""
    sp = [s for s in spinoffs if s.price_factor]
    out = []
    for d in divs:
        f = ONE
        for s in sp:
            if d.symbol == s.parent and d.ex_date < s.ex_date:
                f *= s.price_factor  # type: ignore[operator]
        out.append(d if f == ONE else replace(d, amount_per_share=(d.amount_per_share * f).quantize(Decimal("0.0001"))))
    return out


def restore_closes(closes: Mapping[str, Mapping[date, Decimal]], spinoffs: Sequence[Spinoff]
                   ) -> dict[str, dict[date, Decimal]]:
    """Undo the provider's spin-off adjustment of the parent's earlier closing prices."""
    out = {s: dict(c) for s, c in closes.items()}
    for s in spinoffs:
        if not s.price_factor or s.parent not in out:
            continue
        out[s.parent] = {d: (px * s.price_factor if d < s.ex_date else px) for d, px in out[s.parent].items()}
    return out


__all__ = ["ZERO", "BasisCut", "Spinoff", "basis_cuts", "default_cost_pct", "restore_closes", "restore_dividends"]
