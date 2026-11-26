"""Splits for any portfolio type: load recorded splits, adjust trades, find splits to review (no widgets)."""

from __future__ import annotations

from datetime import date

from ..core.models import Portfolio, StopChange, Trade
from ..core.positions import build_lots
from ..core.splits import APPLIED, IGNORED, Split, SplitProposal, adjust_stops, adjust_trades, pending_splits
from ..data.repo import RepoError, Repos


def adjusted_trades(repos: Repos, portfolio_id: int) -> tuple[list[Trade], list[Split]]:
    splits = repos.splits.list(portfolio_id)
    return adjust_trades(repos.trades.list(portfolio_id), splits), splits


def adjusted_stops(repos: Repos, portfolio_id: int, trades: list[Trade], splits: list[Split]) -> list[StopChange]:
    return adjust_stops(repos.stops.list_for_portfolio(portfolio_id), trades, splits)


def pending_splits_for(repos: Repos, portfolio: Portfolio, today: date) -> list[SplitProposal]:
    assert portfolio.id is not None
    trades = repos.trades.list(portfolio.id)
    symbols = sorted({t.symbol for t in trades})
    return pending_splits(trades, repos.splits.market(symbols), repos.splits.list(portfolio.id), today)


def record_split(repos: Repos, portfolio_id: int, symbol: str, ex_date: date, ratio, apply: bool) -> Split:
    return repos.splits.record(Split(symbol, ex_date, ratio, APPLIED if apply else IGNORED, portfolio_id))


def undo_split(repos: Repos, portfolio_id: int, s: Split) -> None:
    """Forget a recorded split. Refused when sells after it would then exceed their lot."""
    if s.status == APPLIED:
        others = [x for x in repos.splits.list(portfolio_id) if (x.symbol, x.ex_date) != (s.symbol, s.ex_date)]
        lots = build_lots(adjust_trades(repos.trades.list(portfolio_id), others))
        bad = [lot for lot in lots if lot.symbol == s.symbol and lot.remaining < 0]
        if bad:
            raise RepoError(f"Without this split, {s.symbol} sells exceed the shares bought (lot of "
                            f"{bad[0].entry_date.isoformat()}). Edit those trades first.")
    repos.splits.delete(portfolio_id, s.symbol, s.ex_date)
