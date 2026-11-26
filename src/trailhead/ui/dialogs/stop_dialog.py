"""Set stop… — record a manual stop change for an open lot."""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QDate
from PySide6.QtWidgets import QAbstractSpinBox, QComboBox, QDateEdit, QWidget

from ...context import AppContext
from ...core.models import StopChange, StopReason
from ...core.snapshot import PositionView
from .. import format as fmt
from .. import theme
from ..widgets.common import label
from ..widgets.number_edit import NumberEdit
from .base import AppDialog, FieldGrid


class StopDialog(AppDialog):
    def __init__(self, ctx: AppContext, pv: PositionView, lot_id: int | None = None, parent: QWidget | None = None):
        super().__init__(f"Set stop — {pv.symbol}", parent, width=480, primary_text="Set stop")
        self.ctx = ctx
        self.pv = pv
        g = FieldGrid()
        self.grid = g
        self.lot = QComboBox()
        self.lot.setFont(theme.font("num"))
        for lv in pv.open_views:
            self.lot.addItem(f"#{lv.lot.no} · {fmt.shares(lv.remaining)} sh @ {fmt.price(lv.lot.entry)} · stop "
                             f"{fmt.price(lv.lot.current_stop)} · ladder {fmt.price(lv.state.ladder_stop)}", lv.lot.id)
        if lot_id is not None and self.lot.findData(lot_id) >= 0:
            self.lot.setCurrentIndex(self.lot.findData(lot_id))
        g.add("lot", "Lot", self.lot, span=2)
        g.next_row()
        self.stop = NumberEdit(4)
        self.date = QDateEdit()
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("yyyy-MM-dd")
        self.date.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        t = ctx.today()
        self.date.setDate(QDate(t.year, t.month, t.day))
        g.add("stop", "New stop", self.stop, 0)
        g.add("date", "Effective", self.date, 1)
        self.body.addLayout(g)
        self.hint = label("", role="muted")
        self.body.addWidget(self.hint)
        self.lot.currentIndexChanged.connect(self._on_lot)
        self._on_lot()

    def _lot_view(self):
        lid = self.lot.currentData()
        return next((lv for lv in self.pv.open_views if lv.lot.id == lid), None)

    def _on_lot(self) -> None:
        lv = self._lot_view()
        if lv is None:
            return
        self.stop.set_value(max(lv.state.ladder_stop, lv.lot.current_stop))
        self.hint.setText(f"Initial stop {fmt.price(lv.lot.initial_stop)} · current {fmt.price(lv.lot.current_stop)}"
                          f" · ladder {fmt.price(lv.state.ladder_stop)} ({lv.state.reached.name})")

    def submit(self) -> bool:
        self.grid.clear_errors()
        lv = self._lot_view()
        v = self.stop.value()
        if lv is None:
            self.grid.set_error("lot", "No open lot")
            return False
        if v is None or v <= 0:
            self.grid.set_error("stop", "Enter a stop price")
            return False
        q = self.date.date()
        reason = StopReason.LADDER_CONFIRMED if v == lv.state.ladder_stop else StopReason.MANUAL
        self.ctx.repos.stops.add(StopChange(None, lv.lot.id, date(q.year(), q.month(), q.day()), v, reason))
        return True
