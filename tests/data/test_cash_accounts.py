from datetime import date
from decimal import Decimal

import pytest

from trailhead.core.models import (
    CashAccount,
    CashAccountKind,
    CashLink,
    CashRate,
    CashTxn,
    CashTxnKind,
    Portfolio,
)
from trailhead.core.stats import Period
from trailhead.data.repo import RepoError

D = Decimal


@pytest.fixture
def pf(repos):
    return repos.portfolios.create(Portfolio(None, "Cash", D(0), type="cash"))


def new(repos, pf, name="Ally", balance="1000", apy="0.04", **kw):
    a = CashAccount(None, pf.id, name, kw.pop("kind", CashAccountKind.SAVINGS), date(2026, 1, 1), **kw)
    return repos.cash_accounts.create(a, D(balance), D(apy) if apy else None)


def test_create_with_opening_balance_and_rate(repos, pf):
    a = new(repos, pf)
    assert a.id and a.sort_order == 0
    txns = repos.cash_accounts.txns([a.id])[a.id]
    assert [(t.kind, t.amount, t.txn_date) for t in txns] == [(CashTxnKind.OPENING, D(1000), date(2026, 1, 1))]
    assert repos.cash_accounts.rates([a.id])[a.id] == [CashRate(a.id, date(2026, 1, 1), D("0.04"), "MANUAL")]
    b = new(repos, pf, "Vanguard", apy=None, kind=CashAccountKind.MONEY_MARKET, symbol="VMFXX")
    assert b.sort_order == 1
    assert repos.cash_accounts.symbols() == ["VMFXX"]
    assert [x.name for x in repos.cash_accounts.list(pf.id)] == ["Ally", "Vanguard"]


def test_signs_and_validation(repos, pf):
    a = new(repos, pf)
    w = repos.cash_accounts.add(CashTxn(None, a.id, date(2026, 1, 5), CashTxnKind.WITHDRAWAL, D(50)))
    assert w.amount == -50
    with pytest.raises(RepoError):
        repos.cash_accounts.add(CashTxn(None, a.id, date(2026, 1, 5), CashTxnKind.DEPOSIT, D(-5)))
    with pytest.raises(RepoError):
        repos.cash_accounts.add(CashTxn(None, a.id, date(2026, 1, 5), CashTxnKind.INTEREST, D(-5)))
    adj = repos.cash_accounts.add(CashTxn(None, a.id, date(2026, 1, 6), CashTxnKind.ADJUSTMENT, D(-3),
                                          true_up_balance=D(947)))
    assert adj.amount == -3 and repos.cash_accounts.get_txn(adj.id).true_up_balance == 947
    opening = repos.cash_accounts.txns([a.id])[a.id][0]
    with pytest.raises(RepoError):
        repos.cash_accounts.delete_txn(opening.id)


def test_transfer_pair_updates_and_deletes_together(repos, pf):
    a, b = new(repos, pf), new(repos, pf, "Schwab")
    out, inc = repos.cash_accounts.transfer(a.id, b.id, date(2026, 1, 10), D(200), "move")
    assert out.amount == -200 and inc.amount == 200 and out.transfer_id == inc.id and inc.transfer_id == out.id
    repos.cash_accounts.update_txn(CashTxn(out.id, a.id, date(2026, 1, 11), CashTxnKind.TRANSFER, D(-250),
                                           transfer_id=inc.id))
    got = repos.cash_accounts.get_txn(inc.id)
    assert got.amount == 250 and got.txn_date == date(2026, 1, 11)
    repos.cash_accounts.delete_txn(inc.id)
    assert repos.cash_accounts.get_txn(out.id) is None
    with pytest.raises(RepoError):
        repos.cash_accounts.transfer(a.id, a.id, date(2026, 1, 10), D(1))


def test_deleting_an_account_keeps_the_other_side_of_its_transfers(repos, pf):
    a, b = new(repos, pf), new(repos, pf, "Schwab")
    out, inc = repos.cash_accounts.transfer(a.id, b.id, date(2026, 1, 10), D(200))
    repos.cash_accounts.delete(a.id)
    got = repos.cash_accounts.get_txn(inc.id)
    assert got.kind == CashTxnKind.DEPOSIT and got.transfer_id is None
    assert repos.cash_accounts.get(a.id) is None and repos.cash_accounts.get_txn(out.id) is None


