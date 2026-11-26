"""CashAccountsModel: one row per cash account, with its ledger (newest first) as child rows.

Cells use the Stock Trading table's Cell payload and roles, so the same view and delegate paint them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from PySide6.QtCore import QAbstractItemModel, QModelIndex, QPersistentModelIndex, Qt

from ...core.cash import AccountView, CashSnapshot, Flow, InterestProposal
from ...core.models import CASH_KIND_LABELS, CashTxnKind
from ...core.stats import Period
from ...ui import format as fmt
from ..stock_trading.table_model import CELL_ROLE, NODE_ROLE, ROW_STATE_ROLE, SORT_ROLE, Cell, Column

COLUMNS: list[Column] = [
    Column("name", "Account", "l", 180),
    Column("kind", "Type", "l", 120),
    Column("institution", "Institution", "l", 160, tooltip="Bank or broker, and the fund's symbol"),
    Column("linked", "Linked to", "l", 130, tooltip="Portfolios whose buys, sells and dividends settle here"),
    Column("apy", "APY", "r", 68, tooltip="Current APY (money market: the fund's yield)"),
    Column("change", "Change", "r", 108,
           tooltip="Change in balance over the period; on a ledger line, its amount"),
    Column("balance", "Balance", "r", 116, tooltip="Balance today; on a ledger line, the balance after it"),
    Column("est_month", "Est. / month", "r", 96, visible=False, tooltip="Balance × APY ÷ 12"),
    Column("interest", "Interest", "r", 88, tooltip="Interest received in the period"),
    Column("yield", "Yield earned", "r", 102,
           tooltip="Interest received in the period ÷ average balance, annualized"),
    Column("net", "Net flows", "r", 108, visible=False,
           tooltip="Deposits − withdrawals ± transfers and linked portfolios' trades and dividends in the period"),
    Column("pays", "Pays", "l", 84, visible=False, tooltip="Day of the month interest is paid"),
    Column("true_up", "Last true-up", "l", 100, tooltip="When the balance was last checked against the bank"),
    Column("action", "Action", "l", 230),
]
COL_INDEX = {c.key: i for i, c in enumerate(COLUMNS)}

FLOW_LABELS = {
    "OPENING": ("● OPENING", None),
    "DEPOSIT": ("▲ DEPOSIT", "positive"),
    "WITHDRAWAL": ("▼ WITHDRAWAL", "negative"),
    "TRANSFER": ("⇄ TRANSFER", "info"),
    "INTEREST": ("◆ INTEREST", "info"),
    "ADJUSTMENT": ("± ADJUSTMENT", "warning"),
    "SELL": ("▲ SELL", "positive"),
    "BUY": ("▼ BUY", "negative"),
    "DIVIDEND": ("◆ DIVIDEND", "info"),
}


@dataclass
class _Node:
    kind: str  # account | flow
    pv: AccountView
    row: int
    flow: Flow | None = None
    after: Decimal | None = None  # running balance after this flow
    children: list[_Node] = field(default_factory=list)
    parent: _Node | None = None


def action_buttons(data) -> list[tuple[str, str]]:
    """Inline hover buttons for the Action cell. `data` is (AccountView, proposals, stale true-up)."""
    _v, props, _stale = data
    out = []
    if props:
        out.append(("dividend", "Confirm interest"))  # "dividend" paints the info-filled button
    out.append(("trueup", "True up"))
    return out


def payout_text(day: int) -> str:
    if day <= 0:
        return "Month end"
    suffix = "th" if 10 <= day % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def _md(d: date) -> str:
    return f"{d:%b} {d.day}"


def _tone(v: Decimal | None) -> str | None:
    s = fmt.sign_of(v)
    return "positive" if s > 0 else "negative" if s < 0 else None


def flow_title(f: Flow) -> str:
    t = f.txn
    if t is not None and t.is_true_up:
        return "✓ TRUE-UP"
    return FLOW_LABELS.get(f.kind, (f.kind, None))[0]


def flow_detail(f: Flow, names: dict[int, str] | None = None) -> str:
    t = f.txn
    if t is None:
        return f.label
    if t.is_true_up:
        what = "interest" if t.kind == CashTxnKind.INTEREST else "adjustment"
        return f"Balance {fmt.money(t.true_up_balance)}" + (f" · {what}" if t.amount else " · matched")
    if t.kind == CashTxnKind.INTEREST:
        return (f"for {_md(t.period_end)}" if t.period_end else "") + (" · skipped" if not t.amount else "")
    if t.kind == CashTxnKind.TRANSFER:
        other = (names or {}).get(t.transfer_id or 0)
        return (("to " if t.amount < 0 else "from ") + other) if other else ""
    return ""


class CashAccountsModel(QAbstractItemModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._snapshot: CashSnapshot | None = None
        self._nodes: list[_Node] = []
        self._filter = "open"
        self._search = ""
        self._sort: tuple[int, Qt.SortOrder] | None = None
        self.period = Period()
        self.linked: dict[int, list[tuple[str, date]]] = {}
        self.transfer_names: dict[int, str] = {}  # txn id → the account on the other side of a transfer
        self._m: dict[int, dict[str, Any]] = {}  # per account: figures computed once per rebuild

    # ---------------------------------------------------------------- loading
    def set_snapshot(self, snap: CashSnapshot | None) -> None:
        self._snapshot = snap
        self._rebuild()

    def set_period(self, period: Period) -> None:
        self.period = period
        self._rebuild()

    def set_filter(self, mode: str) -> None:
        self._filter = mode
        self._rebuild()

    def set_search(self, text: str) -> None:
        self._search = text.strip().lower()
        self._rebuild()

    def total_positions(self) -> int:
        return len(self._snapshot.accounts) if self._snapshot else 0

    def _visible(self) -> list[AccountView]:
        snap = self._snapshot
        if snap is None:
            return []
        out = []
        for v in snap.accounts:
            a = v.account
            hay = " ".join(x for x in (a.name, a.institution or "", a.symbol or "") if x).lower()
            if self._search and self._search not in hay:
                continue
            if self._filter == "open" and a.closed:
                continue
            out.append(v)
        return out

    def _rebuild(self) -> None:
        self.beginResetModel()
        vs = self._visible()
        stale = set(id(v) for v in self._snapshot.stale_true_ups()) if self._snapshot else set()
        self._m = {}
        for v in vs:
            p = self.period
            self._m[v.id] = {
                "props": v.proposals(), "stale": id(v) in stale, "interest": v.interest_in(p),
                "yield": v.yield_earned(p), "net": v.net_flows_in(p),
                "change": v.balance - (v.balance_on(p.start - _ONE_DAY) if p.start else Decimal(0)),
            }
        if self._sort is not None:
            col, order = self._sort
            key = COLUMNS[col].key
            present = [v for v in vs if self._sort_value(v, key) is not None]
            missing = [v for v in vs if self._sort_value(v, key) is None]
            present.sort(key=lambda v: self._sort_value(v, key), reverse=order == Qt.SortOrder.DescendingOrder)
            vs = present + missing
        self._nodes = []
        for i, v in enumerate(vs):
            node = _Node("account", v, i)
            running = Decimal(0)
            children = []
            for f in v.flows:
                running += f.amount
                children.append(_Node("flow", v, 0, flow=f, after=running, parent=node))
            children.reverse()  # newest first, like a statement
            for j, c in enumerate(children):
                c.row = j
            node.children = children
            self._nodes.append(node)
        self.endResetModel()

    # ---------------------------------------------------------------- Qt model API
    def index(self, row: int, column: int, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> QModelIndex:  # noqa: B008
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        if not parent.isValid():
            return self.createIndex(row, column, self._nodes[row])
        pnode: _Node = parent.internalPointer()
        if pnode.kind != "account" or row >= len(pnode.children):
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
        return len(node.children) if node.kind == "account" else 0

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
        if node.kind == "flow":
            if role in (CELL_ROLE, Qt.ItemDataRole.DisplayRole):
                c = self._flow_cell(node, col.key)
                return c if role == CELL_ROLE else c.text
            if role == Qt.ItemDataRole.ToolTipRole:
                return self._flow_tooltip(node)
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

    def proposals(self, v: AccountView) -> list[InterestProposal]:
        m = self._m.get(v.id)
        return m["props"] if m else v.proposals()

    def is_stale(self, v: AccountView) -> bool:
        m = self._m.get(v.id)
        return bool(m and m["stale"])

    def _fig(self, v: AccountView, key: str):
        return self._m[v.id][key]

    def _row_state(self, node: _Node) -> str | None:
        if node.kind != "account":
            return "child"
        v = node.pv
        if v.account.closed:
            return "closed"
        if v.balance < 0:
            return "stop_hit"
        if self.proposals(v) or self.is_stale(v):
            return "action"
        return None

    def _sort_value(self, v: AccountView, key: str):
        a = v.account
        t = v.last_true_up
        return {
            "name": a.name.lower(),
            "kind": a.kind.value,
            "institution": (a.institution or "").lower() or None,
            "linked": ", ".join(n for n, _ in self.linked.get(v.id, [])) or None,
            "apy": v.apy,
            "change": self._fig(v, "change"),
            "balance": v.balance,
            "est_month": (v.est_annual_interest / 12) if v.est_annual_interest is not None else None,
            "interest": self._fig(v, "interest"),
            "yield": self._fig(v, "yield"),
            "net": self._fig(v, "net"),
            "pays": a.payout_day or 31,
            "true_up": t.txn_date if t else None,
            "action": (0 if v.balance < 0 else 1 if self.proposals(v) else 2 if self.is_stale(v) else None),
        }[key]

    def _cell(self, v: AccountView, key: str) -> Cell:
        a = v.account
        if key == "name":
            return Cell(a.name, "symbol")
        if key == "kind":
            return Cell(CASH_KIND_LABELS[a.kind], "status", badge="info" if a.is_money_market else "open")
        if key == "institution":
            text = " · ".join(x for x in (a.institution, a.symbol) if x)
            return Cell(text or fmt.DASH, "muted")
        if key == "linked":
            names = [n for n, _ in self.linked.get(v.id, [])]
            return Cell(", ".join(names) if names else fmt.DASH, "text" if names else "muted")
        if key == "apy":
            return Cell(fmt.pct(v.apy, 2) if v.apy is not None else fmt.DASH, "num")
        if key == "change":
            ch = self._fig(v, "change")
            return Cell(fmt.signed_money(ch) if ch else fmt.DASH, "signed", _tone(ch))
        if key == "balance":
            return Cell(fmt.money(v.balance), "num", "negative" if v.balance < 0 else None)
        if key == "est_month":
            e = v.est_annual_interest
            return Cell(fmt.money(e / 12) if e else fmt.DASH, "num")
        if key == "interest":
            i = self._fig(v, "interest")
            return Cell(fmt.money(i) if i else fmt.DASH, "num", "info" if i else None)
        if key == "yield":
            y = self._fig(v, "yield")
            return Cell(fmt.pct(y, 2) if y is not None else fmt.DASH, "num")
        if key == "net":
            n = self._fig(v, "net")
            return Cell(fmt.signed_money(n) if n else fmt.DASH, "signed", _tone(n))
        if key == "pays":
            return Cell(payout_text(a.payout_day), "muted")
        if key == "true_up":
            t = v.last_true_up
            stale = self.is_stale(v)
            if t is None:
                return Cell("Never", "muted", "warning" if stale else "faint")
            return Cell(fmt.short_date(t.txn_date), "num", "warning" if stale else "muted")
        if key == "action":
            if a.closed:
                return Cell("", "action")
            props = self.proposals(v)
            stale = self.is_stale(v)
            data = (v, props, stale)
            if v.balance < 0:
                return Cell("Below zero", "action", badge="negative", data=data)
            if props:
                total = sum((p.amount for p in props), Decimal(0))
                text = (f"Interest {_md(props[0].period_end)} ~{fmt.money(total)}" if len(props) == 1
                        else f"{len(props)} interest payments ~{fmt.money(total)}")
                return Cell(text, "action", badge="info", data=data)
            if stale:
                return Cell("True up due", "action", badge="stale", data=data)
            return Cell(" ", "action", data=data)  # blank, but hover shows the True up button
        return Cell("")

    def _tooltip(self, v: AccountView, key: str) -> str | None:
        a = v.account
        if key == "name":
            parts = [a.name]
            if a.institution:
                parts.append(a.institution)
            parts.append(f"Opened {fmt.short_date(a.opened_on)} with {fmt.money(v.opening)}")
            if a.notes:
                parts.append(a.notes)
            return "\n".join(parts)
        if key == "apy":
            if v.apy is None:
                return ("No yield yet: refresh prices, or enter one with Change rate…" if a.is_money_market
                        else "No APY: set one with Change rate…")
            src = v.rate_source
            hist = [f"{fmt.short_date(r.effective_date)}: {fmt.pct(r.apy, 2)}"
                    + ("" if r.source == "MANUAL" else f" ({r.source})") for r in v.rates[-6:]]
            head = ("Fund yield from " + src if a.is_money_market and src and src != "MANUAL"
                    else "Entered by you")
            return head + "\n" + "\n".join(hist)
        if key == "linked":
            items = self.linked.get(v.id, [])
            text = "\n".join(f"{n} since {fmt.short_date(s)}" for n, s in items)
            return (text + "\n" if text else "") + "Double-click to link a portfolio"

        if key == "change":
            p = self.period
            return (f"Balance {fmt.money(v.balance_on(p.start - _ONE_DAY))} on {fmt.short_date(p.start - _ONE_DAY)}"
                    f" → {fmt.money(v.balance)} today" if p.start else None)
        if key == "true_up":
            t = v.last_true_up
            if t is None:
                return "Never trued up. Use True up to check the balance against your bank."
            drift = v.balance_on(t.txn_date) - (t.true_up_balance or 0)
            msg = f"Trued up to {fmt.money(t.true_up_balance)} on {fmt.short_date(t.txn_date)}"
            if drift:
                now = fmt.money(v.balance_on(t.txn_date))
                msg += f"\nEntries changed since: the balance on that date is now {now} ({fmt.signed_money(drift)})"
            return msg
        if key == "action":
            lines = [f"Interest for {_md(p.period_start)}–{_md(p.period_end)} ({p.days} days at "
                     f"{fmt.pct(p.apy, 2)}): ~{fmt.money(p.amount)}" for p in self.proposals(v)]
            if v.balance < 0:
                lines.insert(0, f"The balance is {fmt.money(v.balance)}: a buy may have been paid from elsewhere")
            if self.is_stale(v):
                t = v.last_true_up
                lines.append(f"Last true-up {fmt.short_date(t.txn_date)}" if t else "Never trued up")
            return "\n".join(lines) or None
        return None

    def _flow_cell(self, node: _Node, key: str) -> Cell:
        f = node.flow
        assert f is not None
        empty = Cell("", "text")
        future = f.date > node.pv.today
        title = flow_title(f)
        tone = FLOW_LABELS.get(f.kind, ("", None))[1]
        if f.txn is not None and f.txn.is_true_up:
            tone = "positive"
        cells = {
            "name": Cell(fmt.short_date(f.date), "num", "muted"),
            "kind": Cell(title, "side", tone),
            "institution": Cell(flow_detail(f, self.transfer_names), "muted"),
            "linked": Cell(f.source or "", "muted"),
            "change": Cell(fmt.signed_money(f.amount) if f.amount else fmt.money(0), "signed",
                           _tone(f.amount) if f.kind != "OPENING" else None),
            "balance": Cell(fmt.money(node.after), "num", "faint" if future else "muted"),
            "action": (Cell("✎ " + f.txn.note.splitlines()[0][:80], "muted", "faint")
                       if f.txn is not None and f.txn.note else Cell("scheduled", "muted", "faint") if future
                       else empty),
        }
        return cells.get(key, empty)

    def _flow_tooltip(self, node: _Node) -> str | None:
        f = node.flow
        assert f is not None
        if f.txn is None:
            return f"{f.label} in {f.source} · {fmt.signed_money(f.amount)}\nFrom the linked portfolio: edit the " \
                   "trade or dividend there."
        lines = [f"{flow_title(f).split(' ', 1)[-1].title()} {fmt.signed_money(f.amount)} on {fmt.short_date(f.date)}"]
        d = flow_detail(f, self.transfer_names)
        if d:
            lines.append(d)
        if f.txn.note:
            lines.append(f.txn.note)
        lines.append("Double-click to edit" if f.kind != "OPENING" else "Edit the account to change it")
        return "\n".join(lines)


_ONE_DAY = date(2000, 1, 2) - date(2000, 1, 1)


def _align(a: str) -> Qt.AlignmentFlag:
    h = {"l": Qt.AlignmentFlag.AlignLeft, "r": Qt.AlignmentFlag.AlignRight, "c": Qt.AlignmentFlag.AlignHCenter}[a]
    return h | Qt.AlignmentFlag.AlignVCenter
