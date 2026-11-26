"""KPI card and the wrapping KPI strip."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from .. import theme
from ..theme import tokens
from .common import set_prop
from .flow_layout import FlowLayout


@dataclass
class KpiData:
    value: str = "—"
    sub: str = ""
    value_tone: str | None = None  # positive | negative | None (only for signed P/L)
    sub_tone: str | None = None  # positive | negative | warning | None
    progress: float | None = None  # 0..∞ fraction for Deployed/Max
    tooltip: str = ""
    value_warning: bool = False
    label: str | None = None  # replaces the card's title (e.g. "Return · YTD")


class _ElidedLabel(QLabel):
    """One-line label that ends in … when it doesn't fit; the full text is its tooltip."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full = ""
        self.setMinimumWidth(1)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.set_full_text(text)

    def set_full_text(self, text: str) -> None:
        self._full = text
        self._elide()

    def full_text(self) -> str:
        return self._full

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._elide()

    def _elide(self) -> None:
        shown = self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, max(1, self.width()))
        super().setText(shown)
        super().setToolTip(self._full if shown != self._full else "")


class _Progress(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("role", "progress-track")
        self.setFixedHeight(tokens.PROGRESS_H)
        self._fill = QFrame(self)
        self._fill.setProperty("role", "progress-fill")
        self._frac = 0.0

    def set_fraction(self, frac: float) -> None:
        self._frac = max(0.0, frac)
        tone = "negative" if frac > 1.0 else "warning" if frac > 0.9 else None
        set_prop(self._fill, "tone", tone)
        self._relayout()

    def _relayout(self) -> None:
        self._fill.setGeometry(0, 0, int(self.width() * min(1.0, self._frac)), self.height())

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._relayout()


class _Skeleton(QFrame):
    """Shimmering placeholder bar shown while a value is loading."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("role", "skeleton")
        self.setFixedHeight(16)
        self._opacity = 1.0
        self._anim = QPropertyAnimation(self, b"shimmer", self)
        self._anim.setDuration(tokens.SHIMMER_MS)
        self._anim.setStartValue(0.35)
        self._anim.setKeyValueAt(0.5, 1.0)
        self._anim.setEndValue(0.35)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._anim.setLoopCount(-1)

    def _get(self) -> float:
        return self._opacity

    def _set(self, v: float) -> None:
        self._opacity = v
        p = theme.palette()
        c = tokens.blend(p.surface_3, v, p.surface)
        self.setStyleSheet(f"background: {c};")

    shimmer = Property(float, _get, _set)

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        self._anim.start()

    def hideEvent(self, e) -> None:  # noqa: N802
        super().hideEvent(e)
        self._anim.stop()


class KpiCard(QFrame):
    def __init__(self, key: str, label: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.key = key
        self.setProperty("role", "kpi-card")
        self.setMinimumWidth(tokens.KPI_MIN_W)
        self.setFixedHeight(tokens.KPI_H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        v = QVBoxLayout(self)
        v.setContentsMargins(tokens.CARD_PAD[1], tokens.CARD_PAD[0], tokens.CARD_PAD[1], tokens.CARD_PAD[0])
        v.setSpacing(2)
        self._label = QLabel(label)
        self._label.setProperty("role", "kpi-label")
        self._label.setFont(theme.caption_upper_font())
        self._value = QLabel("—")
        self._value.setProperty("role", "kpi-value")
        self._value.setFont(theme.font("kpi_value"))
        self._skeleton = _Skeleton()
        self._skeleton.hide()
        self._sub = _ElidedLabel("")
        self._sub.setProperty("role", "caption")
        self._sub.setFont(theme.font("caption"))
        self._progress = _Progress()
        self._progress.hide()
        v.addWidget(self._label)
        v.addWidget(self._value)
        v.addWidget(self._skeleton)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self._sub)
        v.addLayout(row)
        v.addWidget(self._progress)
        v.addStretch(1)

    def set_loading(self, loading: bool) -> None:
        self._skeleton.setVisible(loading)
        self._value.setVisible(not loading)

    def set_data(self, d: KpiData) -> None:
        self.set_loading(False)
        self._value.setText(d.value)
        tone = d.value_tone or ("warning" if d.value_warning else None)
        if d.value == "—":
            tone = "faint"
        set_prop(self._value, "tone", tone)
        sub = d.sub
        if d.sub_tone in ("positive", "negative") and sub and sub[0] not in "▲▼":
            sub = ("▲ " if d.sub_tone == "positive" else "▼ ") + sub
        self._sub.set_full_text(sub)
        if d.label is not None:
            self._label.setText(d.label)
        set_prop(self._sub, "tone", d.sub_tone)
        self._progress.setVisible(d.progress is not None)
        if d.progress is not None:
            self._progress.set_fraction(d.progress)
        self.setToolTip(d.tooltip)

    def value_text(self) -> str:
        return self._value.text()


class KpiStrip(QWidget):
    """Cards in a FlowLayout that wraps onto extra rows on narrow windows."""

    def __init__(self, cards: list[tuple[str, str]], parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self._layout = FlowLayout(self, spacing=tokens.SPACE[2], balanced=True)
        self.cards: dict[str, KpiCard] = {}
        for key, text in cards:
            c = KpiCard(key, text)
            self._layout.addWidget(c)
            self.cards[key] = c
        sp = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, w: int) -> int:  # noqa: N802
        return self._layout.heightForWidth(w)

    def set_loading(self, loading: bool) -> None:
        for c in self.cards.values():
            c.set_loading(loading)

    def update_cards(self, data: dict[str, KpiData]) -> None:
        for key, d in data.items():
            if key in self.cards:
                self.cards[key].set_data(d)
