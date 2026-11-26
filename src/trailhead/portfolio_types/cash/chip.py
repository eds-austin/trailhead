"""'Cash · Ally $12,340' label on the page of a portfolio that settles in a cash account."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from ...ui import format as fmt
from ...ui import theme
from ...ui.widgets.common import label, set_prop
from .service import linked_account


class LinkedCashChip(QWidget):
    clicked = Signal(int)  # the Cash portfolio's id

    def __init__(self, parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        self.icon = QLabel()
        self.icon.setPixmap(theme.pixmap("wallet", theme.palette().text_muted, 14))
        self.icon.setFixedSize(QSize(14, 14))
        h.addWidget(self.icon)
        self.text = label("", role="muted")
        h.addWidget(self.text)
        self.value = label("", font_token="num")
        h.addWidget(self.value)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cash_pid: int | None = None
        self.hide()

    def refresh(self, repos, portfolio_id: int, today) -> None:
        v = linked_account(repos, portfolio_id, today)
        if v is None:
            self.hide()
            return
        self._cash_pid = v.account.portfolio_id
        bal = v.balance
        self.text.setText(f"Cash · {v.account.name}")
        self.value.setText(fmt.money(bal))
        set_prop(self.value, "tone", "negative" if bal < 0 else None)
        link = repos.cash_accounts.link_for(portfolio_id)
        self.setToolTip(f"Sells, buys and cash dividends settle in {v.account.name}"
                        + (f" since {fmt.short_date(link.since)}" if link else "")
                        + ".\nNot counted in Deployed / Max. Click to open the Cash portfolio."
                        + ("\nThe balance is below zero." if bal < 0 else ""))
        self.show()

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if self._cash_pid is not None and e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._cash_pid)
        super().mouseReleaseEvent(e)
