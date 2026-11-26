"""Alerts drawer (360 px) opened from the bell: grouped by severity, with Acknowledge / Acknowledge all."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from ...alerts.engine import SEVERITY, AlertKind
from ...context import AppContext
from ...core.models import AlertRecord
from .. import theme
from ..theme import tokens
from ..widgets.common import button, icon_button, label

SEV_LABEL = {"critical": "CRITICAL", "warning": "WARNING", "info": "INFO"}
SEV_TOKEN = {"critical": "negative", "warning": "warning", "info": "info"}


class _AlertRow(QFrame):
    ack = Signal(int)
    open_symbol = Signal(int, str)

    def __init__(self, a: AlertRecord, severity: str):
        super().__init__()
        self.a = a
        p = theme.palette()
        self.setObjectName("alertRow")
        self.setStyleSheet(f"#alertRow {{ border-bottom: 1px solid {p.border}; }}"
                           f"#alertRow:hover {{ background: {p.surface_2}; }}")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        h = QHBoxLayout(self)
        h.setContentsMargins(tokens.SPACE[3], tokens.SPACE[2], tokens.SPACE[2], tokens.SPACE[2])
        h.setSpacing(tokens.SPACE[2])
        bar = QFrame()
        bar.setFixedWidth(3)
        bar.setStyleSheet(f"background: {getattr(p, SEV_TOKEN[severity])}; border-radius: 1px;")
        h.addWidget(bar)
        v = QVBoxLayout()
        v.setSpacing(2)
        top = QHBoxLayout()
        sym = QLabel(a.symbol)
        sym.setFont(theme.font("body_strong"))
        top.addWidget(sym)
        kind = label(a.kind.replace("_", " ").title(), role="caption")
        top.addWidget(kind)
        top.addStretch(1)
        when = label(f"{a.triggered_at:%b %d %H:%M}", role="caption")
        top.addWidget(when)
        v.addLayout(top)
        msg = label(a.message, role="muted")
        msg.setWordWrap(True)
        v.addWidget(msg)
        h.addLayout(v, 1)
        if not a.acknowledged:
            b = button("Ack", "ghost", size="small", tooltip="Acknowledge")
            b.clicked.connect(lambda: self.ack.emit(a.id))
            h.addWidget(b, 0, Qt.AlignmentFlag.AlignTop)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        self.open_symbol.emit(self.a.portfolio_id, self.a.symbol)
        super().mouseReleaseEvent(e)


class AlertsPanel(QFrame):
    open_symbol = Signal(int, str)

    def __init__(self, ctx: AppContext, host: QWidget):
        super().__init__(host)
        self.ctx = ctx
        self.host = host
        self.setProperty("role", "drawer")
        self.setFixedWidth(tokens.ALERTS_PANEL_W)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(tokens.SPACE[4], tokens.SPACE[3], tokens.SPACE[2], tokens.SPACE[3])
        t = QLabel("Alerts")
        t.setFont(theme.font("title"))
        head.addWidget(t)
        head.addStretch(1)
        self.ack_all = button("Acknowledge all", "ghost", size="small")
        self.ack_all.clicked.connect(self._ack_all)
        head.addWidget(self.ack_all)
        close = icon_button("x", "Close", size=16)
        close.clicked.connect(self.hide)
        head.addWidget(close)
        v.addLayout(head)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        v.addWidget(self.scroll, 1)
        self.show_acked = False
        self.toggle_acked = button("Show acknowledged", "ghost", size="small")
        self.toggle_acked.clicked.connect(self._toggle_acked)
        foot = QHBoxLayout()
        foot.setContentsMargins(tokens.SPACE[3], tokens.SPACE[2], tokens.SPACE[3], tokens.SPACE[2])
        foot.addWidget(self.toggle_acked)
        foot.addStretch(1)
        v.addLayout(foot)
        host.installEventFilter(self)
        self.hide()

    def eventFilter(self, obj: QObject, e: QEvent) -> bool:  # noqa: N802
        if obj is self.host and e.type() == QEvent.Type.Resize:
            self._place()
        return False

    def _place(self) -> None:
        top = tokens.HEADER_H
        self.setGeometry(self.host.width() - self.width(), top, self.width(), self.host.height() - top)

    def toggle(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            self.reload()
            self._place()
            self.show()
            self.raise_()

    def _toggle_acked(self) -> None:
        self.show_acked = not self.show_acked
        self.toggle_acked.setText("Hide acknowledged" if self.show_acked else "Show acknowledged")
        self.reload()

    def reload(self) -> None:
        alerts = self.ctx.repos.alerts.list(unacked_only=not self.show_acked)[:200]
        body = QWidget()
        v = QVBoxLayout(body)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        groups: dict[str, list[AlertRecord]] = {"critical": [], "warning": [], "info": []}
        for a in alerts:
            sev = SEVERITY.get(AlertKind(a.kind), "info") if a.kind in AlertKind.__members__ else "info"
            groups[sev].append(a)
        if not alerts:
            from ..widgets.empty_state import EmptyState

            v.addWidget(EmptyState("All clear", "No unacknowledged alerts.", icon="circle-check"), 1)
        for sev, items in groups.items():
            if not items:
                continue
            cap = label(f"{SEV_LABEL[sev]} · {len(items)}", role="caption")
            cap.setContentsMargins(tokens.SPACE[4], tokens.SPACE[3], 0, tokens.SPACE[1])
            v.addWidget(cap)
            for a in items:
                row = _AlertRow(a, sev)
                row.ack.connect(self._ack)
                row.open_symbol.connect(self.open_symbol.emit)
                v.addWidget(row)
        v.addStretch(1)
        self.scroll.setWidget(body)
        self.ack_all.setEnabled(any(not a.acknowledged for a in alerts))

    def _ack(self, aid: int) -> None:
        if self.ctx.alerts is not None:
            self.ctx.alerts.acknowledge(aid)
        else:
            self.ctx.repos.alerts.acknowledge(aid)
        self.ctx.events.alerts_changed.emit()
        self.reload()

    def _ack_all(self) -> None:
        if self.ctx.alerts is not None:
            self.ctx.alerts.acknowledge_all()
        else:
            self.ctx.repos.alerts.acknowledge_all()
        self.ctx.events.alerts_changed.emit()
        self.reload()
