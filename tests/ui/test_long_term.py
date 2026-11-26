from datetime import date, datetime
from decimal import Decimal as D

from PySide6.QtCore import Qt

from trailhead.core.models import CashKind, Dividend, Portfolio, Quote, Side
from trailhead.core.stats import PeriodKey, period_for
from trailhead.portfolio_types import registry
from trailhead.portfolio_types.long_term.table_model import COL_INDEX, action_buttons
from trailhead.portfolio_types.stock_trading.table_model import CELL_ROLE
from trailhead.ui.dialogs.confirm_dividend_dialog import ConfirmDividendDialog
from trailhead.ui.dialogs.holding_trade_dialog import HoldingTradeDialog
from trailhead.ui.dialogs.portfolio_dialog import PortfolioDialog


def _portfolio(ctx, window):
    p = ctx.repos.portfolios.create(Portfolio(None, "Retirement", D(0), type="long_term"))
    ctx.repos.market.upsert_quote(Quote("KO", D(70), D(69), datetime(2026, 11, 20, 10), "yfinance"))
    ctx.events.portfolios_changed.emit()
    window.show_page(f"portfolio:{p.id}")
    tab = window.current_tab()
    assert type(tab).__name__ == "LongTermTab"
    return p, tab


def _buy(ctx, p, tab, symbol, shares, price, asset_type, stop=None, target=None):
    dlg = HoldingTradeDialog(ctx, p, tab.snapshot, prefill={"side": "BUY"}, parent=tab)
    dlg.symbol.setText(symbol)
    dlg._on_symbol_done()
    dlg.asset_type.setCurrentText(asset_type)
    dlg.shares.set_value(D(shares))
    dlg.price.set_value(D(price))
    dlg.price.user_edited = True
    if stop is not None:
        dlg.stop.set_value(D(stop))
    if target is not None:
        dlg.target.set_value(D(target))
    dlg.date.setDate(dlg.date.date().addDays(-300))
    assert dlg.submit(), dlg.error_label.text()
    tab._after_save(dlg.result_trades)
    return dlg.result_trades


def test_new_portfolio_dialog_hides_risk_fields(ctx, window):
    dlg = PortfolioDialog(ctx, parent=window)
    dlg.type.setCurrentIndex(dlg.type.findData("long_term"))
    assert dlg.max_amount.isHidden() and dlg.risk.isHidden()
    dlg.name.setText("Income")
    assert dlg.submit()
    assert dlg.result.type == "long_term" and dlg.result.max_amount == 0


