"""Render the app icon PNGs and the Windows .ico from the SVG sources in packaging/icons/src.

    uv run python packaging/icons/render_icons.py

Each size uses the variant drawn for it: 16 and 32 drop the end dot and thicken the lines, 48 drops
the dashes, 64 and up use the full mark. The scalable icon and the in-app icon are src/full.svg.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

HERE = Path(__file__).parent
SRC = HERE / "src"
ROOT = HERE.parent.parent
APP_ID = "io.github.trailhead.Trailhead"
SIZES = (16, 32, 48, 64, 128, 256, 512)


def source_for(size: int) -> Path:
    if size <= 16:
        return SRC / "16.svg"
    if size <= 32:
        return SRC / "32.svg"
    if size <= 48:
        return SRC / "48.svg"
    return SRC / "full.svg"


def render(svg: Path, size: int) -> QImage:
    renderer = QSvgRenderer(QByteArray(svg.read_bytes()))
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(p, QRectF(0, 0, size, size))
    p.end()
    return img


def main() -> int:
    _app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    for size in SIZES:
        render(source_for(size), size).save(str(HERE / f"{size}.png"))
    # Windows .ico: ImageMagick packs the hand-tuned small sizes; Qt's ico writer stores one image only.
    ico = HERE / "trailhead.ico"
    magick = shutil.which("magick")
    if magick:
        subprocess.run([magick, *(str(HERE / f"{s}.png") for s in (16, 32, 48, 256)), str(ico)], check=True)
    else:
        render(source_for(256), 256).save(str(ico), "ICO")
    shutil.copyfile(SRC / "full.svg", HERE / f"{APP_ID}.svg")
    shutil.copyfile(SRC / "full.svg", ROOT / "src" / "trailhead" / "ui" / "theme" / "app-icon.svg")
    shutil.copyfile(SRC / "mark-small.svg", ROOT / "src" / "trailhead" / "ui" / "theme" / "brand-mark.svg")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
