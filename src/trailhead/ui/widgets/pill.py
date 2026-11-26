"""Level pills: amber stop / cyan target. Painted (shared by delegates and widgets)."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget

from .. import theme
from ..theme import tokens

GLYPH = {"stop": "■", "target": "◆"}
PAD_X = 8
GAP = 6


def pill_colors(kind: str, state: str = "normal") -> tuple[QColor, QColor, QColor]:
    """(fill, text, sub-text) for a pill kind ('stop'|'target') and state ('normal'|'near'|'hit')."""
    p = theme.palette()
    base = getattr(p, kind)
    if state == "hit":
        return QColor(p.negative), QColor(p.text_on_accent), QColor(p.text_on_accent)
    if state == "near" and kind == "stop":
        return QColor(base), QColor(p.text_on_accent), QColor(p.text_on_accent)
    return QColor(tokens.soft(p, kind)), QColor(base), QColor(p.text_muted)


def pill_width(price_text: str, sub_text: str = "") -> int:
    fm_num = QFontMetrics(theme.font("num"))
    fm_cap = QFontMetrics(theme.font("caption"))
    w = PAD_X * 2 + fm_num.horizontalAdvance("■ " + price_text)
    if sub_text:
        w += GAP + fm_cap.horizontalAdvance(sub_text)
    return w


def paint_pill(painter: QPainter, rect: QRectF, kind: str, price_text: str, sub_text: str = "",
               state: str = "normal", align_right: bool = True) -> None:
    fill, fg, sub_fg = pill_colors(kind, state)
    w = min(rect.width(), pill_width(price_text, sub_text))
    h = tokens.PILL_H
    x = rect.right() - w if align_right else rect.left()
    y = rect.center().y() - h / 2
    r = QRectF(x, y, w, h)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(fill)
    painter.drawRoundedRect(r, tokens.RADIUS["md"], tokens.RADIUS["md"])
    painter.setPen(fg)
    nf = theme.font("num")
    painter.setFont(nf)
    main = f"{GLYPH[kind]} {price_text}"
    tx = r.left() + PAD_X
    fm = QFontMetrics(nf)
    painter.drawText(QRectF(tx, r.top(), fm.horizontalAdvance(main) + 1, h),
                     Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, main)
    if sub_text:
        cf = theme.font("caption")
        painter.setFont(cf)
        painter.setPen(sub_fg)
        sx = tx + fm.horizontalAdvance(main) + GAP
        painter.drawText(QRectF(sx, r.top(), r.right() - sx, h),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, sub_text)
    painter.restore()


class LevelPill(QWidget):
    """Widget wrapper around paint_pill (ladder cards, gallery, dialogs)."""

    def __init__(self, kind: str, price_text: str = "—", sub_text: str = "", state: str = "normal",
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.kind, self.price_text, self.sub_text, self.state = kind, price_text, sub_text, state
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def set_values(self, price_text: str, sub_text: str = "", state: str = "normal") -> None:
        self.price_text, self.sub_text, self.state = price_text, sub_text, state
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(pill_width(self.price_text, self.sub_text), tokens.PILL_H)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        paint_pill(p, QRectF(self.rect()), self.kind, self.price_text, self.sub_text, self.state,
                   align_right=False)
        p.end()
