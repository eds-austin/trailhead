"""Find the data provider's symbol for what you typed (BRK.B → BRK-B) or search by company name."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from ...context import AppContext
from ...market.provider import ProviderError, ProviderUnavailableError, SymbolMatch
from .. import theme
from ..widgets.common import button, label
from ..widgets.search_field import SearchField
from .base import AppDialog

COLS = ["Symbol", "Name", "Exchange", "Type"]


class SymbolPickerDialog(AppDialog):
    """`result` is the chosen symbol. `keep_text` labels the cancel button (e.g. "Keep BRK.B")."""

    def __init__(self, ctx: AppContext, symbol: str, intro: str = "", keep_text: str = "Cancel",
                 parent: QWidget | None = None):
        super().__init__("Find the Yahoo Finance symbol", parent, width=620, primary_text="Use symbol")
        self.ctx = ctx
        self.symbol = symbol.strip().upper()
        self.result: str | None = None
        self._matches: list[SymbolMatch] = []
        self._query = ""
        self.cancel_btn.setText(keep_text)
        msg = label(intro or f"Pick the symbol Yahoo Finance uses for {self.symbol}, or search by company name. "
                             "Share classes use a dash there (BRK-B).", role="muted")
        msg.setWordWrap(True)
        self.body.addWidget(msg)
        row = QHBoxLayout()
        self.query = SearchField("Symbol or company name")
        self.query.setText(self.symbol)
        self.query.returnPressed.connect(self.search)
        row.addWidget(self.query, 1)
        self.search_btn = button("Search", icon="search")
        self.search_btn.setAutoDefault(False)
        self.search_btn.clicked.connect(self.search)
        row.addWidget(self.search_btn)
        self.body.addLayout(row)
        self.table = QTableWidget(0, len(COLS))
        self.table.setObjectName("plain")
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setMinimumHeight(240)
        for i, w in enumerate((96, 260, 110)):
            self.table.setColumnWidth(i, w)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._sync)
        self.table.cellDoubleClicked.connect(lambda _r, _c: self._on_primary())
        self.body.addWidget(self.table)
        self.status = label("", role="muted")
        self.status.setWordWrap(True)
        self.body.addWidget(self.status)
        self._sync()
        self.search()

    def search(self) -> None:
        q = self.query.text().strip()
        if not q:
            return
        self._query = q
        if self.ctx.market is None:
            self.status.setText("Symbol search needs market data, which is off.")
            return
        self.status.setText(f"Searching for “{q}”…")
        self.search_btn.setEnabled(False)
        self.ctx.market.find_symbols(q, lambda res, q=q: self._on_results(q, res))

    def _on_results(self, query: str, res) -> None:
        if query != self._query:
            return
        self.search_btn.setEnabled(True)
        if isinstance(res, ProviderUnavailableError):
            self.set_matches([], str(res))
        elif isinstance(res, (ProviderError, Exception)):
            self.set_matches([], f"Search failed: {res}")
        else:
            self.set_matches(res)

    def set_matches(self, matches: list[SymbolMatch], message: str | None = None) -> None:
        self._matches = matches
        self.table.setRowCount(len(matches))
        strong = theme.font("body_strong")
        for r, m in enumerate(matches):
            kind = "Likely match" + (f" · {m.kind}" if m.kind else "") if m.likely else (m.kind or "")
            for c, v in enumerate((m.symbol, m.name or "", m.exchange or "", kind)):
                it = QTableWidgetItem(v)
                if c == 0:
                    it.setFont(strong)
                if m.likely and c == 3:
                    it.setForeground(theme.qcolor(theme.palette().positive, 1.0))
                self.table.setItem(r, c, it)
            self.table.setRowHeight(r, 30)
        if matches:
            self.table.selectRow(0)
        if message is None:
            message = ("" if matches else
                       f"No matches for “{self._query}”. Try the company name, e.g. “Berkshire Hathaway”.")
        self.status.setText(message)
        self._sync()

    def _selected(self) -> SymbolMatch | None:
        rows = self.table.selectionModel().selectedRows()
        return self._matches[rows[0].row()] if rows and rows[0].row() < len(self._matches) else None

    def _sync(self) -> None:
        m = self._selected()
        self.primary_btn.setEnabled(m is not None)
        self.primary_btn.setText(f"Use {m.symbol}" if m else "Use symbol")

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if self.query.hasFocus() and e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.search()
            return
        super().keyPressEvent(e)

    def submit(self) -> bool:
        m = self._selected()
        if m is None:
            return False
        self.result = m.symbol
        return True


def pick_symbol(ctx: AppContext, symbol: str, parent: QWidget, intro: str = "", keep_text: str = "Cancel"
                ) -> str | None:
    dlg = SymbolPickerDialog(ctx, symbol, intro, keep_text, parent=parent)
    return dlg.result if dlg.exec() else None


def change_symbol(ctx: AppContext, symbol: str, parent: QWidget, keep_text: str = "Cancel",
                  no_price: bool = False) -> str | None:
    """Pick the provider's symbol for a held symbol and rename it everywhere, then fetch its price."""
    why = (f"Yahoo Finance returned no price for {symbol}. It probably spells it differently (share classes use a "
           f"dash: BRK-B). Pick the right symbol" if no_price else
           f"Prices are looked up by symbol. If Yahoo Finance spells {symbol} differently (share classes use a dash: "
           "BRK-B), pick its symbol")
    new = pick_symbol(ctx, symbol, parent, keep_text=keep_text,
                      intro=why + ". Your trades and dividends in every portfolio are renamed to it.")
    if not new or new == symbol:
        return None
    n = ctx.repos.trades.rename_symbol(symbol, new)
    for p in ctx.repos.portfolios.list(include_archived=True):
        ctx.data_changed(p.id)  # type: ignore[arg-type]
    ctx.toast(f"{symbol} is now {new}", f"{n} trade{'s' if n != 1 else ''} renamed · fetching its price", "success")
    if ctx.market is not None:
        ctx.market.refresh()
    return new


