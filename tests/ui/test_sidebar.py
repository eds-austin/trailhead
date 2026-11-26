from decimal import Decimal as D

from trailhead.core.models import Portfolio
from trailhead.ui.main_window import MainWindow
from trailhead.ui.theme import tokens


def test_sidebar_collapse_persists(ctx, window):
    ctx.repos.portfolios.create(Portfolio(None, "Swing", D(100000)))
    ctx.events.portfolios_changed.emit()
    sb = window.sidebar
    assert sb.width() == tokens.SIDEBAR_W["expanded"]
    window.actions["toggle_sidebar"].trigger()
    assert sb.is_collapsed() and sb.width() == tokens.SIDEBAR_W["collapsed"]
    btn = sb._items["portfolio:1"]
    assert btn.text() == "" and btn.toolTip() == "Swing"
    assert ctx.setting("ui.sidebar_collapsed") is True
    w2 = MainWindow(ctx)
    assert w2.sidebar.is_collapsed()
    w2.sidebar.set_collapsed(False)
    assert w2.sidebar._items["portfolio:1"].text().strip() == "Swing"
    assert ctx.setting("ui.sidebar_collapsed") is False
