"""CashType: savings accounts and money market funds, with expected interest and links to other portfolios."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import DashboardSummary, PortfolioSummary, PortfolioType
from .service import load_cash

if TYPE_CHECKING:
    from ...context import AppContext
    from ...core.models import Portfolio
    from ...core.stats import Period


class CashType(PortfolioType):
    key = "cash"
    label = "Cash"
    icon = "piggy-bank"
    entry_label = "New Account"
    cash_label = "Move Money"
    has_cash_ledger = True
    uses_risk_settings = False
    tracks_dividends = False
    description = ("Savings accounts and money market funds. Tracks balances, APY and interest (proposed each "
                   "month for you to confirm). Link a Stock Trading or Long-term portfolio to an account to settle "
                   "its buys, sells and dividends there.")

    def create_tab(self, ctx: AppContext, portfolio: Portfolio):
        from .tab import CashTab

        return CashTab(ctx, portfolio)

    def summary_metrics(self, ctx: AppContext, portfolio: Portfolio) -> PortfolioSummary:
        snap = load_cash(ctx.repos, portfolio, ctx.today())
        return PortfolioSummary(value=snap.balance, cash=snap.balance, day_change=None, unrealized_pl=None,
                                realized_pl=None, open_risk=None,
                                extras={"snapshot": snap, "apy": snap.weighted_apy,
                                        "est_interest": snap.est_annual_interest})

    def dashboard_summary(self, ctx: AppContext, portfolio: Portfolio, period: Period) -> DashboardSummary:
        from .dashboard import build_summary

        return build_summary(portfolio, load_cash(ctx.repos, portfolio, ctx.today()), period)
