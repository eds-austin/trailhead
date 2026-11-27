from decimal import Decimal as D

from trailhead.core.models import Portfolio
from trailhead.ui.dialogs.trade_dialog import TradeDialog


def _make_portfolio(ctx, window):
    p = ctx.repos.portfolios.create(Portfolio(None, "Swing", D(100000), D("0.01")))
    ctx.events.portfolios_changed.emit()
    window.show_page(f"portfolio:{p.id}")
    return p, window.current_tab()


def test_empty_state(window):
    assert window.current_page_key() == "dashboard"


def test_trade_entry_flow(ctx, window, qtbot):
    p, tab = _make_portfolio(ctx, window)
    assert tab.table_stack.currentWidget() is tab.empty

    assert not window.header.cash_btn.isVisible()  # stock portfolios have no cash ledger
    assert not window.actions["cash"].isEnabled()

    dlg = TradeDialog(ctx, p, tab.snapshot, parent=window)
    dlg.symbol.setText("W")
    dlg._on_symbol_done()
    dlg.price.setText("43.18")
    dlg.stop.setText("40.26")
    dlg._apply_suggested()
    assert dlg.shares.value() == 340  # rounded down to a multiple of 4
    dlg.shares.setText("100")
    assert dlg.submit()
    tab._after_save(dlg.result_trades)
    assert tab.model.rowCount() == 1
    idx = tab.model.index(0, 0)
    tab.view.expand(idx)
    assert tab.model.rowCount(idx) == 1
    assert tab.kpis.cards["realized"].value_text() == "$0.00"

    sell = TradeDialog(ctx, p, tab.snapshot, prefill={"side": "SELL", "symbol": "W"}, parent=window)
    sell.sell_shares.setText("50")
    sell.sell_price.setText("46.24")
    assert sell.submit()
    tab._after_save(sell.result_trades)
    assert tab.kpis.cards["realized"].value_text() == "+$153.00"
    assert tab.model.rowCount(tab.model.index(0, 0)) == 2


def test_split_sell_and_close(ctx, window):
    p, tab = _make_portfolio(ctx, window)
    for price in ("10", "11"):
        d = TradeDialog(ctx, p, tab.snapshot, parent=window)
        d.symbol.setText("ABC")
        d.price.setText(price)
        d.stop.setText("9")
        d.shares.setText("10")
        assert d.submit()
        ctx.data_changed(p.id)
    s = TradeDialog(ctx, p, tab.snapshot, prefill={"side": "SELL", "symbol": "ABC"}, parent=window)
    s.sell_shares.setText("20")
    s.sell_price.setText("12")
    assert s.split_strip.isVisibleTo(s)
    assert s.submit()
    assert len(s.result_trades) == 2
    ctx.data_changed(p.id)
    pv = tab.snapshot.positions[0]
    assert pv.status == "CLOSED"
    assert tab.model.rowCount() == 0  # Open shows only open positions, even one closed today
    tab.filter.set_value("all")
    assert tab.model.rowCount() == 1


def test_max_amount_warning(ctx, window):
    p, tab = _make_portfolio(ctx, window)
    d = TradeDialog(ctx, p, tab.snapshot, parent=window)
    d.symbol.setText("BIG")
    d.price.setText("1000")
    d.stop.setText("990")
    d.shares.setText("101")
    d._update_live()
    texts = [d.warn_box.itemAt(i).widget().findChild(type(d.title_label)).text() for i in range(d.warn_box.count())]
    assert any("max" in t for t in texts)
    assert d.primary_btn.text() == "Save anyway"


def test_invalid_stop_blocks(ctx, window):
    p, tab = _make_portfolio(ctx, window)
    d = TradeDialog(ctx, p, tab.snapshot, parent=window)
    d.symbol.setText("X")
    d.price.setText("10")
    d.stop.setText("10")
    d.shares.setText("1")
    assert not d.submit()


def test_column_state_remembered(ctx, window):
    p, tab = _make_portfolio(ctx, window)
    tab.view.setColumnHidden(1, True)
    tab._save_columns()
    from trailhead.portfolio_types.stock_trading.tab import PortfolioTab

    tab2 = PortfolioTab(ctx, p)
    assert tab2.view.isColumnHidden(1)


