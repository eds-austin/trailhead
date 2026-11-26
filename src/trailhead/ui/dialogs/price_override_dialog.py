"""Manual price override: flagged with an "M" badge until a fresh fetch clears it, unless pinned."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QDateTime
from PySide6.QtWidgets import QCheckBox, QDateTimeEdit, QLineEdit, QWidget

from ...context import AppContext
from ...core.models import Quote
from .. import format as fmt
from ..widgets.common import button, label
from ..widgets.number_edit import NumberEdit
from .base import AppDialog, FieldGrid


class PriceOverrideDialog(AppDialog):
    def __init__(self, ctx: AppContext, symbol: str | None = None, parent: QWidget | None = None):
        super().__init__("Manual price", parent, width=440, primary_text="Set price")
        self.ctx = ctx
        g = FieldGrid()
        self.grid = g
        self.symbol = QLineEdit(symbol or "")
        self.symbol.setPlaceholderText("Ticker")
        self.symbol.textEdited.connect(lambda t: self.symbol.setText(t.upper()))
        self.symbol.editingFinished.connect(self._load)
        g.add("symbol", "Symbol", self.symbol, span=2)
        g.next_row()
        self.price = NumberEdit(4)
        self.as_of = QDateTimeEdit(QDateTime.currentDateTime())
        self.as_of.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.as_of.setCalendarPopup(True)
        g.add("price", "Price", self.price, 0)
        g.add("as_of", "As of", self.as_of, 1)
        self.body.addLayout(g)
        self.pin = QCheckBox("Pin (keep this price even when a fresh quote arrives)")
        self.body.addWidget(self.pin)
        self.current = label("", role="muted")
        self.body.addWidget(self.current)
        self.clear_btn = button("Clear override", "ghost")
        self.clear_btn.clicked.connect(self._clear)
        self.footer_left.addWidget(self.clear_btn)
        self._load()
        (self.price if symbol else self.symbol).setFocus()

    def _load(self) -> None:
        s = self.symbol.text().strip().upper()
        q = self.ctx.repos.market.get_quote(s) if s else None
        self.clear_btn.setVisible(bool(q and q.is_manual))
        if q is None:
            self.current.setText("No quote stored yet" if s else "")
            return
        self.current.setText(f"Current {fmt.price(q.last)} · {q.source}{' (manual)' if q.is_manual else ''}"
                             f" · {q.as_of:%Y-%m-%d %H:%M}")
        if self.price.value() is None:
            self.price.set_value(q.last)
        self.pin.setChecked(q.pinned)

    def _clear(self) -> None:
        s = self.symbol.text().strip().upper()
        self.ctx.repos.market.clear_manual(s)
        self.accept()

    def submit(self) -> bool:
        self.grid.clear_errors()
        s = self.symbol.text().strip().upper()
        v = self.price.value()
        if not s:
            self.grid.set_error("symbol", "Enter a symbol")
            return False
        if v is None or v <= 0:
            self.grid.set_error("price", "Enter a price")
            return False
        old = self.ctx.repos.market.get_quote(s)
        as_of: datetime = self.as_of.dateTime().toPython()
        prev = old.prev_close if old else None
        self.ctx.repos.market.upsert_quote(Quote(s, v, prev, as_of.replace(microsecond=0), "manual", True,
                                                 self.pin.isChecked()), respect_pinned=False)
        return True
