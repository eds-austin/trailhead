"""Design tokens — the single source of truth for colors, type, spacing and sizes.

See docs/DESIGN_SYSTEM.md. Widgets must reference these values (or QSS object names / properties
generated from them) and never hard-code a color, font size or spacing value.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Palette:
    # Surfaces & lines
    bg: str = "#0E1116"
    surface: str = "#151A21"
    surface_2: str = "#1B222C"
    surface_3: str = "#232B37"
    border: str = "#2A3340"
    border_strong: str = "#3A4555"
    # Text
    text: str = "#E6EDF3"
    text_muted: str = "#8B97A6"
    text_faint: str = "#5D6876"
    text_on_accent: str = "#0B0E12"
    # Semantic
    accent: str = "#4C9AFF"
    positive: str = "#2FBF71"
    negative: str = "#F2545B"
    stop: str = "#F5A524"
    target: str = "#22C3D6"
    warning: str = "#E3B341"
    info: str = "#9A8CFF"


DARK = Palette()

# Alpha levels for tints
SOFT_ALPHA = 0.12  # *_bg_soft: badges and pills
ROW_ALPHA = 0.06  # *_bg_row: whole-row highlight
HOVER_ALPHA = 0.06
SELECTED_ALPHA = 0.14

# Spacing: SPACE[1..8] = 4, 8, 12, 16, 20, 24, 32, 40
SPACE = (0, 4, 8, 12, 16, 20, 24, 32, 40)
RADIUS = {"sm": 4, "md": 6, "lg": 10}

ROW_H = {"compact": 30, "comfy": 36}
HEADER_H = 56
SIDEBAR_W = {"collapsed": 64, "expanded": 208}
KPI_MIN_W = 148
KPI_H = 84
CONTROL_H = 32
CONTROL_H_COMPACT = 28
BORDER_W = 1
FOCUS_W = 2
PILL_H = 22
SIDEBAR_ITEM_H = 40
TOAST_W = 340
ALERTS_PANEL_W = 360
DIALOG_W = 520
DIALOG_BUTTON_MIN_W = 80  # footer buttons: a short label ("Done", "OK") still reads as a button
CHILD_INDENT = 20
ICON_TABLE = 16
ICON_HEADER = 18
BRAND_MARK = 24  # app mark beside the name in the sidebar
WINDOW_PAD = 16
SECTION_GAP = 12
CARD_PAD = (12, 16)  # vertical, horizontal
PROGRESS_H = 4
SPARK_H = 24

FONT_UI = "Inter"
FONT_NUM = "JetBrains Mono"
FONT_UI_FALLBACK = "sans-serif"
FONT_NUM_FALLBACK = "monospace"

# name -> (pixel size, line height, weight, family key)
TYPE: dict[str, tuple[int, int, int, str]] = {
    "display": (28, 34, 600, "ui"),
    "kpi_value": (20, 26, 600, "num"),
    "title": (15, 22, 600, "ui"),
    "body": (13, 20, 400, "ui"),
    "body_strong": (13, 20, 600, "ui"),
    "num": (13, 20, 400, "num"),
    "caption": (11, 16, 500, "ui"),
}
CAPTION_TRACKING = 0.04  # em, KPI labels

# Sector donut only — never used for gain/loss.
CATEGORICAL = (
    "#4C9AFF", "#22C3D6", "#9A8CFF", "#E3B341", "#2FBF71", "#F28C5B",
    "#D16BA5", "#7F8FA6", "#5FD3A0", "#C9A86B", "#6B8CD1",
)

TOAST_MS = 5000
TOAST_FADE_MS = 150
SHIMMER_MS = 1200


def rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def alpha(hex_color: str, a: float) -> str:
    """'#RRGGBB' -> 'rgba(r,g,b,a)' for QSS."""
    r, g, b = rgb(hex_color)
    return f"rgba({r},{g},{b},{a})"


def blend(hex_color: str, a: float, over: str) -> str:
    """Composite hex_color at alpha a over an opaque color; returns '#RRGGBB'."""
    fr, fg, fb = rgb(hex_color)
    br, bg_, bb = rgb(over)
    mix = (round(fr * a + br * (1 - a)), round(fg * a + bg_ * (1 - a)), round(fb * a + bb * (1 - a)))
    return "#{:02X}{:02X}{:02X}".format(*mix)


def lighten(hex_color: str, amount: float) -> str:
    return blend("#FFFFFF", amount, hex_color)


def darken(hex_color: str, amount: float) -> str:
    return blend("#000000", amount, hex_color)


def soft(p: Palette, name: str) -> str:
    """`<name>_bg_soft`: semantic color at 12% over surface (opaque hex)."""
    return blend(getattr(p, name), SOFT_ALPHA, p.surface)


def row_tint(p: Palette, name: str) -> str:
    """`<name>_bg_row`: semantic color at 6% over surface (opaque hex)."""
    return blend(getattr(p, name), ROW_ALPHA, p.surface)
