"""PyQtGraph theme helpers (DESIGN_SYSTEM §6)."""

from __future__ import annotations

from datetime import date, datetime, time

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPen

from .. import theme

_configured = False


def setup_pg() -> None:
    global _configured
    if _configured:
        return
    p = theme.palette()
    pg.setConfigOptions(antialias=True, background=p.surface, foreground=p.text_muted)
    _configured = True


def pen(color: str, width: float = 1.0, style: Qt.PenStyle = Qt.PenStyle.SolidLine, alpha: float = 1.0) -> QPen:
    c = QColor(color)
    c.setAlphaF(alpha)
    pn = QPen(c)
    pn.setWidthF(width)
    pn.setStyle(style)
    pn.setCosmetic(True)
    return pn


def brush(color: str, alpha: float = 1.0):
    c = QColor(color)
    c.setAlphaF(alpha)
    return pg.mkBrush(c)


def style_plot(plot: pg.PlotItem, grid: bool = True, left: bool = True, right: bool = False) -> None:
    p = theme.palette()
    f = theme.font("caption")
    for name in ("left", "bottom", "right", "top"):
        ax = plot.getAxis(name)
        ax.setPen(pen(p.border))
        ax.setTextPen(pen(p.text_muted))
        ax.setStyle(tickFont=f, tickLength=-4, tickTextOffset=6)
    plot.showAxis("left", left)
    plot.showAxis("right", right)
    plot.hideAxis("top")
    if grid:
        plot.showGrid(x=False, y=True, alpha=0.4)
        for name in ("left", "right"):
            plot.getAxis(name).setGrid(int(255 * 0.4))
    plot.getViewBox().setBorder(None)
    plot.setMenuEnabled(False)
    plot.hideButtons()


def to_ts(d: date) -> float:
    return datetime.combine(d, time(12)).timestamp()


class IndexDateAxis(pg.AxisItem):
    """Bottom axis for index-based x (no weekend gaps) labelled with dates."""

    def __init__(self, *a, **kw):
        super().__init__("bottom", *a, **kw)
        self.dates: list[date] = []

    def tickStrings(self, values, scale, spacing):  # noqa: N802
        out = []
        for v in values:
            i = round(v)
            if 0 <= i < len(self.dates) and abs(v - i) < 1e-6:
                d = self.dates[i]
                out.append(d.strftime("%b %d") if spacing < 60 else d.strftime("%b %Y"))
            else:
                out.append("")
        return out

    def tickValues(self, minVal, maxVal, size):  # noqa: N802, N803
        if not self.dates:
            return []
        span = max(1.0, maxVal - minVal)
        step = max(1, round(span / max(2, size / 90)))
        start = max(0, int(minVal))
        major = list(range(start - start % step, int(maxVal) + 1, step))
        return [(step, [float(x) for x in major if 0 <= x < len(self.dates)])]