def test_buy_dividend_reinvest_and_sell(ctx, window):
    p, tab = _portfolio(ctx, window)
    (t,) = _buy(ctx, p, tab, "KO", 100, 60, "Income")
    assert (t.asset_type, t.initial_stop, t.target_price) == ("Income", None, None)
    row = {k: tab.model.index(0, i).data() for k, i in COL_INDEX.items()}
    assert row["type"] == "Income" and row["mkt_value"] == "$7,000.00" and row["total"] == "+$1,000.00"
    assert row["stop"] == "—" and row["target"] == "—"

    # a dividend goes ex while held → Confirm div in the Action cell
    ctx.repos.market.upsert_dividends([Dividend("KO", date(2026, 11, 13), D("0.51"), "y", date(2026, 11, 18))])
    ctx.data_changed(p.id)
    cell = tab.model.index(0, COL_INDEX["action"]).data(CELL_ROLE)
    assert cell.text == "Dividend $51.00" and ("dividend", "Confirm div") in action_buttons(cell.data)
    h = tab.snapshot.holdings[0]
    (d,) = tab.model.pending_divs[h.key]
    dlg = ConfirmDividendDialog(ctx, p, h, d, parent=window)
    assert dlg.amount.value() == D("51.00") and not dlg.reinvest.isChecked()
    dlg.reinvest.setChecked(True)
    dlg.r_price.set_value(D(68))
    dlg.r_price.user_edited = True
    dlg._suggest_shares()
    assert dlg.r_shares.value() == D("0.75")  # 51 / 68
    assert dlg.submit(), dlg.error_label.text()
    ctx.data_changed(p.id)

    ev = [e for e in ctx.repos.cash.list(p.id) if e.kind == CashKind.DIVIDEND]
    assert [(e.amount, e.event_date, e.dividend_ex_date) for e in ev] == [
        (D("51.00"), date(2026, 11, 18), date(2026, 11, 13))]
    drip = [x for x in ctx.repos.trades.list(p.id) if x.drip]
    assert [(x.shares, x.price, x.trade_date, x.asset_type) for x in drip] == [
        (D("0.75"), D(68), date(2026, 11, 18), "Income")]
    h = tab.snapshot.holdings[0]
    assert h.open_shares == D("100.75") and h.dividends == D(51)
    assert h.invested == D(6000)  # reinvesting is not new money
    assert tab.model.index(0, COL_INDEX["action"]).data(CELL_ROLE).text == ""
    # child rows: BUY, DRIP and the dividend payment
    kinds = [tab.model.index(r, COL_INDEX["type"], tab.model.index(0, 0)).data() for r in range(3)]
    assert kinds == ["▲ BUY  #1", "◆ DIV", "▲ DRIP  #2"]

    # the next dividend on this holding remembers DRIP
    assert ConfirmDividendDialog(ctx, p, h, parent=window).reinvest.isChecked()

    # sell part of it: FIFO from the first lot
    dlg = HoldingTradeDialog(ctx, p, tab.snapshot, prefill={"side": "SELL", "symbol": "KO"}, parent=window)
    assert dlg.all_btn.text() == "All 100.75"
    dlg.shares.set_value(D(40))
    dlg.price.set_value(D(72))
    assert dlg.submit(), dlg.error_label.text()
    (sell,) = dlg.result_trades
    assert sell.side == Side.SELL and sell.lot_id == t.id
    ctx.data_changed(p.id)
    assert tab.snapshot.holdings[0].realized_pl == D(480)


def test_stop_target_levels_and_alerts(ctx, window):
    p, tab = _portfolio(ctx, window)
    ctx.repos.market.upsert_quote(Quote("GLD", D(255), D(250), datetime(2026, 11, 20, 10), "yfinance"))
    _buy(ctx, p, tab, "GLD", 10, 180, "Precious metals", target="250")
    h = tab.snapshot.holdings[0]
    assert h.status == "TARGET"
    cell = tab.model.index(0, COL_INDEX["action"]).data(CELL_ROLE)
    assert cell.text.startswith("Target reached") and ("sell", "Record sell") in action_buttons(cell.data)
    alerts = window.run_alerts()
    assert [(a.symbol, a.kind.value) for a in alerts] == [("GLD", "TARGET_REACHED")]
    # an add-on buy inherits the holding's levels; setting a stop applies to every open lot
    _buy(ctx, p, tab, "GLD", 2, 240, "Precious metals")
    ctx.repos.trades.set_levels([lot.id for lot in tab.snapshot.holdings[0].open_lots], D(230), D(300))
    ctx.data_changed(p.id)
    h = tab.snapshot.holdings[0]
    assert (h.stop, h.target, h.status) == (D(230), D(300), "OPEN")
    assert all(lot.buy.initial_stop == D(230) for lot in h.open_lots)


def test_dashboard_includes_long_term(ctx, window):
    p, tab = _portfolio(ctx, window)
    _buy(ctx, p, tab, "KO", 100, 60, "Income")
    window.show_page("dashboard")  # renders without errors
    s = registry.get("long_term").dashboard_summary(ctx, p, period_for(PeriodKey.ALL, ctx.today()))
    assert s.value == D(7000) and s.unrealized_pl == D(1000) and s.open_risk is None
    assert s.period_pl == D(1000)


