"""Cash accounts: balances, rates, expected interest and period figures. Pure Python.

An account's money moves through *flows*: its stored transactions (opening balance, deposits, withdrawals,
transfers, interest, adjustments) plus, later, flows derived from a linked portfolio's trades. Interest is paid
monthly on the account's payout day; the expected interest for a payout period accrues daily, compounding, at the
APY in effect each day on the end-of-day balance. A payout period with no interest recorded is *proposed* for the
user to confirm. A true-up settles every period that ended on or before its date (the difference it booked
includes any interest the bank had paid by then).
"""

from __future__ import annotations

import bisect
import calendar
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from .models import CashAccount, CashRate, CashTxn, CashTxnKind
from .money import ZERO, cents, dsum, safe_div
from .stats import Period

ONE = Decimal(1)
DAYS_PER_YEAR = Decimal(365)
TRUE_UP_STALE_DAYS = 30


@dataclass(frozen=True)
class Flow:
    """One movement of money in an account. txn is the stored row; source names a linked portfolio's flow."""

    date: date
    amount: Decimal
    kind: str  # a CashTxnKind value, or SELL / BUY / DIVIDEND for linked-portfolio flows
    txn: CashTxn | None = None
    source: str | None = None
    label: str = ""

    @property
    def sort_key(self) -> tuple:
        order = 0 if self.kind == CashTxnKind.OPENING else 2 if self.txn is not None and self.txn.is_true_up else 1
        return (self.date, order, self.txn.id if self.txn and self.txn.id else 0)


@dataclass(frozen=True)
class InterestProposal:
    account_id: int
    period_start: date  # exclusive: interest accrues on the days after it, up to period_end
    period_end: date  # the payout date
    amount: Decimal
    apy: Decimal | None  # the rate at the end of the period (it may have changed within it)
    days: int


def payout_date(year: int, month: int, payout_day: int) -> date:
    last = calendar.monthrange(year, month)[1]
    return date(year, month, last if payout_day <= 0 else min(payout_day, last))


def payout_dates(payout_day: int, after: date, through: date) -> list[date]:
    """Payout dates d with after < d ≤ through."""
    out: list[date] = []
    y, m = after.year, after.month
    while True:
        d = payout_date(y, m, payout_day)
        if d > through:
            return out
        if d > after:
            out.append(d)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def previous_payout(payout_day: int, d: date) -> date:
    """The last payout date strictly before d."""
    p = payout_date(d.year, d.month, payout_day)
    if p < d:
        return p
    y, m = (d.year - 1, 12) if d.month == 1 else (d.year, d.month - 1)
    return payout_date(y, m, payout_day)


def daily_rate(apy: Decimal) -> Decimal:
    """The daily compounding rate that yields `apy` over a year."""
    if apy <= 0:
        return ZERO
    return (ONE + apy) ** (ONE / DAYS_PER_YEAR) - ONE


