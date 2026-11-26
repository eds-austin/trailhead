from datetime import date, datetime
from decimal import Decimal as D

from PySide6.QtCore import Qt

from trailhead.core.models import Portfolio, Quote, Side, Trade
from trailhead.portfolio_types.stock_trading.table_model import CELL_ROLE, COL_INDEX


def test_child_rows_align_with_headings_and_pills_show_percent_only(ctx, window):
    p = ctx.repos.portfolios.create(Portfolio(None, "Swing", D(100000)))
    b = ctx.repos.trades.add(Trade(None, p.id, "XNCR", Side.BUY, date(2026, 10, 22), D(100), D("25.63"),
                                   initial_stop=D("20.63"), strategy="Pullback", timeframe="Weekly/Daily"))
    ctx.repos.trades.add(Trade(None, p.id, "XNCR", Side.SELL, date(2026, 10, 26), D(25), D("31.63"), lot_id=b.id))
    ctx.repos.market.upsert_quote(Quote("XNCR", D("33.26"), D(34), datetime(2026, 11, 20, 10), "yfinance"))
    ctx.events.portfolios_changed.emit()
    window.show_page(f"portfolio:{p.id}")
    m = window.current_tab().model
    parent = m.index(0, 0)

    def cell(row, key, par=parent):
        return m.index(row, COL_INDEX[key], par).data(CELL_ROLE)

    stop = m.index(0, COL_INDEX["stop"]).data(CELL_ROLE)
    assert stop.sub == "−38.0%" and "R" not in stop.sub
    assert "R" not in m.index(0, COL_INDEX["target"]).data(CELL_ROLE).sub
    # BUY row
    assert cell(0, "symbol").text == "2026-10-22"
    assert cell(0, "strategy").text == "Pullback · Weekly/Daily · 1-3-6"
    assert cell(0, "shares").text == "100"  # shares bought in the BUY, not remaining
    assert cell(0, "avg_entry").text == "25.63"
    assert cell(0, "stop").text == "20.63"
    assert cell(0, "last").text == ""
    # SELL row: price under Last, nothing under Strategy / Avg entry
    assert cell(1, "strategy").text == "" and cell(1, "avg_entry").text == ""
    assert cell(1, "shares").text == "25" and cell(1, "last").text == "31.63"
    assert cell(1, "real").text == "+$150.00" and cell(1, "r_now").text == "+0.30R"


def test_manual_sector_entry(ctx, window):
    from trailhead.ui.dialogs.sector_dialog import SectorDialog

    p = ctx.repos.portfolios.create(Portfolio(None, "Swing", D(100000)))
    b = ctx.repos.trades.add(Trade(None, p.id, "CSTM", Side.BUY, date(2025, 12, 4), D(80), D("18.28"),
                                   initial_stop=D("15.28")))
    ctx.repos.trades.add(Trade(None, p.id, "CSTM", Side.SELL, date(2026, 4, 1), D(80), D(24), lot_id=b.id))
    ctx.events.portfolios_changed.emit()
    window.show_page(f"portfolio:{p.id}")
    tab = window.current_tab()
    tab.set_show_closed(True)
    assert tab.model.index(0, COL_INDEX["sector"]).data(CELL_ROLE).text == "—"
    dlg = SectorDialog(ctx, "CSTM", parent=window)
    dlg.sector.setCurrentText("Materials")
    assert dlg.submit()
    ctx.events.market_updated.emit()
    assert tab.model.index(0, COL_INDEX["sector"]).data(CELL_ROLE).text == "Materials"
    assert "set manually" in tab.model.index(0, COL_INDEX["sector"]).data(Qt.ItemDataRole.ToolTipRole)


def test_only_buy_sell_rows_and_stop_out_badge(ctx, window):
    from trailhead.core.models import StopChange, StopReason
    from trailhead.ui.dialogs.trade_dialog import TradeDialog

    p = ctx.repos.portfolios.create(Portfolio(None, "Swing", D(100000)))
    b = ctx.repos.trades.add(Trade(None, p.id, "XNCR", Side.BUY, date(2026, 10, 22), D(100), D("25.63"),
                                   initial_stop=D("20.63")))
    ctx.repos.trades.add(Trade(None, p.id, "XNCR", Side.SELL, date(2026, 10, 26), D(25), D("31.63"), lot_id=b.id))
    ctx.repos.stops.add(StopChange(None, b.id, date(2026, 11, 20), D("25.63"), StopReason.LADDER_CONFIRMED))
    ctx.events.portfolios_changed.emit()
    window.show_page(f"portfolio:{p.id}")
    tab = window.current_tab()
    m = tab.model
    parent = m.index(0, 0)
    assert m.rowCount(parent) == 2  # BUY + SELL only; the stop change is not a row
    tip = m.index(0, COL_INDEX["sector"], parent).data(Qt.ItemDataRole.ToolTipRole)
    assert "Stop → 25.63 on 2026-11-20 (ladder)" in tip
    # a sell at or below the stop is ticked as a stop-out automatically
    d = TradeDialog(ctx, p, tab.snapshot, prefill={"side": "SELL", "symbol": "XNCR"}, parent=window)
    d.sell_shares.setText("75")
    d.sell_price.setText("25.40")
    assert d.stop_out.isChecked()
    d.sell_price.setText("30")
    assert not d.stop_out.isChecked()
    d.sell_price.setText("25.40")
    assert d.submit()
    ctx.data_changed(p.id)
    tab.filter.set_value("all")  # the position is closed now
    sell2 = m.index(2, COL_INDEX["status"], m.index(0, 0)).data(CELL_ROLE)
    assert (sell2.text, sell2.badge) == ("Stop-out", "negative")
    assert ctx.repos.trades.get(d.result_trades[0].id).exit_reason == "STOP"


def test_closed_rows_sort_with_blanks_on_columns_they_show_as_dash(ctx, window):
    p = ctx.repos.portfolios.create(Portfolio(None, "Swing", D(100000)))
    for sym, close in (("OPNA", None), ("SHUT", True), ("OPNB", None)):
        b = ctx.repos.trades.add(Trade(None, p.id, sym, Side.BUY, date(2026, 11, 2), D(10), D(50),
                                       initial_stop=D(45)))
        if close:
            ctx.repos.trades.add(Trade(None, p.id, sym, Side.SELL, date(2026, 11, 20), D(10), D(55), lot_id=b.id))
    quotes = {"OPNA": (D(51), D(50)), "SHUT": (D("50.75"), D(50)), "OPNB": (D("50.25"), D(50))}  # +2%, +1.5%, +0.5%
    for sym, (last, prev) in quotes.items():
        ctx.repos.market.upsert_quote(Quote(sym, last, prev, datetime(2026, 11, 25, 10), "yfinance"))
    ctx.events.portfolios_changed.emit()
    window.show_page(f"portfolio:{p.id}")
    tab = window.current_tab()
    tab.set_show_closed(True)
    m = tab.model
    for key in ("day_pct", "mkt_value"):
        for order in (Qt.SortOrder.DescendingOrder, Qt.SortOrder.AscendingOrder):
            m.sort(COL_INDEX[key], order)
            assert m.position_keys()[-1].startswith("SHUT"), (key, order, m.position_keys())