def test_find_symbol_for_holding_without_price(ctx, window, monkeypatch):
    from trailhead.market.provider import SymbolMatch
    from trailhead.ui.dialogs import symbol_picker_dialog as spd

    ctx.repos.settings.set("market.provider", "yfinance")  # missing prices are flagged unless offline
    p, tab = _portfolio(ctx, window)
    _buy(ctx, p, tab, "BRK.B", 300, "40.69", "Equity")
    cell = tab.model.index(0, COL_INDEX["action"]).data(CELL_ROLE)
    assert cell.text == "No price" and ("symbol", "Find symbol") in action_buttons(cell.data)

    dlg = spd.SymbolPickerDialog(ctx, "BRK.B", parent=window)
    dlg.set_matches([SymbolMatch("BRK-B", "Berkshire Hathaway Inc.", "NYSE", "Equity", likely=True),
                     SymbolMatch("BRKU", "Direxion Daily BRKB Bull 2X ETF", "NASDAQ", "ETF")])
    assert dlg.primary_btn.text() == "Use BRK-B" and dlg.table.item(0, 3).text() == "Likely match · Equity"
    assert dlg.submit() and dlg.result == "BRK-B"

    monkeypatch.setattr(spd, "pick_symbol", lambda *a, **k: "BRK-B")
    monkeypatch.setattr(ctx, "market", None)
    tab.change_symbol("BRK.B")
    assert [h.symbol for h in tab.snapshot.holdings] == ["BRK-B"]


def test_berkshire_split_applied_or_already_included(ctx, window):
    from trailhead.core.models import Trade
    from trailhead.ui.dialogs.splits_dialog import SplitsDialog

    p, tab = _portfolio(ctx, window)
    ctx.repos.market.upsert_quote(Quote("BRK-B", D(480), D(478), datetime(2026, 11, 20, 10), "yfinance"))
    ctx.repos.trades.add(Trade(None, p.id, "BRK-B", Side.BUY, date(1998, 5, 4), D(6), D("2034.50"),
                               asset_type="Equity"))
    ctx.repos.splits.upsert_market([("BRK-B", date(2010, 1, 21), D(50), "yfinance")])
    ctx.data_changed(p.id)
    cell = tab.model.index(0, COL_INDEX["action"]).data(CELL_ROLE)
    assert cell.text == "Split 50-for-1?" and ("split", "Review split") in action_buttons(cell.data)
    assert window.update_pending_splits() == 1

    dlg = SplitsDialog(ctx, p, parent=window)
    assert dlg.pending.rowCount() == 1 and dlg.pending.item(0, 4).text() == "6 → 300 sh"
    dlg._record(p, "BRK-B", date(2010, 1, 21), D(50), True)
    assert dlg.pending.isHidden() and dlg.recorded.item(0, 4).text() == "Applied"
    h = tab.snapshot.holdings[0]
    assert (h.open_shares, h.avg_cost, h.cost_basis) == (D(300), D("40.69"), D("12207.00"))
    assert h.market_value == D(144000)
    kinds = [tab.model.index(r, COL_INDEX["type"], tab.model.index(0, 0)).data() for r in range(2)]
    assert kinds == ["▲ BUY  #1", "⇄ SPLIT"]
    assert tab.model.index(0, COL_INDEX["action"]).data(CELL_ROLE).text == ""
    # a sell today is typed in today's shares
    dlg = HoldingTradeDialog(ctx, p, tab.snapshot, prefill={"side": "SELL", "symbol": "BRK-B"}, parent=window)
    assert dlg.all_btn.text() == "All 300"
    dlg.shares.set_value(D(100))
    dlg.price.set_value(D(480))
    assert dlg.submit(), dlg.error_label.text()
    ctx.data_changed(p.id)
    assert tab.snapshot.holdings[0].realized_pl == D(100) * (D(480) - D("40.69"))


def test_split_already_in_my_numbers(ctx, window):
    from trailhead.core.models import Trade

    p, tab = _portfolio(ctx, window)
    ctx.repos.trades.add(Trade(None, p.id, "BRK-B", Side.BUY, date(1998, 5, 4), D(300), D("40.69")))
    ctx.repos.splits.upsert_market([("BRK-B", date(2010, 1, 21), D(50), "yfinance")])
    ctx.data_changed(p.id)
    from trailhead.portfolio_types.splits import record_split

    record_split(ctx.repos, p.id, "BRK-B", date(2010, 1, 21), D(50), apply=False)
    ctx.data_changed(p.id)
    assert window.update_pending_splits() == 0
    assert tab.snapshot.holdings[0].open_shares == D(300)


