"""R-multiple histogram of closed lots: 0.5R bins, colored by sign, expectancy line."""

from __future__ import annotations

from decimal import Decimal

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

from ...core.stats import r_histogram
from .. import format as fmt
from .. import theme
from .base import brush, pen, setup_pg, style_plot

BIN = Decimal("0.5")


class RHistogram(pg.PlotWidget):
    def __init__(self, parent=None):
        setup_pg()
        super().__init__(parent)
        self.setBackground(theme.palette().surface)
        style_plot(self.getPlotItem())
        self.setMouseEnabled(x=False, y=False)
        self.bars = pg.BarGraphItem(x=[], height=[], width=0.45)
        self.addItem(self.bars)
        self.exp_line: pg.InfiniteLine | None = None
        self.empty_text = pg.TextItem("No closed lots in this period", color=theme.palette().text_faint,
                                      anchor=(0.5, 0.5))
        self.empty_text.setFont(theme.font("body"))
        self.addItem(self.empty_text)
        self.getPlotItem().getAxis("left").setWidth(32)

    def set_values(self, rs: list[Decimal], expectancy: Decimal | None, empty_text: str | None = None) -> None:
        p = theme.palette()
        if empty_text is not None:
            self.empty_text.setText(empty_text)
        if self.exp_line is not None:
            self.removeItem(self.exp_line)
            self.exp_line = None
        h = r_histogram(rs, BIN)
        xs = [float(edge + BIN / 2) for edge in h]
        heights = [float(n) for n in h.values()]
        colors = [p.positive if edge >= 0 else p.negative for edge in h]
        if xs:
            self.bars.setOpts(x=xs, height=heights, width=0.45, brushes=[brush(c, 0.85) for c in colors],
                              pens=[pen(c, 1) for c in colors])
        else:
            self.bars.setOpts(x=[], height=[])
        if expectancy is not None:
            self.exp_line = pg.InfiniteLine(
                pos=float(expectancy), angle=90, movable=False, pen=pen(p.accent, 1.5, Qt.PenStyle.DashLine),
                label=f"E = {fmt.r_mult(expectancy, signed=False)}",
                labelOpts={"position": 0.92, "color": QColor(p.accent), "fill": brush(p.surface, 0.9)})
            self.addItem(self.exp_line)
        self.empty_text.setVisible(not xs)
        self.getPlotItem().getAxis("left").setStyle(showValues=bool(xs))
        self.getPlotItem().getAxis("bottom").setStyle(showValues=bool(xs))
        if not xs:
            self.setXRange(-1, 1, padding=0)
            self.setYRange(0, 1, padding=0)
            self.empty_text.setPos(0, 0.5)
        if xs:
            lo = min(min(xs), float(expectancy or 0)) - 0.75
            hi = max(max(xs), float(expectancy or 0)) + 0.75
            self.setXRange(lo, hi, padding=0)
            self.setYRange(0, max(heights) + 1, padding=0)
            step = max(1, round(max(heights) / 8))
            self.getPlotItem().getAxis("left").setTickSpacing(step, step)  # whole-number counts
