from datetime import date, datetime
from decimal import Decimal as D

import pytest

from trailhead.core.models import (
    AlertRecord,
    Bar,
    CashEvent,
    CashKind,
    Dividend,
    Instrument,
    Portfolio,
    Quote,
    Side,
    StopChange,
    StopReason,
    Trade,
)
from trailhead.data.repo import RepoError


def _pf(repos):
    return repos.portfolios.create(Portfolio(None, "Swing", D("100000"), D("0.01")))


def test_portfolio_roundtrip(repos):
    p = _pf(repos)
    assert p.id and p.sort_order == 0
    got = repos.portfolios.get(p.id)
    assert got.max_amount == D("100000") and got.risk_pct == D("0.01") and got.name == "Swing"
    repos.portfolios.update(got.__class__(**{**got.__dict__, "name": "Swing 2", "archived": True}))
    assert repos.portfolios.list() == []
    assert repos.portfolios.list(include_archived=True)[0].name == "Swing 2"


def test_trade_crud_and_rules(repos):
    p = _pf(repos)
    b = repos.trades.add(Trade(None, p.id, "w", Side.BUY, date(2016, 7, 29), D(50), D("43.18"),
                               initial_stop=D("40.26"), timeframe="Weekly/Daily"))
    assert b.id and b.symbol == "W"
    got = repos.trades.get(b.id)
    assert got.price == D("43.18") and got.initial_stop == D("40.26") and got.trade_date == date(2016, 7, 29)
    with pytest.raises(RepoError):  # stop above price
        repos.trades.add(Trade(None, p.id, "X", Side.BUY, date(2020, 1, 1), D(1), D(10), initial_stop=D(11)))
    with pytest.raises(RepoError):  # oversell
        repos.trades.add(Trade(None, p.id, "W", Side.SELL, date(2016, 8, 2), D(51), D("46.24"), lot_id=b.id))
    s1, s2 = repos.trades.add_many([
        Trade(None, p.id, "W", Side.SELL, date(2016, 8, 2), D(20), D("46.24"), lot_id=b.id),
        Trade(None, p.id, "W", Side.SELL, date(2016, 8, 3), D(30), D("47"), lot_id=b.id),
    ])
    with pytest.raises(RepoError):  # BUY with sells cannot be deleted
        repos.trades.delete(b.id)
    with pytest.raises(RepoError):  # cannot shrink lot below sold
        repos.trades.update(Trade(b.id, p.id, "W", Side.BUY, b.trade_date, D(40), b.price, initial_stop=b.initial_stop))
    repos.trades.update(Trade(s2.id, p.id, "W", Side.SELL, date(2016, 8, 4), D(30), D("49.01"), lot_id=b.id))
    assert repos.trades.get(s2.id).price == D("49.01")
    repos.trades.delete(s1.id)
    repos.trades.delete(s2.id)
    repos.trades.delete(b.id)
    assert repos.trades.list(p.id) == []


def test_atomic_add_many(repos):
    p = _pf(repos)
    b = repos.trades.add(Trade(None, p.id, "A", Side.BUY, date(2024, 1, 1), D(10), D(10), initial_stop=D(9)))
    with pytest.raises(RepoError):
        repos.trades.add_many([
            Trade(None, p.id, "A", Side.SELL, date(2024, 1, 2), D(5), D(11), lot_id=b.id),
            Trade(None, p.id, "A", Side.SELL, date(2024, 1, 2), D(50), D(11), lot_id=b.id),
        ])
    assert len(repos.trades.list(p.id)) == 1


def test_stops_cash(repos):
    p = _pf(repos)
    b = repos.trades.add(Trade(None, p.id, "A", Side.BUY, date(2024, 1, 1), D(10), D(10), initial_stop=D(9)))
    s = repos.stops.add(StopChange(None, b.id, date(2024, 1, 5), D("9.5"), StopReason.LADDER_CONFIRMED))
    assert repos.stops.list_for_portfolio(p.id) == [s]
    w = repos.cash.add(CashEvent(None, p.id, date(2024, 1, 1), CashKind.WITHDRAWAL, D(100)))
    assert w.amount == D(-100)
    with pytest.raises(RepoError):
        repos.cash.add(CashEvent(None, p.id, date(2024, 1, 1), CashKind.DEPOSIT, D(-5)))
    assert [e.amount for e in repos.cash.list(p.id)] == [D(-100)]


