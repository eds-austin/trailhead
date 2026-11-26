"""Compare closed-trade performance by strategy, time frame or exit plan (pure: no Qt, no I/O).

Tags live on each BUY, so a lot is the natural unit. In position mode a position takes its lots' tag, or "Mixed"
when they differ (see Position._uniform).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .positions import Lot, Position
from .stats import Period, TradeStats, TradeUnit, trade_stats

GROUP_KEYS: dict[str, str] = {"strategy": "Strategy", "timeframe": "Time frame", "exit_plan": "Exit plan"}
TOTAL_LABELS: dict[str, str] = {"strategy": "All strategies", "timeframe": "All time frames",
                                "exit_plan": "All exit plans"}
NONE_LABEL = "(none)"
LOW_SAMPLE = 10  # fewer closed trades than this → LOW SAMPLE badge
RANGE_SE = 2  # range = expectancy ± 2 standard errors (≈95%)


@dataclass(frozen=True)
class GroupRow:
    label: str
    stats: TradeStats
    open_count: int
    rs: list[Decimal] = field(default_factory=list)
    days: list[int] = field(default_factory=list)
    is_total: bool = False

    @property
    def n(self) -> int:
        return self.stats.n

    @property
    def low_sample(self) -> bool:
        return 0 < self.stats.n < LOW_SAMPLE

    @property
    def avg_win_r(self) -> Decimal | None:
        wins = [r for r in self.rs if r > 0]
        return sum(wins, Decimal(0)) / len(wins) if wins else None

    @property
    def avg_loss_r(self) -> Decimal | None:
        losses = [r for r in self.rs if r < 0]
        return sum(losses, Decimal(0)) / len(losses) if losses else None

    @property
    def std_error(self) -> Decimal | None:
        sd = self.stats.stdev
        return sd / Decimal(len(self.rs)).sqrt() if sd is not None and self.rs else None

    @property
    def half_range(self) -> Decimal | None:
        se = self.std_error
        return se * RANGE_SE if se is not None else None

    @property
    def range(self) -> tuple[Decimal, Decimal] | None:
        e, h = self.stats.expectancy, self.half_range
        return (e - h, e + h) if e is not None and h is not None else None

    @property
    def avg_days(self) -> Decimal | None:
        return Decimal(sum(self.days)) / len(self.days) if self.days else None


def _tag(item: Lot | Position, key: str) -> str:
    return getattr(item, key) or NONE_LABEL


def _closed_item(item: Lot | Position) -> tuple[TradeUnit, int] | None:
    close = item.close_date
    if close is None:
        return None
    start: date = item.entry_date if isinstance(item, Lot) else item.open_date
    return TradeUnit(item.realized_pl, item.realized_r, close), (close - start).days


def group_performance(lots: Iterable[Lot], positions: Iterable[Position], key: str, by: str = "lot",
                      period: Period | None = None) -> list[GroupRow]:
    """One row per tag value seen on a closed-in-period or open item, sorted by closed trades (most first),
    "(none)" last, then a total row. Open counts ignore the period."""
    if key not in GROUP_KEYS:
        raise ValueError(f"Unknown group key {key!r}")
    items: list[Lot | Position] = list(positions) if by == "position" else list(lots)
    closed: dict[str, list[tuple[TradeUnit, int]]] = defaultdict(list)
    open_count: dict[str, int] = defaultdict(int)
    for it in items:
        tag = _tag(it, key)
        if not it.is_closed:
            open_count[tag] += 1
            continue
        if period is not None and not period.contains(it.close_date):
            continue
        c = _closed_item(it)
        if c is not None:
            closed[tag].append(c)

    def row(label: str, entries: list[tuple[TradeUnit, int]], n_open: int, total: bool = False) -> GroupRow:
        units = [u for u, _ in entries]
        return GroupRow(label, trade_stats(units), n_open, [u.r for u in units if u.r is not None],
                        [d for _, d in entries], total)

    tags = set(closed) | set(open_count)
    rows = [row(t, closed.get(t, []), open_count.get(t, 0)) for t in tags]
    rows.sort(key=lambda r: (r.label == NONE_LABEL, -r.n, -r.open_count, r.label.lower()))
    if rows:
        everything = [e for t in tags for e in closed.get(t, [])]
        rows.append(row(TOTAL_LABELS[key], everything, sum(open_count.values()), total=True))
    return rows
