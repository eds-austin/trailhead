"""Portfolio dialog: name, type, max amount, risk %, and the cash account it settles in."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

from PySide6.QtCore import QDate, QTimer
from PySide6.QtWidgets import QAbstractSpinBox, QComboBox, QDateEdit, QLineEdit, QWidget

from ...context import AppContext
from ...core.models import CashLink, Portfolio
from ...portfolio_types import registry
from .. import format as fmt
from ..widgets.common import label
from ..widgets.number_edit import NumberEdit
from .base import AppDialog, FieldGrid


class PortfolioDialog(AppDialog):
    def __init__(self, ctx: AppContext, portfolio: Portfolio | None = None, parent: QWidget | None = None):
        super().__init__("Edit portfolio" if portfolio else "New portfolio", parent, width=460,
                         primary_text="Save" if portfolio else "Create portfolio")
        self.ctx = ctx
        self.portfolio = portfolio
        self.result: Portfolio | None = None
        g = FieldGrid()
        self.grid = g
        self.name = QLineEdit(portfolio.name if portfolio else "")
        self.name.setPlaceholderText("e.g. Swing Trading")
        g.add("name", "Name", self.name)
        g.next_row()
        self.type = QComboBox()
        for t in registry.all_types():
            self.type.addItem(t.label, t.key)
        if portfolio:
            self.type.setCurrentIndex(max(0, self.type.findData(portfolio.type)))
            self.type.setEnabled(False)
        g.add("type", "Type", self.type)
        g.next_row()
        self.max_amount = NumberEdit(2, placeholder="100,000")
        self.max_amount.set_value(portfolio.max_amount if portfolio and portfolio.max_amount else Decimal(100000))
        self.max_label = g.add("max", "Max amount", self.max_amount)
        g.next_row()
        self.risk = NumberEdit(3, placeholder="1.0")
        self.risk.set_value((portfolio.risk_pct if portfolio else Decimal("0.01")) * 100)
        self.risk_label = g.add("risk", "Risk per trade %", self.risk)
        g.next_row()
        link = ctx.repos.cash_accounts.link_for(portfolio.id) if portfolio and portfolio.id else None
        self._link = link
        self.settles = QComboBox()
        self.settles.addItem("No cash account", None)
        owners = {p.id: p.name for p in ctx.repos.portfolios.list()}
        for a in ctx.repos.cash_accounts.list():
            if not a.closed or (link and link.account_id == a.id):
                self.settles.addItem(f"{a.name}  ·  {owners.get(a.portfolio_id, '')}", a.id)
        self.settles.setToolTip("Sells, buys and cash dividends from this date move money in that account of a "
                                "Cash portfolio. It doesn't count in Deployed / Max.")
        if link:
            self.settles.setCurrentIndex(max(0, self.settles.findData(link.account_id)))
        self.settles_label = g.add("settles", "Settles in", self.settles)
        g.next_row()
        since = link.since if link else ctx.today()
        self.since = QDateEdit(QDate(since.year, since.month, since.day))
        self.since.setCalendarPopup(True)
        self.since.setDisplayFormat("yyyy-MM-dd")
        self.since.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.since.setToolTip("Trades and dividends from this date on settle in the account; earlier ones don't")
        self.since_label = g.add("since", "From", self.since)
        self.settles.currentIndexChanged.connect(lambda _i: self._on_type())
        self.body.addLayout(g)
        self.type_hint = label("", role="muted")
        self.type_hint.setWordWrap(True)
        self.body.addWidget(self.type_hint)
        self.hint = label("", role="muted")
        self.body.addWidget(self.hint)
        self.max_amount.textChanged.connect(self._update_hint)
        self.risk.textChanged.connect(self._update_hint)
        self.type.currentIndexChanged.connect(lambda _i: self._on_type())
        self._on_type()

    def _uses_risk(self) -> bool:
        return registry.get(self.type.currentData()).uses_risk_settings

    def _on_type(self) -> None:
        t = registry.get(self.type.currentData())
        for w in (self.max_amount, self.max_label, self.risk, self.risk_label):
            w.setVisible(t.uses_risk_settings)
        can_link = t.key != "cash" and self.settles.count() > 1
        for w in (self.settles, self.settles_label):
            w.setVisible(can_link)
        for w in (self.since, self.since_label):
            w.setVisible(can_link and self.settles.currentData() is not None)
        self.type_hint.setText(t.description)
        self.type_hint.setVisible(bool(t.description) and self.portfolio is None)
        self._update_hint()
        QTimer.singleShot(0, self.adjustSize)

    def _update_hint(self) -> None:
        m, r = self.max_amount.value(), self.risk.value()
        if not self._uses_risk():
            self.hint.setText("")
        elif m and r:
            self.hint.setText(f"Risk per trade = {fmt.money(m * r / 100)}")
        else:
            self.hint.setText("")

    def submit(self) -> bool:
        g = self.grid
        g.clear_errors()
        name = self.name.text().strip()
        m, r = self.max_amount.value(), self.risk.value()
        ok = True
        if not name:
            g.set_error("name", "Enter a name")
            ok = False
        if not self._uses_risk():  # not used by this type; keep what is stored
            m = self.portfolio.max_amount if self.portfolio else Decimal(0)
            r = self.portfolio.risk_pct * 100 if self.portfolio else Decimal(1)
        elif m is None or m <= 0:
            g.set_error("max", "Enter the maximum amount")
            ok = False
        if r is None or r <= 0 or r > 100:
            g.set_error("risk", "Enter a risk % between 0 and 100")
            ok = False
        if not ok:
            return False
        risk_pct = (r / 100).normalize()  # type: ignore[operator]
        if self.portfolio:
            p = replace(self.portfolio, name=name, max_amount=m, risk_pct=risk_pct)
            self.ctx.repos.portfolios.update(p)
            self.result = p
        else:
            self.result = self.ctx.repos.portfolios.create(
                Portfolio(None, name, m, risk_pct, type=self.type.currentData()))  # type: ignore[arg-type]
        self._save_link()
        self.ctx.events.portfolios_changed.emit()
        return True

    def _save_link(self) -> None:
        p = self.result
        if p is None or p.id is None or p.type == "cash":
            return
        aid = self.settles.currentData()
        repo = self.ctx.repos.cash_accounts
        if aid is None:
            if self._link is not None:
                repo.unlink(p.id)
            return
        q = self.since.date()
        since = date(q.year(), q.month(), q.day())
        if self._link != CashLink(p.id, aid, since):
            repo.link(CashLink(p.id, aid, since))
            self.ctx.data_changed(p.id)
