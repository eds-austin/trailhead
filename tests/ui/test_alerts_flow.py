from datetime import date, datetime
from decimal import Decimal as D

from trailhead.core.models import CashEvent, CashKind, Portfolio, Quote, Side, Trade
from trailhead.portfolio_types.stock_trading.table_model import CELL_ROLE, COL_INDEX


def _setup(ctx, window):
    p = ctx.repos.portfolios.create(Portfolio(None, "Swing", D(100000), D("0.01")))
    ctx.repos.cash.add(CashEvent(None, p.id, date(2026, 10, 1), CashKind.DEPOSIT, D(100000)))
    ctx.repos.trades.add(Trade(None, p.id, "NVDA", Side.BUY, date(2026, 10, 2), D(100), D(120),
                               initial_stop=D(114)))
    ctx.events.portfolios_changed.emit()
    window.show_page(f"portfolio:{p.id}")
    return p, window.current_tab()


def test_manual_price_below_stop_raises_stop_hit(ctx, window):
    _, tab = _setup(ctx, window)
    ctx.repos.market.upsert_quote(Quote("NVDA", D("113.50"), D(115), datetime(2026, 11, 20, 10), "manual", True))
    ctx.events.market_updated.emit()
    status = tab.model.index(0, COL_INDEX["status"]).data(CELL_ROLE)
    action = tab.model.index(0, COL_INDEX["action"]).data(CELL_ROLE)
    assert status.badge == "stop_hit"
    assert action.text == "Stop hit — sell 100 sh"
    new = window.run_alerts()
    assert [a.kind.value for a in new] == ["STOP_HIT"]
    assert window.header.bell.count() == 1
    assert window.run_alerts() == []  # dedup
    window.toggle_alerts()
    assert window.alerts_panel.isVisible()
    window.alerts_panel._ack_all()
    assert window.header.bell.count() == 0


def test_confirm_stop_writes_stop_change(ctx, window):
    p, tab = _setup(ctx, window)
    ctx.repos.market.upsert_quote(Quote("NVDA", D("126.50"), D(125), datetime(2026, 11, 20, 10), "yfinance"))
    ctx.events.market_updated.emit()
    pv = tab.snapshot.positions[0]
    assert {a.kind.value for a in pv.actions} == {"SELL_TRANCHE", "RAISE_STOP"}
    assert tab.confirm_stops(pv) == 1
    stops = ctx.repos.stops.list_for_portfolio(p.id)
    assert [(s.stop, s.reason.value) for s in stops] == [(D(117), "LADDER_CONFIRMED")]
    pv = tab.snapshot.positions[0]
    assert {a.kind.value for a in pv.actions} == {"SELL_TRANCHE"}
    # Record sell pre-fill uses the suggested tranche
    from trailhead.ui.dialogs.trade_dialog import TradeDialog

    a = pv.top_action
    d = TradeDialog(ctx, p, tab.snapshot, prefill={"side": "SELL", "symbol": "NVDA", "lot_id": a.lot_id,
                                                   "shares": a.shares, "price": pv.last}, parent=window)
    assert d.sell_shares.value() == 25 and d.sell_price.value() == D("126.50")
