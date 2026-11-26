"""Record stop-out: asks for the actual sell price when a stop is hit and sells the remaining shares."""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QDate
from PySide6.QtWidgets import QAbstractSpinBox, QDateEdit, QWidget

from ...context import AppContext
from ...core.models import Portfolio, Side, Trade
from ...core.money import ZERO
from ...core.snapshot import LotView, PositionView
from ...data.repo import RepoError
from .. import format as fmt
from ..widgets.common import label
from ..widgets.number_edit import NumberEdit
from .base import AppDialog, FieldGrid, inset_panel


class StopOutDialog(AppDialog):
    def __init__(self, ctx: AppContext, portfolio: Portfolio, pv: PositionView, lot_id: int | None = None,
                 parent: QWidget | None = None):
        super().__init__(f"{pv.symbol} — stop hit", parent, width=460, primary_text="Record sale")
        self.ctx, self.portfolio, self.pv = ctx, portfolio, pv
        lots: list[LotView] = [lv for lv in pv.open_views if lv.stop_hit and (lot_id is None or lv.lot.id == lot_id)]
        if not lots:  # opened for a specific lot that isn't hit, or prices moved: sell what was asked for
            lots = [lv for lv in pv.open_views if lot_id is None or lv.lot.id == lot_id]
        self.lots = lots
        self.shares = sum((lv.remaining for lv in lots), ZERO)
        stop = max((lv.lot.current_stop for lv in lots), default=None)
        intro = (f"Last {fmt.price(pv.last)} ≤ stop {fmt.price(stop)}. " if pv.last is not None and stop is not None
                 and pv.last <= stop else "")
        msg = label(f"{intro}Enter the actual sell price to record the sale of {fmt.shares(self.shares)} sh.",
                    role="muted")
        msg.setWordWrap(True)
        self.body.addWidget(msg)
        g = FieldGrid()
        self.grid = g
        self.price = NumberEdit(4, placeholder="0.00")
        self.price.set_value(stop)
        g.add("price", "Sell price", self.price, 0)
        self.date = QDateEdit()
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("yyyy-MM-dd")
        self.date.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        t = ctx.today()
        self.date.setDate(QDate(t.year, t.month, t.day))
        g.add("date", "Date", self.date, 1)
        self.body.addLayout(g)
        panel, pvl = inset_panel()
        for lv in lots:
            pvl.addWidget(label(f"Lot #{lv.lot.no} · {fmt.shares(lv.remaining)} sh @ {fmt.price(lv.lot.entry)}"
                                f" · stop {fmt.price(lv.lot.current_stop)}", font_token="num"))
        self.result_line = label("", font_token="num")
        pvl.addWidget(self.result_line)
        self.body.addWidget(panel)
        self.price.textChanged.connect(lambda _t: self._update())
        self._update()
        self.price.setFocus()

    def _update(self) -> None:
        p = self.price.value()
        if p is None:
            self.result_line.setText("Realized: —")
            return
        pl = sum((lv.remaining * (p - lv.lot.entry) for lv in self.lots), ZERO)
        risk = sum((lv.lot.shares * lv.lot.r for lv in self.lots), ZERO)
        self.result_line.setText(f"Realized: {fmt.signed_money(pl)} · {fmt.r_mult(pl / risk if risk else None)}")
        tone = "positive" if pl > 0 else "negative" if pl < 0 else None
        self.result_line.setProperty("tone", tone)
        self.result_line.style().polish(self.result_line)

    def submit(self) -> bool:
        self.grid.clear_errors()
        p = self.price.value()
        q = self.date.date()
        d = date(q.year(), q.month(), q.day())
        if p is None or p <= 0:
            self.grid.set_error("price", "Enter the price you sold at")
            return False
        if d > self.ctx.today():
            self.grid.set_error("date", "Date cannot be in the future")
            return False
        if not self.lots or self.shares <= 0:
            self.show_error("Nothing left to sell")
            return False
        trades = [Trade(None, self.portfolio.id, self.pv.symbol, Side.SELL, d, lv.remaining, p,  # type: ignore[arg-type]
                        lot_id=lv.lot.id, exit_reason="STOP") for lv in self.lots if lv.remaining > 0]
        try:
            self.result_trades = self.ctx.repos.trades.add_many(trades)
        except RepoError as e:
            self.show_error(str(e))
            return False
        return True
