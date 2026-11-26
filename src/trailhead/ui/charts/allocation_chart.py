"""Allocation charts: horizontal bars by position (single accent hue) and a sector donut (categorical)."""

from __future__ import annotations

from decimal import Decimal

import pyqtgraph as pg
from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .. import format as fmt
from .. import theme
from ..theme import tokens
from .base import brush, pen, setup_pg, style_plot


class _SymbolAxis(pg.AxisItem):
    def __init__(self):
        super().__init__("left")
        self.labels: list[str] = []

    def tickStrings(self, values, scale, spacing):  # noqa: N802
        return [self.labels[int(v)] if 0 <= int(v) < len(self.labels) and v == int(v) else "" for v in values]

    def tickValues(self, minVal, maxVal, size):  # noqa: N802, N803
        return [(1, [float(i) for i in range(len(self.labels))])]


class _MoneyAxis(pg.AxisItem):
    def tickStrings(self, values, scale, spacing):  # noqa: N802
        return [fmt.compact_money(v) for v in values]


class PositionBars(pg.PlotWidget):
    def __init__(self, parent=None):
        setup_pg()
        self.axis = _SymbolAxis()
        super().__init__(parent, axisItems={"left": self.axis, "bottom": _MoneyAxis("bottom")})
        self.setBackground(theme.palette().surface)
        style_plot(self.getPlotItem(), grid=False)
        self.setMouseEnabled(x=False, y=False)
        self.bars = pg.BarGraphItem(x0=[], y=[], width=[], height=0.62)
        self.addItem(self.bars)
        self.getPlotItem().getAxis("left").setWidth(56)
        self.getPlotItem().getViewBox().invertY(True)

    def set_items(self, items: list[tuple[str, Decimal]]) -> None:
        p = theme.palette()
        items = sorted(items, key=lambda x: x[1], reverse=True)
        self.axis.labels = [s for s, _ in items]
        n = len(items)
        self.bars.setOpts(x0=[0] * n, y=list(range(n)), width=[float(v) for _, v in items], height=0.62,
                          brush=brush(p.accent, 0.85), pen=pen(p.accent, 0, alpha=0))
        if items:
            self.setYRange(-0.6, n - 0.4, padding=0)
            self.setXRange(0, float(items[0][1]) * 1.08, padding=0)
        self.axis.picture = None
        self.axis.update()


def _ring_pen(color: str, width: float) -> QPen:
    pn = QPen(QColor(color))
    pn.setWidthF(width)
    pn.setCapStyle(Qt.PenCapStyle.FlatCap)
    return pn


class SectorDonut(QWidget):
    """Donut painted with QPainter; legend on the right. Uses the categorical palette only."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items: list[tuple[str, Decimal]] = []
        self.setMinimumHeight(180)

    def set_items(self, items: list[tuple[str, Decimal]]) -> None:
        self.items = sorted([i for i in items if i[1] > 0], key=lambda x: x[1], reverse=True)
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(360, 220)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = theme.palette()
        qp = QPainter(self)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing)
        total = sum((v for _, v in self.items), Decimal(0))
        r = self.rect().adjusted(tokens.SPACE[3], tokens.SPACE[3], -tokens.SPACE[3], -tokens.SPACE[3])
        d = min(r.height(), r.width() * 0.45)
        circle = QRectF(r.left(), r.center().y() - d / 2, d, d)
        if total <= 0:
            qp.setPen(_ring_pen(p.surface_3, d * 0.18))
            inset = d * 0.09
            qp.drawEllipse(circle.adjusted(inset, inset, -inset, -inset))
            qp.setPen(QColor(p.text_faint))
            qp.setFont(theme.font("body"))
            qp.drawText(circle, Qt.AlignmentFlag.AlignCenter, "No open\npositions")
            qp.end()
            return
        thick = d * 0.18
        inset = thick / 2
        ring = circle.adjusted(inset, inset, -inset, -inset)
        start = 90 * 16
        cols = tokens.CATEGORICAL
        for i, (_name, v) in enumerate(self.items):
            span = -round(float(v / total) * 360 * 16)
            qp.setPen(_ring_pen(cols[i % len(cols)], thick))
            qp.drawArc(ring, start, span)
            start += span
        qp.setPen(QColor(p.text))
        qp.setFont(theme.font("body_strong"))
        qp.drawText(circle, Qt.AlignmentFlag.AlignCenter, f"{len(self.items)}\nsectors")
        lx = circle.right() + tokens.SPACE[5]
        fm = QFontMetrics(theme.font("body"))
        line_h = max(20, fm.height() + 4)
        y = r.center().y() - min(len(self.items), 11) * line_h / 2
        for i, (name, v) in enumerate(self.items[:11]):
            qp.setPen(Qt.PenStyle.NoPen)
            qp.setBrush(QColor(cols[i % len(cols)]))
            qp.drawRoundedRect(QRectF(lx, y + line_h / 2 - 5, 10, 10), 2, 2)
            qp.setPen(QColor(p.text))
            qp.setFont(theme.font("body"))
            pct = fmt.pct(v / total)
            w = r.right() - lx - 18
            qp.drawText(QRectF(lx + 18, y, w - 60, line_h), Qt.AlignmentFlag.AlignVCenter,
                        fm.elidedText(name, Qt.TextElideMode.ElideRight, int(w - 64)))
            qp.setPen(QColor(p.text_muted))
            qp.setFont(theme.font("num"))
            qp.drawText(QRectF(lx + 18, y, w, line_h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                        pct)
            y += line_h
        qp.end()
