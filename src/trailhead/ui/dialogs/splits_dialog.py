"""Stock splits: review the provider's splits (Apply / Already in my numbers), undo recorded ones, record by hand."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QHBoxLayout,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from ...context import AppContext
from ...core.models import Portfolio
from ...core.splits import APPLIED, looks_like_spinoff, ratio_label
from ...data.repo import RepoError
from ...portfolio_types.splits import pending_splits_for, record_split, undo_split
from .. import format as fmt
from .. import theme
from ..widgets.common import button, label
from ..widgets.number_edit import NumberEdit
from .base import AppDialog, FieldGrid

INTRO = ("Prices and dividends from Yahoo Finance are split-adjusted. Apply a split to show your earlier trades in "
         "post-split shares (6 sh @ 2,034.50 → 300 sh @ 40.69, same cost); your entries are kept as typed. Choose "
         "Already in my numbers if you entered them split-adjusted. Yahoo also lists some spin-offs as odd splits "
         "(GE 1.253-for-1): those are not splits; choose Already in my numbers.")


def _table(cols: list[str], widths: tuple[int, ...]) -> QTableWidget:
    t = QTableWidget(0, len(cols))
    t.setObjectName("plain")
    t.setHorizontalHeaderLabels(cols)
    t.verticalHeader().hide()
    t.setShowGrid(False)
    t.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    for i, w in enumerate(widths):
        t.setColumnWidth(i, w)
    t.horizontalHeader().setStretchLastSection(True)
    return t


def _buttons(*btns) -> QWidget:
    box = QWidget()
    h = QHBoxLayout(box)
    h.setContentsMargins(4, 0, 4, 0)
    h.setSpacing(6)
    for b in btns:
        h.addWidget(b)
    h.addStretch(1)
    return box


class SplitsDialog(AppDialog):
    def __init__(self, ctx: AppContext, portfolio: Portfolio | None = None, symbol: str | None = None,
                 parent: QWidget | None = None):
        super().__init__("Stock splits", parent, width=900, primary_text="Done")
        self.ctx, self.only_portfolio, self.symbol = ctx, portfolio, symbol
        self.cancel_btn.hide()
        intro = label(INTRO, role="muted")
        intro.setWordWrap(True)
        self.body.addWidget(intro)
        self.body.addWidget(label("TO REVIEW", role="caption"))
        self.pending = _table(["Portfolio", "Symbol", "Date", "Split", "Held then", ""], (140, 80, 96, 110, 130))
        self.pending.setMinimumHeight(150)
        self.body.addWidget(self.pending)
        self.pending_empty = label("Nothing to review.", role="muted")
        self.body.addWidget(self.pending_empty)
        self.body.addWidget(label("RECORDED", role="caption"))
        self.recorded = _table(["Portfolio", "Symbol", "Date", "Split", "Status", ""], (140, 80, 96, 110, 170))
        self.recorded.setMinimumHeight(130)
        self.body.addWidget(self.recorded)
        self.recorded_empty = label("No splits recorded.", role="muted")
        self.body.addWidget(self.recorded_empty)
        manual = button("Record split…", icon="plus")
        manual.clicked.connect(self._manual)
        self.footer_left.addWidget(manual)
        self.reload()

    def _portfolios(self) -> list[Portfolio]:
        if self.only_portfolio is not None:
            return [self.only_portfolio]
        return self.ctx.repos.portfolios.list()

    def reload(self) -> None:
        today = self.ctx.today()
        items = [(p, s) for p in self._portfolios() for s in pending_splits_for(self.ctx.repos, p, today)
                 if self.symbol is None or s.symbol == self.symbol]
        num = theme.font("num")
        self.pending.setRowCount(len(items))
        for r, (p, s) in enumerate(items):
            spin = looks_like_spinoff(s.ratio)
            held = ("probably a spin-off" if spin else
                    f"{fmt.shares(s.shares_before)} → {fmt.shares(s.shares_after)} sh")
            for c, v in enumerate((p.name, s.symbol, fmt.short_date(s.ex_date), s.label, held)):
                it = QTableWidgetItem(v)
                if c >= 2 and not (spin and c == 4):
                    it.setFont(num)
                if spin and c == 4:
                    it.setForeground(theme.qcolor(theme.palette().warning, 1.0))
                    it.setToolTip("Not a whole-number ratio: Yahoo records spin-offs this way. The share count "
                                  "did not change; choose Already in my numbers.")
                self.pending.setItem(r, c, it)
            apply = button("Apply", "ghost" if spin else "primary", size="small")
            apply.setToolTip(f"Show {s.symbol} trades before {fmt.short_date(s.ex_date)} in post-split shares"
                             + ("\nCareful: this looks like a spin-off, not a split" if spin else ""))
            apply.clicked.connect(lambda _=False, pp=p, ss=s: self._record(pp, ss.symbol, ss.ex_date, ss.ratio, True))
            ign = button("Already in my numbers", "primary" if spin else "ghost", size="small")
            extra = []
            if spin and p.type == "long_term":
                so = button("Record spin-off…", "primary", size="small")
                so.setToolTip("Add the new company's shares and move part of the cost basis to them")
                so.clicked.connect(lambda _=False, pp=p, ss=s: self._spinoff(pp, ss))
                extra.append(so)
                ign.setProperty("variant", "ghost")
            ign.setToolTip("You entered the earlier trades split-adjusted (or this is a spin-off adjustment)")
            ign.clicked.connect(lambda _=False, pp=p, ss=s: self._record(pp, ss.symbol, ss.ex_date, ss.ratio, False))
            self.pending.setCellWidget(r, 5, _buttons(*extra, ign, apply) if spin else _buttons(apply, ign))
            self.pending.setRowHeight(r, 34)
        self.pending.setVisible(bool(items))
        self.pending_empty.setVisible(not items)

        rec = [(p, s) for p in self._portfolios() for s in self.ctx.repos.splits.list(p.id)  # type: ignore[arg-type]
               if self.symbol is None or s.symbol == self.symbol]
        self.recorded.setRowCount(len(rec))
        for r, (p, s) in enumerate(rec):
            status = "Applied" if s.status == APPLIED else "Already in my numbers"
            for c, v in enumerate((p.name, s.symbol, fmt.short_date(s.ex_date), s.label, status)):
                it = QTableWidgetItem(v)
                if c >= 2 and c < 4:
                    it.setFont(num)
                self.recorded.setItem(r, c, it)
            undo = button("Undo", "ghost", size="small")
            undo.setToolTip("Forget this decision; the split is proposed again if Yahoo lists it")
            undo.clicked.connect(lambda _=False, pp=p, ss=s: self._undo(pp, ss))
            self.recorded.setCellWidget(r, 5, _buttons(undo))
            self.recorded.setRowHeight(r, 34)
        self.recorded.setVisible(bool(rec))
        self.recorded_empty.setVisible(not rec)

    def _record(self, p: Portfolio, symbol: str, ex: date, ratio: Decimal, apply: bool) -> None:
        try:
            record_split(self.ctx.repos, p.id, symbol, ex, ratio, apply)  # type: ignore[arg-type]
        except RepoError as e:
            self.show_error(str(e))
            return
        self.ctx.data_changed(p.id)  # type: ignore[arg-type]
        if apply:
            self.ctx.toast(f"{symbol} {ratio_label(ratio)} split applied",
                           "Earlier trades are shown in post-split shares", "success")
        self.reload()

    def _spinoff(self, p: Portfolio, s) -> None:
        from ...portfolio_types.long_term.service import load_holdings
        from ...portfolio_types.stock_trading.service import dividends_by_position
        from .spinoff_dialog import SpinoffDialog

        snap = load_holdings(self.ctx.repos, p, self.ctx.today())
        key = next((k for k, v in dividends_by_position(snap, [s]).items() if v), None)
        h = snap.holding(key) if key else None
        if h is None:
            self.show_error(f"No {s.symbol} holding was found on {fmt.short_date(s.ex_date)}")
            return
        dlg = SpinoffDialog(self.ctx, p, h, parent=self)
        if dlg.exec():
            self.ctx.data_changed(p.id)  # type: ignore[arg-type]
            self.reload()

    def _undo(self, p: Portfolio, s) -> None:
        try:
            undo_split(self.ctx.repos, p.id, s)  # type: ignore[arg-type]
        except RepoError as e:
            self.show_error(str(e))
            return
        self.ctx.data_changed(p.id)  # type: ignore[arg-type]
        self.reload()

    def _manual(self) -> None:
        dlg = ManualSplitDialog(self.ctx, self.only_portfolio, self.symbol, parent=self)
        if dlg.exec():
            self.reload()


class ManualSplitDialog(AppDialog):
    """Record a split the data provider doesn't list (or in offline mode)."""

    def __init__(self, ctx: AppContext, portfolio: Portfolio | None = None, symbol: str | None = None,
                 parent: QWidget | None = None):
        super().__init__("Record split", parent, width=480, primary_text="Record split")
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
        self.symbol = QLineEdit(symbol or "")
        self.symbol.textEdited.connect(lambda t: self.symbol.setText(t.upper()))
        g.add("symbol", "Symbol", self.symbol, 0)
        t = ctx.today()
        self.date = QDateEdit(QDate(t.year, t.month, t.day))
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("yyyy-MM-dd")
        self.date.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        g.add("date", "Effective date", self.date, 1)
        g.next_row()
        self.new = NumberEdit(4, placeholder="50")
        self.old = NumberEdit(4, placeholder="1")
        self.old.set_value(Decimal(1))
        g.add("new", "New shares", self.new, 0)
        g.add("old", "for old shares", self.old, 1)
        self.body.addLayout(g)
        self.apply = QCheckBox("Apply it: my earlier trades are in pre-split shares")
        self.apply.setChecked(True)
        self.body.addWidget(self.apply)
        self.line = label("", role="muted")
        self.body.addWidget(self.line)
        for w in (self.new, self.old):
            w.textChanged.connect(lambda _t: self._update())
        self._update()

    def _ratio(self) -> Decimal | None:
        n, o = self.new.value(), self.old.value()
        return n / o if n and o else None

    def _update(self) -> None:
        r = self._ratio()
        self.line.setText(f"{ratio_label(r)}: 1 share before = {fmt.shares(r)} after" if r else "")
        self.line.setAlignment(Qt.AlignmentFlag.AlignLeft)

    def submit(self) -> bool:
        self.grid.clear_errors()
        sym, r, pid = self.symbol.text().strip().upper(), self._ratio(), self.portfolio.currentData()
        if pid is None:
            self.grid.set_error("portfolio", "Create a portfolio first")
            return False
        if not sym:
            self.grid.set_error("symbol", "Enter a symbol")
            return False
        if r is None or r <= 0 or r == 1:
            self.grid.set_error("new", "Enter the split ratio, e.g. 50 for 1")
            return False
        q = self.date.date()
        try:
            record_split(self.ctx.repos, pid, sym, date(q.year(), q.month(), q.day()), r, self.apply.isChecked())
        except RepoError as e:
            self.show_error(str(e))
            return False
        self.ctx.data_changed(pid)
        return True
