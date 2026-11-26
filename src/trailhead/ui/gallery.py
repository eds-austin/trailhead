"""Hidden debug window (`trailhead --gallery`) showing every themed component."""

from __future__ import annotations

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .theme import tokens
from .widgets.badge import Badge
from .widgets.common import button, frame, icon_button, label
from .widgets.empty_state import EmptyState
from .widgets.kpi_card import KpiData, KpiStrip
from .widgets.pill import LevelPill
from .widgets.segmented import Segmented
from .widgets.status_chip import BellButton, PriceStatusChip
from .widgets.toast import ToastHost


def _section(title: str) -> QLabel:
    lb = label(title, role="title")
    lb.setFont(theme.font("title"))
    return lb


class GalleryWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Trailhead — Component gallery")
        self.resize(1200, 900)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body.setObjectName("root")
        scroll.setWidget(body)
        self.setCentralWidget(scroll)
        self.toasts = ToastHost(scroll.viewport())
        v = QVBoxLayout(body)
        v.setContentsMargins(24, 24, 24, 24)
        v.setSpacing(tokens.SPACE[4])
        p = theme.palette()

        v.addWidget(_section("Colors"))
        grid = QGridLayout()
        for i, name in enumerate(p.__dataclass_fields__):
            sw = QWidget()
            sw.setObjectName("swatch")
            sw.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            sw.setFixedSize(132, 56)
            color = getattr(p, name)
            fg = p.text_on_accent if name in ("text", "text_muted", "accent", "positive", "negative",
                                               "stop", "target", "warning", "info") else p.text
            sw.setStyleSheet(f"#swatch {{ background: {color}; border-radius: 6px; border: 1px solid {p.border}; }}")
            lb = QLabel(f"{name}\n{color}", sw)
            lb.setStyleSheet(f"color: {fg}; padding: 6px;")
            lb.setFont(theme.font("caption"))
            grid.addWidget(sw, i // 8, i % 8)
        v.addLayout(grid)

        v.addWidget(_section("Typography"))
        for tok in tokens.TYPE:
            lb = QLabel(f"{tok} — $12,345.67  +1.05R  The quick brown fox")
            lb.setFont(theme.caption_upper_font() if tok == "caption" else theme.font(tok))
            v.addWidget(lb)

        v.addWidget(_section("Buttons"))
        row = QHBoxLayout()
        row.addWidget(button("New Trade", "primary", icon="plus"))
        row.addWidget(button("Cash", icon="wallet"))
        row.addWidget(button("Ghost", "ghost"))
        row.addWidget(button("Delete", "danger", icon="trash-2"))
        row.addWidget(button("Delete trade", "danger-solid"))
        dis = button("Disabled", "primary")
        dis.setEnabled(False)
        row.addWidget(dis)
        row.addWidget(icon_button("refresh-cw", "Refresh"))
        bell = BellButton()
        bell.set_count(3)
        row.addWidget(bell)
        chip = PriceStatusChip()
        chip.set_status("fresh", "Prices 10:42")
        row.addWidget(chip)
        chip2 = PriceStatusChip()
        chip2.set_status("stale", "Prices 2d ago")
        row.addWidget(chip2)
        row.addStretch(1)
        v.addLayout(row)

        v.addWidget(_section("Inputs"))
        row = QHBoxLayout()
        le = QLineEdit()
        le.setPlaceholderText("Symbol")
        row.addWidget(le)
        sp = QDoubleSpinBox()
        sp.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        sp.setMaximum(1e9)
        sp.setValue(127.40)
        row.addWidget(sp)
        err = QLineEdit("121.50")
        err.setProperty("error", True)
        row.addWidget(err)
        de = QDateEdit(QDate.currentDate())
        de.setCalendarPopup(True)
        de.setDisplayFormat("yyyy-MM-dd")
        row.addWidget(de)
        cb = QComboBox()
        cb.addItems(["—", "Pullback", "Breakout", "Reversion"])
        row.addWidget(cb)
        row.addWidget(QCheckBox("Desktop notifications"))
        seg = Segmented([("buy", "BUY", "positive"), ("sell", "SELL", "negative")])
        row.addWidget(seg)
        v.addLayout(row)
        v.addWidget(label("Initial stop must be below price", role="error"))

        v.addWidget(_section("Pills & badges"))
        row = QHBoxLayout()
        row.addWidget(LevelPill("stop", "118.40", "−4.1% · −1.3R"))
        row.addWidget(LevelPill("stop", "118.40", "−1.2% · −0.4R", state="near"))
        row.addWidget(LevelPill("stop", "118.40", "+0.3%", state="hit"))
        row.addWidget(LevelPill("target", "131.20", "+3.0% · +1.0R"))
        for text, kind in [("OPEN", "open"), ("CLOSED", "closed"), ("STOP HIT", "stop_hit"),
                           ("Raise stop → $41.72", "action"), ("Earnings 11/24", "earnings"),
                           ("M", "manual"), ("Stale", "stale")]:
            row.addWidget(Badge(text, kind))
        row.addStretch(1)
        v.addLayout(row)

        v.addWidget(_section("KPI strip"))
        strip = KpiStrip([("value", "Portfolio value"), ("unreal", "Unrealized P/L"),
                          ("deployed", "Deployed / Max"), ("deployed2", "Deployed / Max"),
                          ("pf", "Profit factor"), ("loading", "Loading")])
        strip.update_cards({
            "value": KpiData("$104,218.40", "+$1,204.10 · +1.17%", sub_tone="positive"),
            "unreal": KpiData("−$482.10", "−1.9% of cost", value_tone="negative", sub_tone="negative"),
            "deployed": KpiData("$68,400", "of $100,000", progress=0.684),
            "deployed2": KpiData("$104,200", "of $100,000", progress=1.04),
            "pf": KpiData("—", ""),
        })
        strip.cards["loading"].set_loading(True)
        v.addWidget(strip)

        v.addWidget(_section("Toasts"))
        row = QHBoxLayout()
        for sev in ("info", "success", "warning", "error"):
            b = button(f"Show {sev}")
            b.clicked.connect(lambda _=False, s=sev: self.toasts.show(
                f"{s.title()} toast", "NVDA — Last 118.10 ≤ stop 118.40. Sell remaining 75 sh.", s))
            row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)

        v.addWidget(_section("Empty state"))
        es_panel = frame("table-panel")
        es_panel.setFixedHeight(260)
        QVBoxLayout(es_panel).addWidget(
            EmptyState("No positions yet", "Add your first trade to start tracking.", "New Trade"))
        v.addWidget(es_panel)
        v.addStretch(1)
