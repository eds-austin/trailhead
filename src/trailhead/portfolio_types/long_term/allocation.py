"""Allocation by investment type: a compact labelled bar list (painted)."""

from __future__ import annotations

from decimal import Decimal

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget

from ...core.models import ASSET_TYPES
from ...ui import format as fmt
from ...ui import theme
from ...ui.theme import tokens

# Categorical hues (never the gain/loss colours): Equity blue, ETF cyan, Income violet, metals gold, other grey.
TYPE_COLORS = dict(zip(ASSET_TYPES, (tokens.CATEGORICAL[0], tokens.CATEGORICAL[1], tokens.CATEGORICAL[2],
                                     tokens.CATEGORICAL[3], tokens.CATEGORICAL[7]), strict=True))
ROW_H = 22
LABEL_W = 108
PCT_W = 44


def type_color(name: str) -> str:
    return TYPE_COLORS.get(name, tokens.CATEGORICAL[7])


class AllocationBars(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list[tuple[str, Decimal]] = []
        self._empty = "No holdings yet"
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_items(self, items: list[tuple[str, Decimal]], empty_text: str = "No holdings yet") -> None:
        self._items = items
        self._empty = empty_text
        total = sum((v for _, v in items), Decimal(0))
        self.setToolTip("\n".join(f"{n}: {fmt.money(v)} ({fmt.pct(v / total)})" for n, v in items) if total else "")
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = theme.palette()
        qp = QPainter(self)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing)
        total = sum((v for _, v in self._items), Decimal(0))
        if not self._items or not total:
            qp.setPen(QColor(p.text_faint))
            qp.setFont(theme.font("body"))
            qp.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._empty)
            return
        body, num = theme.font("body"), theme.font("num")
        w = self.width()
        bar_x = LABEL_W
        bar_w = max(20, w - LABEL_W - PCT_W - tokens.SPACE[2])
        for i, (name, v) in enumerate(self._items):
            y = i * (ROW_H + tokens.SPACE[1])
            if y + ROW_H > self.height():
                break
            frac = float(v / total)
            qp.setFont(body)
            qp.setPen(QColor(p.text_muted))
            fm = QFontMetrics(body)
            qp.drawText(QRectF(0, y, LABEL_W - tokens.SPACE[2], ROW_H), int(Qt.AlignmentFlag.AlignVCenter),
                        fm.elidedText(name, Qt.TextElideMode.ElideRight, LABEL_W - tokens.SPACE[2]))
            track = QRectF(bar_x, y + ROW_H / 2 - 4, bar_w, 8)
            qp.setPen(Qt.PenStyle.NoPen)
            qp.setBrush(QColor(p.surface_3))
            qp.drawRoundedRect(track, 4, 4)
            qp.setBrush(QColor(type_color(name)))
            qp.drawRoundedRect(QRectF(track.left(), track.top(), max(4.0, track.width() * frac), 8), 4, 4)
            qp.setFont(num)
            qp.setPen(QColor(p.text))
            qp.drawText(QRectF(w - PCT_W, y, PCT_W, ROW_H),
                        int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), fmt.pct(v / total, 0))
