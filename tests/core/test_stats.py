from datetime import date
from decimal import Decimal as D

from trailhead.core.positions import build_lots, build_positions
from trailhead.core.snapshot import MarketView, evaluate
from trailhead.core.stats import (
    Period,
    PeriodKey,
    closed_lot_units,
    closed_position_units,
    period_for,
    r_histogram,
    realized_in_period,
    trade_stats,
)

from .conftest import buy, sell


def r2(v):
    return round(v, 2)


def test_golden_stats(golden):
    _, trades, _ = golden
    lots = build_lots(trades)
    s = trade_stats(closed_lot_units(lots))
    assert (s.wins, s.losses) == (2, 0)
    assert s.win_pct == 1
    assert s.profit_factor is None  # shown as "—"
    assert r2(s.expectancy) == D("1.52")
    assert r2(s.stdev) == D("0.67")
    assert r2(s.r_over_stdev) == D("2.27")
    pos = trade_stats(closed_position_units(build_positions(lots)))
    assert pos.n == 1 and r2(pos.expectancy) == D("1.52") and pos.stdev is None


def test_golden_snapshot(golden):
    pf, trades, cash = golden
    snap = evaluate(pf, trades, [], cash, {}, date(2016, 9, 1))
    assert snap.value == 0  # closed position, no cash balance in a stock-trading portfolio
    assert snap.deployed == 0
    assert snap.open_risk == 0
    assert snap.positions[0].status == "CLOSED"


def test_no_trades():
    s = trade_stats([])
    assert s.n == 0 and s.win_pct is None and s.expectancy is None and s.stdev is None
    assert s.profit_factor is None and s.r_over_stdev is None


def test_single_trade_and_loss():
    lots = build_lots([buy(1, "X", date(2024, 1, 2), 10, 50, 45), sell(2, "X", date(2024, 1, 3), 10, 45, 1)])
    s = trade_stats(closed_lot_units(lots))
    assert s.losses == 1 and s.win_pct == 0 and s.expectancy == -1 and s.stdev is None


def test_profit_factor_and_zero_pl():
    lots = build_lots([
        buy(1, "A", date(2024, 1, 2), 10, 50, 45), sell(2, "A", date(2024, 1, 3), 10, 60, 1),
        buy(3, "B", date(2024, 1, 2), 10, 50, 45), sell(4, "B", date(2024, 1, 3), 10, 45, 3),
        buy(5, "C", date(2024, 1, 2), 10, 50, 45), sell(6, "C", date(2024, 1, 3), 10, 50, 5),
    ])
    s = trade_stats(closed_lot_units(lots))
    assert (s.wins, s.losses, s.n) == (1, 1, 3)
    assert s.win_pct == D("0.5")
    assert s.profit_factor == 2


def test_period_filters():
    today = date(2026, 11, 19)
    assert period_for(PeriodKey.MTD, today) == Period(date(2026, 11, 1), today)
    assert period_for(PeriodKey.QTD, today) == Period(date(2026, 10, 1), today)
    assert period_for(PeriodKey.YTD, today) == Period(date(2026, 1, 1), today)
    assert period_for(PeriodKey.L12M, today) == Period(date(2025, 11, 20), today)
    assert period_for(PeriodKey.ALL, today) == Period()


def test_realized_by_sell_date(golden):
    _, trades, _ = golden
    lots = build_lots(trades)
    total, n = realized_in_period(lots, Period(date(2016, 8, 3), date(2016, 8, 31)))
    assert (total, n) == (D("291.50"), 1)
    units = closed_lot_units(lots, Period(date(2016, 8, 3), None))
    assert len(units) == 1


def test_open_position_metrics():
    from trailhead.core.models import CashEvent, CashKind, Portfolio

    pf = Portfolio(1, "P", D(100000))
    trades = [buy(1, "NVDA", date(2026, 10, 2), 100, "120", "114")]
    cash = [CashEvent(1, 1, date(2026, 10, 1), CashKind.DEPOSIT, D(50000))]
    mv = {"NVDA": MarketView(last=D("127"), prev_close=D("125"),
                             highs=[(date(2026, 10, 5), D("126.20"))])}
    snap = evaluate(pf, trades, [], cash, mv, date(2026, 11, 19))
    p = snap.positions[0]
    assert snap.value == snap.market_value == D(12700)  # positions only; deposits are ignored
    assert p.pct_of_portfolio == 1
    assert snap.deployed == D(12000)
    assert p.unrealized_pl == D(700)
    assert r2(p.current_r) == D("1.17")
    assert p.open_risk == D(1300)
    assert p.day_change == D(200)
    assert p.next_target == D(132)  # reached M1 (126) → next 2R
    assert [a.kind.value for a in p.actions] == ["SELL_TRANCHE", "RAISE_STOP"]
    assert p.stop_distance[0] == (D(114) - D(127)) / D(127)
    assert p.status == "OPEN"
    assert p.days_held == 48


def test_histogram():
    h = r_histogram([D("1.05"), D("2.00"), D("-0.3"), D("-1")])
    assert h == {D("-1.0"): 1, D("-0.5"): 1, D("1.0"): 1, D("2.0"): 1}


def test_sqn_caps_n_and_rates():
    from trailhead.core.stats import TradeUnit, sqn_rating

    units = [TradeUnit(D(1), D(r), None) for r in ("2", "-1")]
    s = trade_stats(units)  # E 0.5, stdev √4.5
    assert r2(s.sqn) == r2(D(2).sqrt() * D("0.5") / D("4.5").sqrt())
    many = trade_stats(units * 100)  # 200 trades: N counts as 100
    assert r2(many.sqn) == r2(10 * many.expectancy / many.stdev)
    assert trade_stats(units[:1]).sqn is None  # no stdev from one trade
    assert [sqn_rating(D(v)) for v in ("-0.1", "0", "1.6", "2", "2.5", "3", "5", "7")] == [
        "Losing", "Poor", "Below avg", "Average", "Good", "Excellent", "Superb", "Holy grail"]
    assert sqn_rating(None) is None
