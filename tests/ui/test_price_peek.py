from datetime import date, datetime, timedelta
from decimal import Decimal as D

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent

from trailhead.core.models import Bar, Portfolio, Quote
from trailhead.ui.dialogs.trade_dialog import TradeDialog
from trailhead.ui.widgets.price_peek import PeekInfo, distance_texts

from .test_long_term import _buy
from .test_long_term import _portfolio as _long_term


def _bars(symbol, ctx, n=70, close=D(45)):
    today = ctx.today()
    days = [today - timedelta(days=i) for i in range(n * 7 // 5, -1, -1)]
    bars = [Bar(d, close - 1, close + 1, close - 2, close, 1000) for d in days if d.weekday() < 5]
    ctx.repos.market.upsert_bars(symbol, bars)


def _hover(view, column, row=0):
    idx = view.model().index(row, column)
    pos = QPointF(view.visualRect(idx).center())
    e = QMouseEvent(QEvent.Type.MouseMove, pos, view.viewport().mapToGlobal(pos), Qt.MouseButton.NoButton,
                    Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    view.viewport().setMouseTracking(True)
    from PySide6.QtWidgets import QApplication

    QApplication.sendEvent(view.viewport(), e)


def test_distance_texts():
    base = PeekInfo("W", "W", last=D(50), stop=D(45), target=D(55), target_label="2R target")
    assert distance_texts(base) == [("−10.0% to stop", "stop"), ("+10.0% to 2R target", "target")]
    hit = PeekInfo("W", "W", last=D(44), stop=D(45), target=None)
    assert [t for t, _ in distance_texts(hit)] == ["Stop hit · 2.3% below", "No target set"]
    assert distance_texts(PeekInfo("W", "W", last=D(60), stop=None, target=D(55)))[1] == ("Target reached",
                                                                                           "positive")
    assert distance_texts(PeekInfo("W", "W", closed_on=date(2026, 11, 3)))[0][0].startswith("Closed")


def test_stock_trading_peek_on_symbol_hover(ctx, window, qtbot):
    p = ctx.repos.portfolios.create(Portfolio(None, "Swing", D(100000), D("0.01")))
    ctx.events.portfolios_changed.emit()
    window.show_page(f"portfolio:{p.id}")
    tab = window.current_tab()
    _bars("W", ctx)
    ctx.repos.market.upsert_quote(Quote("W", D(45), D(44), datetime(2026, 11, 20, 10), "yfinance"))
    dlg = TradeDialog(ctx, p, tab.snapshot, parent=window)
    dlg.symbol.setText("W")
    dlg._on_symbol_done()
    dlg.price.setText("43.18")
    dlg.stop.setText("40.26")
    dlg.shares.setText("100")
    assert dlg.submit()
    tab._after_save(dlg.result_trades)

    _hover(tab.view, 1)  # not the symbol column: nothing
    qtbot.wait(600)
    assert tab.peek.peek is None or not tab.peek.peek.isVisible()

    _hover(tab.view, 0)
    qtbot.waitUntil(lambda: tab.peek.peek is not None and tab.peek.peek.isVisible(), timeout=2000)
    peek = tab.peek.peek
    assert peek.symbol.text() == "W"
    assert peek.stack.currentWidget() is peek.chart and len(peek.chart.bars) > 50
    assert peek.info.stop == D("40.26") and peek.info.target_label.endswith("R target")
    texts = [peek.footer.itemAt(i).widget().text() for i in range(peek.footer.count())
             if peek.footer.itemAt(i).widget() is not None]
    assert any(t.endswith("to stop") for t in texts)

    tab.view.verticalScrollBar().valueChanged.emit(1)  # scrolling hides it
    assert not peek.isVisible()


def test_long_term_peek_without_history(ctx, window, qtbot):
    p, tab = _long_term(ctx, window)
    _buy(ctx, p, tab, "KO", 100, 60, "Income", stop=55, target=80)
    _hover(tab.view, 0)
    qtbot.waitUntil(lambda: tab.peek.peek is not None and tab.peek.peek.isVisible(), timeout=2000)
    peek = tab.peek.peek
    assert peek.info.entry_label == "Avg cost" and peek.info.target == D(80)
    assert peek.stack.currentWidget() is peek.message  # manual provider: no history to fetch


def test_sma():
    from trailhead.ui.charts.candle_chart import sma

    out = sma([1.0, 2.0, 3.0, 4.0], period=3)
    assert out[:2] == [None, None] and out[2] == 2.0 and out[3] == 3.0


def test_compact_chart_runs_to_the_right_edge_with_axis_tags(ctx, qtbot):
    from trailhead.ui.charts.candle_chart import CandleChart, ChartLevels

    _bars("W", ctx)
    bars = ctx.repos.market.bars("W")
    shown = bars[30].date
    c = CandleChart(compact=True)
    qtbot.addWidget(c)
    c.set_data("W", bars, ChartLevels(D(44), "Entry", D(40), D(50)), visible_from=shown)
    x0, x1 = c.price.getViewBox().viewRange()[0]
    assert x1 < len(bars) and x0 > 29  # no empty space after the last bar; warm-up bars stay off screen
    assert [t[1] for t in c.level_axis.tags] == ["44.00", "50.00", "40.00"]  # entry, target, stop
    assert len(c.sma_line.xData) == len(bars) - 17


def test_far_entry_gets_an_edge_tag_not_a_line(ctx, qtbot):
    from trailhead.ui.charts.candle_chart import CandleChart, ChartLevels, entry_is_near

    _bars("W", ctx)  # every bar trades 43–46
    bars = ctx.repos.market.bars("W")
    assert entry_is_near(D(44), bars) and entry_is_near(D("42.5"), bars)
    assert not entry_is_near(D(20), bars) and not entry_is_near(D(90), bars)
    c = CandleChart(compact=True)
    qtbot.addWidget(c)
    c.set_data("W", bars, ChartLevels(D(20), "Avg cost", D(42), D(48)), visible_from=bars[30].date)
    tag, below = c._edge_tag
    assert below and tag.textItem.toPlainText().startswith("▼ Avg cost 20.00 · −55.6%")
    assert "20.00" not in [t[1] for t in c.level_axis.tags]  # no line, no axis tag
    lo = c.price.getViewBox().viewRange()[1][0]
    assert lo > 30  # the candles were not squashed to reach the entry
    c.set_data("W", bars, ChartLevels(D(44), "Entry", D(42), D(48)), visible_from=bars[30].date)
    assert c._edge_tag is None and "44.00" in [t[1] for t in c.level_axis.tags]
