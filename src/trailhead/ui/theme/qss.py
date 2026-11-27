"""Generate the application Qt stylesheet from tokens."""

from __future__ import annotations

from .tokens import (
    CONTROL_H,
    HEADER_H,
    RADIUS,
    SIDEBAR_ITEM_H,
    SPACE,
    TYPE,
    Palette,
    alpha,
    darken,
    lighten,
)


def _px(name: str) -> int:
    return TYPE[name][0]


def build_qss(p: Palette) -> str:
    r_sm, r_md, r_lg = RADIUS["sm"], RADIUS["md"], RADIUS["lg"]
    cap, title = _px("caption"), _px("title")
    return f"""
* {{ outline: none; }}
QWidget {{ color: {p.text}; }}
QMainWindow, QDialog#appDialog, QWidget#root {{ background: {p.bg}; }}
QToolTip {{ background: {p.surface_3}; color: {p.text}; border: 1px solid {p.border_strong};
            border-radius: {r_sm}px; padding: {SPACE[1]}px {SPACE[2]}px; }}
QLabel {{ background: transparent; }}
QLabel[role="title"] {{ font-size: {title}px; font-weight: 600; }}
QLabel[role="display"] {{ font-size: {_px("display")}px; font-weight: 600; }}
QLabel[role="muted"] {{ color: {p.text_muted}; }}
QLabel[role="faint"] {{ color: {p.text_faint}; }}
QLabel[role="caption"] {{ color: {p.text_muted}; font-size: {cap}px; font-weight: 500; }}
QLabel[role="error"] {{ color: {p.negative}; font-size: {cap}px; font-weight: 500; }}
QLabel[role="num"] {{ font-family: "JetBrains Mono"; }}
QLabel[tone="positive"] {{ color: {p.positive}; }}
QLabel[tone="negative"] {{ color: {p.negative}; }}
QLabel[tone="warning"] {{ color: {p.warning}; }}
QLabel[tone="faint"] {{ color: {p.text_faint}; }}

/* ---------- Buttons ---------- */
QPushButton, QToolButton {{ border-radius: {r_md}px; min-height: {CONTROL_H}px; padding: 0 {SPACE[3] + 2}px;
    background: {p.surface_2}; border: 1px solid {p.border_strong}; color: {p.text}; }}
QPushButton:hover, QToolButton:hover {{ background: {p.surface_3}; }}
QPushButton:pressed, QToolButton:pressed {{ background: {darken(p.surface_3, 0.08)}; }}
QPushButton:focus, QToolButton:focus {{ border: 2px solid {p.accent}; }}
QPushButton:disabled {{ color: {p.text_faint}; background: {p.surface_2}; border-color: {p.border}; }}
QPushButton[variant="primary"] {{ background: {p.accent}; color: {p.text_on_accent}; border: none;
    font-weight: 600; }}
QPushButton[variant="primary"]:hover {{ background: {lighten(p.accent, 0.08)}; }}
QPushButton[variant="primary"]:pressed {{ background: {darken(p.accent, 0.08)}; }}
QPushButton[variant="primary"]:focus {{ border: 2px solid {lighten(p.accent, 0.35)}; }}
QPushButton[variant="primary"]:disabled {{ background: {p.surface_3}; color: {p.text_faint}; }}
QPushButton[variant="ghost"], QToolButton[variant="ghost"] {{ background: transparent; border: none;
    color: {p.text_muted}; padding: 0 {SPACE[2]}px; }}
QPushButton[variant="ghost"]:hover, QToolButton[variant="ghost"]:hover {{ background: {p.surface_2};
    color: {p.text}; }}
QPushButton[variant="ghost"]:focus, QToolButton[variant="ghost"]:focus {{ border: 2px solid {p.accent}; }}
QPushButton[variant="danger"] {{ background: transparent; border: none; color: {p.negative}; }}
QPushButton[variant="danger"]:hover {{ background: {alpha(p.negative, 0.12)}; }}
QPushButton[variant="danger-solid"] {{ background: {p.negative}; color: {p.text_on_accent}; border: none;
    font-weight: 600; }}
QPushButton[variant="danger-solid"]:hover {{ background: {lighten(p.negative, 0.08)}; }}
QPushButton[scale="small"], QToolButton[scale="small"] {{ min-height: 24px; padding: 0 {SPACE[2]}px;
    font-size: {cap}px; }}
QPushButton::menu-indicator {{ width: 0; image: none; }}

/* ---------- Inputs ---------- */
QLineEdit, QDoubleSpinBox, QSpinBox, QDateEdit, QComboBox, QPlainTextEdit, QTextEdit {{
    background: {p.surface_2}; border: 1px solid {p.border_strong}; border-radius: {r_md}px;
    padding: 0 {SPACE[2]}px; min-height: {CONTROL_H}px; color: {p.text};
    selection-background-color: {alpha(p.accent, 0.35)}; selection-color: {p.text}; }}
QPlainTextEdit, QTextEdit {{ padding: {SPACE[1]}px {SPACE[2]}px; }}
QAbstractSpinBox {{ padding-left: {SPACE[2]}px; padding-right: {SPACE[2]}px; }}
QLineEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus, QDateEdit:focus, QComboBox:focus,
QPlainTextEdit:focus, QTextEdit:focus {{ border-color: {p.accent}; }}
QLineEdit:disabled, QDoubleSpinBox:disabled, QComboBox:disabled, QDateEdit:disabled {{
    color: {p.text_faint}; border-color: {p.border}; }}
QLineEdit[error="true"], QDoubleSpinBox[error="true"], QDateEdit[error="true"], QComboBox[error="true"] {{
    border-color: {p.negative}; }}
QLineEdit[numeric="true"], QDoubleSpinBox, QSpinBox {{ font-family: "JetBrains Mono"; }}
QLineEdit[role="search"] {{ padding-left: {SPACE[2]}px; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ subcontrol-origin: border; width: 0;
    height: 0; border: none; }}
QDateEdit::drop-down, QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow, QDateEdit::down-arrow {{ image: url(ICON_CHEVRON_DOWN); width: 14px; height: 14px; }}
QComboBox QAbstractItemView {{ background: {p.surface_2}; border: 1px solid {p.border_strong};
    selection-background-color: {p.surface_3}; selection-color: {p.text}; padding: {SPACE[1]}px;
    outline: none; }}
QCheckBox {{ spacing: {SPACE[2]}px; background: transparent; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: {r_sm}px; border: 1px solid {p.border_strong};
    background: {p.surface_2}; }}
QCheckBox::indicator:checked {{ background: {p.accent}; border-color: {p.accent}; image: url(ICON_CHECK); }}

QCalendarWidget QWidget {{ alternate-background-color: {p.surface_2}; }}
QCalendarWidget QAbstractItemView {{ background: {p.surface_2}; color: {p.text};
    selection-background-color: {p.accent}; selection-color: {p.text_on_accent}; }}
QCalendarWidget QAbstractItemView:disabled {{ color: {p.text_faint}; }}
QCalendarWidget QToolButton {{ background: transparent; border: none; color: {p.text}; min-height: 24px; }}
QCalendarWidget #qt_calendar_navigationbar {{ background: {p.surface_3}; }}

/* ---------- Menus ---------- */
QMenuBar {{ background: {p.bg}; color: {p.text_muted}; border-bottom: 1px solid {p.border}; }}
QMenuBar::item {{ padding: {SPACE[1]}px {SPACE[2] + 2}px; background: transparent; }}
QMenuBar::item:selected {{ background: {p.surface_2}; color: {p.text}; border-radius: {r_sm}px; }}
QMenu {{ background: {p.surface_2}; border: 1px solid {p.border_strong}; padding: {SPACE[1]}px;
    border-radius: {r_md}px; }}
QMenu::item {{ padding: {SPACE[1] + 2}px {SPACE[5]}px {SPACE[1] + 2}px {SPACE[3]}px; border-radius: {r_sm}px; }}
QMenu::item:selected {{ background: {p.surface_3}; }}
QMenu::item:disabled {{ color: {p.text_faint}; }}
QMenu::separator {{ height: 1px; background: {p.border}; margin: {SPACE[1]}px {SPACE[2]}px; }}

/* ---------- Scrollbars ---------- */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle {{ background: {p.border_strong}; border-radius: 4px; min-height: 24px; min-width: 24px;
    margin: 2px; }}
QScrollBar::handle:hover {{ background: {p.text_faint}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QAbstractScrollArea {{ background: transparent; }}
QScrollArea {{ border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

/* ---------- Splitter ---------- */
QSplitter::handle {{ background: {p.bg}; }}
QSplitter::handle:vertical {{ height: {SPACE[3]}px; }}
QSplitter::handle:horizontal {{ width: {SPACE[3]}px; }}

/* ---------- Sidebar & header ---------- */
QFrame#sidebar {{ background: {p.bg}; border: none; border-right: 1px solid {p.border}; }}
QPushButton[role="nav"] {{ background: transparent; border: none; border-left: 2px solid transparent;
    border-radius: 0; text-align: left; padding: 0 {SPACE[3]}px; min-height: {SIDEBAR_ITEM_H}px;
    color: {p.text_muted}; }}
QPushButton[role="nav"]:hover {{ background: {p.surface}; color: {p.text}; }}
QPushButton[role="nav"]:checked {{ background: {p.surface_2}; color: {p.text}; border-left: 2px solid {p.accent}; }}
QPushButton[role="nav"]:focus {{ border-left: 2px solid {p.accent}; }}
QPushButton[role="nav"][collapsed="true"] {{ text-align: center; padding: 0; }}
QFrame#header {{ background: {p.bg}; border: none; min-height: {HEADER_H}px; max-height: {HEADER_H}px; }}
QFrame[role="status-chip"] {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 11px; }}

/* ---------- Cards / panels ---------- */
QFrame[role="kpi-card"] {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: {r_lg}px; }}
QFrame[role="kpi-card"]:hover {{ background: {p.surface_2}; }}
QFrame[role="kpi-card"] QLabel[role="kpi-label"] {{ color: {p.text_muted}; font-size: {cap}px;
    font-weight: 500; }}
QFrame[role="kpi-card"] QLabel[role="kpi-value"] {{ font-family: "JetBrains Mono";
    font-size: {_px("kpi_value")}px; font-weight: 600; }}
QFrame[role="panel"] {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: {r_lg}px; }}
QFrame[role="peek"] {{ background: {p.surface_2}; border: 1px solid {p.border_strong}; border-radius: {r_lg}px; }}
QFrame[role="inset"] {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: {r_md}px; }}
QFrame[role="warning-strip"] {{ background: {alpha(p.warning, 0.12)}; border-radius: {r_sm}px; }}
QFrame[role="warning-strip"] QLabel {{ color: {p.warning}; }}
QFrame[role="error-strip"] {{ background: {alpha(p.negative, 0.12)}; border-radius: {r_sm}px; }}
QFrame[role="error-strip"] QLabel {{ color: {p.negative}; }}
QFrame[role="hairline"] {{ background: {p.border}; border: none; max-height: 1px; min-height: 1px; }}
QFrame[role="progress-track"] {{ background: {p.surface_3}; border: none; border-radius: 2px; }}
QFrame[role="progress-fill"] {{ background: {p.accent}; border: none; border-radius: 2px; }}
QFrame[role="progress-fill"][tone="warning"] {{ background: {p.warning}; }}
QFrame[role="progress-fill"][tone="negative"] {{ background: {p.negative}; }}
QFrame[role="skeleton"] {{ background: {p.surface_3}; border: none; border-radius: {r_sm}px; }}

/* ---------- Segmented control ---------- */
QFrame[role="segmented"] {{ background: {p.surface_2}; border: 1px solid {p.border}; border-radius: {r_md}px; }}
QFrame[role="segmented"] QPushButton {{ background: transparent; border: none; border-radius: {r_sm}px;
    color: {p.text_muted}; min-height: {CONTROL_H - 6}px; font-weight: 600; padding: 0 {SPACE[4]}px; }}
QFrame[role="segmented"] QPushButton:hover {{ color: {p.text}; }}
QFrame[role="segmented"] QPushButton:checked {{ background: {p.surface_3}; color: {p.text}; }}
QFrame[role="segmented"] QPushButton[tone="positive"]:checked {{ color: {p.positive};
    border-bottom: 2px solid {p.positive}; }}
QFrame[role="segmented"] QPushButton[tone="negative"]:checked {{ color: {p.negative};
    border-bottom: 2px solid {p.negative}; }}
QFrame[role="segmented"] QPushButton:focus {{ border: 1px solid {p.accent}; }}
/* View switch (Positions | Performance): larger text, the current view in the selection tint */
QFrame[role="segmented"][scale="view"] QPushButton {{ font-size: {title}px; padding: 0 {SPACE[5]}px; }}
QFrame[role="segmented"][scale="view"] QPushButton:checked {{ background: {alpha(p.accent, 0.14)}; color: {p.accent}; }}
QFrame[role="vrule"] {{ background: {p.border_strong}; border: none; min-width: 1px; max-width: 1px; }}
/* Large side switch (trade dialog): solid fill for the active side */
QFrame[role="segmented"][scale="large"] {{ border-radius: {r_md + 2}px; }}
QFrame[role="segmented"][scale="large"] QPushButton {{ min-height: 40px; font-size: {title}px; font-weight: 700;
    padding: 0 {SPACE[6]}px; border-radius: {r_md}px; }}
QFrame[role="segmented"][scale="large"] QPushButton:hover {{ background: {p.surface_3}; }}
QFrame[role="segmented"][scale="large"] QPushButton[tone="positive"]:checked {{ background: {p.positive};
    color: {p.text_on_accent}; border: none; }}
QFrame[role="segmented"][scale="large"] QPushButton[tone="negative"]:checked {{ background: {p.negative};
    color: {p.text_on_accent}; border: none; }}
QLabel[role="title"][tone="positive"] {{ color: {p.positive}; }}
QLabel[role="title"][tone="negative"] {{ color: {p.negative}; }}

/* ---------- Table ---------- */
QTreeView, QTableView, QListView {{ background: {p.surface}; alternate-background-color: {p.surface};
    border: none; color: {p.text}; selection-background-color: transparent; selection-color: {p.text};
    show-decoration-selected: 1; }}
QFrame[role="table-panel"] {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: {r_lg}px; }}
QHeaderView {{ background: {p.surface_2}; border: none; }}
QHeaderView::section {{ background: {p.surface_2}; color: {p.text_muted}; border: none;
    border-bottom: 1px solid {p.border}; padding: 0 {SPACE[2]}px; height: 30px; font-weight: 600; }}
QHeaderView::section:hover {{ color: {p.text}; }}
QHeaderView::up-arrow, QHeaderView::down-arrow {{ width: 0; height: 0; }}
QTableView {{ gridline-color: {p.border}; }}
QTreeView::item, QTableView::item {{ border: none; padding: 0; background: transparent; }}
QTreeView::item:selected, QTreeView::item:hover {{ background: transparent; }}
QTreeView::branch {{ background: transparent; image: none; border-image: none; }}
QTableView::item:selected {{ background: {alpha(p.accent, 0.14)}; color: {p.text}; }}
QTableView#plain {{ gridline-color: transparent; }}

/* ---------- Toasts / drawer ---------- */
QFrame[role="toast"] {{ background: {p.surface_3}; border: 1px solid {p.border_strong}; border-radius: {r_md}px; }}
QFrame[role="drawer"] {{ background: {p.surface}; border: none; border-left: 1px solid {p.border}; }}
QFrame[role="ladder-card"] {{ background: {p.surface_2}; border: 1px solid {p.border}; border-radius: {r_lg}px; }}

QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {p.text_muted}; padding: {SPACE[2]}px {SPACE[3]}px;
    border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {p.text}; border-bottom: 2px solid {p.accent}; }}
QDialog {{ background: {p.surface_2}; }}
QDialog QLabel[role="field-label"] {{ color: {p.text_muted}; }}
QMessageBox {{ background: {p.surface_2}; }}
"""