def test_fdx_spinoff(ctx, window):
    from trailhead.core.models import Trade
    from trailhead.ui.dialogs.spinoff_dialog import SpinoffDialog

    p, tab = _portfolio(ctx, window)
    r = ctx.repos
    r.market.upsert_quote(Quote("FDX", D(330), D(328), datetime(2026, 11, 20, 10), "yfinance"))
    r.market.upsert_quote(Quote("FDXF", D(115), D(114), datetime(2026, 11, 20, 10), "yfinance"))
    lot = r.trades.add(Trade(None, p.id, "FDX", Side.BUY, date(2024, 1, 2), D(100), D(250), asset_type="Equity"))
    r.trades.add(Trade(None, p.id, "FDX", Side.SELL, date(2025, 5, 1), D(20), D(300), lot_id=lot.id))
    r.splits.upsert_market([("FDX", date(2026, 6, 1), D("1.241"), "yfinance")])  # how Yahoo records it
    r.market.upsert_dividends([Dividend("FDX", date(2026, 3, 9), D("1.1684"), "y")])  # Yahoo-adjusted 1.45
    ctx.data_changed(p.id)
    cell = tab.model.index(0, COL_INDEX["action"]).data(CELL_ROLE)
    assert cell.text == "Spin-off Jun 1?" and ("spinoff", "Record spin-off") in action_buttons(cell.data)

    h = tab.snapshot.holdings[0]
    dlg = SpinoffDialog(ctx, p, h, parent=window)
    assert dlg.pct.value() == D("19.42") and dlg._ex() == date(2026, 6, 1)  # 1 − 1/1.241
    dlg.child.setText("FDXF")
    dlg.shares.set_value(D(40))
    assert dlg.submit(), dlg.error_label.text()
    ctx.data_changed(p.id)

    fdx, fdxf = sorted(tab.snapshot.holdings, key=lambda x: x.symbol)
    moved = D(80) * D(250) * D("0.1942")  # 3,884
    assert fdx.cost_basis == D(20000) - moved and fdx.avg_cost == D("201.45")
    assert fdx.realized_pl == D(1000)  # the sale before the spin-off keeps the full cost
    assert (fdxf.open_shares, fdxf.cost_basis, fdxf.asset_type) == (D(40), moved, "Equity")
    assert fdx.invested + fdxf.invested == D(25000)  # no new money
    assert tab.snapshot.total_return == fdx.total_return + fdxf.total_return
    assert window.update_pending_splits() == 0  # Yahoo's 1.241 "split" is handled
    # the March dividend is proposed at the real $1.45, not Yahoo's adjusted 1.1684
    (d,) = tab.model.pending_divs[fdx.key]
    assert d.amount_per_share == D("1.4500") and d.amount == D("116.00")
    rows = tab.model.position_keys()
    i = rows.index(fdx.key)
    kinds = [tab.model.index(r_, COL_INDEX["type"], tab.model.index(i, 0)).data()
             for r_ in range(tab.model.rowCount(tab.model.index(i, 0)))]
    assert "⑂ SPIN-OFF" in kinds
    j = rows.index(fdxf.key)
    assert tab.model.index(0, COL_INDEX["type"], tab.model.index(j, 0)).data() == "▲ SPIN-OFF  #1"

    # undo gives FDX its full cost back and removes FDXF
    from trailhead.portfolio_types.spinoffs import undo_spinoff

    undo_spinoff(r, r.spinoffs.list(p.id)[0])
    ctx.data_changed(p.id)
    assert [x.symbol for x in tab.snapshot.holdings] == ["FDX"]
    assert tab.snapshot.holdings[0].cost_basis == D(20000)


