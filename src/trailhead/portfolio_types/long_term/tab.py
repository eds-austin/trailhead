"""Long-term Holdings page: KPI strip + allocation by type + holdings table (trades and dividends as child rows)."""

from __future__ import annotations

import base64
import csv
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from PySide6.QtCore import QByteArray, QModelIndex, QPoint, Qt, QTimer
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QMenu, QStackedWidget, QVBoxLayout, QWidget

from ...context import AppContext
from ...core.holdings import HoldingsSnapshot, HoldingView
from ...core.metals import display_symbol, is_bullion
from ...core.models import CashEvent, Portfolio, Side, Trade
from ...core.stats import PERIOD_LABELS, PeriodKey, period_for
from ...data.repo import RepoError
from ...ui import format as fmt
from ...ui.dialogs.base import confirm, info
from ...ui.theme import tokens
from ...ui.widgets.common import button, frame, label
from ...ui.widgets.empty_state import EmptyState
from ...ui.widgets.kpi_card import KpiStrip
from ...ui.widgets.price_peek import PeekController, PeekInfo
from ...ui.widgets.search_field import SearchField
from ...ui.widgets.segmented import Segmented
from ..stock_trading.positions_view import PositionsView
from ..stock_trading.service import dividends_by_position, pending_dividends_for
from .allocation import AllocationBars
from .kpis import KPI_CARDS, compute_kpis
from .service import load_holdings, pl_points
from .table_model import COLUMNS, NODE_ROLE, HoldingsTreeModel, action_buttons

ALLOC_W = 300


def peek_info(index: QModelIndex) -> PeekInfo | None:
    """Hover peek for a holding's Symbol cell: last price against its stop and profit target (both optional)."""
    if index.parent().isValid() or COLUMNS[index.column()].key != "symbol":
        return None
    node = index.data(NODE_ROLE)
    if node is None:
        return None
    h: HoldingView = node.pv
    name = h.market.name or ""
    if is_bullion(h.symbol):
        name = "Bullion · per troy ounce"
    return PeekInfo(h.symbol, display_symbol(h.symbol), name, h.last, h.day_pct, entry=h.avg_cost,
                    entry_label="Avg cost", stop=h.stop, target=h.target,
                    closed_on=h.position.close_date if h.is_closed else None)


