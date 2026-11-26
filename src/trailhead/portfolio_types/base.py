"""PortfolioType ABC: each portfolio kind plugs in a tab, summary metrics and an entry action."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from ..core.models import Portfolio

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

    from ..context import AppContext
    from ..core.stats import Period


@dataclass(frozen=True)
class PortfolioSummary:
    value: Decimal
    cash: Decimal
    day_change: Decimal | None
    unrealized_pl: Decimal | None
    realized_pl: Decimal | None
    open_risk: Decimal | None
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Holding:
    """One open holding, for the Dashboard's biggest moves and sector allocation."""

    symbol: str
    market_value: Decimal
    day_change: Decimal | None = None
    day_pct: Decimal | None = None
    sector: str | None = None


@dataclass(frozen=True)
class AttentionItem:
    """A Dashboard 'Needs attention · coming up' line. tone: negative | warning | info | positive."""

    tone: str
    title: str
    detail: str = ""
    sort_key: tuple = ()


@dataclass(frozen=True)
class DashboardSummary:
    """Everything the Dashboard shows for one portfolio over a period. Money is None when unknown or not
    applicable to the portfolio type (shown as a dash)."""

    portfolio: Portfolio
    subtitle: str
    value: Decimal
    day_change: Decimal | None = None
    period_pl: Decimal | None = None  # realized + unrealized + income earned in the period
    unrealized_pl: Decimal | None = None
    income: Decimal = Decimal(0)  # dividends / interest received in the period
    open_risk: Decimal | None = None
    open_risk_pct: Decimal | None = None
    open_risk_warn: bool = False
    pl_curve: list[tuple[date, Decimal]] = field(default_factory=list)  # cumulative P/L within the period
    holdings: list[Holding] = field(default_factory=list)
    attention: list[AttentionItem] = field(default_factory=list)


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    kind: str = "text"  # text | number | percent | bool | choice
    default: Any = None
    choices: tuple[str, ...] = ()


class PortfolioType(ABC):
    key: str
    label: str
    icon: str
    entry_label: str = "New entry"
    has_cash_ledger: bool = True  # shows the Cash button / Ctrl+D (deposits, withdrawals)
    cash_label: str = "Cash"  # that button's text
    uses_risk_settings: bool = True  # max amount and risk % per trade (R-multiple sizing)
    tracks_dividends: bool = True  # dividends are proposed for its holdings (Data ▸ Pending dividends)
    description: str = ""

    @abstractmethod
    def create_tab(self, ctx: AppContext, portfolio: Portfolio) -> QWidget: ...

    @abstractmethod
    def summary_metrics(self, ctx: AppContext, portfolio: Portfolio) -> PortfolioSummary: ...

    def dashboard_summary(self, ctx: AppContext, portfolio: Portfolio, period: Period) -> DashboardSummary:
        """Dashboard row for this portfolio. The default uses summary_metrics(); types override it to add the
        period P/L, the P/L curve, holdings and attention items."""
        s = self.summary_metrics(ctx, portfolio)
        return DashboardSummary(portfolio, self.label, s.value, s.day_change, None, s.unrealized_pl,
                                open_risk=s.open_risk)

    def settings_fields(self) -> list[FieldSpec]:
        return []
