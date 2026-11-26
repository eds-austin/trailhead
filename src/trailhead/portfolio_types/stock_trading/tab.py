"""Stock-trading portfolio tab: KPI strip + positions table + detail pane (PLAN §7.2–7.4)."""

from __future__ import annotations

import base64
import contextlib
import csv
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from PySide6.QtCore import QByteArray, QModelIndex, QPoint, Qt, QTimer
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QMenu,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ...context import AppContext
from ...core.models import Portfolio, Side, Trade
from ...core.performance import GROUP_KEYS, group_performance
from ...core.snapshot import PortfolioSnapshot
from ...core.stats import (
    PERIOD_LABELS,
    PeriodKey,
    closed_lot_units,
    closed_position_units,
    period_for,
    trade_stats,
)
from ...data.repo import RepoError
from ...ui import format as fmt
from ...ui.dialogs.base import confirm, info
from ...ui.theme import tokens
from ...ui.widgets.common import button, frame, label
from ...ui.widgets.empty_state import EmptyState
from ...ui.widgets.kpi_card import KpiStrip
from ...ui.widgets.search_field import SearchField
from ...ui.widgets.segmented import Segmented
from .kpis import KPI_CARDS, compute_kpis
from .performance_view import PerformanceTable
from .positions_view import PositionsView
from .service import accept_dividend, dividends_by_position, load_snapshot, pending_dividends_for
from .table_model import COL_INDEX, COLUMNS, COLUMNS_REV, NODE_ROLE, RESIZED_IN_REV, PositionsTreeModel

HIST_W = 340  # R distribution panel beside the KPI strip


