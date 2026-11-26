"""Status badges (OPEN, CLOSED, STOP HIT, ACTION…, EARNINGS, M, STALE). Painted + widget."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget

from .. import theme
from ..theme import tokens

PAD_X = 6
BADGE_H = 18

# kind -> (color token, solid?)
KINDS = {
    "open": ("accent", False),
    "closed": ("text_muted", False),
    "stop_hit": ("negative", True),
    "action": ("warning", False),
    "earnings": ("warning", False),
    "manual": ("info", False),
    "stale": ("warning", False),
    "positive": ("positive", False),
    "negative": ("negative", False),
    "info": ("info", False),
    "target": ("target", False),
    "stop": ("stop", False),
}


def badge_colors(kind: str) -> tuple[QColor, QColor]:
    p = theme.palette()
    token, solid = KINDS.get(kind, ("text_muted", False))
    color = getattr(p, token)
    if kind == "closed":
        return QColor(p.surface_3), QColor(p.text_muted)
    if solid:
        return QColor(color), QColor(p.text_on_accent)
    return QColor(tokens.soft(p, token)), QColor(color)


def badge_font():
    f = theme.font("caption", weight=600)
    f.setCapitalization(f.Capitalization.AllUppercase)
    return f


def badge_width(text: str) -> int:
    return QFontMetrics(badge_font()).horizontalAdvance(text.upper()) + PAD_X * 2 + 1


def paint_badge(painter: QPainter, rect: QRectF, text: str, kind: str, align: str = "left") -> float:
    """Paint a badge vertically centered in rect; returns its width."""
    bg, fg = badge_colors(kind)
    w = min(rect.width(), badge_width(text))
    x = {"left": rect.left(), "right": rect.right() - w, "center": rect.center().x() - w / 2}[align]
    r = QRectF(x, rect.center().y() - BADGE_H / 2, w, BADGE_H)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(bg)
    painter.drawRoundedRect(r, tokens.RADIUS["sm"], tokens.RADIUS["sm"])
    painter.setPen(fg)
    painter.setFont(badge_font())
    painter.drawText(r, Qt.AlignmentFlag.AlignCenter, text.upper())
    painter.restore()
    return w


class Badge(QWidget):
    def __init__(self, text: str, kind: str = "open", parent: QWidget | None = None):
        super().__init__(parent)
        self.text, self.kind = text, kind
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def set_badge(self, text: str, kind: str) -> None:
        self.text, self.kind = text, kind
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(badge_width(self.text), BADGE_H)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        paint_badge(p, QRectF(self.rect()), self.text, self.kind)
        p.end()
