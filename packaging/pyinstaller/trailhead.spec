# PyInstaller spec — Windows one-folder build (also works on Linux for a smoke test).
#   uv run --with pyinstaller pyinstaller packaging/pyinstaller/trailhead.spec --noconfirm
# Output: dist/Trailhead/Trailhead(.exe)
# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve().parents[1]  # noqa: F821 - SPECPATH is injected by PyInstaller
PKG = ROOT / "src" / "trailhead"

datas = [
    (str(PKG / "ui" / "theme" / "fonts"), "trailhead/ui/theme/fonts"),
    (str(PKG / "ui" / "theme" / "icons"), "trailhead/ui/theme/icons"),
    (str(PKG / "ui" / "theme" / "app-icon.svg"), "trailhead/ui/theme"),
    (str(PKG / "ui" / "theme" / "brand-mark.svg"), "trailhead/ui/theme"),
    (str(PKG / "data" / "migrations"), "trailhead/data/migrations"),
]
datas += collect_data_files("desktop_notifier")
datas += collect_data_files("yfinance")
hiddenimports = collect_submodules("trailhead") + collect_submodules(
    "pyqtgraph", filter=lambda n: not any(x in n for x in ("opengl", "jupyter", "examples", "canvas", "flowchart")))
QT_EXCLUDES = [f"PySide6.{m}" for m in (
    "QtWebEngineCore", "QtWebEngineWidgets", "QtWebEngineQuick", "QtWebChannel", "QtWebSockets", "QtQml", "QtQuick",
    "QtQuick3D", "QtQuickWidgets", "Qt3DCore", "Qt3DRender", "Qt3DExtras", "Qt3DInput", "Qt3DLogic", "Qt3DAnimation",
    "QtMultimedia", "QtMultimediaWidgets", "QtCharts", "QtDataVisualization", "QtGraphs", "QtPdf", "QtPdfWidgets",
    "QtBluetooth", "QtNfc", "QtPositioning", "QtLocation", "QtSensors", "QtSerialPort", "QtSql", "QtTest",
    "QtDesigner", "QtHelp", "QtScxml", "QtRemoteObjects", "QtSpatialAudio", "QtTextToSpeech", "QtHttpServer")]
if sys.platform == "win32":
    hiddenimports += collect_submodules("winrt")
else:
    hiddenimports += collect_submodules("dbus_fast")

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "pyinstaller" / "launcher.py")],
    pathex=[str(ROOT / "src")],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "IPython", "PyQt5", "PyQt6", "PySide2", "pytest", "pyqtgraph.examples",
              "pyqtgraph.opengl", "pyqtgraph.jupyter", *QT_EXCLUDES],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821
icon = str(ROOT / "packaging" / "icons" / "trailhead.ico") if sys.platform == "win32" else None
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Trailhead",
    console=False,
    icon=icon,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Trailhead")  # noqa: F821