def test_market_repo(repos):
    ts = datetime(2026, 11, 19, 16, 0)
    repos.market.upsert_quote(Quote("NVDA", D("127.40"), D("125"), ts, "yfinance"))
    assert repos.market.get_quote("NVDA").last == D("127.40")
    repos.market.upsert_quote(Quote("NVDA", D("118"), None, ts, "manual", is_manual=True, pinned=True))
    assert not repos.market.upsert_quote(Quote("NVDA", D("130"), D("129"), ts, "yfinance"))
    assert repos.market.get_quote("NVDA").last == D("118")
    repos.market.clear_manual("NVDA")
    assert repos.market.upsert_quote(Quote("NVDA", D("130"), D("129"), ts, "yfinance"))
    repos.market.upsert_instrument(Instrument("NVDA", "NVIDIA", "Technology", next_earnings_date=date(2026, 11, 24)))
    repos.market.upsert_instrument(Instrument("NVDA", None, None, next_earnings_date=date(2026, 11, 25)))
    i = repos.market.get_instruments(["NVDA"])["NVDA"]
    assert (i.name, i.sector, i.next_earnings_date) == ("NVIDIA", "Technology", date(2026, 11, 25))
    repos.market.upsert_bars("NVDA", [Bar(date(2026, 11, 18), D(1), D(2), D("0.5"), D("1.5"), 100),
                                      Bar(date(2026, 11, 19), D(1), D(3), D("0.5"), D("1.5"), 100)])
    assert repos.market.last_bar_date("NVDA") == date(2026, 11, 19)
    assert repos.market.highs(["NVDA"], since=date(2026, 11, 19)) == {"NVDA": [(date(2026, 11, 19), D(3))]}
    repos.market.upsert_dividends([Dividend("NVDA", date(2026, 9, 1), D("0.01"), "yfinance")])
    assert repos.market.dividends(["NVDA"])[0].amount_per_share == D("0.01")


def test_alert_dedup(repos):
    p = _pf(repos)
    a = AlertRecord(None, p.id, "NVDA", None, "EARNINGS_SOON", D(20261124), "Earnings 11/24", datetime.now())
    assert repos.alerts.insert_if_new(a) is not None
    assert repos.alerts.insert_if_new(a) is None  # NULL lot_id still deduplicated
    assert repos.alerts.count_unacked(p.id) == 1
    repos.alerts.acknowledge_all(p.id)
    assert repos.alerts.count_unacked() == 0


def test_settings(repos):
    assert repos.settings.get("backup.keep") == 30
    repos.settings.set("backup.keep", 10)
    assert repos.settings.get("backup.keep") == 10
    repos.settings.set("ui.column_state.1", {"a": [1, 2]})
    assert repos.settings.get("ui.column_state.1") == {"a": [1, 2]}
    assert repos.settings.get("missing", "x") == "x"


def test_long_term_buys_need_no_stop_and_keep_type_target_drip(repos):
    from trailhead.data.repo import RepoError

    p = repos.portfolios.create(Portfolio(None, "Hold", D(0), type="long_term"))
    t = repos.trades.add(Trade(None, p.id, "gld", Side.BUY, date(2026, 1, 5), D("2.5"), D(180),
                               asset_type="Precious metals", target_price=D(250)))
    got = repos.trades.get(t.id)
    assert (got.symbol, got.initial_stop, got.asset_type, got.target_price, got.drip) == (
        "GLD", None, "Precious metals", D(250), False)
    # a long-term stop may sit above the purchase price (trailing a winner)
    repos.trades.set_levels([t.id], D(200), None)
    got = repos.trades.get(t.id)
    assert (got.initial_stop, got.target_price) == (D(200), None)
    d = repos.trades.add(Trade(None, p.id, "GLD", Side.BUY, date(2026, 2, 5), D("0.1"), D(190), drip=True))
    assert repos.trades.get(d.id).drip is True
    with pytest.raises(RepoError):
        repos.trades.add(Trade(None, p.id, "GLD", Side.BUY, date(2026, 2, 5), D(1), D(190), target_price=D(-1)))
    # stock trading still requires a stop below the price
    st = repos.portfolios.create(Portfolio(None, "Swing", D(10000)))
    with pytest.raises(RepoError):
        repos.trades.add(Trade(None, st.id, "GLD", Side.BUY, date(2026, 2, 5), D(1), D(190)))
