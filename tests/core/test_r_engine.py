from datetime import date
from decimal import Decimal as D

import pytest

from trailhead.core.money import cents
from trailhead.core.r_engine import (
    ActionKind,
    ExitPlan,
    Ladder,
    as_plan,
    evaluate_ladder,
    high_water_mark,
    milestone,
)

GOLDEN = Ladder(D("43.18"), D("40.26"), D(50))


def test_golden_levels():
    assert GOLDEN.r == D("2.92")
    assert GOLDEN.level(1) == D("46.10")
    assert GOLDEN.level(2) == D("49.02")
    assert GOLDEN.level(3) == D("51.94")
    assert GOLDEN.level(6) == D("60.70")
    assert GOLDEN.stop_for(milestone(1)) == D("41.72")


def test_invalid_ladder():
    with pytest.raises(ValueError):
        Ladder(D(10), D(10), D(5))
    with pytest.raises(ValueError):
        Ladder(D(10), D(11), D(5))


@pytest.mark.parametrize("n,q,runner", [(50, 12, 14), (101, 25, 26), (3, 0, 3), (4, 1, 1), (100, 25, 25)])
def test_tranche_rounding(n, q, runner):
    lad = Ladder(D(10), D(9), D(n))
    assert lad.tranche == q
    assert lad.runner == runner
    assert sum(lad.tranche_sizes()) == n


# HWM sequence from PLAN §12: 45 → 46.10 → 49.02 → 51.94 → 60.70 → 69.46 → 78.22
# next target = next whole R; next action = next milestone of the 1-3-6 plan
SEQUENCE = [
    ("45", "M0", "46.10", "46.10", "40.26", 0),
    ("46.10", "M1", "49.02", "49.02", "41.72", 12),
    ("49.02", "M2", "51.94", "51.94", "43.18", 12),
    ("51.94", "M3", "54.86", "60.70", "43.18", 24),
    ("60.70", "M4", "63.62", "69.46", "51.94", 36),
    ("69.46", "T9", "72.38", "78.22", "60.70", 36),
    ("78.22", "T12", "81.14", "86.98", "69.46", 36),
]


@pytest.mark.parametrize("hwm,name,next_target,next_action,ladder_stop,expected", SEQUENCE)
def test_milestone_transitions(hwm, name, next_target, next_action, ladder_stop, expected):
    st = evaluate_ladder(GOLDEN, lot_id=1, hwm=D(hwm), last=D(hwm), current_stop=D("40.26"), actual_sold=D(0))
    assert st.reached.name == name
    assert st.next_target == D(next_target)
    assert GOLDEN.trigger(st.next_milestone) == D(next_action)
    assert st.ladder_stop == D(ladder_stop)
    assert st.expected_sold == expected


def test_136_no_action_between_milestones():
    """At 4R (between 3R and 6R) nothing new is due once 3R was handled; the next target is still 5R."""
    st = evaluate_ladder(GOLDEN, lot_id=1, hwm=GOLDEN.level(4), last=GOLDEN.level(4), current_stop=D("43.18"),
                         actual_sold=D(24))
    assert st.actions == []
    assert st.next_r == 5 and st.next_target == GOLDEN.level(5)


P123 = Ladder(D("43.18"), D("40.26"), D(50), ExitPlan.P123)


@pytest.mark.parametrize("k,name,stop_k,expected", [
    (0, "M0", -1, 0), (1, "M1", 0, 12), (2, "M2", 1, 24), (3, "M3", 2, 36), (4, "T4", 3, 36), (7, "T7", 6, 36),
])
def test_123_plan(k, name, stop_k, expected):
    hwm = P123.level(k) if k else D(45)
    st = evaluate_ladder(P123, lot_id=1, hwm=hwm, last=hwm, current_stop=D("40.26"), actual_sold=D(0))
    assert st.reached.name == name
    assert st.ladder_stop == P123.level(stop_k)
    assert st.expected_sold == expected
    assert st.next_r == max(1, k + 1)


def test_123_trails_one_r_each_whole_r():
    st = evaluate_ladder(P123, lot_id=1, hwm=P123.level(D("4.5")), last=P123.level(4), current_stop=P123.level(2),
                         actual_sold=D(36))
    assert [(a.kind, a.price) for a in st.actions] == [(ActionKind.RAISE_STOP, P123.level(3))]


def test_unknown_plan_falls_back_to_136():
    assert as_plan(None) == ExitPlan.P136 and as_plan("bogus") == ExitPlan.P136


def test_just_below_trigger_does_not_count():
    st = evaluate_ladder(GOLDEN, lot_id=1, hwm=D("46.09"), last=D("46"), current_stop=D("40.26"), actual_sold=D(0))
    assert st.reached.name == "M0"
    assert st.actions == []


def test_actions_m1():
    st = evaluate_ladder(GOLDEN, lot_id=7, hwm=D("46.50"), last=D("46.20"), current_stop=D("40.26"),
                         actual_sold=D(0))
    kinds = [a.kind for a in st.actions]
    assert kinds == [ActionKind.SELL_TRANCHE, ActionKind.RAISE_STOP]
    assert st.actions[0].shares == 12
    assert st.actions[1].price == D("41.72")
    assert st.top_action.kind == ActionKind.SELL_TRANCHE


def test_actions_cleared_after_sell_and_confirm():
    st = evaluate_ladder(GOLDEN, lot_id=7, hwm=D("46.50"), last=D("46.20"), current_stop=D("41.72"),
                         actual_sold=D(12))
    assert st.actions == []


def test_reached_milestone_persists_after_pullback():
    st = evaluate_ladder(GOLDEN, lot_id=7, hwm=D("49.50"), last=D("44"), current_stop=D("41.72"),
                         actual_sold=D(12))
    assert st.reached.name == "M2"
    assert [a.kind for a in st.actions] == [ActionKind.RAISE_STOP]
    assert st.actions[0].price == D("43.18")


def test_stop_hit_has_priority():
    st = evaluate_ladder(GOLDEN, lot_id=7, hwm=D("46.50"), last=D("41.50"), current_stop=D("41.72"),
                         actual_sold=D(0))
    assert st.top_action.kind == ActionKind.STOP_HIT
    assert st.top_action.shares == 50


def test_closed_lot_has_no_actions():
    st = evaluate_ladder(GOLDEN, lot_id=7, hwm=D("80"), last=D("30"), current_stop=D("40.26"), actual_sold=D(50))
    assert st.actions == []


def test_high_water_mark():
    highs = [(date(2024, 1, 1), D("99")), (date(2024, 1, 2), D("50")), (date(2024, 1, 3), D("52")),
             (date(2024, 1, 4), None)]
    assert high_water_mark(date(2024, 1, 2), highs, D("51")) == D("52")
    assert high_water_mark(date(2024, 1, 2), highs, D("53")) == D("53")
    assert high_water_mark(date(2024, 1, 5), [], None) is None


def test_odd_r_levels_exact():
    lad = Ladder(D("127.40"), D("121.00"), D(156))
    assert lad.level(1) == D("133.80")
    assert cents(lad.level(D("-0.5"))) == D("124.20")


@pytest.mark.parametrize("n,sold,expected", [
    (80, 0, 20), (80, 20, 20), (80, 40, 20), (80, 60, 20), (80, 70, 10), (80, 10, 10),
    (50, 36, 14), (50, 24, 12), (3, 0, 3), (3, 1, 2), (80, 80, 0),
])
def test_next_tranche_is_one_quarter_at_a_time(n, sold, expected):
    assert Ladder(D(10), D(9), D(n)).next_tranche(D(sold)) == expected
