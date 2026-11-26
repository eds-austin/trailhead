"""QTreeView subclass painting row states, hairlines, left accent bars and the chevron caret."""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QAbstractItemView, QApplication, QHeaderView, QStyle, QStyleOptionViewItem, QTreeView

from ...ui import theme
from ...ui.theme import tokens
from .delegates import PositionsDelegate
from .table_model import ROW_STATE_ROLE


class PositionsView(QTreeView):
    activated_position = Signal(QModelIndex)  # double-click on a parent row
    activated_child = Signal(QModelIndex)  # double-click on a trade / stop row
    action_clicked = Signal(str, QModelIndex)  # inline Confirm stop / Record sell

    def __init__(self, parent=None, columns=None, buttons=None):
        super().__init__(parent)
        self.setObjectName("positions")
        self._delegate = PositionsDelegate(self, columns=columns, buttons=buttons)
        self.setItemDelegate(self._delegate)
        self._consume_click = False
        self._delegate.action_clicked.connect(self._on_action_clicked)
        self.setUniformRowHeights(True)
        self.setRootIsDecorated(True)
        self.setIndentation(tokens.CHILD_INDENT)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setExpandsOnDoubleClick(False)
        self.setMouseTracking(True)
        self.setSortingEnabled(True)
        self.setAllColumnsShowFocus(True)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        h = self.header()
        h.setStretchLastSection(True)
        h.setSectionsMovable(True)
        h.setHighlightSections(False)
        h.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        h.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        h.setSortIndicatorShown(True)
        h.setFirstSectionMovable(False)
        self._hover_row: QModelIndex = QModelIndex()
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.timeout.connect(self._single_click)
        self._pending_click: QModelIndex | None = None
        self.clicked.connect(self._on_clicked)
        self.doubleClicked.connect(self._on_double)
        self._chev_r = theme.pixmap("chevron-right", theme.palette().text_muted, 14)
        self._chev_d = theme.pixmap("chevron-down", theme.palette().text_muted, 14)

    def set_density(self, density: str) -> None:
        self._delegate.density = density
        self.doItemsLayout()

    def density(self) -> str:
        return self._delegate.density

    # --------------------------------------------------------------- clicks
    def _on_action_clicked(self, key: str, index: QModelIndex) -> None:
        self._consume_click = True
        self.action_clicked.emit(key, index)

    def _on_clicked(self, index: QModelIndex) -> None:
        if self._consume_click:
            self._consume_click = False
            return
        if index.parent().isValid():
            return
        self._pending_click = index.siblingAtColumn(0)
        self._click_timer.start(QApplication.doubleClickInterval())

    def _single_click(self) -> None:
        idx = self._pending_click
        self._pending_click = None
        if idx is not None and idx.isValid():
            self.setExpanded(idx, not self.isExpanded(idx))

    def _on_double(self, index: QModelIndex) -> None:
        self._click_timer.stop()
        self._pending_click = None
        if index.parent().isValid():
            self.activated_child.emit(index)
        else:
            self.activated_position.emit(index)  # keeps the column (Sector double-click edits the sector)

    def keyPressEvent(self, e) -> None:  # noqa: N802
        idx = self.currentIndex()
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space) and idx.isValid():
            top = idx.siblingAtColumn(0)
            if not top.parent().isValid():
                self.setExpanded(top, not self.isExpanded(top))
                return
            self.activated_child.emit(idx)
            return
        super().keyPressEvent(e)

    # --------------------------------------------------------------- hover
    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        idx = self.indexAt(e.position().toPoint())
        row0 = idx.siblingAtColumn(0) if idx.isValid() else QModelIndex()
        if row0 != self._hover_row:
            self._hover_row = row0
            self._delegate.hover_row = QPersistentModelIndex(row0)
            self.viewport().update()
        super().mouseMoveEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self._hover_row = QModelIndex()
        self._delegate.hover_row = QPersistentModelIndex()
        self.viewport().update()
        super().leaveEvent(e)

    # --------------------------------------------------------------- painting
    def drawRow(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:  # noqa: N802
        p = theme.palette()
        rect = QRect(0, option.rect.top(), self.viewport().width(), option.rect.height())
        state = index.data(ROW_STATE_ROLE)
        is_child = index.parent().isValid()
        base = p.bg if is_child else p.surface
        if state == "stop_hit":
            base = tokens.row_tint(p, "negative")
        painter.fillRect(rect, QColor(base))
        selected = self.selectionModel() is not None and self.selectionModel().isRowSelected(
            index.row(), index.parent())
        if selected:
            painter.fillRect(rect, theme.qcolor(p.accent, tokens.SELECTED_ALPHA))
        elif index.siblingAtColumn(0) == self._hover_row:
            painter.fillRect(rect, theme.qcolor(p.accent, tokens.HOVER_ALPHA))
        painter.fillRect(QRect(rect.left(), rect.bottom(), rect.width(), 1), QColor(p.border))
        bar = None
        if selected:
            bar = p.accent
        elif state == "stop_hit":
            bar = p.negative
        elif state == "action":
            bar = p.warning
        if bar:
            painter.fillRect(QRect(0, rect.top(), 2, rect.height()), QColor(bar))
        opt = QStyleOptionViewItem(option)
        opt.state &= ~QStyle.StateFlag.State_Selected
        opt.state &= ~QStyle.StateFlag.State_MouseOver
        if state == "closed":
            painter.setOpacity(0.72)
        super().drawRow(painter, opt, index)
        painter.setOpacity(1.0)

    def drawBranches(self, painter: QPainter, rect: QRect, index: QModelIndex) -> None:  # noqa: N802
        if index.parent().isValid() or not self.model() or self.model().rowCount(index) == 0:
            return
        pm = self._chev_d if self.isExpanded(index) else self._chev_r
        x = rect.right() - tokens.CHILD_INDENT + (tokens.CHILD_INDENT - 14) // 2 + 2
        y = rect.top() + (rect.height() - 14) // 2
        painter.drawPixmap(QPoint(x, y), pm)

    # --------------------------------------------------------------- spans/expansion
    def expanded_keys(self) -> set[str]:
        m = self.model()
        out = set()
        for r in range(m.rowCount()):
            idx = m.index(r, 0)
            if self.isExpanded(idx):
                out.add(m.position_keys()[r])
        return out

    def restore_expanded(self, keys: set[str]) -> None:
        m = self.model()
        for r, key in enumerate(m.position_keys()):
            if key in keys:
                self.setExpanded(m.index(r, 0), True)
