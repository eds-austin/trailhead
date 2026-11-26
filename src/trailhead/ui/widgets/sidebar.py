"""Left navigation: Dashboard, portfolios, + New portfolio, Settings."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .. import theme
from ..theme import tokens


class Sidebar(QFrame):
    selected = Signal(str)  # "dashboard" or "portfolio:<id>"
    new_portfolio = Signal()
    open_settings = Signal()
    context_requested = Signal(int, QPoint)  # portfolio id, global pos
    collapsed_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(tokens.SIDEBAR_W["expanded"])
        self._v = QVBoxLayout(self)
        self._v.setContentsMargins(0, tokens.SPACE[3], 0, tokens.SPACE[3])
        self._v.setSpacing(2)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._items: dict[str, QPushButton] = {}
        self._collapsed = False

        brand = QWidget()
        bh = QHBoxLayout(brand)
        self._brand_layout = bh
        bh.setContentsMargins(tokens.SPACE[3], 0, 0, 0)
        bh.setSpacing(tokens.SPACE[2] + 2)
        logo = QLabel()
        logo.setPixmap(theme.brand_pixmap())
        name = QLabel("Trailhead")
        name.setFont(theme.font("body_strong"))
        bh.addWidget(logo)
        bh.addWidget(name, 1)
        self._brand_name = name
        brand.setFixedHeight(tokens.SIDEBAR_ITEM_H)
        self._v.addWidget(brand)
        self._v.addSpacing(tokens.SPACE[2])

        self._add_item("dashboard", "Dashboard", "layout-dashboard")
        self._v.addSpacing(tokens.SPACE[3])
        sec = QPushButton("PORTFOLIOS")
        sec.setProperty("role", "nav")
        sec.setEnabled(False)
        sec.setFont(theme.caption_upper_font())
        sec.setMinimumHeight(24)
        sec.setMaximumHeight(24)
        self._v.addWidget(sec)
        self._section = sec
        self._portfolio_box = QVBoxLayout()
        self._portfolio_box.setSpacing(2)
        self._v.addLayout(self._portfolio_box)
        self.new_btn = self._make_button("New portfolio", "plus")
        self.new_btn.setCheckable(False)
        self.new_btn.clicked.connect(self.new_portfolio.emit)
        self._v.addWidget(self.new_btn)
        self._v.addStretch(1)
        self.toggle_btn = self._make_button("Collapse", "panel-left-close")
        self.toggle_btn.setCheckable(False)
        self.toggle_btn.setToolTip("Collapse sidebar (Ctrl+\\)")
        self.toggle_btn.clicked.connect(lambda: self.set_collapsed(not self._collapsed))
        self._v.addWidget(self.toggle_btn)
        self.settings_btn = self._make_button("Settings", "settings")
        self.settings_btn.setCheckable(False)
        self.settings_btn.clicked.connect(self.open_settings.emit)
        self._v.addWidget(self.settings_btn)

    # ------------------------------------------------------------------ collapse
    def is_collapsed(self) -> bool:
        return self._collapsed

    def set_collapsed(self, collapsed: bool, emit: bool = True) -> None:
        """Icon-only rail (64 px) or full width (208 px). Labels move into tooltips when collapsed."""
        changed = collapsed != self._collapsed
        self._collapsed = collapsed
        self.setFixedWidth(tokens.SIDEBAR_W["collapsed" if collapsed else "expanded"])
        self._brand_name.setVisible(not collapsed)
        left = (tokens.SIDEBAR_W["collapsed"] - tokens.BRAND_MARK) // 2 if collapsed else tokens.SPACE[3]
        self._brand_layout.setContentsMargins(left, 0, 0, 0)
        self._section.setVisible(not collapsed)
        for b in self._buttons():
            self._apply_mode(b)
        p = theme.palette()
        icon = "panel-left-open" if collapsed else "panel-left-close"
        self.toggle_btn.setIcon(theme.icon(icon, p.text_muted, 18, active_color=p.text))
        self.toggle_btn.setProperty("label", "Expand" if collapsed else "Collapse")
        self._apply_mode(self.toggle_btn)
        self.toggle_btn.setToolTip(("Expand" if collapsed else "Collapse") + " sidebar (Ctrl+\\)")
        if changed and emit:
            self.collapsed_changed.emit(collapsed)

    def _buttons(self) -> list[QPushButton]:
        return [*self._items.values(), self.new_btn, self.settings_btn]

    def _apply_mode(self, b: QPushButton) -> None:
        text = b.property("label") or ""
        b.setText("" if self._collapsed else f"  {text}")
        if b is not self.toggle_btn:
            b.setToolTip(text if self._collapsed else "")
        b.setProperty("collapsed", self._collapsed)
        b.style().unpolish(b)
        b.style().polish(b)

    def _make_button(self, text: str, icon: str) -> QPushButton:
        b = QPushButton(f"  {text}")
        b.setProperty("role", "nav")
        b.setProperty("label", text)
        b.setCheckable(True)
        p = theme.palette()
        b.setIcon(theme.icon(icon, p.text_muted, 18, active_color=p.text))
        b.setIconSize(QSize(18, 18))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # pages are switched by Ctrl+0…9; avoids a stray focus bar
        return b

    def _add_item(self, key: str, text: str, icon: str, layout=None) -> QPushButton:
        b = self._make_button(text, icon)
        self._group.addButton(b)
        (layout or self._v).addWidget(b)
        self._items[key] = b
        b.clicked.connect(lambda _=False, k=key: self.selected.emit(k))
        return b

    def set_portfolios(self, items: list[tuple[int, str, str]]) -> None:
        """items: (id, name, icon)."""
        for key in [k for k in self._items if k.startswith("portfolio:")]:
            b = self._items.pop(key)
            self._group.removeButton(b)
            b.deleteLater()
        for pid, name, icon in items:
            b = self._add_item(f"portfolio:{pid}", name, icon, self._portfolio_box)
            b.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            b.customContextMenuRequested.connect(
                lambda pos, pid=pid, btn=b: self.context_requested.emit(pid, btn.mapToGlobal(pos)))
            self._apply_mode(b)

    def set_current(self, key: str) -> None:
        b = self._items.get(key)
        if b:
            b.setChecked(True)

    def keys(self) -> list[str]:
        return list(self._items)
