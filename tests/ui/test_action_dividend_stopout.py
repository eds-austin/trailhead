from datetime import date, datetime
from decimal import Decimal as D

from trailhead.core.models import CashKind, Dividend, Portfolio, Quote, Side, Trade
from trailhead.portfolio_types.stock_trading.table_model import CELL_ROLE, COL_INDEX, action_buttons
from trailhead.ui.dialogs.stop_out_dialog import StopOutDialog


def _setup(ctx, window, last):
    p = ctx.repos.portfolios.create(Portfolio(None, "Swing", D(100000)))
    b1 = ctx.repos.trades.add(Trade(None, p.id, "XOM", Side.BUY, date(2026, 10, 1), D(80), D(118), initial_stop=D(113)))
    ctx.repos.trades.add(Trade(None, p.id, "XOM", Side.BUY, date(2026, 10, 8), D(40), D(119), initial_stop=D(114)))
    ctx.repos.market.upsert_quote(Quote("XOM", D(last), D(115), datetime(2026, 11, 20, 10), "yfinance"))
    ctx.events.portfolios_changed.emit()
    window.show_page(f"portfolio:{p.id}")
    return p, b1, window.current_tab()


def test_dividend_confirm_from_action_cell(ctx, window):
    p, _, tab = _setup(ctx, window, "116")
    ctx.repos.market.upsert_dividends([Dividend("XOM", date(2026, 11, 13), D("0.99"), "y", date(2026, 11, 18))])
    ctx.data_changed(p.id)
    cell = tab.model.index(0, COL_INDEX["action"]).data(CELL_ROLE)
    assert cell.text == "Dividend $118.80" and cell.badge == "info"
    assert ("dividend", "Confirm div") in action_buttons(cell.data)
    pv = tab.snapshot.positions[0]
    assert tab.confirm_dividends(pv) == 1
    ev = [e for e in ctx.repos.cash.list(p.id) if e.kind == CashKind.DIVIDEND]
    assert [(e.amount, e.event_date) for e in ev] == [(D("118.80"), date(2026, 11, 18))]
    assert tab.model.index(0, COL_INDEX["action"]).data(CELL_ROLE).text == ""
    assert tab.snapshot.positions[0].dividends == D("118.80")


def test_stop_out_asks_for_actual_price(ctx, window):
    p, _, tab = _setup(ctx, window, "112.50")
    pv = tab.snapshot.positions[0]
    cell = tab.model.index(0, COL_INDEX["action"]).data(CELL_ROLE)
    assert [k for k, _ in action_buttons(cell.data)] == ["stopout"]
    dlg = StopOutDialog(ctx, p, pv, parent=window)
    assert dlg.shares == 120 and dlg.price.value() == D(114)  # pre-filled with the stop
    dlg.price.setText("112.85")
    assert dlg.submit()
    sells = [t for t in ctx.repos.trades.list(p.id) if t.side == Side.SELL]
    assert sorted((t.shares, t.price) for t in sells) == [(D(40), D("112.85")), (D(80), D("112.85"))]
    ctx.data_changed(p.id)
    assert tab.snapshot.positions[0].status == "CLOSED"
