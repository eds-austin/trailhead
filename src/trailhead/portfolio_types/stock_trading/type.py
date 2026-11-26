"""StockTradingType: the R-multiple swing-trading portfolio."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...core.money import ZERO
from ...core.stats import closed_lot_units, trade_stats
from ..base import DashboardSummary, PortfolioSummary, PortfolioType
from .service import load_snapshot

if TYPE_CHECKING:
    from ...context import AppContext
    from ...core.models import Portfolio
    from ...core.stats import Period


class StockTradingType(PortfolioType):
    key = "stock_trading"
    label = "Stock Trading"
    icon = "trending-up"
    entry_label = "New Trade"
    has_cash_ledger = False  # positions only; cash (e.g. dividends) belongs to a separate cash portfolio
    description = "Swing trades sized by risk, with R multiples, a stop ladder and profit tranches."

    def create_tab(self, ctx: AppContext, portfolio: Portfolio):
        from .tab import PortfolioTab

        return PortfolioTab(ctx, portfolio)

    def summary_metrics(self, ctx: AppContext, portfolio: Portfolio) -> PortfolioSummary:
        snap = load_snapshot(ctx.repos, portfolio, ctx.today())
        realized = sum((lot.realized_pl for lot in snap.lots), start=ZERO)
        st = trade_stats(closed_lot_units(snap.lots))
        return PortfolioSummary(
            value=snap.value, cash=ZERO, day_change=snap.day_change, unrealized_pl=snap.unrealized_pl,
            realized_pl=realized, open_risk=snap.open_risk,
            extras={"snapshot": snap, "dividends": snap.dividends, "deployed": snap.deployed,
                    "expectancy": st.expectancy, "open_positions": len(snap.open_positions)},
        )

    def dashboard_summary(self, ctx: AppContext, portfolio: Portfolio, period: Period) -> DashboardSummary:
        from datetime import timedelta
        from decimal import Decimal

        from ...core.equity import pl_curve
        from .dashboard import build_summary

        today = ctx.today()
        snap = load_snapshot(ctx.repos, portfolio, today)
        curve = []
        if snap.trades:
            symbols = sorted({t.symbol for t in snap.trades})
            first = min([t.trade_date for t in snap.trades] + [e.event_date for e in snap.cash_events])
            closes = ctx.repos.market.closes(symbols, since=first - timedelta(days=7))
            quotes = ctx.repos.market.get_quotes(symbols)
            curve = pl_curve(snap.trades, snap.cash_events, closes, today, start=period.start,
                             last_prices={s: q.last for s, q in quotes.items()})
        warn = Decimal(str(ctx.setting("alerts.open_risk_warn_pct") or 0.06))
        return build_summary(snap, curve, period, warn)
