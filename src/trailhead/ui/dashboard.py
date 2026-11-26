"""Dashboard: combined summary band with a compact cumulative P/L chart, a portfolios table with a Total row, and a
right rail with Today (biggest moves, needs attention) and Allocation (by portfolio or sector)."""

from __future__ import annotations

from decimal import Decimal

from PySide6.QtCore import QModelIndex, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QScrollArea,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..context import AppContext
from ..core.models import Portfolio
from ..core.stats import PERIOD_LABELS, PeriodKey, period_for
from ..portfolio_types import registry
from ..portfolio_types import totals as agg
from ..portfolio_types.base import DashboardSummary
from . import format as fmt
from . import theme
from .charts.equity_chart import CompactPLChart
from .theme import tokens
from .widgets.common import frame, label, set_prop
from .widgets.empty_state import EmptyState
from .widgets.segmented import Segmented

PERIODS = [(PeriodKey.MTD, "MTD"), (PeriodKey.QTD, "QTD"), (PeriodKey.YTD, "YTD"), (PeriodKey.L12M, "12M"),
           (PeriodKey.ALL, "All")]
COLUMNS = ["Portfolio", "Value", "Share", "Today", "P/L", "Unrealized", "Income", "Open risk", "Trend"]
C_NAME, C_VALUE, C_SHARE, C_TODAY, C_PL, C_UNREAL, C_INCOME, C_RISK, C_TREND = range(len(COLUMNS))
ROW_H = 44
PID_ROLE = Qt.ItemDataRole.UserRole
SUB_ROLE = Qt.ItemDataRole.UserRole + 1
ICON_ROLE = Qt.ItemDataRole.UserRole + 2
SPARK_ROLE = Qt.ItemDataRole.UserRole + 3
TOTAL_ROLE = Qt.ItemDataRole.UserRole + 4
TONE_ICONS = {"negative": "octagon-alert", "warning": "triangle-alert", "info": "calendar",
              "positive": "circle-check"}


def _tone(v: Decimal | None) -> str | None:
    s = fmt.sign_of(v)
    return "positive" if s > 0 else "negative" if s < 0 else None


def _arrow(v: Decimal | None) -> str:
    s = fmt.sign_of(v)
    return "▲ " if s > 0 else "▼ " if s < 0 else ""


def _caption(text: str = "") -> QLabel:
    lb = label(text, role="muted")
    lb.setFont(theme.caption_upper_font())
    return lb


def _num(text: str = "", tone: str | None = None, token: str = "num") -> QLabel:
    lb = label(text, tone=tone)
    lb.setFont(theme.font(token))
    return lb


def _set_num(lb: QLabel, text: str, tone: str | None = None) -> None:
    lb.setText(text)
    set_prop(lb, "tone", tone)


def _clear(layout: QLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget() is not None:
            item.widget().deleteLater()
        elif item.layout() is not None:
            _clear(item.layout())


def _panel(spacing: int = tokens.SPACE[2]) -> tuple[QFrame, QVBoxLayout]:
    f = frame("panel")
    v = QVBoxLayout(f)
    v.setContentsMargins(tokens.SPACE[4], tokens.SPACE[3], tokens.SPACE[4], tokens.SPACE[3])
    v.setSpacing(spacing)
    return f, v


def _icon_label(name: str, color: str, size: int = 16) -> QLabel:
    lb = QLabel()
    lb.setPixmap(theme.pixmap(name, color, size))
    lb.setFixedSize(size, size)
    return lb


class _Bar(QWidget):
    """Thin horizontal share bar: surface_3 track, accent fill."""

    def __init__(self, frac: float, parent=None):
        super().__init__(parent)
        self.frac = max(0.0, min(1.0, frac))
        self.setFixedHeight(6)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = theme.palette()
        qp = QPainter(self)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing)
        qp.setPen(Qt.PenStyle.NoPen)
        r = QRectF(self.rect())
        qp.setBrush(QColor(p.surface_3))
        qp.drawRoundedRect(r, 3, 3)
        if self.frac > 0:
            qp.setBrush(QColor(p.accent))
            qp.drawRoundedRect(QRectF(r.left(), r.top(), max(r.height(), r.width() * self.frac), r.height()), 3, 3)
        qp.end()


