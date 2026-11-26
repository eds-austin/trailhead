"""Load a stock-trading portfolio's rows and evaluate them into a snapshot (no widgets, no SQL)."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from ...core.models import Portfolio, Side
from ...core.snapshot import MarketView, PortfolioSnapshot, evaluate
from ...data.repo import Repos


def market_views(repos: Repos, first_dates: dict[str, date]) -> dict[str, MarketView]:
    symbols = list(first_dates)
    quotes = repos.market.get_quotes(symbols)
    instruments = repos.market.get_instruments(symbols)
    since = min(first_dates.values()) if first_dates else None
    highs = repos.market.highs(symbols, since=since)
    out: dict[str, MarketView] = {}
    for s in symbols:
        q = quotes.get(s)
        i = instruments.get(s)
        out[s] = MarketView(
            last=q.last if q else None,
            prev_close=q.prev_close if q else None,
            is_manual=bool(q and q.is_manual),
            highs=highs.get(s, ()),
            name=i.name if i else None,
            sector=i.sector if i else None,
            sector_manual=bool(i and i.sector_manual),
            next_earnings=i.next_earnings_date if i else None,
            div_yield=i.div_yield if i else None,
            annual_dividend=i.annual_dividend if i else None,
            ex_div_date=i.ex_div_date if i else None,
            div_pay_date=i.div_pay_date if i else None,
        )
    return out


def first_buy_dates(trades: Iterable) -> dict[str, date]:
    out: dict[str, date] = {}
    for t in trades:
        if t.side == Side.BUY and (t.symbol not in out or t.trade_date < out[t.symbol]):
            out[t.symbol] = t.trade_date
    return out


def load_snapshot(repos: Repos, portfolio: Portfolio, today: date) -> PortfolioSnapshot:
    from ..splits import adjusted_stops, adjusted_trades

    assert portfolio.id is not None
    trades, splits = adjusted_trades(repos, portfolio.id)  # earlier trades in post-split shares
    stops = adjusted_stops(repos, portfolio.id, trades, splits)
    cash = repos.cash.list(portfolio.id)
    mv = market_views(repos, first_buy_dates(trades))
    return evaluate(portfolio, trades, stops, cash, mv, today)


def pending_dividends_for(repos: Repos, portfolio: Portfolio, today: date):
    from ...core.dividends import pending_dividends
    from ..splits import adjusted_trades

    assert portfolio.id is not None
    trades, _ = adjusted_trades(repos, portfolio.id)  # provider dividends are per post-split share
    symbols = sorted({t.symbol for t in trades})
    first = min((t.trade_date for t in trades), default=today)
    from ...core.spinoffs import restore_dividends

    divs = restore_dividends(repos.market.dividends(symbols, first), repos.spinoffs.list(portfolio.id))
    return pending_dividends(trades, repos.cash.list(portfolio.id), divs,
                             repos.dividend_reviews.dismissed(portfolio.id), today)


def dividends_by_position(snapshot: PortfolioSnapshot, proposals) -> dict[str, list]:
    """Assign each pending dividend to the position that held the shares on the day before its ex-date."""
    out: dict[str, list] = {}
    for d in proposals:
        for pv in snapshot.positions:
            pos = pv.position
            if pv.symbol != d.symbol or pos.open_date >= d.ex_date:
                continue
            if pos.close_date is not None and pos.close_date < d.ex_date:
                continue
            out.setdefault(pv.key, []).append(d)
            break
    return out


def accept_dividend(repos: Repos, portfolio_id: int, d, amount, today: date):
    """Record a proposed dividend as a DIVIDEND cash event (dated on the pay date when known and past)."""
    from ...core.models import CashEvent, CashKind
    from ...ui import format as fmt

    when = d.pay_date if d.pay_date and d.pay_date <= today else min(d.ex_date, today)
    return repos.cash.add(CashEvent(None, portfolio_id, when, CashKind.DIVIDEND, amount, d.symbol, d.ex_date,
                                    f"{fmt.shares(d.shares)} sh × {d.amount_per_share}"))