def test_no_price_is_visible_and_asks_once(ctx, window, monkeypatch):
    from trailhead.ui.dialogs import symbol_picker_dialog as spd

    ctx.repos.settings.set("market.provider", "yfinance")
    p, tab = _portfolio(ctx, window)
    _buy(ctx, p, tab, "BRK.B", 300, "40.69", "Equity")
    status = tab.model.index(0, COL_INDEX["status"]).data(CELL_ROLE)
    assert (status.text, status.badge) == ("No price", "stale")
    assert "double-click" in tab.model.index(0, COL_INDEX["status"]).data(Qt.ItemDataRole.ToolTipRole)

    calls = []
    monkeypatch.setattr(spd, "change_symbol", lambda ctx_, sym, parent, **kw: calls.append((sym, kw)) or None)
    tab._on_position_activated(tab.model.index(0, COL_INDEX["status"]))  # double-click the badge
    assert calls == [("BRK.B", {})]

    calls.clear()
    assert window.missing_price_symbols() == ["BRK.B"]
    window.ask_missing_symbols()  # after a refresh: pops up; "Keep BRK.B" (None) remembers the choice
    assert calls == [("BRK.B", {"keep_text": "Keep BRK.B", "no_price": True})]
    assert ctx.setting("market.keep_symbols") == ["BRK.B"]
    window.ask_missing_symbols()
    assert len(calls) == 1 and window.missing_price_symbols() == []


def test_return_card_follows_period(ctx, window):
    p, tab = _portfolio(ctx, window)
    _buy(ctx, p, tab, "KO", 100, 60, "Income")  # bought ~300 days ago: all of it in the last 12 months
    card = tab.kpis.cards["total_return"]
    tab.period.setCurrentIndex(tab.period.findData("ALL"))
    assert card._label.text() == "Total return" and card.value_text() == "+$1,000.00"
    tab.period.setCurrentIndex(tab.period.findData("L12M"))
    assert card._label.text() == "Total return - 12M" and card.value_text() == "+$1,000.00"
    assert "all time +$1,000" in card._sub.full_text()


def test_confirming_several_reinvested_dividends_counts_earlier_drip_shares(ctx, window, monkeypatch):
    p, tab = _portfolio(ctx, window)
    _buy(ctx, p, tab, "KO", 100, 60, "Income")  # bought 300 days before 2026-11-20
    ctx.repos.market.upsert_dividends([Dividend("KO", date(2026, 3, 13), D("0.50"), "y", date(2026, 3, 31)),
                                       Dividend("KO", date(2026, 6, 12), D("0.50"), "y", date(2026, 6, 30))])
    ctx.data_changed(p.id)
    proposed = []

    def fake_exec(dlg):
        proposed.append((dlg.proposal.shares, dlg.amount.value()))
        dlg.reinvest.setChecked(True)
        dlg.r_price.set_value(D(50))
        dlg.r_shares.set_value(D(1))  # $50 bought 1 share
        return dlg.submit()

    monkeypatch.setattr(ConfirmDividendDialog, "exec", fake_exec)
    assert tab.confirm_dividends(tab.snapshot.holdings[0]) == 2
    assert proposed == [(D(100), D("50.00")), (D(101), D("50.50"))]  # the 2nd pays on the reinvested share too
    assert tab.snapshot.holdings[0].open_shares == D(102)


