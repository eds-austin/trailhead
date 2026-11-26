from datetime import date
from decimal import Decimal as D
from pathlib import Path

from trailhead.core.models import CashKind, Dividend, Portfolio, Side, Trade
from trailhead.ui.dialogs.dividend_review_dialog import DividendReviewDialog


def test_dividend_review_accept_and_dismiss(ctx, window):
    p = ctx.repos.portfolios.create(Portfolio(None, "Swing", D(100000)))
    ctx.repos.trades.add(Trade(None, p.id, "AAPL", Side.BUY, date(2026, 5, 1), D(80), D(200), initial_stop=D(190)))
    ctx.repos.market.upsert_dividends([Dividend("AAPL", date(2026, 5, 11), D("0.26"), "y", date(2026, 5, 15)),
                                       Dividend("AAPL", date(2026, 8, 10), D("0.27"), "y")])
    ctx.events.portfolios_changed.emit()
    assert window.update_pending_dividends() == 2
    dlg = DividendReviewDialog(ctx, parent=window)
    assert dlg.table.rowCount() == 2
    dlg._accept(p, dlg._items()[0][1], dlg._edits[0])
    ev = [e for e in ctx.repos.cash.list(p.id) if e.kind == CashKind.DIVIDEND]
    assert [(e.amount, e.event_date, e.dividend_ex_date) for e in ev] == [(D("20.80"), date(2026, 5, 15),
                                                                           date(2026, 5, 11))]
    dlg._dismiss(p, dlg._items()[0][1])
    assert dlg.table.rowCount() == 0
    assert window.update_pending_dividends() == 0
    tab = window.pages[f"portfolio:{p.id}"]
    assert tab.snapshot.positions[0].dividends == D("20.80")


def test_auto_backup_once_per_day(ctx, window):
    ctx.repos.portfolios.create(Portfolio(None, "Swing", D(100000)))
    ctx.events.portfolios_changed.emit()
    assert window.auto_backup()
    assert not window.auto_backup()
    folder = Path(ctx.setting("backup.dir"))
    assert len(list(folder.glob("portfolio-*.db"))) == 1
