"""Pending dividend review (Accept / Edit amount / Dismiss) and manual dividend entry."""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QComboBox,
    QDateEdit,
    QHBoxLayout,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from ...context import AppContext
from ...core.dividends import DividendProposal
from ...core.models import CashEvent, CashKind, Portfolio
from ...portfolio_types import registry
from ...portfolio_types.stock_trading.service import accept_dividend, pending_dividends_for
from .. import format as fmt
from .. import theme
from ..widgets.common import button, label
from ..widgets.number_edit import NumberEdit
from .base import AppDialog, FieldGrid

COLS = ["Portfolio", "Symbol", "Ex-date", "Pay date", "$/sh", "Shares", "Amount", ""]


def _qdate(d: date) -> QDate:
    return QDate(d.year, d.month, d.day)


class DividendReviewDialog(AppDialog):
    def __init__(self, ctx: AppContext, parent: QWidget | None = None):
        super().__init__("Pending dividends", parent, width=860, primary_text="Done")
        self.ctx = ctx
        self.changed = False
        self.cancel_btn.hide()
        self.body.addWidget(label("Dividends whose ex-date found shares in your portfolio. Accepting records a "
                                  "DIVIDEND cash event (amount is editable). In a Long-term Holdings portfolio, "
                                  "Accept… also lets you reinvest it.", role="muted"))
        self.table = QTableWidget(0, len(COLS))
        self.table.setObjectName("plain")
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setMinimumHeight(300)
        for i, w in enumerate((130, 70, 96, 96, 70, 70, 110, 170)):
            self.table.setColumnWidth(i, w)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.body.addWidget(self.table)
        self.empty = label("No pending dividends.", role="muted")
        self.body.addWidget(self.empty)
        manual = button("Manual dividend…", icon="plus")
        manual.clicked.connect(self._manual)
        self.footer_left.addWidget(manual)
        self.reload()

    def _items(self) -> list[tuple[Portfolio, DividendProposal]]:
        out = []
        for p in self.ctx.repos.portfolios.list():
            if registry.get(p.type).tracks_dividends:
                out += [(p, d) for d in pending_dividends_for(self.ctx.repos, p, self.ctx.today())]
        return out

    def reload(self) -> None:
        items = self._items()
        self.table.setRowCount(len(items))
        self.empty.setVisible(not items)
        self.table.setVisible(bool(items))
        num = theme.font("num")
        self._edits: list[NumberEdit] = []
        for r, (p, d) in enumerate(items):
            vals = [p.name, d.symbol, fmt.short_date(d.ex_date), fmt.short_date(d.pay_date),
                    fmt.number(d.amount_per_share, 4), fmt.shares(d.shares)]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c >= 2:
                    it.setFont(num)
                if c >= 4:
                    it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(r, c, it)
            amt = NumberEdit(2)
            amt.set_value(d.amount)
            amt.setMinimumHeight(26)
            self.table.setCellWidget(r, 6, amt)
            self._edits.append(amt)
            box = QWidget()
            h = QHBoxLayout(box)
            h.setContentsMargins(4, 0, 4, 0)
            h.setSpacing(6)
            acc = button("Accept…" if p.type == "long_term" else "Accept", "primary", size="small")
            acc.clicked.connect(lambda _=False, pp=p, dd=d, e=amt: self._accept(pp, dd, e))
            dis = button("Dismiss", "ghost", size="small")
            dis.clicked.connect(lambda _=False, pp=p, dd=d: self._dismiss(pp, dd))
            h.addWidget(acc)
            h.addWidget(dis)
            self.table.setCellWidget(r, 7, box)
            self.table.setRowHeight(r, 34)

    def _accept(self, p: Portfolio, d: DividendProposal, edit: NumberEdit) -> None:
        amount = edit.value()
        if amount is None or amount <= 0:
            self.show_error("Enter a positive amount")
            return
        if p.type == "long_term":
            if not self._confirm_long_term(p, d, amount):
                return
        else:
            accept_dividend(self.ctx.repos, p.id, d, amount, self.ctx.today())  # type: ignore[arg-type]
        self._done(p)

    def _confirm_long_term(self, p: Portfolio, d: DividendProposal, amount) -> bool:
        """Long-term holdings: confirm with the option to reinvest (adds the DRIP shares to the holding)."""
        from ...portfolio_types.long_term.service import load_holdings
        from ...portfolio_types.stock_trading.service import dividends_by_position
        from .confirm_dividend_dialog import ConfirmDividendDialog

        snap = load_holdings(self.ctx.repos, p, self.ctx.today())
        key = next((k for k, ds in dividends_by_position(snap, [d]).items() if ds), None)
        holding = snap.holding(key) if key else None
        if holding is None:
            self.show_error(f"No {d.symbol} holding was found for this dividend")
            return False
        return bool(ConfirmDividendDialog(self.ctx, p, holding, d, amount, parent=self).exec())

    def _dismiss(self, p: Portfolio, d: DividendProposal) -> None:
        self.ctx.repos.dividend_reviews.dismiss(p.id, d.symbol, d.ex_date)  # type: ignore[arg-type]
        self._done(p)

    def _done(self, p: Portfolio) -> None:
        self.changed = True
        self.ctx.data_changed(p.id)  # type: ignore[arg-type]
        self.reload()

    def _manual(self) -> None:
        if ManualDividendDialog(self.ctx, parent=self).exec():
            self.changed = True
            self.reload()


