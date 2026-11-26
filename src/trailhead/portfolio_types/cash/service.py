"""Loading a Cash portfolio: its accounts, rates, transactions and the flows of linked portfolios."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from ...core.cash import AccountView, CashSnapshot, Flow, build_snapshot
from ...core.models import CashKind, CashLink, CashRate, Portfolio, Side
from ...data.repo import Repos

LINKED_KINDS = ("BUY", "SELL", "DIVIDEND")


def linked_flows(repos: Repos, links: list[CashLink]) -> dict[int, list[Flow]]:
    """Per cash account: the sells (+proceeds), buys (−cost) and cash dividends (+) of each linked portfolio from
    its link date. A dividend fully reinvested (a DRIP buy of the same cost on its date) moves no cash, so the pair
    is left out; when the reinvestment differs (fractional-share rounding) both show and the remainder settles.
    Shares received in a spin-off cost nothing."""
    out: dict[int, list[Flow]] = defaultdict(list)
    for link in links:
        p = repos.portfolios.get(link.portfolio_id)
        if p is None:
            continue
        trades = [t for t in repos.trades.list(p.id) if t.trade_date >= link.since and not t.spinoff_of]
        divs = [e for e in repos.cash.list(p.id) if e.kind == CashKind.DIVIDEND and e.event_date >= link.since]
        drip_cost: dict[tuple[str, date], Decimal] = defaultdict(Decimal)
        for t in trades:
            if t.side == Side.BUY and t.drip:
                drip_cost[(t.symbol, t.trade_date)] += t.amount
        div_amount: dict[tuple[str | None, date], Decimal] = defaultdict(Decimal)
        for e in divs:
            div_amount[(e.symbol, e.event_date)] += e.amount
        netted = {k for k, cost in drip_cost.items() if div_amount.get(k) == cost}
        for t in trades:
            if t.side == Side.BUY and t.drip and (t.symbol, t.trade_date) in netted:
                continue
            sign = -1 if t.side == Side.BUY else 1
            what = "DRIP BUY" if t.drip else t.side.value
            out[link.account_id].append(Flow(
                t.trade_date, sign * t.amount, t.side.value, source=p.name,
                label=f"{what} {t.shares.normalize():f} {t.symbol} @ {t.price.normalize():f}"))
        for e in divs:
            if (e.symbol, e.event_date) in netted:
                continue
            out[link.account_id].append(Flow(e.event_date, e.amount, "DIVIDEND", source=p.name,
                                             label=f"{e.symbol} dividend"))
    return out


def load_cash(repos: Repos, portfolio: Portfolio, today: date) -> CashSnapshot:
    assert portfolio.id is not None
    accounts = repos.cash_accounts.list(portfolio.id)
    ids = [a.id for a in accounts if a.id is not None]
    links = repos.cash_accounts.links(ids)
    return build_snapshot(accounts, repos.cash_accounts.rates(ids), repos.cash_accounts.txns(ids), today,
                          linked_flows(repos, links))


def linked_names(repos: Repos, account_ids: list[int]) -> dict[int, list[tuple[str, date]]]:
    """Per account: (portfolio name, since) of the portfolios linked to it."""
    out: dict[int, list[tuple[str, date]]] = defaultdict(list)
    for link in repos.cash_accounts.links(account_ids):
        p = repos.portfolios.get(link.portfolio_id)
        if p is not None and not p.archived:
            out[link.account_id].append((p.name, link.since))
    return out


def sync_fund_yields(repos: Repos, source: str, today: date) -> int:
    """Record each money market fund's latest yield from the data provider as its rate from today, when it
    changed. Accounts with a manual rate are left alone. Returns the number of accounts updated."""
    funds = [a for a in repos.cash_accounts.list() if a.is_money_market and a.symbol and not a.manual_rate
             and not a.closed]
    if not funds:
        return 0
    instruments = repos.market.get_instruments({a.symbol for a in funds if a.symbol})
    rates = repos.cash_accounts.rates([a.id for a in funds if a.id is not None])
    n = 0
    for a in funds:
        inst = instruments.get(a.symbol or "")
        y = inst.div_yield if inst else None
        if y is None or a.id is None:
            continue
        current = [r for r in rates.get(a.id, []) if r.effective_date <= today]
        if current and current[-1].apy == y:
            continue
        repos.cash_accounts.set_rate(CashRate(a.id, max(today, a.opened_on), y, source))
        n += 1
    return n


def linked_account(repos: Repos, portfolio_id: int, today: date) -> AccountView | None:
    """The cash account a Stock Trading / Long-term portfolio settles in, with every flow that reaches it."""
    link = repos.cash_accounts.link_for(portfolio_id)
    if link is None:
        return None
    a = repos.cash_accounts.get(link.account_id)
    owner = repos.portfolios.get(a.portfolio_id) if a else None
    if a is None or a.id is None or owner is None or owner.archived:
        return None
    snap = build_snapshot([a], repos.cash_accounts.rates([a.id]), repos.cash_accounts.txns([a.id]), today,
                          linked_flows(repos, repos.cash_accounts.links([a.id])))
    return snap.accounts[0]


def cash_after_buy(repos: Repos, portfolio_id: int, today: date, cost, replacing=None):
    """(account view, balance after paying `cost`) when the portfolio settles in a cash account; `replacing` is the
    cost of the buy being edited (already in the balance). None when not linked."""
    v = linked_account(repos, portfolio_id, today)
    if v is None:
        return None
    return v, v.balance + (replacing or 0) - cost
