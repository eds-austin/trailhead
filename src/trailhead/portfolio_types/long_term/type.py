"""LongTermType: buy-and-hold stocks, ETFs, income investments and precious metals (no R multiples)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...core.money import ZERO
from ..base import DashboardSummary, PortfolioSummary, PortfolioType
from .service import load_holdings

if TYPE_CHECKING:
    from ...context import AppContext
    from ...core.models import Portfolio
    from ...core.stats import Period


class LongTermType(PortfolioType):
    key = "long_term"
    label = "Long-term Holdings"
    icon = "briefcase"
    entry_label = "New Trade"
    has_cash_ledger = False
    uses_risk_settings = False
    description = ("Buy-and-hold stocks, ETFs, income investments and precious metals. Tracks total return, "
                   "dividends, yield on cost and income; stops and targets are optional.")

    def create_tab(self, ctx: AppContext, portfolio: Portfolio):
        from .tab import LongTermTab

        return LongTermTab(ctx, portfolio)

    def summary_metrics(self, ctx: AppContext, portfolio: Portfolio) -> PortfolioSummary:
        snap = load_holdings(ctx.repos, portfolio, ctx.today())
        return PortfolioSummary(
            value=snap.value, cash=ZERO, day_change=snap.day_change, unrealized_pl=snap.unrealized_pl,
            realized_pl=snap.realized_pl, open_risk=None,
            extras={"snapshot": snap, "dividends": snap.dividends, "total_return": snap.total_return,
                    "est_income": snap.est_income, "open_positions": len(snap.open_holdings)},
        )

    def dashboard_summary(self, ctx: AppContext, portfolio: Portfolio, period: Period) -> DashboardSummary:
        from ..stock_trading.service import dividends_by_position, pending_dividends_for
        from .dashboard import build_summary
        from .service import pl_points

        today = ctx.today()
        snap = load_holdings(ctx.repos, portfolio, today)
        curve = pl_points(ctx.repos, snap, today, period.start)
        by_pos = dividends_by_position(snap, pending_dividends_for(ctx.repos, portfolio, today))
        pending = [d for ds in by_pos.values() for d in ds]
        return build_summary(snap, curve, period, pending)
