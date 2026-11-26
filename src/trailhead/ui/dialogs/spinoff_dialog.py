"""Record a spin-off: the new company's shares join the portfolio and take part of the parent's cost basis."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import QAbstractSpinBox, QDateEdit, QLineEdit, QPushButton, QWidget

from ...context import AppContext
from ...core.holdings import HoldingView
from ...core.models import Portfolio
from ...core.money import dsum
from ...core.spinoffs import default_cost_pct
from ...core.splits import looks_like_spinoff
from ...data.repo import RepoError
from ...portfolio_types.spinoffs import record_spinoff
from .. import format as fmt
from .. import theme
from ..widgets.common import label
from ..widgets.number_edit import NumberEdit
from .base import AppDialog, FieldGrid, inset_panel


def provider_spinoff(ctx: AppContext, holding: HoldingView) -> tuple[date, Decimal] | None:
    """The latest odd-ratio 'split' the provider lists for the parent while held: Yahoo's record of a spin-off."""
    done = {s.ex_date for s in ctx.repos.spinoffs.list(holding.position.lots[0].buy.portfolio_id)
            if s.parent == holding.symbol}
    end = holding.position.close_date
    found = [m for m in ctx.repos.splits.market([holding.symbol]) if looks_like_spinoff(m.ratio)
             and m.ex_date > holding.position.open_date and (end is None or m.ex_date <= end)
             and m.ex_date not in done]
    return (found[-1].ex_date, found[-1].ratio) if found else None


class SpinoffDialog(AppDialog):
    def __init__(self, ctx: AppContext, portfolio: Portfolio, holding: HoldingView, parent: QWidget | None = None):
        super().__init__(f"{holding.symbol} spin-off", parent, width=520, primary_text="Record spin-off")
        self.ctx, self.portfolio, self.holding = ctx, portfolio, holding
        self.result = None
        found = provider_spinoff(ctx, holding)
        self.factor = found[1] if found else None
        self.factor_date = found[0] if found else None
        msg = label(f"The new company's shares join this portfolio as a holding, and part of {holding.symbol}'s "
                    "cost basis moves to them. Total value and return don't change on the day.", role="muted")
        msg.setWordWrap(True)
        self.body.addWidget(msg)
        g = FieldGrid()
        self.grid = g
        self.child = QLineEdit()
        self.child.setPlaceholderText("e.g. FDXF")
        self.child.setFont(theme.font("body_strong"))
        self.child.textEdited.connect(lambda t: self.child.setText(t.upper()))
        find = QPushButton("Find symbol…")
        find.setProperty("variant", "ghost")
        find.setProperty("scale", "small")
        find.setCursor(Qt.CursorShape.PointingHandCursor)
        find.setStyleSheet(f"color: {theme.palette().accent};")
        find.setAutoDefault(False)
        find.clicked.connect(self._find)
        g.add("child", "New symbol", self.child, 0, extra=find, span=2)
        g.next_row()
        d = found[0] if found else ctx.today()
        self.ex = QDateEdit(QDate(d.year, d.month, d.day))
        self.ex.setCalendarPopup(True)
        self.ex.setDisplayFormat("yyyy-MM-dd")
        self.ex.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        g.add("ex", "Ex-date", self.ex, 0)
        self.shares = NumberEdit(4, placeholder="0")
        g.add("shares", "Shares received", self.shares, 1)
        g.next_row()
        self.pct = NumberEdit(4, placeholder="19.4")
        if self.factor:
            self.pct.set_value(default_cost_pct(self.factor) * 100)
        g.add("pct", "Cost moved %", self.pct, 0)
        self.body.addLayout(g)
        hint = label(f"Pre-filled from Yahoo's price adjustment on that date (×{self.factor}). The company's tax "
                     "notice (Form 8937) or your broker may give an exact figure." if self.factor else
                     "From the company's tax notice (Form 8937) or your broker's cost-basis page.", role="muted")
        hint.setWordWrap(True)
        self.body.addWidget(hint)
        panel, pv = inset_panel()
        self.line = label("", font_token="num")
        self.line.setWordWrap(True)
        pv.addWidget(self.line)
        self.body.addWidget(panel)
        for w in (self.shares, self.pct):
            w.textChanged.connect(lambda _t: self._update())
        self.child.textChanged.connect(lambda _t: self._update())
        self.ex.dateChanged.connect(lambda _d: self._update())
        self._update()
        self.child.setFocus()

    def _ex(self) -> date:
        q = self.ex.date()
        return date(q.year(), q.month(), q.day())

    def _basis(self) -> Decimal:
        day = self._ex() - timedelta(days=1)
        total = Decimal(0)
        for lot in self.holding.lots:
            if lot.entry_date > day:
                continue
            held = lot.shares - dsum(s.shares for s in lot.sells if s.trade_date <= day)
            if held > 0:
                total += held * lot.entry_at(day)
        return total

    def _find(self) -> None:
        from .symbol_picker_dialog import pick_symbol

        new = pick_symbol(self.ctx, self.child.text() or self.holding.symbol, self,
                          intro="Search for the spun-off company by its symbol or name.")
        if new:
            self.child.setText(new)

    def _update(self) -> None:
        h, basis = self.holding, self._basis()
        pct, n, child = self.pct.value(), self.shares.value(), self.child.text().strip() or "the new company"
        if not basis:
            self.line.setText(f"No {h.symbol} shares were held the day before this date")
            return
        if not pct or not 0 < pct < 100:
            self.line.setText(f"{h.symbol} cost basis then: {fmt.money(basis)}")
            return
        moved = basis * pct / 100
        text = f"{h.symbol} cost {fmt.money(basis)} → {fmt.money(basis - moved)}\n{child}: cost {fmt.money(moved)}"
        if n:
            text += f" for {fmt.shares(n)} sh = {fmt.price(moved / n)} per share"
        self.line.setText(text)

    def submit(self) -> bool:
        g = self.grid
        g.clear_errors()
        child, n, pct = self.child.text().strip().upper(), self.shares.value(), self.pct.value()
        if not child:
            g.set_error("child", "Enter the new company's symbol")
            return False
        if not n or n <= 0:
            g.set_error("shares", "Enter the shares you received")
            return False
        if not pct or not 0 < pct < 100:
            g.set_error("pct", "Between 0 and 100")
            return False
        if self._ex() > self.ctx.today():
            g.set_error("ex", "Cannot be in the future")
            return False
        try:
            self.result = record_spinoff(self.ctx.repos, self.portfolio.id, self.holding, child, self._ex(), n,  # type: ignore[arg-type]
                                         pct / 100, self.factor if self._ex() == self.factor_date else None)
        except RepoError as e:
            self.show_error(str(e))
            return False
        return True

