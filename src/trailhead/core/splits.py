"""Stock splits (pure). Stored trades keep the values as typed; recorded (APPLIED) splits put earlier trades into
post-split terms when a portfolio is loaded: shares × factor, prices / factor, cost unchanged.

The data provider's prices and per-share dividends are split-adjusted, so adjusted trades match them. A split is
never applied silently: the user may have entered old trades split-adjusted already (status IGNORED).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction

from .models import Side, StopChange, Trade
from .money import ZERO

APPLIED = "APPLIED"
IGNORED = "IGNORED"
ONE = Decimal(1)
SHARE_Q = Decimal("1e-8")  # 1-for-3 makes repeating decimals: keep a full sell from leaving 1e-26 shares open


@dataclass(frozen=True)
class Split:
    symbol: str
    ex_date: date
    ratio: Decimal  # new shares per old share
    status: str = APPLIED
    portfolio_id: int | None = None

    @property
    def label(self) -> str:
        return ratio_label(self.ratio)


@dataclass(frozen=True)
class SplitProposal:
    symbol: str
    ex_date: date
    ratio: Decimal
    shares_before: Decimal  # held the day before, in that day's shares
    first_trade: date

    @property
    def label(self) -> str:
        return ratio_label(self.ratio)

    @property
    def shares_after(self) -> Decimal:
        return (self.shares_before * self.ratio).quantize(SHARE_Q)


def _num(d: Decimal) -> str:
    d = d.normalize()
    return f"{d:f}" if d != d.to_integral_value() else f"{d.to_integral_value():f}"


def ratio_label(ratio: Decimal) -> str:
    """50 → '50-for-1'; 0.1 → '1-for-10'; 1/3 → '1-for-3'; 1.5 → '3-for-2'; 1.253 → '1.253-for-1'."""
    f = Fraction(ratio).limit_denominator(20)
    if f.denominator > 1 and f.numerator > 1 and abs(Decimal(f.numerator) / f.denominator - ratio) < Decimal("1e-9"):
        return f"{f.numerator}-for-{f.denominator}"
    if ratio >= 1:
        return f"{_num(ratio)}-for-1"
    inv = (ONE / ratio).quantize(Decimal("0.001"))
    return f"1-for-{_num(inv)}"


QUARTER = Decimal("0.25")


def _quarters(v: Decimal) -> Decimal | None:
    """v rounded to a multiple of ¼ when it is one to within 0.1% (Yahoo rounds 1-for-3 to 0.3333 = 1 / 3.0003)."""
    q = (v / QUARTER).to_integral_value() * QUARTER
    return q if q > 0 and abs(v - q) <= v * Decimal("0.001") else None


def clean_ratio(ratio: Decimal) -> Decimal:
    """The exact split ratio behind a rounded one: 0.3333 → 1/3 (a 1-for-3 consolidation), 1.3333 → 4/3."""
    if ratio <= 0:
        return ratio
    q = _quarters(ratio)
    if q is not None:
        return q
    inv = _quarters(ONE / ratio)
    return ONE / inv if inv is not None else ratio


def looks_like_spinoff(ratio: Decimal) -> bool:
    """Real splits are whole ratios (2-for-1, 3-for-2, 5-for-4, 1-for-10, 1-for-3). Yahoo also records spin-offs as
    'splits' with the price adjustment as the ratio (FDX 1.241 on 2026-06-01, GE 1.253): those are not splits.
    A spin-off lowers the parent's price, so its ratio is always above 1; below 1 is a reverse split."""
    return ratio > ONE and _quarters(ratio) is None and _quarters(ONE / ratio) is None


def split_factor(splits: Iterable[Split], symbol: str, on: date) -> Decimal:
    """Shares on `on` × factor = today's shares. A trade dated on the ex-date is already post-split."""
    f = ONE
    for s in splits:
        if s.status == APPLIED and s.symbol == symbol and s.ex_date > on:
            f *= s.ratio
    return f


def _div(v: Decimal | None, f: Decimal) -> Decimal | None:
    return None if v is None else v / f


def adjust_trade(t: Trade, splits: Sequence[Split]) -> Trade:
    f = split_factor(splits, t.symbol, t.trade_date)
    if f == ONE:
        return t
    shares = (t.shares * f).quantize(SHARE_Q)
    price = t.price * t.shares / shares if shares else t.price / f  # cost unchanged, even for 1-for-3
    return replace(t, shares=shares, price=price, initial_stop=_div(t.initial_stop, f),
                   target_price=_div(t.target_price, f), split_factor=f)


def adjust_trades(trades: Iterable[Trade], splits: Sequence[Split]) -> list[Trade]:
    splits = [s for s in splits if s.status == APPLIED]
    return [adjust_trade(t, splits) for t in trades] if splits else list(trades)


def adjust_stops(stops: Iterable[StopChange], trades: Iterable[Trade], splits: Sequence[Split]) -> list[StopChange]:
    splits = [s for s in splits if s.status == APPLIED]
    if not splits:
        return list(stops)
    sym = {t.id: t.symbol for t in trades}
    out = []
    for s in stops:
        f = split_factor(splits, sym.get(s.lot_id, ""), s.effective_date)
        out.append(s if f == ONE else replace(s, stop=s.stop / f))
    return out


def _held(trades: Iterable[Trade], symbol: str, at_end_of: date) -> Decimal:
    total = ZERO
    for t in trades:
        if t.symbol == symbol and t.trade_date <= at_end_of:
            total += t.shares if t.side == Side.BUY else -t.shares
    return max(total, ZERO)


def pending_splits(trades: Sequence[Trade], market: Iterable[Split], recorded: Sequence[Split],
                   today: date) -> list[SplitProposal]:
    """Provider splits on which shares were held (the day before) and that the user hasn't applied or ignored."""
    done = {(s.symbol, s.ex_date) for s in recorded}
    adjusted = adjust_trades(trades, recorded)  # today's terms
    first: dict[str, date] = {}
    for t in trades:
        if t.symbol not in first or t.trade_date < first[t.symbol]:
            first[t.symbol] = t.trade_date
    out = []
    for m in market:
        if (m.symbol, m.ex_date) in done or m.ex_date > today or m.ratio == ONE or m.symbol not in first:
            continue
        if m.ex_date <= first[m.symbol]:
            continue
        day = m.ex_date - timedelta(days=1)
        held = _held(adjusted, m.symbol, day)
        if held > 0:
            then = held / split_factor(recorded, m.symbol, day)
            out.append(SplitProposal(m.symbol, m.ex_date, clean_ratio(m.ratio), then, first[m.symbol]))
    return sorted(out, key=lambda p: (p.ex_date, p.symbol))
