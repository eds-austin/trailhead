"""Compare closed-trade performance by strategy, time frame or exit plan, or by strategy with each strategy's
time frames nested under it (pure: no Qt, no I/O).

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

GROUP_KEYS: dict[str, str] = {"strategy": "Strategy", "timeframe": "Time frame", "exit_plan": "Exit plan",
                              "strategy_timeframe": "Strategy × Time frame"}
TOTAL_LABELS: dict[str, str] = {"strategy": "All strategies", "timeframe": "All time frames",
                                "exit_plan": "All exit plans", "strategy_timeframe": "All strategies"}
# A grouping's tag fields, outermost first. Nested groupings list each outer row followed by its inner rows.
GROUP_FIELDS: dict[str, tuple[str, ...]] = {"strategy_timeframe": ("strategy", "timeframe")}
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
    parent: str | None = None  # the outer group's label, for a nested (inner) row

    @property
    def key(self) -> str:
        """Unique name across the table: "Pullback · Daily" for a nested row."""
        return f"{self.parent} · {self.label}" if self.parent else self.label

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
    def r_spread(self) -> tuple[Decimal, Decimal, Decimal] | None:
        """Worst, median and best R."""
        if not self.rs:
            return None
        rs = sorted(self.rs)
        mid = len(rs) // 2
        median = rs[mid] if len(rs) % 2 else (rs[mid - 1] + rs[mid]) / 2
        return rs[0], median, rs[-1]

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


def _order(r: GroupRow) -> tuple:
    return r.label == NONE_LABEL, -r.n, -r.open_count, r.label.lower()


def group_performance(lots: Iterable[Lot], positions: Iterable[Position], key: str, by: str = "lot",
                      period: Period | None = None) -> list[GroupRow]:
    """One row per tag value seen on a closed-in-period or open item, sorted by closed trades (most first),
    "(none)" last, then a total row. Open counts ignore the period. A nested grouping puts each outer row's
    inner rows (same order) right after it."""
    if key not in GROUP_KEYS:
        raise ValueError(f"Unknown group key {key!r}")
    fields = GROUP_FIELDS.get(key, (key,))
    items: list[Lot | Position] = list(positions) if by == "position" else list(lots)
    closed: dict[tuple[str, ...], list[tuple[TradeUnit, int]]] = defaultdict(list)
    open_count: dict[tuple[str, ...], int] = defaultdict(int)
    for it in items:
        tags = tuple(_tag(it, f) for f in fields)
        if not it.is_closed:
            open_count[tags] += 1
            continue
        if period is not None and not period.contains(it.close_date):
            continue
        c = _closed_item(it)
        if c is not None:
            closed[tags].append(c)

    def row(label: str, entries: list[tuple[TradeUnit, int]], n_open: int, total: bool = False,
            parent: str | None = None) -> GroupRow:
        units = [u for u, _ in entries]
        return GroupRow(label, trade_stats(units), n_open, [u.r for u in units if u.r is not None],
                        [d for _, d in entries], total, parent)

    all_tags = set(closed) | set(open_count)

    def level(prefix: tuple[str, ...]) -> list[GroupRow]:
        depth = len(prefix)
        rows = []
        for tag in {t[depth] for t in all_tags if t[:depth] == prefix}:
            p = (*prefix, tag)
            entries = [e for t, es in closed.items() if t[:depth + 1] == p for e in es]
            n_open = sum(n for t, n in open_count.items() if t[:depth + 1] == p)
            rows.append(row(tag, entries, n_open, parent=" · ".join(prefix) or None))
        rows.sort(key=_order)
        out = []
        for r in rows:
            out.append(r)
            if depth + 1 < len(fields):
                out += level((*prefix, r.label))
        return out

    rows = level(())
    if rows:
        everything = [e for es in closed.values() for e in es]
        rows.append(row(TOTAL_LABELS[key], everything, sum(open_count.values()), total=True))
    return rows
