from datetime import date
from decimal import Decimal

from trailhead.core.cash import (
    build_snapshot,
    daily_rate,
    payout_date,
    payout_dates,
    previous_payout,
)
from trailhead.core.models import CashAccount, CashAccountKind, CashRate, CashTxn, CashTxnKind
from trailhead.core.stats import Period

D = Decimal


def acct(aid=1, opened=date(2026, 1, 1), payout_day=0, **kw):
    return CashAccount(aid, 10, f"Acct {aid}", CashAccountKind.SAVINGS, opened, payout_day=payout_day, **kw)


def txn(tid, d, kind, amount, aid=1, **kw):
    return CashTxn(tid, aid, d, kind, D(amount), **kw)


def snap(accounts, rates, txns, today):
    return build_snapshot(accounts, rates, txns, today)


def test_payout_schedule():
    assert payout_date(2026, 2, 0) == date(2026, 2, 28)
    assert payout_date(2026, 2, 15) == date(2026, 2, 15)
    assert payout_dates(0, date(2026, 1, 1), date(2026, 3, 31)) == [date(2026, 1, 31), date(2026, 2, 28),
                                                                   date(2026, 3, 31)]
    assert payout_dates(15, date(2026, 1, 15), date(2026, 3, 14)) == [date(2026, 2, 15)]
    assert previous_payout(0, date(2026, 3, 31)) == date(2026, 2, 28)
    assert previous_payout(0, date(2026, 3, 10)) == date(2026, 2, 28)
    assert previous_payout(15, date(2026, 1, 20)) == date(2026, 1, 15)
    assert previous_payout(15, date(2026, 1, 10)) == date(2025, 12, 15)


def test_daily_rate_compounds_to_apy():
    r = daily_rate(D("0.04"))
    assert abs((1 + r) ** 365 - D("1.04")) < D("1e-12")
    assert daily_rate(D(0)) == 0


def test_balance_flows_and_transfers():
    a = acct()
    txns = {1: [txn(1, date(2026, 1, 1), CashTxnKind.OPENING, "10000"),
                txn(2, date(2026, 1, 10), CashTxnKind.DEPOSIT, "500"),
                txn(3, date(2026, 1, 20), CashTxnKind.WITHDRAWAL, "-200"),
                txn(4, date(2026, 1, 25), CashTxnKind.TRANSFER, "-1000", transfer_id=5)]}
    s = snap([a], {}, txns, date(2026, 1, 31))
    v = s.accounts[0]
    assert v.balance_on(date(2025, 12, 31)) == 0
    assert v.balance_on(date(2026, 1, 9)) == 10000
    assert v.balance_on(date(2026, 1, 10)) == 10500
    assert v.balance == 9300
    assert v.net_flows_in(Period(date(2026, 1, 1), date(2026, 1, 31))) == -700  # opening excluded


def test_interest_proposal_matches_apy_for_a_full_month():
    a = acct(opened=date(2025, 12, 31))
    rates = {1: [CashRate(1, date(2025, 12, 31), D("0.04"))]}
    txns = {1: [txn(1, date(2025, 12, 31), CashTxnKind.OPENING, "100000")]}
    s = snap([a], rates, txns, date(2026, 2, 5))
    props = s.proposals()
    assert [p.period_end for p in props] == [date(2026, 1, 31)]
    jan = props[0]
    expected = D(100000) * ((1 + daily_rate(D("0.04"))) ** 31 - 1)
    assert abs(jan.amount - expected) < D("0.01")
    assert jan.days == 31 and jan.apy == D("0.04")


def test_first_period_counts_from_the_opening_day_and_rate_changes_apply():
    a = acct(opened=date(2026, 1, 16))
    rates = {1: [CashRate(1, date(2026, 1, 16), D("0.05")), CashRate(1, date(2026, 1, 26), D("0"))]}
    txns = {1: [txn(1, date(2026, 1, 16), CashTxnKind.OPENING, "36500")]}
    p = snap([a], rates, txns, date(2026, 1, 31)).proposals()[0]
    # 10 days (16th..25th) at 5 %, then 0 %
    expected = D(36500) * ((1 + daily_rate(D("0.05"))) ** 10 - 1)
    assert abs(p.amount - expected) < D("0.01")


