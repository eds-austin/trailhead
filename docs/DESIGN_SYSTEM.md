# Trailhead — Design System

> Visual language for the app: a **dark trading terminal**, quiet surfaces, crisp tabular numbers, and color used only for meaning.
> Implementation: `src/trailhead/ui/theme/tokens.py` (source of truth) → `qss.py` (generated Qt stylesheet) + PyQtGraph theme helpers.
> Rule: **widgets never hard-code a color, font size or spacing value.** They reference tokens or QSS object names and properties.

---

## 1. Principles

1. **Numbers first.** Prices, P/L and R are the content. They use a monospaced, tabular face and are right-aligned so digits line up down columns.
2. **Color means something.** Neutral grays carry structure. Color is reserved for meaning: gain or loss, **stop (amber)**, **target (cyan)**, warnings and the primary action. Nothing is colored just for decoration.
3. **The two levels that matter are always visible.** The current stop and the next target appear as pills in the table, as lines on the chart, and in the ladder cards, always in the same two colors.
4. **Calm density.** The layout is spreadsheet-dense but has room to breathe: a 4px grid, flat surfaces, hairline borders, no shadows and no gradients.
5. **Status is never shown by color alone.** Every colored status also has a sign, icon or text (`+`, `−`, ▲▼, "STOP HIT").

---

## 2. Color tokens (dark theme)

### Surfaces & lines
| Token | Hex | Use |
|---|---|---|
| `bg` | `#0E1116` | Window background, sidebar |
| `surface` | `#151A21` | Panels, table background, cards |
| `surface_2` | `#1B222C` | Raised elements: header row, inputs, dialog body, hovered card |
| `surface_3` | `#232B37` | Pressed states, selected segmented option, menu hover |
| `border` | `#2A3340` | Hairlines, card borders, grid lines |
| `border_strong` | `#3A4555` | Input borders, focus-adjacent dividers |

### Text
| Token | Hex | Use |
|---|---|---|
| `text` | `#E6EDF3` | Primary text and numbers |
| `text_muted` | `#8B97A6` | Labels, secondary columns, axis text |
| `text_faint` | `#5D6876` | Placeholders, disabled text, R-ladder labels |
| `text_on_accent` | `#0B0E12` | Text on accent, amber and cyan fills |

### Semantic
| Token | Hex | Meaning |
|---|---|---|
| `accent` | `#4C9AFF` | Primary action (New Trade), focus ring, links, selection |
| `positive` | `#2FBF71` | Gains, up candles, wins |
| `negative` | `#F2545B` | Losses, down candles, STOP HIT |
| `stop` | `#F5A524` | **Current stop**: pill, chart line, ladder |
| `target` | `#22C3D6` | **Next target**: pill, chart line, ladder |
| `warning` | `#E3B341` | Exceeds max or risk, stale prices, earnings soon |
| `info` | `#9A8CFF` | Manual-price badge, informational alerts |
| `sma` | `#D66BD9` | 18-day moving average on candle charts (Trailhead-only chart series) |

### Tints (backgrounds for badges, pills and row states)
Each tint is the semantic color at reduced alpha over `surface`:
- `*_bg_soft` = 12% alpha (badges and pills);
- `*_bg_row` = 6% alpha (whole-row highlight, e.g. a STOP HIT row tinted with `negative_bg_row`).

Interaction tints:
- Row hover: `accent` at 6%.
- Selected row: `accent` at 14%, plus a 2px left bar in `accent`.
- Child (trade) rows: `bg` color, slightly darker than parent rows, to show nesting.

### Contrast check
`text` on `surface` ≈ 14:1; `text_muted` on `surface` ≈ 6:1; `stop` and `target` on `surface` > 7:1; `text_on_accent` on `accent` ≈ 7:1. These meet WCAG AA for body text.

---

## 3. Typography

| Token | Family | Size / line | Weight | Use |
|---|---|---|---|---|
| `display` | Inter | 28 / 34 | 600 | Dashboard headline value |
| `kpi_value` | JetBrains Mono | 20 / 26 | 600 | KPI card values |
| `title` | Inter | 15 / 22 | 600 | Section titles, dialog titles, portfolio name |
| `body` | Inter | 13 / 20 | 400 | Default UI text, table text |
| `body_strong` | Inter | 13 / 20 | 600 | Symbols, column headers |
| `num` | JetBrains Mono | 13 / 20 | 400 | All numeric cells and inputs (`font-feature-settings: "tnum"`) |
| `caption` | Inter | 11 / 16 | 500 | KPI labels, badges, axis labels. Uppercase with +0.04em tracking for KPI labels. |

