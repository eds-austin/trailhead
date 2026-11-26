"""KPI card definitions and metric → KpiData mapping for the stock-trading portfolio (PLAN §7.2)."""

from __future__ import annotations

from decimal import Decimal

from ...core.models import CashKind
from ...core.money import safe_div
from ...core.snapshot import PortfolioSnapshot
from ...core.stats import (
    PERIOD_LABELS,
    Period,
    PeriodKey,
    closed_lot_units,
    closed_position_units,
    realized_in_period,
    trade_stats,
)
from ...ui import format as fmt
from ...ui.widgets.kpi_card import KpiData

KPI_CARDS: list[tuple[str, str]] = [
    ("value", "Portfolio value"),
    ("dividends", "Dividends"),
    ("unrealized", "Unrealized P/L"),
    ("realized", "Realized P/L"),
    ("open_risk", "Open risk"),
    ("deployed", "Deployed / Max"),
    ("win_pct", "Win %"),
    ("profit_factor", "Profit factor"),
    ("expectancy", "Expectancy"),
    ("r_stdev", "R / StDev"),
]


def _tone(v: Decimal | None) -> str | None:
    s = fmt.sign_of(v)
    return "positive" if s > 0 else "negative" if s < 0 else None


def compute_kpis(snap: PortfolioSnapshot, period: Period, period_key: PeriodKey | str = PeriodKey.YTD,
                 by: str = "lot", open_risk_warn: Decimal = Decimal("0.06")) -> dict[str, KpiData]:
    pf = snap.portfolio
    value = snap.value
    plabel = PERIOD_LABELS.get(PeriodKey(period_key), str(period_key))
    if by == "position":
        units = closed_position_units([pv.position for pv in snap.positions], period)
    else:
        units = closed_lot_units(snap.lots, period)
    st = trade_stats(units)
    realized, n_sells = realized_in_period(snap.lots, period)
    day = snap.day_change if snap.open_positions else None
    has_prices = any(p.last is not None for p in snap.open_positions)
    deployed_frac = safe_div(snap.deployed, pf.max_amount)
    risk_pct = snap.open_risk_pct
    unreal = snap.unrealized_pl if has_prices else None
    divs = [e for e in snap.cash_events if e.kind == CashKind.DIVIDEND and period.contains(e.event_date)]
    div_period = sum((e.amount for e in divs), Decimal(0))
    n_divs = len(divs)
    out = {
        "value": KpiData(
            fmt.money(value),
            f"{fmt.signed_money(day)} · {fmt.signed_pct(snap.day_change_pct, 2)} today" if day is not None else
            "No open positions" if not snap.open_positions else "",
            sub_tone=_tone(day),
            tooltip="Market value of open positions (cash is tracked separately)"),
        "dividends": KpiData(fmt.money(div_period), f"{n_divs} payment{'s' if n_divs != 1 else ''} · {plabel}"
                             + (f" · {fmt.money(snap.dividends)} all time" if snap.dividends != div_period else ""),
                             tooltip="Dividends received on this portfolio's stocks. They count toward each "
                                     "position's Total P/L; the cash itself can live in a separate cash portfolio."),
        "unrealized": KpiData(fmt.signed_money(unreal), f"{fmt.signed_pct(snap.unrealized_pct, 2)} of cost"
                              if unreal is not None else "", value_tone=_tone(unreal), sub_tone=None),
        "realized": KpiData(fmt.signed_money(realized), f"{n_sells} sell{'s' if n_sells != 1 else ''} · {plabel}",
                            value_tone=_tone(realized)),
        "open_risk": KpiData(fmt.money(snap.open_risk), f"{fmt.pct(risk_pct, 2)} of value",
                             value_warning=risk_pct is not None and risk_pct > open_risk_warn,
                             sub_tone="warning" if risk_pct is not None and risk_pct > open_risk_warn else None,
                             tooltip="Σ remaining × (last − current stop)"),
        "deployed": KpiData(fmt.compact_money(snap.deployed) if snap.deployed >= 10000 else fmt.money(snap.deployed),
                            f"of {fmt.money(pf.max_amount, 0)} · {fmt.pct(deployed_frac)}",
                            progress=float(deployed_frac) if deployed_frac is not None else 0.0,
                            value_warning=deployed_frac is not None and deployed_frac > 1,
                            tooltip="Cost basis of open lots vs. the portfolio's max amount"),
        "win_pct": KpiData(fmt.pct(st.win_pct), f"{st.wins}W – {st.losses}L · {plabel}"),
        "profit_factor": KpiData(fmt.ratio(st.profit_factor),
                                 "No losses yet" if st.losses == 0 and st.wins else
                                 f"{fmt.signed_money(st.gross_profit, 0)} / {fmt.signed_money(st.gross_loss, 0)}"
                                 if st.n else ""),
        "expectancy": KpiData(fmt.r_mult(st.expectancy), f"{st.n} trade{'s' if st.n != 1 else ''}"
                              f" ({'positions' if by == 'position' else 'lots'})",
                              value_tone=_tone(st.expectancy)),
        "r_stdev": KpiData(fmt.ratio(st.r_over_stdev), f"StDev {fmt.ratio(st.stdev)}"),
    }
    return out
