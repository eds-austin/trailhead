"""Hover price peek: a small candle chart with the stop / target / entry lines, shown after hovering a holding's
symbol cell. Read-only and transient; the detail pane (double-click) is the full chart."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from PySide6.QtCore import QEvent, QModelIndex, QObject, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QAbstractItemView, QFrame, QHBoxLayout, QStackedWidget, QVBoxLayout, QWidget
from shiboken6 import isValid

from .. import format as fmt
from .. import theme
from ..charts.candle_chart import SMA_PERIOD, CandleChart, ChartLevels
from ..theme import tokens
from .common import label

SMA_WARMUP_DAYS = 31  # calendar days loaded before the visible window, so the SMA spans the whole chart


@dataclass(frozen=True)
class PeekInfo:
    symbol: str  # the data symbol (price history is stored under it)
    display: str  # as shown in the table
    name: str = ""
    last: Decimal | None = None
    day_pct: Decimal | None = None
    entry: Decimal | None = None
    entry_label: str = "Entry"
    stop: Decimal | None = None
    target: Decimal | None = None
    target_label: str = "target"  # "2R target"
    closed_on: date | None = None  # closed position: chart ends here, no levels


def distance_texts(info: PeekInfo) -> list[tuple[str, str | None]]:
    """Footer parts as (text, tone): how far the last price is from the stop and the target, in words."""
    if info.closed_on is not None:
        return [(f"Closed {fmt.short_date(info.closed_on)}", "faint")]
    last = info.last
    if last is None or last == 0:
        return [("No price yet", "faint")]
    out: list[tuple[str, str | None]] = []
    if info.stop is None:
        out.append(("No stop set", "faint"))
    elif last <= info.stop:
        out.append((f"Stop hit · {fmt.pct((info.stop - last) / last)} below", "negative"))
    else:
        out.append((f"{fmt.signed_pct((info.stop - last) / last)} to stop", "stop"))
    if info.target is None:
        out.append(("No target set", "faint"))
    elif last >= info.target:
        out.append((f"{info.target_label.capitalize()} reached", "positive"))
    else:
        out.append((f"{fmt.signed_pct((info.target - last) / last)} to {info.target_label}", "target"))
    return out


class PricePeek(QWidget):
    """The floating card. A tooltip-type window: it never takes focus or mouse input."""

    def __init__(self, parent: QWidget, hint: str = ""):
        super().__init__(parent, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFixedWidth(tokens.PEEK_W)
        self.info: PeekInfo | None = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QFrame()
        card.setProperty("role", "peek")
        outer.addWidget(card)
        v = QVBoxLayout(card)
        pad_v, pad_h = tokens.CARD_PAD
        v.setContentsMargins(pad_h, pad_v, pad_h, pad_v)
        v.setSpacing(tokens.SPACE[2])

        head = QHBoxLayout()
        head.setSpacing(tokens.SPACE[2])
        self.symbol = label("", font_token="body_strong")
        head.addWidget(self.symbol)
        self.name = label("", role="muted")
        self.name.setMinimumWidth(0)
        head.addWidget(self.name, 1)
        self.last = label("", font_token="num")
        head.addWidget(self.last)
        self.day = label("", font_token="num")
        head.addWidget(self.day)
        v.addLayout(head)

        self.stack = QStackedWidget()
        self.stack.setFixedHeight(tokens.PEEK_CHART_H)
        self.chart = CandleChart(compact=True, background=theme.palette().surface_2)
        self.message = label("", role="muted")
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stack.addWidget(self.chart)
        self.stack.addWidget(self.message)
        v.addWidget(self.stack)

        self.footer = QHBoxLayout()
        self.footer.setSpacing(tokens.SPACE[2])
        v.addLayout(self.footer)
        caps = QHBoxLayout()
        caps.setSpacing(tokens.SPACE[1])
        p = theme.palette()
        for text, color in (("Daily · 3 months ·", p.text_faint), (f"{SMA_PERIOD}-day SMA", p.sma),
                            (f"· {hint}" if hint else "", p.text_faint)):
            if text:
                cap = label(text, role="caption")
                cap.setStyleSheet(f"color: {color};")
                caps.addWidget(cap)
        caps.addStretch(1)
        v.addLayout(caps)

    def set_info(self, info: PeekInfo) -> None:
        self.info = info
        p = theme.palette()
        self.symbol.setText(info.display)
        self.name.setText(self.name.fontMetrics().elidedText(info.name, Qt.TextElideMode.ElideRight, 150))
        self.last.setText(fmt.price(info.last) if info.last is not None else "—")
        day = info.day_pct
        self.day.setText(fmt.signed_pct(day) if day is not None and info.closed_on is None else "")
        tone = p.positive if day and day > 0 else p.negative if day and day < 0 else p.text_muted
        self.day.setStyleSheet(f"color: {tone};")
        while self.footer.count():
            item = self.footer.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        font = theme.font("num")
        font.setPixelSize(12)
        for i, (text, tone) in enumerate(distance_texts(info)):  # stop on the left, target on the right
            if i:
                self.footer.addStretch(1)
            lb = label(text)
            lb.setFont(font)
            lb.setStyleSheet(f"color: {getattr(p, 'text_faint' if tone == 'faint' else tone or 'text')};")
            self.footer.addWidget(lb)

    def set_bars(self, bars, loading: bool = False, visible_from: date | None = None) -> None:
        info = self.info
        if info is None:
            return
        if not bars:
            self.chart.clear()
            self.message.setText("Loading price history…" if loading else "No price history yet")
            self.stack.setCurrentWidget(self.message)
            return
        live = info.closed_on is None  # a closed position keeps only its entry line
        levels = ChartLevels(info.entry, info.entry_label, info.stop if live else None, info.target if live else None)
        self.chart.set_data(info.symbol, bars, levels, visible_from=visible_from)
        self.stack.setCurrentWidget(self.chart)


class PeekController(QObject):
    """Shows a PricePeek after the mouse rests on a cell that `info_for` answers for (the symbol cell of a holding).

    Hidden on leaving the cell, clicking, scrolling, typing, a model reset or the window losing focus. Once a peek
    is showing, moving to another symbol switches it at once. The symbol cell's own tooltip is suppressed while
    peeks are on, since the peek's header carries the name."""

    def __init__(self, view: QAbstractItemView, ctx, info_for: Callable[[QModelIndex], PeekInfo | None],
                 hint: str = ""):
        super().__init__(view)
        self.view, self.ctx, self.info_for, self.hint = view, ctx, info_for, hint
        self.peek: PricePeek | None = None
        self._pending: tuple[PeekInfo, QRect] | None = None  # waiting out the hover delay
        self._shown_rect = QRect()  # the cell the visible peek belongs to
        self._fetching: set[str] = set()
        self._tried: set[str] = set()  # one background fetch per symbol per session
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(tokens.PEEK_DELAY_MS)
        self._timer.timeout.connect(self._show_pending)
        self._viewport = view.viewport()
        self._viewport.installEventFilter(self)
        view.installEventFilter(self)
        for bar in (view.verticalScrollBar(), view.horizontalScrollBar()):
            bar.valueChanged.connect(self.hide)
        QGuiApplication.instance().applicationStateChanged.connect(self._on_app_state)  # bound: drops with self
        self._model = None
        self._watch_model()

    def _watch_model(self) -> None:
        m = self.view.model()
        if m is self._model or m is None:
            return
        self._model = m
        for sig in (m.modelReset, m.layoutChanged, m.rowsRemoved):
            sig.connect(self.hide)

    # ---------------------------------------------------------------- events
    def eventFilter(self, obj, e) -> bool:  # noqa: N802
        if not isValid(self.view):  # the table is being torn down
            return False
        t = e.type()
        if obj is self._viewport:
            if t == QEvent.Type.MouseMove:
                self._hovered(e.position().toPoint())
            elif t == QEvent.Type.ToolTip:
                if self._info_at(e.pos())[0] is not None:
                    return True  # the peek replaces the symbol tooltip
            elif t in (QEvent.Type.Leave, QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick,
                       QEvent.Type.Wheel, QEvent.Type.ContextMenu):
                self.hide()
        elif obj is self.view and t in (QEvent.Type.KeyPress, QEvent.Type.Hide, QEvent.Type.FocusOut):
            self.hide()
        return False

    def _info_at(self, pos: QPoint) -> tuple[PeekInfo | None, QRect]:
        self._watch_model()
        idx = self.view.indexAt(pos)
        if not idx.isValid():
            return None, QRect()
        return self.info_for(idx), self.view.visualRect(idx)

    def _hovered(self, pos: QPoint) -> None:
        info, rect = self._info_at(pos)
        if info is None:
            self.hide()
            return
        if self.peek is not None and self.peek.isVisible():
            if rect != self._shown_rect:  # already peeking: switch rows at once
                self._pending = (info, rect)
                self._show_pending()
            return
        if self._pending is None or self._pending[1] != rect:
            self._pending = (info, rect)
            self._timer.start()

    def _on_app_state(self, state) -> None:
        if state != Qt.ApplicationState.ApplicationActive:
            self.hide()

    def hide(self, *_args) -> None:
        self._timer.stop()
        self._pending = None
        if self.peek is not None:
            self.peek.hide()

    # ---------------------------------------------------------------- showing
    def _window(self) -> tuple[date, date, date]:
        """(load from, show from, end): the extra month before `show from` warms up the moving average."""
        info = self.peek.info  # type: ignore[union-attr]
        end = info.closed_on or self.ctx.today()
        shown = end - timedelta(days=tokens.PEEK_DAYS)
        return shown - timedelta(days=SMA_WARMUP_DAYS), shown, end

    def _show_pending(self) -> None:
        if self._pending is None or not self.view.isVisible():
            return
        info, rect = self._pending
        self._pending = None
        if self.peek is None:
            self.peek = PricePeek(self.view.window(), self.hint)
        self.peek.set_info(info)
        start, shown, end = self._window()
        bars = self.ctx.repos.market.bars(info.symbol, start, end)
        need = not bars or bars[0].date > start + timedelta(days=10)
        loading = need and self._fetch(info.symbol, start)
        self.peek.set_bars(bars, loading=loading, visible_from=shown)
        self._shown_rect = rect
        self._place(rect)
        self.peek.show()
        self.peek.raise_()

    def _place(self, cell: QRect) -> None:
        peek = self.peek
        peek.adjustSize()  # type: ignore[union-attr]
        vp = self.view.viewport()
        gap = tokens.SPACE[2]
        top_left = vp.mapToGlobal(QPoint(cell.right() + gap, cell.top() - tokens.SPACE[2]))
        size = peek.size()  # type: ignore[union-attr]
        screen = QGuiApplication.screenAt(top_left) or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry()
        x, y = top_left.x(), top_left.y()
        if y + size.height() > avail.bottom():  # flip above the row near the bottom of the screen
            y = vp.mapToGlobal(QPoint(0, cell.bottom() + tokens.SPACE[2])).y() - size.height()
        x = max(avail.left(), min(x, avail.right() - size.width()))
        y = max(avail.top(), y)
        peek.move(x, y)  # type: ignore[union-attr]

    def _fetch(self, symbol: str, start: date) -> bool:
        """Start a background history fetch for a symbol missing bars; True while one is running."""
        if symbol in self._fetching:
            return True
        if self.ctx.market is None or symbol in self._tried:
            return False
        self._tried.add(symbol)
        self._fetching.add(symbol)
        self.ctx.market.fetch_history(symbol, start, lambda res, s=symbol: self._fetched(s, res))
        return True

    def _fetched(self, symbol: str, _result) -> None:
        self._fetching.discard(symbol)
        peek = self.peek
        if not (isValid(self) and isValid(self.view) and peek is not None and isValid(peek)):
            return  # the tab closed while the fetch ran
        if not peek.isVisible() or peek.info is None or peek.info.symbol != symbol:
            return
        start, shown, end = self._window()
        peek.set_bars(self.ctx.repos.market.bars(symbol, start, end), visible_from=shown)
