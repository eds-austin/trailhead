"""Search box with a leading magnifier, vertically centred.

QLineEdit.addAction() places its icon from the widget's natural height and ignores the stylesheet's min-height,
so the icon sat low in our 32 px fields. Here the icon is a child label centred in resizeEvent.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QLineEdit

from .. import theme
from ..theme import tokens

ICON = 16


class SearchField(QLineEdit):
    def __init__(self, placeholder: str = "", parent=None):
        super().__init__(parent)
        self.setProperty("role", "search")
        self.setPlaceholderText(placeholder)
        self.setClearButtonEnabled(True)
        self._icon = QLabel(self)
        self._icon.setPixmap(theme.pixmap("search", theme.palette().text_faint, ICON))
        self._icon.setFixedSize(ICON, ICON)
        self._icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._icon.setStyleSheet("background: transparent;")
        self.setTextMargins(ICON + tokens.SPACE[2], 0, 0, 0)  # padding-left (QSS) + icon + gap

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._icon.move(tokens.SPACE[2] + 1, (self.height() - ICON) // 2)
