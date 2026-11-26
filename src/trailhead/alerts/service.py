"""AlertService: evaluate alerts for all portfolios, dedup into alerts_log, announce new ones."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from PySide6.QtCore import QObject, Signal

from ..core.models import AlertRecord
from ..data.repo import Repos
from ..portfolio_types.long_term.service import load_holdings
from ..portfolio_types.stock_trading.service import load_snapshot
from .engine import Alert, AlertSettings, evaluate, evaluate_holdings


class AlertService(QObject):
    new_alerts = Signal(list)  # list[Alert]
    changed = Signal()

    def __init__(self, repos: Repos, today_fn, parent: QObject | None = None):
        super().__init__(parent)
        self.repos = repos
        self.today_fn = today_fn

    def settings(self) -> AlertSettings:
        s = self.repos.settings
        return AlertSettings(Decimal(str(s.get("alerts.near_stop_pct") or 0.02)),
                             int(s.get("alerts.earnings_days") or 7))

    def run(self) -> list[Alert]:
        fresh: list[Alert] = []
        cfg = self.settings()
        now = datetime.now().replace(microsecond=0)
        for p in self.repos.portfolios.list():
            if p.type == "stock_trading":
                alerts = evaluate(load_snapshot(self.repos, p, self.today_fn()), cfg)
            elif p.type == "long_term":
                alerts = evaluate_holdings(load_holdings(self.repos, p, self.today_fn()), cfg)
            else:
                continue
            for a in alerts:
                rec = AlertRecord(None, a.portfolio_id, a.symbol, a.lot_id, a.kind.value, a.level, a.message, now)
                if self.repos.alerts.insert_if_new(rec) is not None:
                    fresh.append(a)
        if fresh:
            self.new_alerts.emit(fresh)
            self.changed.emit()
        return fresh

    def unacked_count(self) -> int:
        return self.repos.alerts.count_unacked()

    def acknowledge(self, alert_id: int) -> None:
        self.repos.alerts.acknowledge(alert_id)
        self.changed.emit()

    def acknowledge_all(self) -> None:
        self.repos.alerts.acknowledge_all()
        self.changed.emit()
