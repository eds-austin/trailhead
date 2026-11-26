"""KPI cards for a Long-term Holdings portfolio."""

from __future__ import annotations

from decimal import Decimal

from ...core.holdings import HoldingsSnapshot, period_return
from ...core.money import dsum
from ...core.stats import PERIOD_LABELS, Period, PeriodKey
from ...ui import format as fmt
from ...ui.widgets.kpi_card import KpiData

KPI_CARDS: list[tuple[str, str]] = [
    ("value", "Portfolio value"),
    ("total_return", "Total return"),
    ("unrealized", "Unrealized P/L"),
    ("realized", "Realized P/L"),
    ("income", "Est. annual income"),
    ("yoc", "Yield on cost"),
    ("dividends", "Dividends received"),
    ("next_div", "Next dividend"),
]


def _signed_compact(v: Decimal) -> str:
    return ("+" if v > 0 else "") + fmt.compact_money(v)


SHORT = {"Last 12 months": "12M"}  # card titles are short
ALL_TIME_TIP = ("Unrealized + realized P/L + dividends received, since each holding was bought.\n"
                "% is of the money invested (the cost of every buy except reinvested dividends).")


def _total_return(snap: HoldingsSnapshot, tr: Decimal | None, period: Period, plabel: str,
                  points: list | None) -> KpiData:
    all_time = KpiData(
        fmt.signed_money(tr), f"{fmt.signed_pct(snap.total_return_pct, 2)} on {fmt.compact_money(snap.invested)}"
        if tr is not None and snap.invested else "", value_tone=_tone(tr), tooltip=ALL_TIME_TIP,
        label="Total return")
    if period.start is None or tr is None or not points:
        return all_time
    pr = period_return(points, snap.trades, period.start, period.end)
    if pr is None:
        return all_time
    return KpiData(
        fmt.signed_money(pr.pl), f"{fmt.signed_pct(pr.pct, 2)} · all time {_signed_compact(tr)}",
        value_tone=_tone(pr.pl), label=f"Total return - {SHORT.get(plabel, plabel)}",
        tooltip=f"Earned {plabel}: change in value of the holdings at daily closes + realized P/L + dividends "
                f"received.\n% = that ÷ (value at the start, {fmt.money(pr.start_value)}, + half the money added "
                f"in the period, {fmt.signed_money(pr.net_added)}).\n\nAll time: {fmt.signed_money(tr)} "
                f"({fmt.signed_pct(snap.total_return_pct, 2)} on {fmt.money(snap.invested)} invested).")


def _tone(v: Decimal | None) -> str | None:
    s = fmt.sign_of(v)
    return "positive" if s > 0 else "negative" if s < 0 else None


def compute_kpis(snap: HoldingsSnapshot, period: Period, period_key: PeriodKey | str = PeriodKey.YTD,
                 points: list | None = None) -> dict[str, KpiData]:
    """`points`: the full daily P/L series (service.pl_points); with a Period other than All, Total return shows
    the return earned in the period and the all-time figure goes in the subtitle."""
    plabel = PERIOD_LABELS.get(PeriodKey(period_key), str(period_key))
    open_h = snap.open_holdings
    has_prices = any(h.last is not None for h in open_h)
    day = snap.day_change if open_h else None
    tr = snap.total_return if has_prices or not open_h else None
    unreal = snap.unrealized_pl if has_prices else None
    divs, n_divs = snap.income_between(period.start, period.end)
    sells = [s for lot in snap.lots for s in lot.sells if period.contains(s.trade_date)]
    realized = dsum(lot.realized_pl_between(period.start, period.end) for lot in snap.lots)
    reinv = snap.reinvested
    up = snap.upcoming_dividends()
    nxt = up[0] if up else None
    if nxt is not None:
        h, nd = nxt
        others = [x for x in up[1:] if x[1].date == nd.date]
        what = "ex-date" if nd.ex_date else "pays"
        est = (h.open_shares * nd.per_share) if nd.per_share is not None else None
        sub = f"{what}{' (est.)' if nd.estimated else ''}" + (f" · ~{fmt.money(est)}" if est else "")
        names = h.symbol + (f" +{len(others)}" if others else "")
        next_card = KpiData(f"{fmt.month_day(nd.date)} · {names}", sub,
                            tooltip="\n".join(f"{x.symbol}: {'ex' if d.ex_date else 'pays'} {fmt.short_date(d.date)}"
                                              f"{' (estimated from past payments)' if d.estimated else ''}"
                                              for x, d in up[:8]))
    else:
        next_card = KpiData("—", "No dividend scheduled" if open_h else "")
    return {
        "value": KpiData(
            fmt.money(snap.value),
            f"{fmt.signed_money(day)} · {fmt.signed_pct(snap.day_change_pct, 2)} today" if day is not None else
            "No holdings" if not open_h else "",
            sub_tone=_tone(day), tooltip="Market value of the holdings"),
        "total_return": _total_return(snap, tr, period, plabel, points),
        "unrealized": KpiData(fmt.signed_money(unreal), f"{fmt.signed_pct(snap.unrealized_pct, 2)} of cost"
                              if unreal is not None else "", value_tone=_tone(unreal)),
        "income": KpiData(
            fmt.money(snap.est_income) if open_h else "—",
            f"{fmt.money(snap.est_income / 12)} a month on average" if open_h and snap.est_income else "",
            tooltip="Shares held × the annual dividend per share (the provider's forward rate, else the last 12 "
                    "months)."),
        "yoc": KpiData(
            fmt.pct(snap.yield_on_cost, 2) if open_h else "—",
            f"Current yield {fmt.pct(snap.div_yield, 2)}" if open_h else "",
            tooltip="Yield on cost = estimated annual income ÷ cost basis of the shares held.\n"
                    "Current yield = estimated annual income ÷ market value."),
        "dividends": KpiData(
            fmt.money(divs), f"{n_divs} payment{'s' if n_divs != 1 else ''} · {plabel}"
            + (f" · {fmt.money(reinv, 0)} reinvested all time" if reinv else ""),
            tooltip=f"Dividends received · {fmt.money(snap.dividends)} all time"),
        "realized": KpiData(fmt.signed_money(realized), f"{len(sells)} sell{'s' if len(sells) != 1 else ''} · {plabel}",
                            value_tone=_tone(realized)),
        "next_div": next_card,
    }