class LongTermTab(QWidget):
    def __init__(self, ctx: AppContext, portfolio: Portfolio, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.portfolio = portfolio
        self.snapshot: HoldingsSnapshot | None = None
        self._points: list = []
        self._build()
        self._restore_ui_state()
        ctx.events.portfolio_data_changed.connect(self._on_data_changed)
        ctx.events.market_updated.connect(self.reload)
        ctx.events.settings_changed.connect(self._on_settings_changed)
        self.reload()

    @property
    def pid(self) -> int:
        assert self.portfolio.id is not None
        return self.portfolio.id

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(tokens.WINDOW_PAD, 0, tokens.WINDOW_PAD, tokens.WINDOW_PAD)
        v.setSpacing(tokens.SECTION_GAP)
        top_bar = QHBoxLayout()
        top_bar.setSpacing(tokens.SPACE[2])
        top_bar.addWidget(label("SUMMARY & INCOME", role="caption"))
        top_bar.addStretch(1)
        top_bar.addWidget(self._build_cash_chip())
        top_bar.addSpacing(tokens.SPACE[4])
        top_bar.addWidget(label("Period", role="muted"))
        self.period = QComboBox()
        for k in (PeriodKey.MTD, PeriodKey.QTD, PeriodKey.YTD, PeriodKey.L12M, PeriodKey.ALL):
            self.period.addItem(PERIOD_LABELS[k], k.value)
        self.period.setCurrentIndex(self.period.findData(PeriodKey.YTD.value))
        self.period.setToolTip("Period for the Return, Dividends received and Realized P/L cards")
        self.period.currentIndexChanged.connect(lambda _i: (self._save_ui(), self.refresh_kpis()))
        top_bar.addWidget(self.period)
        v.addLayout(top_bar)

        self.kpis = KpiStrip(KPI_CARDS)
        top = QHBoxLayout()
        top.setSpacing(tokens.SPACE[2])
        top.addWidget(self.kpis, 1)
        top.addWidget(self._build_allocation(), 0, Qt.AlignmentFlag.AlignTop)
        v.addLayout(top)

        panel = frame("table-panel")
        pv = QVBoxLayout(panel)
        pv.setContentsMargins(1, tokens.SPACE[2], 1, 1)
        pv.setSpacing(tokens.SPACE[2])
        bar = QHBoxLayout()
        bar.setContentsMargins(tokens.SPACE[3], 0, tokens.SPACE[3], 0)
        self.filter = Segmented([("open", "Open"), ("closed", "Closed"), ("all", "All")])
        self.filter.setToolTip("Open shows only current holdings")
        self.filter.changed.connect(self._on_filter)
        bar.addWidget(self.filter)
        self.search = SearchField("Search symbol  (Ctrl+F)")
        self.search.setFixedWidth(240)
        self.search.textChanged.connect(self._on_search)
        bar.addWidget(self.search)
        bar.addStretch(1)
        self.count_label = label("", role="muted")
        bar.addWidget(self.count_label)
        self.columns_btn = button("Columns", "ghost", icon="columns-3")
        self.columns_btn.clicked.connect(self._columns_menu)
        bar.addWidget(self.columns_btn)
        pv.addLayout(bar)

        self.table_stack = QStackedWidget()
        self.empty = EmptyState("No holdings yet", "Add your first buy to start tracking.", "New Trade",
                                icon="briefcase")
        self.empty.action.connect(lambda: self.new_entry())
        self.no_match = EmptyState("No matching holdings", "Try another filter or search.", icon="search")
        self.model = HoldingsTreeModel(self)
        self.view = PositionsView(columns=COLUMNS, buttons=action_buttons)
        self.view.setModel(self.model)
        self.model.modelReset.connect(self._after_reset)
        self.view.activated_child.connect(self._on_child_activated)
        self.view.activated_position.connect(self._on_position_activated)
        self.view.customContextMenuRequested.connect(self._context_menu)
        self.view.action_clicked.connect(self._on_action_clicked)
        self.peek = PeekController(self.view, self.ctx, peek_info)
        h = self.view.header()
        for sig in (h.sortIndicatorChanged, h.sectionMoved, h.sectionResized):
            sig.connect(lambda *_: self._col_timer.start())
        for w in (self.empty, self.no_match, self.view):
            self.table_stack.addWidget(w)
        pv.addWidget(self.table_stack, 1)
        v.addWidget(panel, 1)
        self._col_timer = QTimer(self)
        self._col_timer.setSingleShot(True)
        self._col_timer.setInterval(400)
        self._col_timer.timeout.connect(self._save_columns)

    def _build_allocation(self) -> QWidget:
        f = frame("panel")
        f.setFixedWidth(ALLOC_W)
        f.setFixedHeight(2 * tokens.KPI_H + tokens.SPACE[2])  # two rows of KPI cards
        hv = QVBoxLayout(f)
        hv.setContentsMargins(tokens.SPACE[3], tokens.SPACE[2], tokens.SPACE[3], tokens.SPACE[2])
        hv.setSpacing(tokens.SPACE[2])
        hh = QHBoxLayout()
        hh.addWidget(label("Allocation by type", font_token="body_strong"))
        hh.addStretch(1)
        self.alloc_sub = label("", role="caption")
        hh.addWidget(self.alloc_sub)
        hv.addLayout(hh)
        self.alloc = AllocationBars()
        hv.addWidget(self.alloc, 1)
        return f

    def _build_cash_chip(self):
        from ..cash.chip import LinkedCashChip

        self.cash_chip = LinkedCashChip()
        self.cash_chip.clicked.connect(lambda pid: self.ctx.events.open_page.emit(f"portfolio:{pid}"))
        # any portfolio's change can move this account's balance (the Cash page, another linked portfolio)
        self.ctx.events.portfolio_data_changed.connect(lambda _pid: self._refresh_cash_chip())
        self.ctx.events.portfolios_changed.connect(self._refresh_cash_chip)
        return self.cash_chip

    def _refresh_cash_chip(self) -> None:
        self.cash_chip.refresh(self.ctx.repos, self.pid, self.ctx.today())

    # ------------------------------------------------------------------ state
    def _restore_ui_state(self) -> None:
        s = self.ctx.repos.settings
        self.view.set_density(s.get("ui.table_density") or "compact")
        for i, c in enumerate(COLUMNS):
            self.view.setColumnWidth(i, c.width)
            self.view.setColumnHidden(i, not c.visible)
        state = s.get(f"ui.column_state.{self.pid}")
        restored = False
        if state:
            try:
                restored = self.view.header().restoreState(QByteArray(base64.b64decode(state)))
            except (ValueError, TypeError):
                restored = False
        if not restored:
            self.view.header().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        ui = s.get(f"ui.tab.{self.pid}") or {}
        i = self.period.findData(ui.get("period"))
        if i >= 0:
            self.period.blockSignals(True)
            self.period.setCurrentIndex(i)
            self.period.blockSignals(False)
        if ui.get("filter"):
            self.filter.set_value(ui["filter"], emit=False)
            self.model.set_filter(ui["filter"])

    def _save_ui(self) -> None:
        self.ctx.repos.settings.set(f"ui.tab.{self.pid}", {"period": self.period.currentData(),
                                                           "filter": self.filter.value()})

    def _save_columns(self) -> None:
        data = bytes(self.view.header().saveState().data())
        self.ctx.repos.settings.set(f"ui.column_state.{self.pid}", base64.b64encode(data).decode())

    def _on_settings_changed(self) -> None:
        self.view.set_density(self.ctx.repos.settings.get("ui.table_density") or "compact")
        self.reload()

    # ------------------------------------------------------------------ data
    def _on_data_changed(self, pid: int) -> None:
        if pid == self.pid:
            self.reload()

    def reload(self) -> None:
        p = self.ctx.repos.portfolios.get(self.pid)
        if p is not None:
            self.portfolio = p
        expanded = self.view.expanded_keys() if self.model.rowCount() else set()
        current = self._current_key()
        today = self.ctx.today()
        self.snapshot = load_holdings(self.ctx.repos, self.portfolio, today)
        self._points = pl_points(self.ctx.repos, self.snapshot, today)  # daily P/L: the period's return
        self.model.flag_missing_prices = self.ctx.setting("market.provider") != "manual"
        self.model.pending_divs = dividends_by_position(
            self.snapshot, pending_dividends_for(self.ctx.repos, self.portfolio, today))
        from ..splits import pending_splits_for

        self.model.pending_splits = dividends_by_position(  # same matching: the holding open on the ex-date
            self.snapshot, pending_splits_for(self.ctx.repos, self.portfolio, today))
        self.model.set_snapshot(self.snapshot)
        self.view.restore_expanded(expanded)
        if current:
            idx = self.model.position_index(current)
            if idx.isValid():
                self.view.setCurrentIndex(idx)
        self.refresh_kpis()
        self._refresh_cash_chip()

    def refresh_kpis(self) -> None:
        snap = self.snapshot
        if snap is None:
            return
        self.kpis.update_cards(compute_kpis(snap, period_for(self.period.currentData(), self.ctx.today()),
                                            self.period.currentData(), self._points))
        items = snap.allocation_by_type()
        self.alloc.set_items(items)
        n = len(snap.open_holdings)
        self.alloc_sub.setText(f"{n} holding{'s' if n != 1 else ''}" if n else "")

    def _after_reset(self) -> None:
        total = self.model.total_positions()
        shown = self.model.rowCount()
        self.table_stack.setCurrentWidget(self.empty if total == 0 else self.no_match if shown == 0 else self.view)
        n_open = len(self.snapshot.open_holdings) if self.snapshot else 0
        self.count_label.setText(f"{n_open} held · {shown} shown" if total else "")
        sort_col = self.view.header().sortIndicatorSection()
        if 0 <= sort_col < len(COLUMNS) and self.model._sort is None:
            self.model._sort = (sort_col, self.view.header().sortIndicatorOrder())
            QTimer.singleShot(0, self.model._rebuild)

    def _on_filter(self, mode: str) -> None:
        self.model.set_filter(mode)
        self._save_ui()

    def _on_search(self, text: str) -> None:
        self.model.set_search(text)
        if text and self.model.rowCount() == 1:
            self.view.expand(self.model.index(0, 0))

    def set_show_closed(self, show: bool) -> None:
        self.filter.set_value("all" if show else "open")

    # ------------------------------------------------------------------ selection
    def _current_key(self) -> str | None:
        node = self.model.node(self.view.currentIndex())
        return node.pv.key if node else None

    def selected_node(self):
        idx = self.view.currentIndex()
        return idx.data(NODE_ROLE) if idx.isValid() else None

    def _on_child_activated(self, index: QModelIndex) -> None:
        node = index.data(NODE_ROLE)
        if node is not None and node.kind == "trade":
            self.edit_trade(node.trade)

    def _on_position_activated(self, idx: QModelIndex) -> None:
        node = idx.data(NODE_ROLE)
        if node is not None and COLUMNS[idx.column()].key in ("status", "last") and node.pv.last is None \
                and not node.pv.is_closed and not is_bullion(node.pv.symbol):
            self.change_symbol(node.pv.symbol)
            return
        top = idx.siblingAtColumn(0)
        self.view.setExpanded(top, not self.view.isExpanded(top))

    def _on_action_clicked(self, key: str, index: QModelIndex) -> None:
        node = index.data(NODE_ROLE)
        if node is None:
            return
        if key == "dividend":
            self.confirm_dividends(node.pv)
        elif key == "sell":
            self.record_sell(node.pv)
        elif key == "symbol":
            self.change_symbol(node.pv.symbol)
        elif key == "split":
            self.review_splits(node.pv.symbol)
        elif key == "spinoff":
            self.record_spinoff(node.pv)

    # ------------------------------------------------------------------ actions
    def new_entry(self, prefill: dict | None = None) -> None:
        from ...ui.dialogs.holding_trade_dialog import HoldingTradeDialog

        if self.snapshot is None:
            return
        dlg = HoldingTradeDialog(self.ctx, self.portfolio, self.snapshot, prefill=prefill, parent=self.window())
        if dlg.exec():
            self._after_save(dlg.result_trades)

    def edit_trade(self, trade: Trade | None) -> None:
        from ...ui.dialogs.holding_trade_dialog import HoldingTradeDialog

        if trade is None or self.snapshot is None:
            return
        trade = self.ctx.repos.trades.get(trade.id) or trade  # as typed, not in post-split shares  # type: ignore[arg-type]
        dlg = HoldingTradeDialog(self.ctx, self.portfolio, self.snapshot, trade=trade, parent=self.window())
        if dlg.exec():
            self._after_save(dlg.result_trades, edited=True)

    def _after_save(self, trades: list[Trade], edited: bool = False) -> None:
        self.ctx.data_changed(self.pid)
        if not trades:
            return
        t = trades[0]
        for h in (self.snapshot.holdings if self.snapshot else []):
            if any(x.id == t.id for x in h.position.trades):
                idx = self.model.position_index(h.key)
                if idx.isValid():
                    self.view.setCurrentIndex(idx)
                    self.view.scrollTo(idx)
        if edited:
            self.ctx.toast("Trade saved", f"{t.side.value} {fmt.shares(t.shares)} {t.symbol}", "success")
            return
        verb = "Bought" if t.side == Side.BUY else "Sold"
        n = sum((x.shares for x in trades), Decimal(0))
        what = f"oz {display_symbol(t.symbol)}" if is_bullion(t.symbol) else t.symbol
        self.ctx.toast(f"{verb} {fmt.shares(n)} {what}", f"@ {fmt.price(t.price)}", "success")

    def record_sell(self, h: HoldingView) -> None:
        self.new_entry({"side": "SELL", "symbol": h.symbol, "shares": h.open_shares, "price": h.last})

    def confirm_dividends(self, h: HoldingView) -> int:
        """Confirm each pending dividend of a holding in turn (amount, date, optional reinvestment)."""
        from ...ui.dialogs.confirm_dividend_dialog import ConfirmDividendDialog

        n, seen = 0, set()
        while True:
            # Re-read after each one: a reinvested dividend adds shares, so later proposals pay on more shares.
            d = next((d for d in self.model.pending_divs.get(h.key, []) if d.ex_date not in seen), None)
            if d is None:
                break
            seen.add(d.ex_date)
            dlg = ConfirmDividendDialog(self.ctx, self.portfolio, h, d, parent=self.window())
            if not dlg.exec() or dlg.result is None:
                break
            n += 1
            ev, buy = dlg.result
            self.ctx.toast(f"{h.symbol} dividend recorded", f"{fmt.money(ev.amount)}"
                           + (f" · reinvested in {fmt.shares(buy.shares)} sh" if buy else ""), "success")
            self.ctx.data_changed(self.pid)
            h = next((x for x in self.snapshot.holdings if x.key == h.key), h) if self.snapshot else h
        return n

    def record_dividend(self, h: HoldingView) -> None:
        from ...ui.dialogs.confirm_dividend_dialog import ConfirmDividendDialog

        dlg = ConfirmDividendDialog(self.ctx, self.portfolio, h, parent=self.window())
        if dlg.exec():
            self.ctx.data_changed(self.pid)

    def set_levels(self, h: HoldingView) -> None:
        from ...ui.dialogs.holding_levels_dialog import HoldingLevelsDialog

        if HoldingLevelsDialog(self.ctx, h, parent=self.window()).exec():
            self.ctx.data_changed(self.pid)

    def edit_selected(self) -> None:
        node = self.selected_node()
        if node is None:
            return
        if node.kind == "trade":
            self.edit_trade(node.trade)
        elif node.kind == "position":
            self.view.setExpanded(self.view.currentIndex().siblingAtColumn(0), True)

    def delete_selected(self) -> None:
        node = self.selected_node()
        if node is None:
            return
        if node.kind == "trade":
            self.delete_trade(node.trade)
        elif node.kind == "dividend":
            self.delete_dividend(node.event)
        elif node.kind == "spinoff":
            self.undo_spinoff(node.split)

    def delete_trade(self, t: Trade) -> None:
        if t.side == Side.BUY and self.snapshot is not None:
            lot = next((lot for lot in self.snapshot.lots if lot.id == t.id), None)
            if lot is not None and lot.sells:
                info(self.window(), "Cannot delete BUY",
                     f"{t.symbol} BUY #{lot.no} has {len(lot.sells)} sell(s). Delete the SELL trades first.")
                return
        what = "DRIP" if t.drip else t.side.value
        desc = f"{what} {fmt.shares(t.shares)} {t.symbol} @ {t.price} on {t.trade_date.isoformat()}"
        if not confirm(self.window(), "Delete trade", f"Delete this trade?\n\n{desc}"):
            return
        try:
            self.ctx.repos.trades.delete(t.id)  # type: ignore[arg-type]
        except RepoError as e:
            info(self.window(), "Cannot delete", str(e))
            return
        self.ctx.data_changed(self.pid)
        self.ctx.toast("Trade deleted", desc, "info")

    def delete_dividend(self, e: CashEvent | None) -> None:
        if e is None:
            return
        drip = [t for t in (self.snapshot.trades if self.snapshot else [])
                if t.drip and t.symbol == e.symbol and t.trade_date == e.event_date]
        extra = ("\n\nThe reinvestment bought on the same day is a separate DRIP row; delete it too if it was "
                 "wrong." if drip else "")
        desc = f"{e.symbol} dividend {fmt.money(e.amount)} on {e.event_date.isoformat()}"
        if not confirm(self.window(), "Delete dividend",
                       f"Delete this dividend? It will be proposed again for review.\n\n{desc}{extra}"):
            return
        self.ctx.repos.cash.delete(e.id)  # type: ignore[arg-type]
        self.ctx.data_changed(self.pid)
        self.ctx.toast("Dividend deleted", desc, "info")

    def open_cash(self) -> None:  # no cash ledger in this portfolio type
        return

    def toggle_detail(self) -> None:  # no detail pane in this portfolio type
        return

    def override_price(self, symbol: str | None = None) -> None:
        from ...ui.dialogs.price_override_dialog import PriceOverrideDialog

        if PriceOverrideDialog(self.ctx, symbol, parent=self.window()).exec():
            self.ctx.events.market_updated.emit()

    def edit_sector(self, symbol: str) -> None:
        from ...ui.dialogs.sector_dialog import SectorDialog

        if SectorDialog(self.ctx, symbol, parent=self.window()).exec():
            self.ctx.events.market_updated.emit()

    def record_spinoff(self, h: HoldingView) -> None:
        from ...ui.dialogs.spinoff_dialog import SpinoffDialog

        dlg = SpinoffDialog(self.ctx, self.portfolio, h, parent=self.window())
        if dlg.exec() and dlg.result is not None:
            s, buy = dlg.result
            self.ctx.data_changed(self.pid)
            self.ctx.toast(f"{s.parent} spin-off recorded", f"{fmt.shares(buy.shares)} {s.child} added with "
                           f"{fmt.money(buy.amount)} of {s.parent}'s cost", "success")

    def undo_spinoff(self, s) -> None:
        from ...portfolio_types.spinoffs import undo_spinoff

        if not confirm(self.window(), "Undo spin-off",
                       f"Remove the {s.child} shares received from {s.parent} on {s.ex_date.isoformat()} and give "
                       f"{s.parent} its full cost back?", ok_text="Undo spin-off"):
            return
        try:
            undo_spinoff(self.ctx.repos, s)
        except RepoError as e:
            info(self.window(), "Cannot undo", f"{e}\n\nDelete the {s.child} sells first.")
            return
        self.ctx.data_changed(self.pid)

    def review_splits(self, symbol: str | None = None) -> None:
        from ...ui.dialogs.splits_dialog import SplitsDialog

        SplitsDialog(self.ctx, self.portfolio, symbol, parent=self.window()).exec()

    def change_symbol(self, symbol: str) -> None:
        from ...ui.dialogs.symbol_picker_dialog import change_symbol

        change_symbol(self.ctx, symbol, self.window())

    def reveal(self, symbol: str) -> None:
        if self.filter.value() == "closed":
            self.filter.set_value("open")
        for r, key in enumerate(self.model.position_keys()):
            if key.split("#")[0] == symbol:
                idx = self.model.index(r, 0)
                self.view.setCurrentIndex(idx)
                self.view.scrollTo(idx)

    def focus_search(self) -> None:
        self.search.setFocus()
        self.search.selectAll()

    # ------------------------------------------------------------------ menus
    def _columns_menu(self) -> None:
        m = QMenu(self)
        for i, c in enumerate(COLUMNS):
            if c.key == "symbol":
                continue
            a = QAction(c.title, m)
            a.setCheckable(True)
            a.setChecked(not self.view.isColumnHidden(i))
            a.toggled.connect(lambda on, col=i: (self.view.setColumnHidden(col, not on), self._col_timer.start()))
            m.addAction(a)
        m.addSeparator()
        m.addAction("Reset columns", self._reset_columns)
        m.exec(self.columns_btn.mapToGlobal(QPoint(0, self.columns_btn.height())))

    def show_columns_menu(self) -> None:
        self._columns_menu()

    def _reset_columns(self) -> None:
        h = self.view.header()
        for i, c in enumerate(COLUMNS):
            h.moveSection(h.visualIndex(i), i)
            self.view.setColumnWidth(i, c.width)
            self.view.setColumnHidden(i, not c.visible)
        self._save_columns()

    def _context_menu(self, pos: QPoint) -> None:
        idx = self.view.indexAt(pos)
        if not idx.isValid():
            return
        self.view.setCurrentIndex(idx)
        node = idx.data(NODE_ROLE)
        h: HoldingView = node.pv
        m = QMenu(self)
        if node.kind == "trade":
            m.addAction("Edit trade…", lambda: self.edit_trade(node.trade))
            m.addAction("Delete trade", lambda: self.delete_trade(node.trade))
            m.addSeparator()
        elif node.kind == "dividend":
            m.addAction("Delete dividend", lambda: self.delete_dividend(node.event))
            m.addSeparator()
        elif node.kind == "spinoff":
            m.addAction(f"Undo spin-off of {node.split.child}", lambda: self.undo_spinoff(node.split))
            m.addSeparator()
        metal = is_bullion(h.symbol)
        if not h.is_closed:
            m.addAction("Buy more…", lambda: self.new_entry({"side": "BUY", "symbol": h.symbol}))
            m.addAction("Sell…", lambda: self.new_entry({"side": "SELL", "symbol": h.symbol}))
            m.addAction("Set stop & target…", lambda: self.set_levels(h))
            if not metal:
                m.addAction("Record spin-off…", lambda: self.record_spinoff(h))
            m.addSeparator()
            if not metal:
                if self.model.pending_divs.get(h.key):
                    m.addAction("Confirm dividend…", lambda: self.confirm_dividends(h))
                m.addAction("Record dividend…", lambda: self.record_dividend(h))
                m.addSeparator()
        if not metal:
            m.addAction("Stock splits…", lambda: self.review_splits(h.symbol))
            m.addAction("Change symbol…", lambda: self.change_symbol(h.symbol))
        m.addAction("Override price…", lambda: self.override_price(h.symbol))
        if not metal:
            m.addAction("Edit sector…", lambda: self.edit_sector(h.symbol))
            m.addSeparator()
            m.addAction("Copy symbol", lambda: QGuiApplication.clipboard().setText(h.symbol))
        m.exec(self.view.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------------ export
    def export_csv(self, path: Path) -> int:
        headers, data = self.table_rows()
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(headers)
            w.writerows(data)
        return len(data)

    def table_rows(self) -> tuple[list[str], list[list[str]]]:
        h = self.view.header()
        cols = [h.logicalIndex(v) for v in range(h.count()) if not h.isSectionHidden(h.logicalIndex(v))]
        data = [[self.model.index(r, c).data() or "" for c in cols] for r in range(self.model.rowCount())]
        return [COLUMNS[c].title for c in cols], data

    def update_portfolio(self, p: Portfolio) -> None:
        self.portfolio = replace(p)
        self.reload()
