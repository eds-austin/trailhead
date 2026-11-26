"""Centered empty state: glyph, title, body and an optional primary action."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .. import theme
from ..theme import tokens
from .common import button, label


class EmptyState(QWidget):
    action = Signal()

    def __init__(self, title: str, body: str, action_text: str | None = None, icon: str = "inbox",
                 parent: QWidget | None = None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.setSpacing(tokens.SPACE[2])
        glyph = QLabel()
        glyph.setPixmap(theme.pixmap(icon, theme.palette().text_faint, 40))
        glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title = label(title, role="title")
        self.title.setFont(theme.font("title"))
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.body = label(body, role="muted")
        self.body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.body.setWordWrap(True)
        self.body.setMinimumWidth(320)
        v.addWidget(glyph)
        v.addSpacing(tokens.SPACE[1])
        v.addWidget(self.title)
        v.addWidget(self.body)
        self.button = None
        if action_text:
            v.addSpacing(tokens.SPACE[2])
            row = QHBoxLayout()
            row.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.button = button(action_text, "primary", icon="plus")
            self.button.clicked.connect(self.action.emit)
            row.addWidget(self.button)
            v.addLayout(row)