@dataclass
class AccountView:
    account: CashAccount
    rates: list[CashRate]
    flows: list[Flow]
    today: date
    _dates: list[date] = field(default_factory=list, repr=False)
    _cum: list[Decimal] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        self.rates = sorted(self.rates, key=lambda r: r.effective_date)
        self.flows = sorted(self.flows, key=lambda f: f.sort_key)
        total = ZERO
        for f in self.flows:
            total += f.amount
            if self._dates and self._dates[-1] == f.date:
                self._cum[-1] = total
            else:
                self._dates.append(f.date)
                self._cum.append(total)

    # ------------------------------------------------------------------ identity
    @property
    def id(self) -> int:
        assert self.account.id is not None
        return self.account.id

    @property
    def key(self) -> str:
        return f"acct:{self.account.id}"

    @property
    def txns(self) -> list[CashTxn]:
        return [f.txn for f in self.flows if f.txn is not None]

    # ------------------------------------------------------------------ balance
    def balance_on(self, d: date) -> Decimal:
        """End-of-day balance on d."""
        i = bisect.bisect_right(self._dates, d)
        return self._cum[i - 1] if i else ZERO

    @property
    def balance(self) -> Decimal:
        return self.balance_on(self.today)

    @property
    def opening(self) -> Decimal:
        return dsum(f.amount for f in self.flows if f.kind == CashTxnKind.OPENING)

    # ------------------------------------------------------------------ rate
    def apy_on(self, d: date) -> Decimal | None:
        r = None
        for x in self.rates:
            if x.effective_date > d:
                break
            r = x
        return r.apy if r else None

    @property
    def apy(self) -> Decimal | None:
        return self.apy_on(self.today)

    @property
    def rate_source(self) -> str | None:
        cur = [x for x in self.rates if x.effective_date <= self.today]
        return cur[-1].source if cur else None

    @property
    def est_annual_interest(self) -> Decimal | None:
        a = self.apy
        return self.balance * a if a is not None and self.balance > 0 else (ZERO if a is not None else None)

    # ------------------------------------------------------------------ interest
    def accrue(self, start: date, end: date, exclude_period: date | None = None) -> Decimal:
        """Expected interest for the days after `start` up to `end` (compounded daily, unrounded). Interest
        already recorded for the period `exclude_period` is left out of the balance it earns on."""
        if end <= start:
            return ZERO
        paid_in_period = [f for f in self.flows if exclude_period is not None and f.txn is not None
                          and f.txn.kind == CashTxnKind.INTEREST and f.txn.period_end == exclude_period]
        accrued = ZERO
        d = max(start, self.account.opened_on - timedelta(days=1))
        while d < end:
            d += timedelta(days=1)
            apy = self.apy_on(d)
            if not apy:
                continue
            bal = self.balance_on(d) - dsum(f.amount for f in paid_in_period if f.date <= d)
            if bal > 0:
                accrued += (bal + accrued) * daily_rate(apy)
        return accrued

    @property
    def last_true_up(self) -> CashTxn | None:
        ups = [t for t in self.txns if t.is_true_up]
        return ups[-1] if ups else None

    @property
    def settled_through(self) -> date | None:
        """Interest periods ending on or before this date are settled by a true-up."""
        t = self.last_true_up
        return t.txn_date if t else None

    def interest_paid_for(self) -> set[date]:
        return {t.period_end for t in self.txns if t.kind == CashTxnKind.INTEREST and t.period_end is not None}

    def proposals(self) -> list[InterestProposal]:
        """Payout periods up to today with no interest recorded and not settled by a true-up."""
        a = self.account
        if a.closed or not self.rates:
            return []
        paid = self.interest_paid_for()
        settled = self.settled_through
        out = []
        for p in payout_dates(a.payout_day, a.opened_on, self.today):
            if p in paid or (settled is not None and p <= settled):
                continue
            start = max(previous_payout(a.payout_day, p), a.opened_on - timedelta(days=1))
            amount = cents(self.accrue(start, p, exclude_period=p))
            if amount <= 0:
                continue
            out.append(InterestProposal(self.id, start, p, amount, self.apy_on(p), (p - start).days))
        return out

    def accrued_since_payout(self) -> Decimal:
        """Interest earned since the last payout date (not yet paid)."""
        start = max(previous_payout(self.account.payout_day, self.today), self.account.opened_on - timedelta(days=1))
        if self.today in payout_dates(self.account.payout_day, self.today - timedelta(days=1), self.today):
            return ZERO
        return cents(self.accrue(start, self.today))

    # ------------------------------------------------------------------ period figures
    def _in(self, period: Period, kinds: Iterable[str]) -> list[Flow]:
        ks = set(kinds)
        return [f for f in self.flows if f.kind in ks and period.contains(f.date) and f.date <= self.today]

    def interest_in(self, period: Period) -> Decimal:
        return dsum(f.amount for f in self._in(period, (CashTxnKind.INTEREST,)))

    def net_flows_in(self, period: Period) -> Decimal:
        """Money added minus money taken out (deposits, withdrawals, transfers, linked portfolio flows)."""
        return dsum(f.amount for f in self.flows if period.contains(f.date) and f.date <= self.today
                    and f.kind not in (CashTxnKind.OPENING, CashTxnKind.INTEREST, CashTxnKind.ADJUSTMENT))

    def adjustments_in(self, period: Period) -> Decimal:
        return dsum(f.amount for f in self._in(period, (CashTxnKind.ADJUSTMENT,)))

    def average_balance(self, period: Period) -> tuple[Decimal, int]:
        """Average end-of-day balance over the days of the period the account was open, and that day count."""
        start = max(period.start or self.account.opened_on, self.account.opened_on)
        end = min(period.end or self.today, self.today)
        if end < start:
            return ZERO, 0
        total, n, d = ZERO, 0, start
        while d <= end:
            total += self.balance_on(d)
            n += 1
            d += timedelta(days=1)
        return total / n, n

    def yield_earned(self, period: Period) -> Decimal | None:
        """Interest received in the period ÷ the average balance, annualized."""
        avg, n = self.average_balance(period)
        if n < 28 or avg <= 0:
            return None
        return safe_div(self.interest_in(period) * DAYS_PER_YEAR / n, avg)


