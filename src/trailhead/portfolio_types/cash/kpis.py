"""KPI cards for a Cash portfolio."""

from __future__ import annotations

from decimal import Decimal

from ...core.cash import CashSnapshot
from ...core.money import dsum
from ...core.stats import PERIOD_LABELS, Period, PeriodKey
from ...ui import format as fmt
from ...ui.widgets.kpi_card import KpiData

KPI_CARDS: list[tuple[str, str]] = [
    ("balance", "Total cash"),
    ("apy", "Weighted APY"),
    ("interest", "Interest earned"),
    ("yield", "Yield earned"),
    ("net", "Net flows"),
    ("pending", "Interest to confirm"),
]

SHORT = {"Last 12 months": "12M"}


def _tone(v: Decimal | None) -> str | None:
    s = fmt.sign_of(v)
    return "positive" if s > 0 else "negative" if s < 0 else None


def compute_kpis(snap: CashSnapshot, period: Period, period_key: PeriodKey | str = PeriodKey.YTD
                 ) -> dict[str, KpiData]:
    plabel = PERIOD_LABELS.get(PeriodKey(period_key), str(period_key))
    short = SHORT.get(plabel, plabel)
    n = len(snap.open_accounts)
    apy = snap.weighted_apy
    est = snap.est_annual_interest
    interest = snap.interest_in(period)
    y = snap.yield_earned(period)
    net = snap.net_flows_in(period)
    props = snap.proposals()
    pending = dsum(p.amount for p in props)
    accrued = dsum(a.accrued_since_payout() for a in snap.open_accounts)
    payments = sum(1 for a in snap.accounts for f in a.flows if f.kind == "INTEREST" and period.contains(f.date)
                   and f.amount)
    return {
        "balance": KpiData(fmt.money(snap.balance), f"{n} account{'s' if n != 1 else ''}" if n else "No accounts",
                           tooltip="Sum of the account balances today"),
        "apy": KpiData(fmt.pct(apy, 2) if apy is not None else "—",
                       f"≈ {fmt.money(est, 0)} a year" if est else "",
                       tooltip="Balance-weighted APY of the accounts that have a rate.\n"
                               f"Estimate = each balance × its APY: {fmt.money(est)} a year, "
                               f"{fmt.money(est / 12)} a month."),
        "interest": KpiData(fmt.money(interest), f"{payments} payment{'s' if payments != 1 else ''}"
                            + (f" · {fmt.money(accrued)} accrued" if accrued else ""),
                            label=f"Interest earned - {short}",
                            tooltip="Interest confirmed in the period (including interest booked by a true-up).\n"
                                    "Accrued: expected interest since each account's last payout date."),
        "yield": KpiData(fmt.pct(y, 2) if y is not None else "—",
                         f"annualized · {plabel}" if y is not None else "Needs a month of history",
                         label=f"Yield earned - {short}",
                         tooltip="Interest received in the period ÷ the average daily balance, annualized.\n"
                                 "Compare with the weighted APY: lower means interest is missing or a rate is "
                                 "out of date."),
        "net": KpiData(fmt.signed_money(net), "deposits, withdrawals, trades", value_tone=_tone(net),
                       label=f"Net flows - {short}",
                       tooltip="Deposits − withdrawals, transfers and linked portfolios' sells, buys and "
                               "dividends. Opening balances, interest and adjustments are not included."),
        "pending": KpiData(fmt.money(pending) if props else "None",
                           f"{len(props)} payment{'s' if len(props) != 1 else ''} · estimated" if props
                           else "All interest confirmed" if n else "",
                           sub_tone="warning" if props else None,
                           tooltip="Expected interest for payout dates with none recorded. Confirm it from the "
                                   "account's Action cell."),
    }
