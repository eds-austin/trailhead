"""Daily candle chart with volume, entry / stop / target / R-ladder lines, trade markers and crosshair."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

import pyqtgraph as pg
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPicture
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ...core.models import Bar
from .. import format as fmt
from .. import theme
from ..theme import tokens
from .base import IndexDateAxis, brush, pen, setup_pg, style_plot


class CandlestickItem(pg.GraphicsObject):
    """Candles at integer x positions: rows of (x, open, high, low, close)."""

    def __init__(self):
        super().__init__()
        self.rows: list[tuple[float, float, float, float, float]] = []
        self.picture = QPicture()

    def set_rows(self, rows) -> None:
        self.rows = rows
        self._generate()
        self.informViewBoundsChanged()
        self.update()

    def _generate(self) -> None:
        p = theme.palette()
        self.picture = QPicture()
        qp = QPainter(self.picture)
        w = 0.32
        for x, o, h, lo, c in self.rows:
            up = c >= o
            color = p.positive if up else p.negative
            qp.setPen(pen(color, 1))
            qp.drawLine(QPointF(x, lo), QPointF(x, h))
            qp.setBrush(brush(color, 0.85))
            top, bot = max(o, c), min(o, c)
            if top - bot <= 0:
                qp.drawLine(QPointF(x - w, o), QPointF(x + w, o))
            else:
                qp.drawRect(QRectF(x - w, bot, 2 * w, top - bot))
        qp.end()

    def paint(self, painter, *_args) -> None:
        painter.drawPicture(0, 0, self.picture)

    def boundingRect(self) -> QRectF:  # noqa: N802
        if not self.rows:
            return QRectF()
        lows = [r[3] for r in self.rows]
        highs = [r[2] for r in self.rows]
        return QRectF(self.rows[0][0] - 1, min(lows), self.rows[-1][0] - self.rows[0][0] + 2, max(highs) - min(lows))


@dataclass
class ChartLevels:
    entry: Decimal | None = None
    stop: Decimal | None = None
    target: Decimal | None = None
    ladder: list[tuple[str, Decimal]] = field(default_factory=list)  # ("1R", price)


@dataclass
class TradeMarker:
    date: date
    side: str  # BUY | SELL
    price: Decimal
    text: str = ""


class CandleChart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        setup_pg()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(tokens.SPACE[1])
        self.readout = QLabel(" ")
        self.readout.setFont(theme.font("num"))
        self.readout.setProperty("role", "muted")
        v.addWidget(self.readout)
        self.glw = pg.GraphicsLayoutWidget()
        self.glw.setBackground(theme.palette().surface)
        self.glw.ci.setContentsMargins(0, 0, 0, 0)
        self.glw.ci.setSpacing(0)
        v.addWidget(self.glw, 1)
        self.axis = IndexDateAxis()
        self.price = self.glw.addPlot(row=0, col=0, axisItems={"bottom": pg.AxisItem("bottom")})
        self.vol_axis = IndexDateAxis()
        self.vol = self.glw.addPlot(row=1, col=0, axisItems={"bottom": self.vol_axis})
        self.glw.ci.layout.setRowStretchFactor(0, 82)
        self.glw.ci.layout.setRowStretchFactor(1, 18)
        style_plot(self.price, left=False, right=True)
        style_plot(self.vol, grid=False, left=False, right=True)
        self.price.getAxis("bottom").setStyle(showValues=False)
        self.price.getAxis("right").setWidth(64)
        self.vol.getAxis("right").setWidth(64)
        self.vol.getAxis("right").setStyle(showValues=False)
        self.vol.setXLink(self.price)
        for pl in (self.price, self.vol):
            pl.setMouseEnabled(x=True, y=False)
        self.price.getViewBox().setAutoVisible(y=True)
        self.price.enableAutoRange(axis="y")
        self.vol.getViewBox().setAutoVisible(y=True)
        self.candles = CandlestickItem()
        self.price.addItem(self.candles)
        self.volume = pg.BarGraphItem(x=[], height=[], width=0.64)
        self.vol.addItem(self.volume)
        p = theme.palette()
        self.vline = pg.InfiniteLine(angle=90, movable=False, pen=pen(p.text_faint, 1))
        self.hline = pg.InfiniteLine(angle=0, movable=False, pen=pen(p.text_faint, 1))
        self.price.addItem(self.vline, ignoreBounds=True)
        self.price.addItem(self.hline, ignoreBounds=True)
        self._extra: list = []
        self.bars: list[Bar] = []
        self.symbol = ""
        self._proxy = pg.SignalProxy(self.price.scene().sigMouseMoved, rateLimit=60, slot=self._mouse_moved)

    # ------------------------------------------------------------------ data
    def clear(self) -> None:
        for it in self._extra:
            for pl in (self.price, self.vol):
                if it in pl.items:
                    pl.removeItem(it)
        self._extra = []
        self.candles.set_rows([])
        self.volume.setOpts(x=[], height=[])
        self.bars = []
        self.readout.setText(" ")

    def _add(self, item, plot=None, ignore=True) -> None:
        (plot or self.price).addItem(item, ignoreBounds=ignore)
        self._extra.append(item)

    def _level_line(self, y: Decimal, color: str, label: str, width: float = 1, style=Qt.PenStyle.SolidLine,
                    pill: bool = False, fill_text: str | None = None) -> None:
        p = theme.palette()
        opts = {"position": 0.985, "color": QColor(fill_text or color), "anchors": [(1, 0.5), (1, 0.5)],
                "movable": False}
        if pill:
            opts["fill"] = brush(color, 0.9)
            opts["color"] = QColor(p.text_on_accent)
            opts["border"] = None
        line = pg.InfiniteLine(pos=float(y), angle=0, movable=False, pen=pen(color, width, style), label=label,
                               labelOpts=opts)
        line.label.setFont(theme.font("caption", weight=600))
        self._add(line, ignore=not pill)  # stop / target always stay in view

    def set_data(self, symbol: str, bars: list[Bar], levels: ChartLevels | None = None,
                 markers: list[TradeMarker] | None = None, earnings: date | None = None,
                 dividends: list[date] | None = None) -> None:
        self.clear()
        self.symbol = symbol
        bars = [b for b in bars if None not in (b.open, b.high, b.low, b.close)]
        self.bars = bars
        if not bars:
            self.readout.setText(f"{symbol}  ·  no price history yet")
            return
        p = theme.palette()
        idx = {b.date: i for i, b in enumerate(bars)}
        self.vol_axis.dates = [b.date for b in bars]
        rows = [(float(i), float(b.open), float(b.high), float(b.low), float(b.close)) for i, b in enumerate(bars)]
        self.candles.set_rows(rows)
        vols = [float(b.volume or 0) for b in bars]
        colors = [p.positive if b.close >= b.open else p.negative for b in bars]
        self.volume.setOpts(x=list(range(len(bars))), height=vols, width=0.64,
                            brushes=[brush(c, 0.25) for c in colors], pens=[pen(c, 0, alpha=0) for c in colors])
        lv = levels or ChartLevels()
        for name, y in lv.ladder:
            self._level_line(y, p.text_faint, name, 1, Qt.PenStyle.DotLine)
        if lv.entry is not None:
            self._level_line(lv.entry, p.text, f"Entry {fmt.price(lv.entry)}", 1, Qt.PenStyle.DashLine)
        if lv.target is not None:
            self._level_line(lv.target, p.target, f"TGT {fmt.price(lv.target)}", 2, pill=True)
        if lv.stop is not None:
            self._level_line(lv.stop, p.stop, f"STOP {fmt.price(lv.stop)}", 2, pill=True)

        def x_for(d: date) -> int | None:
            if d in idx:
                return idx[d]
            later = [i for dd, i in idx.items() if dd >= d]
            return min(later) if later else None

        buys, sells = [], []
        for m in markers or []:
            x = x_for(m.date)
            if x is None:
                continue
            b = bars[x]
            span = float(b.high - b.low) or float(b.close) * 0.01
            if m.side == "BUY":
                buys.append({"pos": (x, float(b.low) - span * 0.35), "data": m.text})
            else:
                sells.append({"pos": (x, float(b.high) + span * 0.35), "data": m.text})
        if buys:
            self._add(pg.ScatterPlotItem(spots=buys, symbol="t1", size=9, brush=brush(p.positive), pen=None),
                      ignore=False)
        if sells:
            self._add(pg.ScatterPlotItem(spots=sells, symbol="t", size=9, brush=brush(p.negative), pen=None),
                      ignore=False)
        if earnings is not None:
            x = x_for(earnings)
            xe = float(x) if x is not None else float(len(bars) - 1 + _bdays(bars[-1].date, earnings))
            line = pg.InfiniteLine(pos=xe, angle=90, movable=False, pen=pen(p.warning, 1, Qt.PenStyle.DashLine),
                                   label="E", labelOpts={"position": 0.96, "color": QColor(p.warning),
                                                         "fill": brush(p.warning, 0.15)})
            self._add(line)
        for d in dividends or []:
            x = x_for(d)
            if x is None:
                continue
            tick = pg.ScatterPlotItem(x=[x], y=[0], symbol="s", size=5, brush=brush(p.info), pen=None)
            self._add(tick, plot=self.vol)
        n = len(bars)
        self.price.setXRange(max(-1, n - 130), n + 3, padding=0)
        self._show_readout(n - 1)

    # ------------------------------------------------------------------ crosshair
    def _show_readout(self, i: int) -> None:
        if not (0 <= i < len(self.bars)):
            return
        b = self.bars[i]
        chg = ""
        if i > 0 and self.bars[i - 1].close:
            chg = "  " + fmt.signed_pct((b.close - self.bars[i - 1].close) / self.bars[i - 1].close)  # type: ignore[operator]
        vol = f"  V {fmt.compact_money(b.volume).lstrip('$')}" if b.volume else ""
        self.readout.setText(f"{self.symbol}  {b.date:%Y-%m-%d}  O {fmt.price(b.open)}  H {fmt.price(b.high)}  "
                             f"L {fmt.price(b.low)}  C {fmt.price(b.close)}{chg}{vol}")

    def _mouse_moved(self, evt) -> None:
        pos = evt[0]
        if not self.bars or not self.price.sceneBoundingRect().contains(pos):
            return
        pt = self.price.getViewBox().mapSceneToView(pos)
        i = round(pt.x())
        self.vline.setPos(i)
        self.hline.setPos(pt.y())
        self._show_readout(max(0, min(len(self.bars) - 1, i)))


def _bdays(a: date, b: date) -> int:
    from ...market.service import business_days_between

    return business_days_between(a, b)
