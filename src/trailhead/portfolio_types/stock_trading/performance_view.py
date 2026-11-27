"""Performance table: closed-trade stats per strategy, time frame or exit plan (or strategy with its time frames
nested under it), with an R strip per group: every trade's R, plus the expectancy and its ≈95% range."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

from PySide6.QtCore import QModelIndex, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from ...core.performance import LOW_SAMPLE, NONE_LABEL, RANGE_SE, GroupRow
from ...core.stats import SQN_MIN_TRADES, sqn_rating
from ...ui import format as fmt
from ...ui import theme
from ...ui.theme import tokens
from ...ui.widgets.badge import badge_width, paint_badge

ROW_ROLE = Qt.ItemDataRole.UserRole
ROW_H = 48
RANGE_W = 280
INDENT = tokens.SPACE[5]  # nested (inner) rows
AXIS_CLAMP = (-4.0, 10.0)  # one wild trade mustn't flatten the rest; it's drawn at the edge
PAD = tokens.SPACE[2]


def _r(v: Decimal | None) -> str:
    return fmt.r_mult(v)


def _tone(v: Decimal | None) -> str | None:
    s = fmt.sign_of(v)
    return "positive" if s > 0 else "negative" if s < 0 else None


# (title, text, sub-line, tone, sort key). Text/sub take a GroupRow; sort key returns a comparable or None.
Col = tuple[str, Callable[[GroupRow], str], Callable[[GroupRow], str] | None, Callable[[GroupRow], str | None] | None,
            Callable[[GroupRow], object]]


def _dash_if_empty(f: Callable[[GroupRow], str]) -> Callable[[GroupRow], str]:
    return lambda g: f(g) if g.n else fmt.DASH


COLUMNS: list[Col] = [
    ("Group", lambda g: g.label, None, None, lambda g: g.label.lower()),
    ("Closed", lambda g: str(g.n), None, None, lambda g: g.n),
    ("Open", lambda g: str(g.open_count), None, None, lambda g: g.open_count),
    ("Win %", _dash_if_empty(lambda g: fmt.pct(g.stats.win_pct)),
     lambda g: f"{g.stats.wins}W – {g.stats.losses}L" if g.n else "", None, lambda g: g.stats.win_pct),
    ("Avg win", lambda g: _r(g.avg_win_r), None, lambda g: "positive" if g.avg_win_r else None,
     lambda g: g.avg_win_r),
    ("Avg loss", lambda g: _r(g.avg_loss_r), None, lambda g: "negative" if g.avg_loss_r else None,
     lambda g: g.avg_loss_r),
    ("Profit factor", _dash_if_empty(lambda g: fmt.ratio(g.stats.profit_factor)), None, None,
     lambda g: g.stats.profit_factor),
    ("Expectancy", lambda g: _r(g.stats.expectancy),
     lambda g: f"± {fmt.number(g.half_range)}" if g.half_range is not None else "",
     lambda g: _tone(g.stats.expectancy), lambda g: g.stats.expectancy),
    ("R range", lambda g: "" if g.n else "No closed trades yet", None, None, lambda g: g.stats.expectancy),
    ("StDev", lambda g: fmt.ratio(g.stats.stdev), None, None, lambda g: g.stats.stdev),
    ("SQN", lambda g: fmt.ratio(g.stats.sqn), lambda g: sqn_rating(g.stats.sqn) or "",
     lambda g: "negative" if g.stats.sqn is not None and g.stats.sqn < 0 else None, lambda g: g.stats.sqn),
    ("P/L", _dash_if_empty(lambda g: fmt.signed_money(g.stats.total_pl)), None,
     lambda g: _tone(g.stats.total_pl) if g.n else None, lambda g: g.stats.total_pl if g.n else None),
    ("Avg days", lambda g: fmt.number(g.avg_days, 0), None, None, lambda g: g.avg_days),
]
C_NAME, C_RANGE, C_SQN = 0, 8, 10
TOOLTIPS = {
    1: "Closed trades in the period", 2: "Still open (any period)",
    4: "Average R of winning trades", 5: "Average R of losing trades",
    6: "Gross profit ÷ gross loss ($)", 7: "Average R per closed trade",
    8: "Top: each dot is one closed trade at its R (green win, red loss), so you see the spread of results.\n"
       f"Bottom: the diamond is the expectancy (average R); the capped line is ± {RANGE_SE} standard errors, "
       "the range the true expectancy\nprobably (≈95%) lies in. If it crosses 0 there's no evidence of an edge "
       "yet; groups whose ranges overlap a lot can't be told apart yet.",
    10: "System Quality Number (Van Tharp): √N × expectancy ÷ StDev, N capped at 100.\n"
        "Under 1.6 poor · 1.6 below average · 2 average · 2.5 good · 3 excellent · 5 superb · 7+ holy grail.\n"
        f"Needs about {SQN_MIN_TRADES} trades to mean much. Above 2, the ≈95% expectancy bar clears 0.",
    11: "Realized P/L of the closed trades (partial sells of still-open lots are not included)",
    12: "Average days from entry to close",
}


class _Delegate(QStyledItemDelegate):
    def __init__(self, table: PerformanceTable):
        super().__init__(table)
        self.table = table
        self.hover_row = -1

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        g: GroupRow | None = index.data(ROW_ROLE)
        col = index.column()
        if col == C_RANGE:
            return QSize(RANGE_W + 2 * PAD, ROW_H)
        if g is None:
            return QSize(60, ROW_H)
        if col == C_NAME:
            w = QFontMetrics(theme.font(_name_font(g))).horizontalAdvance(g.label) + (INDENT if g.parent else 0)
            if g.low_sample:
                w += tokens.SPACE[2] + badge_width("Low sample")
            return QSize(w + 2 * PAD + tokens.SPACE[3], ROW_H)
        fm = QFontMetrics(theme.font("num", weight=600))
        _t, text, sub, _tone_f, _k = COLUMNS[col]
        w = fm.horizontalAdvance(text(g))
        if sub is not None:
            w = max(w, QFontMetrics(theme.font("caption")).horizontalAdvance(sub(g)))
        return QSize(w + 2 * PAD + 2, ROW_H)

    def paint(self, qp: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        g: GroupRow | None = index.data(ROW_ROLE)
        if g is None:
            return
        p = theme.palette()
        r = QRectF(option.rect)
        qp.save()
        qp.setRenderHint(QPainter.RenderHint.Antialiasing)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        if selected:
            qp.fillRect(r, theme.qcolor(p.accent, 0.14))
        elif g.is_total:
            qp.fillRect(r, QColor(p.surface_2))
        elif index.row() == self.hover_row:
            qp.fillRect(r, theme.qcolor(p.accent, 0.06))
        if selected and index.column() == 0:
            qp.fillRect(QRectF(r.left(), r.top(), 2, r.height()), QColor(p.accent))
        if not g.is_total:
            qp.setPen(QPen(QColor(p.border), 1))
            qp.drawLine(QPointF(r.left(), r.bottom()), QPointF(r.right(), r.bottom()))
        inner = r.adjusted(PAD + (tokens.SPACE[2] if index.column() == 0 else 0), 0, -PAD, 0)
        col = index.column()
        if col == C_NAME:
            self._paint_name(qp, inner, g)
        elif col == C_RANGE:
            self._paint_range(qp, inner, g)
        else:
            self._paint_number(qp, inner, g, col)
        qp.restore()

    def _paint_name(self, qp: QPainter, r: QRectF, g: GroupRow) -> None:
        p = theme.palette()
        if g.parent:
            r = r.adjusted(INDENT, 0, 0, 0)
        font = _name_font(g)
        qp.setFont(theme.font(font))
        qp.setPen(QColor(p.text_muted if g.label == NONE_LABEL else p.text))
        qp.drawText(r, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, g.label)
        if g.low_sample:
            x = r.left() + QFontMetrics(theme.font(font)).horizontalAdvance(g.label) + tokens.SPACE[2]
            paint_badge(qp, QRectF(x, r.top(), r.right() - x, r.height()), "Low sample", "action")

    def _paint_number(self, qp: QPainter, r: QRectF, g: GroupRow, col: int) -> None:
        p = theme.palette()
        _t, text, sub, tone_f, _k = COLUMNS[col]
        tone = tone_f(g) if tone_f is not None else None
        value = text(g)
        color = {"positive": p.positive, "negative": p.negative}.get(tone or "", p.text)
        if value == fmt.DASH or col == 2 or (col == C_SQN and g.n < SQN_MIN_TRADES and color == p.text):
            color = p.text_muted
        qp.setPen(QColor(color))
        qp.setFont(theme.font("num", weight=600 if g.is_total else None))
        s = sub(g) if sub is not None else ""
        right = Qt.AlignmentFlag.AlignRight
        if s:
            qp.drawText(QRectF(r.left(), r.top() + 6, r.width(), 20), right | Qt.AlignmentFlag.AlignVCenter, value)
            qp.setPen(QColor(p.text_muted))
            qp.setFont(theme.font("caption"))
            qp.drawText(QRectF(r.left(), r.top() + 25, r.width(), 16), right | Qt.AlignmentFlag.AlignVCenter, s)
        else:
            qp.drawText(r, right | Qt.AlignmentFlag.AlignVCenter, value)

    def _paint_range(self, qp: QPainter, r: QRectF, g: GroupRow) -> None:
        p = theme.palette()
        box = QRectF(r.center().x() - RANGE_W / 2, r.top(), RANGE_W, r.height())  # centred under the header
        if not g.n:
            qp.setPen(QColor(p.text_muted))
            qp.setFont(theme.font("caption"))
            qp.drawText(box, Qt.AlignmentFlag.AlignCenter, "No closed trades yet")
            return
        lo, hi = self.table.axis

        def x(v: float) -> float:
            return box.left() + box.width() * (min(max(v, lo), hi) - lo) / (hi - lo)

        # two lanes: the trades on top, the expectancy and its range below; the total row leaves room for labels
        top = box.top() + (7 if g.is_total else 10)
        trades_y, exp_y = top + 5, top + 19
        for t in self.table.ticks:  # faint whole-R grid, stronger at 0
            qp.setPen(QPen(QColor(p.border_strong if t == 0 else p.border), 1))
            qp.drawLine(QPointF(x(t), top - 2), QPointF(x(t), exp_y + 5))
        for v in g.rs:
            paint_trade_dot(qp, x(float(v)), trades_y, v)
        rng, e = g.range, g.stats.expectancy
        if e is not None:
            x0, x1 = (x(float(rng[0])), x(float(rng[1]))) if rng is not None else (None, None)
            paint_error_bar(qp, x(float(e)), exp_y, x0, x1, p.text if g.is_total else p.accent)
        if g.is_total:  # axis labels under the last row
            qp.setPen(QColor(p.text_muted))
            qp.setFont(theme.font("caption"))
            for t in self.table.ticks:
                label = "0" if t == 0 else f"{t:+g}R"
                qp.drawText(QRectF(x(t) - 20, exp_y + 6, 40, 14), Qt.AlignmentFlag.AlignCenter, label)


def paint_trade_dot(qp: QPainter, x: float, y: float, r: Decimal | float) -> None:
    """One closed trade: a dot at its R, green for a win, red for a loss."""
    p = theme.palette()
    c = p.positive if r > 0 else p.negative if r < 0 else p.text_muted
    qp.setPen(Qt.PenStyle.NoPen)
    qp.setBrush(theme.qcolor(c, 0.7))
    qp.drawEllipse(QPointF(x, y), 3, 3)


def paint_error_bar(qp: QPainter, xe: float, y: float, x0: float | None, x1: float | None, color: str) -> None:
    """Expectancy as a diamond, its ≈95% range as a capped line (|——◆——|)."""
    qp.setBrush(Qt.BrushStyle.NoBrush)
    if x0 is not None and x1 is not None:
        qp.setPen(QPen(QColor(color), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
        qp.drawLine(QPointF(x0, y), QPointF(x1, y))
        for xc in (x0, x1):
            qp.drawLine(QPointF(xc, y - 4), QPointF(xc, y + 4))
    qp.setPen(Qt.PenStyle.NoPen)
    qp.setBrush(QColor(color))
    qp.drawPolygon(QPolygonF([QPointF(xe, y - 5), QPointF(xe + 5, y), QPointF(xe, y + 5), QPointF(xe - 5, y)]))


class RangeLegend(QWidget):
    """Key for the R range column, shown in the toolbar while the Performance table is."""

    ITEMS = [("trades", "One closed trade's R"), ("bar", "Expectancy and its ≈95% range")]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setToolTip(TOOLTIPS[C_RANGE])
        fm = QFontMetrics(theme.font("caption"))
        self._glyph_w = {"trades": 22, "bar": 30}
        w = sum(self._glyph_w[k] + tokens.SPACE[2] + fm.horizontalAdvance(t) for k, t in self.ITEMS)
        self.setFixedSize(int(w + tokens.SPACE[4] * (len(self.ITEMS) - 1)) + 2, 20)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = theme.palette()
        qp = QPainter(self)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing)
        qp.setFont(theme.font("caption"))
        fm = QFontMetrics(qp.font())
        x, y = 1.0, self.height() / 2
        for key, text in self.ITEMS:
            gw = self._glyph_w[key]
            if key == "trades":
                paint_trade_dot(qp, x + 4, y, 1)
                paint_trade_dot(qp, x + 12, y, -1)
            else:
                paint_error_bar(qp, x + gw / 2, y, x + 1, x + gw - 1, p.accent)
            x += gw + tokens.SPACE[2]
            qp.setPen(QColor(p.text_muted))
            qp.drawText(QRectF(x, 0, fm.horizontalAdvance(text) + 2, self.height()),
                        Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
            x += fm.horizontalAdvance(text) + tokens.SPACE[4]
        qp.end()


def _name_font(g: GroupRow) -> str:
    return "body" if g.parent else "body_strong"


def _range_tip(g: GroupRow) -> str:
    lines = []
    if g.r_spread is not None:
        worst, median, best = g.r_spread
        lines.append(f"{len(g.rs)} trades · worst {_r(worst)} · median {_r(median)} · best {_r(best)}")
    if g.range is not None:
        lines.append(f"Expectancy {_r(g.stats.expectancy)}, ≈95% range {_r(g.range[0])} to {_r(g.range[1])}")
    return "\n".join(lines)


class PerformanceTable(QTableWidget):
    """Group rows + total row. Click a header to sort (total stays last); selecting a row emits it."""

    group_selected = Signal(object)  # GroupRow | None

    def __init__(self, parent=None):
        super().__init__(0, len(COLUMNS), parent)
        self.setObjectName("plain")
        self.rows: list[GroupRow] = []
        self.axis: tuple[float, float] = (-1.0, 2.0)
        self.ticks: list[int] = [0, 1, 2]
        self.sort_col, self.sort_desc = 1, True
        self.group_title = "Strategy"
        self.delegate = _Delegate(self)
        self.setItemDelegate(self.delegate)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(ROW_H)
        self.setShowGrid(False)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setMouseTracking(True)
        h = self.horizontalHeader()
        h.setHighlightSections(False)
        h.setSectionsClickable(True)
        h.setSortIndicatorShown(False)  # Qt reserves arrow room on every section; we mark the sorted one in text
        h.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        h.setStretchLastSection(True)  # spare width goes to the last column (right-aligned numbers)
        h.setMinimumSectionSize(64)
        h.sectionClicked.connect(self._on_header)
        self.entered.connect(self._on_entered)
        self.itemSelectionChanged.connect(self._emit_selection)
        self.set_group_title("Strategy")

    # ------------------------------------------------------------------ api
    def set_group_title(self, title: str) -> None:
        self.group_title = title
        labels = [c[0] for c in COLUMNS]
        labels[C_NAME] = title
        labels[self.sort_col] += " ▾" if self.sort_desc else " ▴"
        self.setHorizontalHeaderLabels(labels)
        for c in range(len(COLUMNS)):
            it = self.horizontalHeaderItem(c)
            h_align = (Qt.AlignmentFlag.AlignLeft if c == C_NAME else
                       Qt.AlignmentFlag.AlignHCenter if c == C_RANGE else Qt.AlignmentFlag.AlignRight)
            it.setTextAlignment(h_align | Qt.AlignmentFlag.AlignVCenter)
            it.setToolTip(TOOLTIPS.get(c, ""))
        self.horizontalHeaderItem(C_NAME).setToolTip(
            f"Grouped by each BUY's {title.lower()}. LOW SAMPLE = fewer than {LOW_SAMPLE} closed trades.")

    def set_rows(self, rows: list[GroupRow], keep: str | None = None) -> None:
        self.rows = list(rows)
        self._set_axis()
        self._fill(keep)

    def selected_label(self) -> str | None:
        g = self.selected_row()
        return g.key if g is not None else None

    def selected_row(self) -> GroupRow | None:
        items = self.selectedItems()
        return items[0].data(ROW_ROLE) if items else None

    def export_rows(self) -> tuple[list[str], list[list[str]]]:
        headers = [c[0] for c in COLUMNS]
        headers[C_NAME] = self.group_title
        out = []
        for r in range(self.rowCount()):
            g: GroupRow = self.item(r, 0).data(ROW_ROLE)
            row = []
            for c, (_t, text, _sub, _tone, _k) in enumerate(COLUMNS):
                if c == C_RANGE:
                    rng = g.range
                    row.append(f"{_r(rng[0])} to {_r(rng[1])}" if rng else "")
                elif c == 7 and g.half_range is not None:
                    row.append(f"{text(g)} ± {fmt.number(g.half_range)}")
                elif c == C_NAME:
                    row.append(g.key + (" LOW SAMPLE" if g.low_sample else ""))
                elif c == C_SQN and g.stats.sqn is not None:
                    row.append(f"{text(g)} {sqn_rating(g.stats.sqn)}")
                else:
                    row.append(text(g))
            out.append(row)
        return headers, out

    # ------------------------------------------------------------------ internals
    def _set_axis(self) -> None:
        vals = [-1.0, 1.0]
        for g in self.rows:
            vals += [float(v) for v in g.rs]
            if g.range is not None:
                vals += [float(g.range[0]), float(g.range[1])]
        lo, hi = max(min(vals), AXIS_CLAMP[0]), min(max(vals), AXIS_CLAMP[1])
        span = hi - lo
        self.axis = (lo - span * 0.04, hi + span * 0.04)
        step = 1 if span <= 8 else 2
        self.ticks = [t for t in range(int(lo) - 1, int(hi) + 2) if t % step == 0 and lo <= t <= hi]

    def _sorted(self) -> list[GroupRow]:
        """Sort each level on its own: nested rows stay under their outer row, the total stays last."""
        totals = [g for g in self.rows if g.is_total]
        children: dict[str, list[GroupRow]] = {}
        for g in self.rows:
            if g.parent:
                children.setdefault(g.parent, []).append(g)

        def walk(groups: list[GroupRow]) -> list[GroupRow]:
            out = []
            for g in self._sort_level(groups):
                out.append(g)
                out += walk(children.get(g.key, []))
            return out

        return walk([g for g in self.rows if not g.is_total and not g.parent]) + totals

    def _sort_level(self, groups: list[GroupRow]) -> list[GroupRow]:
        key = COLUMNS[self.sort_col][4]
        known = [g for g in groups if key(g) is not None]
        unknown = [g for g in groups if key(g) is None]
        known.sort(key=key, reverse=self.sort_desc)
        if self.sort_col == 1:  # default: most closed first, "(none)" last
            known.sort(key=lambda g: (g.label == NONE_LABEL, -g.n if self.sort_desc else g.n, -g.open_count))
        return known + unknown

    def _fill(self, keep: str | None) -> None:
        self.blockSignals(True)
        rows = self._sorted()
        self.setRowCount(len(rows))
        select = -1
        for i, g in enumerate(rows):
            for c in range(len(COLUMNS)):
                it = QTableWidgetItem(COLUMNS[c][1](g))
                it.setData(ROW_ROLE, g)
                self.setItem(i, c, it)
            self.item(i, C_RANGE).setToolTip(_range_tip(g))
            if g.stats.sqn is not None and g.n < SQN_MIN_TRADES:
                self.item(i, C_SQN).setToolTip(f"Only {g.n} closed trades: SQN needs about {SQN_MIN_TRADES} "
                                               "to mean much")
            if keep is not None and g.key == keep:
                select = i
        self.set_group_title(self.group_title)  # moves the ▾ / ▴ marker to the sorted column
        if select >= 0:
            self.selectRow(select)
        else:
            self.clearSelection()
        self.blockSignals(False)
        self.resizeColumnsToContents()
        self.setMinimumHeight(self.horizontalHeader().height() + ROW_H * min(len(rows), 4) + 2)
        self._emit_selection()

    def _on_header(self, col: int) -> None:
        if col == self.sort_col:
            self.sort_desc = not self.sort_desc
        else:
            self.sort_col, self.sort_desc = col, col != C_NAME
        self._fill(self.selected_label())

    def _on_entered(self, index: QModelIndex) -> None:
        if self.delegate.hover_row != index.row():
            self.delegate.hover_row = index.row()
            self.viewport().update()

    def leaveEvent(self, e) -> None:  # noqa: N802
        self.delegate.hover_row = -1
        self.viewport().update()
        super().leaveEvent(e)

    def _emit_selection(self) -> None:
        self.group_selected.emit(self.selected_row())
