"""Long-term Holdings: lots + quotes + dividends → holding views and portfolio totals. Pure Python.

No R multiples here. A holding is a Position (lots of one symbol from the first BUY until it is sold out). Its
stop and profit target are optional and live on its open BUY lots (`initial_stop`, `target_price`).

Total return counts every dollar the holding produced: unrealized + realized + dividends received. A reinvested
dividend (DRIP) is both income (the dividend) and a purchase (the DRIP BUY's cost basis), so nothing is double
counted. Total return % divides by the money you put in yourself: the cost of non-DRIP BUYs.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from itertools import pairwise
from statistics import median

from .models import ASSET_TYPES, CashEvent, CashKind, Dividend, Portfolio, Side, Trade
from .money import ZERO, dsum, safe_div
from .positions import Lot, Position, build_lots, build_positions
from .snapshot import EMPTY_MARKET, MarketView, position_dividends

TTM_DAYS = 365
DIV_HISTORY_DAYS = 400  # dividend history loaded for trailing income and the payment schedule
MIXED = "Mixed"


# ---------------------------------------------------------------- dividend schedule
@dataclass(frozen=True)
class NextDividend:
    ex_date: date | None
    pay_date: date | None
    per_share: Decimal | None
    estimated: bool  # projected from the payment history, not announced

    @property
    def date(self) -> date | None:
        """The next dividend event: the ex-date, or the pay date once the ex-date has passed."""
        return self.ex_date or self.pay_date


def trailing_per_share(history: Iterable[Dividend], today: date) -> Decimal | None:
    """Dividends per share with an ex-date in the last 365 days; None when there is no history at all."""
    hist = list(history)
    if not hist:
        return None
    start = today - timedelta(days=TTM_DAYS)
    return dsum(d.amount_per_share for d in hist if start < d.ex_date <= today)


def annual_per_share(market: MarketView, history: Iterable[Dividend], today: date) -> Decimal | None:
    """Forward annual dividend per share from the provider, else the trailing 12 months."""
    if market.annual_dividend is not None and market.annual_dividend > 0:
        return market.annual_dividend
    ttm = trailing_per_share(history, today)
    if ttm is not None:
        return ttm
    return market.annual_dividend  # 0 or None


def next_dividend(market: MarketView, history: Sequence[Dividend], today: date) -> NextDividend | None:
    hist = sorted(history, key=lambda d: d.ex_date)
    last_amount = hist[-1].amount_per_share if hist else None
    ex, pay = market.ex_div_date, market.div_pay_date
    if ex is not None and ex >= today:
        return NextDividend(ex, pay if pay is not None and pay >= ex else None, last_amount, False)
    if ex is not None and pay is not None and pay >= today:  # past the ex-date, not paid yet
        return NextDividend(None, pay, last_amount, False)
    if len(hist) < 2:
        return None
    gaps = [(b.ex_date - a.ex_date).days for a, b in pairwise(hist[-5:])]
    gap = int(median(gaps))
    if gap < 20:  # irregular or special payments: don't guess
        return None
    nxt = hist[-1].ex_date + timedelta(days=gap)
    if nxt < today - timedelta(days=gap):  # payments stopped a while ago
        return None
    while nxt < today:
        nxt += timedelta(days=gap)
    return NextDividend(nxt, None, last_amount, True)


# ---------------------------------------------------------------- holding view
def lot_stop(lot: Lot) -> Decimal | None:
    return lot.buy.initial_stop


def lot_target(lot: Lot) -> Decimal | None:
    return lot.buy.target_price


@dataclass
class HoldingView:
    position: Position
    market: MarketView
    history: Sequence[Dividend] = ()
    dividends: Decimal = ZERO  # received while this position was held
    payments: list[CashEvent] = field(default_factory=list)
    today: date | None = None
    portfolio_value: Decimal | None = None
    splits: list = field(default_factory=list)  # applied splits while held (core.splits.Split)
    spinoffs: list = field(default_factory=list)  # spin-offs from this holding while held (core.spinoffs.Spinoff)

    @property
    def symbol(self) -> str:
        return self.position.symbol

    @property
    def key(self) -> str:
        return self.position.key

    @property
    def lots(self) -> list[Lot]:
        return self.position.lots

    @property
    def open_lots(self) -> list[Lot]:
        return self.position.open_lots

    @property
    def is_closed(self) -> bool:
        return self.position.is_closed

    @property
    def asset_type(self) -> str | None:
        vals = {lot.buy.asset_type for lot in self.lots if lot.buy.asset_type}
        if not vals:
            return None
        return vals.pop() if len(vals) == 1 else MIXED

    @property
    def open_shares(self) -> Decimal:
        return self.position.open_shares

    @property
    def avg_cost(self) -> Decimal | None:
        return self.position.avg_entry

    @property
    def cost_basis(self) -> Decimal:
        """Cost of the shares still held (reinvested dividends included)."""
        return self.position.cost_basis_open

    @property
    def invested(self) -> Decimal:
        """Money put in: cost of every BUY except reinvested dividends. Cost a spin-off moved to the new company
        counts there (the new holding's lot), not here."""
        return dsum(lot.shares * lot.buy.price - lot.basis_moved for lot in self.lots if not lot.buy.drip)

    @property
    def reinvested(self) -> Decimal:
        return dsum(lot.shares * lot.buy.price for lot in self.lots if lot.buy.drip)

    @property
    def last(self) -> Decimal | None:
        return self.market.last

    @property
    def market_value(self) -> Decimal:
        if self.is_closed:
            return ZERO
        mark = self.last if self.last is not None else (self.avg_cost or ZERO)
        return self.open_shares * mark

    @property
    def unrealized_pl(self) -> Decimal | None:
        if self.is_closed:
            return ZERO
        if self.last is None:
            return None
        return self.market_value - self.cost_basis

    @property
    def unrealized_pct(self) -> Decimal | None:
        return safe_div(self.unrealized_pl, self.cost_basis) if not self.is_closed else None

    @property
    def realized_pl(self) -> Decimal:
        return self.position.realized_pl

    @property
    def total_return(self) -> Decimal | None:
        u = self.unrealized_pl
        if u is None:
            return None
        return u + self.realized_pl + self.dividends

    @property
    def total_return_pct(self) -> Decimal | None:
        return safe_div(self.total_return, self.invested)

    @property
    def day_change(self) -> Decimal | None:
        if self.is_closed or self.last is None or self.market.prev_close is None:
            return None
        return self.open_shares * (self.last - self.market.prev_close)

    @property
    def day_pct(self) -> Decimal | None:
        if self.last is None or self.market.prev_close in (None, 0):
            return None
        return (self.last - self.market.prev_close) / self.market.prev_close  # type: ignore[operator]

    @property
    def pct_of_portfolio(self) -> Decimal | None:
        return None if self.is_closed else safe_div(self.market_value, self.portfolio_value)

    # dividends -------------------------------------------------------
    @property
    def annual_per_share(self) -> Decimal | None:
        return annual_per_share(self.market, self.history, self.today or date.today())

    @property
    def est_income(self) -> Decimal | None:
        """Estimated dividend income over the next year at the current rate."""
        a = self.annual_per_share
        if self.is_closed or a is None:
            return None
        return self.open_shares * a

    @property
    def div_yield(self) -> Decimal | None:
        a = self.annual_per_share
        if a is None:
            return self.market.div_yield
        return safe_div(a, self.last)

    @property
    def yield_on_cost(self) -> Decimal | None:
        """Estimated annual income ÷ cost basis of the shares held."""
        return safe_div(self.est_income, self.cost_basis) if not self.is_closed else None

    @property
    def next_dividend(self) -> NextDividend | None:
        if self.is_closed:
            return None
        return next_dividend(self.market, self.history, self.today or date.today())

    # stop / target ---------------------------------------------------
    @property
    def stop(self) -> Decimal | None:
        """Highest stop among open lots (the nearest exit), None when no lot has one."""
        stops = [s for lot in self.open_lots if (s := lot_stop(lot)) is not None]
        return max(stops) if stops else None

    @property
    def target(self) -> Decimal | None:
        """Lowest profit target among open lots."""
        ts = [t for lot in self.open_lots if (t := lot_target(lot)) is not None]
        return min(ts) if ts else None

    @property
    def stop_hit(self) -> bool:
        return self.last is not None and self.stop is not None and not self.is_closed and self.last <= self.stop

    @property
    def target_hit(self) -> bool:
        return (self.last is not None and self.target is not None and not self.is_closed
                and self.last >= self.target)

    def distance(self, level: Decimal | None) -> Decimal | None:
        """Fraction from last to a level (negative when the level is below the price)."""
        if level is None or self.last in (None, 0):
            return None
        return (level - self.last) / self.last  # type: ignore[operator]

    @property
    def status(self) -> str:
        if self.is_closed:
            return "CLOSED"
        if self.stop_hit:
            return "STOP_HIT"
        if self.target_hit:
            return "TARGET"
        return "OPEN"

    @property
    def days_held(self) -> int | None:
        end = self.position.close_date or self.today
        return (end - self.position.open_date).days if end is not None else None


# ---------------------------------------------------------------- snapshot
@dataclass
class HoldingsSnapshot:
    portfolio: Portfolio
    lots: list[Lot]
    holdings: list[HoldingView]
    cash_events: list[CashEvent]
    trades: list[Trade]
    today: date
    splits: list = field(default_factory=list)  # recorded splits of the portfolio (core.splits.Split)

    @property
    def positions(self) -> list[HoldingView]:  # same name as PortfolioSnapshot (pending-dividend matching)
        return self.holdings

    @property
    def open_holdings(self) -> list[HoldingView]:
        return [h for h in self.holdings if not h.is_closed]

    open_positions = open_holdings

    @property
    def value(self) -> Decimal:
        return dsum(h.market_value for h in self.open_holdings)

    @property
    def cost_basis(self) -> Decimal:
        return dsum(h.cost_basis for h in self.open_holdings)

    @property
    def unrealized_pl(self) -> Decimal:
        return dsum(h.unrealized_pl or ZERO for h in self.open_holdings)

    @property
    def unrealized_pct(self) -> Decimal | None:
        return safe_div(self.unrealized_pl, self.cost_basis)

    @property
    def realized_pl(self) -> Decimal:
        return dsum(h.realized_pl for h in self.holdings)

    @property
    def dividends(self) -> Decimal:
        return dsum(e.amount for e in self.cash_events if e.kind == CashKind.DIVIDEND)

    @property
    def reinvested(self) -> Decimal:
        return dsum(h.reinvested for h in self.holdings)

    @property
    def invested(self) -> Decimal:
        return dsum(h.invested for h in self.holdings)

    @property
    def total_return(self) -> Decimal:
        return self.unrealized_pl + self.realized_pl + self.dividends

    @property
    def total_return_pct(self) -> Decimal | None:
        return safe_div(self.total_return, self.invested)

    @property
    def est_income(self) -> Decimal:
        return dsum(h.est_income or ZERO for h in self.open_holdings)

    @property
    def yield_on_cost(self) -> Decimal | None:
        return safe_div(self.est_income, self.cost_basis)

    @property
    def div_yield(self) -> Decimal | None:
        return safe_div(self.est_income, self.value)

    @property
    def day_change(self) -> Decimal:
        return dsum(h.day_change or ZERO for h in self.open_holdings)

    @property
    def day_change_pct(self) -> Decimal | None:
        return safe_div(self.day_change, self.value - self.day_change)

    @property
    def stale_symbols(self) -> list[str]:
        return [h.symbol for h in self.open_holdings if h.last is None]

    def income_between(self, start: date | None, end: date | None) -> tuple[Decimal, int]:
        evs = [e for e in self.cash_events if e.kind == CashKind.DIVIDEND
               and (start is None or e.event_date >= start) and (end is None or e.event_date <= end)]
        return dsum(e.amount for e in evs), len(evs)

    def allocation_by_type(self) -> list[tuple[str, Decimal]]:
        """(investment type, market value), in ASSET_TYPES order then the rest; empty types left out."""
        by: dict[str, Decimal] = defaultdict(lambda: ZERO)
        for h in self.open_holdings:
            by[h.asset_type or "Other"] += h.market_value
        order = {t: i for i, t in enumerate(ASSET_TYPES)}
        return sorted(((t, v) for t, v in by.items() if v > 0), key=lambda x: (order.get(x[0], 99), x[0]))

    def upcoming_dividends(self, within_days: int | None = None) -> list[tuple[HoldingView, NextDividend]]:
        out = []
        for h in self.open_holdings:
            nd = h.next_dividend
            if nd is None or nd.date is None:
                continue
            if within_days is not None and (nd.date - self.today).days > within_days:
                continue
            out.append((h, nd))
        return sorted(out, key=lambda x: (x[1].date, x[0].symbol))  # type: ignore[arg-type,return-value]

    def holding(self, key: str) -> HoldingView | None:
        return next((h for h in self.holdings if h.key == key), None)

    position = holding


def holding_payments(position: Position, cash_events: Iterable[CashEvent]) -> list[CashEvent]:
    start, end = position.open_date, position.close_date
    out = []
    for e in cash_events:
        if e.kind != CashKind.DIVIDEND or e.symbol != position.symbol:
            continue
        d = e.dividend_ex_date or e.event_date
        if d >= start and (end is None or d <= end):
            out.append(e)
    return sorted(out, key=lambda e: (e.event_date, e.id or 0))


def evaluate_holdings(portfolio: Portfolio, trades: Sequence[Trade], cash_events: Sequence[CashEvent],
                      market: Mapping[str, MarketView], history: Mapping[str, Sequence[Dividend]],
                      today: date, spinoffs: Sequence = ()) -> HoldingsSnapshot:
    from .spinoffs import basis_cuts

    lots = build_lots(trades)
    basis_cuts(lots, spinoffs)
    views = []
    for pos in build_positions(lots):
        views.append(HoldingView(pos, market.get(pos.symbol, EMPTY_MARKET), history.get(pos.symbol, ()),
                                 position_dividends(pos, cash_events), holding_payments(pos, cash_events), today))
    snap = HoldingsSnapshot(portfolio, lots, views, list(cash_events), list(trades), today)
    value = snap.value
    for h in views:
        h.portfolio_value = value
        end = h.position.close_date
        h.spinoffs = [s for s in spinoffs if s.parent == h.symbol and h.position.open_date < s.ex_date
                      and (end is None or s.ex_date <= end)]
    return snap


@dataclass(frozen=True)
class PeriodReturn:
    pl: Decimal  # unrealized change + realized + dividends within the period
    pct: Decimal | None  # simple Dietz: pl ÷ (value at start + ½ net money added)
    start_value: Decimal
    net_added: Decimal


def period_return(points: Sequence, trades: Iterable[Trade], start: date, end: date | None = None
                  ) -> PeriodReturn | None:
    """Return earned from `start` on, from a full (not rebased) daily P/L series (core.equity.PLPoint).
    Money added = cost of BUYs (not reinvested dividends or spin-off shares) − SELL proceeds in the period."""
    if not points:
        return None
    before = [p for p in points if p.date < start]
    within = [p for p in points if p.date >= start and (end is None or p.date <= end)]
    if not within:
        return None
    base_total = before[-1].total if before else Decimal(0)
    start_value = before[-1].market_value if before else Decimal(0)
    pl = within[-1].total - base_total
    added = Decimal(0)
    for t in trades:
        if t.trade_date < start or (end is not None and t.trade_date > end):
            continue
        if t.side == Side.BUY and not t.drip and not t.spinoff_of:
            added += t.amount
        elif t.side == Side.SELL:
            added -= t.amount
    base = start_value + added / 2
    return PeriodReturn(pl, pl / base if base > 0 else None, start_value, added)
