"""Bottom-right toasts with a severity bar. Errors stay until dismissed."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPropertyAnimation, Qt, QTimer
from PySide6.QtWidgets import QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .. import theme
from ..theme import tokens
from .common import icon_button

SEVERITY_TOKEN = {"info": "info", "success": "positive", "warning": "warning", "error": "negative",
                  "critical": "negative"}


class Toast(QFrame):
    def __init__(self, host: ToastHost, title: str, body: str = "", severity: str = "info"):
        super().__init__(host.parentWidget())
        self.host = host
        self.severity = severity
        self.setProperty("role", "toast")
        self.setFixedWidth(tokens.TOAST_W)
        p = theme.palette()
        color = getattr(p, SEVERITY_TOKEN.get(severity, "info"))
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, tokens.SPACE[1], 0)
        h.setSpacing(tokens.SPACE[3])
        bar = QFrame()
        bar.setFixedWidth(3)
        bar.setStyleSheet(f"background: {color}; border-top-left-radius: {tokens.RADIUS['md']}px;"
                          f"border-bottom-left-radius: {tokens.RADIUS['md']}px;")
        h.addWidget(bar)
        v = QVBoxLayout()
        v.setContentsMargins(0, tokens.SPACE[2] + 2, 0, tokens.SPACE[2] + 2)
        v.setSpacing(2)
        t = QLabel(title)
        t.setFont(theme.font("body_strong"))
        t.setWordWrap(True)
        v.addWidget(t)
        if body:
            b = QLabel(body)
            b.setProperty("role", "muted")
            b.setWordWrap(True)
            v.addWidget(b)
        h.addLayout(v, 1)
        close = icon_button("x", "Dismiss", size=14)
        close.clicked.connect(self.dismiss)
        h.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)
        self._fx = QGraphicsOpacityEffect(self)
        self._fx.setOpacity(0.0)
        self.setGraphicsEffect(self._fx)
        self._anim = QPropertyAnimation(self._fx, b"opacity", self)
        self._anim.setDuration(tokens.TOAST_FADE_MS)
        if severity not in ("error", "critical"):
            QTimer.singleShot(tokens.TOAST_MS, self, self.dismiss)

    def appear(self) -> None:
        self.adjustSize()
        self.show()
        self.raise_()
        self._anim.stop()
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()

    def dismiss(self) -> None:
        if not self.isVisible():
            return
        self._anim.stop()
        self._anim.setStartValue(self._fx.opacity())
        self._anim.setEndValue(0.0)
        self._anim.finished.connect(self._remove)
        self._anim.start()

    def _remove(self) -> None:
        self.hide()
        self.host.remove(self)
        self.deleteLater()


class ToastHost(QObject):
    """Stacks toasts in the bottom-right of a parent widget and follows its resizes."""

    MAX = 4

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self._parent = parent
        self._toasts: list[Toast] = []
        parent.installEventFilter(self)

    def parentWidget(self) -> QWidget:  # noqa: N802
        return self._parent

    def show(self, title: str, body: str = "", severity: str = "info") -> Toast:
        t = Toast(self, title, body, severity)
        self._toasts.append(t)
        while len(self._toasts) > self.MAX:
            self._toasts[0].dismiss()
            self._toasts.pop(0)
        t.appear()
        self._reposition()
        return t

    def remove(self, t: Toast) -> None:
        if t in self._toasts:
            self._toasts.remove(t)
        self._reposition()

    def toasts(self) -> list[Toast]:
        return list(self._toasts)

    def _reposition(self) -> None:
        margin = tokens.WINDOW_PAD
        y = self._parent.height() - margin
        for t in reversed(self._toasts):
            t.adjustSize()
            y -= t.height()
            t.move(self._parent.width() - t.width() - margin, y)
            y -= tokens.SPACE[2]

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802
        if obj is self._parent and ev.type() == QEvent.Type.Resize:
            self._reposition()
        return False
