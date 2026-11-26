from datetime import date
from decimal import Decimal as D

from trailhead.core.models import Portfolio, Side, Trade


def _setup(ctx):
    p = ctx.repos.portfolios.create(Portfolio(None, "Swing", D(50000)))
    a = ctx.repos.trades.add(Trade(None, p.id, "AAA", Side.BUY, date(2026, 2, 2), D(10), D(100), initial_stop=D(90),
                                   strategy="Pullback", timeframe="Weekly/Daily"))
    ctx.repos.trades.add(Trade(None, p.id, "AAA", Side.SELL, date(2026, 2, 12), D(10), D(120), lot_id=a.id))
    b = ctx.repos.trades.add(Trade(None, p.id, "BBB", Side.BUY, date(2026, 3, 2), D(10), D(100), initial_stop=D(90),
                                   strategy="Breakout", exit_plan="1-2-3"))
    ctx.repos.trades.add(Trade(None, p.id, "BBB", Side.SELL, date(2026, 3, 9), D(10), D(95), lot_id=b.id))
    ctx.repos.trades.add(Trade(None, p.id, "CCC", Side.BUY, date(2026, 10, 1), D(10), D(50), initial_stop=D(45),
                               strategy="Pullback"))
    ctx.events.portfolios_changed.emit()
    return p


def test_performance_view_groups_selects_and_exports(ctx, window, tmp_path):
    p = _setup(ctx)
    window.show_page(f"portfolio:{p.id}")
    tab = window.current_tab()
    tab.view_mode.set_value("performance")
    assert tab.table_stack.currentWidget() is tab.perf
    assert not tab.search.isVisibleTo(tab) and tab.group_by.isVisibleTo(tab)
    labels = [tab.perf.item(r, 0).data(0x0100).label for r in range(tab.perf.rowCount())]
    assert labels == ["Pullback", "Breakout", "All strategies"]  # 1 closed each; ties → more open first

    tab.perf.selectRow(0)
    assert tab.hist_title.text() == "R distribution · Pullback"
    assert tab.hist_sub.text() == "1 closed lot · E +2.00R"

    tab.group_by.set_value("exit_plan")
    assert [tab.perf.item(r, 0).text() for r in range(tab.perf.rowCount())] == ["1-3-6", "1-2-3", "All exit plans"]
    assert tab.hist_title.text() == "R distribution"  # "Pullback" is not an exit plan: selection cleared

    out = tmp_path / "perf.csv"
    assert tab.export_csv(out) == 3
    text = out.read_text()
    assert text.splitlines()[0].startswith("Exit plan,Closed,Open,Win %")
    assert "1-2-3 LOW SAMPLE" in text

    tab.view_mode.set_value("positions")
    assert tab.table_stack.currentWidget() is tab.view
    assert tab.hist_title.text() == "R distribution"
    tab.view_mode.set_value("performance")
    assert ctx.repos.settings.get(f"ui.tab.{p.id}")["view"] == "performance"
    assert ctx.repos.settings.get(f"ui.tab.{p.id}")["group"] == "exit_plan"


def test_header_click_sorts_and_keeps_total_last(ctx, window):
    p = _setup(ctx)
    window.show_page(f"portfolio:{p.id}")
    tab = window.current_tab()
    tab.view_mode.set_value("performance")
    perf = tab.perf
    perf.horizontalHeader().sectionClicked.emit(7)  # Expectancy, high → low
    names = [perf.item(r, 0).text() for r in range(perf.rowCount())]
    assert names == ["Pullback", "Breakout", "All strategies"]
    assert perf.horizontalHeaderItem(7).text() == "Expectancy ▾"
    perf.horizontalHeader().sectionClicked.emit(7)
    assert [perf.item(r, 0).text() for r in range(perf.rowCount())][:2] == ["Breakout", "Pullback"]
