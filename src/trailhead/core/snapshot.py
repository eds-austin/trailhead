"""Market-aware evaluation: lots + quotes → lot/position views and portfolio totals. Pure Python.

Nothing here is persisted (PLAN §5: derived values are never stored).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .models import CashEvent, CashKind, Portfolio, StopChange, Trade
from .money import ZERO, dsum, safe_div
from .positions import Lot, Position, build_lots, build_positions
from .r_engine import ActionDue, ActionKind, LadderState, evaluate_ladder, high_water_mark


@dataclass(frozen=True)
class MarketView:
    """Per-symbol market inputs (from quotes, price_history and instruments)."""

    last: Decimal | None = None
    prev_close: Decimal | None = None
    is_manual: bool = False
    highs: Sequence[tuple[date, Decimal | None]] = ()
    name: str | None = None
    sector: str | None = None
    sector_manual: bool = False
    next_earnings: date | None = None
    div_yield: Decimal | None = None
    annual_dividend: Decimal | None = None
    ex_div_date: date | None = None
    div_pay_date: date | None = None


EMPTY_MARKET = MarketView()


@dataclass
class LotView:
    lot: Lot
    market: MarketView
    state: LadderState

    @property
    def last(self) -> Decimal | None:
        return self.market.last

    @property
    def remaining(self) -> Decimal:
        return max(self.lot.remaining, ZERO)

    @property
    def mark(self) -> Decimal:
        """Price used for valuation: last quote, else entry (no quote yet)."""
        return self.last if self.last is not None else self.lot.entry

    @property
    def market_value(self) -> Decimal:
        return self.remaining * self.mark

    @property
    def unrealized_pl(self) -> Decimal | None:
        if self.last is None:
            return None
        return self.remaining * (self.last - self.lot.entry)

    @property
    def current_r(self) -> Decimal | None:
        if self.last is None or self.lot.is_closed:
            return None
        return (self.last - self.lot.entry) / self.lot.r

    @property
    def open_risk(self) -> Decimal:
        """remaining × max(0, last − current stop): money at risk from today's price."""
        return self.remaining * max(ZERO, self.mark - self.lot.current_stop)

    @property
    def risk_to_entry(self) -> Decimal:
        """remaining × (E − current stop); negative = locked-in profit."""
        return self.remaining * (self.lot.entry - self.lot.current_stop)

    @property
    def day_change(self) -> Decimal | None:
        if self.last is None or self.market.prev_close is None:
            return None
        return self.remaining * (self.last - self.market.prev_close)

    @property
    def stop_hit(self) -> bool:
        return self.last is not None and not self.lot.is_closed and self.last <= self.lot.current_stop


@dataclass
class PositionView:
    position: Position
    lot_views: list[LotView]
    market: MarketView
    dividends: Decimal = ZERO
    today: date | None = None
    portfolio_value: Decimal | None = None

    @property
    def symbol(self) -> str:
        return self.position.symbol

    @property
    def key(self) -> str:
        return self.position.key

    @property
    def open_views(self) -> list[LotView]:
        return [lv for lv in self.lot_views if not lv.lot.is_closed]

    @property
    def is_closed(self) -> bool:
        return self.position.is_closed

    @property
    def stop_hit(self) -> bool:
        return any(lv.stop_hit for lv in self.open_views)

    @property
    def status(self) -> str:
        if self.is_closed:
            return "CLOSED"
        return "STOP_HIT" if self.stop_hit else "OPEN"

    @property
    def last(self) -> Decimal | None:
        return self.market.last

    @property
    def stop_view(self) -> LotView | None:
        """Open lot with the highest current stop (the nearest exit)."""
        ov = self.open_views
        return max(ov, key=lambda lv: lv.lot.current_stop) if ov else None

    @property
    def stop(self) -> Decimal | None:
        lv = self.stop_view
        return lv.lot.current_stop if lv else None

    @property
    def target_view(self) -> LotView | None:
        ov = self.open_views
        return min(ov, key=lambda lv: lv.state.next_target) if ov else None

    @property
    def next_target(self) -> Decimal | None:
        lv = self.target_view
        return lv.state.next_target if lv else None

    def _dist(self, level: Decimal | None, lv: LotView | None) -> tuple[Decimal | None, Decimal | None]:
        if level is None or lv is None or self.last is None or self.last == 0:
            return None, None
        return (level - self.last) / self.last, (level - self.last) / lv.lot.r

    @property
    def stop_distance(self) -> tuple[Decimal | None, Decimal | None]:
        """(fraction, R) from last to the stop; negative when the stop is below the price."""
        return self._dist(self.stop, self.stop_view)

    @property
    def target_distance(self) -> tuple[Decimal | None, Decimal | None]:
        return self._dist(self.next_target, self.target_view)

    @property
    def actions(self) -> list[ActionDue]:
        acts = [a for lv in self.open_views for a in lv.state.actions]
        return sorted(acts, key=lambda a: (a.priority, a.lot_id or 0))

    @property
    def top_action(self) -> ActionDue | None:
        acts = self.actions
        if not acts:
            return None
        top = acts[0]
        if top.kind == ActionKind.STOP_HIT:  # aggregate: sell all remaining shares of hit lots
            shares = dsum(a.shares or ZERO for a in acts if a.kind == ActionKind.STOP_HIT)
            return ActionDue(top.kind, top.lot_id, shares, top.price, top.milestone)
        return top

    @property
    def open_shares(self) -> Decimal:
        return self.position.open_shares

    @property
    def market_value(self) -> Decimal:
        return dsum(lv.market_value for lv in self.open_views)

    @property
    def cost_basis(self) -> Decimal:
        return self.position.cost_basis_open

    @property
    def unrealized_pl(self) -> Decimal | None:
        if not self.open_views:
            return ZERO
        vals = [lv.unrealized_pl for lv in self.open_views]
        return None if any(v is None for v in vals) else dsum(vals)  # type: ignore[arg-type]

    @property
    def unrealized_pct(self) -> Decimal | None:
        return safe_div(self.unrealized_pl, self.cost_basis)

    @property
    def realized_pl(self) -> Decimal:
        return self.position.realized_pl

    @property
    def total_pl(self) -> Decimal:
        return (self.unrealized_pl or ZERO) + self.realized_pl + self.dividends

    @property
    def current_r(self) -> Decimal | None:
        """Unrealized R of the open lots: Σ unrealized / Σ(remaining × R)."""
        u = self.unrealized_pl
        if u is None or not self.open_views:
            return None
        return safe_div(u, dsum(lv.remaining * lv.lot.r for lv in self.open_views))

    @property
    def realized_r(self) -> Decimal | None:
        return self.position.realized_r

    @property
    def open_risk(self) -> Decimal:
        return dsum(lv.open_risk for lv in self.open_views)

    @property
    def day_change(self) -> Decimal | None:
        if not self.open_views:
            return None
        vals = [lv.day_change for lv in self.open_views]
        return None if any(v is None for v in vals) else dsum(vals)  # type: ignore[arg-type]

    @property
    def day_pct(self) -> Decimal | None:
        if self.last is None or self.market.prev_close in (None, 0):
            return None
        return (self.last - self.market.prev_close) / self.market.prev_close  # type: ignore[operator]

    @property
    def pct_of_portfolio(self) -> Decimal | None:
        if self.is_closed:
            return None
        return safe_div(self.market_value, self.portfolio_value)

    @property
    def days_held(self) -> int | None:
        end = self.position.close_date or self.today
        if end is None:
            return None
        return (end - self.position.open_date).days

    @property
    def earnings_soon_days(self) -> int | None:
        if self.market.next_earnings is None or self.today is None:
            return None
        return (self.market.next_earnings - self.today).days


