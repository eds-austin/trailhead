from datetime import date
from decimal import Decimal as D

from trailhead.core.models import CashLink, CashTxnKind, Portfolio, Side, Trade
from trailhead.portfolio_types.cash.table_model import COL_INDEX, action_buttons
from trailhead.portfolio_types.stock_trading.table_model import CELL_ROLE
from trailhead.ui.dialogs.cash_account_dialogs import (
    CashAccountDialog,
    ConfirmInterestDialog,
    MoveMoneyDialog,
    RateDialog,
    TrueUpDialog,
)
from trailhead.ui.dialogs.portfolio_dialog import PortfolioDialog

TODAY = date(2026, 11, 20)


def _cash(ctx, window):
    p = ctx.repos.portfolios.create(Portfolio(None, "Cash", D(0), type="cash"))
    ctx.events.portfolios_changed.emit()
    window.show_page(f"portfolio:{p.id}")
    tab = window.current_tab()
    assert type(tab).__name__ == "CashTab"
    return p, tab


def _add(ctx, p, tab, name, balance, apy="4.00", opened=None, fund=None):
    dlg = CashAccountDialog(ctx, p, parent=tab)
    if fund:
        dlg.kind.set_value("MONEY_MARKET")
        dlg.symbol.setText(fund)
    dlg.name.setText(name)
    if apy is not None:
        if fund:
            dlg.manual.setChecked(True)
        dlg.apy.set_value(D(apy))
    dlg.balance.set_value(D(balance))
    if opened:
        dlg.opened.setDate(dlg.opened.date().addDays((opened - TODAY).days))
    assert dlg.submit(), dlg.error_label.text()
    return tab.snapshot.get(dlg.result.id)


def _row(tab, r=0):
    return {k: tab.model.index(r, i).data() for k, i in COL_INDEX.items()}


def test_new_cash_portfolio_hides_risk_and_link_fields(ctx, window):
    dlg = PortfolioDialog(ctx, parent=window)
    dlg.type.setCurrentIndex(dlg.type.findData("cash"))
    assert dlg.max_amount.isHidden() and dlg.settles.isHidden()
    dlg.name.setText("Cash")
    assert dlg.submit() and dlg.result.type == "cash"
    window.show_page(f"portfolio:{dlg.result.id}")
    assert window.header.new_trade_btn.text() == "New Account"
    assert window.header.cash_btn.text() == "Move Money" and not window.header.cash_btn.isHidden()


def test_account_interest_true_up_flow(ctx, window):
    p, tab = _cash(ctx, window)
    v = _add(ctx, p, tab, "Ally", "10000", opened=date(2026, 9, 30))
    assert v.balance == 10000 and v.apy == D("0.04")
    row = _row(tab)
    assert row["name"] == "Ally" and row["balance"] == "$10,000.00" and row["apy"] == "4.00%"
    # Oct 31 is proposed (Nov 30 hasn't come yet)
    v = tab.snapshot.get(v.id)
    (prop,) = v.proposals()
    assert prop.period_end == date(2026, 10, 31)
    cell = tab.model.index(0, COL_INDEX["action"]).data(CELL_ROLE)
    assert cell.text.startswith("Interest Oct 31") and action_buttons(cell.data)[0] == ("dividend", "Confirm interest")
    dlg = ConfirmInterestDialog(ctx, p, v, prop, parent=tab)
    dlg.amount.set_value(D("33.50"))
    assert dlg.submit()
    v = tab.snapshot.get(v.id)
    assert v.proposals() == [] and v.balance == D("10033.50")
    assert tab.kpis.cards["interest"].value_text() == "$33.50"
    # deposit, then a true-up that books the difference as interest
    mm = MoveMoneyDialog(ctx, p, tab.snapshot, v.id, parent=tab)
    mm.amount.set_value(D(500))
    assert mm.submit()
    v = tab.snapshot.get(v.id)
    assert v.balance == D("10533.50")
    tu = TrueUpDialog(ctx, p, v, parent=tab)
    tu.actual.set_value(D("10560.00"))
    assert tu.book.value() == "INTEREST"
    assert tu.submit()
    v = tab.snapshot.get(v.id)
    assert v.balance == D("10560.00") and v.last_true_up.true_up_balance == D("10560.00")
    assert v.last_true_up.kind == CashTxnKind.INTEREST and v.last_true_up.amount == D("26.50")
    # the ledger lists newest first
    tab.view.expand(tab.model.index(0, 0))
    first = tab.model.index(0, 0, tab.model.index(0, 0))
    assert first.siblingAtColumn(COL_INDEX["kind"]).data() == "✓ TRUE-UP"