@dataclass
class CashSnapshot:
    accounts: list[AccountView]
    today: date

    @property
    def open_accounts(self) -> list[AccountView]:
        return [a for a in self.accounts if not a.account.closed]

    @property
    def balance(self) -> Decimal:
        return dsum(a.balance for a in self.accounts)

    @property
    def weighted_apy(self) -> Decimal | None:
        with_rate = [a for a in self.open_accounts if a.apy is not None and a.balance > 0]
        total = dsum(a.balance for a in with_rate)
        return safe_div(dsum(a.balance * a.apy for a in with_rate), total)  # type: ignore[operator]

    @property
    def est_annual_interest(self) -> Decimal:
        return dsum(a.est_annual_interest or ZERO for a in self.open_accounts)

    def proposals(self) -> list[InterestProposal]:
        return [p for a in self.accounts for p in a.proposals()]

    def interest_in(self, period: Period) -> Decimal:
        return dsum(a.interest_in(period) for a in self.accounts)

    def net_flows_in(self, period: Period) -> Decimal:
        return dsum(a.net_flows_in(period) for a in self.accounts)

    def yield_earned(self, period: Period) -> Decimal | None:
        avgs = [a.average_balance(period) for a in self.accounts]
        n = max((x[1] for x in avgs), default=0)
        avg = dsum(x[0] * x[1] for x in avgs) / n if n else ZERO
        if n < 28 or avg <= 0:
            return None
        return safe_div(self.interest_in(period) * DAYS_PER_YEAR / n, avg)

    def stale_true_ups(self) -> list[AccountView]:
        """Open accounts not trued up (or opened) in the last TRUE_UP_STALE_DAYS days."""
        out = []
        for a in self.open_accounts:
            t = a.last_true_up
            last = t.txn_date if t else a.account.opened_on
            if (self.today - last).days > TRUE_UP_STALE_DAYS:
                out.append(a)
        return out

    def get(self, account_id: int) -> AccountView | None:
        return next((a for a in self.accounts if a.account.id == account_id), None)


def txn_flows(txns: Sequence[CashTxn]) -> list[Flow]:
    return [Flow(t.txn_date, t.amount, t.kind.value, txn=t) for t in txns]


def build_snapshot(accounts: Sequence[CashAccount], rates: dict[int, list[CashRate]],
                   txns: dict[int, list[CashTxn]], today: date,
                   extra_flows: dict[int, list[Flow]] | None = None) -> CashSnapshot:
    views = []
    for a in accounts:
        assert a.id is not None
        flows = txn_flows(txns.get(a.id, [])) + list((extra_flows or {}).get(a.id, []))
        views.append(AccountView(a, list(rates.get(a.id, [])), flows, today))
    return CashSnapshot(views, today)
