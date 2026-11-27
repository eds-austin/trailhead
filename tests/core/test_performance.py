from datetime import date
from decimal import Decimal as D

import pytest

from trailhead.core.performance import NONE_LABEL, group_performance
from trailhead.core.positions import build_lots, build_positions
from trailhead.core.stats import Period

from .conftest import buy, sell


def _book():
    """R = 10 on every lot (entry 100, stop 90, 10 sh → $100 = 1R)."""
    trades = [
        buy(1, "AAA", date(2026, 1, 5), 10, 100, 90, strategy="Pullback", timeframe="Weekly/Daily"),
        sell(2, "AAA", date(2026, 1, 15), 10, 120, 1),  # +2R, 10 days
        buy(3, "BBB", date(2026, 2, 2), 10, 100, 90, strategy="Pullback", timeframe="Weekly/Daily",
            exit_plan="1-2-3"),
        sell(4, "BBB", date(2026, 2, 12), 10, 95, 3),  # −0.5R
        buy(5, "CCC", date(2026, 3, 2), 10, 100, 90, strategy="Breakout"),
        sell(6, "CCC", date(2026, 3, 22), 10, 110, 5),  # +1R
        buy(7, "DDD", date(2026, 4, 1), 10, 100, 90),  # open, untagged
        buy(8, "AAA", date(2025, 6, 2), 10, 100, 90, strategy="Breakout"),
        sell(9, "AAA", date(2025, 6, 9), 10, 90, 8),  # −1R last year
    ]
    lots = build_lots(trades, [])
    return lots, build_positions(lots)


def test_group_by_strategy_lots():
    lots, positions = _book()
    rows = group_performance(lots, positions, "strategy", "lot")
    assert [r.label for r in rows] == ["Breakout", "Pullback", NONE_LABEL, "All strategies"]
    brk, pull, none, total = rows
    assert (brk.n, brk.open_count, sorted(brk.rs)) == (2, 0, [D(-1), D(1)])
    assert (pull.n, pull.stats.wins, pull.stats.losses) == (2, 1, 1)
    assert pull.avg_win_r == D(2) and pull.avg_loss_r == D("-0.5") and pull.stats.expectancy == D("0.75")
    assert pull.days == [10, 10] and pull.low_sample
    lo, hi = pull.range
    assert hi - pull.stats.expectancy == pull.half_range == 2 * pull.stats.stdev / D(2).sqrt()
    assert lo < 0 < hi
    assert (none.n, none.open_count, none.range) == (0, 1, None) and not none.low_sample
    assert total.is_total and total.n == 4 and total.open_count == 1


def test_period_filters_closed_but_not_open():
    lots, positions = _book()
    ytd = Period(date(2026, 1, 1), date(2026, 12, 31))
    rows = group_performance(lots, positions, "strategy", "lot", ytd)
    brk = next(r for r in rows if r.label == "Breakout")
    assert brk.rs == [D(1)]
    assert rows[-1].n == 3 and rows[-1].open_count == 1


def test_exit_plan_defaults_and_position_mode_mixed():
    lots, positions = _book()
    plans = group_performance(lots, positions, "exit_plan", "lot")
    assert [r.label for r in plans] == ["1-3-6", "1-2-3", "All exit plans"]
    # an add-on with another strategy while the first lot is open makes one "Mixed" position
    mixed = build_lots([
        buy(1, "EEE", date(2026, 5, 1), 10, 100, 90, strategy="Pullback"),
        buy(2, "EEE", date(2026, 5, 4), 10, 105, 95, strategy="Breakout"),
        sell(3, "EEE", date(2026, 5, 11), 10, 110, 1),
        sell(4, "EEE", date(2026, 5, 21), 10, 115, 2),
    ], [])
    pos = group_performance(mixed, build_positions(mixed), "strategy", "position")
    assert [(r.label, r.n, r.days) for r in pos] == [("Mixed", 1, [20]), ("All strategies", 1, [20])]
    assert [r.label for r in group_performance(mixed, [], "strategy", "lot")][:2] == ["Breakout", "Pullback"]


def test_unknown_key_and_empty():
    with pytest.raises(ValueError):
        group_performance([], [], "sector")
    assert group_performance([], [], "strategy") == []


def test_strategy_by_timeframe_nests_inner_rows():
    lots, positions = _book()
    rows = group_performance(lots, positions, "strategy_timeframe", "lot")
    assert [(r.key, r.n, r.open_count) for r in rows] == [
        ("Breakout", 2, 0), ("Breakout · (none)", 2, 0),
        ("Pullback", 2, 0), ("Pullback · Weekly/Daily", 2, 0),
        (NONE_LABEL, 0, 1), (f"{NONE_LABEL} · {NONE_LABEL}", 0, 1),
        ("All strategies", 4, 1),
    ]
    assert rows[1].label == NONE_LABEL and rows[1].parent == "Breakout"
    assert rows[3].rs == rows[2].rs


def test_r_spread():
    lots, positions = _book()
    pull = group_performance(lots, positions, "strategy", "lot")[1]
    assert pull.r_spread == (D("-0.5"), D("0.75"), D(2))
