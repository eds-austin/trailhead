"""AppContext: shared services handed to tabs and dialogs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Signal

from .data.repo import Repos

if TYPE_CHECKING:
    from .alerts.service import AlertService
    from .market.service import MarketService


class AppEvents(QObject):
    portfolio_data_changed = Signal(int)  # trades / cash / stops of a portfolio changed
    portfolios_changed = Signal()  # created / renamed / archived
    market_updated = Signal()  # quotes / history / instruments changed
    alerts_changed = Signal()
    settings_changed = Signal()
    toast = Signal(str, str, str)  # title, body, severity
    request_new_trade = Signal(int, object)  # portfolio id, prefill dict
    open_page = Signal(str)  # page key, e.g. "portfolio:3"


@dataclass
class AppContext:
    repos: Repos
    db_path: Path
    events: AppEvents = field(default_factory=AppEvents)
    today_fn: Callable[[], date] = date.today
    market: MarketService | None = None
    alerts: AlertService | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    def today(self) -> date:
        return self.today_fn()

    def setting(self, key: str, default: Any = None) -> Any:
        return self.repos.settings.get(key, default)

    def toast(self, title: str, body: str = "", severity: str = "info") -> None:
        self.events.toast.emit(title, body, severity)

    def data_changed(self, portfolio_id: int) -> None:
        self.events.portfolio_data_changed.emit(portfolio_id)
