from datetime import date
from decimal import Decimal as D

from trailhead.alerts.engine import AlertKind, AlertSettings, date_level, evaluate
from trailhead.core.models import Portfolio, Side, StopChange, StopReason, Trade
from trailhead.core.snapshot import MarketView
from trailhead.core.snapshot import evaluate as snapshot

PF = Portfolio(1, "P", D(100000))
TODAY = date(2026, 11, 20)


def _snap(last, high=None, stops=(), earnings=None):
    trades = [Trade(1, 1, "NVDA", Side.BUY, date(2026, 10, 2), D(100), D(120), initial_stop=D(114))]
    highs = [(date(2026, 10, 5), D(high))] if high else []
    mv = {"NVDA": MarketView(last=D(last), prev_close=D(last), highs=highs, next_earnings=earnings)}
    return snapshot(PF, trades, list(stops), [], mv, TODAY)


def kinds(alerts):
    return [a.kind for a in alerts]


def test_stop_hit():
    a = evaluate(_snap("113.90"))
    assert kinds(a) == [AlertKind.STOP_HIT]
    assert a[0].level == D(114) and a[0].title == "NVDA — Stop hit"
    assert "Sell remaining 100 sh" in a[0].message


def test_near_stop():
    assert kinds(evaluate(_snap("116"))) == [AlertKind.NEAR_STOP]
    assert evaluate(_snap("116"), AlertSettings(near_stop_pct=D("0.01"))) == []


def test_target_reached_until_acted_on():
    a = evaluate(_snap("126.50", high="126.80"))
    assert kinds(a) == [AlertKind.TARGET_REACHED]
    assert a[0].level == D(126)
    confirmed = [StopChange(1, 1, TODAY, D(117), StopReason.LADDER_CONFIRMED)]
    # stop raised but tranche not sold yet → still pending
    assert kinds(evaluate(_snap("126.50", high="126.80", stops=confirmed))) == [AlertKind.TARGET_REACHED]


def test_earnings_soon():
    a = evaluate(_snap("125", earnings=date(2026, 11, 24)))
    assert kinds(a) == [AlertKind.EARNINGS_SOON]
    assert a[0].level == date_level(date(2026, 11, 24)) == D(20261124)
    assert evaluate(_snap("125", earnings=date(2026, 12, 24))) == []