def test_rates_upsert_and_links(repos, pf):
    a = new(repos, pf)
    repos.cash_accounts.set_rate(CashRate(a.id, date(2026, 3, 1), D("0.035")))
    repos.cash_accounts.set_rate(CashRate(a.id, date(2026, 3, 1), D("0.036"), "yahoo"))
    assert [(r.apy, r.source) for r in repos.cash_accounts.rates([a.id])[a.id]] == [
        (D("0.04"), "MANUAL"), (D("0.036"), "yahoo")]
    stock = repos.portfolios.create(Portfolio(None, "Swing", D(100000)))
    repos.cash_accounts.link(CashLink(stock.id, a.id, date(2026, 2, 1)))
    assert repos.cash_accounts.link_for(stock.id) == CashLink(stock.id, a.id, date(2026, 2, 1))
    assert repos.cash_accounts.links([a.id]) == [CashLink(stock.id, a.id, date(2026, 2, 1))]
    repos.cash_accounts.unlink(stock.id)
    assert repos.cash_accounts.link_for(stock.id) is None


def test_deleting_the_portfolio_removes_its_accounts(repos, pf):
    a = new(repos, pf)
    repos.portfolios.delete(pf.id)
    assert repos.cash_accounts.get(a.id) is None
    assert repos.cash_accounts.txns([a.id])[a.id] == []


def test_linked_portfolio_flows(repos, pf):
    from trailhead.core.models import CashEvent, CashKind, Side, Trade
    from trailhead.portfolio_types.cash.service import load_cash

    a = new(repos, pf, balance="10000", apy=None)
    lt = repos.portfolios.create(Portfolio(None, "Long", D(0), type="long_term"))
    t = repos.trades
    t.add(Trade(None, lt.id, "OLD", Side.BUY, date(2025, 12, 1), D(1), D(50)))  # before the link: ignored
    b = t.add(Trade(None, lt.id, "KO", Side.BUY, date(2026, 1, 5), D(10), D(60)))
    t.add(Trade(None, lt.id, "KO", Side.SELL, date(2026, 1, 20), D(4), D(70), lot_id=b.id))
    repos.cash.add(CashEvent(None, lt.id, date(2026, 1, 15), CashKind.DIVIDEND, D("5.10"), symbol="KO"))
    # a reinvested dividend moves no cash
    repos.cash.add(CashEvent(None, lt.id, date(2026, 1, 16), CashKind.DIVIDEND, D("3"), symbol="O"))
    t.add(Trade(None, lt.id, "O", Side.BUY, date(2026, 1, 16), D("0.05"), D(60), drip=True))
    repos.cash_accounts.link(CashLink(lt.id, a.id, date(2026, 1, 1)))
    snap = load_cash(repos, pf, date(2026, 1, 31))
    v = snap.accounts[0]
    assert [(f.kind, f.amount, f.source) for f in v.flows if f.txn is None] == [
        ("BUY", D(-600), "Long"), ("DIVIDEND", D("5.10"), "Long"), ("SELL", D(280), "Long")]
    assert v.balance == D("9685.10")
    assert v.net_flows_in(Period()) == D("-314.90")


def test_partly_reinvested_dividend_leaves_the_remainder_in_cash(repos, pf):
    from trailhead.core.models import CashEvent, CashKind, Side, Trade
    from trailhead.portfolio_types.cash.service import load_cash

    a = new(repos, pf, balance="100", apy=None)
    lt = repos.portfolios.create(Portfolio(None, "Long", D(0), type="long_term"))
    repos.trades.add(Trade(None, lt.id, "O", Side.BUY, date(2026, 1, 2), D(10), D(60)))
    repos.cash.add(CashEvent(None, lt.id, date(2026, 1, 16), CashKind.DIVIDEND, D("3.00"), symbol="O"))
    repos.trades.add(Trade(None, lt.id, "O", Side.BUY, date(2026, 1, 16), D("0.049"), D(60), drip=True))
    repos.cash_accounts.link(CashLink(lt.id, a.id, date(2026, 1, 10)))
    v = load_cash(repos, pf, date(2026, 1, 31)).accounts[0]
    assert v.balance == D("100") + D("3.00") - D("2.94")
