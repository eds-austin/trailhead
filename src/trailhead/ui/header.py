"""Top header bar: portfolio switcher, price status, refresh, bell, Cash, New Trade."""

from __future__ import annotations

from PySide6.QtCore import QSize, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QMenu, QPushButton

from . import theme
from .theme import tokens
from .widgets.common import button, icon_button
from .widgets.status_chip import BellButton, PriceStatusChip


class Header(QFrame):
    refresh_clicked = Signal()
    bell_clicked = Signal()
    cash_clicked = Signal()
    new_trade_clicked = Signal()
    switch_requested = Signal(str)  # page key

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("header")
        h = QHBoxLayout(self)
        h.setContentsMargins(tokens.WINDOW_PAD, 0, tokens.WINDOW_PAD, 0)
        h.setSpacing(tokens.SPACE[2])
        self.title = QPushButton("Dashboard")
        self.title.setProperty("variant", "ghost")
        self.title.setFont(theme.font("title"))
        self.title.setStyleSheet(f"color: {theme.palette().text}; text-align: left;")
        self.title.setIcon(theme.icon("chevron-down", theme.palette().text_muted, 16))
        self.title.setIconSize(QSize(16, 16))
        self.title.setLayoutDirection(self.title.layoutDirection().RightToLeft)
        self._menu = QMenu(self)
        self.title.setMenu(self._menu)
        h.addWidget(self.title)
        h.addSpacing(tokens.SPACE[3])
        self.chip = PriceStatusChip()
        h.addWidget(self.chip)
        self.refresh_btn = icon_button("refresh-cw", "Refresh prices (F5)")
        self.refresh_btn.clicked.connect(self.refresh_clicked.emit)
        h.addWidget(self.refresh_btn)
        h.addStretch(1)
        self.bell = BellButton()
        self.bell.clicked.connect(self.bell_clicked.emit)
        h.addWidget(self.bell)
        self.cash_btn = button("Cash", icon="wallet", tooltip="Deposit / withdraw (Ctrl+D)")
        self.cash_btn.clicked.connect(self.cash_clicked.emit)
        h.addWidget(self.cash_btn)
        self.new_trade_btn = button("New Trade", "primary", icon="plus", tooltip="New trade (Ctrl+N)")
        self.new_trade_btn.clicked.connect(self.new_trade_clicked.emit)
        h.addWidget(self.new_trade_btn)

    def set_title(self, text: str) -> None:
        self.title.setText(text + "  ")

    def set_switch_targets(self, items: list[tuple[str, str]]) -> None:
        self._menu.clear()
        for key, text in items:
            self._menu.addAction(text, lambda k=key: self.switch_requested.emit(k))

    def set_type_labels(self, entry_label: str, cash_label: str) -> None:
        """The primary and Cash buttons are named for the current portfolio type (New Trade / New Account …)."""
        self.new_trade_btn.setText(entry_label)
        self.new_trade_btn.setToolTip(f"{entry_label} (Ctrl+N)")
        self.cash_btn.setText(cash_label)
        cash_icon = "arrow-left-right" if cash_label != "Cash" else "wallet"
        self.cash_btn.setIcon(theme.icon(cash_icon, theme.palette().text_muted, size=16))
        self.cash_btn.setToolTip(f"{'Deposit, withdraw or transfer' if cash_label != 'Cash' else 'Deposit / withdraw'}"
                                 " (Ctrl+D)")

    def set_portfolio_actions_enabled(self, enabled: bool) -> None:
        self.cash_btn.setEnabled(enabled)
        self.new_trade_btn.setEnabled(enabled)
