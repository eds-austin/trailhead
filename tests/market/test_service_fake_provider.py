from datetime import date, datetime, timedelta
from decimal import Decimal as D

import pytest

from trailhead.core.models import Bar, Instrument, Portfolio, Quote, Side, Trade
from trailhead.data.db import connect
from trailhead.data.repo import Repos
from trailhead.market.provider import DividendDTO, ProfileDTO, ProviderError, QuoteDTO
from trailhead.market.service import (
    MarketService,
    business_days_between,
    clock,
    price_status,
    refresh_targets,
    run_refresh,
)

TODAY = date(2026, 11, 20)


class FakeProvider:
    name = "fake"

    def __init__(self, offline=False):
        self.offline = offline
        self.calls: list[tuple] = []

    def quotes(self, symbols):
        self.calls.append(("quotes", tuple(symbols)))
        if self.offline:
            raise ProviderError("offline")
        return {s: QuoteDTO(s, D("127.40"), D("125.10"), datetime(2026, 11, 20, 16)) for s in symbols if s != "BAD"}

    def history(self, symbol, start, end):
        self.calls.append(("history", symbol, start, end))
        if self.offline:
            raise ProviderError("offline")
        out, d = [], start
        while d <= end:
            if d.weekday() < 5:
                out.append(Bar(d, D(120), D(130), D(119), D(125), 1000))
            d += timedelta(days=1)
        return out

    def dividends(self, symbol, start):
        self.calls.append(("dividends", symbol))
        if self.offline:
            raise ProviderError("offline")
        return [DividendDTO(symbol, date(2026, 11, 5), D("0.25"))]

    def profile(self, symbol):
        self.calls.append(("profile", symbol))
        if self.offline:
            raise ProviderError("offline")
        return ProfileDTO(symbol, f"{symbol} Inc", "Information Technology", None, date(2026, 11, 24), D("0.01"))


@pytest.fixture
def repos(tmp_path):
    r = Repos.open(connect(tmp_path / "m.db"))
    p = r.portfolios.create(Portfolio(None, "P", D(100000)))
    r.trades.add(Trade(None, p.id, "NVDA", Side.BUY, date(2026, 10, 2), D(10), D(120), initial_stop=D(114)))
    b = r.trades.add(Trade(None, p.id, "OLD", Side.BUY, date(2025, 1, 2), D(10), D(50), initial_stop=D(45)))
    r.trades.add(Trade(None, p.id, "OLD", Side.SELL, date(2025, 2, 2), D(10), D(55), lot_id=b.id))
    r.market.upsert_instrument(Instrument("OLD", "Old Co", "Energy"))
    yield r
    r.conn.close()


def test_targets_skip_old_closed(repos):
    assert refresh_targets(repos, None, TODAY) == {"NVDA": date(2026, 10, 2)}


def test_targets_include_old_closed_without_sector(repos):
    repos.market.set_sector("OLD", None)
    assert refresh_targets(repos, None, TODAY) == {"NVDA": date(2026, 10, 2), "OLD": date(2025, 1, 2)}


def test_manual_sector_survives_refresh(repos):
    repos.market.set_sector("NVDA", "Semis")
    run_refresh(repos, FakeProvider(), {"NVDA": date(2026, 10, 2)}, TODAY, force_daily=True)
    i = repos.market.get_instruments(["NVDA"])["NVDA"]
    assert (i.sector, i.sector_manual, i.name) == ("Semis", True, "NVDA Inc")
    repos.market.set_sector("NVDA", None)  # clear → provider value comes back
    run_refresh(repos, FakeProvider(), {"NVDA": date(2026, 10, 2)}, TODAY)
    i = repos.market.get_instruments(["NVDA"])["NVDA"]
    assert (i.sector, i.sector_manual) == ("Information Technology", False)


def test_refresh_writes_and_is_incremental(repos):
    fp = FakeProvider()
    res = run_refresh(repos, fp, {"NVDA": date(2026, 10, 2)}, TODAY)
    assert res.ok and res.quotes == 1 and res.profiles == 1 and res.dividends == 1
    assert repos.market.get_quote("NVDA").last == D("127.40")
    hist_calls = [c for c in fp.calls if c[0] == "history"]
    assert hist_calls[0][2] == TODAY - timedelta(days=365)
    assert repos.market.get_instruments(["NVDA"])["NVDA"].sector == "Information Technology"
    fp.calls.clear()
    run_refresh(repos, fp, {"NVDA": date(2026, 10, 2)}, TODAY)
    hist = [c for c in fp.calls if c[0] == "history"]
    assert hist and hist[0][2] == TODAY  # only today's bar re-fetched
    assert not [c for c in fp.calls if c[0] in ("profile", "dividends")]  # once per day


def test_manual_override_precedence(repos):
    repos.market.upsert_quote(Quote("NVDA", D("100"), None, datetime(2026, 11, 20, 9), "manual", True, True))
    res = run_refresh(repos, FakeProvider(), {"NVDA": date(2026, 10, 2)}, TODAY)
    assert res.pinned_skipped == 1
    assert repos.market.get_quote("NVDA").last == D("100")
    repos.market.upsert_quote(Quote("NVDA", D("100"), None, datetime(2026, 11, 20, 9), "manual", True, False),
                              respect_pinned=False)
    run_refresh(repos, FakeProvider(), {"NVDA": date(2026, 10, 2)}, TODAY)
    q = repos.market.get_quote("NVDA")
    assert q.last == D("127.40") and not q.is_manual