def test_awp_consolidation_and_drip_before_it(ctx, window):
    from trailhead.core.models import Bar, Trade
    from trailhead.portfolio_types.splits import record_split
    from trailhead.ui.dialogs.splits_dialog import SplitsDialog

    p, tab = _portfolio(ctx, window)
    ctx.repos.market.upsert_quote(Quote("AWP", D("12.30"), D("12.20"), datetime(2026, 11, 20, 10), "yfinance"))
    ctx.repos.trades.add(Trade(None, p.id, "AWP", Side.BUY, date(2025, 2, 28), D(2493), D("4.015"),
                               asset_type="Income"))
    # Yahoo: the consolidation as a rounded 'split'; dividends and closes in post-consolidation terms (×3)
    ctx.repos.splits.upsert_market([("AWP", date(2026, 2, 9), D("0.3333"), "yfinance")])
    ctx.repos.market.upsert_dividends([Dividend("AWP", date(2025, 3, 20), D("0.1971"), "y", date(2025, 3, 31))])
    ctx.repos.market.upsert_bars("AWP", [Bar(date(2025, 3, 31), None, None, None, D("12.30"), None)])
    ctx.data_changed(p.id)
    dlg = SplitsDialog(ctx, p, parent=window)
    assert dlg.pending.item(0, 4).text() == "2,493 → 831 sh"
    record_split(ctx.repos, p.id, "AWP", date(2026, 2, 9), D(1) / D(3), True)
    ctx.data_changed(p.id)
    h = tab.snapshot.holdings[0]
    assert (h.open_shares, h.cost_basis) == (D(831), D("10009.395"))
    (d,) = tab.model.pending_divs[h.key]
    c = ConfirmDividendDialog(ctx, p, h, d, parent=window)
    assert c.amount.value() == D("163.79")  # 831 × 0.1971 = 2,493 × 0.0657
    assert c.reinvest.isChecked() or c.reinvest.setChecked(True) is None
    assert c.r_price.value() == D("4.10")  # the close on the pay date, in that day's (pre-consolidation) price
    assert c.r_shares.value() == D("39.9487")  # 163.79 / 4.10, in that day's shares
    assert c.submit()
    ctx.data_changed(p.id)
    assert tab.snapshot.holdings[0].open_shares == D(831) + (D("39.9487") / 3).quantize(D("1e-8"))


def test_bullion_in_ounces(ctx, window):
    p, tab = _portfolio(ctx, window)
    ctx.repos.market.upsert_quote(Quote("GC=F", D("3600.90"), D("3535.80"), datetime(2026, 11, 20, 10), "yfinance"))
    dlg = HoldingTradeDialog(ctx, p, tab.snapshot, prefill={"side": "BUY"}, parent=tab)
    assert dlg.kind.isHidden()  # only offered for Precious metals
    dlg.asset_type.setCurrentText("Precious metals")
    assert not dlg.kind.isHidden()
    dlg.kind.set_value("bullion")
    assert dlg.symbol.isHidden() and not dlg.metal.isHidden()
    dlg.asset_type.setCurrentText("ETF")  # back to a ticker
    assert dlg.kind.isHidden() and not dlg.is_bullion() and not dlg.symbol.isHidden()
    dlg.asset_type.setCurrentText("Precious metals")
    dlg.kind.set_value("bullion")
    assert dlg.shares_label.text() == "Ounces" and dlg.sym() == "GC=F"
    assert dlg.price.value() == D("3600.90")  # pre-filled with the metal's price
    dlg.shares.set_value(D("2.5"))
    dlg.price.set_value(D("2700"))
    dlg.price.user_edited = True
    dlg.date.setDate(dlg.date.date().addDays(-200))
    assert dlg.submit(), dlg.error_label.text()
    (t,) = dlg.result_trades
    assert (t.symbol, t.shares, t.asset_type) == ("GC=F", D("2.5"), "Precious metals")
    tab._after_save(dlg.result_trades)
    row = {k: tab.model.index(0, i).data() for k, i in COL_INDEX.items()}
    assert row["symbol"] == "Gold" and row["shares"] == "2.5 oz" and row["mkt_value"] == "$9,002.25"
    tab.search.setText("gold")
    assert tab.model.rowCount() == 1
    tab.search.setText("")
    h = tab.snapshot.holdings[0]
    from trailhead.portfolio_types.long_term.table_model import action_buttons as ab
    assert ("symbol", "Find symbol") not in ab((h, [], []))
    # selling opens in bullion mode for the holding
    sell = HoldingTradeDialog(ctx, p, tab.snapshot, prefill={"side": "SELL", "symbol": "GC=F"}, parent=tab)
    assert sell.is_bullion() and sell.metal.currentText() == "Gold"
    sell.shares.set_value(D(1))
    sell.price.set_value(D(3600))
    sell.price.user_edited = True
    assert sell.submit(), sell.error_label.text()
    ctx.data_changed(p.id)
    assert tab.snapshot.holdings[0].open_shares == D("1.5")
    # editing the buy keeps bullion mode
    ed = HoldingTradeDialog(ctx, p, tab.snapshot, trade=t, parent=tab)
    assert ed.is_bullion() and not ed.kind.isEnabled()
