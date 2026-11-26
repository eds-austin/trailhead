"""Load a Long-term Holdings portfolio and record dividends (no widgets, no SQL)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from ...core.holdings import DIV_HISTORY_DAYS, HoldingsSnapshot, HoldingView, evaluate_holdings
from ...core.models import CashEvent, CashKind, Portfolio, Side, Trade
from ...data.repo import RepoError, Repos
from ..stock_trading.service import first_buy_dates, market_views


def load_holdings(repos: Repos, portfolio: Portfolio, today: date) -> HoldingsSnapshot:
    from ..splits import adjusted_trades

    assert portfolio.id is not None
    trades, splits = adjusted_trades(repos, portfolio.id)  # earlier trades in post-split shares
    cash = repos.cash.list(portfolio.id)
    firsts = first_buy_dates(trades)
    mv = market_views(repos, firsts)
    from ...core.spinoffs import restore_dividends

    spinoffs = repos.spinoffs.list(portfolio.id)
    history: dict[str, list] = {}
    divs = repos.market.dividends(list(firsts), since=today - timedelta(days=DIV_HISTORY_DAYS))
    for d in restore_dividends(divs, spinoffs):
        history.setdefault(d.symbol, []).append(d)
    snap = evaluate_holdings(portfolio, trades, cash, mv, history, today, spinoffs)
    snap.splits = splits
    for h in snap.holdings:
        end = h.position.close_date
        h.splits = [s for s in splits if s.symbol == h.symbol and s.status == "APPLIED"
                    and h.position.open_date < s.ex_date and (end is None or s.ex_date <= end)]
    return snap


@dataclass(frozen=True)
class Reinvestment:
    shares: Decimal
    price: Decimal

    @property
    def amount(self) -> Decimal:
        return self.shares * self.price


def record_dividend(repos: Repos, portfolio_id: int, symbol: str, amount: Decimal, paid_on: date,
                    ex_date: date | None, note: str | None = None, reinvest: Reinvestment | None = None,
                    holding: HoldingView | None = None) -> tuple[CashEvent, Trade | None]:
    """Record a received dividend, and optionally the shares it bought (a DRIP BUY added to the holding: same
    investment type, stop and target as the holding's open lots). The BUY is validated before anything is saved."""
    if amount <= 0:
        raise RepoError("A dividend must be positive")
    buy = None
    if reinvest is not None:
        if reinvest.shares <= 0 or reinvest.price <= 0:
            raise RepoError("Enter the shares and price of the reinvestment")
        asset_type = stop = target = None
        if holding is not None:
            asset_type = holding.asset_type if holding.asset_type != "Mixed" else None
            if asset_type is None and holding.lots:
                asset_type = holding.lots[-1].buy.asset_type
            stop, target = holding.stop, holding.target
        ex_txt = f" (ex {ex_date.isoformat()})" if ex_date else ""
        buy = Trade(None, portfolio_id, symbol, Side.BUY, paid_on, reinvest.shares, reinvest.price,
                    initial_stop=stop, notes=f"Reinvested dividend{ex_txt}", asset_type=asset_type,
                    target_price=target, drip=True)
        repos.trades._validate(buy)
    ev = repos.cash.add(CashEvent(None, portfolio_id, paid_on, CashKind.DIVIDEND, amount, symbol, ex_date, note))
    if buy is not None:
        try:
            buy = repos.trades.add(buy)
        except RepoError:
            repos.cash.delete(ev.id)  # type: ignore[arg-type]
            raise
    return ev, buy


def pl_points(repos: Repos, snap: HoldingsSnapshot, today: date, start: date | None = None) -> list:
    """Daily cumulative P/L (core.equity.PLPoint) at daily closes: spin-off cost moves and Yahoo's spin-off price
    scaling are handled. With `start`, rebased to P/L earned since then."""
    from ...core.equity import pl_curve
    from ...core.spinoffs import BasisCut, restore_closes

    if not snap.trades:
        return []
    assert snap.portfolio.id is not None
    symbols = sorted({t.symbol for t in snap.trades})
    first = min([t.trade_date for t in snap.trades] + [e.event_date for e in snap.cash_events])
    closes = restore_closes(repos.market.closes(symbols, since=first - timedelta(days=7)),
                            repos.spinoffs.list(snap.portfolio.id))
    quotes = repos.market.get_quotes(symbols)
    cuts = [BasisCut(lot.id, lot.symbol, on, per, n) for lot in snap.lots for on, per, n in lot.basis_cuts]
    return pl_curve(snap.trades, snap.cash_events, closes, today, start=start,
                    last_prices={s: q.last for s, q in quotes.items()}, basis_cuts=cuts)
