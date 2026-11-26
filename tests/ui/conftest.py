from datetime import date

import pytest
from PySide6.QtCore import QThreadPool, QTimer
from PySide6.QtWidgets import QApplication

from trailhead.app import create_app, create_context


@pytest.fixture
def ctx(tmp_path, qtbot):
    create_app()
    before = set(QApplication.topLevelWidgets())
    c = create_context(tmp_path / "ui.db")
    c.today_fn = lambda: date(2026, 11, 20)
    c.repos.settings.set("alerts.desktop", False)
    c.repos.settings.set("market.provider", "manual")  # never touch the network in tests
    c.repos.settings.set("backup.dir", str(tmp_path / "backups"))  # never write to the real Documents folder
    yield c
    for w in [w for w in QApplication.topLevelWidgets() if w not in before]:
        for t in w.findChildren(QTimer):
            t.stop()
        w.close()
    for _ in range(3):  # jobs can be queued by events processed during teardown
        QThreadPool.globalInstance().waitForDone(5000)
        QApplication.processEvents()
    c.repos.conn.close()


@pytest.fixture
def window(ctx, qtbot):
    from trailhead.ui.main_window import MainWindow

    return _show(MainWindow(ctx))


def _show(w):
    w.show()
    return w
