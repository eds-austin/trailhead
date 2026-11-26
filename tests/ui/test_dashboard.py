from datetime import date
from decimal import Decimal as D

from trailhead.core.models import CashEvent, CashKind, Portfolio, Side, Trade
from trailhead.ui.dashboard import C_NAME, C_VALUE, PID_ROLE, TOTAL_ROLE


def _two_portfolios(ctx):
    p = ctx.repos.portfolios.create(Portfolio(None, "Swing", D(100000)))
    ctx.repos.cash.add(CashEvent(None, p.id, date(2026, 1, 2), CashKind.DEPOSIT, D(100000)))
    b = ctx.repos.trades.add(Trade(None, p.id, "W", Side.BUY, date(2026, 2, 2), D(50), D("43.18"),
                                   initial_stop=D("40.26")))
    ctx.repos.trades.add(Trade(None, p.id, "W", Side.SELL, date(2026, 2, 9), D(50), D("46.24"), lot_id=b.id))
    ctx.repos.trades.add(Trade(None, p.id, "NVDA", Side.BUY, date(2026, 10, 2), D(10), D(120), initial_stop=D(114)))
    q = ctx.repos.portfolios.create(Portfolio(None, "Second", D(20000)))
    ctx.repos.trades.add(Trade(None, q.id, "AAPL", Side.BUY, date(2026, 9, 1), D(5), D(200), initial_stop=D(190)))
    ctx.events.portfolios_changed.emit()
    return p, q


def test_dashboard_table_totals_and_open(ctx, window):
    p, q = _two_portfolios(ctx)
    window.show_page("dashboard")
    dash = window.dashboard
    dash.reload()
    t = dash.table
    assert t.rowCount() == 3  # two portfolios + Total
    assert [t.item(r, C_NAME).data(PID_ROLE) for r in range(2)] == [p.id, q.id]
    assert t.item(2, C_NAME).text() == "Total" and t.item(2, C_VALUE).data(TOTAL_ROLE)
    # no quotes in tests: open positions are valued at cost (NVDA 1,200 + AAPL 1,000)
    assert t.item(2, C_VALUE).text() == "$2,200.00"
    assert dash.total_caption.text().startswith("Total value · 2 portfolios")
    assert dash.total_pl.text() == "+$153.00"  # lot W realized in YTD
    assert len(dash.chart.others) == 2 and dash.legend.isVisibleTo(dash)
    dash.alloc_mode.set_value("sector")
    assert "Swing, Second" in dash.alloc_sub.text()

    dash.period.set_value("MTD")  # nothing realized in November
    assert dash.total_pl.text() in ("$0.00", "+$0.00")

    t.activated.emit(t.model().index(1, 0))
    assert window.current_page_key() == f"portfolio:{q.id}"


def test_dashboard_single_portfolio_has_no_total_row(ctx, window):
    p = ctx.repos.portfolios.create(Portfolio(None, "Only", D(50000)))
    ctx.repos.trades.add(Trade(None, p.id, "NVDA", Side.BUY, date(2026, 10, 2), D(10), D(120), initial_stop=D(114)))
    ctx.events.portfolios_changed.emit()
    window.show_page("dashboard")
    dash = window.dashboard
    dash.reload()
    assert dash.table.rowCount() == 1
    assert not dash.legend.isVisibleTo(dash)


def test_position_detail_still_opens(ctx, window):
    p, _q = _two_portfolios(ctx)
    window.show_page(f"portfolio:{p.id}")
    tab = window.current_tab()
    # R distribution beside the KPI strip: lot W closed at 1.05R this year
    assert tab.hist_sub.text() == "1 closed lot · E +1.05R"
    assert tab.hist.bars.opts["height"] == [1.0]
    tab.unit.set_value("position")
    assert tab.hist_sub.text() == "1 closed position · E +1.05R"
    tab.period.setCurrentIndex(tab.period.findData("MTD"))
    assert tab.hist_sub.text() == "" and tab.hist.empty_text.isVisible()
    idx = tab.model.index(tab.model.position_keys().index("NVDA#0"), 0)
    tab.view.setCurrentIndex(idx)
    tab._on_position_activated(idx)
    assert tab.detail.title.text() == "NVDA"
    assert tab.splitter.sizes()[1] > 0
