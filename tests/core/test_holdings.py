from datetime import date
from decimal import Decimal as D

from trailhead.core.holdings import evaluate_holdings, next_dividend, trailing_per_share
from trailhead.core.models import CashEvent, CashKind, Dividend, Portfolio, Side, Trade
from trailhead.core.snapshot import MarketView
from trailhead.core.stats import PeriodKey, period_for
from trailhead.portfolio_types.long_term.dashboard import build_summary

TODAY = date(2026, 11, 20)
PF = Portfolio(1, "Hold", D(0), type="long_term")


def b(tid, sym, d, n, px, **kw):
    return Trade(tid, 1, sym, Side.BUY, d, D(n), D(px), **kw)


def s(tid, sym, d, n, px, lot):
    return Trade(tid, 1, sym, Side.SELL, d, D(n), D(px), lot_id=lot)


def div(sym, d, amt, pay=None):
    return Dividend(sym, d, D(amt), "y", pay)


QUARTERLY = [div("KO", date(2025, 11, 28), "0.51"), div("KO", date(2026, 3, 13), "0.51"),
             div("KO", date(2026, 6, 13), "0.51"), div("KO", date(2026, 9, 13), "0.51")]


def _snap(trades, cash=(), market=None, history=None):
    market = market or {"KO": MarketView(last=D(70), prev_close=D(69))}
    return evaluate_holdings(PF, trades, list(cash), market, history or {}, TODAY)


def test_total_return_counts_dividends_and_drip_without_double_counting():
    trades = [b(1, "KO", date(2025, 1, 2), 100, 60, asset_type="Income"),
              # a $102 dividend reinvested: 1.5 sh @ 68
              b(2, "KO", date(2026, 4, 1), "1.5", 68, asset_type="Income", drip=True)]
    cash = [CashEvent(1, 1, date(2026, 4, 1), CashKind.DIVIDEND, D(102), "KO", date(2026, 3, 13))]
    h = _snap(trades, cash).holdings[0]
    assert h.invested == D(6000) and h.reinvested == D(102)
    assert h.cost_basis == D(6102)
    assert h.market_value == D("101.5") * 70  # 7105
    assert h.unrealized_pl == D(7105) - D(6102)
    # total return = market value − money invested (the dividend bought shares; the DRIP cost is not new money)
    assert h.total_return == D(7105) - D(6000) == D(1105)
    assert h.total_return_pct == D(1105) / D(6000)
    assert h.asset_type == "Income"


def test_realized_and_closed_holding():
    trades = [b(1, "KO", date(2025, 1, 2), 100, 60), s(2, "KO", date(2026, 2, 1), 100, 66, 1)]
    cash = [CashEvent(1, 1, date(2025, 6, 1), CashKind.DIVIDEND, D(48), "KO", date(2025, 5, 30))]
    h = _snap(trades, cash).holdings[0]
    assert h.is_closed and h.status == "CLOSED"
    assert h.realized_pl == D(600) and h.dividends == D(48)
    assert h.total_return == D(648) and h.market_value == 0 and h.est_income is None


def test_income_yield_on_cost_and_trailing_rate():
    trades = [b(1, "KO", date(2025, 1, 2), 100, 50)]
    # no provider rate → trailing 12 months of payments (ex-dates within the last 365 days): 4 × 0.51
    assert trailing_per_share(QUARTERLY, TODAY) == D("2.04")
    h = _snap(trades, history={"KO": QUARTERLY}).holdings[0]
    assert h.annual_per_share == D("2.04")
    assert h.est_income == D(204)
    assert h.yield_on_cost == D(204) / D(5000)
    assert h.div_yield == D("2.04") / D(70)
    # the provider's forward rate wins when known
    m = {"KO": MarketView(last=D(70), annual_dividend=D("2.12"))}
    assert _snap(trades, market=m, history={"KO": QUARTERLY}).holdings[0].est_income == D(212)