**Fonts:** Inter and JetBrains Mono, both under the SIL OFL, are bundled in `ui/theme/fonts/` and registered with `QFontDatabase.addApplicationFont`. If they fail to load, the fallback is the system UI font and `monospace`.

**Number formatting** (`ui/format.py`):
- **Money:** `$12,345.67`. Negatives are `−$1,234.00`, using a true minus sign U+2212, never parentheses.
- **Signed deltas:** `+$153.00`, `−$48.20`, `+1.05R`, `−0.42R`, `+3.2%`.
- **Prices:** 2 decimals (4 decimals if the price is under $1).
- **Percents:** 1 decimal (2 decimals in the KPI sub-line).
- **R multiples:** 2 decimals with the `R` suffix.
- **Shares:** integers with thousands separators.
- An empty or undefined value shows an em dash `—` in `text_faint`. Never `#DIV/0!`, `nan` or `0` as a placeholder.

---

## 4. Spacing, radius, sizing

| Token | Value |
|---|---|
| `space_1…8` | 4, 8, 12, 16, 20, 24, 32, 40 px |
| `radius_sm` | 4 (badges) |
| `radius_md` | 6 (inputs, buttons, pills) |
| `radius_lg` | 10 (cards, dialogs, chart panels) |
| `row_h_compact` / `row_h_comfy` | 30 / 36 px (table density setting) |
| `header_h` | 56 px |
| `sidebar_w` | 64 px collapsed (icons) / 208 px expanded |
| `kpi_min_w` | 148 px, height 84 px |
| `control_h` | 32 px (inputs, buttons); 28 px compact |
| `border_w` | 1 px; focus ring 2 px `accent` |

Layout rules:
- Window padding is 16. The gap between the KPI strip, the table and the detail pane is 12. Cards have 12/16 internal padding.
- There are no drop shadows. Elevation comes from stepping `bg → surface → surface_2 → surface_3`.

---

## 5. Components

### 5.1 KpiCard
- Container: `surface`, 1px `border`, `radius_lg`, padding 12/16.
- Label: `caption`, uppercase, `text_muted`.
- Value: `kpi_value`. It is colored only if it is signed P/L.
- Sub-line: `caption` in `text_muted`, or a signed delta in `positive` or `negative` with ▲/▼.
- Optional elements:
  - a 24px-high sparkline (PyQtGraph, 1.5px line, no axes) in `text_muted`, or green/red by trend;
  - a progress bar for *Deployed / Max*: 4px high, `surface_3` track, `accent` fill; `warning` above 90%, `negative` above 100%.
- States: hover lifts to `surface_2`. A loading state shows a skeleton shimmer bar.

### 5.2 Level pills (stop and target)
- Shape: `radius_md`, 22px high, padding 0/8, `num` text.
- **Stop pill:** `stop_bg_soft` fill, `stop` text, a small ■ glyph before the price, and the distance (`−4.1% · −1.3R`) in `caption` `text_muted` after it.
- **Target pill:** `target_bg_soft` fill, `target` text, a ◆ glyph, then the distance.
- When the price is within `near_stop_pct` of the stop, the stop pill switches to a solid `stop` fill with `text_on_accent` text.
- **STOP HIT:** a solid `negative` fill.

### 5.3 Badges
Uppercase `caption`, `radius_sm`, padding 2/6, soft tint background plus colored text:

| Badge | Color |
|---|---|
| OPEN | `accent` |
| CLOSED | `text_muted` on `surface_3` |
| STOP HIT | `negative` (solid) |
| ACTION · Sell 12 sh @1R / Raise stop → $41.72 | `warning` |
| EARNINGS 11/24 | `warning` |
| M (manual price) | `info` |
| STALE | `warning` |

### 5.4 Positions table (QTreeView)
- **Header:** `surface_2` background, `body_strong` text in `text_muted`, a 1px `border` bottom, and a sort arrow in `accent`. The text column is left-aligned; number columns are right-aligned.
- **Parent rows:** `surface` background with a 1px `border` bottom. The symbol is `body_strong`, followed by the sector in `caption` `text_muted`.
- **Child rows:** `bg` background, indented 20px. A side marker shows BUY (▲, `positive`) or SELL (▼, `negative`). Stop-change rows are italic in `text_muted`.
- **Caret:** a chevron in `text_muted`, rotating 90° when expanded (no animation needed).
- **Signed numbers** are colored. Neutral numbers use `text`.
- **Row states:** hover, selected (with the 2px left accent bar), STOP HIT (`negative_bg_row` plus a 2px left bar in `negative`), action due (2px left bar in `warning`).
- **Gridlines:** horizontal only, in `border`. No vertical lines; column spacing alone separates columns.
- **Empty state:** a centered illustration glyph, the title "No positions yet", the body "Add your first trade to start tracking.", and a primary **New Trade** button.

