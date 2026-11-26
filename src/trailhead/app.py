"""QApplication bootstrap: fonts + QSS, open DB, show MainWindow."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from PySide6.QtWidgets import QApplication

from .ui import theme

APP_NAME = "Trailhead"
log = logging.getLogger(__name__)


def create_app(qt_args: list[str] | None = None) -> QApplication:
    app = QApplication.instance() or QApplication(qt_args or ["trailhead"])
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName("Trailhead")
    app.setOrganizationName(APP_NAME)
    app.setDesktopFileName("io.github.trailhead.Trailhead")  # matches the .desktop file
    theme.apply_theme(app)
    from PySide6.QtGui import QIcon

    app.setWindowIcon(QIcon(str(theme.THEME_DIR / "app-icon.svg")))
    return app


def create_context(db_path: Path | str):
    from .context import AppContext
    from .data.db import connect
    from .data.repo import Repos
    from .market.manual_provider import create_provider
    from .market.service import MarketService

    conn = connect(db_path)
    ctx = AppContext(Repos.open(conn), Path(db_path))
    ctx.market = MarketService(Path(db_path), lambda: create_provider(ctx.setting("market.provider") or "yfinance"),
                               today_fn=lambda: ctx.today())
    from .alerts.service import AlertService

    ctx.alerts = AlertService(ctx.repos, lambda: ctx.today())
    return ctx


def build_window(ctx):
    from .ui.main_window import MainWindow

    return MainWindow(ctx)


def run(args: argparse.Namespace, qt_args: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    app = create_app(qt_args)
    if args.gallery:
        from .ui.gallery import GalleryWindow

        win = GalleryWindow()
        win.show()
        return app.exec()

    from .data.db import default_db_path

    if not args.db:
        from .data.legacy import migrate_legacy_dirs

        migrate_legacy_dirs()
    db_path = Path(args.db) if args.db else default_db_path()
    log.info("Database: %s", db_path)
    ctx = create_context(db_path)
    win = build_window(ctx)
    win.show()
    from PySide6.QtCore import QTimer

    QTimer.singleShot(300, win.startup)
    code = app.exec()
    ctx.repos.conn.close()
    return code
