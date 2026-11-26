"""Cash page: KPI strip + accounts table (each account's ledger as child rows)."""

from __future__ import annotations

import base64
import csv
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QByteArray, QModelIndex, QPoint, Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QMenu, QStackedWidget, QVBoxLayout, QWidget

from ...context import AppContext
from ...core.cash import AccountView, CashSnapshot
from ...core.models import CashTxn, CashTxnKind, Portfolio
from ...core.stats import PERIOD_LABELS, PeriodKey, period_for
from ...data.repo import RepoError
from ...ui import format as fmt
from ...ui.dialogs.base import confirm, info
from ...ui.theme import tokens
from ...ui.widgets.common import button, frame, label
from ...ui.widgets.empty_state import EmptyState
from ...ui.widgets.kpi_card import KpiStrip
from ...ui.widgets.search_field import SearchField
from ...ui.widgets.segmented import Segmented
from ..stock_trading.positions_view import PositionsView
from .kpis import KPI_CARDS, compute_kpis
from .service import linked_names, load_cash
from .table_model import COLUMNS, NODE_ROLE, CashAccountsModel, action_buttons


class CashTab(QWidget):
    def __init__(self, ctx: AppContext, portfolio: Portfolio, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.portfolio = portfolio
        self.snapshot: CashSnapshot | None = None
        self._build()
        self._restore_ui_state()
        ctx.events.portfolio_data_changed.connect(self._on_data_changed)
        ctx.events.market_updated.connect(self.reload)
        ctx.events.settings_changed.connect(self._on_settings_changed)
        ctx.events.portfolios_changed.connect(self.reload)
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
        top_bar.addWidget(label("BALANCES & INTEREST", role="caption"))
        top_bar.addStretch(1)
        top_bar.addWidget(label("Period", role="muted"))
        self.period = QComboBox()
        for k in (PeriodKey.MTD, PeriodKey.QTD, PeriodKey.YTD, PeriodKey.L12M, PeriodKey.ALL):
            self.period.addItem(PERIOD_LABELS[k], k.value)
        self.period.setCurrentIndex(self.period.findData(PeriodKey.YTD.value))
        self.period.setToolTip("Period for interest, yield, net deposits and the Change column")
        self.period.currentIndexChanged.connect(lambda _i: (self._save_ui(), self._apply_period()))
        top_bar.addWidget(self.period)
        v.addLayout(top_bar)

        self.kpis = KpiStrip(KPI_CARDS)
        v.addWidget(self.kpis)

        panel = frame("table-panel")
        pv = QVBoxLayout(panel)
        pv.setContentsMargins(1, tokens.SPACE[2], 1, 1)
        pv.setSpacing(tokens.SPACE[2])
        bar = QHBoxLayout()
        bar.setContentsMargins(tokens.SPACE[3], 0, tokens.SPACE[3], 0)
        self.filter = Segmented([("open", "Open"), ("all", "All")])
        self.filter.setToolTip("All includes closed accounts")
        self.filter.changed.connect(self._on_filter)
        bar.addWidget(self.filter)
        self.search = SearchField("Search accounts  (Ctrl+F)")
        self.search.setFixedWidth(240)
        self.search.textChanged.connect(self._on_search)
        bar.addWidget(self.search)
        bar.addStretch(1)
        self.count_label = label("", role="muted")
        bar.addWidget(self.count_label)
        self.trueup_btn = button("True up", "ghost", icon="scale",
                                 tooltip="Check the selected account's balance against your bank")
        self.trueup_btn.clicked.connect(lambda: self.true_up(self._selected_view()))
        bar.addWidget(self.trueup_btn)
        self.link_btn = button("Link portfolio", "ghost", icon="arrow-left-right",
                               tooltip="Settle a Stock Trading or Long-term portfolio's trades and dividends in the "
                                       "selected account")
        self.link_btn.clicked.connect(lambda: self.link_portfolio(self._selected_view()))
        bar.addWidget(self.link_btn)
        self.columns_btn = button("Columns", "ghost", icon="columns-3")
        self.columns_btn.clicked.connect(self._columns_menu)
        bar.addWidget(self.columns_btn)
        pv.addLayout(bar)

        self.table_stack = QStackedWidget()
        self.empty = EmptyState("No cash accounts yet", "Add a savings account or money market fund with "
                                "today's balance.", "New Account", icon="piggy-bank")
        self.empty.action.connect(lambda: self.new_entry())
        self.no_match = EmptyState("No matching accounts", "Try another filter or search.", icon="search")
        self.model = CashAccountsModel(self)
        self.view = PositionsView(columns=COLUMNS, buttons=action_buttons)
        self.view.setModel(self.model)
        self.model.modelReset.connect(self._after_reset)
        self.view.activated_child.connect(self._on_child_activated)
        self.view.activated_position.connect(self._on_account_activated)
        self.view.customContextMenuRequested.connect(self._context_menu)
        self.view.action_clicked.connect(self._on_action_clicked)
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
        if ui.get("filter") in ("open", "all"):
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
        # A linked portfolio's trades and dividends move money in these accounts too.
        link = self.ctx.repos.cash_accounts.link_for(pid)
        if pid == self.pid or (link is not None and self.snapshot is not None
                               and self.snapshot.get(link.account_id) is not None):
            self.reload()

    def _period(self):
        return period_for(self.period.currentData(), self.ctx.today())

    def reload(self) -> None:
        p = self.ctx.repos.portfolios.get(self.pid)
        if p is None:
            return
        self.portfolio = p
        expanded = self.view.expanded_keys() if self.model.rowCount() else set()
        current = self._current_key()
        self.snapshot = load_cash(self.ctx.repos, self.portfolio, self.ctx.today())
        ids = [a.id for a in self.snapshot.accounts]
        self.model.linked = linked_names(self.ctx.repos, ids)
        self.model.transfer_names = self._transfer_names()
        self.model.period = self._period()
        self.model.set_snapshot(self.snapshot)
        self.view.restore_expanded(expanded)
        if current:
            idx = self.model.position_index(current)
            if idx.isValid():
                self.view.setCurrentIndex(idx)
        self.refresh_kpis()

    def _transfer_names(self) -> dict[int, str]:
        """Transfer txn id → the account on the other side (any Cash portfolio)."""
        snap = self.snapshot
        if snap is None:
            return {}
        out = {}
        repo = self.ctx.repos.cash_accounts
        for v in snap.accounts:
            for t in v.txns:
                if t.kind == CashTxnKind.TRANSFER and t.transfer_id:
                    other_v = next((x for x in snap.accounts for y in x.txns if y.id == t.transfer_id), None)
                    if other_v is not None:
                        out[t.transfer_id] = other_v.account.name
                    else:
                        o = repo.get_txn(t.transfer_id)
                        a = repo.get(o.account_id) if o else None
                        if a is not None:
                            out[t.transfer_id] = a.name
        return out

    def _apply_period(self) -> None:
        self.model.set_period(self._period())
        self.refresh_kpis()

    def refresh_kpis(self) -> None:
        if self.snapshot is None:
            return
        self.kpis.update_cards(compute_kpis(self.snapshot, self._period(), self.period.currentData()))

    def _after_reset(self) -> None:
        total = self.model.total_positions()
        shown = self.model.rowCount()
        self.table_stack.setCurrentWidget(self.empty if total == 0 else self.no_match if shown == 0 else self.view)
        n_open = len(self.snapshot.open_accounts) if self.snapshot else 0
        self.count_label.setText(f"{n_open} open · {shown} shown" if total else "")
        self.trueup_btn.setVisible(total > 0)
        self.link_btn.setVisible(total > 0)
        sort_col = self.view.header().sortIndicatorSection()
        if 0 <= sort_col < len(COLUMNS) and self.model._sort is None:
            self.model._sort = (sort_col, self.view.header().sortIndicatorOrder())
            QTimer.singleShot(0, self.model._rebuild)

    def _on_filter(self, mode: str) -> None:
        self.model.set_filter(mode)
        self._save_ui()

    def _on_search(self, text: str) -> None:
        self.model.set_search(text)

    def set_show_closed(self, show: bool) -> None:
        self.filter.set_value("all" if show else "open")

    # ------------------------------------------------------------------ selection
    def _current_key(self) -> str | None:
        node = self.model.node(self.view.currentIndex())
        return node.pv.key if node else None

    def selected_node(self):
        idx = self.view.currentIndex()
        return idx.data(NODE_ROLE) if idx.isValid() else None

    def _selected_view(self) -> AccountView | None:
        node = self.selected_node()
        if node is not None:
            return node.pv
        snap = self.snapshot
        return snap.open_accounts[0] if snap and len(snap.open_accounts) == 1 else None

    def _on_child_activated(self, index: QModelIndex) -> None:
        node = index.data(NODE_ROLE)
        if node is not None and node.flow is not None:
            self.edit_flow(node)

    def _on_account_activated(self, idx: QModelIndex) -> None:
        node = idx.data(NODE_ROLE)
        if node is not None and COLUMNS[idx.column()].key == "linked":
            self.link_portfolio(node.pv)
            return
        top = idx.siblingAtColumn(0)
        self.view.setExpanded(top, not self.view.isExpanded(top))

    def _on_action_clicked(self, key: str, index: QModelIndex) -> None:
        node = index.data(NODE_ROLE)
        if node is None:
            return
        if key == "dividend":
            self.confirm_interest(node.pv)
        elif key == "trueup":
            self.true_up(node.pv)

    # ------------------------------------------------------------------ actions
    def new_entry(self, prefill: dict | None = None) -> None:
        from ...ui.dialogs.cash_account_dialogs import CashAccountDialog

        dlg = CashAccountDialog(self.ctx, self.portfolio, parent=self.window())
        if dlg.exec() and dlg.result is not None:
            a = dlg.result
            idx = self.model.position_index(f"acct:{a.id}")
            if idx.isValid():
                self.view.setCurrentIndex(idx)
            self.ctx.toast("Account added", f"{a.name} · {fmt.money(dlg.balance.value())}", "success")
            if a.is_money_market and not a.manual_rate and self.ctx.market is not None:
                self.ctx.market.refresh()  # fetch the fund's yield

    def edit_account(self, v: AccountView | None) -> None:
        from ...ui.dialogs.cash_account_dialogs import CashAccountDialog

        if v is None:
            return
        if CashAccountDialog(self.ctx, self.portfolio, v, parent=self.window()).exec():
            self.ctx.toast("Account saved", v.account.name, "success")

    def open_cash(self) -> None:
        """Move money (deposit / withdrawal / transfer) for the selected account."""
        from ...ui.dialogs.cash_account_dialogs import MoveMoneyDialog

        snap = self.snapshot
        if snap is None or not snap.open_accounts:
            info(self.window(), "Move money", "Add an account first.")
            return
        v = self._selected_view()
        dlg = MoveMoneyDialog(self.ctx, self.portfolio, snap, v.id if v else None, parent=self.window())
        if dlg.exec():
            self.ctx.toast("Recorded", f"{dlg.kind.value().title()} {fmt.money(dlg.amount.value())}", "success")

    def true_up(self, v: AccountView | None) -> None:
        from ...ui.dialogs.cash_account_dialogs import TrueUpDialog

        if v is None:
            info(self.window(), "True up", "Select an account first.")
            return
        dlg = TrueUpDialog(self.ctx, self.portfolio, v, parent=self.window())
        if dlg.exec() and dlg.result is not None:
            t = dlg.result
            self.ctx.toast(f"{v.account.name} trued up", "Balance matched" if not t.amount else
                           f"{fmt.signed_money(t.amount)} booked as {t.kind.value.lower()}", "success")

    def confirm_interest(self, v: AccountView) -> int:
        """Confirm each pending interest payment of an account in turn (oldest first)."""
        from ...ui.dialogs.cash_account_dialogs import ConfirmInterestDialog

        n, seen = 0, set()
        while True:
            # Re-read after each one: a confirmed payment earns interest in the next period.
            fresh = self.snapshot.get(v.id) if self.snapshot else None
            if fresh is None:
                break
            p = next((p for p in fresh.proposals() if p.period_end not in seen), None)
            if p is None:
                break
            seen.add(p.period_end)
            dlg = ConfirmInterestDialog(self.ctx, self.portfolio, fresh, p, parent=self.window())
            if not dlg.exec() or dlg.result is None:
                break
            n += 1
            amt = dlg.result.amount
            self.ctx.toast(f"{v.account.name} interest", fmt.money(amt) if amt else "Skipped", "success")
        return n

    def link_portfolio(self, v: AccountView | None) -> None:
        from ...ui.dialogs.cash_account_dialogs import LinkPortfolioDialog

        if v is None:
            info(self.window(), "Link portfolio", "Select an account first.")
            return
        dlg = LinkPortfolioDialog(self.ctx, self.portfolio, v, parent=self.window())
        dlg.exec()
        if dlg.linked_name:
            self.ctx.toast("Portfolio linked", f"{dlg.linked_name} settles in {v.account.name}", "success")
        if dlg.changed:
            self.reload()

    def change_rate(self, v: AccountView) -> None:
        from ...ui.dialogs.cash_account_dialogs import RateDialog

        RateDialog(self.ctx, self.portfolio, v, parent=self.window()).exec()

    def edit_flow(self, node) -> None:
        from ...ui.dialogs.cash_account_dialogs import MoveMoneyDialog

        f = node.flow
        t: CashTxn | None = f.txn
        if t is None:
            info(self.window(), "From a linked portfolio",
                 f"{f.label} comes from {f.source}. Edit or delete it there; the cash balance follows.")
            return
        if t.kind == CashTxnKind.OPENING:
            self.edit_account(node.pv)
            return
        if t.is_true_up:
            info(self.window(), "True-up", "A true-up can't be edited. Delete it and true up again.")
            return
        if self.snapshot is not None:
            MoveMoneyDialog(self.ctx, self.portfolio, self.snapshot, t.account_id, txn=t, parent=self.window()).exec()

    def edit_selected(self) -> None:
        node = self.selected_node()
        if node is None:
            return
        if node.kind == "flow":
            self.edit_flow(node)
        else:
            self.edit_account(node.pv)

    def delete_selected(self) -> None:
        node = self.selected_node()
        if node is None:
            return
        if node.kind == "flow":
            self.delete_flow(node)
        else:
            self.delete_account(node.pv)

    def delete_flow(self, node) -> None:
        f = node.flow
        t: CashTxn | None = f.txn
        if t is None:
            self.edit_flow(node)
            return
        from .table_model import flow_title

        what = flow_title(f).split(" ", 1)[-1].lower()
        extra = ""
        if t.kind == CashTxnKind.TRANSFER:
            extra = "\n\nBoth sides of the transfer are deleted."
        elif t.kind == CashTxnKind.INTEREST and t.period_end and not t.is_true_up:
            extra = "\n\nThe interest for that month will be proposed again."
        desc = f"{what} {fmt.signed_money(t.amount)} on {fmt.short_date(t.txn_date)}"
        if not confirm(self.window(), "Delete entry", f"Delete this entry?\n\n{desc}{extra}"):
            return
        try:
            self.ctx.repos.cash_accounts.delete_txn(t.id)  # type: ignore[arg-type]
        except RepoError as e:
            info(self.window(), "Cannot delete", str(e))
            return
        self.ctx.data_changed(self.pid)
        self.ctx.toast("Entry deleted", desc, "info")

    def delete_account(self, v: AccountView) -> None:
        links = self.model.linked.get(v.id, [])
        extra = (f"\n\nIt is linked to {', '.join(n for n, _ in links)}; those portfolios will no longer settle "
                 "in a cash account." if links else "")
        if not confirm(self.window(), "Delete account",
                       f"Delete “{v.account.name}” and its whole ledger ({len(v.txns)} entries)? Transfers with "
                       f"other accounts stay there as deposits or withdrawals.\n\nTo keep its history, edit it and "
                       f"mark it closed instead.{extra}"):
            return
        self.ctx.repos.cash_accounts.delete(v.id)
        self.ctx.data_changed(self.pid)
        self.ctx.toast("Account deleted", v.account.name, "info")

    def toggle_detail(self) -> None:  # no detail pane in this portfolio type
        return

    def override_price(self, symbol: str | None = None) -> None:
        from ...ui.dialogs.price_override_dialog import PriceOverrideDialog

        if PriceOverrideDialog(self.ctx, symbol, parent=self.window()).exec():
            self.ctx.events.market_updated.emit()

    def reveal(self, name: str) -> None:
        for r, key in enumerate(self.model.position_keys()):
            node = self.model.node(self.model.index(r, 0))
            if node is not None and name in (node.pv.account.name, node.pv.account.symbol, key):
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
            if c.key == "name":
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
        v: AccountView = node.pv
        m = QMenu(self)
        if node.kind == "flow":
            t = node.flow.txn
            if t is None:
                m.addAction(f"From {node.flow.source}: edit it there", lambda: self.edit_flow(node))
            elif t.kind != CashTxnKind.OPENING:
                if not t.is_true_up:
                    m.addAction("Edit entry…", lambda: self.edit_flow(node))
                m.addAction("Delete entry", lambda: self.delete_flow(node))
            m.addSeparator()
        if not v.account.closed:
            m.addAction("Move money…", lambda: self.open_cash())
            if self.model.proposals(v):
                m.addAction("Confirm interest…", lambda: self.confirm_interest(v))
            m.addAction("True up…", lambda: self.true_up(v))
            m.addAction("Change rate…", lambda: self.change_rate(v))
            m.addAction("Link portfolio…", lambda: self.link_portfolio(v))
            m.addSeparator()
        m.addAction("Edit account…", lambda: self.edit_account(v))
        m.addAction("Delete account…", lambda: self.delete_account(v))
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
        data = [[(self.model.index(r, c).data() or "").strip() for c in cols] for r in range(self.model.rowCount())]
        return [COLUMNS[c].title for c in cols], data

    def update_portfolio(self, p: Portfolio) -> None:
        self.portfolio = replace(p)
        self.reload()
