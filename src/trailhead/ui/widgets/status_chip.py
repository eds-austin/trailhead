"""Header widgets: the price-status chip and the alerts bell with a count."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QToolButton, QWidget

from .. import theme
from ..theme import tokens


class _Dot(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(8, 8)
        self.color = theme.palette().text_faint

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self.color))
        p.drawEllipse(QRectF(0, 0, 8, 8))
        p.end()


class PriceStatusChip(QFrame):
    """● Prices 10:42 — green when fresh, amber when stale/failed, faint when never fetched."""

    STATES = ("none", "fresh", "stale", "loading", "error")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("role", "status-chip")
        self.setFixedHeight(22)
        h = QHBoxLayout(self)
        h.setContentsMargins(tokens.SPACE[2], 0, tokens.SPACE[2] + 2, 0)
        h.setSpacing(tokens.SPACE[1] + 2)
        self._dot = _Dot()
        self._text = QLabel("Prices —")
        self._text.setProperty("role", "caption")
        self._text.setFont(theme.font("caption"))
        h.addWidget(self._dot)
        h.addWidget(self._text)
        self.state = "none"

    def set_status(self, state: str, text: str, tooltip: str = "") -> None:
        p = theme.palette()
        self.state = state
        self._dot.color = {"fresh": p.positive, "stale": p.warning, "error": p.warning,
                           "loading": p.accent}.get(state, p.text_faint)
        self._dot.update()
        self._text.setText(text)
        self.setToolTip(tooltip)


class BellButton(QToolButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("variant", "ghost")
        p = theme.palette()
        self.setIcon(theme.icon("bell", p.text_muted, 18, active_color=p.text))
        self.setIconSize(QSize(18, 18))
        self.setToolTip("Alerts")
        self._count = 0
        self.setFixedWidth(40)

    def set_count(self, n: int) -> None:
        self._count = n
        self.setToolTip(f"Alerts ({n} unacknowledged)" if n else "Alerts")
        self.update()

    def count(self) -> int:
        return self._count

    def paintEvent(self, e) -> None:  # noqa: N802
        super().paintEvent(e)
        if self._count <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = theme.palette()
        text = str(self._count) if self._count < 100 else "99+"
        f = theme.font("caption", weight=600)
        f.setPixelSize(9)
        p.setFont(f)
        w = max(14, p.fontMetrics().horizontalAdvance(text) + 6)
        r = QRectF(self.width() / 2 + 1, 4, w, 14)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(pal.negative))
        p.drawRoundedRect(r, 7, 7)
        p.setPen(QColor(pal.text_on_accent))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)
        p.end()