### 5.5 Buttons
| Variant | Style |
|---|---|
| Primary | `accent` fill, `text_on_accent`, weight 600. Hover lightens by 8%; pressed darkens by 8%. |
| Secondary | `surface_2` fill, 1px `border_strong`, `text` |
| Ghost | Transparent, `text_muted`. Hover shows `surface_2`. Used for icon buttons such as refresh and the bell. |
| Danger | `negative` text on a ghost button. Confirmation dialogs use a `negative` fill. |

All buttons are `control_h` high with `radius_md` corners and a 2px focus ring in `accent`.

### 5.6 Inputs
- Fill: `surface_2`, with a 1px `border_strong` border and `radius_md`. Text is `num` for numeric fields and `body` otherwise.
- Focus: the border turns `accent`. Error: the border turns `negative`, with `caption` error text in `negative` below the field.
- Numeric fields use a QDoubleSpinBox without arrows, accept typed input only, and select all their text on focus.
- Date fields use QDateEdit with a calendar popup styled to match.
- **Segmented control (BUY | SELL):** a `surface_2` track with `surface_3` for the selected segment. The selected BUY segment has `positive` text and a 2px bottom line; SELL uses `negative`.
- **View switch (Positions | Performance):** a segmented control with 15px labels; the current view uses `accent` text on the 14% accent selection tint. A 1px `border_strong` vertical rule separates it from the filters that act on that view.

### 5.7 Dialogs
- Body: `surface_2`, `radius_lg`, a 1px `border`, padding 20, and a `title` heading.
- Footer: actions aligned right, with the primary button last, each at least 80px wide (`dialog_button_min_w`). Enter submits and Esc cancels. A dialog with no fields opens without focus on its buttons, so no focus ring shows until Tab is pressed.
- **Risk panel** in the trade dialog: a `surface` inset box with a `radius_md` 1px `border`. Each value line is in `num`. Warning lines get a ⚠ icon and `warning` text on a `warning_bg_soft` strip.

### 5.8 Sidebar & header
- **Sidebar:** `bg` background with a 1px `border` on the right. Items are 40px high with an icon plus a label. The active item has `surface_2` fill and a 2px `accent` bar on the left. The "＋ New portfolio" item uses the ghost style.
- **Header:** `bg` background, 56px high. It holds the portfolio name (`title`), the price-status chip (a dot in `positive` if fresh or `warning` if stale, plus "Prices 10:42" in `caption`), a ghost refresh button, a bell with a count badge (`negative` dot), a secondary **Cash** button and a primary **＋ New Trade** button.

### 5.9 Toasts & alerts panel
- **Toasts:** bottom right, `surface_3`, `radius_md`, a 3px left bar in the severity color, auto-dismissed after 5s. Errors stay until dismissed.
- **Alerts panel:** a 360px drawer opened from the bell. Alerts are grouped by severity, each row showing the symbol, the message and the time, with Acknowledge buttons.

---

## 6. Charts (PyQtGraph)

Global setup comes from tokens: `pg.setConfigOptions(antialias=True, background=surface, foreground=text_muted)`. Axes use `caption` font in `text_muted`, the grid is `border` at 40% alpha, and there is no outer frame.

| Element | Style |
|---|---|
| Up candle | Body and wick `positive`; body filled at 85% alpha |
| Down candle | `negative` |
| Volume | Bars at 25% alpha of the candle color, in a sub-plot 18% of the height |
| Entry line | `text` dashed 1px, label "Entry 43.18" |
| **Current stop** | `stop` solid 2px, with the right-edge label pill "STOP 41.72" (pill style) |
| **Next target** | `target` solid 2px, with the right-edge label pill "TGT 49.02" |
| 18-day SMA | `sma` 1.25px at 70% alpha; its value is in the crosshair readout |
| Hover peek | Compact chart: candles edge to edge, level prices as filled tags on the price axis (`stop` / `target` fill, entry on `surface3`), no volume, ladder or crosshair |
| R ladder | `text_faint` dotted 1px at −1R, −0.5R, 0, 1R, 2R, 3R, 6R, 9R…, labels "1R" etc. on the right |
| Buy / Sell markers | ▲ `positive` below the low / ▼ `negative` above the high, 9px |
| Earnings | Vertical dashed `warning` line with an "E" tag at the top |
| Dividend ex-date | Small `info` tick on the x-axis |
| Crosshair | `text_faint` 1px, with an OHLC readout in `num` at the top left |
| Equity curve | `accent` 2px line with a 10% `accent` fill below; deposits as a `text_faint` step line |
| R histogram | 0.5R bins. Bars `positive` for > 0 and `negative` for < 0. Expectancy shown as a vertical `accent` dashed line labeled "E = 1.52R". |
| Allocation | Horizontal bars by position in a single `accent` hue, ordered by size. The sector donut uses the categorical palette below. |

