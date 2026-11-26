from datetime import date
from decimal import Decimal as D

from trailhead.core.dividends import pending_dividends, shares_held
from trailhead.core.models import CashEvent, CashKind, Dividend

from .conftest import buy, sell


def test_pending_dividends():
    trades = [buy(1, "AAPL", date(2026, 5, 1), 80, 200, 190), sell(2, "AAPL", date(2026, 8, 9), 40, 210, 1)]
    divs = [Dividend("AAPL", date(2026, 5, 1), D("0.25"), "y"),   # bought ON ex-date → not eligible
            Dividend("AAPL", date(2026, 5, 11), D("0.26"), "y"),
            Dividend("AAPL", date(2026, 8, 10), D("0.27"), "y"),
            Dividend("AAPL", date(2026, 12, 1), D("0.27"), "y")]  # future
    cash = [CashEvent(1, 1, date(2026, 5, 20), CashKind.DIVIDEND, D("20.80"), "AAPL", date(2026, 5, 11))]
    assert shares_held(trades, "AAPL", date(2026, 8, 9)) == 40
    props = pending_dividends(trades, cash, divs, today=date(2026, 11, 20))
    assert [(p.ex_date, p.shares, p.amount) for p in props] == [(date(2026, 8, 10), D(40), D("10.80"))]
    assert pending_dividends(trades, cash, divs, {("AAPL", date(2026, 8, 10))}, date(2026, 11, 20)) == []
