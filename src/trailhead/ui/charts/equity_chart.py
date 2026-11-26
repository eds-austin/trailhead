"""Cumulative P/L curve: accent line with a 10% fill to the zero line, zero baseline in text_faint."""

from __future__ import annotations

import pyqtgraph as pg
from PySide6.QtCore import Qt

from ...core.equity import PLPoint
from .. import format as fmt
from .. import theme
from .base import brush, pen, setup_pg, style_plot, to_ts


class _MoneyAxis(pg.AxisItem):
    def tickStrings(self, values, scale, spacing):  # noqa: N802
        return [fmt.compact_money(v) for v in values]


class PLChart(pg.PlotWidget):
    def __init__(self, parent=None):
        setup_pg()
        super().__init__(parent, axisItems={"bottom": pg.DateAxisItem(), "left": _MoneyAxis("left")})
        p = theme.palette()
        self.setBackground(p.surface)
        style_plot(self.getPlotItem())
        self.getPlotItem().getAxis("left").setWidth(56)
        self.zero = pg.InfiniteLine(pos=0, angle=0, movable=False, pen=pen(p.text_faint, 1, Qt.PenStyle.DashLine))
        self.addItem(self.zero)
        self.curve = pg.PlotDataItem(pen=pen(p.accent, 2), fillLevel=0, brush=brush(p.accent, 0.10))
        self.addItem(self.curve)
        self.setMouseEnabled(x=True, y=False)
        self.getPlotItem().getViewBox().setAutoVisible(y=True)

    def set_points(self, pts: list[PLPoint]) -> None:
        if not pts:
            self.curve.setData([], [])
            return
        self.curve.setData([to_ts(p.date) for p in pts], [float(p.total) for p in pts])
        self.getPlotItem().enableAutoRange()


EquityChart = PLChart  # backwards-compatible name


class CompactPLChart(PLChart):
    """Short cumulative P/L chart for the Dashboard summary band: the combined curve (accent, filled) plus one
    dashed muted line per portfolio when there is more than one. Not pannable."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseEnabled(x=False, y=False)
        self.getPlotItem().getAxis("left").setWidth(48)
        self.others: list[pg.PlotDataItem] = []

    def set_series(self, main: list[tuple], others: list[list[tuple]] = ()) -> None:
        p = theme.palette()
        for item in self.others:
            self.removeItem(item)
        self.others = []
        for series in others:
            item = pg.PlotDataItem([to_ts(d) for d, _ in series], [float(v) for _, v in series],
                                   pen=pen(p.text_muted, 1.5, Qt.PenStyle.DashLine))
            self.addItem(item)
            self.others.append(item)
        self.removeItem(self.curve)  # keep the combined line on top
        self.addItem(self.curve)
        self.curve.setData([to_ts(d) for d, _ in main], [float(v) for _, v in main])
        self.getPlotItem().enableAutoRange()