def test_recorded_interest_and_true_up_settle_periods():
    a = acct(opened=date(2025, 12, 31))
    rates = {1: [CashRate(1, date(2025, 12, 31), D("0.04"))]}
    base = [txn(1, date(2025, 12, 31), CashTxnKind.OPENING, "10000")]
    jan = txn(2, date(2026, 1, 31), CashTxnKind.INTEREST, "33.50", period_end=date(2026, 1, 31))
    s = snap([a], rates, {1: [*base, jan]}, date(2026, 4, 2))
    assert [p.period_end for p in s.proposals()] == [date(2026, 2, 28), date(2026, 3, 31)]
    # February's interest accrues on January's interest too (it is in the balance)
    feb = s.proposals()[0]
    assert abs(feb.amount - D("10033.50") * ((1 + daily_rate(D("0.04"))) ** 28 - 1)) < D("0.01")
    up = txn(3, date(2026, 3, 31), CashTxnKind.INTEREST, "70", true_up_balance=D("10103.50"))
    s2 = snap([a], rates, {1: [*base, jan, up]}, date(2026, 4, 2))
    assert s2.proposals() == []
    assert s2.accounts[0].last_true_up == up


def test_interest_for_the_period_is_not_counted_in_its_own_balance():
    a = acct(opened=date(2025, 12, 31))
    rates = {1: [CashRate(1, date(2025, 12, 31), D("0.04"))]}
    txns = [txn(1, date(2025, 12, 31), CashTxnKind.OPENING, "10000")]
    v = snap([a], rates, {1: txns}, date(2026, 2, 1)).accounts[0]
    before = v.accrue(date(2025, 12, 31), date(2026, 1, 31), exclude_period=date(2026, 1, 31))
    paid = txn(2, date(2026, 1, 31), CashTxnKind.INTEREST, "1000", period_end=date(2026, 1, 31))
    v2 = snap([a], rates, {1: [*txns, paid]}, date(2026, 2, 1)).accounts[0]
    assert v2.accrue(date(2025, 12, 31), date(2026, 1, 31), exclude_period=date(2026, 1, 31)) == before


def test_no_proposals_without_a_rate_or_for_closed_accounts():
    txns = {1: [txn(1, date(2026, 1, 1), CashTxnKind.OPENING, "1000")]}
    assert snap([acct()], {}, txns, date(2026, 3, 1)).proposals() == []
    rates = {1: [CashRate(1, date(2026, 1, 1), D("0.04"))]}
    assert snap([acct(closed=True)], rates, txns, date(2026, 3, 1)).proposals() == []


def test_snapshot_totals_and_yield_earned():
    a, b = acct(1, opened=date(2025, 12, 31)), acct(2, opened=date(2025, 12, 31))
    rates = {1: [CashRate(1, date(2025, 12, 31), D("0.04"))], 2: [CashRate(2, date(2025, 12, 31), D("0.02"))]}
    txns = {1: [txn(1, date(2025, 12, 31), CashTxnKind.OPENING, "30000"),
                txn(3, date(2026, 1, 31), CashTxnKind.INTEREST, "100", period_end=date(2026, 1, 31))],
            2: [txn(2, date(2025, 12, 31), CashTxnKind.OPENING, "10000", aid=2)]}
    s = snap([a, b], rates, txns, date(2026, 1, 31))
    assert s.balance == D("40100")
    assert s.weighted_apy == (D("30100") * D("0.04") + D("10000") * D("0.02")) / D("40100")
    jan = Period(date(2026, 1, 1), date(2026, 1, 31))
    assert s.interest_in(jan) == 100
    y = s.accounts[0].yield_earned(jan)
    assert y is not None and abs(y - D(100) * 365 / 31 / (D(30000) + D(100) / 31)) < D("0.0001")


def test_stale_true_ups():
    a = acct(opened=date(2026, 1, 1))
    txns = {1: [txn(1, date(2026, 1, 1), CashTxnKind.OPENING, "1000")]}
    assert snap([a], {}, txns, date(2026, 1, 31)).stale_true_ups() == []
    assert len(snap([a], {}, txns, date(2026, 2, 5)).stale_true_ups()) == 1
    txns[1].append(txn(2, date(2026, 2, 1), CashTxnKind.ADJUSTMENT, "0", true_up_balance=D(1000)))
    assert snap([a], {}, txns, date(2026, 2, 5)).stale_true_ups() == []