class ManualDividendDialog(AppDialog):
    def __init__(self, ctx: AppContext, portfolio: Portfolio | None = None, parent: QWidget | None = None):
        super().__init__("Manual dividend", parent, width=480, primary_text="Add dividend")
        self.ctx = ctx
        g = FieldGrid()
        self.grid = g
        self.portfolio = QComboBox()
        for p in ctx.repos.portfolios.list():
            self.portfolio.addItem(p.name, p.id)
        if portfolio is not None:
            self.portfolio.setCurrentIndex(max(0, self.portfolio.findData(portfolio.id)))
        g.add("portfolio", "Portfolio", self.portfolio, span=2)
        g.next_row()
        self.symbol = QLineEdit()
        self.symbol.textEdited.connect(lambda t: self.symbol.setText(t.upper()))
        g.add("symbol", "Symbol", self.symbol, span=2)
        g.next_row()
        t = ctx.today()
        self.ex_date = QDateEdit(_qdate(t))
        self.pay_date = QDateEdit(_qdate(t))
        for de in (self.ex_date, self.pay_date):
            de.setCalendarPopup(True)
            de.setDisplayFormat("yyyy-MM-dd")
            de.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        g.add("ex", "Ex-date", self.ex_date, 0)
        g.add("pay", "Paid on", self.pay_date, 1)
        g.next_row()
        self.amount = NumberEdit(2)
        g.add("amount", "Amount", self.amount, 0)
        self.body.addLayout(g)

    def submit(self) -> bool:
        self.grid.clear_errors()
        sym = self.symbol.text().strip().upper()
        amt = self.amount.value()
        if self.portfolio.currentData() is None:
            self.grid.set_error("portfolio", "Create a portfolio first")
            return False
        if not sym:
            self.grid.set_error("symbol", "Enter a symbol")
            return False
        if amt is None or amt <= 0:
            self.grid.set_error("amount", "Enter a positive amount")
            return False
        q1, q2 = self.ex_date.date(), self.pay_date.date()
        ex = date(q1.year(), q1.month(), q1.day())
        paid = date(q2.year(), q2.month(), q2.day())
        if paid > self.ctx.today():
            self.grid.set_error("pay", "Pay date cannot be in the future")
            return False
        pid = self.portfolio.currentData()
        self.ctx.repos.cash.add(CashEvent(None, pid, paid, CashKind.DIVIDEND, amt, sym, ex, "Manual"))
        self.ctx.data_changed(pid)
        return True
