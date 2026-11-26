"""PositionsTreeModel: position (parent) rows with trade / stop-change child rows (PLAN §7.3)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from PySide6.QtCore import QAbstractItemModel, QModelIndex, QPersistentModelIndex, Qt

from ...core.models import Side, StopChange, StopReason, Trade
from ...core.money import ZERO
from ...core.r_engine import ActionDue, ActionKind
from ...core.snapshot import PortfolioSnapshot, PositionView
from ...ui import format as fmt

CELL_ROLE = Qt.ItemDataRole.UserRole + 1  # Cell payload for the delegate
SORT_ROLE = Qt.ItemDataRole.UserRole + 2
ROW_STATE_ROLE = Qt.ItemDataRole.UserRole + 3  # "stop_hit" | "action" | "closed" | None
NODE_ROLE = Qt.ItemDataRole.UserRole + 4  # the _Node


@dataclass(frozen=True)
class Column:
    key: str
    title: str
    align: str = "r"  # l | r | c
    width: int = 90
    visible: bool = True
    tooltip: str = ""


COLUMNS: list[Column] = [
    Column("symbol", "Symbol", "l", 150),
    Column("sector", "Sector", "l", 130),
    Column("status", "Status", "l", 96),
    Column("strategy", "Strategy / TF", "l", 150),
    Column("shares", "Shares", "r", 92, tooltip="Open / original shares"),
    Column("avg_entry", "Avg entry", "r", 84),
    Column("last", "Last", "r", 96),
    Column("day_pct", "Day %", "r", 70),
    Column("mkt_value", "Mkt value", "r", 104),
    Column("pct_port", "% port.", "r", 66),
    Column("stop", "Stop", "r", 142, tooltip="Current stop (highest among open lots) and % distance from last"),
    Column("target", "Next target", "r", 142, tooltip="Next milestone target (lowest among open lots) and % distance"),
    Column("r_now", "R now", "r", 72, tooltip="Unrealized R of open lots"),
    Column("unreal", "Unreal. P/L", "r", 104),
    Column("real", "Real. P/L", "r", 100),
    Column("total", "Total P/L", "r", 104, tooltip="Unrealized + realized + dividends"),
    Column("open_risk", "Open risk", "r", 92, tooltip="Σ remaining × (last − current stop)"),
    Column("earnings", "Earnings", "l", 128),
    Column("div_yield", "Div yield", "r", 74, visible=False),
    Column("days", "Days", "r", 56),
    Column("action", "Action", "l", 260),
]
COL_INDEX = {c.key: i for i, c in enumerate(COLUMNS)}
# Bump when default column widths change so saved layouts pick up the new widths for those columns.
COLUMNS_REV = 4
RESIZED_IN_REV = {2: ("symbol", "stop", "target"), 3: ("action",), 4: ("status",)}


@dataclass
class Cell:
    text: str
    kind: str = "text"  # text | num | signed | muted | side | symbol | status | pill | action | earnings | last
    tone: str | None = None  # positive | negative | warning | faint ...
    sub: str = ""
    state: str = "normal"  # pill state: normal | near | hit
    badge: str | None = None
    data: Any = None
    italic: bool = False


@dataclass
class _Node:
    kind: str  # position | trade | stop
    pv: PositionView
    row: int
    trade: Trade | None = None
    stop: StopChange | None = None
    children: list[_Node] = field(default_factory=list)
    parent: _Node | None = None


def action_text(a: ActionDue | None, ladder_k: str | None = None) -> str:
    if a is None:
        return ""
    if a.kind == ActionKind.STOP_HIT:
        return f"Stop hit — sell {fmt.shares(a.shares)} sh"
    if a.kind == ActionKind.SELL_TRANCHE:
        return f"Sell {fmt.shares(a.shares)} sh @{ladder_k or a.milestone}"
    return f"Raise stop → ${fmt.price(a.price)}"


def _milestone_k(pv: PositionView, a: ActionDue) -> str | None:
    for lv in pv.lot_views:
        if lv.lot.id == a.lot_id:
            return lv.state.reached.label
    return None


def action_buttons(data) -> list[tuple[str, str]]:
    """Inline hover buttons for the Action cell. `data` is (PositionView, pending dividends)."""
    pv, divs = data
    kinds = {a.kind for a in pv.actions}
    out = []
    if ActionKind.STOP_HIT in kinds:
        out.append(("stopout", "Record stop-out"))
    elif ActionKind.SELL_TRANCHE in kinds:
        out.append(("sell", "Record sell"))
    if ActionKind.RAISE_STOP in kinds:
        out.append(("confirm", "Confirm stop"))
    if divs:
        out.append(("dividend", "Confirm div"))
    return out


def dividend_text(divs) -> str:
    total = sum((d.amount for d in divs), Decimal(0))
    return f"Dividend {fmt.money(total)}" + (f" ({len(divs)})" if len(divs) > 1 else "")


def _signed_tone(v: Decimal | None) -> str | None:
    s = fmt.sign_of(v)
    return "positive" if s > 0 else "negative" if s < 0 else None


class PositionsTreeModel(QAbstractItemModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._snapshot: PortfolioSnapshot | None = None
        self._nodes: list[_Node] = []
        self._filter = "open"
        self._search = ""
        self._sort: tuple[int, Qt.SortOrder] | None = None
        self.near_stop_pct = Decimal("0.02")
        self.pending_divs: dict[str, list] = {}  # position key → pending dividend proposals
        self.earnings_days = 7
        self.flag_missing_prices = True  # off in manual (offline) mode

    # ---------------------------------------------------------------- loading
    def set_snapshot(self, snap: PortfolioSnapshot | None) -> None:
        self._snapshot = snap
        self._rebuild()

    def snapshot(self) -> PortfolioSnapshot | None:
        return self._snapshot

    def set_filter(self, mode: str, search: str | None = None) -> None:
        self._filter = mode
        if search is not None:
            self._search = search.strip().upper()
        self._rebuild()

    def set_search(self, text: str) -> None:
        self._search = text.strip().upper()
        self._rebuild()

    def total_positions(self) -> int:
        return len(self._snapshot.positions) if self._snapshot else 0

    def _visible_positions(self) -> list[PositionView]:
        snap = self._snapshot
        if snap is None:
            return []
        out = []
        for pv in snap.positions:
            if self._search and self._search not in pv.symbol:
                continue
            if self._filter != "all" and pv.is_closed != (self._filter == "closed"):
                continue
            out.append(pv)
        return out

    def _rebuild(self) -> None:
        self.beginResetModel()
        pvs = self._visible_positions()
        if self._sort is not None:
            col, order = self._sort
            pvs = self._sorted(pvs, col, order, self.pending_divs)
        self._nodes = []
        for i, pv in enumerate(pvs):
            node = _Node("position", pv, i)
            items: list[tuple[tuple, _Node]] = []
            for t in pv.position.trades:
                items.append(((t.trade_date, 0 if t.side == Side.BUY else 1, t.id or 0),
                              _Node("trade", pv, 0, trade=t, parent=node)))
            items.sort(key=lambda x: x[0])
            for j, (_, child) in enumerate(items):
                child.row = j
                node.children.append(child)
            self._nodes.append(node)
        self.endResetModel()

    # ---------------------------------------------------------------- Qt model API
    def index(self, row: int, column: int, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> QModelIndex:  # noqa: B008
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        if not parent.isValid():
            return self.createIndex(row, column, self._nodes[row])
        pnode: _Node = parent.internalPointer()
        if pnode.kind != "position" or row >= len(pnode.children):
            return QModelIndex()
        return self.createIndex(row, column, pnode.children[row])

    def parent(self, index: QModelIndex | QPersistentModelIndex = QModelIndex()) -> QModelIndex:  # type: ignore[override]  # noqa: B008
        if not index.isValid():
            return QModelIndex()
        node: _Node = index.internalPointer()
        if node.parent is None:
            return QModelIndex()
        return self.createIndex(node.parent.row, 0, node.parent)

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: N802, B008
        if not parent.isValid():
            return len(self._nodes)
        if parent.column() != 0:
            return 0
        node: _Node = parent.internalPointer()
        return len(node.children) if node.kind == "position" else 0

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: N802, B008
        return len(COLUMNS)

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def headerData(self, section: int, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation != Qt.Orientation.Horizontal:
            return None
        col = COLUMNS[section]
        if role == Qt.ItemDataRole.DisplayRole:
            return col.title
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return _align(col.align)
        if role == Qt.ItemDataRole.ToolTipRole and col.tooltip:
            return col.tooltip
        return None

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        node: _Node = index.internalPointer()
        col = COLUMNS[index.column()]
        if role == NODE_ROLE:
            return node
        if role == ROW_STATE_ROLE:
            return self._row_state(node)
        if node.kind != "position":
            if role == CELL_ROLE:
                return child_cell(node, col.key)
            if role == Qt.ItemDataRole.DisplayRole:
                return child_cell(node, col.key).text
            if role == Qt.ItemDataRole.TextAlignmentRole:
                return _align(col.align)
            if role == Qt.ItemDataRole.ToolTipRole:
                return child_tooltip(node)
            return None
        if role == CELL_ROLE:
            return self._cell(node.pv, col.key)
        if role == Qt.ItemDataRole.DisplayRole:
            return self._cell(node.pv, col.key).text
        if role == SORT_ROLE:
            return self._sort_value_for(node.pv, col.key)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return _align(col.align)
        if role == Qt.ItemDataRole.ToolTipRole:
            return self._tooltip(node.pv, col.key)
        return None

    def sort(self, column: int, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder) -> None:
        self._sort = (column, order)
        self._rebuild()

    @staticmethod
    def _sorted(pvs: list[PositionView], column: int, order: Qt.SortOrder, divs: dict | None = None
                ) -> list[PositionView]:
        key = COLUMNS[column].key
        present = [p for p in pvs if PositionsTreeModel._sort_value(p, key, divs) is not None]
        missing = [p for p in pvs if PositionsTreeModel._sort_value(p, key, divs) is None]
        present.sort(key=lambda p: PositionsTreeModel._sort_value(p, key, divs),
                     reverse=order == Qt.SortOrder.DescendingOrder)
        return present + missing

    # ---------------------------------------------------------------- helpers
    def position_index(self, key: str) -> QModelIndex:
        for n in self._nodes:
            if n.pv.key == key:
                return self.index(n.row, 0)
        return QModelIndex()

    def position_keys(self) -> list[str]:
        return [n.pv.key for n in self._nodes]

    def node(self, index: QModelIndex) -> _Node | None:
        return index.internalPointer() if index.isValid() else None

    def _row_state(self, node: _Node) -> str | None:
        if node.kind != "position":
            return "child"
        pv = node.pv
        if pv.status == "STOP_HIT":
            return "stop_hit"
        if pv.is_closed:
            return "closed"
        if pv.top_action is not None:
            return "action"
        return None

    def _sort_value_for(self, pv: PositionView, key: str):
        return self._sort_value(pv, key, self.pending_divs)

    @staticmethod
    def _sort_value(pv: PositionView, key: str, divs: dict | None = None):
        divs = divs or {}
        closed = pv.is_closed  # closed rows show a dash for these, so they sort with the blanks
        stop_pct = pv.stop_distance[0]
        tgt_pct = pv.target_distance[0]
        return {
            "symbol": pv.symbol,
            "sector": pv.market.sector or "",
            "status": pv.status,
            "strategy": f"{pv.position.strategy or ''} {pv.position.timeframe or ''} {pv.position.exit_plan or ''}",
            "shares": pv.open_shares,
            "avg_entry": pv.position.avg_entry,
            "last": pv.last,
            "day_pct": None if closed else pv.day_pct,
            "mkt_value": None if closed else pv.market_value,
            "pct_port": pv.pct_of_portfolio,
            "stop": stop_pct,
            "target": tgt_pct,
            "r_now": pv.current_r,
            "unreal": None if closed else pv.unrealized_pl,
            "real": pv.realized_pl,
            "total": pv.total_pl,
            "open_risk": None if closed else pv.open_risk,
            "earnings": None if closed else pv.market.next_earnings,
            "div_yield": pv.market.div_yield,
            "days": pv.days_held,
            "action": pv.top_action.priority if pv.top_action else (9 if pv.key in divs else None),
        }[key]

    def _cell(self, pv: PositionView, key: str) -> Cell:
        closed = pv.is_closed
        if key == "symbol":
            return Cell(pv.symbol, "symbol")
        if key == "sector":
            return Cell(pv.market.sector or fmt.DASH, "muted")
        if key == "status":
            if pv.last is None and not closed and self.flag_missing_prices:
                return Cell("No price", "status", badge="stale")
            label = {"OPEN": "Open", "CLOSED": "Closed", "STOP_HIT": "Stop hit"}[pv.status]
            return Cell(label, "status", badge=pv.status.lower())
        if key == "strategy":
            parts = [p for p in (pv.position.strategy, pv.position.timeframe, pv.position.exit_plan) if p]
            return Cell(" · ".join(parts) if parts else fmt.DASH, "muted")
        if key == "shares":
            if closed:
                return Cell(f"0 / {fmt.shares(pv.position.original_shares)}", "num", "faint")
            return Cell(f"{fmt.shares(pv.open_shares)} / {fmt.shares(pv.position.original_shares)}", "num")
        if key == "avg_entry":
            return Cell(fmt.price(pv.position.avg_entry), "num")
        if key == "last":
            return Cell(fmt.price(pv.last), "last", badge="manual" if pv.market.is_manual else None)
        if key == "day_pct":
            v = None if closed else pv.day_pct
            return Cell(fmt.signed_pct(v), "signed", _signed_tone(v))
        if key == "mkt_value":
            return Cell(fmt.DASH if closed else fmt.money(pv.market_value), "num")
        if key == "pct_port":
            return Cell(fmt.pct(pv.pct_of_portfolio), "num")
        if key == "stop":
            if closed or pv.stop is None:
                return Cell(fmt.DASH, "num", "faint")
            pct, _r = pv.stop_distance
            state = "hit" if pv.stop_hit else (
                "near" if pv.last is not None and pv.last <= pv.stop * (1 + self.near_stop_pct) else "normal")
            sub = fmt.signed_pct(pct) if pct is not None else ""
            return Cell(fmt.price(pv.stop), "pill", sub=sub, state=state, data="stop")
        if key == "target":
            if closed or pv.next_target is None:
                return Cell(fmt.DASH, "num", "faint")
            pct, _r = pv.target_distance
            sub = fmt.signed_pct(pct) if pct is not None else ""
            return Cell(fmt.price(pv.next_target), "pill", sub=sub, data="target")
        if key == "r_now":
            v = pv.current_r
            return Cell(fmt.r_mult(v), "signed", _signed_tone(v))
        if key == "unreal":
            v = None if closed else pv.unrealized_pl
            return Cell(fmt.signed_money(v), "signed", _signed_tone(v))
        if key == "real":
            v = pv.realized_pl if pv.realized_pl != 0 or closed else None
            return Cell(fmt.signed_money(v), "signed", _signed_tone(v))
        if key == "total":
            v = pv.total_pl
            return Cell(fmt.signed_money(v), "signed", _signed_tone(v))
        if key == "open_risk":
            return Cell(fmt.DASH if closed else fmt.money(pv.open_risk), "num", "faint" if closed else None)
        if key == "earnings":
            d = pv.market.next_earnings
            if d is None or closed:
                return Cell(fmt.DASH, "muted")
            days = pv.earnings_soon_days
            if days is not None and 0 <= days <= self.earnings_days:
                return Cell(f"Earnings {fmt.month_day(d)}", "earnings", badge="earnings")
            return Cell(fmt.short_date(d), "muted")
        if key == "div_yield":
            return Cell(fmt.pct(pv.market.div_yield, 2), "num")
        if key == "days":
            return Cell(str(pv.days_held) if pv.days_held is not None else fmt.DASH, "num")
        if key == "action":
            a = pv.top_action
            divs = self.pending_divs.get(pv.key, [])
            if a is None and not divs:
                return Cell("", "action")
            if a is None:
                return Cell(dividend_text(divs), "action", badge="info", data=(pv, divs))
            kind = "stop_hit" if a.kind == ActionKind.STOP_HIT else "action"
            return Cell(action_text(a, _milestone_k(pv, a)), "action", badge=kind, data=(pv, divs))
        return Cell("")

    def _tooltip(self, pv: PositionView, key: str) -> str | None:
        if key == "symbol":
            name = pv.market.name
            return f"{pv.symbol} — {name}" if name else pv.symbol
        if key == "sector":
            if pv.market.sector_manual:
                return f"{pv.market.sector} (set manually) — double-click to change"
            return (f"{pv.market.sector} — double-click to change" if pv.market.sector
                    else "No sector yet — double-click to enter one")
        if key == "last" and pv.market.is_manual:
            return "Manual price override"
        if key in ("status", "last") and pv.last is None and not pv.is_closed:
            return (f"No price for {pv.symbol}. Yahoo Finance may spell it differently (BRK-B, not BRK.B): "
                    "double-click to find its symbol.")
        if key == "action" and (pv.actions or self.pending_divs.get(pv.key)):
            names = {lv.lot.id: f"lot #{lv.lot.no}" for lv in pv.lot_views}
            lines = [f"{action_text(a, _milestone_k(pv, a))} ({names.get(a.lot_id, '')})" for a in pv.actions]
            lines += [f"Dividend {fmt.money(d.amount)}: {fmt.shares(d.shares)} sh × {d.amount_per_share}"
                      f" (ex {fmt.short_date(d.ex_date)})" for d in self.pending_divs.get(pv.key, [])]
            return "\n".join(lines)
        if key == "stop" and pv.stop_view is not None:
            lv = pv.stop_view
            return (f"Current stop {fmt.price(lv.lot.current_stop)} (lot #{lv.lot.no})\n"
                    f"Ladder stop {fmt.price(lv.state.ladder_stop)} at {lv.state.reached.name}")
        if key == "target" and pv.target_view is not None:
            lv = pv.target_view
            st = lv.state
            action_px = fmt.price(lv.lot.ladder.trigger(st.next_milestone))
            return (f"Next target {st.next_r}R ({fmt.price(st.next_target)}) — lot #{lv.lot.no}, "
                    f"{lv.lot.exit_plan} plan\nNext action at {st.next_milestone.label} ({action_px})")
        return None


def _align(a: str) -> Qt.AlignmentFlag:
    h = {"l": Qt.AlignmentFlag.AlignLeft, "r": Qt.AlignmentFlag.AlignRight, "c": Qt.AlignmentFlag.AlignHCenter}[a]
    return h | Qt.AlignmentFlag.AlignVCenter


def _lot_view(node: _Node, lot_id: int | None):
    return next((lv for lv in node.pv.lot_views if lv.lot.id == lot_id), None)


def child_cell(node: _Node, key: str) -> Cell:
    """Cell for a BUY / SELL row: each value sits under the column whose heading it matches.
    Stop changes are not rows; they show in the BUY row's tooltip (and the lot's Stop cell)."""
    empty = Cell("", "text")
    t = node.trade
    assert t is not None
    note = Cell("✎ " + t.notes.splitlines()[0][:80], "muted", "faint") if t.notes else empty
    if t.side == Side.BUY:
        lv = _lot_view(node, t.id)
        lot = lv.lot if lv else None
        closed = lot is None or lot.is_closed
        unreal = None if closed or lv is None else lv.unrealized_pl
        r_now = None if lv is None else lv.current_r
        cells = {
            "symbol": Cell(fmt.short_date(t.trade_date), "num", "muted"),
            "sector": Cell(f"▲ BUY  #{lot.no if lot else '?'}", "side", "positive"),
            "strategy": Cell(" · ".join(x for x in (t.strategy, t.timeframe, lot.exit_plan if lot else None) if x)
                             or fmt.DASH, "muted"),
            "shares": Cell(fmt.shares(t.shares), "num"),  # shares bought in this transaction
            "avg_entry": Cell(fmt.price(t.price), "num"),
            "stop": Cell(fmt.price(lot.current_stop if lot else t.initial_stop), "num", "faint" if closed else "stop"),
            "r_now": Cell(fmt.r_mult(r_now) if r_now is not None else "", "signed", _signed_tone(r_now)),
            "unreal": Cell(fmt.signed_money(unreal) if unreal is not None else "", "signed", _signed_tone(unreal)),
            "open_risk": Cell(fmt.money(lv.open_risk) if lv is not None and not closed else "", "num"),
            "days": Cell(str((node.pv.today - t.trade_date).days) if node.pv.today and not closed else "", "num",
                         "muted"),
            "action": note,
        }
        return cells.get(key, empty)
    lv = _lot_view(node, t.lot_id)
    lot = lv.lot if lv else None
    pl = t.shares * (t.price - lot.entry) if lot else None
    rr = pl / (lot.shares * lot.r) if lot is not None and pl is not None else None
    cells = {
        "symbol": Cell(fmt.short_date(t.trade_date), "num", "muted"),
        "sector": Cell(f"▼ SELL  #{(lot.sell_number(t.id) if lot else None) or '?'}", "side", "negative"),
        "status": (Cell("Stop-out", "status", badge="negative") if t.is_stop_out
                   else Cell(f"lot #{lot.no if lot else '?'}", "muted")),
        "shares": Cell(fmt.shares(t.shares), "num"),
        "last": Cell(fmt.price(t.price), "num"),
        "r_now": Cell(fmt.r_mult(rr), "signed", _signed_tone(rr)),
        "real": Cell(fmt.signed_money(pl), "signed", _signed_tone(pl)),
        "action": note,
    }
    return cells.get(key, empty)


def _stop_history(lv) -> str:
    if lv is None:
        return ""
    lines = [f"\nStop → {fmt.price(sc.stop)} on {fmt.short_date(sc.effective_date)}"
             f" ({'ladder' if sc.reason == StopReason.LADDER_CONFIRMED else 'manual'})"
             for sc in sorted(lv.lot.stops, key=lambda x: (x.effective_date, x.id or 0))]
    return "".join(lines)


def child_tooltip(node: _Node) -> str | None:
    tip = _child_tooltip(node)
    t = node.trade
    if tip and t is not None and t.split_factor != 1:
        tip += (f"\nEntered as {fmt.shares(t.shares / t.split_factor)} @ {fmt.price(t.price * t.split_factor)}"
                " · shown in post-split shares")
    return tip


def _child_tooltip(node: _Node) -> str | None:
    t = node.trade
    if t is None:
        return None
    lv = _lot_view(node, t.id if t.side == Side.BUY else t.lot_id)
    lot_no = lv.lot.no if lv else "?"
    if t.side == Side.BUY and t.initial_stop is not None:
        r = t.price - t.initial_stop
        return (f"{t.symbol} BUY #{lot_no}: {fmt.shares(t.shares)} @ {fmt.price(t.price)}\n"
                f"Initial stop {fmt.price(t.initial_stop)}"
                f" · R/share {fmt.price(r)} · initial risk {fmt.money(r * t.shares)}"
                + _stop_history(lv) + (f"\n{t.notes}" if t.notes else ""))
    if t.side == Side.SELL:
        sell_no = (lv.lot.sell_number(t.id) if lv else None) or "?"
        return (f"{t.symbol} SELL #{sell_no}{' (stop-out)' if t.is_stop_out else ''}: "
                f"{fmt.shares(t.shares)} @ {fmt.price(t.price)} from lot #{lot_no}"
                + (f"\n{t.notes}" if t.notes else ""))
    return None


def lot_label(lot, today: date | None = None) -> str:
    rem = lot.remaining
    return (f"#{lot.no} · {fmt.short_date(lot.entry_date)} · {fmt.shares(rem)} rem · "
            f"@ {fmt.price(lot.entry)} · R {fmt.price(lot.r)}")


__all__ = [
    "CELL_ROLE",
    "COLUMNS",
    "COL_INDEX",
    "NODE_ROLE",
    "ROW_STATE_ROLE",
    "SORT_ROLE",
    "ZERO",
    "Cell",
    "PositionsTreeModel",
    "action_text",
    "child_cell",
    "dividend_text",
    "lot_label",
]