def test_exit_plan_123_saved_and_drives_actions(ctx, window):
    from datetime import datetime

    from trailhead.core.models import Quote

    p, tab = _make_portfolio(ctx, window)
    d = TradeDialog(ctx, p, tab.snapshot, parent=window)
    d.symbol.setText("XNCR")
    d.shares.setText("100")
    d.price.setText("25.00")
    d.stop.setText("20.00")
    d.exit_plan.setCurrentIndex(d.exit_plan.findData("1-2-3"))
    d._update_live()
    assert d.levels_line.text().endswith("3R 40.00")  # 1-2-3 shows 1R, 2R, 3R
    assert d.submit()
    assert ctx.repos.trades.get(d.result_trades[0].id).exit_plan == "1-2-3"
    ctx.repos.market.upsert_quote(Quote("XNCR", D("35.10"), D(35), datetime(2026, 11, 20, 10), "yfinance"))
    ctx.events.market_updated.emit()
    pv = tab.snapshot.positions[0]
    acts = {a.kind.value: a for a in pv.actions}
    assert acts["SELL_TRANCHE"].shares == 50  # ¼ at 1R and ¼ at 2R
    assert acts["RAISE_STOP"].price == D(30)  # 2R reached → stop to 1R (25 + 5)
    assert pv.next_target == D(40)  # next whole R (3R)


def test_suggested_tranche_is_next_quarter_even_when_two_are_due(ctx, window):
    """80 sh, 20 sold, price past 6R (two tranches due): the sell dialog suggests 20, not 40."""
    from datetime import date, datetime

    from trailhead.core.models import Quote, Side, Trade

    p, tab = _make_portfolio(ctx, window)
    b = ctx.repos.trades.add(Trade(None, p.id, "CSTM", Side.BUY, date(2025, 12, 4), D(80), D("18.28"),
                                   initial_stop=D("15.28")))
    ctx.repos.trades.add(Trade(None, p.id, "CSTM", Side.SELL, date(2026, 1, 5), D(20), D("21.28"), lot_id=b.id))
    ctx.repos.market.upsert_quote(Quote("CSTM", D("37"), D("36"), datetime(2026, 11, 20, 10), "yfinance"))
    ctx.events.market_updated.emit()
    pv = tab.snapshot.positions[0]
    assert pv.top_action.shares == 40  # the action badge still reports everything due
    d = TradeDialog(ctx, p, tab.snapshot, prefill={"side": "SELL", "symbol": "CSTM"}, parent=window)
    assert d._tranche() == 20
    assert d.use_tranche.text() == "Suggested tranche: 20"


def test_new_buy_fetches_the_current_price(ctx, window, monkeypatch):
    from datetime import datetime

    from trailhead.core.models import Quote

    p, tab = _make_portfolio(ctx, window)
    asked = []

    def fake_fetch(symbol, callback):
        asked.append(symbol)
        q = Quote(symbol, D("43.18"), D("42.90"), datetime(2026, 11, 20, 10), "yfinance")
        ctx.repos.market.upsert_quote(q)
        callback(q)

    monkeypatch.setattr(ctx.market, "fetch_quote", fake_fetch)
    dlg = TradeDialog(ctx, p, tab.snapshot, parent=window)
    dlg.symbol.setText("DD")
    dlg._on_symbol_done()
    dlg._on_symbol_done()  # editingFinished fires again on focus changes: one fetch per symbol
    assert asked == ["DD"] and dlg.price.value() == D("43.18")
    assert "last 43.18" in dlg.symbol_info.text()
    assert not dlg.use_suggested.isVisible() or not dlg.use_suggested.text()  # no stop yet: nothing to size
    dlg.stop.setText("40.26")
    dlg._update_live()
    assert dlg.use_suggested.text() == "Use suggested: 340"
    assert dlg.shares.value() is None  # not filled until the link is clicked
    dlg._apply_suggested()
    assert dlg.shares.value() == 340

    typed = TradeDialog(ctx, p, tab.snapshot, parent=window)
    typed.price.setText("41.00")
    typed.price.user_edited = True  # a typed limit price is never overwritten
    typed.symbol.setText("KO")
    typed._on_symbol_done()
    assert typed.price.value() == D("41.00")
