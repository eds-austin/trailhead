"""Edit sector — set a stock's sector by hand (kept across refreshes) or clear it to use the provider's."""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QWidget

from ...context import AppContext
from ...market.provider import GICS_SECTORS
from ..widgets.common import button, label
from .base import AppDialog, FieldGrid

SECTORS = sorted(set(GICS_SECTORS.values()))


class SectorDialog(AppDialog):
    def __init__(self, ctx: AppContext, symbol: str, parent: QWidget | None = None):
        super().__init__(f"Sector — {symbol}", parent, width=440, primary_text="Save")
        self.ctx, self.symbol = ctx, symbol
        inst = ctx.repos.market.get_instruments([symbol]).get(symbol)
        g = FieldGrid()
        self.grid = g
        self.sector = QComboBox()
        self.sector.setEditable(True)  # pick an S&P sector or type your own
        self.sector.addItems(SECTORS)
        self.sector.setCurrentText(inst.sector if inst and inst.sector else "")
        self.sector.lineEdit().setPlaceholderText("Choose or type a sector")
        g.add("sector", "Sector", self.sector, span=2)
        self.body.addLayout(g)
        if inst and inst.sector and not inst.sector_manual:
            note = "Currently from the data provider. Saving makes it a manual value that refreshes keep."
        elif inst and inst.sector_manual:
            note = "Set manually. Clear it to let the data provider fill it in again."
        else:
            note = "The data provider has no sector for this stock. A manual value is kept across refreshes."
        hint = label(note, role="muted")
        hint.setWordWrap(True)
        self.body.addWidget(hint)
        clear = button("Clear (use provider)", "ghost")
        clear.setAutoDefault(False)
        clear.clicked.connect(self._clear)
        clear.setVisible(bool(inst and inst.sector_manual))
        self.footer_left.addWidget(clear)

    def _clear(self) -> None:
        self.ctx.repos.market.set_sector(self.symbol, None)
        self.accept()

    def submit(self) -> bool:
        value = self.sector.currentText().strip()
        if not value:
            self.grid.set_error("sector", "Choose or type a sector, or Cancel")
            return False
        self.ctx.repos.market.set_sector(self.symbol, value)
        return True
