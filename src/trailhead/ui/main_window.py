"""Main application window: sidebar + header + stacked pages, menus and shortcuts (PLAN §7.1, §7.7)."""

from __future__ import annotations

import base64
import contextlib
import logging
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QByteArray, QPoint, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QMainWindow,
    QMenu,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..context import AppContext
from ..core.models import Portfolio
from ..portfolio_types import registry
from .dialogs.base import confirm, info
from .header import Header
from .theme import tokens
from .widgets.empty_state import EmptyState
from .widgets.sidebar import Sidebar
from .widgets.toast import ToastHost

log = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    discord_done = Signal(object)  # str error or None, emitted from the sender thread
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle("Trailhead")
        self.resize(1480, 920)
        self.setMinimumSize(980, 620)
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        self.sidebar = Sidebar()
        h.addWidget(self.sidebar)
        content = QWidget()
        v = QVBoxLayout(content)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.header = Header()
        v.addWidget(self.header)
        self.stack = QStackedWidget()
        v.addWidget(self.stack, 1)
        h.addWidget(content, 1)
        self.content = content
        self.toasts = ToastHost(content)
        self.pages: dict[str, QWidget] = {}
        self.portfolios: dict[int, Portfolio] = {}
        self.actions: dict[str, QAction] = {}
        self._build_menus()
        self._wire()
        self.dashboard = self._create_dashboard()
        self.add_page("dashboard", self.dashboard, "Dashboard")
        self.reload_portfolios()
        self._restore_geometry()
        self._setup_market()
        self._setup_alerts()
        self._setup_backups()

    # ------------------------------------------------------------------ pages
    def _create_dashboard(self) -> QWidget:
        try:
            from .dashboard import DashboardPage
        except ImportError:
            return placeholder_page("Dashboard", "Charts and cross-portfolio totals appear here.")
        page = DashboardPage(self.ctx)
        page.open_portfolio.connect(lambda pid: self.show_page(f"portfolio:{pid}"))
        return page

    def add_page(self, key: str, widget: QWidget, title: str) -> None:
        widget.setProperty("page_title", title)
        self.pages[key] = widget
        self.stack.addWidget(widget)

    def remove_page(self, key: str) -> None:
        w = self.pages.pop(key, None)
        if w is not None:
            self.stack.removeWidget(w)
            w.deleteLater()

    def show_page(self, key: str) -> None:
        w = self.pages.get(key)
        if w is None:
            return
        self.stack.setCurrentWidget(w)
        self.sidebar.set_current(key)
        self.header.set_title(w.property("page_title") or "")
        is_pf = key.startswith("portfolio:")
        self.header.set_portfolio_actions_enabled(is_pf)
        pf = self.portfolios.get(int(key.split(":")[1])) if is_pf else None
        ptype = registry.get(pf.type) if pf is not None else None
        has_cash = ptype is not None and ptype.has_cash_ledger
        self.header.cash_btn.setVisible(has_cash)
        self.header.set_type_labels(ptype.entry_label if ptype else "New Trade", ptype.cash_label if ptype else "Cash")
        self.actions["new_trade"].setText(f"{ptype.entry_label if ptype else 'New trade'}…".capitalize())
        self.actions["cash"].setText(f"{ptype.cash_label if ptype else 'Cash'}…".capitalize())
        for k in ("new_trade", "cash", "edit_trade", "delete_trade", "find", "export_csv", "export_json",
                  "columns", "show_closed"):
            if k in self.actions:
                self.actions[k].setEnabled(is_pf)
        self.actions["cash"].setEnabled(has_cash)
        stops = self.actions.get("show_stop_changes")
        if stops is not None:  # only portfolio types that list stop changes offer the toggle
            stops.setEnabled(hasattr(w, "set_show_stop_changes"))
            stops.blockSignals(True)
            stops.setChecked(bool(getattr(w, "show_stop_changes", False)))
            stops.blockSignals(False)
        self.ctx.repos.settings.set("ui.last_page", key)
        if hasattr(w, "on_shown"):
            w.on_shown()
        view = getattr(w, "view", None)
        (view if view is not None else w).setFocus()  # keep the initial focus off header buttons

    def current_page_key(self) -> str:
        w = self.stack.currentWidget()
        for k, v in self.pages.items():
            if v is w:
                return k
        return ""

    def current_tab(self):
        key = self.current_page_key()
        return self.pages.get(key) if key.startswith("portfolio:") else None

    def current_portfolio(self) -> Portfolio | None:
        key = self.current_page_key()
        if key.startswith("portfolio:"):
            return self.portfolios.get(int(key.split(":")[1]))
        return None

    def reload_portfolios(self) -> None:
        portfolios = self.ctx.repos.portfolios.list()
        if getattr(self, "_loaded", False):
            current = self.current_page_key()
        else:
            self._loaded = True
            current = self.ctx.repos.settings.get("ui.last_page") or (
                f"portfolio:{portfolios[0].id}" if portfolios else "dashboard")
        wanted = {f"portfolio:{p.id}" for p in portfolios}
        for key in [k for k in self.pages if k.startswith("portfolio:") and k not in wanted]:
            self.remove_page(key)
        self.portfolios = {p.id: p for p in portfolios}  # type: ignore[misc]
        for p in portfolios:
            key = f"portfolio:{p.id}"
            if key not in self.pages:
                self.add_page(key, registry.get(p.type).create_tab(self.ctx, p), p.name)
            else:
                self.pages[key].setProperty("page_title", p.name)
                if hasattr(self.pages[key], "update_portfolio"):
                    self.pages[key].update_portfolio(p)
        items = [(p.id, p.name, registry.get(p.type).icon) for p in portfolios]
        self.sidebar.set_portfolios(items)  # type: ignore[arg-type]
        self.header.set_switch_targets([("dashboard", "Dashboard")] +
                                       [(f"portfolio:{p.id}", p.name) for p in portfolios])
        if current not in self.pages:
            current = f"portfolio:{portfolios[0].id}" if portfolios else "dashboard"
        self.show_page(current)

    # ------------------------------------------------------------------ menus
    def _act(self, key: str, text: str, shortcut: str | QKeySequence.StandardKey | None = None,
             slot=None, enabled: bool = True) -> QAction:
        a = QAction(text, self)
        if shortcut is not None:
            a.setShortcut(QKeySequence(shortcut))
        a.setEnabled(enabled)
        if slot is not None:
            a.triggered.connect(slot)
        self.actions[key] = a
        self.addAction(a)
        return a

    def _build_menus(self) -> None:
        mb = self.menuBar()
        f = mb.addMenu("&File")
        f.addAction(self._act("new_portfolio", "New portfolio…", None, self.new_portfolio))
        f.addSeparator()
        f.addAction(self._act("backup_now", "Backup now", "Ctrl+B", lambda: self._call("backup_now")))
        f.addAction(self._act("backup_save_as", "Backup as…", None, lambda: self._call("backup_save_as")))
        f.addAction(self._act("restore", "Restore…", None, lambda: self._call("restore")))
        f.addSeparator()
        f.addAction(self._act("export_json", "Export portfolio JSON…", None, lambda: self._call("export_json")))
        f.addAction(self._act("import_json", "Import portfolio JSON…", None, lambda: self._call("import_json")))
        f.addAction(self._act("export_csv", "Export table CSV…", None, self.export_csv))
        f.addAction(self._act("import_legacy", "Import legacy spreadsheet CSV…", None,
                              lambda: self._call("import_legacy")))
        f.addSeparator()
        f.addAction(self._act("quit", "Quit", QKeySequence.StandardKey.Quit, self.close))
        e = mb.addMenu("&Edit")
        e.addAction(self._act("new_trade", "New trade…", "Ctrl+N", lambda: self._tab_call("new_entry")))
        e.addAction(self._act("cash", "Cash…", "Ctrl+D", lambda: self._tab_call("open_cash")))
        e.addSeparator()
        e.addAction(self._act("edit_trade", "Edit trade…", "Ctrl+E", lambda: self._tab_call("edit_selected")))
        e.addAction(self._act("delete_trade", "Delete trade", "Del", lambda: self._tab_call("delete_selected")))
        e.addSeparator()
        e.addAction(self._act("settings", "Settings…", "Ctrl+,", lambda: self._call("open_settings")))
        vm = mb.addMenu("&View")
        dens = vm.addMenu("Density")
        for key, text, val in (("density_compact", "Compact", "compact"), ("density_comfy", "Comfortable", "comfy")):
            a = self._act(key, text, None, lambda _=False, v=val: self.set_density(v))
            a.setCheckable(True)
            dens.addAction(a)
        vm.addAction(self._act("columns", "Columns…", None, lambda: self._tab_call("show_columns_menu")))
        sc = self._act("show_closed", "Show closed", None)
        sc.setCheckable(True)
        sc.toggled.connect(lambda on: self._tab_call("set_show_closed", on))
        vm.addAction(sc)
        ss = self._act("show_stop_changes", "Show stop changes", None)
        ss.setCheckable(True)
        ss.setChecked(True)
        ss.toggled.connect(lambda on: self._tab_call("set_show_stop_changes", on))
        vm.addAction(ss)
        vm.addSeparator()
        vm.addAction(self._act("find", "Search symbol", "Ctrl+F", lambda: self._tab_call("focus_search")))
        vm.addAction(self._act("toggle_sidebar", "Collapse sidebar", "Ctrl+\\",
                               lambda: self.sidebar.set_collapsed(not self.sidebar.is_collapsed())))
        vm.addAction(self._act("toggle_detail", "Toggle detail pane", "Ctrl+J",
                               lambda: self._tab_call("toggle_detail")))
        vm.addAction(self._act("alerts_panel", "Alerts…", None, lambda: self._call("toggle_alerts")))
        vm.addAction(self._act("dashboard", "Dashboard", "Ctrl+0", lambda: self.show_page("dashboard")))
        d = mb.addMenu("&Data")
        d.addAction(self._act("refresh", "Refresh prices", "F5", lambda: self._call("refresh_prices")))
        d.addAction(self._act("pending_dividends", "Pending dividends…", None,
                              lambda: self._call("pending_dividends")))
        d.addAction(self._act("stock_splits", "Stock splits…", None, lambda: self._call("stock_splits")))
        d.addSeparator()
        d.addAction(self._act("manual_price", "Manual price…", None, self.manual_price))
        d.addAction(self._act("manual_dividend", "Manual dividend…", None, lambda: self._call("manual_dividend")))
        hm = mb.addMenu("&Help")
        hm.addAction(self._act("shortcuts", "Keyboard shortcuts", None, self.show_shortcuts))
        hm.addAction(self._act("about", "About Trailhead", None, self.show_about))
        for i in range(1, 10):
            self._act(f"switch_{i}", f"Switch to portfolio {i}", f"Ctrl+{i}", lambda _=False, n=i: self.switch_to(n))
        density = self.ctx.repos.settings.get("ui.table_density") or "compact"
        self.actions["density_compact"].setChecked(density == "compact")
        self.actions["density_comfy"].setChecked(density == "comfy")

    def _wire(self) -> None:
        self.sidebar.selected.connect(self.show_page)
        self.sidebar.new_portfolio.connect(self.new_portfolio)
        self.sidebar.open_settings.connect(lambda: self._call("open_settings"))
        self.sidebar.context_requested.connect(self._portfolio_menu)
        self.sidebar.collapsed_changed.connect(self._on_sidebar_collapsed)
        self.sidebar.set_collapsed(bool(self.ctx.setting("ui.sidebar_collapsed", False)), emit=False)
        self._sync_sidebar_action()
        self.header.switch_requested.connect(self.show_page)
        self.header.new_trade_clicked.connect(lambda: self._tab_call("new_entry"))
        self.header.cash_clicked.connect(lambda: self._tab_call("open_cash"))
        self.header.refresh_clicked.connect(lambda: self._call("refresh_prices"))
        self.header.bell_clicked.connect(lambda: self._call("toggle_alerts"))
        self.ctx.events.toast.connect(self.toasts.show)
        self.ctx.events.portfolios_changed.connect(self.reload_portfolios)
        self.ctx.events.open_page.connect(self.show_page)

    # Hooks implemented by later phases (market, alerts, backup) are looked up as methods on self.
    def _call(self, name: str, *args) -> None:
        fn = getattr(self, name, None)
        if fn is None:
            self.ctx.toast("Not available yet", f"'{name.replace('_', ' ')}' arrives in a later phase.", "info")
            return
        fn(*args)

    def _tab_call(self, name: str, *args) -> None:
        tab = self.current_tab()
        if tab is None:
            if name == "new_entry" and not self.portfolios:
                self.new_portfolio()
            return
        fn = getattr(tab, name, None)
        if fn is not None:
            fn(*args)

    # ------------------------------------------------------------------ commands
    def new_portfolio(self) -> None:
        from .dialogs.portfolio_dialog import PortfolioDialog

        dlg = PortfolioDialog(self.ctx, parent=self)
        if dlg.exec() and dlg.result is not None:
            self.show_page(f"portfolio:{dlg.result.id}")
            self.ctx.toast("Portfolio created", f"{dlg.result.name} · add your first trade to get started", "success")

    def edit_portfolio(self, pid: int) -> None:
        from .dialogs.portfolio_dialog import PortfolioDialog

        p = self.ctx.repos.portfolios.get(pid)
        if p is None:
            return
        dlg = PortfolioDialog(self.ctx, p, parent=self)
        if dlg.exec():
            self.ctx.data_changed(pid)

    def archive_portfolio(self, pid: int) -> None:
        p = self.ctx.repos.portfolios.get(pid)
        if p is None:
            return
        if confirm(self, "Archive portfolio", f"Archive “{p.name}”? It is hidden from the sidebar and dashboard. "
                   "Its data is kept and is included in backups.", ok_text="Archive", danger=False):
            self.ctx.repos.portfolios.update(replace(p, archived=True))
            self.ctx.events.portfolios_changed.emit()

    def _portfolio_menu(self, pid: int, pos: QPoint) -> None:
        m = QMenu(self)
        m.addAction("Open", lambda: self.show_page(f"portfolio:{pid}"))
        m.addAction("Rename / edit settings…", lambda: self.edit_portfolio(pid))
        m.addAction("Export JSON…", lambda: self._call("export_json", pid))
        m.addSeparator()
        m.addAction("Archive", lambda: self.archive_portfolio(pid))
        m.exec(pos)

    def _on_sidebar_collapsed(self, collapsed: bool) -> None:
        self.ctx.repos.settings.set("ui.sidebar_collapsed", collapsed)
        self._sync_sidebar_action()

    def _sync_sidebar_action(self) -> None:
        self.actions["toggle_sidebar"].setText(
            "Expand sidebar" if self.sidebar.is_collapsed() else "Collapse sidebar")

    def switch_to(self, n: int) -> None:
        ps = list(self.portfolios.values())
        if 1 <= n <= len(ps):
            self.show_page(f"portfolio:{ps[n - 1].id}")

    def set_density(self, value: str) -> None:
        self.ctx.repos.settings.set("ui.table_density", value)
        self.actions["density_compact"].setChecked(value == "compact")
        self.actions["density_comfy"].setChecked(value == "comfy")
        self.ctx.events.settings_changed.emit()

    def manual_price(self) -> None:
        tab = self.current_tab()
        if tab is not None:
            tab.override_price(None)
            return
        from .dialogs.price_override_dialog import PriceOverrideDialog

        if PriceOverrideDialog(self.ctx, None, parent=self).exec():
            self.ctx.events.market_updated.emit()

    def export_csv(self) -> None:
        tab = self.current_tab()
        if tab is None:
            return
        p = self.current_portfolio()
        name = (p.name if p else "positions").replace(" ", "_")
        path, filt = QFileDialog.getSaveFileName(self, "Export table", str(Path.home() / f"{name}.csv"),
                                                 "CSV (*.csv);;Excel (*.xlsx)")
        if not path:
            return
        out = Path(path)
        if out.suffix.lower() == ".xlsx" or "xlsx" in filt:
            try:
                from openpyxl import Workbook
            except ImportError:
                info(self, "Excel export", "Install the optional 'openpyxl' package for .xlsx export. Saved CSV.")
                out = out.with_suffix(".csv")
            else:
                headers, rows = tab.table_rows()
                wb = Workbook()
                ws = wb.active
                ws.append(headers)
                for r in rows:
                    ws.append(r)
                wb.save(out.with_suffix(".xlsx"))
                self.ctx.toast("Exported", str(out.with_suffix(".xlsx")), "success")
                return
        n = tab.export_csv(out)
        self.ctx.toast("Exported", f"{n} rows → {out}", "success")

    def show_shortcuts(self) -> None:
        info(self, "Keyboard shortcuts",
             "Ctrl+N  New trade\nCtrl+D  Cash\nF5  Refresh prices\nCtrl+F  Search symbol\n"
             "Enter / Space  Expand row\nCtrl+E  Edit trade\nDel  Delete trade\nCtrl+B  Backup now\n"
             "Ctrl+1…9  Switch portfolio\nCtrl+0  Dashboard\nCtrl+\\  Collapse / expand sidebar\n"
             "In the trade dialog: B / S switch BUY / SELL")

    def show_about(self) -> None:
        from .. import __version__

        info(self, "About Trailhead",
             f"Trailhead {__version__}\nStock-trading (R-multiple) and long-term holdings tracker.\n\n"
             "Market data: Yahoo Finance via yfinance (unofficial).\nFonts: Inter, JetBrains Mono (SIL OFL).\n"
             "Icons: Lucide (ISC).")

    # ------------------------------------------------------------------ market data
    def _setup_market(self) -> None:
        m = self.ctx.market
        if m is None:
            return
        m.started.connect(lambda: self.header.chip.set_status("loading", "Refreshing…", "Fetching prices"))
        m.refreshed.connect(self._on_refreshed)
        m.failed.connect(self._on_refresh_failed)
        m.busy_changed.connect(lambda b: self.actions["refresh"].setEnabled(not b))
        m.busy_changed.connect(lambda b: self.header.refresh_btn.setEnabled(not b))
        self._auto_timer = QTimer(self)
        self._auto_timer.timeout.connect(self._auto_refresh)
        self._configure_auto_refresh()
        self.ctx.events.settings_changed.connect(self._configure_auto_refresh)
        self.update_price_chip()

    def _configure_auto_refresh(self) -> None:
        minutes = int(self.ctx.setting("refresh.auto_minutes") or 0)
        if minutes > 0:
            self._auto_timer.start(minutes * 60_000)
        else:
            self._auto_timer.stop()

    def _auto_refresh(self) -> None:
        from datetime import UTC, datetime

        from ..market.service import in_market_hours

        if in_market_hours(datetime.now(UTC)):
            self.refresh_prices(quiet=True)

    def startup(self) -> None:
        """Called once after the window is shown."""
        if self.ctx.setting("refresh.on_startup") and self.ctx.market is not None and self.portfolios:
            self.refresh_prices(quiet=True)

    def refresh_prices(self, quiet: bool = False) -> None:
        if self.ctx.market is None:
            return
        self._quiet_refresh = quiet
        if not self.ctx.market.refresh():
            self.ctx.toast("Refresh already running", "", "info")

    def _on_refreshed(self, res) -> None:
        self.update_price_chip()
        self.ctx.events.market_updated.emit()
        QTimer.singleShot(300, self.ask_missing_symbols)
        if res.ok and not getattr(self, "_quiet_refresh", False):
            extra = f" · {len(res.errors)} warning(s)" if res.errors else ""
            self.ctx.toast("Prices refreshed", f"{res.quotes} quotes from {res.source}{extra}", "success")
        elif res.ok and res.errors:
            self.ctx.toast("Some market data failed", "\n".join(res.errors[:3]), "warning")

    def missing_price_symbols(self) -> list[str]:
        """Held symbols with no price, not yet asked about this session nor kept by the user."""
        from ..core.positions import build_lots

        if self.ctx.setting("market.provider") == "manual":
            return []
        held = {lot.symbol for p in self.portfolios.values()
                for lot in build_lots(self.ctx.repos.trades.list(p.id)) if not lot.is_closed}  # type: ignore[arg-type]
        quotes = self.ctx.repos.market.get_quotes(list(held))
        skip = set(self.ctx.setting("market.keep_symbols") or []) | getattr(self, "_asked_symbols", set())
        return sorted(s for s in held if s not in quotes and s not in skip)

    def ask_missing_symbols(self) -> None:
        """After a refresh, ask once per symbol for the right Yahoo symbol of holdings that got no price."""
        from .dialogs.symbol_picker_dialog import change_symbol

        if getattr(self, "_asking_symbols", False) or self.ctx.market is None or not self.isVisible():
            return
        self._asking_symbols = True
        try:
            for sym in self.missing_price_symbols():
                self._asked_symbols = getattr(self, "_asked_symbols", set()) | {sym}
                if change_symbol(self.ctx, sym, self, keep_text=f"Keep {sym}", no_price=True) is None:
                    kept = set(self.ctx.setting("market.keep_symbols") or []) | {sym}
                    self.ctx.repos.settings.set("market.keep_symbols", sorted(kept))
                    self.ctx.toast(f"Keeping {sym}", "Enter its price with right-click ▸ Override price, or "
                                   "double-click its No price badge to look again.", "info")
                else:
                    break  # renamed: a refresh is running; the next one asks about the rest
        finally:
            self._asking_symbols = False

    def _on_refresh_failed(self, errors: list) -> None:
        self.update_price_chip()
        self.ctx.toast("Price refresh failed — showing cached values", "\n".join(str(e) for e in errors[:3]),
                       "error")

    def update_price_chip(self) -> None:
        from datetime import datetime

        from ..market.service import price_status

        state, text, tip = price_status(self.ctx.setting("market.status"), datetime.now(), bool(self.portfolios))
        self.header.chip.set_status(state, text, tip)

    def open_settings(self) -> None:
        from .dialogs.settings_dialog import SettingsDialog

        dlg = SettingsDialog(self.ctx, parent=self)
        if dlg.exec():
            self.ctx.events.settings_changed.emit()
            self.update_price_chip()

    # ------------------------------------------------------------------ alerts
    def _setup_alerts(self) -> None:
        from ..alerts.discord import DiscordSender
        from ..alerts.notifier import Notifier
        from .dialogs.alerts_panel import AlertsPanel

        self.notifier = Notifier()
        self.discord = DiscordSender()
        self.discord_done.connect(self._on_discord_done)
        self.alerts_panel = AlertsPanel(self.ctx, self.content)
        self.alerts_panel.open_symbol.connect(self.reveal_symbol)
        self._alert_timer = QTimer(self)
        self._alert_timer.setSingleShot(True)
        self._alert_timer.setInterval(250)
        self._alert_timer.timeout.connect(self.run_alerts)
        self.ctx.events.portfolio_data_changed.connect(lambda _pid: self._alert_timer.start())
        self.ctx.events.market_updated.connect(self._alert_timer.start)
        self.ctx.events.settings_changed.connect(self._alert_timer.start)
        self.ctx.events.alerts_changed.connect(self.update_bell)
        if self.ctx.alerts is not None:
            self.ctx.alerts.new_alerts.connect(self._on_new_alerts)
            self.ctx.alerts.changed.connect(self.update_bell)
        self.update_bell()

    def run_alerts(self) -> list:
        if self.ctx.alerts is None:
            return []
        return self.ctx.alerts.run()

    def update_bell(self) -> None:
        self.header.bell.set_count(self.ctx.repos.alerts.count_unacked())
        if self.alerts_panel.isVisible():
            self.alerts_panel.reload()

    def _on_new_alerts(self, alerts: list) -> None:
        sev_map = {"critical": "error", "warning": "warning", "info": "info"}
        for a in alerts[:3]:
            self.ctx.toast(a.title, a.message, sev_map[a.severity])
        if len(alerts) > 3:
            self.ctx.toast(f"{len(alerts) - 3} more alerts", "Open the bell to review them.", "info")
        if self.ctx.setting("alerts.desktop"):
            for a in alerts[:5]:
                self.notifier.notify(a.title, a.message, critical=a.severity == "critical")
        self._send_discord(alerts)

    def _send_discord(self, alerts: list) -> None:
        from ..alerts.discord import payloads, selected, valid_webhook

        url = self.ctx.setting("alerts.discord.webhook") or ""
        picked = selected(alerts, self.ctx.setting("alerts.discord.categories") or [])
        if not picked or not valid_webhook(url):
            return
        names = {p.id: p.name for p in self.ctx.repos.portfolios.list()}
        self.discord.send(url, payloads(picked, names), self.discord_done.emit)

    def _on_discord_done(self, error: str | None) -> None:
        if error:
            self.ctx.toast("Discord alert not sent", error, "warning")

    def toggle_alerts(self) -> None:
        self.alerts_panel.toggle()

    def reveal_symbol(self, portfolio_id: int, symbol: str) -> None:
        key = f"portfolio:{portfolio_id}"
        if key not in self.pages:
            return
        self.show_page(key)
        tab = self.pages[key]
        if hasattr(tab, "reveal"):
            tab.reveal(symbol)

    # ------------------------------------------------------------------ dividends
    def update_pending_dividends(self, announce: bool = False) -> int:
        from ..portfolio_types.stock_trading.service import pending_dividends_for

        n = 0
        for p in self.portfolios.values():
            if registry.get(p.type).tracks_dividends:
                n += len(pending_dividends_for(self.ctx.repos, p, self.ctx.today()))
        self.actions["pending_dividends"].setText(f"Pending dividends ({n})…" if n else "Pending dividends…")
        prev = getattr(self, "_pending_divs", 0)
        if announce and n > prev:
            self.ctx.toast(f"{n} pending dividend{'s' if n > 1 else ''}", "Review them in Data ▸ Pending dividends.",
                           "info")
        self._pending_divs = n
        return n

    def update_pending_splits(self, announce: bool = False) -> int:
        from ..portfolio_types.splits import pending_splits_for

        items = [s for p in self.portfolios.values() for s in pending_splits_for(self.ctx.repos, p, self.ctx.today())]
        n = len(items)
        self.actions["stock_splits"].setText(f"Stock splits ({n} to review)…" if n else "Stock splits…")
        prev = getattr(self, "_pending_splits", 0)
        if announce and n > prev:
            names = ", ".join(sorted({f"{s.symbol} {s.label}" for s in items})[:3])
            self.ctx.toast(f"{n} stock split{'s' if n > 1 else ''} to review", f"{names} · Data ▸ Stock splits",
                           "info")
        self._pending_splits = n
        return n

    def stock_splits(self) -> None:
        from .dialogs.splits_dialog import SplitsDialog

        SplitsDialog(self.ctx, parent=self).exec()
        self.update_pending_splits()

    def pending_dividends(self) -> None:
        from .dialogs.dividend_review_dialog import DividendReviewDialog

        DividendReviewDialog(self.ctx, parent=self).exec()
        self.update_pending_dividends()

    def manual_dividend(self) -> None:
        from .dialogs.dividend_review_dialog import ManualDividendDialog

        ManualDividendDialog(self.ctx, self.current_portfolio(), parent=self).exec()

    # ------------------------------------------------------------------ backups
    def _setup_backups(self) -> None:
        self.ctx.events.market_updated.connect(lambda: self.update_pending_dividends(announce=True))
        self.ctx.events.portfolio_data_changed.connect(lambda _pid: self.update_pending_dividends())
        self.ctx.events.market_updated.connect(lambda: self.update_pending_splits(announce=True))
        self.ctx.events.portfolio_data_changed.connect(lambda _pid: self.update_pending_splits())
        self._backup_timer = QTimer(self)
        self._backup_timer.timeout.connect(self.auto_backup)
        self._backup_timer.start(60 * 60_000)  # hourly check; backs up at most once per day
        self.update_pending_dividends()
        self.update_pending_splits()

    def auto_backup(self, force: bool = False) -> bool:
        from datetime import date

        from ..data.backup import BackupError, backup_dir, backup_now

        last = self.ctx.setting("backup.last_auto")
        if not force and last == date.today().isoformat():
            return False
        if not self.portfolios:
            return False
        try:
            backup_now(self.ctx.repos.conn, backup_dir(self.ctx.repos), int(self.ctx.setting("backup.keep") or 30))
        except BackupError as e:
            self.ctx.toast("Automatic backup skipped", str(e), "warning")
            return False
        self.ctx.repos.settings.set("backup.last_auto", date.today().isoformat())
        return True

    def backup_now(self) -> None:
        from ..data.backup import BackupError, backup_dir, backup_now

        try:
            keep = int(self.ctx.setting("backup.keep") or 30)
            path = backup_now(self.ctx.repos.conn, backup_dir(self.ctx.repos), keep)
        except BackupError as e:
            self.ctx.toast("Backup failed", str(e), "error")
            return
        self.ctx.toast("Backup saved", str(path), "success")

    def backup_save_as(self) -> None:
        from datetime import datetime

        from ..data.backup import backup_to

        default = Path.home() / f"portfolio-{datetime.now():%Y%m%d-%H%M%S}.db"
        path, _ = QFileDialog.getSaveFileName(self, "Save backup as", str(default), "SQLite database (*.db)")
        if path:
            try:
                backup_to(self.ctx.repos.conn, Path(path))
            except OSError as e:
                self.ctx.toast("Backup failed", str(e), "error")
                return
            self.ctx.toast("Backup saved", path, "success")

    def restore(self) -> None:
        from ..data.backup import BackupError, backup_dir, restore, validate_db
        from ..data.repo import Repos

        start = str(backup_dir(self.ctx.repos))
        path, _ = QFileDialog.getOpenFileName(self, "Restore from backup", start, "SQLite database (*.db)")
        if not path:
            return
        v = validate_db(Path(path))
        if not v.ok:
            info(self, "Cannot restore", v.message)
            return
        if not confirm(self, "Restore backup",
                       f"Replace all current data with this backup?\n\n{Path(path).name}\n{v.portfolios} portfolio(s),"
                       f" {v.trades} trade(s), schema v{v.version}.\n\nThe current database is backed up first.",
                       ok_text="Restore", danger=True):
            return
        try:
            safety, conn = restore(Path(path), self.ctx.db_path, self.ctx.repos.conn,
                                   backup_dir(self.ctx.repos) / "pre-restore")
        except (BackupError, OSError) as e:
            info(self, "Restore failed", str(e))
            return
        self.ctx.repos = Repos.open(conn)
        if self.ctx.alerts is not None:
            self.ctx.alerts.repos = self.ctx.repos
        self.reload_all()
        self.ctx.toast("Backup restored", f"Safety copy of the previous data: {safety}", "success")

    def reload_all(self) -> None:
        for key in [k for k in self.pages if k.startswith("portfolio:")]:
            self.remove_page(key)
        self._loaded = False
        self.reload_portfolios()
        self.ctx.events.market_updated.emit()
        self.update_price_chip()
        self.update_bell()

    def export_json(self, pid: int | None = None) -> None:
        from ..data.backup import BackupError, export_json, write_json

        p = self.portfolios.get(pid) if pid is not None else self.current_portfolio()
        if p is None:
            return
        default = Path.home() / f"{p.name.replace(' ', '_')}.json"
        path, _ = QFileDialog.getSaveFileName(self, "Export portfolio", str(default), "JSON (*.json)")
        if not path:
            return
        try:
            write_json(export_json(self.ctx.repos, p.id), Path(path))  # type: ignore[arg-type]
        except (BackupError, OSError) as e:
            self.ctx.toast("Export failed", str(e), "error")
            return
        self.ctx.toast("Portfolio exported", path, "success")

    def import_json(self) -> None:
        from ..data.backup import BackupError, import_json, read_json
        from ..data.repo import RepoError

        path, _ = QFileDialog.getOpenFileName(self, "Import portfolio", str(Path.home()), "JSON (*.json)")
        if not path:
            return
        try:
            p = import_json(self.ctx.repos, read_json(Path(path)))
        except (BackupError, RepoError, KeyError, ValueError) as e:
            info(self, "Import failed", f"Nothing was imported.\n\n{e}")
            return
        self.ctx.events.portfolios_changed.emit()
        self.show_page(f"portfolio:{p.id}")
        self.ctx.toast("Portfolio imported", p.name, "success")

    def import_legacy(self) -> None:
        from ..data.importers.spreadsheet_csv import import_csv

        p = self.current_portfolio()
        if p is None:
            info(self, "Import legacy CSV", "Open the portfolio to import into first.")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Import legacy spreadsheet CSV", str(Path.home()), "CSV (*.csv)")
        if not path:
            return
        rep = import_csv(self.ctx.repos, p.id, Path(path))  # type: ignore[arg-type]
        self.ctx.data_changed(p.id)  # type: ignore[arg-type]
        details = "\n".join(rep.skipped[:15]) + ("\n…" if len(rep.skipped) > 15 else "")
        info(self, "Import finished", f"Imported {rep.buys} buys and {rep.sells} sells into {p.name}."
             + (f"\n\nSkipped {len(rep.skipped)} row(s):\n{details}" if rep.skipped else ""))

    # ------------------------------------------------------------------ geometry
    def _restore_geometry(self) -> None:
        g = self.ctx.repos.settings.get("ui.window_geometry")
        if g:
            with contextlib.suppress(ValueError, TypeError):
                self.restoreGeometry(QByteArray(base64.b64decode(g)))

    def save_geometry(self) -> None:
        self.ctx.repos.settings.set("ui.window_geometry",
                                    base64.b64encode(bytes(self.saveGeometry().data())).decode())

    def closeEvent(self, e) -> None:  # noqa: N802
        try:
            self.save_geometry()
            if not getattr(self, "skip_close_backup", False):
                self.auto_backup(force=True)
        except Exception:  # closing must never fail (e.g. database already closed)
            log.exception("Error while saving state on close")
        super().closeEvent(e)


def placeholder_page(title: str, body: str, action: str | None = None) -> QWidget:
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(tokens.WINDOW_PAD, tokens.WINDOW_PAD, tokens.WINDOW_PAD, tokens.WINDOW_PAD)
    v.addWidget(EmptyState(title, body, action), 1)
    return w