def test_next_dividend_announced_then_estimated():
    m = MarketView(last=D(70), ex_div_date=date(2026, 11, 28), div_pay_date=date(2026, 12, 15))
    nd = next_dividend(m, QUARTERLY, TODAY)
    assert (nd.ex_date, nd.pay_date, nd.estimated) == (date(2026, 11, 28), date(2026, 12, 15), False)
    # ex-date passed, payment still ahead
    m2 = MarketView(last=D(70), ex_div_date=date(2026, 11, 14), div_pay_date=date(2026, 12, 1))
    nd2 = next_dividend(m2, QUARTERLY, TODAY)
    assert nd2.ex_date is None and nd2.date == date(2026, 12, 1)
    # nothing announced: project from the quarterly schedule (last ex 2026-09-13 + ~92 days)
    nd3 = next_dividend(MarketView(last=D(70)), QUARTERLY, TODAY)
    assert nd3.estimated and date(2026, 12, 10) <= nd3.ex_date <= date(2026, 12, 16)
    assert next_dividend(MarketView(), [], TODAY) is None


def test_optional_stop_and_target_status():
    trades = [b(1, "GLD", date(2025, 1, 2), 10, 180, initial_stop=D(170), target_price=D(250))]
    m = {"GLD": MarketView(last=D(255))}
    h = _snap(trades, market=m).holdings[0]
    assert h.stop == D(170) and h.target == D(250)
    assert h.target_hit and not h.stop_hit and h.status == "TARGET"
    h2 = _snap([b(1, "GLD", date(2025, 1, 2), 10, 180)], market=m).holdings[0]
    assert h2.stop is None and h2.target is None and h2.status == "OPEN"


def test_allocation_and_dashboard_summary():
    trades = [b(1, "KO", date(2025, 1, 2), 100, 60, asset_type="Income"),
              b(2, "GLD", date(2025, 1, 2), 10, 180, asset_type="Precious metals", initial_stop=D(200))]
    m = {"KO": MarketView(last=D(70), prev_close=D(69)), "GLD": MarketView(last=D(190), prev_close=D(188))}
    snap = _snap(trades, market=m)
    assert snap.allocation_by_type() == [("Income", D(7000)), ("Precious metals", D(1900))]
    s_ = build_summary(snap, [], period_for(PeriodKey.YTD, TODAY))
    assert s_.value == D(8900) and s_.open_risk is None
    assert s_.attention[0].title == "GLD: stop hit"
    # ETFs and metals have no sector: the Dashboard's allocation falls back to the investment type
    assert {h.symbol: h.sector for h in s_.holdings} == {"KO": "Income", "GLD": "Precious metals"}


def test_period_return_simple_dietz():
    from trailhead.core.equity import PLPoint
    from trailhead.core.holdings import period_return

    z = D(0)
    pts = [PLPoint(date(2025, 12, 31), D(1000), z, D(1000), z, D(11000)),  # value 11,000 at the start of 2026
           PLPoint(date(2026, 6, 30), D(1500), z, D(1500), z, D(16500)),
           PLPoint(date(2026, 11, 20), D(2100), D(100), D(1900), D(100), D(16000))]
    trades = [b(1, "KO", date(2025, 1, 2), 100, 100),
              b(2, "KO", date(2026, 3, 2), 40, 100),  # $4,000 added in the period
              b(3, "KO", date(2026, 4, 1), "0.5", 100, drip=True),  # a reinvested dividend is not new money
              s(4, "KO", date(2026, 9, 1), 10, 110, 1)]  # $1,100 taken out
    pr = period_return(pts, trades, date(2026, 1, 1))
    assert pr.pl == D(1100) and pr.start_value == D(11000) and pr.net_added == D(2900)
    assert pr.pct == D(1100) / (D(11000) + D(1450))
    assert period_return(pts, trades, date(2027, 1, 1)) is None
