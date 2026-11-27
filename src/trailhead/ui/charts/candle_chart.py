"""Daily candle chart with volume, entry / stop / target / R-ladder lines, trade markers and crosshair."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

import pyqtgraph as pg
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPicture
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ...core.models import Bar
from .. import format as fmt
from .. import theme
from ..theme import tokens
from .base import IndexDateAxis, brush, pen, setup_pg, style_plot

SMA_PERIOD = 18


def sma(closes: list[float], period: int = SMA_PERIOD) -> list[float | None]:
    """Simple moving average of the closes; None until `period` closes are in."""
    out: list[float | None] = []
    run = 0.0
    for i, c in enumerate(closes):
        run += c
        if i >= period:
            run -= closes[i - period]
        out.append(run / period if i >= period - 1 else None)
    return out


ENTRY_FIT_SPAN = 0.25  # draw the entry line if it is within this share of the chart's price span


def entry_is_near(entry: Decimal, bars: list[Bar], *levels: Decimal | None) -> bool:
    """True when stretching the chart to show the entry keeps the candles readable: the entry lies within
    ENTRY_FIT_SPAN of the span of the shown bars (and the stop / target, which are always in view)."""
    vals = [float(b.low) for b in bars if b.low is not None] + [float(b.high) for b in bars if b.high is not None]
    vals += [float(v) for v in levels if v is not None]
    if not vals:
        return True
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * ENTRY_FIT_SPAN
    return lo - pad <= float(entry) <= hi + pad


class LevelAxis(pg.AxisItem):
    """Right price axis that also paints a filled price tag for each level line (the compact chart's labels, so the
    candles can run to the right edge)."""

    PAD = 4

    def __init__(self):
        super().__init__("right")
        self.tags: list[tuple[float, str, str, str]] = []  # (price, text, fill, text color)

    def set_tags(self, tags) -> None:
        self.tags = tags
        if tags:
            fm = QFontMetrics(theme.font("caption", weight=600))
            need = max(fm.horizontalAdvance(t) for _, t, _, _ in tags) + 2 * self.PAD + 6
            self.setWidth(max(48, need))
        self.update()

    def paint(self, painter, *args) -> None:
        super().paint(painter, *args)
        view = self.linkedView()
        if not self.tags or view is None:
            return
        font = theme.font("caption", weight=600)
        fm = QFontMetrics(font)
        h = fm.height() + 2
        rect = self.boundingRect()
        painter.setFont(font)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for y, text, fill, color in self.tags:
            yy = self.mapFromScene(view.mapViewToScene(QPointF(0, y))).y()
            if not rect.top() <= yy <= rect.bottom():
                continue  # off the scale: a pinned tag would show a price at the wrong height
            top = max(rect.top(), min(yy - h / 2, rect.bottom() - h))
            box = QRectF(2, top, fm.horizontalAdvance(text) + 2 * self.PAD, h)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(fill))
            painter.drawRoundedRect(box, tokens.RADIUS["sm"], tokens.RADIUS["sm"])
            painter.setPen(QColor(color))
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)


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

    def dataBounds(self, ax: int, frac: float = 1.0, orthoRange=None):  # noqa: N802, N803
        """Lets the y auto-range fit only the candles in view (`orthoRange` is the visible x span)."""
        rows = self.rows
        if ax == 1 and orthoRange is not None:
            lo_x, hi_x = orthoRange
            rows = [r for r in rows if lo_x <= r[0] <= hi_x]
        if not rows:
            return None, None
        if ax == 0:
            return rows[0][0], rows[-1][0]
        return min(r[3] for r in rows), max(r[2] for r in rows)

    def boundingRect(self) -> QRectF:  # noqa: N802
        if not self.rows:
            return QRectF()
        lows = [r[3] for r in self.rows]
        highs = [r[2] for r in self.rows]
        return QRectF(self.rows[0][0] - 1, min(lows), self.rows[-1][0] - self.rows[0][0] + 2, max(highs) - min(lows))


@dataclass
class ChartLevels:
    entry: Decimal | None = None
    entry_label: str = "Entry"
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
    """Full chart for the detail pane, or `compact` for the hover peek: candles, the SMA and level lines only (no
    volume, readout, crosshair, R ladder or mouse interaction); the level prices are tags on the price axis so the
    candles run to the right edge."""

    def __init__(self, parent=None, compact: bool = False, background: str | None = None):
        super().__init__(parent)
        setup_pg()
        self.compact = compact
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(tokens.SPACE[1])
        self.readout = QLabel(" ")
        self.readout.setFont(theme.font("num"))
        self.readout.setProperty("role", "muted")
        self.readout.setVisible(not compact)
        v.addWidget(self.readout)
        self.glw = pg.GraphicsLayoutWidget()
        self.glw.setBackground(background or theme.palette().surface)
        self.glw.ci.setContentsMargins(0, 0, 0, 0)
        self.glw.ci.setSpacing(0)
        v.addWidget(self.glw, 1)
        self.level_axis = LevelAxis() if compact else None
        axes = {"bottom": pg.AxisItem("bottom")}
        if self.level_axis is not None:
            axes["right"] = self.level_axis
        self.price = self.glw.addPlot(row=0, col=0, axisItems=axes)
        style_plot(self.price, left=False, right=True)
        self.price.getAxis("bottom").setStyle(showValues=False)
        self.price.getAxis("right").setWidth(48 if compact else 64)
        self.price.getViewBox().setAutoVisible(y=True)
        self.price.enableAutoRange(axis="y")
        self.candles = CandlestickItem()
        self.price.addItem(self.candles)
        self.sma_line = pg.PlotDataItem(pen=pen(theme.palette().sma, 1.25, alpha=0.7))
        self.price.addItem(self.sma_line, ignoreBounds=True)
        self.sma: list[float | None] = []
        self.vol_axis = IndexDateAxis()
        self.vol = self.volume = None
        if compact:
            self.price.hideAxis("bottom")
            self.price.setMouseEnabled(x=False, y=False)
            self.price.getViewBox().setDefaultPadding(0.08)  # keep the stop / target pills off the edges
            self.glw.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        else:
            self.vol = self.glw.addPlot(row=1, col=0, axisItems={"bottom": self.vol_axis})
            self.glw.ci.layout.setRowStretchFactor(0, 82)
            self.glw.ci.layout.setRowStretchFactor(1, 18)
            style_plot(self.vol, grid=False, left=False, right=True)
            self.vol.getAxis("right").setWidth(64)
            self.vol.getAxis("right").setStyle(showValues=False)
            self.vol.setXLink(self.price)
            for pl in (self.price, self.vol):
                pl.setMouseEnabled(x=True, y=False)
            self.vol.getViewBox().setAutoVisible(y=True)
            self.volume = pg.BarGraphItem(x=[], height=[], width=0.64)
            self.vol.addItem(self.volume)
        p = theme.palette()
        self.vline = pg.InfiniteLine(angle=90, movable=False, pen=pen(p.text_faint, 1))
        self.hline = pg.InfiniteLine(angle=0, movable=False, pen=pen(p.text_faint, 1))
        if not compact:
            self.price.addItem(self.vline, ignoreBounds=True)
            self.price.addItem(self.hline, ignoreBounds=True)
        self._extra: list = []
        self._tags: list[tuple[float, str, str, str]] = []
        self._edge_tag: tuple[pg.TextItem, bool] | None = None
        self.price.getViewBox().sigRangeChanged.connect(self._place_edge_tag)
        self.bars: list[Bar] = []
        self.symbol = ""
        self._proxy = None if compact else pg.SignalProxy(self.price.scene().sigMouseMoved, rateLimit=60,
                                                          slot=self._mouse_moved)

    # ------------------------------------------------------------------ data
    def clear(self) -> None:
        for it in self._extra:
            for pl in (self.price, self.vol):
                if pl is not None and it in pl.items:
                    pl.removeItem(it)
        self._extra = []
        self.candles.set_rows([])
        self.sma_line.setData([], [])
        self.sma = []
        self._edge_tag = None
        if self.level_axis is not None:
            self.level_axis.set_tags([])
        if self.volume is not None:
            self.volume.setOpts(x=[], height=[])
        self.bars = []
        self.readout.setText(" ")

    def _add(self, item, plot=None, ignore=True) -> None:
        (plot or self.price).addItem(item, ignoreBounds=ignore)
        self._extra.append(item)

    def _level_line(self, y: Decimal, color: str, label: str, width: float = 1, style=Qt.PenStyle.SolidLine,
                    pill: bool = False, fill_text: str | None = None, fit: bool | None = None) -> None:
        """`fit`: the y range stretches to include the line (default: pills only, i.e. stop and target)."""
        p = theme.palette()
        ignore = not (pill if fit is None else fit)
        if self.compact:  # the price goes in a tag on the axis instead
            self._add(pg.InfiniteLine(pos=float(y), angle=0, movable=False, pen=pen(color, width, style)),
                      ignore=ignore)
            fill = color if pill else p.surface_3
            self._tags.append((float(y), fmt.price(y), fill, p.text_on_accent if pill else p.text))
            return
        opts = {"position": 0.985, "color": QColor(fill_text or color), "anchors": [(1, 0.5), (1, 0.5)],
                "movable": False}
        if pill:
            opts["fill"] = brush(color, 0.9)
            opts["color"] = QColor(p.text_on_accent)
            opts["border"] = None
        line = pg.InfiniteLine(pos=float(y), angle=0, movable=False, pen=pen(color, width, style), label=label,
                               labelOpts=opts)
        line.label.setFont(theme.font("caption", weight=600))
        self._add(line, ignore=ignore)  # stop / target always stay in view

    def set_data(self, symbol: str, bars: list[Bar], levels: ChartLevels | None = None,
                 markers: list[TradeMarker] | None = None, earnings: date | None = None,
                 dividends: list[date] | None = None, visible_from: date | None = None) -> None:
        """`visible_from` (compact): bars before it only warm up the moving average."""
        self.clear()
        self._tags = []
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
        self.sma = sma([float(b.close) for b in bars])
        pts = [(i, v) for i, v in enumerate(self.sma) if v is not None]
        self.sma_line.setData([x for x, _ in pts], [v for _, v in pts])
        if self.volume is not None:
            vols = [float(b.volume or 0) for b in bars]
            colors = [p.positive if b.close >= b.open else p.negative for b in bars]
            self.volume.setOpts(x=list(range(len(bars))), height=vols, width=0.64,
                                brushes=[brush(c, 0.25) for c in colors], pens=[pen(c, 0, alpha=0) for c in colors])
        lv = levels or ChartLevels()
        for name, y in [] if self.compact else lv.ladder:
            self._level_line(y, p.text_faint, name, 1, Qt.PenStyle.DotLine)
        n = len(bars)
        first = 0
        if self.compact and visible_from is not None:
            first = next((i for i, b in enumerate(bars) if b.date >= visible_from), 0)
        elif not self.compact:
            first = max(0, n - 130)
        if lv.entry is not None:
            near = entry_is_near(lv.entry, bars[first:], lv.stop, lv.target)
            if near:
                self._level_line(lv.entry, p.text, f"{lv.entry_label} {fmt.price(lv.entry)}", 1,
                                 Qt.PenStyle.DashLine, fit=True)
            else:
                self._entry_edge_tag(lv.entry, lv.entry_label, bars[-1].close)
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
        for d in [] if self.vol is None else dividends or []:
            x = x_for(d)
            if x is None:
                continue
            tick = pg.ScatterPlotItem(x=[x], y=[0], symbol="s", size=5, brush=brush(p.info), pen=None)
            self._add(tick, plot=self.vol)
        if self.compact:  # from `visible_from` to the last bar, edge to edge
            self.price.setXRange(first - 0.6, n - 0.4, padding=0)
            self.level_axis.set_tags(self._tags)  # type: ignore[union-attr]
        else:
            self.price.setXRange(max(-1, n - 130), n + 3, padding=0)
            self._show_readout(n - 1)
        self._place_edge_tag()

    # ------------------------------------------------------------------ far-away entry
    def _entry_edge_tag(self, entry: Decimal, name: str, last: Decimal | None) -> None:
        """Entry too far from the prices on screen to draw without squashing the candles: a tag pinned to the top
        or bottom edge, pointing at it, with its distance from the last close."""
        p = theme.palette()
        below = last is None or entry < last
        dist = f" · {fmt.signed_pct((entry - last) / last)}" if last else ""
        tag = pg.TextItem(f"{'▼' if below else '▲'} {name} {fmt.price(entry)}{dist}", color=QColor(p.text_muted),
                          fill=brush(p.surface_3, 0.92), border=pen(p.border_strong), anchor=(0, 1 if below else 0))
        tag.setFont(theme.font("caption", weight=600))
        tag.setZValue(20)
        self._add(tag)
        self._edge_tag = (tag, below)

    def _place_edge_tag(self, *_args) -> None:
        if self._edge_tag is None:
            return
        tag, below = self._edge_tag
        (x0, x1), (y0, y1) = self.price.getViewBox().viewRange()
        tag.setPos(x0 + (x1 - x0) * 0.01, y0 if below else y1)

    # ------------------------------------------------------------------ crosshair
    def _show_readout(self, i: int) -> None:
        if not (0 <= i < len(self.bars)):
            return
        b = self.bars[i]
        chg = ""
        if i > 0 and self.bars[i - 1].close:
            chg = "  " + fmt.signed_pct((b.close - self.bars[i - 1].close) / self.bars[i - 1].close)  # type: ignore[operator]
        vol = f"  V {fmt.compact_money(b.volume).lstrip('$')}" if b.volume else ""
        m = self.sma[i] if i < len(self.sma) else None
        ma = f"  SMA{SMA_PERIOD} {fmt.price(Decimal(str(round(m, 4))))}" if m is not None else ""
        self.readout.setText(f"{self.symbol}  {b.date:%Y-%m-%d}  O {fmt.price(b.open)}  H {fmt.price(b.high)}  "
                             f"L {fmt.price(b.low)}  C {fmt.price(b.close)}{chg}{vol}{ma}")

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
