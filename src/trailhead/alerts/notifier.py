"""Desktop notifications via desktop-notifier (D-Bus / WinRT); tray balloon fallback. Never blocks the UI."""

from __future__ import annotations

import logging
import threading

from PySide6.QtWidgets import QApplication, QSystemTrayIcon

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self, app_name: str = "Trailhead"):
        self.app_name = app_name
        self._tray: QSystemTrayIcon | None = None
        self._lock = threading.Lock()
        self._backend = None
        self._failed = False

    def _get_backend(self):
        if self._backend is None and not self._failed:
            try:
                from desktop_notifier import DesktopNotifierSync

                self._backend = DesktopNotifierSync(app_name=self.app_name)
            except Exception:
                log.info("desktop-notifier unavailable; using tray fallback", exc_info=True)
                self._failed = True
        return self._backend

    def notify(self, title: str, body: str, critical: bool = False) -> None:
        """Send from a background thread; fall back to the tray icon if the backend fails."""

        def work():
            with self._lock:
                backend = self._get_backend()
                if backend is None:
                    return False
                try:
                    from desktop_notifier import Urgency

                    backend.send(title, body, urgency=Urgency.Critical if critical else Urgency.Normal)
                    return True
                except Exception:
                    log.info("Desktop notification failed", exc_info=True)
                    return False

        def run():
            if not work():
                self._tray_fallback(title, body)

        threading.Thread(target=run, name="notify", daemon=True).start()

    def _tray_fallback(self, title: str, body: str) -> None:
        app = QApplication.instance()
        if app is None or not QSystemTrayIcon.isSystemTrayAvailable():
            return
        from PySide6.QtCore import QTimer

        self._pending = (title, body)
        QTimer.singleShot(0, app, self._show_tray)  # runs on the GUI thread

    def _show_tray(self) -> None:
        if self._tray is None:
            app = QApplication.instance()
            self._tray = QSystemTrayIcon(app.windowIcon(), app)
            self._tray.show()
        title, body = self._pending
        self._tray.showMessage(title, body, QSystemTrayIcon.MessageIcon.Information, 8000)