@dataclass
class PortfolioSnapshot:
    portfolio: Portfolio
    lots: list[Lot]
    positions: list[PositionView]
    cash_events: list[CashEvent]
    trades: list[Trade]
    today: date
    lot_views: dict[int, LotView] = field(default_factory=dict)

    @property
    def open_positions(self) -> list[PositionView]:
        return [p for p in self.positions if not p.is_closed]

    @property
    def market_value(self) -> Decimal:
        return dsum(p.market_value for p in self.open_positions)

    @property
    def value(self) -> Decimal:
        """Portfolio value = market value of open positions. A stock-trading portfolio holds no cash balance;
        cash (e.g. dividends) is tracked separately."""
        return self.market_value

    @property
    def deployed(self) -> Decimal:
        """Cost basis of open lots."""
        return dsum(lot.cost_basis_open for lot in self.lots)

    @property
    def unrealized_pl(self) -> Decimal:
        return dsum(p.unrealized_pl or ZERO for p in self.open_positions)

    @property
    def unrealized_pct(self) -> Decimal | None:
        return safe_div(self.unrealized_pl, self.deployed)

    @property
    def open_risk(self) -> Decimal:
        return dsum(p.open_risk for p in self.open_positions)

    @property
    def open_risk_pct(self) -> Decimal | None:
        return safe_div(self.open_risk, self.value)

    @property
    def day_change(self) -> Decimal:
        return dsum(p.day_change or ZERO for p in self.open_positions)

    @property
    def day_change_pct(self) -> Decimal | None:
        prev = self.value - self.day_change
        return safe_div(self.day_change, prev)

    @property
    def dividends(self) -> Decimal:
        return dsum(e.amount for e in self.cash_events if e.kind == CashKind.DIVIDEND)

    @property
    def stale_symbols(self) -> list[str]:
        return [p.symbol for p in self.open_positions if p.last is None]

    def position(self, key: str) -> PositionView | None:
        for p in self.positions:
            if p.key == key:
                return p
        return None



def position_dividends(position: Position, cash_events: Iterable[CashEvent]) -> Decimal:
    """Dividend cash events for the symbol whose ex-date (or event date) is within the position's range."""
    start = position.open_date
    end = position.close_date
    total = ZERO
    for e in cash_events:
        if e.kind != CashKind.DIVIDEND or e.symbol != position.symbol:
            continue
        d = e.dividend_ex_date or e.event_date
        if d >= start and (end is None or d <= end):
            total += e.amount
    return total


def evaluate(portfolio: Portfolio, trades: Sequence[Trade], stop_changes: Sequence[StopChange],
             cash_events: Sequence[CashEvent], market: Mapping[str, MarketView], today: date) -> PortfolioSnapshot:
    lots = build_lots(trades, stop_changes)
    positions = build_positions(lots)
    lot_views: dict[int, LotView] = {}
    pviews: list[PositionView] = []
    for pos in positions:
        mv = market.get(pos.symbol, EMPTY_MARKET)
        views = []
        for lot in pos.lots:
            hwm = high_water_mark(lot.entry_date, mv.highs, mv.last)
            state = evaluate_ladder(lot.ladder, lot_id=lot.id, hwm=hwm, last=mv.last,
                                    current_stop=lot.current_stop, actual_sold=lot.sold)
            lv = LotView(lot, mv, state)
            lot_views[lot.id] = lv
            views.append(lv)
        pviews.append(PositionView(pos, views, mv, position_dividends(pos, cash_events), today))
    snap = PortfolioSnapshot(portfolio, lots, pviews, list(cash_events), list(trades), today, lot_views)
    value = snap.value
    for pv in pviews:
        pv.portfolio_value = value
    return snap
