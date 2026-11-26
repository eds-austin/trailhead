"""Record and undo spin-offs (no widgets)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

from ..core.models import Side, Trade
from ..core.spinoffs import Spinoff
from ..core.splits import IGNORED
from ..data.repo import RepoError, Repos


def record_spinoff(repos: Repos, portfolio_id: int, holding, child: str, ex_date: date, shares: Decimal,
                   cost_pct: Decimal, price_factor: Decimal | None = None) -> tuple[Spinoff, Trade]:
    """The new company's shares become a BUY lot on the ex-date carrying cost_pct of the parent's cost basis then
    (`holding` is the parent HoldingView). A provider 'split' on that date (how Yahoo records it) is marked done."""
    child = child.strip().upper()
    if not child or child == holding.symbol:
        raise RepoError("Enter the new company's symbol")
    if shares <= 0:
        raise RepoError("Enter the shares you received")
    if not (0 < cost_pct < 1):
        raise RepoError("The share of cost moved must be between 0 and 100%")
    if ex_date <= holding.position.open_date:
        raise RepoError(f"{holding.symbol} was bought on or after that date")
    day_before = date.fromordinal(ex_date.toordinal() - 1)
    basis = sum((lot.entry_at(day_before) * held for lot in holding.lots
                 if lot.entry_date <= day_before
                 and (held := lot.shares - sum((s.shares for s in lot.sells if s.trade_date <= day_before),
                                               Decimal(0))) > 0), Decimal(0))
    if basis <= 0:
        raise RepoError(f"No {holding.symbol} shares were held the day before {ex_date.isoformat()}")
    moved = basis * cost_pct
    asset_type = next((lot.buy.asset_type for lot in reversed(holding.lots) if lot.buy.asset_type), None)
    buy = repos.trades.add(Trade(None, portfolio_id, child, Side.BUY, ex_date, shares, moved / shares,
                                 asset_type=asset_type, spinoff_of=holding.symbol,
                                 notes=f"Spun off from {holding.symbol} ({cost_pct * 100:.4g}% of its cost)"))
    s = Spinoff(holding.symbol, child, ex_date, cost_pct, price_factor, buy.id, portfolio_id)
    try:
        repos.spinoffs.add(s)
    except Exception:
        repos.trades.delete(buy.id)  # type: ignore[arg-type]
        raise
    for m in repos.splits.market([holding.symbol]):
        if m.ex_date == ex_date:
            repos.splits.record(replace(m, status=IGNORED, portfolio_id=portfolio_id))
    return s, buy


def undo_spinoff(repos: Repos, s: Spinoff) -> None:
    """Remove a spin-off and the new company's lot (refused once that lot has sells)."""
    assert s.portfolio_id is not None
    if s.child_trade_id is not None:
        repos.trades.delete(s.child_trade_id)  # raises RepoError when it has sells
    repos.spinoffs.delete(s.portfolio_id, s.parent, s.ex_date)
    repos.splits.delete(s.portfolio_id, s.parent, s.ex_date)  # the provider's 'split' is proposed again

