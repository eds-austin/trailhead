"""Set (or clear) a Long-term holding's optional stop and profit target. They apply to all its open lots."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from ...context import AppContext
from ...core.holdings import HoldingView
from ...data.repo import RepoError
from .. import format as fmt
from ..widgets.common import label
from ..widgets.number_edit import NumberEdit
from .base import AppDialog, FieldGrid


class HoldingLevelsDialog(AppDialog):
    def __init__(self, ctx: AppContext, holding: HoldingView, parent: QWidget | None = None):
        super().__init__(f"{holding.symbol} — stop & target", parent, width=440, primary_text="Save")
        self.ctx, self.holding = ctx, holding
        msg = label(f"Last {fmt.price(holding.last)} · avg cost {fmt.price(holding.avg_cost)}. "
                    "Leave a field empty for no stop or no target.", role="muted")
        msg.setWordWrap(True)
        self.body.addWidget(msg)
        g = FieldGrid()
        self.grid = g
        self.stop = NumberEdit(4, placeholder="None")
        self.stop.set_value(holding.stop)
        self.target = NumberEdit(4, placeholder="None")
        self.target.set_value(holding.target)
        g.add("stop", "Stop", self.stop, 0)
        g.add("target", "Target", self.target, 1)
        self.body.addLayout(g)
        self.stop.setFocus()

    def submit(self) -> bool:
        self.grid.clear_errors()
        stop, target = self.stop.value(), self.target.value()
        if stop is not None and stop <= 0:
            self.grid.set_error("stop", "Must be positive")
            return False
        if target is not None and target <= 0:
            self.grid.set_error("target", "Must be positive")
            return False
        if stop is not None and target is not None and stop >= target:
            self.grid.set_error("stop", "Must be below the target")
            return False
        try:
            self.ctx.repos.trades.set_levels([lot.id for lot in self.holding.open_lots], stop, target)
        except RepoError as e:
            self.show_error(str(e))
            return False
        return True
