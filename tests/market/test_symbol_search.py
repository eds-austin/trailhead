from datetime import date
from decimal import Decimal as D

from trailhead.core.models import CashEvent, CashKind, Portfolio, Side, Trade
from trailhead.data.db import connect
from trailhead.data.repo import Repos
from trailhead.market.provider import ProfileDTO, ProviderError, SymbolMatch, symbol_variants
from trailhead.market.service import MarketService


def test_symbol_variants():
    assert symbol_variants("BRK.B") == ["BRK-B"]
    assert symbol_variants("brk/b") == ["BRK-B"]
    assert symbol_variants("BRK B") == ["BRK-B"]
    assert symbol_variants("BRKB") == ["BRK-B"]
    assert symbol_variants("BF.B") == ["BF-B"]
    assert symbol_variants("SHOP.TO") == ["SHOP-TO"]  # only a candidate: checked with the provider first
    assert symbol_variants("BRK-B") == [] and symbol_variants("MSFT") == [] and symbol_variants("KO") == []


class SearchProvider:
    name = "fake"
    KNOWN = {"BRK-B": "Berkshire Hathaway Inc."}

    def profile(self, symbol):
        if symbol not in self.KNOWN:
            raise ProviderError(f"{symbol}: unknown symbol")
        return ProfileDTO(symbol, self.KNOWN[symbol])

    def search(self, query):
        return [SymbolMatch("BRKU", "Direxion Daily BRKB Bull 2X ETF", "NASDAQ", "ETF"),
                SymbolMatch("BRK-B", "Berkshire Hathaway Inc.", "NYSE", "Equity")]


def test_find_symbols_puts_known_variants_first(tmp_path, qtbot):
    svc = MarketService(tmp_path / "m.db", SearchProvider)
    got = []
    svc.find_symbols("BRK.B", got.append)
    qtbot.waitUntil(lambda: bool(got), timeout=5000)
    assert [(m.symbol, m.likely, m.exchange) for m in got[0]] == [("BRK-B", True, "NYSE"), ("BRKU", False, "NASDAQ")]


def test_rename_symbol_everywhere(tmp_path):
    repos = Repos.open(connect(tmp_path / "r.db"))
    a = repos.portfolios.create(Portfolio(None, "Hold", D(0), type="long_term"))
    b = repos.portfolios.create(Portfolio(None, "Swing", D(10000)))
    t = repos.trades.add(Trade(None, a.id, "BRK.B", Side.BUY, date(2026, 1, 5), D(300), D("40.69")))
    repos.trades.add(Trade(None, a.id, "BRK.B", Side.SELL, date(2026, 2, 5), D(10), D(45), lot_id=t.id))
    repos.trades.add(Trade(None, b.id, "BRK.B", Side.BUY, date(2026, 1, 5), D(5), D(400), initial_stop=D(380)))
    repos.cash.add(CashEvent(None, a.id, date(2026, 3, 1), CashKind.DIVIDEND, D(1), "BRK.B"))
    repos.market.set_sector("BRK.B", "Financials")
    assert repos.trades.rename_symbol("brk.b", "BRK-B") == 3
    assert repos.trades.symbols() == ["BRK-B"]
    assert [e.symbol for e in repos.cash.list(a.id)] == ["BRK-B"]
    inst = repos.market.get_instruments(["BRK-B"])["BRK-B"]
    assert inst.sector == "Financials" and inst.sector_manual