def test_offline_keeps_cache(repos):
    run_refresh(repos, FakeProvider(), {"NVDA": date(2026, 10, 2)}, TODAY)
    res = run_refresh(repos, FakeProvider(offline=True), {"NVDA": date(2026, 10, 2)}, TODAY + timedelta(days=1))
    assert not res.ok and res.errors
    assert repos.market.get_quote("NVDA").last == D("127.40")
    state, _, _ = price_status(repos.settings.get("market.status"), datetime(2026, 11, 21, 12))
    assert state == "error"


def test_price_status():
    assert price_status(None, datetime(2026, 11, 20))[0] == "none"
    st = {"success": "2026-11-20T10:42:00", "attempt": "2026-11-20T10:42:00", "ok": True, "source": "fake"}
    assert price_status(st, datetime(2026, 11, 20, 12))[:2] == ("fresh", "Prices updated 10:42 AM")
    assert price_status(st, datetime(2026, 11, 21, 12))[1] == "Prices updated Nov 20, 10:42 AM"
    assert clock(datetime(2026, 1, 1, 20, 59)) == "8:59 PM"
    assert clock(datetime(2026, 1, 1, 0, 5)) == "12:05 AM"
    assert clock(datetime(2026, 1, 1, 12, 0)) == "12:00 PM"
    assert price_status(st, datetime(2026, 11, 23, 12))[0] == "fresh"  # Fri → Mon is one trading day
    assert price_status(st, datetime(2026, 11, 24, 12))[0] == "stale"
    assert business_days_between(date(2026, 11, 20), date(2026, 11, 23)) == 1


def test_refresh_stores_provider_splits(repos):
    from trailhead.market.provider import SplitDTO

    class WithSplits(FakeProvider):
        def splits(self, symbol):
            return [SplitDTO(symbol, date(2024, 6, 10), D(10))]

    pf = repos.portfolios.create(Portfolio(None, "P", D(10000)))
    repos.trades.add(Trade(None, pf.id, "NVDA", Side.BUY, date(2026, 11, 2), D(10), D(120), initial_stop=D(110)))
    res = run_refresh(repos, WithSplits(), refresh_targets(repos, None, TODAY), TODAY)
    assert res.splits == 1
    assert [(s.symbol, s.ratio) for s in repos.splits.market(["NVDA"])] == [("NVDA", D(10))]


def test_service_threaded(repos, qtbot, tmp_path):
    db = tmp_path / "m.db"
    svc = MarketService(db, FakeProvider, today_fn=lambda: TODAY)
    with qtbot.waitSignal(svc.refreshed, timeout=10000) as blocker:
        assert svc.refresh()
    assert blocker.args[0].quotes == 1
    assert not svc.busy
    got = []
    svc.validate_symbol("NVDA", lambda s, ok, info: got.append((s, ok, info)))
    qtbot.waitUntil(lambda: bool(got), timeout=5000)
    assert got[0][1] is True and "NVDA Inc" in got[0][2]


def test_money_market_yield_is_refreshed_unless_manual(repos):
    from trailhead.core.models import CashAccount, CashAccountKind

    pf = repos.portfolios.create(Portfolio(None, "Cash", D(0), type="cash"))
    fund = repos.cash_accounts.create(CashAccount(None, pf.id, "Fidelity", CashAccountKind.MONEY_MARKET,
                                                  date(2026, 11, 1), symbol="SPAXX"), D(5000))
    mine = repos.cash_accounts.create(CashAccount(None, pf.id, "Mine", CashAccountKind.MONEY_MARKET,
                                                  date(2026, 11, 1), symbol="VMFXX", manual_rate=True), D(10),
                                      D("0.05"))
    targets = refresh_targets(repos, None, TODAY)
    assert {"SPAXX", "VMFXX"} <= set(targets)
    run_refresh(repos, FakeProvider(), targets, TODAY)
    rates = repos.cash_accounts.rates([fund.id, mine.id])
    assert [(r.effective_date, r.apy, r.source) for r in rates[fund.id]] == [(TODAY, D("0.01"), "fake")]
    assert [r.apy for r in rates[mine.id]] == [D("0.05")]
    run_refresh(repos, FakeProvider(), targets, TODAY + timedelta(days=1))  # unchanged yield: no new row
    assert len(repos.cash_accounts.rates([fund.id])[fund.id]) == 1


def test_fetch_quote_for_one_symbol(repos, qtbot, tmp_path):
    svc = MarketService(tmp_path / "m.db", FakeProvider, today_fn=lambda: TODAY)
    got = []
    svc.fetch_quote("DD", got.append)  # not held anywhere: fetched and stored all the same
    qtbot.waitUntil(lambda: bool(got), timeout=5000)
    assert got[0].last == D("127.40") and repos.market.get_quote("DD").last == D("127.40")
    off = MarketService(tmp_path / "m.db", lambda: FakeProvider(offline=True), today_fn=lambda: TODAY)
    got.clear()
    off.fetch_quote("DD", got.append)
    qtbot.waitUntil(lambda: bool(got), timeout=10000)
    assert isinstance(got[0], ProviderError)
