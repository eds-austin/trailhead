from datetime import date
from decimal import Decimal as D

from trailhead.core.equity import pl_curve
from trailhead.core.models import CashEvent, CashKind, Portfolio
from trailhead.core.snapshot import MarketView, evaluate

from .conftest import buy, sell


def test_pl_curve_golden(golden):
    _, trades, cash = golden  # the 100,000 deposit is ignored
    closes = {"W": {date(2016, 7, 29): D("44"), date(2016, 8, 1): D("45"), date(2016, 8, 3): D("48")}}
    pts = pl_curve(trades, cash, closes, date(2016, 8, 5))
    by = {p.date: p for p in pts}
    assert by[date(2016, 7, 29)].total == 100 * (D(44) - D("43.18"))
    assert by[date(2016, 8, 2)].realized == D("153.00")
    assert by[date(2016, 8, 2)].unrealized == 50 * (D(45) - D("43.18"))
    assert by[date(2016, 8, 5)].total == D("444.50")
    assert by[date(2016, 8, 5)].market_value == 0
    assert date(2016, 7, 30) not in by  # weekend skipped


def test_pl_curve_dividends_last_price_and_period_rebase():
    trades = [buy(1, "A", date(2024, 1, 2), 10, 10, 9), sell(2, "A", date(2024, 1, 3), 5, 12, 1)]
    cash = [CashEvent(1, 1, date(2024, 1, 4), CashKind.DIVIDEND, D("1.50"), "A"),
            CashEvent(2, 1, date(2024, 1, 1), CashKind.DEPOSIT, D(1000))]
    pts = pl_curve(trades, cash, {}, date(2024, 1, 4), last_prices={"A": D(20)})
    last = pts[-1]
    assert (last.realized, last.unrealized, last.dividends) == (D(10), D(50), D("1.50"))
    assert last.total == D("61.50")
    rebased = pl_curve(trades, cash, {}, date(2024, 1, 4), start=date(2024, 1, 4), last_prices={"A": D(20)})
    assert [p.date for p in rebased] == [date(2024, 1, 4)]
    assert rebased[0].total == D("61.50") - D(10)  # realized on 1/3 is before the period


def test_empty():
    assert pl_curve([], [], {}, date(2024, 1, 1)) == []


def test_value_is_positions_only_and_pct_sums_to_100():
    trades = [buy(4, "EOG", date(2026, 8, 20), 32, "152.17", 145),
              buy(1, "XNCR", date(2026, 10, 22), 100, "25.63", "20.63"),
              sell(2, "XNCR", date(2026, 10, 26), 25, "31.63", 1),
              buy(3, "CVX", date(2026, 11, 12), 24, "217.37", 210)]
    mv = {"XNCR": MarketView(last=D("33.26")), "EOG": MarketView(last=D("148.60")),
          "CVX": MarketView(last=D("208.37"))}
    snap = evaluate(Portfolio(1, "P", D(100000)), trades, [], [], mv, date(2026, 11, 20))
    assert snap.value == D("2494.50") + D("4755.20") + D("5000.88")
    assert sum(p.pct_of_portfolio for p in snap.open_positions) == 1


def test_spinoff_moves_cost_without_a_jump():
    from datetime import date
    from decimal import Decimal as D

    from trailhead.core.equity import pl_curve
    from trailhead.core.models import Side, Trade
    from trailhead.core.spinoffs import BasisCut

    trades = [Trade(1, 1, "P", Side.BUY, date(2026, 6, 1), D(10), D(100)),
              Trade(2, 1, "C", Side.BUY, date(2026, 6, 3), D(10), D(20), spinoff_of="P")]
    closes = {"P": {date(2026, 6, 1): D(100), date(2026, 6, 2): D(100), date(2026, 6, 3): D(80)},
              "C": {date(2026, 6, 3): D(20)}}
    cut = BasisCut(1, "P", date(2026, 6, 3), D(20), D(10))
    pts = {p.date: p for p in pl_curve(trades, [], closes, date(2026, 6, 3), basis_cuts=[cut])}
    assert pts[date(2026, 6, 3)].total == 0 and pts[date(2026, 6, 3)].market_value == D(1000)
    assert {p.date: p for p in pl_curve(trades, [], closes, date(2026, 6, 3))}[date(2026, 6, 3)].total == -200