**Categorical palette** (for sectors only, in this order): `#4C9AFF`, `#22C3D6`, `#9A8CFF`, `#E3B341`, `#2FBF71`, `#F28C5B`, `#D16BA5`, `#7F8FA6`, `#5FD3A0`, `#C9A86B`, `#6B8CD1`. Gains and losses are never encoded with these colors.

---

## 7. Iconography & motion
- **Icons:** a single outline set (Lucide or Tabler SVGs, MIT licensed, bundled), 16px in tables and 18px in the header and sidebar, stroke 1.75. They are tinted at runtime with the token color.
- **Motion:** minimal. Hover and press transitions are instant (Qt Widgets). Toasts fade over 150ms. The loading skeleton shimmers over 1.2s. Nothing animates numbers.

---

## 8. Implementation sketch

```python
# ui/theme/tokens.py
from dataclasses import dataclass

@dataclass(frozen=True)
class Palette:
    bg: str = "#0E1116"; surface: str = "#151A21"; surface_2: str = "#1B222C"; surface_3: str = "#232B37"
    border: str = "#2A3340"; border_strong: str = "#3A4555"
    text: str = "#E6EDF3"; text_muted: str = "#8B97A6"; text_faint: str = "#5D6876"; text_on_accent: str = "#0B0E12"
    accent: str = "#4C9AFF"; positive: str = "#2FBF71"; negative: str = "#F2545B"
    stop: str = "#F5A524"; target: str = "#22C3D6"; warning: str = "#E3B341"; info: str = "#9A8CFF"

SPACE = (0, 4, 8, 12, 16, 20, 24, 32, 40)
RADIUS = {"sm": 4, "md": 6, "lg": 10}
FONT_UI, FONT_NUM = "Inter", "JetBrains Mono"
TYPE = {"display": (28, 600), "kpi_value": (20, 600), "title": (15, 600),
        "body": (13, 400), "body_strong": (13, 600), "num": (13, 400), "caption": (11, 500)}
DARK = Palette()

def alpha(hex_color: str, a: float) -> str:  # "#RRGGBB" → "rgba(r,g,b,a)"
    r, g, b = (int(hex_color[i:i+2], 16) for i in (1, 3, 5))
    return f"rgba({r},{g},{b},{a})"
```

```python
# ui/theme/qss.py (excerpt) — build_qss(p: Palette) -> str
QPushButton[variant="primary"] { background: {p.accent}; color: {p.text_on_accent}; border: none;
                                  border-radius: 6px; padding: 0 14px; min-height: 32px; font-weight: 600; }
QFrame[role="kpi-card"]        { background: {p.surface}; border: 1px solid {p.border}; border-radius: 10px; }
QTreeView                      { background: {p.surface}; alternate-background-color: {p.surface};
                                  border: none; selection-background-color: {alpha(p.accent, .14)}; }
QHeaderView::section           { background: {p.surface_2}; color: {p.text_muted}; border: none;
                                  border-bottom: 1px solid {p.border}; padding: 0 8px; height: 30px; }
QLineEdit, QDoubleSpinBox, QDateEdit, QComboBox
                               { background: {p.surface_2}; border: 1px solid {p.border_strong};
                                  border-radius: 6px; padding: 0 8px; min-height: 32px; color: {p.text}; }
QLineEdit:focus, QDoubleSpinBox:focus { border-color: {p.accent}; }
```

Widgets select their variants with dynamic properties (`btn.setProperty("variant", "primary")`). Pills, badges and colored numbers in the table are painted by `QStyledItemDelegate`s that read `DARK` directly, because QSS cannot style individual cells. A future light theme only needs a second `Palette` instance; no widget code changes.