class PortfolioTab(QWidget):
    def __init__(self, ctx: AppContext, portfolio: Portfolio, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.portfolio = portfolio
        self.snapshot: PortfolioSnapshot | None = None
        self.detail = None  # set by _build_detail (phase 6)
        self._perf_selected: str | None = None  # performance group shown in the R distribution panel
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

        perf = QHBoxLayout()
        perf.setSpacing(tokens.SPACE[2])
        perf.addWidget(label("SUMMARY & PERFORMANCE", role="caption"))
        perf.addStretch(1)
        perf.addWidget(self._build_cash_chip())
        perf.addSpacing(tokens.SPACE[4])
        perf.addWidget(label("Stats by", role="muted"))
        self.unit = Segmented([("lot", "Lot"), ("position", "Position")])
        self.unit.setToolTip("Closed lots (spreadsheet rows) or closed positions as the trade unit")
        self.unit.changed.connect(lambda _k: self._save_and_refresh_kpis())
        perf.addWidget(self.unit)
        perf.addSpacing(tokens.SPACE[2])
        perf.addWidget(label("Period", role="muted"))
        self.period = QComboBox()
        for k in (PeriodKey.MTD, PeriodKey.QTD, PeriodKey.YTD, PeriodKey.L12M, PeriodKey.ALL):
            self.period.addItem(PERIOD_LABELS[k], k.value)
        self.period.setCurrentIndex(self.period.findData(PeriodKey.YTD.value))
        self.period.currentIndexChanged.connect(lambda _i: self._save_and_refresh_kpis())
        perf.addWidget(self.period)
        v.addLayout(perf)

        self.kpis = KpiStrip(KPI_CARDS)
        top = QHBoxLayout()
        top.setSpacing(tokens.SPACE[2])
        top.addWidget(self.kpis, 1)
        top.addWidget(self._build_histogram(), 0, Qt.AlignmentFlag.AlignTop)
        v.addLayout(top)

        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.setChildrenCollapsible(True)
        panel = frame("table-panel")
        pv = QVBoxLayout(panel)
        pv.setContentsMargins(1, tokens.SPACE[2], 1, 1)
        pv.setSpacing(tokens.SPACE[2])
        bar = QHBoxLayout()
        bar.setContentsMargins(tokens.SPACE[3], 0, tokens.SPACE[3], 0)
        self.view_mode = Segmented([("positions", "Positions"), ("performance", "Performance")])
        self.view_mode.setToolTip("Positions table, or closed-trade stats compared by strategy, time frame or "
                                  "exit plan")
        self.view_mode.setProperty("scale", "view")
        self.view_mode.changed.connect(lambda _k: self._on_view_mode())
        bar.addWidget(self.view_mode)
        view_sep = frame("vrule")  # sets the view switch apart from the filters that act on the view
        view_sep.setFixedSize(1, 24)
        bar.addSpacing(tokens.SPACE[1])
        bar.addWidget(view_sep)
        bar.addSpacing(tokens.SPACE[1])
        self.group_label = label("Group by", role="muted")
        bar.addWidget(self.group_label)
        self.group_by = Segmented([(k, v) for k, v in GROUP_KEYS.items()])
        self.group_by.changed.connect(lambda _k: self._on_group_by())
        bar.addWidget(self.group_by)
        self.filter = Segmented([("open", "Open"), ("closed", "Closed"), ("all", "All")])
        self.filter.setToolTip("Open shows only open positions")
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
        self.empty = EmptyState("No positions yet", "Add your first trade to start tracking.", "New Trade",
                                icon="chart-candlestick")
        self.empty.action.connect(lambda: self.new_entry())
        self.no_match = EmptyState("No matching positions", "Try another filter or search.", icon="search")
        self.model = PositionsTreeModel(self)
        self.view = PositionsView()
        self.view.setModel(self.model)
        self.model.modelReset.connect(self._after_reset)
        self.view.activated_child.connect(self._on_child_activated)
        self.view.activated_position.connect(self._on_position_activated)
        self.view.customContextMenuRequested.connect(self._context_menu)
        self.view.action_clicked.connect(self._on_action_clicked)
        self.view.selectionModel().currentRowChanged.connect(lambda cur, _prev: self._on_current_changed(cur))
        h = self.view.header()
        h.sortIndicatorChanged.connect(lambda *_: self._save_columns_later())
        h.sectionMoved.connect(lambda *_: self._save_columns_later())
        h.sectionResized.connect(lambda *_: self._save_columns_later())
        self.table_stack.addWidget(self.empty)
        self.table_stack.addWidget(self.no_match)
        self.table_stack.addWidget(self.view)
        self.perf = PerformanceTable()
        self.perf.group_selected.connect(lambda _g: self._on_group_selected())
        self.table_stack.addWidget(self.perf)
        pv.addWidget(self.table_stack, 1)
        self._sync_mode_widgets()
        self.splitter.addWidget(panel)
        self._build_detail()
        v.addWidget(self.splitter, 1)
        self._col_timer = QTimer(self)
        self._col_timer.setSingleShot(True)
        self._col_timer.setInterval(400)
        self._col_timer.timeout.connect(self._save_columns)

    def _build_histogram(self) -> QWidget:
        """R-multiple distribution of the closed trades behind the Win % / Expectancy cards (same period and unit)."""
        from ...ui.charts.r_histogram import RHistogram

        f = frame("panel")
        f.setFixedWidth(HIST_W)
        f.setFixedHeight(2 * tokens.KPI_H + tokens.SPACE[2])  # two rows of KPI cards
        hv = QVBoxLayout(f)
        hv.setContentsMargins(tokens.SPACE[3], tokens.SPACE[2], tokens.SPACE[3], tokens.SPACE[2])
        hv.setSpacing(tokens.SPACE[1])
        hh = QHBoxLayout()
        self.hist_title = label("R distribution", font_token="body_strong")
        hh.addWidget(self.hist_title)
        hh.addStretch(1)
        self.hist_sub = label("", role="caption")
        hh.addWidget(self.hist_sub)
        hv.addLayout(hh)
        self.hist = RHistogram()
        self.hist.setToolTip("R multiples of closed trades in 0.5R bins; the dashed line is the expectancy")
        hv.addWidget(self.hist, 1)
        return f

    def _build_detail(self) -> None:
        """Detail pane: candle chart + ladder cards (collapsible bottom splitter)."""
        from .detail import DetailPane

        self.detail = DetailPane(self.ctx)
        self.detail.close_requested.connect(self.hide_detail)
        self.detail.confirm.connect(lambda pv, lot_id: self.confirm_stops(pv, lot_id))
        self.detail.record_sell.connect(lambda pv, lot_id: self.record_suggested_sell(pv, lot_id))
        self.splitter.addWidget(self.detail)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        self.splitter.setSizes([560, 0])
        self.splitter.splitterMoved.connect(lambda *_: self._save_ui())

    def hide_detail(self) -> None:
        self.splitter.setSizes([sum(self.splitter.sizes()), 0])
        self._save_ui()

    def toggle_detail(self) -> None:
        sizes = self.splitter.sizes()
        if len(sizes) > 1 and sizes[1] == 0:
            self._on_position_activated(self.view.currentIndex())
        else:
            self.hide_detail()

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
        density = s.get("ui.table_density") or "compact"
        self.view.set_density(density)
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
        rev = int(s.get(f"ui.columns_rev.{self.pid}") or 1)
        if restored and rev < COLUMNS_REV:
            for r, keys in RESIZED_IN_REV.items():
                if rev < r:
                    for k in keys:
                        self.view.setColumnWidth(COL_INDEX[k], COLUMNS[COL_INDEX[k]].width)
            self._save_columns()
        s.set(f"ui.columns_rev.{self.pid}", COLUMNS_REV)
        ui = s.get(f"ui.tab.{self.pid}") or {}
        if ui.get("period"):
            i = self.period.findData(ui["period"])
            if i >= 0:
                self.period.blockSignals(True)
                self.period.setCurrentIndex(i)
                self.period.blockSignals(False)
        if ui.get("unit"):
            self.unit.set_value(ui["unit"], emit=False)
        if ui.get("filter"):
            self.filter.set_value(ui["filter"], emit=False)
            self.model.set_filter(ui["filter"])
        if ui.get("splitter"):
            with contextlib.suppress(ValueError, TypeError):
                self.splitter.restoreState(QByteArray(base64.b64decode(ui["splitter"])))
        if ui.get("group") in GROUP_KEYS:
            self.group_by.set_value(ui["group"], emit=False)
        if ui.get("view") == "performance":
            self.view_mode.set_value("performance", emit=False)
        self.model.set_show_stop_changes(ui.get("stop_rows", True))
        self._sync_mode_widgets()

    def _save_ui(self) -> None:
        self.ctx.repos.settings.set(f"ui.tab.{self.pid}", {
            "period": self.period.currentData(), "unit": self.unit.value(), "filter": self.filter.value(),
            "splitter": base64.b64encode(bytes(self.splitter.saveState().data())).decode(),
            "view": self.view_mode.value(), "group": self.group_by.value(),
            "stop_rows": self.model.show_stop_changes,
        })

    # ------------------------------------------------------------------ performance view
    @property
    def performance_mode(self) -> bool:
        return self.view_mode.value() == "performance"

    def _sync_mode_widgets(self) -> None:
        perf = self.performance_mode
        for w in (self.group_label, self.group_by):
            w.setVisible(perf)
        for w in (self.filter, self.search, self.columns_btn):
            w.setVisible(not perf)

    def _on_view_mode(self) -> None:
        self._sync_mode_widgets()
        if self.performance_mode:
            self.hide_detail()
        self._save_ui()
        self.refresh_kpis()
        self._after_reset()

    def _on_group_by(self) -> None:
        self._save_ui()
        self.refresh_kpis()

    def _on_group_selected(self) -> None:
        if self.snapshot is not None:
            self._refresh_histogram()

    def refresh_performance(self) -> None:
        if self.snapshot is None:
            return
        key = self.group_by.value()
        rows = group_performance(self.snapshot.lots, [pv.position for pv in self.snapshot.positions], key,
                                 self.unit.value(), self.current_period())
        self.perf.set_group_title(GROUP_KEYS[key])
        self.perf.blockSignals(True)
        self.perf.set_rows(rows, keep=self._perf_selected)
        self.perf.blockSignals(False)

    def _refresh_histogram(self) -> None:
        """R distribution of the selected performance group, else of every closed trade in the period."""
        by = self.unit.value()
        noun = "position" if by == "position" else "lot"
        g = self.perf.selected_row() if self.performance_mode else None
        self._perf_selected = g.label if g is not None else None
        if g is None:
            period = self.current_period()
            if by == "position":
                units = closed_position_units([pv.position for pv in self.snapshot.positions], period)
            else:
                units = closed_lot_units(self.snapshot.lots, period)
            rs, st = [u.r for u in units if u.r is not None], trade_stats(units)
            self.hist_title.setText("R distribution")
        else:
            rs, st = g.rs, g.stats
            self.hist_title.setText(f"R distribution · {g.label}")
        self.hist.set_values(rs, st.expectancy, empty_text=f"No closed {noun}s in this period")
        self.hist_sub.setText(f"{st.n} closed {noun}{'s' if st.n != 1 else ''} · E {fmt.r_mult(st.expectancy)}"
                              if st.n else "")

    def _save_columns_later(self) -> None:
        self._col_timer.start()

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
        current_key = self._current_position_key()
        self.snapshot = load_snapshot(self.ctx.repos, self.portfolio, self.ctx.today())
        self.model.pending_divs = dividends_by_position(
            self.snapshot, pending_dividends_for(self.ctx.repos, self.portfolio, self.ctx.today()))
        self.model.flag_missing_prices = self.ctx.setting("market.provider") != "manual"
        self.model.near_stop_pct = Decimal(str(self.ctx.setting("alerts.near_stop_pct") or 0.02))
        self.model.earnings_days = int(self.ctx.setting("alerts.earnings_days") or 7)
        self.model.set_snapshot(self.snapshot)
        self.view.restore_expanded(expanded)
        if current_key:
            idx = self.model.position_index(current_key)
            if idx.isValid():
                self.view.setCurrentIndex(idx)
                self._on_current_changed(idx)
        self.refresh_kpis()
        self._refresh_cash_chip()

    def _after_reset(self) -> None:
        total = self.model.total_positions()
        shown = self.model.rowCount()
        if total == 0:
            self.table_stack.setCurrentWidget(self.empty)
        elif self.performance_mode:
            self.table_stack.setCurrentWidget(self.perf)
        elif shown == 0:
            self.table_stack.setCurrentWidget(self.no_match)
        else:
            self.table_stack.setCurrentWidget(self.view)
        n_open = len(self.snapshot.open_positions) if self.snapshot else 0
        if self.performance_mode:
            unit = "positions" if self.unit.value() == "position" else "lots"
            self.count_label.setText(f"Closed {unit} · {self.period.currentText()}" if total else "")
        else:
            self.count_label.setText(f"{n_open} open · {shown} shown" if total else "")
        sort_col = self.view.header().sortIndicatorSection()
        if 0 <= sort_col < len(COLUMNS) and self.model._sort is None:
            self.model._sort = (sort_col, self.view.header().sortIndicatorOrder())
            QTimer.singleShot(0, self.model._rebuild)

    def current_period(self):
        return period_for(self.period.currentData(), self.ctx.today())

    def refresh_kpis(self) -> None:
        if self.snapshot is None:
            return
        warn = Decimal(str(self.ctx.setting("alerts.open_risk_warn_pct") or 0.06))
        self.kpis.update_cards(compute_kpis(self.snapshot, self.current_period(), self.period.currentData(),
                                            self.unit.value(), warn))
        self.refresh_performance()
        self._refresh_histogram()

    def _save_and_refresh_kpis(self) -> None:
        self._save_ui()
        self.refresh_kpis()
        if self.performance_mode:
            self._after_reset()  # count label names the unit and period

    def _on_filter(self, mode: str) -> None:
        self.model.set_filter(mode)
        self._save_ui()

    def _on_search(self, text: str) -> None:
        self.model.set_search(text)
        if text and self.model.rowCount() == 1:
            self.view.expand(self.model.index(0, 0))

    def set_show_closed(self, show: bool) -> None:
        self.filter.set_value("all" if show else "open")

    @property
    def show_stop_changes(self) -> bool:
        return self.model.show_stop_changes

    def set_show_stop_changes(self, show: bool) -> None:
        """View › Show stop changes: stop raises as their own lines under each position (remembered per portfolio)."""
        if show == self.model.show_stop_changes:
            return
        expanded = self.view.expanded_keys() if self.model.rowCount() else set()
        self.model.set_show_stop_changes(show)
        self.view.restore_expanded(expanded)
        self._save_ui()

    # ------------------------------------------------------------------ selection helpers
    def _current_position_key(self) -> str | None:
        idx = self.view.currentIndex()
        node = self.model.node(idx)
        return node.pv.key if node else None

    def selected_node(self):
        idx = self.view.currentIndex()
        return idx.data(NODE_ROLE) if idx.isValid() else None

    def _on_current_changed(self, cur: QModelIndex) -> None:
        if self.detail is not None:
            node = cur.data(NODE_ROLE) if cur.isValid() else None
            self.detail.show_position(node.pv if node else None, self.snapshot)

    def _on_position_activated(self, index: QModelIndex) -> None:
        node = index.data(NODE_ROLE)
        if (node is not None and COLUMNS[index.column()].key in ("status", "last") and node.pv.last is None
                and not node.pv.is_closed):
            self.change_symbol(node.pv.symbol)
            return
        if COLUMNS[index.column()].key == "sector":
            node = index.data(NODE_ROLE)
            if node is not None:
                self.edit_sector(node.pv.symbol)
            return
        index = index.siblingAtColumn(0)
        if self.detail is not None:
            sizes = self.splitter.sizes()
            if len(sizes) > 1 and sizes[1] == 0:
                total = sum(sizes)
                self.splitter.setSizes([int(total * 0.52), int(total * 0.48)])
                self._save_ui()
            self._on_current_changed(index)

    def _on_child_activated(self, index: QModelIndex) -> None:
        node = index.data(NODE_ROLE)
        if node is None:
            return
        if node.kind == "trade":
            self.edit_trade(node.trade)

    # ------------------------------------------------------------------ strategy actions
    def _on_action_clicked(self, key: str, index: QModelIndex) -> None:
        node = index.data(NODE_ROLE)
        if node is None:
            return
        if key == "confirm":
            self.confirm_stops(node.pv)
        elif key == "dividend":
            self.confirm_dividends(node.pv)
        elif key == "stopout":
            self.record_stop_out(node.pv)
        else:
            self.record_suggested_sell(node.pv)

    def confirm_dividends(self, pv) -> int:
        """Record every pending dividend of a position at its proposed amount (one click)."""
        from ...ui import format as fmt

        divs = self.model.pending_divs.get(pv.key, [])
        for d in divs:
            accept_dividend(self.ctx.repos, self.pid, d, d.amount, self.ctx.today())
        if divs:
            total = sum((d.amount for d in divs), Decimal(0))
            self.ctx.data_changed(self.pid)
            self.ctx.toast(f"{pv.symbol} dividend recorded", f"{fmt.money(total)} · ex-date "
                           + ", ".join(fmt.short_date(d.ex_date) for d in divs), "success")
        return len(divs)

    def record_stop_out(self, pv, lot_id: int | None = None) -> None:
        from ...ui.dialogs.stop_out_dialog import StopOutDialog

        dlg = StopOutDialog(self.ctx, self.portfolio, pv, lot_id, parent=self.window())
        if dlg.exec():
            self._after_save(dlg.result_trades)

    def confirm_stops(self, pv, lot_id: int | None = None) -> int:
        """Write LADDER_CONFIRMED stop changes for every (or one) lot with a RAISE_STOP action."""
        from ...core.models import StopChange, StopReason
        from ...core.r_engine import ActionKind
        from ...ui import format as fmt

        n = 0
        new_stop = None
        for a in pv.actions:
            if a.kind != ActionKind.RAISE_STOP or (lot_id is not None and a.lot_id != lot_id):
                continue
            self.ctx.repos.stops.add(StopChange(None, a.lot_id, self.ctx.today(), a.price,  # type: ignore[arg-type]
                                                StopReason.LADDER_CONFIRMED))
            new_stop = a.price
            n += 1
        if n:
            self.ctx.data_changed(self.pid)
            self.ctx.toast(f"{pv.symbol} stop raised → {fmt.price(new_stop)}",
                           f"Confirmed on {n} lot{'s' if n > 1 else ''}", "success")
        return n

    def record_suggested_sell(self, pv, lot_id: int | None = None) -> None:
        from ...core.r_engine import ActionKind

        acts = [a for a in pv.actions if a.kind in (ActionKind.STOP_HIT, ActionKind.SELL_TRANCHE)
                and (lot_id is None or a.lot_id == lot_id)]
        a = acts[0] if acts else None
        price = pv.last
        if a is not None and a.kind == ActionKind.STOP_HIT:
            self.record_stop_out(pv, lot_id)
        elif a is not None:
            self.record_sell(a.lot_id, a.shares, pv.symbol, price)
        else:
            self.record_sell(lot_id, None, pv.symbol, price)

    # ------------------------------------------------------------------ actions
    def new_entry(self, prefill: dict | None = None) -> None:
        from ...ui.dialogs.trade_dialog import TradeDialog

        if self.snapshot is None:
            return
        dlg = TradeDialog(self.ctx, self.portfolio, self.snapshot, prefill=prefill, parent=self.window())
        if dlg.exec():
            self._after_save(dlg.result_trades)

    def edit_trade(self, trade: Trade | None) -> None:
        from ...ui.dialogs.trade_dialog import TradeDialog

        if trade is None or self.snapshot is None:
            return
        trade = self.ctx.repos.trades.get(trade.id) or trade  # as typed, not in post-split shares  # type: ignore[arg-type]
        dlg = TradeDialog(self.ctx, self.portfolio, self.snapshot, trade=trade, parent=self.window())
        if dlg.exec():
            self._after_save(dlg.result_trades)

    def _after_save(self, trades: list[Trade]) -> None:
        self.ctx.data_changed(self.pid)
        if trades:
            t = trades[0]
            for pv in (self.snapshot.positions if self.snapshot else []):
                if any(x.id == t.id for x in pv.position.trades):
                    idx = self.model.position_index(pv.key)
                    if idx.isValid():
                        self.view.setCurrentIndex(idx)
                        self.view.scrollTo(idx)
            verb = "Bought" if t.side == Side.BUY else "Sold"
            n = sum((x.shares for x in trades), Decimal(0))
            self.ctx.toast(f"{verb} {n:,} {t.symbol}", f"Saved {len(trades)} trade{'s' if len(trades) > 1 else ''}",
                           "success")

    def record_sell(self, lot_id: int | None = None, shares: Decimal | None = None, symbol: str | None = None,
                    price: Decimal | None = None) -> None:
        self.new_entry({"side": "SELL", "symbol": symbol, "lot_id": lot_id, "shares": shares, "price": price})

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
        elif node.kind == "stop":
            self.delete_stop(node.stop)

    def delete_trade(self, t: Trade) -> None:
        if t.side == Side.BUY and self.snapshot is not None:
            lot = next((lot for lot in self.snapshot.lots if lot.id == t.id), None)
            if lot is not None and lot.sells:
                info(self.window(), "Cannot delete BUY",
                     f"{t.symbol} BUY #{lot.no} has {len(lot.sells)} sell(s). Delete the SELL trades first.")
                return
        desc = f"{t.side.value} {t.shares:,} {t.symbol} @ {t.price} on {t.trade_date.isoformat()}"
        if not confirm(self.window(), "Delete trade", f"Delete this trade?\n\n{desc}"):
            return
        try:
            self.ctx.repos.trades.delete(t.id)  # type: ignore[arg-type]
        except RepoError as e:
            info(self.window(), "Cannot delete", str(e))
            return
        self.ctx.data_changed(self.pid)
        self.ctx.toast("Trade deleted", desc, "info")

    def _lot_stops(self, node) -> list:
        """Stop changes of the lot behind a BUY or stop row (or the only open lot of a position row), oldest first."""
        lots = node.pv.position.lots
        if node.kind == "trade" and node.trade.side == Side.BUY:
            lots = [lot for lot in lots if lot.id == node.trade.id]
        elif node.kind == "stop":
            lots = [lot for lot in lots if lot.id == node.stop.lot_id]
        elif node.kind == "position":
            lots = [lot for lot in lots if not lot.is_closed]
            if len(lots) != 1:
                return []
        else:
            return []
        return sorted((s for lot in lots for s in lot.stops), key=lambda s: (s.effective_date, s.id or 0))

    def delete_stop(self, s) -> None:
        if s is None:
            return
        if confirm(self.window(), "Undo stop change",
                   f"Undo the stop change to {s.stop} on {s.effective_date.isoformat()}?", ok_text="Undo"):
            self.ctx.repos.stops.delete(s.id)
            self.ctx.data_changed(self.pid)

    def open_cash(self) -> None:  # no cash ledger: cash lives in a linked Cash portfolio account
        return

    def reveal(self, symbol: str) -> None:
        """Select (and expand) the open position for a symbol."""
        if self.filter.value() == "closed":
            self.filter.set_value("open")
        keys = self.model.position_keys()
        for r, key in enumerate(keys):
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
            a.toggled.connect(lambda on, col=i: (self.view.setColumnHidden(col, not on), self._save_columns_later()))
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
        m = QMenu(self)
        pv = node.pv
        if node.kind == "trade":
            m.addAction("Edit trade…", lambda: self.edit_trade(node.trade))
            m.addAction("Delete trade", lambda: self.delete_trade(node.trade))
            m.addSeparator()
        stops = self._lot_stops(node)
        if stops:
            last = stops[-1]
            prev = stops[-2].stop if len(stops) > 1 else None
            back = f" (back to {prev})" if prev is not None else " (back to the initial stop)"
            m.addAction(f"Undo stop change → {last.stop} on {last.effective_date.isoformat()}{back}",
                        lambda: self.delete_stop(last))
            m.addSeparator()
        if not pv.is_closed:
            lot_id = None
            shares = None
            a = pv.top_action
            if node.kind == "trade" and node.trade.side == Side.BUY:
                lot_id = node.trade.id
            elif node.kind == "stop":
                lot_id = node.stop.lot_id
            elif a is not None and a.lot_id is not None and a.shares:
                lot_id, shares = a.lot_id, a.shares
            m.addAction("Record sell…", lambda: self.record_sell(lot_id, shares, pv.symbol))
            m.addAction("Set stop…", lambda: self.set_stop(pv, lot_id))
            m.addAction("Add-on buy…", lambda: self.new_entry({"side": "BUY", "symbol": pv.symbol}))
        m.addAction("Stock splits…", lambda: self.review_splits(pv.symbol))
        m.addAction("Change symbol…", lambda: self.change_symbol(pv.symbol))
        m.addAction("Override price…", lambda: self.override_price(pv.symbol))
        m.addAction("Edit sector…", lambda: self.edit_sector(pv.symbol))
        m.addSeparator()
        m.addAction("Copy symbol", lambda: QGuiApplication.clipboard().setText(pv.symbol))
        m.exec(self.view.viewport().mapToGlobal(pos))

    def set_stop(self, pv, lot_id: int | None = None) -> None:
        from ...ui.dialogs.stop_dialog import StopDialog

        dlg = StopDialog(self.ctx, pv, lot_id, parent=self.window())
        if dlg.exec():
            self.ctx.data_changed(self.pid)

    def edit_sector(self, symbol: str) -> None:
        from ...ui.dialogs.sector_dialog import SectorDialog

        if SectorDialog(self.ctx, symbol, parent=self.window()).exec():
            self.ctx.events.market_updated.emit()  # every tab / the dashboard shows the new sector

    def review_splits(self, symbol: str | None = None) -> None:
        from ...ui.dialogs.splits_dialog import SplitsDialog

        SplitsDialog(self.ctx, self.portfolio, symbol, parent=self.window()).exec()

    def change_symbol(self, symbol: str) -> None:
        from ...ui.dialogs.symbol_picker_dialog import change_symbol

        change_symbol(self.ctx, symbol, self.window())

    def override_price(self, symbol: str | None = None) -> None:
        from ...ui.dialogs.price_override_dialog import PriceOverrideDialog

        dlg = PriceOverrideDialog(self.ctx, symbol, parent=self.window())
        if dlg.exec():
            self.ctx.events.market_updated.emit()

    # ------------------------------------------------------------------ export
    def export_csv(self, path: Path) -> int:
        """Write the current table (positions: visible columns in visual order; or the performance table) to CSV.
        Returns rows written."""
        headers, data = self.table_rows()
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(headers)
            w.writerows(data)
        return len(data)

    def table_rows(self) -> tuple[list[str], list[list[str]]]:
        if self.performance_mode:
            return self.perf.export_rows()
        h = self.view.header()
        cols = [h.logicalIndex(v) for v in range(h.count()) if not h.isSectionHidden(h.logicalIndex(v))]
        data = [[self.model.index(r, c).data() or "" for c in cols] for r in range(self.model.rowCount())]
        return [COLUMNS[c].title for c in cols], data

    def update_portfolio(self, p: Portfolio) -> None:
        self.portfolio = replace(p)
        self.reload()
