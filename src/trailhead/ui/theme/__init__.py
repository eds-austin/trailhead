"""Theme runtime: fonts, tinted icons, QSS application and font helpers."""

from __future__ import annotations

import functools
import logging
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

from . import tokens
from .qss import build_qss
from .tokens import DARK, Palette

log = logging.getLogger(__name__)

THEME_DIR = Path(__file__).parent
FONT_DIR = THEME_DIR / "fonts"
ICON_DIR = THEME_DIR / "icons"

_families: dict[str, str] = {"ui": tokens.FONT_UI_FALLBACK, "num": tokens.FONT_NUM_FALLBACK}
_palette: Palette = DARK


def palette() -> Palette:
    return _palette


def load_fonts() -> dict[str, str]:
    """Register bundled fonts. Falls back to system UI / monospace families on failure."""
    loaded: set[str] = set()
    for ttf in sorted(FONT_DIR.glob("*.ttf")):
        fid = QFontDatabase.addApplicationFont(str(ttf))
        if fid < 0:
            log.warning("Could not load font %s", ttf.name)
            continue
        loaded.update(QFontDatabase.applicationFontFamilies(fid))
    if tokens.FONT_UI in loaded:
        _families["ui"] = tokens.FONT_UI
    else:
        _families["ui"] = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family()
    if tokens.FONT_NUM in loaded:
        _families["num"] = tokens.FONT_NUM
    else:
        _families["num"] = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()
    return dict(_families)


def family(kind: str) -> str:
    return _families[kind]


_WEIGHTS = {400: QFont.Weight.Normal, 500: QFont.Weight.Medium, 600: QFont.Weight.DemiBold,
            700: QFont.Weight.Bold}


def font(token: str, *, italic: bool = False, weight: int | None = None) -> QFont:
    """QFont for a TYPE token (display, kpi_value, title, body, body_strong, num, caption)."""
    px, _line, w, fam = tokens.TYPE[token]
    f = QFont(_families[fam])
    f.setPixelSize(px)
    f.setWeight(_WEIGHTS[weight or w])
    f.setItalic(italic)
    if fam == "num":
        f.setStyleHint(QFont.StyleHint.Monospace)
        f.setFeature(QFont.Tag("tnum"), 1)
    return f


def caption_upper_font() -> QFont:
    f = font("caption")
    f.setCapitalization(QFont.Capitalization.AllUppercase)
    f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 100 + tokens.CAPTION_TRACKING * 100)
    return f


@functools.cache
def _svg_source(name: str) -> str:
    return (ICON_DIR / f"{name}.svg").read_text(encoding="utf-8")


def tinted_svg(name: str, color: str, stroke: float = 1.75) -> bytes:
    src = _svg_source(name).replace("currentColor", color)
    src = src.replace('stroke-width="2"', f'stroke-width="{stroke}"')
    return src.encode("utf-8")


@functools.cache
def pixmap(name: str, color: str, size: int = tokens.ICON_TABLE, dpr: float = 2.0) -> QPixmap:
    renderer = QSvgRenderer(QByteArray(tinted_svg(name, color)))
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(p, QRectF(0, 0, size, size))
    p.end()
    return pm


@functools.cache
def brand_pixmap(size: int = tokens.BRAND_MARK, dpr: float = 2.0) -> QPixmap:
    """The Trailhead mark (full colour, drawn for small sizes), from brand-mark.svg."""
    renderer = QSvgRenderer(str(THEME_DIR / "brand-mark.svg"))
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(p, QRectF(0, 0, size, size))
    p.end()
    return pm


def icon(name: str, color: str | None = None, size: int = tokens.ICON_HEADER,
         active_color: str | None = None) -> QIcon:
    """Lucide outline icon tinted with a token color (default text_muted)."""
    color = color or _palette.text_muted
    ic = QIcon()
    ic.addPixmap(pixmap(name, color, size), QIcon.Mode.Normal, QIcon.State.Off)
    ic.addPixmap(pixmap(name, active_color or color, size), QIcon.Mode.Normal, QIcon.State.On)
    ic.addPixmap(pixmap(name, active_color or color, size), QIcon.Mode.Active, QIcon.State.Off)
    ic.addPixmap(pixmap(name, _palette.text_faint, size), QIcon.Mode.Disabled, QIcon.State.Off)
    return ic


def qcolor(hex_color: str, a: float = 1.0) -> QColor:
    c = QColor(hex_color)
    c.setAlphaF(a)
    return c


def _icon_files(cache_dir: Path, p: Palette) -> dict[str, str]:
    """Write tinted SVGs used from QSS (url()) into a cache folder."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, str] = {}
    for key, (name, color) in {
        "ICON_CHEVRON_DOWN": ("chevron-down", p.text_muted),
        "ICON_CHECK": ("check", p.text_on_accent),
    }.items():
        path = cache_dir / f"{name}-{color.lstrip('#')}.svg"
        path.write_bytes(tinted_svg(name, color, stroke=2.25))
        out[key] = path.as_posix()
    return out


def apply_theme(app: QApplication, p: Palette = DARK, cache_dir: Path | None = None) -> str:
    """Load fonts, set the app font, palette and stylesheet. Returns the QSS used."""
    global _palette
    _palette = p
    load_fonts()
    app.setFont(font("body"))
    qss = build_qss(p)
    if cache_dir is None:
        import platformdirs

        cache_dir = Path(platformdirs.user_cache_dir("Trailhead")) / "theme"
    try:
        for key, path in _icon_files(cache_dir, p).items():
            qss = qss.replace(key, path)
    except OSError:  # pragma: no cover - read-only cache dir
        log.warning("Could not write theme icon cache")
    app.setStyle("Fusion")
    from PySide6.QtGui import QPalette

    pal = QPalette()
    roles = {
        QPalette.ColorRole.Window: p.bg, QPalette.ColorRole.WindowText: p.text,
        QPalette.ColorRole.Base: p.surface, QPalette.ColorRole.AlternateBase: p.surface,
        QPalette.ColorRole.Text: p.text, QPalette.ColorRole.Button: p.surface_2,
        QPalette.ColorRole.ButtonText: p.text, QPalette.ColorRole.Highlight: p.accent,
        QPalette.ColorRole.HighlightedText: p.text_on_accent, QPalette.ColorRole.ToolTipBase: p.surface_3,
        QPalette.ColorRole.ToolTipText: p.text, QPalette.ColorRole.PlaceholderText: p.text_faint,
        QPalette.ColorRole.Link: p.accent, QPalette.ColorRole.Mid: p.border,
        QPalette.ColorRole.Dark: p.bg, QPalette.ColorRole.Light: p.surface_3,
    }
    for role, c in roles.items():
        pal.setColor(role, QColor(c))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(p.text_faint))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(p.text_faint))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(p.text_faint))
    app.setPalette(pal)
    app.setStyleSheet(qss)
    return qss


def icon_size(px: int) -> QSize:
    return QSize(px, px)
