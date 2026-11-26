"""Trades → lots → positions; FIFO sell matching (PLAN §6.1). Pure Python.

A lot is one BUY trade; SELLs reference a lot. A position groups the lots of one symbol from the first BUY
(while flat) until remaining shares reach 0. A later BUY of the same symbol starts a new position.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .models import Side, StopChange, Trade
from .money import ZERO, dsum, safe_div
from .r_engine import Ladder, as_plan


class LedgerError(ValueError):
    """Trades that cannot be reconciled (sell without a lot, oversell, ...)."""


def _trade_key(t: Trade) -> tuple:
    return (t.trade_date, t.id or 0)


@dataclass
class Lot:
    buy: Trade
    sells: list[Trade] = field(default_factory=list)
    stops: list[StopChange] = field(default_factory=list)
    no: int = 0  # display number: n-th BUY of this symbol (1-based)
    sell_no: dict[int, int] = field(default_factory=dict)  # sell trade id → n-th SELL of this symbol
    # spin-offs: (ex-date, per-share cost moved to the new company, shares held then); see core/spinoffs.py
    basis_cuts: list[tuple[date, Decimal, Decimal]] = field(default_factory=list)

    def sell_number(self, sell_id: int | None) -> int | None:
        return self.sell_no.get(sell_id) if sell_id is not None else None

    @property
    def id(self) -> int:
        assert self.buy.id is not None
        return self.buy.id

    @property
    def symbol(self) -> str:
        return self.buy.symbol

    @property
    def entry(self) -> Decimal:
        """Cost per share now (after any spin-off moved part of it to the new company)."""
        if not self.basis_cuts:
            return self.buy.price
        return self.buy.price - dsum(per for _d, per, _n in self.basis_cuts)

    def entry_at(self, d: date) -> Decimal:
        """Cost per share on day d (spin-off cuts from their ex-date on)."""
        return self.buy.price - dsum(per for on, per, _n in self.basis_cuts if on <= d)

    @property
    def basis_moved(self) -> Decimal:
        """Cost moved to spun-off companies."""
        return dsum(per * n for _d, per, n in self.basis_cuts)

    @property
    def entry_date(self) -> date:
        return self.buy.trade_date

    @property
    def initial_stop(self) -> Decimal:
        assert self.buy.initial_stop is not None
        return self.buy.initial_stop

    @property
    def shares(self) -> Decimal:
        """N, the original shares."""
        return self.buy.shares

    @property
    def r(self) -> Decimal:
        return self.entry - self.initial_stop

    @property
    def ladder(self) -> Ladder:
        return Ladder(self.entry, self.initial_stop, self.shares, as_plan(self.buy.exit_plan))

    @property
    def exit_plan(self) -> str:
        return as_plan(self.buy.exit_plan).value

    @property
    def sold(self) -> Decimal:
        return dsum(s.shares for s in self.sells)

    @property
    def remaining(self) -> Decimal:
        return self.shares - self.sold

    @property
    def is_closed(self) -> bool:
        return self.remaining <= 0

    @property
    def close_date(self) -> date | None:
        if not self.is_closed or not self.sells:
            return None
        return max(s.trade_date for s in self.sells)

    @property
    def cost_basis_open(self) -> Decimal:
        return max(self.remaining, ZERO) * self.entry

    @property
    def risk_dollars(self) -> Decimal:
        """Initial $ risk: N × R."""
        return self.shares * self.r

    @property
    def realized_pl(self) -> Decimal:
        return dsum(s.shares * (s.price - self.entry_at(s.trade_date)) for s in self.sells)

    def realized_pl_between(self, start: date | None, end: date | None) -> Decimal:
        return dsum(s.shares * (s.price - self.entry_at(s.trade_date)) for s in self.sells
                    if _in_range(s.trade_date, start, end))

    @property
    def realized_r(self) -> Decimal | None:
        """Σ(sell shares × (sell price − E)) / (N × R). Final once the lot is closed."""
        return safe_div(self.realized_pl, self.risk_dollars)

    @property
    def current_stop(self) -> Decimal:
        """Latest stop change, else the initial stop."""
        if not self.stops:
            return self.initial_stop
        latest = max(self.stops, key=lambda s: (s.effective_date, s.created_at or s.effective_date, s.id or 0))
        return latest.stop

    @property
    def strategy(self) -> str | None:
        return self.buy.strategy

    @property
    def timeframe(self) -> str | None:
        return self.buy.timeframe


def _in_range(d: date, start: date | None, end: date | None) -> bool:
    return (start is None or d >= start) and (end is None or d <= end)


@dataclass
class Position:
    symbol: str
    lots: list[Lot] = field(default_factory=list)
    seq: int = 0  # n-th position of this symbol (0-based)

    @property
    def key(self) -> str:
        return f"{self.symbol}#{self.seq}"

    @property
    def open_date(self) -> date:
        return min(lot.entry_date for lot in self.lots)

    @property
    def open_lots(self) -> list[Lot]:
        return [lot for lot in self.lots if not lot.is_closed]

    @property
    def is_closed(self) -> bool:
        return all(lot.is_closed for lot in self.lots)

    @property
    def close_date(self) -> date | None:
        if not self.is_closed:
            return None
        dates = [lot.close_date for lot in self.lots if lot.close_date]
        return max(dates) if dates else None

    @property
    def open_shares(self) -> Decimal:
        return dsum(max(lot.remaining, ZERO) for lot in self.lots)

    @property
    def original_shares(self) -> Decimal:
        return dsum(lot.shares for lot in self.lots)

    @property
    def avg_entry(self) -> Decimal | None:
        """Weighted average entry of open lots (of all lots once closed)."""
        lots = self.open_lots
        if lots:
            return safe_div(dsum(lot.remaining * lot.entry for lot in lots), dsum(lot.remaining for lot in lots))
        return safe_div(dsum(lot.shares * lot.entry for lot in self.lots), self.original_shares)

    @property
    def cost_basis_open(self) -> Decimal:
        return dsum(lot.cost_basis_open for lot in self.lots)

    @property
    def realized_pl(self) -> Decimal:
        return dsum(lot.realized_pl for lot in self.lots)

    @property
    def risk_dollars(self) -> Decimal:
        return dsum(lot.risk_dollars for lot in self.lots)

    @property
    def realized_r(self) -> Decimal | None:
        """Position realized R = Σ realized P/L / Σ(N × R) of its lots."""
        return safe_div(self.realized_pl, self.risk_dollars)

    @property
    def trades(self) -> list[Trade]:
        out: list[Trade] = []
        for lot in self.lots:
            out.append(lot.buy)
            out.extend(lot.sells)
        return sorted(out, key=_trade_key)

    @property
    def stop_changes(self) -> list[StopChange]:
        return sorted((s for lot in self.lots for s in lot.stops), key=lambda s: (s.effective_date, s.id or 0))

    def _uniform(self, attr: str) -> str | None:
        vals = {getattr(lot, attr) for lot in self.lots}
        if len(vals) == 1:
            return vals.pop()
        return "Mixed"

    @property
    def strategy(self) -> str | None:
        return self._uniform("strategy")

    @property
    def timeframe(self) -> str | None:
        return self._uniform("timeframe")

    @property
    def exit_plan(self) -> str | None:
        return self._uniform("exit_plan")


def trade_numbers(trades: Iterable[Trade]) -> dict[int, int]:
    """Per-symbol display numbers: BUYs and SELLs of each symbol are numbered 1, 2, … separately,
    in trade-date order (ties by entry order). Returns trade id → number."""
    counters: dict[tuple[str, Side], int] = defaultdict(int)
    out: dict[int, int] = {}
    for t in sorted(trades, key=_trade_key):
        if t.id is None:
            continue
        counters[(t.symbol, t.side)] += 1
        out[t.id] = counters[(t.symbol, t.side)]
    return out


def build_lots(trades: Iterable[Trade], stop_changes: Iterable[StopChange] = ()) -> list[Lot]:
    """Group trades into lots. Raises LedgerError for sells that do not reference a known lot."""
    lots: dict[int, Lot] = {}
    sells: list[Trade] = []
    for t in sorted(trades, key=_trade_key):
        if t.side == Side.BUY:
            if t.id is None:
                raise LedgerError("BUY trade without id")
            lots[t.id] = Lot(t)
        else:
            sells.append(t)
    for s in sells:
        if s.lot_id is None or s.lot_id not in lots:
            raise LedgerError(f"SELL #{s.id} {s.symbol} references unknown lot {s.lot_id}")
        lot = lots[s.lot_id]
        if lot.symbol != s.symbol:
            raise LedgerError(f"SELL #{s.id} symbol {s.symbol} does not match lot {lot.id} ({lot.symbol})")
        lot.sells.append(s)
    for sc in stop_changes:
        if sc.lot_id in lots:
            lots[sc.lot_id].stops.append(sc)
    numbers = trade_numbers([*(lot.buy for lot in lots.values()), *sells])
    for lot in lots.values():
        lot.no = numbers.get(lot.id, 0)
        lot.sell_no = {s.id: numbers[s.id] for s in lot.sells if s.id in numbers}
    return sorted(lots.values(), key=lambda lot: (lot.entry_date, lot.id))


def build_positions(lots: Iterable[Lot]) -> list[Position]:
    """Walk each symbol's trades chronologically; a BUY while flat opens a new position."""
    by_symbol: dict[str, list[Lot]] = defaultdict(list)
    for lot in lots:
        by_symbol[lot.symbol].append(lot)
    out: list[Position] = []
    for symbol, sym_lots in by_symbol.items():
        lot_of: dict[int, Lot] = {lot.id: lot for lot in sym_lots}
        events: list[Trade] = sorted([t for lot in sym_lots for t in (lot.buy, *lot.sells)], key=_trade_key)
        open_shares = ZERO
        current: Position | None = None
        seq = 0
        for t in events:
            if t.side == Side.BUY:
                if current is None or open_shares <= 0:
                    current = Position(symbol, seq=seq)
                    seq += 1
                    out.append(current)
                current.lots.append(lot_of[t.id])  # type: ignore[index]
                open_shares += t.shares
            else:
                open_shares -= t.shares
    out.sort(key=lambda p: (p.open_date, p.symbol, p.seq))
    return out


