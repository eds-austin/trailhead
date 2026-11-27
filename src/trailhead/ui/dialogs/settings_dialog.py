"""Settings: backup folder & retention, refresh, alert thresholds, notifications (desktop, Discord), density,
data provider."""

from __future__ import annotations

from decimal import Decimal

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QComboBox, QFileDialog, QGridLayout, QLineEdit, QWidget

from ...alerts.discord import DiscordSender, test_payload, valid_webhook
from ...alerts.engine import ALERT_CATEGORIES
from ...context import AppContext
from ..theme import tokens
from ..widgets.common import button, label
from ..widgets.number_edit import NumberEdit
from .base import AppDialog, FieldGrid

AUTO_CHOICES = [(0, "Off"), (5, "Every 5 minutes"), (15, "Every 15 minutes"), (30, "Every 30 minutes"),
                (60, "Every hour")]


class SettingsDialog(AppDialog):
    test_done = Signal(object)  # str error or None, emitted from the sender thread

    def __init__(self, ctx: AppContext, parent: QWidget | None = None):
        super().__init__("Settings", parent, width=560)
        self.ctx = ctx
        s = ctx.repos.settings

        self.body.addWidget(label("BACKUPS", role="caption"))
        g = FieldGrid()
        self.grid = g
        from ...data.backup import default_backup_dir

        self.backup_dir = QLineEdit(s.get("backup.dir") or "")
        self.backup_dir.setPlaceholderText(str(default_backup_dir()))
        browse = button("Browse…")
        browse.setAutoDefault(False)
        browse.clicked.connect(self._browse)
        g.add("backup_dir", "Folder", self.backup_dir, extra=browse, span=2)
        g.next_row()
        self.keep = NumberEdit(0)
        self.keep.set_value(s.get("backup.keep") or 30, 0)
        g.add("keep", "Keep newest", self.keep)
        self.body.addLayout(g)

        self.body.addWidget(label("MARKET DATA", role="caption"))
        g2 = FieldGrid()
        self.grid2 = g2
        self.provider = QComboBox()
        self.provider.addItem("Yahoo Finance (yfinance, free)", "yfinance")
        self.provider.addItem("Manual prices only (offline)", "manual")
        self.provider.setCurrentIndex(max(0, self.provider.findData(s.get("market.provider") or "yfinance")))
        g2.add("provider", "Provider", self.provider, span=2)
        g2.next_row()
        self.api_key = QLineEdit(s.get("market.api_key") or "")
        self.api_key.setPlaceholderText("Reserved for paid providers")
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setEnabled(False)
        g2.add("api_key", "API key", self.api_key, span=2)
        g2.next_row()
        self.on_startup = QCheckBox("Refresh prices on startup")
        self.on_startup.setChecked(bool(s.get("refresh.on_startup")))
        g2.add("startup", "", self.on_startup, span=2)
        g2.next_row()
        self.auto = QComboBox()
        for minutes, text in AUTO_CHOICES:
            self.auto.addItem(text, minutes)
        self.auto.setCurrentIndex(max(0, self.auto.findData(int(s.get("refresh.auto_minutes") or 0))))
        g2.add("auto", "Auto refresh", self.auto, span=2)
        self.body.addLayout(g2)
        self.body.addWidget(label("Auto refresh runs on weekdays 09:30–16:00 New York time.", role="muted"))

        self.body.addWidget(label("ALERTS", role="caption"))
        g3 = FieldGrid()
        self.grid3 = g3
        self.near = NumberEdit(2)
        self.near.set_value(Decimal(str(s.get("alerts.near_stop_pct") or 0.02)) * 100)
        self.earn = NumberEdit(0)
        self.earn.set_value(s.get("alerts.earnings_days") or 7, 0)
        g3.add("near", "Near stop %", self.near, 0)
        g3.add("earn", "Earnings within days", self.earn, 1)
        g3.next_row()
        self.risk_warn = NumberEdit(2)
        self.risk_warn.set_value(Decimal(str(s.get("alerts.open_risk_warn_pct") or 0.06)) * 100)
        g3.add("risk_warn", "Open risk warn %", self.risk_warn, 0)
        g3.next_row()
        self.desktop = QCheckBox("Desktop notifications")
        self.desktop.setChecked(bool(s.get("alerts.desktop")))
        g3.add("desktop", "", self.desktop, span=2)
        self.body.addLayout(g3)
        self._build_discord(s)

        self.body.addWidget(label("DISPLAY", role="caption"))
        g4 = FieldGrid()
        self.density = QComboBox()
        self.density.addItem("Compact (30 px rows)", "compact")
        self.density.addItem("Comfortable (36 px rows)", "comfy")
        self.density.setCurrentIndex(max(0, self.density.findData(s.get("ui.table_density") or "compact")))
        g4.add("density", "Table density", self.density, span=2)
        self.body.addLayout(g4)

    def _build_discord(self, s) -> None:
        self.body.addWidget(label("DISCORD", role="caption"))
        g = FieldGrid()
        self.grid_discord = g
        self.webhook = QLineEdit(s.get("alerts.discord.webhook") or "")
        self.webhook.setPlaceholderText("https://discord.com/api/webhooks/…")
        self.webhook.setEchoMode(QLineEdit.EchoMode.Password)  # anyone with the URL can post to the channel
        self.webhook.setToolTip("In Discord: Server Settings → Integrations → Webhooks → New Webhook → "
                                "Copy Webhook URL")
        self.test_btn = button("Send test")
        self.test_btn.setAutoDefault(False)
        self.test_btn.clicked.connect(self._send_test)
        g.add("webhook", "Webhook URL", self.webhook, extra=self.test_btn, span=2)
        g.next_row()
        picked = set(s.get("alerts.discord.categories") or [])
        self.discord_cats: dict[str, QCheckBox] = {}
        box = QWidget()
        cg = QGridLayout(box)
        cg.setContentsMargins(0, tokens.SPACE[1], 0, 0)
        cg.setHorizontalSpacing(tokens.SPACE[4])
        cg.setVerticalSpacing(tokens.SPACE[2])
        for i, (key, text) in enumerate(ALERT_CATEGORIES.items()):
            cb = QCheckBox(text)
            cb.setChecked(key in picked)
            self.discord_cats[key] = cb
            cg.addWidget(cb, i // 2, i % 2)
        g.add("categories", "Send", box, span=2)
        self.body.addLayout(g)
        self.test_status = label("")
        self.test_status.hide()
        self.body.addWidget(self.test_status)
        self.body.addWidget(label("Sent while Trailhead is running, once per alert (like the bell).\n"
                                  "Find the URL in Discord: Server Settings → Integrations → Webhooks.",
                                  role="muted"))
        self._sender = DiscordSender()
        self.test_done.connect(self._on_test_done)

    def _send_test(self) -> None:
        url = self.webhook.text().strip()
        if not valid_webhook(url):
            self.grid_discord.set_error("webhook", "Paste the webhook URL Discord gives you")
            return
        self.grid_discord.set_error("webhook", None)
        self.test_btn.setEnabled(False)
        self._set_test_status("Sending…", None)
        self._sender.send(url, [test_payload()], self.test_done.emit)

    def _on_test_done(self, error: str | None) -> None:
        self.test_btn.setEnabled(True)
        if error:
            self._set_test_status(f"Test failed: {error}", "negative")
        else:
            self._set_test_status("Test message sent. Check the channel.", "positive")

    def _set_test_status(self, text: str, tone: str | None) -> None:
        self.test_status.setText(text)
        self.test_status.setProperty("tone", tone or "")
        self.test_status.style().unpolish(self.test_status)
        self.test_status.style().polish(self.test_status)
        self.test_status.show()

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Backup folder", self.backup_dir.text() or
                                             self.backup_dir.placeholderText())
        if d:
            self.backup_dir.setText(d)

    def submit(self) -> bool:
        keep = self.keep.value()
        near, earn, risk = self.near.value(), self.earn.value(), self.risk_warn.value()
        ok = True
        if keep is None or keep < 1:
            self.grid.set_error("keep", "Keep at least 1 backup")
            ok = False
        if near is None or near < 0 or near > 50:
            self.grid3.set_error("near", "0–50 %")
            ok = False
        if earn is None or earn < 0:
            self.grid3.set_error("earn", "Enter days")
            ok = False
        if risk is None or risk <= 0:
            self.grid3.set_error("risk_warn", "Enter a percent")
            ok = False
        webhook = self.webhook.text().strip()
        if webhook and not valid_webhook(webhook):
            self.grid_discord.set_error("webhook", "Paste the webhook URL Discord gives you")
            ok = False
        if not ok:
            return False
        s = self.ctx.repos.settings
        s.set("backup.dir", self.backup_dir.text().strip() or None)
        s.set("backup.keep", int(keep))  # type: ignore[arg-type]
        s.set("market.provider", self.provider.currentData())
        s.set("market.api_key", self.api_key.text())
        s.set("refresh.on_startup", self.on_startup.isChecked())
        s.set("refresh.auto_minutes", int(self.auto.currentData()))
        s.set("alerts.near_stop_pct", float(near / 100))  # type: ignore[operator]
        s.set("alerts.earnings_days", int(earn))  # type: ignore[arg-type]
        s.set("alerts.open_risk_warn_pct", float(risk / 100))  # type: ignore[operator]
        s.set("alerts.desktop", self.desktop.isChecked())
        s.set("alerts.discord.webhook", webhook)
        s.set("alerts.discord.categories", [k for k, cb in self.discord_cats.items() if cb.isChecked()])
        s.set("ui.table_density", self.density.currentData())
        return True
