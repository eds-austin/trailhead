"""Cell painting for the positions table: pills, badges, signed numbers and child trade lines."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QModelIndex, QPersistentModelIndex, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from ...ui import theme
from ...ui.theme import tokens
from ...ui.widgets.badge import badge_width, paint_badge
from ...ui.widgets.pill import paint_pill
from .table_model import CELL_ROLE, COLUMNS, Cell, action_buttons

BTN_H = 22
BTN_PAD = 8
BTN_GAP = 6

CELL_PAD = 8


def tone_color(tone: str | None) -> QColor:
    p = theme.palette()
    return QColor({
        "positive": p.positive, "negative": p.negative, "warning": p.warning, "faint": p.text_faint,
        "muted": p.text_muted, "muted_italic": p.text_muted, "stop": p.stop, "target": p.target, "info": p.info,
    }.get(tone or "", p.text))


class PositionsDelegate(QStyledItemDelegate):
    action_clicked = Signal(str, QModelIndex)  # "confirm" | "sell", index

    def __init__(self, parent=None, density: str = "compact", columns=None, buttons=None):
        super().__init__(parent)
        self.density = density
        self.columns = columns if columns is not None else COLUMNS
        self.buttons = buttons if buttons is not None else action_buttons
        self.hover_row = QPersistentModelIndex()
        self._num = theme.font("num")
        self._body = theme.font("body")
        self._strong = theme.font("body_strong")
        self._caption = theme.font("caption")

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        col = self.columns[index.column()] if index.column() < len(self.columns) else None
        return QSize(col.width if col else 80, tokens.ROW_H[self.density])

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        cell: Cell | None = index.data(CELL_ROLE)
        if cell is None:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(option.rect).adjusted(CELL_PAD, 0, -CELL_PAD, 0)
        align = index.data(Qt.ItemDataRole.TextAlignmentRole) or (Qt.AlignmentFlag.AlignLeft |
                                                                   Qt.AlignmentFlag.AlignVCenter)
        k = cell.kind
        if k == "symbol":
            painter.setFont(self._strong)
            painter.setPen(tone_color(None))
            painter.drawText(rect, int(align), cell.text)
        elif k == "status":
            paint_badge(painter, rect, cell.text, cell.badge or "open")
        elif k == "pill":
            paint_pill(painter, rect, cell.data, cell.text, cell.sub, cell.state, align_right=True)
        elif k == "action":
            if cell.text and self._is_hovered(index):
                for _key, text, r in self._button_rects(rect, cell):
                    self._paint_button(painter, r, text, _key)
            elif cell.text.strip():  # a blank cell still offers its hover buttons
                paint_badge(painter, rect, cell.text, cell.badge or "action")
        elif k == "earnings":
            paint_badge(painter, rect, cell.text, "earnings")
        elif k == "last":
            painter.setFont(self._num)
            painter.setPen(tone_color("faint" if cell.text == "—" else None))
            text_rect = rect
            if cell.badge:
                bw = badge_width("M")
                paint_badge(painter, QRectF(rect.left(), rect.top(), bw, rect.height()), "M", "manual")
                text_rect = rect.adjusted(bw + 4, 0, 0, 0)
            painter.drawText(text_rect, int(align), cell.text)
        else:
            f = self._num if k in ("num", "signed") else self._strong if k == "side" else self._body
            if cell.italic:
                f = theme.font("num" if k in ("num", "signed") else "body_strong" if k == "side" else "body",
                               italic=True)
            painter.setFont(f)
            tone = cell.tone
            if k == "muted":
                tone = tone or "muted"
            if cell.text == "—":
                tone = "faint"
            painter.setPen(tone_color(tone))
            fm = QFontMetrics(painter.font())
            painter.drawText(rect, int(align), fm.elidedText(cell.text, Qt.TextElideMode.ElideRight, int(rect.width())))
        painter.restore()

    def _is_hovered(self, index: QModelIndex) -> bool:
        return (self.hover_row.isValid() and not index.parent().isValid()
                and index.row() == self.hover_row.row() and not self.hover_row.parent().isValid())

    def _button_rects(self, rect: QRectF, cell: Cell) -> list[tuple[str, str, QRectF]]:
        fm = QFontMetrics(theme.font("caption", weight=600))
        x = rect.left()
        out = []
        for key, text in self.buttons(cell.data):
            w = fm.horizontalAdvance(text) + BTN_PAD * 2
            out.append((key, text, QRectF(x, rect.center().y() - BTN_H / 2, w, BTN_H)))
            x += w + BTN_GAP
        return out

    def _paint_button(self, painter: QPainter, r: QRectF, text: str, key: str) -> None:
        p = theme.palette()
        fill = {"confirm": p.stop, "dividend": p.info, "stopout": p.negative}.get(key)
        primary = fill is not None
        painter.setPen(Qt.PenStyle.NoPen if primary else QColor(p.border_strong))
        painter.setBrush(QColor(fill or p.surface_3))
        painter.drawRoundedRect(r, tokens.RADIUS["md"], tokens.RADIUS["md"])
        painter.setPen(QColor(p.text_on_accent if primary else p.text))
        painter.setFont(theme.font("caption", weight=600))
        painter.drawText(r, Qt.AlignmentFlag.AlignCenter, text)

    def editorEvent(self, event, model, option, index) -> bool:  # noqa: N802
        if (event.type() == QEvent.Type.MouseButtonRelease and index.isValid() and not index.parent().isValid()
                and self.columns[index.column()].key == "action"):
            cell: Cell | None = index.data(CELL_ROLE)
            if cell and cell.text:
                rect = QRectF(option.rect).adjusted(CELL_PAD, 0, -CELL_PAD, 0)
                pos = event.position()
                for key, _text, r in self._button_rects(rect, cell):
                    if r.contains(pos):
                        self.action_clicked.emit(key, index)
                        return True
        return super().editorEvent(event, model, option, index)

    def initStyleOption(self, option: QStyleOptionViewItem, index: QModelIndex) -> None:  # noqa: N802
        super().initStyleOption(option, index)
        option.state &= ~QStyle.StateFlag.State_HasFocus
