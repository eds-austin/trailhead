from datetime import date
from decimal import Decimal as D

from trailhead.core.models import Portfolio
from trailhead.portfolio_types import totals as agg
from trailhead.portfolio_types.base import AttentionItem, DashboardSummary, Holding


def _row(pid, value, day=None, pl=None, income=D(0), risk=None, warn=False, curve=(), holdings=(), attention=()):
    pf = Portfolio(pid, f"P{pid}", D(1000), sort_order=pid)
    return DashboardSummary(pf, "", D(value), day, pl, None, income, risk, None, warn, list(curve), list(holdings),
                            list(attention))


def test_totals_skip_unknown_values():
    t = agg.totals([_row(1, 100, day=D(-5), pl=D(10), risk=D(3), warn=True), _row(2, 300, income=D(7))])
    assert (t.count, t.value, t.day_change, t.period_pl, t.income, t.open_risk) == (2, 400, -5, 10, 7, 3)
    assert t.open_risk_warn
    assert t.day_change_pct == D(-5) / D(405)
    assert agg.totals([_row(1, 100)]).day_change is None


def test_combine_curves_carries_values_forward():
    a = [(date(2026, 1, 1), D(0)), (date(2026, 1, 3), D(10))]
    b = [(date(2026, 1, 2), D(5)), (date(2026, 1, 4), D(-1))]
    assert agg.combine_curves([a, b, []]) == [
        (date(2026, 1, 1), D(0)), (date(2026, 1, 2), D(5)), (date(2026, 1, 3), D(15)), (date(2026, 1, 4), D(9))]
    assert agg.combine_curves([]) == []


def test_top_moves_by_size_then_gainers_first():
    hs = [Holding("A", D(1), D(1), D("0.01")), Holding("B", D(1), D(-1), D("-0.05")),
          Holding("C", D(1), D(1), D("0.03")), Holding("D", D(1), None, None), Holding("E", D(1), D(0), D(0))]
    moves = agg.top_moves([_row(1, 5, holdings=hs)], n=2)
    assert [h.symbol for h, _ in moves] == ["C", "B"]


def test_sector_allocation_and_attention_order():
    r1 = _row(1, 10, holdings=[Holding("A", D(6), sector="Energy"), Holding("B", D(4))],
              attention=[AttentionItem("positive", "ok"), AttentionItem("warning", "risk", sort_key=(3,))])
    r2 = _row(2, 5, holdings=[Holding("C", D(5), sector="Energy")],
              attention=[AttentionItem("negative", "stop", sort_key=(0, "C"))])
    items, names = agg.sector_allocation([r1, r2, _row(3, 0)])
    assert items == [("Energy", D(11)), ("Unknown", D(4))] and names == ["P1", "P2"]
    assert [a.title for a, _ in agg.attention([r1, r2])] == ["stop", "risk", "ok"]
