"""R math and the milestone ladders (PLAN §6.2). Pure Python, Decimal only.

For a lot with entry E, initial stop S0 and N original shares: R = E − S0, level(k) = E + k·R.
Each BUY chooses an exit plan; milestones are the levels where the user is asked to act.

Exit plan "1-3-6" (default, longer-term): take ¼ at 1R, 3R, 6R; trail the runner 3R behind in 3R steps.

| Milestone | Trigger (HWM ≥) | Suggested sell     | Ladder stop after |
| M0        | —               | —                  | E − 1R            |
| M1        | level(1)        | tranche 1 (25%)    | E − 0.5R          |
| M2        | level(2)        | —                  | E                 |
| M3        | level(3)        | tranche 2 (25%)    | E                 |
| M4        | level(6)        | tranche 3 (25%)    | level(3)          |
| T(3k)     | level(3k), k≥3  | —                  | level(3k − 3)     |

Exit plan "1-2-3" (shorter-term): take ¼ at 1R, 2R, 3R; trail the runner 1R behind each new whole R.

| M0 | —        | —         | E − 1R          |
| M1 | level(1) | tranche 1 | E               |
| M2 | level(2) | tranche 2 | level(1)        |
| M3 | level(3) | tranche 3 | level(2)        |
| Tk | level(k), k≥4 | —    | level(k − 1)    |

The "next target" shown to the user is always the next whole R above the high-water mark, whether or not that
level needs action (e.g. 1-3-6 at 4.2R → next target 5R, next action at 6R).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum

from .money import ZERO, floor_int

HALF = Decimal("0.5")


class ExitPlan(StrEnum):
    P136 = "1-3-6"
    P123 = "1-2-3"


DEFAULT_PLAN = ExitPlan.P136
EXIT_PLAN_LABELS = {
    ExitPlan.P136: "1-3-6 · sell ¼ at 1R, 3R, 6R · trail 3R",
    ExitPlan.P123: "1-2-3 · sell ¼ at 1R, 2R, 3R · trail 1R",
}


def as_plan(value: str | ExitPlan | None) -> ExitPlan:
    try:
        return ExitPlan(value) if value else DEFAULT_PLAN
    except ValueError:
        return DEFAULT_PLAN


@dataclass(frozen=True)
class Milestone:
    index: int  # 0 = M0, 1 = M1, ... 4 = M4, 5 = T9, 6 = T12, ...
    name: str
    trigger_k: Decimal | None  # R multiple that triggers it (None for M0)
    stop_k: Decimal  # ladder stop after reaching it, as an R multiple from entry
    tranche: int | None  # tranche number suggested to sell (1..3) or None

    @property
    def label(self) -> str:
        if self.trigger_k is None:
            return "Start"
        return f"{_k_str(self.trigger_k)}R"


def _k_str(k: Decimal) -> str:
    return str(int(k)) if k == k.to_integral_value() else str(k.normalize())


def milestone(index: int, plan: ExitPlan | str = DEFAULT_PLAN) -> Milestone:
    if index < 0:
        raise ValueError("milestone index must be ≥ 0")
    plan = as_plan(plan)
    if index == 0:
        return Milestone(0, "M0", None, Decimal(-1), None)
    if plan == ExitPlan.P123:
        if index <= 3:
            return Milestone(index, f"M{index}", Decimal(index), Decimal(index - 1), index)
        return Milestone(index, f"T{index}", Decimal(index), Decimal(index - 1), None)
    fixed = {
        1: Milestone(1, "M1", Decimal(1), -HALF, 1),
        2: Milestone(2, "M2", Decimal(2), ZERO, None),
        3: Milestone(3, "M3", Decimal(3), ZERO, 2),
        4: Milestone(4, "M4", Decimal(6), Decimal(3), 3),
    }
    if index in fixed:
        return fixed[index]
    trigger = Decimal(3 * (index - 2))  # 5 → 9, 6 → 12, ...
    return Milestone(index, f"T{int(trigger)}", trigger, trigger - 3, None)


def milestones(through: int, plan: ExitPlan | str = DEFAULT_PLAN) -> list[Milestone]:
    return [milestone(i, plan) for i in range(through + 1)]


def index_for_k(k: Decimal, plan: ExitPlan | str = DEFAULT_PLAN) -> int:
    """Highest milestone index whose trigger multiple is ≤ k (k = (HWM − E) / R)."""
    if as_plan(plan) == ExitPlan.P123:
        return max(0, floor_int(k))
    if k >= 6:
        return 2 + floor_int(k / 3)
    if k >= 3:
        return 3
    if k >= 2:
        return 2
    if k >= 1:
        return 1
    return 0


class ActionKind(StrEnum):
    STOP_HIT = "STOP_HIT"
    SELL_TRANCHE = "SELL_TRANCHE"
    RAISE_STOP = "RAISE_STOP"


ACTION_PRIORITY = {ActionKind.STOP_HIT: 0, ActionKind.SELL_TRANCHE: 1, ActionKind.RAISE_STOP: 2}


@dataclass(frozen=True)
class ActionDue:
    kind: ActionKind
    lot_id: int | None
    shares: Decimal | None = None  # SELL_TRANCHE / STOP_HIT
    price: Decimal | None = None  # suggested price (sell) or new stop (raise)
    milestone: str | None = None

    @property
    def priority(self) -> int:
        return ACTION_PRIORITY[self.kind]


@dataclass(frozen=True)
class Ladder:
    """Static ladder math for one lot."""

    entry: Decimal
    initial_stop: Decimal
    shares: Decimal  # N, original shares
    plan: ExitPlan = DEFAULT_PLAN

    def __post_init__(self) -> None:
        if self.entry - self.initial_stop <= 0:
            raise ValueError("Initial stop must be below entry (R must be > 0)")
        if self.shares <= 0:
            raise ValueError("Shares must be > 0")

    @property
    def r(self) -> Decimal:
        return self.entry - self.initial_stop

    def level(self, k: Decimal | int) -> Decimal:
        return self.entry + Decimal(k) * self.r

    @property
    def tranche(self) -> int:
        """q = floor(N / 4)."""
        return floor_int(self.shares / 4)

    @property
    def runner(self) -> Decimal:
        return self.shares - 3 * self.tranche

    def tranche_sizes(self) -> list[Decimal]:
        q = Decimal(self.tranche)
        return [q, q, q, self.runner]

    def next_tranche(self, sold: Decimal) -> Decimal:
        """Shares in the next single tranche (¼, the runner last), given shares already sold from the lot.
        A partly sold tranche suggests the rest of it. With N < 4 (q = 0) the whole remainder is one tranche."""
        remaining = self.shares - sold
        if remaining <= 0:
            return ZERO
        boundary = ZERO
        for size in self.tranche_sizes():
            boundary += size
            if boundary > sold:
                return min(boundary - sold, remaining)
        return remaining

    def expected_sold(self, m: Milestone | int) -> Decimal:
        i = m.index if isinstance(m, Milestone) else m
        q = Decimal(self.tranche)
        if i <= 0:
            return ZERO
        if self.plan == ExitPlan.P123:
            return min(i, 3) * q
        if i <= 2:
            return q
        if i == 3:
            return 2 * q
        return 3 * q

    def trigger(self, m: Milestone) -> Decimal | None:
        return None if m.trigger_k is None else self.level(m.trigger_k)

    def stop_for(self, m: Milestone) -> Decimal:
        return self.level(m.stop_k)

    def milestone(self, index: int) -> Milestone:
        return milestone(index, self.plan)

    def highest_reached(self, hwm: Decimal | None) -> Milestone:
        if hwm is None:
            return self.milestone(0)
        return self.milestone(index_for_k((hwm - self.entry) / self.r, self.plan))

    def next_whole_r(self, hwm: Decimal | None) -> int:
        """Next whole R multiple above the high-water mark (at least 1R)."""
        if hwm is None:
            return 1
        return max(1, floor_int((hwm - self.entry) / self.r) + 1)

    def r_multiple(self, price: Decimal) -> Decimal:
        return (price - self.entry) / self.r


def high_water_mark(entry_date: date, highs: Iterable[tuple[date, Decimal | None]],
                    last: Decimal | None) -> Decimal | None:
    """max(daily highs from entry date onward, last quote)."""
    best = last
    for d, h in highs:
        if h is None or d < entry_date:
            continue
        if best is None or h > best:
            best = h
    return best


@dataclass(frozen=True)
class LadderState:
    ladder: Ladder
    hwm: Decimal | None
    reached: Milestone
    next_milestone: Milestone  # next level where action will be suggested
    next_target: Decimal  # price of the next whole R (shown to the user)
    ladder_stop: Decimal
    current_stop: Decimal
    expected_sold: Decimal
    actual_sold: Decimal
    remaining: Decimal
    actions: list[ActionDue] = field(default_factory=list)
    next_r: int = 1

    @property
    def top_action(self) -> ActionDue | None:
        return min(self.actions, key=lambda a: a.priority) if self.actions else None


def evaluate_ladder(ladder: Ladder, *, lot_id: int | None, hwm: Decimal | None, last: Decimal | None,
                    current_stop: Decimal, actual_sold: Decimal) -> LadderState:
    """Compute milestone state and suggested actions for one lot."""
    reached = ladder.highest_reached(hwm)
    nxt = ladder.milestone(reached.index + 1)
    next_r = ladder.next_whole_r(hwm)
    next_target = ladder.level(next_r)
    ladder_stop = ladder.stop_for(reached)
    expected = ladder.expected_sold(reached)
    remaining = ladder.shares - actual_sold
    actions: list[ActionDue] = []
    if remaining > 0:
        if last is not None and last <= current_stop:
            actions.append(ActionDue(ActionKind.STOP_HIT, lot_id, remaining, last, reached.name))
        if actual_sold < expected:
            qty = min(expected - actual_sold, remaining)
            actions.append(ActionDue(ActionKind.SELL_TRANCHE, lot_id, qty, last, reached.name))
        if ladder_stop > current_stop:
            actions.append(ActionDue(ActionKind.RAISE_STOP, lot_id, None, ladder_stop, reached.name))
    return LadderState(ladder, hwm, reached, nxt, next_target, ladder_stop, current_stop, expected,
                       actual_sold, remaining, actions, next_r)