def test_transfer_rate_change_and_delete(ctx, window):
    p, tab = _cash(ctx, window)
    a = _add(ctx, p, tab, "Ally", "1000")
    b = _add(ctx, p, tab, "Fidelity", "50", apy="3.65", fund="SPAXX")
    mm = MoveMoneyDialog(ctx, p, tab.snapshot, a.id, parent=tab)
    mm.kind.set_value("TRANSFER")
    mm.to.setCurrentIndex(mm.to.findData(b.id))
    mm.amount.set_value(D(200))
    assert mm.submit()
    assert tab.snapshot.get(a.id).balance == 800 and tab.snapshot.get(b.id).balance == 250
    assert tab.model.transfer_names  # the ledger names the other account
    rd = RateDialog(ctx, p, tab.snapshot.get(a.id), parent=tab)
    rd.apy.set_value(D("3.5"))
    assert rd.submit()
    assert tab.snapshot.get(a.id).apy == D("0.035")
    ctx.repos.cash_accounts.delete(a.id)
    ctx.data_changed(p.id)
    got = tab.snapshot.get(b.id)
    assert [t.kind for t in got.txns] == [CashTxnKind.OPENING, CashTxnKind.DEPOSIT]


def test_linked_stock_portfolio_settles_in_the_account(ctx, window):
    p, tab = _cash(ctx, window)
    v = _add(ctx, p, tab, "Schwab sweep", "5000")
    stock = ctx.repos.portfolios.create(Portfolio(None, "Swing", D(100000)))
    dlg = PortfolioDialog(ctx, stock, parent=window)
    assert dlg.settles.count() == 2
    dlg.settles.setCurrentIndex(dlg.settles.findData(v.id))
    dlg.since.setDate(dlg.since.date().addDays(-30))
    assert dlg.submit()
    assert ctx.repos.cash_accounts.link_for(stock.id) == CashLink(stock.id, v.id, date(2026, 10, 21))
    b = ctx.repos.trades.add(Trade(None, stock.id, "NVDA", Side.BUY, date(2026, 11, 2), D(10), D(120),
                                   initial_stop=D(110)))
    ctx.repos.trades.add(Trade(None, stock.id, "NVDA", Side.SELL, date(2026, 11, 10), D(5), D(130), lot_id=b.id))
    ctx.data_changed(stock.id)
    got = tab.snapshot.get(v.id)
    assert got.balance == D(5000) - 1200 + 650
    assert _row(tab)["linked"] == "Swing"
    window.show_page(f"portfolio:{stock.id}")
    st = window.current_tab()
    assert not st.cash_chip.isHidden() and st.cash_chip.value.text() == "$4,450.00"
    # a buy bigger than the cash left warns but doesn't block
    from trailhead.ui.dialogs.trade_dialog import low_cash_warning

    assert low_cash_warning(ctx, stock, None, D(5000)).startswith("Schwab sweep would go to −$550.00")
    assert low_cash_warning(ctx, stock, None, D(100)) is None


def test_dashboard_includes_cash(ctx, window):
    p, tab = _cash(ctx, window)
    _add(ctx, p, tab, "Ally", "2500", opened=date(2026, 9, 30))
    from trailhead.core.stats import PeriodKey, period_for
    from trailhead.portfolio_types import registry

    s = registry.get("cash").dashboard_summary(ctx, p, period_for(PeriodKey.YTD, TODAY))
    assert s.value == 2500 and s.open_risk is None
    assert any("confirm interest" in a.title for a in s.attention)
    assert any("true up" in a.title for a in s.attention)
    window.show_page("dashboard")


def test_link_and_unlink_from_the_cash_page(ctx, window):
    from trailhead.ui.dialogs.cash_account_dialogs import LinkPortfolioDialog

    p, tab = _cash(ctx, window)
    v = _add(ctx, p, tab, "Brokerage Sweep", "70000")
    stock = ctx.repos.portfolios.create(Portfolio(None, "Swing", D(100000)))
    dlg = LinkPortfolioDialog(ctx, p, tab.snapshot.get(v.id), parent=tab)
    assert dlg.target.currentData() == stock.id
    assert dlg.submit() and dlg.linked_name == "Swing"
    assert ctx.repos.cash_accounts.link_for(stock.id) == CashLink(stock.id, v.id, TODAY)
    tab.reload()
    assert _row(tab)["linked"] == "Swing"
    dlg = LinkPortfolioDialog(ctx, p, tab.snapshot.get(v.id), parent=tab)
    assert dlg.target.count() == 1 and dlg.target.currentData() is None  # nothing left to link
    dlg._unlink(stock.id)
    assert ctx.repos.cash_accounts.link_for(stock.id) is None
