from datetime import date
from decimal import Decimal as D

from trailhead.core.equity import PLPoint
from trailhead.core.models import CashEvent, CashKind, Portfolio
from trailhead.core.snapshot import MarketView, evaluate
from trailhead.core.stats import PeriodKey, period_for
from trailhead.portfolio_types.stock_trading.dashboard import build_summary

from .conftest import buy

TODAY = date(2026, 11, 20)


def _snap(market, cash=()):
    pf = Portfolio(1, "Swing", D(10000))
    trades = [buy(1, "AAA", date(2026, 10, 1), 10, 100, 90), buy(2, "BBB", date(2026, 10, 1), 10, 50, 45)]
    return evaluate(pf, trades, [], list(cash), market, TODAY)


def test_stop_hit_risk_and_earnings_items():
    market = {"AAA": MarketView(last=D(89), prev_close=D(92), sector="Tech"),
              "BBB": MarketView(last=D(52), prev_close=D(51), next_earnings=date(2026, 12, 1))}
    snap = _snap(market, [CashEvent(1, 1, date(2026, 11, 2), CashKind.DIVIDEND, D(4), "BBB"),
                          CashEvent(2, 1, date(2025, 11, 2), CashKind.DIVIDEND, D(9), "BBB")])
    curve = [PLPoint(TODAY, D(-90), D(0), D(-94), D(4), D(1410))]
    s = build_summary(snap, curve, period_for(PeriodKey.YTD, TODAY), open_risk_warn=D("0.01"))
    titles = [a.title for a in s.attention]
    assert titles[0] == "AAA: stop hit"
    assert any(t.startswith("Open risk") for t in titles)
    assert "BBB: earnings Dec 1" in titles
    assert not any(t.startswith("No sells") for t in titles)
    assert s.income == D(4)  # only this year's dividend
    assert s.period_pl == D(-90) and s.value == D(1410)
    assert s.day_change == D(-30 + 10)
    assert {h.symbol: h.sector for h in s.holdings} == {"AAA": "Tech", "BBB": None}
    assert s.subtitle == "2 open positions"


def test_on_plan_item_when_nothing_due():
    market = {"AAA": MarketView(last=D(101), prev_close=D(100)), "BBB": MarketView(last=D(51), prev_close=D(50))}
    s = build_summary(_snap(market), [], period_for(PeriodKey.YTD, TODAY), open_risk_warn=D("0.5"))
    assert [a.title for a in s.attention] == ["No sells or stop raises due"]
    assert s.period_pl == 0 and not s.open_risk_warn
