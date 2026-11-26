"""A wrapping flow layout (adapted from the Qt FlowLayout example)."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QLayoutItem, QWidget


class FlowLayout(QLayout):
    """Lays items left-to-right and wraps; items on each line stretch to fill the width."""

    def __init__(self, parent: QWidget | None = None, spacing: int = 8, stretch: bool = True,
                 balanced: bool = False):
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._spacing = spacing
        self._stretch = stretch
        self._balanced = balanced
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._do_layout(QRect(0, 0, width, 0), test=True)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._do_layout(rect, test=False)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _visible(self) -> list[QLayoutItem]:
        return [i for i in self._items if i.widget() is None or not i.widget().isHidden()]

    def _do_layout(self, rect: QRect, test: bool) -> int:
        m = self.contentsMargins()
        eff = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        sp = self._spacing
        if self._balanced:
            return self._do_balanced(rect, eff, test)
        lines: list[list[QLayoutItem]] = [[]]
        x = 0
        for item in self._visible():
            w = item.sizeHint().width()
            if lines[-1] and x + w > eff.width():
                lines.append([])
                x = 0
            lines[-1].append(item)
            x += w + sp
        y = eff.y()
        for line in lines:
            if not line:
                continue
            line_h = max(i.sizeHint().height() for i in line)
            natural = sum(i.sizeHint().width() for i in line) + sp * (len(line) - 1)
            extra = max(0, eff.width() - natural) if self._stretch else 0
            per = extra // len(line) if line else 0
            xx = eff.x()
            for idx, item in enumerate(line):
                w = item.sizeHint().width() + per
                if self._stretch and idx == len(line) - 1:
                    w = eff.right() + 1 - xx
                if not test:
                    item.setGeometry(QRect(QPoint(xx, y), QSize(w, line_h)))
                xx += w + sp
            y += line_h + sp
        return y - sp - rect.y() + m.bottom()

    def _do_balanced(self, rect: QRect, eff: QRect, test: bool) -> int:
        """Equal-width columns; rows balanced so wrapped lines hold a similar number of items."""
        items = self._visible()
        if not items:
            return 0
        sp = self._spacing
        min_w = max(i.minimumSize().width() for i in items)
        fit = max(1, (eff.width() + sp) // (min_w + sp))
        rows = -(-len(items) // fit)
        cols = -(-len(items) // rows)
        col_w = (eff.width() - sp * (cols - 1)) / cols
        line_h = max(i.sizeHint().height() for i in items)
        for idx, item in enumerate(items):
            r, c = divmod(idx, cols)
            if not test:
                x = eff.x() + round(c * (col_w + sp))
                w = round((c + 1) * (col_w + sp) - sp) - round(c * (col_w + sp))
                item.setGeometry(QRect(x, eff.y() + r * (line_h + sp), w, line_h))
        return rows * line_h + (rows - 1) * sp + self.contentsMargins().top() + self.contentsMargins().bottom()