class _LegendSwatch(QWidget):
    """12px line sample: solid accent (combined) or dashed muted (per portfolio)."""

    def __init__(self, dashed: bool, parent=None):
        super().__init__(parent)
        self.dashed = dashed
        self.setFixedSize(14, 10)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = theme.palette()
        qp = QPainter(self)
        pen = QPen(QColor(p.text_muted if self.dashed else p.accent), 2)
        if self.dashed:
            pen.setStyle(Qt.PenStyle.DashLine)
            pen.setWidthF(1.5)
        qp.setPen(pen)
        y = self.height() / 2
        qp.drawLine(QPointF(0, y), QPointF(self.width(), y))
        qp.end()


class _RowDelegate(QStyledItemDelegate):
    """Paints the portfolios table: row tints, the name cell (icon, name, subtitle), sparklines and numbers."""

    def __init__(self, table: QTableWidget):
        super().__init__(table)
        self.table = table
        self.hover_row = -1

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        pad = tokens.SPACE[2] * 2
        col = index.column()
        if col == C_TREND:
            return QSize(96 + pad, ROW_H)
        if col == C_NAME:
            fm = QFontMetrics(theme.font("body_strong"))
            sub = QFontMetrics(theme.font("caption")).horizontalAdvance(index.data(SUB_ROLE) or "")
            return QSize(16 + tokens.SPACE[2] + max(fm.horizontalAdvance(index.data() or ""), sub) + pad, ROW_H)
        fm = QFontMetrics(theme.font("num", weight=600))
        return QSize(fm.horizontalAdvance(index.data() or "") + pad + 2, ROW_H)

    def paint(self, qp: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        p = theme.palette()
        r = QRectF(option.rect)
        qp.save()
        qp.setRenderHint(QPainter.RenderHint.Antialiasing)
        is_total = bool(index.data(TOTAL_ROLE))
        selected = bool(option.state & QStyle.StateFlag.State_Selected) and not is_total
        if is_total:
            qp.fillRect(r, QColor(p.surface_2))
        elif selected:
            qp.fillRect(r, theme.qcolor(p.accent, 0.14))
        elif index.row() == self.hover_row:
            qp.fillRect(r, theme.qcolor(p.accent, 0.06))
        if selected and index.column() == 0:
            qp.fillRect(QRectF(r.left(), r.top(), 2, r.height()), QColor(p.accent))
        if not is_total:
            qp.setPen(QPen(QColor(p.border), 1))
            qp.drawLine(QPointF(r.left(), r.bottom()), QPointF(r.right(), r.bottom()))
        pad = tokens.SPACE[2]
        inner = r.adjusted(pad, 0, -pad, 0)
        col = index.column()
        if col == C_NAME:
            self._paint_name(qp, inner, index, is_total)
        elif col == C_TREND:
            self._paint_spark(qp, inner, index.data(SPARK_ROLE) or [])
        else:
            fg = index.data(Qt.ItemDataRole.ForegroundRole)
            qp.setPen(fg.color() if fg is not None else QColor(p.text))
            f = theme.font("num", weight=600 if is_total else None)
            qp.setFont(f)
            qp.drawText(inner, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, index.data() or "")
        qp.restore()

    def _paint_name(self, qp: QPainter, r: QRectF, index: QModelIndex, is_total: bool) -> None:
        p = theme.palette()
        icon = index.data(ICON_ROLE)
        x = r.left()
        if icon:
            qp.drawPixmap(int(x), int(r.center().y() - 8), theme.pixmap(icon, p.accent, 16))
            x += 16 + tokens.SPACE[2]
        sub = index.data(SUB_ROLE) or ""
        qp.setPen(QColor(p.text))
        qp.setFont(theme.font("body_strong"))
        if sub:
            qp.drawText(QRectF(x, r.top() + 4, r.right() - x, 20), Qt.AlignmentFlag.AlignVCenter,
                        index.data() or "")
            qp.setPen(QColor(p.text_muted))
            qp.setFont(theme.font("caption"))
            qp.drawText(QRectF(x, r.top() + 22, r.right() - x, 16), Qt.AlignmentFlag.AlignVCenter, sub)
        else:
            qp.drawText(QRectF(x, r.top(), r.right() - x, r.height()), Qt.AlignmentFlag.AlignVCenter,
                        index.data() or "")

    def _paint_spark(self, qp: QPainter, r: QRectF, values: list[float]) -> None:
        if len(values) < 2:
            return
        box = QRectF(r.left(), r.center().y() - 11, min(r.width(), 96), 22)
        lo, hi = min(values), max(values)
        span = (hi - lo) or 1.0
        path = QPainterPath()
        for i, v in enumerate(values):
            x = box.left() + box.width() * i / (len(values) - 1)
            pt = QPointF(x, box.bottom() - box.height() * (v - lo) / span)
            if i == 0:
                path.moveTo(pt)
            else:
                path.lineTo(pt)
        pen = QPen(QColor(theme.palette().accent), 1.5)
        qp.setPen(pen)
        qp.setBrush(Qt.BrushStyle.NoBrush)
        qp.drawPath(path)


class PortfolioTable(QTableWidget):
    open_portfolio = Signal(int)

    def __init__(self, parent=None):
        super().__init__(0, len(COLUMNS), parent)
        self.setObjectName("plain")
        self.delegate = _RowDelegate(self)
        self.setItemDelegate(self.delegate)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(ROW_H)
        self.setShowGrid(False)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        h = self.horizontalHeader()
        h.setHighlightSections(False)
        h.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(C_NAME, QHeaderView.ResizeMode.Stretch)
        h.setMinimumSectionSize(72)
        self.activated.connect(self._on_activated)
        self.entered.connect(self._on_entered)

    def _on_entered(self, index: QModelIndex) -> None:
        if self.delegate.hover_row != index.row():
            self.delegate.hover_row = index.row()
            self.viewport().update()

    def leaveEvent(self, e) -> None:  # noqa: N802
        self.delegate.hover_row = -1
        self.viewport().update()
        super().leaveEvent(e)

    def _on_activated(self, index: QModelIndex) -> None:
        pid = self.item(index.row(), C_NAME).data(PID_ROLE)
        if pid is not None:
            self.open_portfolio.emit(int(pid))

    def set_period_label(self, plabel: str) -> None:
        labels = list(COLUMNS)
        labels[C_PL] = f"P/L {plabel}"
        labels[C_INCOME] = f"Income {plabel}"
        self.setHorizontalHeaderLabels(labels)
        for c in range(len(COLUMNS)):
            align = Qt.AlignmentFlag.AlignLeft if c in (C_NAME, C_TREND) else Qt.AlignmentFlag.AlignRight
            self.horizontalHeaderItem(c).setTextAlignment(align | Qt.AlignmentFlag.AlignVCenter)

    def set_rows(self, rows: list[DashboardSummary], total: agg.Totals) -> None:
        p = theme.palette()
        show_total = len(rows) > 1
        self.setRowCount(len(rows) + (1 if show_total else 0))

        def cell(r: int, c: int, text: str, tone: str | None = None, muted: bool = False,
                 tip: str = "") -> QTableWidgetItem:
            it = QTableWidgetItem(text)
            color = {"positive": p.positive, "negative": p.negative, "warning": p.warning}.get(tone or "")
            if color or muted:
                it.setForeground(QColor(color or p.text_muted))
            if tip:
                it.setToolTip(tip)
            self.setItem(r, c, it)
            return it

        for i, row in enumerate(rows):
            pf = row.portfolio
            name = cell(i, C_NAME, pf.name)
            name.setData(PID_ROLE, pf.id)
            name.setData(SUB_ROLE, row.subtitle)
            name.setData(ICON_ROLE, registry.get(pf.type).icon)
            name.setToolTip("Double-click or press Enter to open")
            self._fill_numbers(i, row.value, agg.share(row, total.value), row.day_change, row.period_pl,
                               row.unrealized_pl, row.income, row.open_risk, row.open_risk_pct, row.open_risk_warn,
                               cell)
            spark = cell(i, C_TREND, "")
            spark.setData(SPARK_ROLE, [float(v) for _, v in row.pl_curve[:: max(1, len(row.pl_curve) // 60)]]
                          + ([float(row.pl_curve[-1][1])] if row.pl_curve else []))
        if show_total:
            r = len(rows)
            for c in range(len(COLUMNS)):
                self.setItem(r, c, QTableWidgetItem(""))
            cell(r, C_NAME, "Total")
            self._fill_numbers(r, total.value, Decimal(1) if total.value else None, total.day_change,
                               total.period_pl, total.unrealized_pl, total.income, total.open_risk, None,
                               total.open_risk_warn, cell)
            for c in range(len(COLUMNS)):
                it = self.item(r, c)
                if it is None:
                    it = cell(r, c, "")
                it.setData(TOTAL_ROLE, True)
                it.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.setFixedHeight(self.horizontalHeader().height() + ROW_H * self.rowCount() + 2)

    def _fill_numbers(self, r, value, share, today, pl, unreal, income, risk, risk_pct, warn, cell) -> None:
        cell(r, C_VALUE, fmt.money(value))
        cell(r, C_SHARE, fmt.pct(share), muted=True)
        cell(r, C_TODAY, fmt.signed_money(today) if today is not None else fmt.DASH, _tone(today),
             muted=today is None or fmt.sign_of(today) == 0)
        cell(r, C_PL, fmt.signed_money(pl) if pl is not None else fmt.DASH, _tone(pl), muted=pl is None)
        cell(r, C_UNREAL, fmt.signed_money(unreal) if unreal is not None else fmt.DASH, _tone(unreal),
             muted=unreal is None)
        cell(r, C_INCOME, fmt.money(income), muted=not income)
        if risk is None:
            cell(r, C_RISK, fmt.DASH, muted=True)
        else:
            text = fmt.money(risk) + (f" · {fmt.pct(risk_pct)}" if risk_pct is not None else "")
            cell(r, C_RISK, ("⚠ " if warn else "") + text, "warning" if warn else None,
                 tip="Open risk is above your limit (Settings › Alerts)" if warn else "Σ remaining × (last − stop)")


class DashboardPage(QWidget):
    open_portfolio = Signal(int)

    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._dirty = True
        self.rows: list[DashboardSummary] = []
        self.stack = QStackedWidget()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.stack)
        self.empty = EmptyState("No portfolios yet", "Create a portfolio to see your totals and charts here.",
                                icon="layout-dashboard")
        self.stack.addWidget(self.empty)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        scroll.setWidget(body)
        self.stack.addWidget(scroll)
        v = QVBoxLayout(body)
        v.setContentsMargins(tokens.WINDOW_PAD, 0, tokens.WINDOW_PAD, tokens.WINDOW_PAD)
        v.setSpacing(tokens.SECTION_GAP)

        top = QHBoxLayout()
        top.addStretch(1)
        self.period = Segmented([(k.value, text) for k, text in PERIODS])
        self.period.set_value(PeriodKey.YTD.value, emit=False)
        self.period.changed.connect(lambda _k: self.reload())
        top.addWidget(self.period)
        v.addLayout(top)

        v.addWidget(self._build_summary())

        table_panel, tv = _panel()
        tv.setContentsMargins(0, tokens.SPACE[3], 0, 0)
        th = QHBoxLayout()
        th.setContentsMargins(tokens.SPACE[4], 0, tokens.SPACE[4], 0)
        th.addWidget(label("Portfolios", font_token="body_strong"))
        th.addStretch(1)
        th.addWidget(label("Double-click a row to open it", role="caption"))
        tv.addLayout(th)
        self.table = PortfolioTable()
        self.table.open_portfolio.connect(self.open_portfolio)
        tv.addWidget(self.table)
        v.addWidget(table_panel)

        # Three equal panels under the table: they share the window's width and the tallest sets the row height.
        cols = QHBoxLayout()
        cols.setSpacing(tokens.SECTION_GAP)
        for panel in (self._build_moves(), self._build_attention(), self._build_allocation()):
            cols.addWidget(panel, 1)
        v.addLayout(cols)
        v.addStretch(1)

        ctx.events.portfolio_data_changed.connect(self._mark_dirty)
        ctx.events.market_updated.connect(self._mark_dirty)
        ctx.events.portfolios_changed.connect(self._mark_dirty)
        ctx.events.settings_changed.connect(self._mark_dirty)

    # ------------------------------------------------------------------ build
    def _build_summary(self) -> QWidget:
        f, v = _panel()
        v.setContentsMargins(tokens.SPACE[5], tokens.SPACE[4], tokens.SPACE[5], tokens.SPACE[4])
        h = QHBoxLayout()
        h.setSpacing(tokens.SPACE[7])
        v.addLayout(h)

        g = QGridLayout()
        g.setHorizontalSpacing(tokens.SPACE[7])
        g.setVerticalSpacing(tokens.SPACE[1])
        self.total_caption = _caption()
        g.addWidget(self.total_caption, 0, 0, 1, 2)
        line = QHBoxLayout()
        line.setSpacing(tokens.SPACE[3])
        self.total_value = _num()
        big = theme.font("kpi_value", weight=500)
        big.setPixelSize(tokens.TYPE["display"][0])
        self.total_value.setFont(big)
        line.addWidget(self.total_value, 0, Qt.AlignmentFlag.AlignBaseline)
        self.total_today = _num()
        line.addWidget(self.total_today, 0, Qt.AlignmentFlag.AlignBaseline)
        line.addStretch(1)
        g.addLayout(line, 1, 0, 1, 2)
        g.setRowMinimumHeight(2, tokens.SPACE[3])
        self.pl_caption = _caption()
        self.income_caption = _caption()
        g.addWidget(self.pl_caption, 3, 0)
        g.addWidget(self.income_caption, 3, 1)
        self.total_pl = _num(token="kpi_value")
        self.total_income = _num(token="kpi_value")
        g.addWidget(self.total_pl, 4, 0)
        g.addWidget(self.total_income, 4, 1)
        self.income_sub = label("", role="caption")
        g.addWidget(self.income_sub, 5, 1)
        left = QWidget()
        left.setLayout(g)
        g.setContentsMargins(0, 0, 0, 0)
        h.addWidget(left, 0, Qt.AlignmentFlag.AlignVCenter)

        right = QVBoxLayout()
        right.setSpacing(tokens.SPACE[1])
        lh = QHBoxLayout()
        self.chart_caption = _caption()
        lh.addWidget(self.chart_caption)
        lh.addStretch(1)
        self.legend = QWidget()
        lg = QHBoxLayout(self.legend)
        lg.setContentsMargins(0, 0, 0, 0)
        lg.setSpacing(tokens.SPACE[3])
        lg.addWidget(_LegendSwatch(dashed=False))
        lg.addWidget(label("All portfolios", role="caption"))
        lg.addSpacing(tokens.SPACE[2])
        lg.addWidget(_LegendSwatch(dashed=True))
        lg.addWidget(label("Each portfolio", role="caption"))
        lh.addWidget(self.legend)
        right.addLayout(lh)
        self.chart = CompactPLChart()
        self.chart.setFixedHeight(128)
        right.addWidget(self.chart)
        self.chart_empty = label("No trades in this period", role="caption")
        right.addWidget(self.chart_empty)
        h.addLayout(right, 1)
        return f

    def _build_moves(self) -> QWidget:
        f, v = _panel(tokens.SPACE[2])
        v.addWidget(label("Today's biggest moves", font_token="body_strong"))
        v.addWidget(_caption("All holdings · % change"))
        self.moves = QGridLayout()
        self.moves.setHorizontalSpacing(tokens.SPACE[3])
        self.moves.setVerticalSpacing(tokens.SPACE[1])
        self.moves.setColumnStretch(1, 1)
        v.addLayout(self.moves)
        v.addStretch(1)
        return f

    def _build_attention(self) -> QWidget:
        f, v = _panel(tokens.SPACE[2])
        v.addWidget(label("Needs attention", font_token="body_strong"))
        v.addWidget(_caption("Coming up · all portfolios"))
        self.attention = QVBoxLayout()
        self.attention.setSpacing(tokens.SPACE[2])
        v.addLayout(self.attention)
        v.addStretch(1)
        return f

    def _build_allocation(self) -> QWidget:
        f, v = _panel(tokens.SPACE[2])
        h = QHBoxLayout()
        h.addWidget(label("Allocation", font_token="body_strong"))
        h.addStretch(1)
        self.alloc_mode = Segmented([("portfolio", "Portfolio"), ("sector", "Sector")])
        self.alloc_mode.changed.connect(lambda _k: self._fill_allocation())
        h.addWidget(self.alloc_mode)
        v.addLayout(h)
        self.alloc_sub = label("", role="caption")
        self.alloc_sub.setWordWrap(True)
        v.addWidget(self.alloc_sub)
        self.alloc_rows = QGridLayout()
        self.alloc_rows.setHorizontalSpacing(tokens.SPACE[2])
        self.alloc_rows.setVerticalSpacing(tokens.SPACE[1])
        self.alloc_rows.setColumnStretch(0, 1)
        v.addLayout(self.alloc_rows)
        v.addStretch(1)
        return f

    # ------------------------------------------------------------------ data
    def _mark_dirty(self, *_a) -> None:
        self._dirty = True
        if self.isVisible():
            self.reload()

    def on_shown(self) -> None:
        if self._dirty:
            self.reload()

    def _portfolios(self) -> list[Portfolio]:
        return self.ctx.repos.portfolios.list()

    def reload(self) -> None:
        self._dirty = False
        portfolios = self._portfolios()
        if not portfolios:
            self.stack.setCurrentWidget(self.empty)
            return
        self.stack.setCurrentIndex(1)
        key = PeriodKey(self.period.value() or PeriodKey.YTD.value)
        plabel = dict(PERIODS)[key]
        period = period_for(key, self.ctx.today())
        self.rows = [registry.get(p.type).dashboard_summary(self.ctx, p, period) for p in portfolios]
        self.total = agg.totals(self.rows)
        self._fill_summary(plabel)
        self.table.set_period_label(plabel)
        self.table.set_rows(self.rows, self.total)
        self._fill_today()
        self._fill_allocation()

    def _fill_summary(self, plabel: str) -> None:
        t = self.total
        n = t.count
        self.total_caption.setText(f"Total value · {n} portfolio{'s' if n != 1 else ''}")
        self.total_value.setText(fmt.money(t.value))
        if t.day_change is None:
            _set_num(self.total_today, "No change today", None)
            set_prop(self.total_today, "role", "muted")
        else:
            set_prop(self.total_today, "role", None)
            _set_num(self.total_today, f"{_arrow(t.day_change)}{fmt.signed_money(t.day_change)} · "
                     f"{fmt.signed_pct(t.day_change_pct, 2)} today", _tone(t.day_change))
        long_label = PERIOD_LABELS.get(PeriodKey(self.period.value()), plabel)
        self.pl_caption.setText(f"P/L · {long_label}")
        self.income_caption.setText(f"Income · {long_label}")
        _set_num(self.total_pl, fmt.signed_money(t.period_pl) if t.period_pl is not None else fmt.DASH,
                 _tone(t.period_pl))
        self.total_pl.setToolTip("Realized + unrealized P/L + income earned in the period")
        _set_num(self.total_income, fmt.money(t.income))
        self.income_sub.setText("Dividends and interest received")
        self.chart_caption.setText(f"Cumulative P/L · {long_label}")
        curves = [r.pl_curve for r in self.rows if r.pl_curve]
        combined = agg.combine_curves(curves)
        self.chart.set_series(combined, curves if len(curves) > 1 else [])
        self.legend.setVisible(len(curves) > 1)
        self.chart.setVisible(bool(combined))
        self.chart_empty.setVisible(not combined)

    def _fill_today(self) -> None:
        p = theme.palette()
        _clear(self.moves)
        moves = agg.top_moves(self.rows)
        multi = len(self.rows) > 1
        if not moves:
            self.moves.addWidget(label("No price moves yet today", role="caption"), 0, 0, 1, 4)
        for i, (h, pf) in enumerate(moves):
            tone = _tone(h.day_pct)
            self.moves.addWidget(_num(h.symbol), i, 0)
            where = label(pf.name if multi else "", role="caption")
            self.moves.addWidget(where, i, 1)
            pct = _num(f"{_arrow(h.day_pct)}{fmt.signed_pct(h.day_pct, 2)}", tone)
            pct.setAlignment(Qt.AlignmentFlag.AlignRight)
            self.moves.addWidget(pct, i, 2)
            amt = _num(fmt.signed_money(h.day_change) if h.day_change is not None else fmt.DASH, tone)
            amt.setAlignment(Qt.AlignmentFlag.AlignRight)
            self.moves.addWidget(amt, i, 3)
        self.moves.setColumnStretch(1, 1)

        _clear(self.attention)
        items = agg.attention(self.rows)
        if not items:
            self.attention.addWidget(label("Nothing needs attention", role="caption"))
        colors = {"negative": p.negative, "warning": p.warning, "info": p.info, "positive": p.positive}
        for item, pf in items:
            row = QHBoxLayout()
            row.setSpacing(tokens.SPACE[2])
            row.addWidget(_icon_label(TONE_ICONS.get(item.tone, "info"), colors.get(item.tone, p.text_muted)),
                          0, Qt.AlignmentFlag.AlignTop)
            col = QVBoxLayout()
            col.setSpacing(0)
            title = label(item.title)
            title.setWordWrap(True)
            col.addWidget(title)
            detail = " · ".join(x for x in (pf.name if multi else "", item.detail) if x)
            if detail:
                d = label(detail, role="caption")
                d.setWordWrap(True)
                col.addWidget(d)
            row.addLayout(col, 1)
            self.attention.addLayout(row)

    def _fill_allocation(self) -> None:
        _clear(self.alloc_rows)
        if not self.rows:
            return
        if self.alloc_mode.value() == "sector":
            items, names = agg.sector_allocation(self.rows)
            self.alloc_sub.setText(f"Holdings in {', '.join(names)}" if names else "No open holdings")
        else:
            items = sorted(((r.portfolio.name, r.value) for r in self.rows if r.value > 0),
                           key=lambda x: x[1], reverse=True)
            self.alloc_sub.setText(f"{fmt.money(self.total.value)} across {self.total.count} "
                                   f"portfolio{'s' if self.total.count != 1 else ''}")
        whole = sum((v for _, v in items), Decimal(0))
        for i, (name, value) in enumerate(items):
            frac = value / whole if whole else Decimal(0)
            r = i * 2
            n = label(name)
            n.setToolTip(name)
            self.alloc_rows.addWidget(n, r, 0)
            val = _num(fmt.money(value, 0))
            set_prop(val, "role", "muted")
            val.setAlignment(Qt.AlignmentFlag.AlignRight)
            self.alloc_rows.addWidget(val, r, 1)
            pct = _num(fmt.pct(frac))
            pct.setAlignment(Qt.AlignmentFlag.AlignRight)
            pct.setMinimumWidth(48)
            self.alloc_rows.addWidget(pct, r, 2)
            self.alloc_rows.addWidget(_Bar(float(frac)), r + 1, 0, 1, 3)
        if not items:
            self.alloc_rows.addWidget(label("Nothing to show", role="caption"), 0, 0)
