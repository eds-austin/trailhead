from datetime import date
from decimal import Decimal

import pytest

from trailhead.core.models import CashEvent, CashKind, Portfolio, Side, Trade

D = Decimal


def buy(tid, symbol, d, shares, price, stop, pid=1, **kw):
    return Trade(tid, pid, symbol, Side.BUY, d, D(shares), D(price), initial_stop=D(stop), **kw)


def sell(tid, symbol, d, shares, price, lot_id, pid=1):
    return Trade(tid, pid, symbol, Side.SELL, d, D(shares), D(price), lot_id=lot_id)


@pytest.fixture
def golden():
    """PLAN §6.5: two W lots bought 2016-07-29, sold 08-02 and 08-04."""
    pf = Portfolio(1, "Golden", D("100000"), D("0.01"))
    trades = [
        buy(1, "W", date(2016, 7, 29), 50, "43.18", "40.26", timeframe="Weekly/Daily"),
        buy(2, "W", date(2016, 7, 29), 50, "43.18", "40.26", timeframe="Weekly/Daily"),
        sell(3, "W", date(2016, 8, 2), 50, "46.24", 1),
        sell(4, "W", date(2016, 8, 4), 50, "49.01", 2),
    ]
    cash = [CashEvent(1, 1, date(2016, 7, 29), CashKind.DEPOSIT, D("100000"))]
    return pf, trades, cash