def validate(trades: Sequence[Trade]) -> list[str]:
    """Return human-readable problems (oversold lots, sells before buys, ...)."""
    problems: list[str] = []
    try:
        lots = build_lots(trades)
    except LedgerError as e:
        return [str(e)]
    for lot in lots:
        if lot.remaining < 0:
            problems.append(f"Lot #{lot.id} {lot.symbol} is oversold by {-lot.remaining}")
        for s in lot.sells:
            if s.trade_date < lot.entry_date:
                problems.append(f"SELL #{s.id} {s.symbol} is dated before its lot #{lot.id}")
        if lot.buy.initial_stop is None or lot.initial_stop >= lot.entry:
            problems.append(f"Lot #{lot.id} {lot.symbol} needs an initial stop below entry")
    return problems


def open_lots_fifo(lots: Iterable[Lot], symbol: str, exclude_sell_id: int | None = None) -> list[Lot]:
    """Open lots of a symbol, oldest first (FIFO)."""
    out = []
    for lot in lots:
        if lot.symbol != symbol:
            continue
        rem = lot.remaining
        if exclude_sell_id is not None:
            rem += dsum(s.shares for s in lot.sells if s.id == exclude_sell_id)
        if rem > 0:
            out.append(lot)
    return sorted(out, key=lambda lot: (lot.entry_date, lot.id))


def remaining_excluding(lot: Lot, exclude_sell_id: int | None) -> Decimal:
    rem = lot.remaining
    if exclude_sell_id is not None:
        rem += dsum(s.shares for s in lot.sells if s.id == exclude_sell_id)
    return rem


def fifo_allocate(lots: Sequence[Lot], shares: Decimal, first_lot_id: int | None = None,
                  exclude_sell_id: int | None = None) -> list[tuple[int, Decimal]]:
    """Split a sell across open lots. Starts with first_lot_id (if given) then FIFO order.

    Returns [(lot_id, shares)]. Raises LedgerError if open shares are insufficient.
    """
    ordered = list(lots)
    if first_lot_id is not None:
        ordered.sort(key=lambda lot: lot.id != first_lot_id)  # stable: chosen lot first, rest FIFO
    left = shares
    out: list[tuple[int, Decimal]] = []
    for lot in ordered:
        if left <= 0:
            break
        avail = remaining_excluding(lot, exclude_sell_id)
        if avail <= 0:
            continue
        take = min(avail, left)
        out.append((lot.id, take))
        left -= take
    if left > 0:
        raise LedgerError(f"Only {shares - left} shares open; cannot sell {shares}")
    return out
