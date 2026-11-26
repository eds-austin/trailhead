"""HoldingsTreeModel: one row per holding, with its BUY / DRIP / SELL trades and dividend payments as child rows.

Cells use the Stock Trading table's Cell payload and roles, so the same view and delegate paint them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from PySide6.QtCore import QAbstractItemModel, QModelIndex, QPersistentModelIndex, Qt

from ...core.holdings import HoldingsSnapshot, HoldingView
from ...core.metals import display_symbol, is_bullion, quantity_unit
from ...core.models import CashEvent, Side, Trade
from ...core.splits import looks_like_spinoff
from ...ui import format as fmt
from ..stock_trading.table_model import CELL_ROLE, NODE_ROLE, ROW_STATE_ROLE, SORT_ROLE, Cell, Column, dividend_text

COLUMNS: list[Column] = [
    Column("symbol", "Symbol", "l", 150),
    Column("type", "Type", "l", 118, tooltip="Investment type"),
    Column("sector", "Sector", "l", 130, visible=False),
    Column("status", "Status", "l", 96),
    Column("shares", "Shares", "r", 92),
    Column("avg_cost", "Avg cost", "r", 84),
    Column("last", "Last", "r", 96),
    Column("day_pct", "Day %", "r", 70),
    Column("mkt_value", "Mkt value", "r", 108),
    Column("pct_port", "% port.", "r", 66),
    Column("cost_basis", "Cost basis", "r", 104, visible=False, tooltip="Cost of the shares held"),
    Column("unreal", "Unreal. P/L", "r", 104),
    Column("real", "Real. P/L", "r", 100, visible=False),
    Column("dividends", "Dividends", "r", 96, tooltip="Dividends received while held"),
    Column("total", "Total return", "r", 110, tooltip="Unrealized + realized P/L + dividends received"),
    Column("total_pct", "Return %", "r", 78,
           tooltip="Total return ÷ money invested (every buy except reinvested dividends)"),
    Column("div_yield", "Yield", "r", 64, tooltip="Annual dividend ÷ last price"),
    Column("yoc", "YoC", "r", 64, tooltip="Yield on cost: estimated annual income ÷ cost basis"),
    Column("income", "Income / yr", "r", 96, tooltip="Shares × annual dividend per share"),
    Column("next_div", "Next div", "l", 100, tooltip="Next ex-dividend date (or pay date once ex has passed). "
                                                     "~ = estimated from past payments"),
    Column("stop", "Stop", "r", 128, tooltip="Optional stop and % distance from last"),
    Column("target", "Target", "r", 128, tooltip="Optional profit target and % distance from last"),
    Column("days", "Days", "r", 56, visible=False),
    Column("action", "Action", "l", 220),
]
COL_INDEX = {c.key: i for i, c in enumerate(COLUMNS)}


@dataclass
class _Node:
    kind: str  # position | trade | dividend
    pv: HoldingView
    row: int
    trade: Trade | None = None
    event: CashEvent | None = None
    split: Any = None
    children: list[_Node] = field(default_factory=list)
    parent: _Node | None = None


def action_buttons(data) -> list[tuple[str, str]]:
    """Inline hover buttons for the Action cell. `data` is (HoldingView, pending dividends, pending splits)."""
    h, divs, splits = data
    out = []
    if splits:
        spin = any(looks_like_spinoff(s.ratio) for s in splits)
        out.append(("spinoff", "Record spin-off") if spin else ("split", "Review split"))
    if h.last is None and not h.is_closed and not is_bullion(h.symbol):
        out.append(("symbol", "Find symbol"))
    if h.stop_hit or h.target_hit:
        out.append(("sell", "Record sell"))
    if divs:
        out.append(("dividend", "Confirm div"))
    return out


def _tone(v: Decimal | None) -> str | None:
    s = fmt.sign_of(v)
    return "positive" if s > 0 else "negative" if s < 0 else None


def _signed(v: Decimal | None) -> Cell:
    return Cell(fmt.signed_money(v), "signed", _tone(v))


class HoldingsTreeModel(QAbstractItemModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._snapshot: HoldingsSnapshot | None = None
        self._nodes: list[_Node] = []
        self._filter = "open"
        self._search = ""
        self._sort: tuple[int, Qt.SortOrder] | None = None
        self.pending_divs: dict[str, list] = {}
        self.pending_splits: dict[str, list] = {}
        self.flag_missing_prices = True  # off in manual (offline) mode, where prices are entered by hand

    # ---------------------------------------------------------------- loading
    def set_snapshot(self, snap: HoldingsSnapshot | None) -> None:
        self._snapshot = snap
        self._rebuild()

    def set_filter(self, mode: str) -> None:
        self._filter = mode
        self._rebuild()

    def set_search(self, text: str) -> None:
        self._search = text.strip().upper()
        self._rebuild()

    def total_positions(self) -> int:
        return len(self._snapshot.holdings) if self._snapshot else 0

    def _visible(self) -> list[HoldingView]:
        snap = self._snapshot
        if snap is None:
            return []
        out = []
        for h in snap.holdings:
            if self._search and self._search not in h.symbol and self._search not in display_symbol(h.symbol).upper():
                continue
            if self._filter != "all" and h.is_closed != (self._filter == "closed"):
                continue
            out.append(h)
        return out

    def _rebuild(self) -> None:
        self.beginResetModel()
        hs = self._visible()
        if self._sort is not None:
            col, order = self._sort
            key = COLUMNS[col].key
            present = [h for h in hs if self._sort_value(h, key) is not None]
            missing = [h for h in hs if self._sort_value(h, key) is None]
            present.sort(key=lambda h: self._sort_value(h, key), reverse=order == Qt.SortOrder.DescendingOrder)
            hs = present + missing
        self._nodes = []
        for i, h in enumerate(hs):
            node = _Node("position", h, i)
            items: list[tuple[tuple, _Node]] = []
            for t in h.position.trades:
                order = (2 if t.drip else 0) if t.side == Side.BUY else 3  # a dividend precedes its DRIP buy
                items.append(((t.trade_date, order, t.id or 0),
                              _Node("trade", h, 0, trade=t, parent=node)))
            for e in h.payments:
                items.append(((e.event_date, 1, e.id or 0), _Node("dividend", h, 0, event=e, parent=node)))
            for sp in h.splits:
                items.append(((sp.ex_date, -1, 0), _Node("split", h, 0, split=sp, parent=node)))
            for so in h.spinoffs:
                items.append(((so.ex_date, -1, 1), _Node("spinoff", h, 0, split=so, parent=node)))
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
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return _align(col.align)
        if node.kind != "position":
            if role in (CELL_ROLE, Qt.ItemDataRole.DisplayRole):
                c = child_cell(node, col.key)
                return c if role == CELL_ROLE else c.text
            if role == Qt.ItemDataRole.ToolTipRole:
                return child_tooltip(node)
            return None
        if role in (CELL_ROLE, Qt.ItemDataRole.DisplayRole):
            c = self._cell(node.pv, col.key)
            return c if role == CELL_ROLE else c.text
        if role == SORT_ROLE:
            return self._sort_value(node.pv, col.key)
        if role == Qt.ItemDataRole.ToolTipRole:
            return self._tooltip(node.pv, col.key)
        return None

    def sort(self, column: int, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder) -> None:
        self._sort = (column, order)
        self._rebuild()

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
        h = node.pv
        if h.stop_hit:
            return "stop_hit"
        if h.is_closed:
            return "closed"
        if h.target_hit:
            return "action"
        return None

    def _sort_value(self, h: HoldingView, key: str):
        nd = h.next_dividend
        return {
            "symbol": h.symbol,
            "type": h.asset_type or "",
            "sector": h.market.sector or "",
            "status": h.status,
            "shares": h.open_shares,
            "avg_cost": h.avg_cost,
            "last": h.last,
            "day_pct": None if h.is_closed else h.day_pct,
            "mkt_value": h.market_value,
            "pct_port": h.pct_of_portfolio,
            "cost_basis": h.cost_basis,
            "unreal": None if h.is_closed else h.unrealized_pl,
            "real": h.realized_pl,
            "dividends": h.dividends,
            "total": h.total_return,
            "total_pct": h.total_return_pct,
            "div_yield": h.div_yield,
            "yoc": h.yield_on_cost,
            "income": h.est_income,
            "next_div": nd.date if nd else None,
            "stop": h.distance(h.stop),
            "target": h.distance(h.target),
            "days": h.days_held,
            "action": (0 if h.stop_hit else 1 if h.target_hit else 2 if h.key in self.pending_splits
                       else 3 if h.key in self.pending_divs else None),
        }[key]

    def _cell(self, h: HoldingView, key: str) -> Cell:
        closed = h.is_closed
        if key == "symbol":
            return Cell(display_symbol(h.symbol), "symbol")
        if key == "type":
            return Cell(h.asset_type or fmt.DASH, "muted")
        if key == "sector":
            return Cell(h.market.sector or fmt.DASH, "muted")
        if key == "status":
            if h.last is None and not closed and self.flag_missing_prices:
                return Cell("No price", "status", badge="stale")
            label, badge = {"OPEN": ("Open", "open"), "CLOSED": ("Closed", "closed"),
                            "STOP_HIT": ("Stop hit", "stop_hit"), "TARGET": ("Target hit", "target")}[h.status]
            return Cell(label, "status", badge=badge)
        if key == "shares":
            unit = " oz" if is_bullion(h.symbol) else ""
            return Cell(fmt.shares(h.open_shares) + unit, "num", "faint" if closed else None)
        if key == "avg_cost":
            return Cell(fmt.price(h.avg_cost), "num")
        if key == "last":
            return Cell(fmt.price(h.last), "last", badge="manual" if h.market.is_manual else None)
        if key == "day_pct":
            v = None if closed else h.day_pct
            return Cell(fmt.signed_pct(v), "signed", _tone(v))
        if key == "mkt_value":
            return Cell(fmt.DASH if closed else fmt.money(h.market_value), "num")
        if key == "pct_port":
            return Cell(fmt.pct(h.pct_of_portfolio), "num")
        if key == "cost_basis":
            return Cell(fmt.DASH if closed else fmt.money(h.cost_basis), "num")
        if key == "unreal":
            return _signed(None if closed else h.unrealized_pl)
        if key == "real":
            return _signed(h.realized_pl if h.realized_pl or closed else None)
        if key == "dividends":
            return Cell(fmt.money(h.dividends) if h.dividends else fmt.DASH, "num")
        if key == "total":
            return _signed(h.total_return)
        if key == "total_pct":
            v = h.total_return_pct
            return Cell(fmt.signed_pct(v), "signed", _tone(v))
        if key == "div_yield":
            return Cell(fmt.DASH if closed else fmt.pct(h.div_yield, 2), "num")
        if key == "yoc":
            return Cell(fmt.pct(h.yield_on_cost, 2), "num")
        if key == "income":
            v = h.est_income
            return Cell(fmt.money(v) if v else fmt.DASH, "num")
        if key == "next_div":
            nd = h.next_dividend
            if nd is None or nd.date is None:
                return Cell(fmt.DASH, "num")
            text = ("~" if nd.estimated else "") + _md(nd.date) + ("" if nd.ex_date else " pay")
            return Cell(text, "num", "muted" if nd.estimated else None)  # same font; estimates muted with ~
        if key in ("stop", "target"):
            level = h.stop if key == "stop" else h.target
            if closed or level is None:
                return Cell(fmt.DASH, "num", "faint")
            pct = h.distance(level)
            state = "hit" if (h.stop_hit if key == "stop" else h.target_hit) else "normal"
            return Cell(fmt.price(level), "pill", sub=fmt.signed_pct(pct) if pct is not None else "", state=state,
                        data=key)
        if key == "days":
            return Cell(str(h.days_held) if h.days_held is not None else fmt.DASH, "num")
        if key == "action":
            divs = self.pending_divs.get(h.key, [])
            splits = self.pending_splits.get(h.key, [])
            data = (h, divs, splits)
            if h.stop_hit:
                return Cell(f"Stop hit — {fmt.price(h.stop)}", "action", badge="stop_hit", data=data)
            if h.target_hit:
                return Cell(f"Target reached — {fmt.price(h.target)}", "action", badge="target", data=data)
            if splits:
                if looks_like_spinoff(splits[0].ratio):
                    return Cell(f"Spin-off {_md(splits[0].ex_date)}?", "action", badge="action", data=data)
                return Cell(f"Split {splits[0].label}?", "action", badge="action", data=data)
            if divs:
                return Cell(dividend_text(divs), "action", badge="info", data=data)
            if h.last is None and not closed and self.flag_missing_prices:
                return Cell("No price", "action", badge="stale", data=data)
            return Cell("", "action")
        return Cell("")

    def _tooltip(self, h: HoldingView, key: str) -> str | None:
        if key == "symbol":
            if is_bullion(h.symbol):
                return (f"{display_symbol(h.symbol)} bullion in troy ounces, valued at the {h.symbol} futures price "
                        "(close to spot). Avg cost and Last are per ounce.")
            return f"{h.symbol} — {h.market.name}" if h.market.name else h.symbol
        if key == "last" and h.market.is_manual:
            return "Manual price override"
        if key in ("status", "last") and h.last is None and not h.is_closed and is_bullion(h.symbol):
            return f"No price for {display_symbol(h.symbol)} yet: refresh prices (F5) or set one manually."
        if key in ("status", "last") and h.last is None and not h.is_closed:
            return (f"No price for {h.symbol}. Yahoo Finance may spell it differently (BRK-B, not BRK.B): "
                    "double-click to find its symbol.")
        if key == "total":
            return (f"Unrealized {fmt.signed_money(h.unrealized_pl)} + realized {fmt.signed_money(h.realized_pl)}"
                    f" + dividends {fmt.money(h.dividends)}\nInvested {fmt.money(h.invested)}"
                    + (f" · reinvested dividends {fmt.money(h.reinvested)}" if h.reinvested else ""))
        if key in ("income", "div_yield", "yoc"):
            a = h.annual_per_share
            if a is None:
                return "No dividend data yet"
            src = "provider's annual rate" if h.market.annual_dividend else "last 12 months"
            return f"{fmt.number(a, 4)} per share per year ({src})"
        if key == "next_div":
            nd = h.next_dividend
            if nd is None:
                return None
            parts = []
            if nd.ex_date:
                parts.append(f"Ex-date {fmt.short_date(nd.ex_date)}")
            if nd.pay_date:
                parts.append(f"pays {fmt.short_date(nd.pay_date)}")
            if nd.per_share is not None:
                parts.append(f"~{fmt.number(nd.per_share, 4)}/sh ≈ {fmt.money(nd.per_share * h.open_shares)}")
            return " · ".join(parts) + ("\nEstimated from past payments" if nd.estimated else "")
        if key == "action":
            lines = []
            if h.stop_hit:
                lines.append(f"Last {fmt.price(h.last)} ≤ stop {fmt.price(h.stop)}")
            if h.target_hit:
                lines.append(f"Last {fmt.price(h.last)} ≥ target {fmt.price(h.target)}")
            if h.last is None and not h.is_closed and not is_bullion(h.symbol):
                lines.append(f"No price for {h.symbol}. If Yahoo Finance spells it differently (BRK-B, not "
                             "BRK.B), Find symbol renames it.")
            lines += [(f"Yahoo shows a {sp.label} 'split' on {fmt.short_date(sp.ex_date)}: probably a spin-off. "
                       "Record it with the new company's symbol and shares."
                       if looks_like_spinoff(sp.ratio) else
                       f"{sp.symbol} had a {sp.label} split on {fmt.short_date(sp.ex_date)} — review it")
                      for sp in self.pending_splits.get(h.key, [])]
            lines += [f"Dividend {fmt.money(d.amount)}: {fmt.shares(d.shares)} sh × {d.amount_per_share}"
                      f" (ex {fmt.short_date(d.ex_date)})" for d in self.pending_divs.get(h.key, [])]
            return "\n".join(lines) or None
        return None


def _unit(symbol: str) -> str:
    return " oz" if is_bullion(symbol) else ""


def _md(d) -> str:
    return f"{d:%b} {d.day}"


def _align(a: str) -> Qt.AlignmentFlag:
    h = {"l": Qt.AlignmentFlag.AlignLeft, "r": Qt.AlignmentFlag.AlignRight, "c": Qt.AlignmentFlag.AlignHCenter}[a]
    return h | Qt.AlignmentFlag.AlignVCenter


def _lot(node: _Node, lot_id: int | None):
    return next((lot for lot in node.pv.lots if lot.id == lot_id), None)


def _entered_as(t: Trade) -> str:
    if t.split_factor == 1:
        return ""
    return (f"\nEntered as {fmt.shares(t.shares / t.split_factor)} @ {fmt.price(t.price * t.split_factor)}"
            " · shown in post-split shares")


def child_cell(node: _Node, key: str) -> Cell:
    empty = Cell("", "text")
    if node.kind == "spinoff":
        so = node.split
        moved = _moved(node)
        return {
            "symbol": Cell(fmt.short_date(so.ex_date), "num", "muted"),
            "type": Cell("⑂ SPIN-OFF", "side", "info"),
            "status": Cell(f"→ {so.child}", "muted"),
            "cost_basis": Cell(fmt.signed_money(-moved), "num", "muted"),
            "action": Cell(f"{so.cost_pct * 100:.4g}% of cost to {so.child}", "muted", "faint"),
        }.get(key, empty)
    if node.kind == "split":
        sp = node.split
        return {
            "symbol": Cell(fmt.short_date(sp.ex_date), "num", "muted"),
            "type": Cell("⇄ SPLIT", "side", "info"),
            "status": Cell(sp.label, "muted"),
        }.get(key, empty)
    if node.kind == "dividend":
        e = node.event
        assert e is not None
        cells = {
            "symbol": Cell(fmt.short_date(e.event_date), "num", "muted"),
            "type": Cell("◆ DIV", "side", "info"),
            "status": Cell(f"ex {_md(e.dividend_ex_date)}" if e.dividend_ex_date else "", "muted"),
            "dividends": Cell(fmt.money(e.amount), "num", "info"),
            "total": Cell(fmt.signed_money(e.amount), "signed", "positive"),
            "action": Cell("✎ " + e.note.splitlines()[0][:80], "muted", "faint") if e.note else empty,
        }
        return cells.get(key, empty)
    t = node.trade
    assert t is not None
    note = Cell("✎ " + t.notes.splitlines()[0][:80], "muted", "faint") if t.notes else empty
    if t.side == Side.BUY:
        lot = _lot(node, t.id)
        cells = {
            "symbol": Cell(fmt.short_date(t.trade_date), "num", "muted"),
            "type": Cell(f"▲ {'DRIP' if t.drip else 'SPIN-OFF' if t.spinoff_of else 'BUY'}  #{lot.no if lot else '?'}",
                         "side", "positive"),
            "status": Cell(t.asset_type or "", "muted"),
            "shares": Cell(fmt.shares(t.shares) + _unit(t.symbol), "num"),
            "avg_cost": Cell(fmt.price(t.price), "num"),
            "cost_basis": Cell(fmt.money(t.amount), "num"),
            "action": note,
        }
        if lot is not None and lot.is_closed:
            cells["status"] = Cell("sold", "muted", "faint")
        return cells.get(key, empty)
    lot = _lot(node, t.lot_id)
    pl = t.shares * (t.price - lot.entry) if lot else None
    cells = {
        "symbol": Cell(fmt.short_date(t.trade_date), "num", "muted"),
        "type": Cell(f"▼ SELL  #{(lot.sell_number(t.id) if lot else None) or '?'}", "side", "negative"),
        "status": (Cell("Stop-out", "status", badge="negative") if t.is_stop_out
                   else Cell(f"lot #{lot.no if lot else '?'}", "muted")),
        "shares": Cell(fmt.shares(t.shares) + _unit(t.symbol), "num"),
        "last": Cell(fmt.price(t.price), "num"),
        "real": _signed(pl),
        "total": _signed(pl),
        "action": note,
    }
    return cells.get(key, empty)


def _moved(node: _Node) -> Decimal:
    so = node.split
    return sum((per * n for lot in node.pv.lots for on, per, n in lot.basis_cuts if on == so.ex_date), Decimal(0))


def child_tooltip(node: _Node) -> str | None:
    if node.kind == "spinoff":
        so = node.split
        return (f"{so.parent} spun off {so.child} on {fmt.short_date(so.ex_date)}. {so.cost_pct * 100:.4g}% of the "
                f"cost basis ({fmt.money(_moved(node))}) moved to the {so.child} holding; sells before that date "
                "keep the full cost." + ("\nEarlier prices and dividends are shown as they were, not as Yahoo "
                                        f"adjusted them (×{so.price_factor})." if so.price_factor else "")
                + "\nRight-click to undo.")
    if node.kind == "split":
        sp = node.split
        return (f"{sp.symbol} {sp.label} split on {fmt.short_date(sp.ex_date)}. Earlier trades are shown in "
                "post-split shares. Undo it in Data ▸ Stock splits.")
    if node.kind == "dividend":
        e = node.event
        assert e is not None
        return (f"{e.symbol} dividend {fmt.money(e.amount)} received {fmt.short_date(e.event_date)}"
                + (f" (ex {fmt.short_date(e.dividend_ex_date)})" if e.dividend_ex_date else "")
                + (f"\n{e.note}" if e.note else ""))
    t = node.trade
    if t is None:
        return None
    if t.side == Side.BUY:
        what = "reinvested dividend" if t.drip else f"spun off from {t.spinoff_of}" if t.spinoff_of else "BUY"
        lines = [f"{display_symbol(t.symbol)} {what}: {fmt.shares(t.shares)} {quantity_unit(t.symbol)} @ "
                 f"{fmt.price(t.price)} = {fmt.money(t.amount)}"]
        lot = _lot(node, t.id)
        if lot is not None and lot.basis_cuts:
            lines.append(f"Cost now {fmt.price(lot.entry)} per share after "
                         f"{fmt.money(lot.basis_moved)} moved to spun-off shares")
        if t.initial_stop is not None or t.target_price is not None:
            lines.append(f"Stop {fmt.price(t.initial_stop)} · target {fmt.price(t.target_price)}")
        if t.notes:
            lines.append(t.notes)
        return "\n".join(lines) + _entered_as(t)
    lot = _lot(node, t.lot_id)
    pl = t.shares * (t.price - lot.entry) if lot else None
    return (f"{t.symbol} SELL: {fmt.shares(t.shares)} @ {fmt.price(t.price)} · realized {fmt.signed_money(pl)}"
            + (f"\n{t.notes}" if t.notes else "") + _entered_as(t))
