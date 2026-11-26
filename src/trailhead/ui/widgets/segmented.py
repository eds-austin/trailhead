"""Segmented control (e.g. BUY | SELL, Open | Closed | All)."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QPushButton


class Segmented(QFrame):
    changed = Signal(str)

    def __init__(self, options: list[tuple[str, str]] | list[tuple[str, str, str]], parent=None):
        """options: (key, label[, tone]) where tone is 'positive'/'negative' for the selected text."""
        super().__init__(parent)
        self.setProperty("role", "segmented")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        for opt in options:
            key, text = opt[0], opt[1]
            b = QPushButton(text)
            b.setCheckable(True)
            if len(opt) > 2:
                b.setProperty("tone", opt[2])
            lay.addWidget(b)
            self._group.addButton(b)
            self._buttons[key] = b
            b.clicked.connect(lambda _=False, k=key: self.changed.emit(k))
        if self._buttons:
            next(iter(self._buttons.values())).setChecked(True)

    def value(self) -> str:
        for k, b in self._buttons.items():
            if b.isChecked():
                return k
        return ""

    def set_value(self, key: str, emit: bool = True) -> None:
        b = self._buttons.get(key)
        if b is None or b.isChecked():
            return
        b.setChecked(True)
        if emit:
            self.changed.emit(key)

    def button(self, key: str) -> QPushButton:
        return self._buttons[key]