class UnknownSymbolMixin:
    """For trade dialogs: when the provider doesn't know a NEW symbol, offer "Find symbol…" and, on saving a BUY,
    pop up the picker once (cancel keeps the typed symbol). Needs self.ctx, self.symbol, self.sym(),
    self.is_buy(), self._on_symbol_done() and a layout row for the link."""

    def _init_symbol_check(self, row) -> None:
        from PySide6.QtWidgets import QPushButton

        self._unknown_symbol: str | None = None
        self._symbol_kept: str | None = None
        btn = QPushButton("Find symbol…")
        btn.setProperty("variant", "ghost")
        btn.setProperty("scale", "small")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(f"color: {theme.palette().accent};")
        btn.setAutoDefault(False)
        btn.hide()
        btn.clicked.connect(self._find_symbol)
        row.addWidget(btn)
        self.find_symbol_btn = btn

    def _symbol_checked(self, symbol: str, ok: bool) -> None:
        new = symbol not in self.ctx.repos.trades.symbols()  # type: ignore[attr-defined]
        self._unknown_symbol = symbol if not ok and new else None
        self.find_symbol_btn.setVisible(self._unknown_symbol is not None)

    def _find_symbol(self) -> bool:
        sym = self.sym()  # type: ignore[attr-defined]
        new = pick_symbol(self.ctx, sym, self, keep_text=f"Keep {sym}")  # type: ignore[attr-defined, arg-type]
        if not new:
            self._symbol_kept = sym
            return False
        self.symbol.setText(new)  # type: ignore[attr-defined]
        self._unknown_symbol = None
        self.find_symbol_btn.hide()
        self._on_symbol_done()  # type: ignore[attr-defined]
        return True

    def _check_symbol_before_save(self) -> None:
        sym = self.sym()  # type: ignore[attr-defined]
        if self.is_buy() and sym and sym == self._unknown_symbol and self._symbol_kept != sym:  # type: ignore[attr-defined]
            self._find_symbol()
