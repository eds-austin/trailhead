"""Themed dialog base: title, body, right-aligned footer; Enter submits, Esc cancels."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from ..theme import tokens
from ..widgets.common import button, set_prop


class AppDialog(QDialog):
    def __init__(self, title: str, parent: QWidget | None = None, width: int = tokens.DIALOG_W,
                 primary_text: str = "Save"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(width)
        outer = QVBoxLayout(self)
        pad = tokens.SPACE[5]
        outer.setContentsMargins(pad, pad, pad, pad)
        outer.setSpacing(tokens.SPACE[3])
        self.title_label = QLabel(title)
        self.title_label.setFont(theme.font("title"))
        outer.addWidget(self.title_label)
        self.body = QVBoxLayout()
        self.body.setSpacing(tokens.SPACE[3])
        outer.addLayout(self.body, 1)
        self.error_label = QLabel("")
        self.error_label.setProperty("role", "error")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        outer.addWidget(self.error_label)
        foot = QHBoxLayout()
        foot.setSpacing(tokens.SPACE[2])
        self.footer_left = QHBoxLayout()
        foot.addLayout(self.footer_left)
        foot.addStretch(1)
        self.cancel_btn = button("Cancel")
        self.cancel_btn.setAutoDefault(False)
        self.cancel_btn.clicked.connect(self.reject)
        self.primary_btn = button(primary_text, "primary")
        self.primary_btn.setDefault(True)
        self.primary_btn.setAutoDefault(True)
        self.primary_btn.clicked.connect(self._on_primary)
        for b in (self.cancel_btn, self.primary_btn):
            b.setMinimumWidth(tokens.DIALOG_BUTTON_MIN_W)
        foot.addWidget(self.cancel_btn)
        foot.addWidget(self.primary_btn)
        outer.addLayout(foot)

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        QTimer.singleShot(0, self._drop_button_focus)

    def _drop_button_focus(self) -> None:
        """A dialog with no fields opens with focus on its default button, so the focus ring shows before any
        key is pressed. Drop it: Enter still submits (keyPressEvent), and Tab brings the ring back."""
        if self.focusWidget() in (self.primary_btn, self.cancel_btn):
            self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)  # somewhere for focus to stay on activation
            self.setFocus(Qt.FocusReason.OtherFocusReason)

    def _on_primary(self) -> None:
        self.error_label.hide()
        if self.submit():
            self.accept()

    def submit(self) -> bool:
        """Validate and persist; return True to close."""
        return True

    def show_error(self, msg: str) -> None:
        self.error_label.setText(msg)
        self.error_label.setVisible(bool(msg))

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
                e.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            fw = self.focusWidget()
            if fw is not None and fw.inherits("QPlainTextEdit"):
                return super().keyPressEvent(e)
            if self.primary_btn.isEnabled():
                self._on_primary()
            return
        super().keyPressEvent(e)


class FieldGrid(QGridLayout):
    """Label + field + inline error rows, in up to two column pairs."""

    def __init__(self):
        super().__init__()
        self.setHorizontalSpacing(tokens.SPACE[3])
        self.setVerticalSpacing(2)
        self._errors: dict[str, tuple[QWidget, QLabel]] = {}
        self._row = 0

    def add(self, key: str, label: str, field: QWidget, col: int = 0, extra: QWidget | None = None,
            span: int = 1) -> QLabel:
        lb = QLabel(label)
        lb.setProperty("role", "field-label")
        lb.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        c = col * 2
        r = self._row * 2
        self.addWidget(lb, r, c)
        if extra is not None:
            box = QWidget()
            h = QHBoxLayout(box)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(tokens.SPACE[2])
            h.addWidget(field, 1)
            h.addWidget(extra)
            self.addWidget(box, r, c + 1, 1, span * 2 - 1)
        else:
            self.addWidget(field, r, c + 1, 1, span * 2 - 1)
        err = QLabel("")
        err.setProperty("role", "error")
        err.hide()
        self.addWidget(err, r + 1, c + 1, 1, span * 2 - 1)
        self._errors[key] = (field, err)
        return lb

    def next_row(self) -> None:
        self._row += 1

    def set_error(self, key: str, msg: str | None) -> None:
        field, err = self._errors[key]
        err.setText(msg or "")
        err.setVisible(bool(msg))
        set_prop(field, "error", bool(msg))

    def clear_errors(self) -> None:
        for k in self._errors:
            self.set_error(k, None)


def confirm(parent: QWidget, title: str, text: str, ok_text: str = "Delete", danger: bool = True) -> bool:
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Icon.NoIcon)
    ok: QPushButton = box.addButton(ok_text, QMessageBox.ButtonRole.AcceptRole)
    ok.setProperty("variant", "danger-solid" if danger else "primary")
    cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(cancel)
    box.exec()
    return box.clickedButton() is ok


def info(parent: QWidget, title: str, text: str) -> None:
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Icon.NoIcon)
    box.addButton("OK", QMessageBox.ButtonRole.AcceptRole)
    box.exec()


def inset_panel() -> tuple[QFrame, QVBoxLayout]:
    f = QFrame()
    f.setProperty("role", "inset")
    v = QVBoxLayout(f)
    v.setContentsMargins(tokens.SPACE[3], tokens.SPACE[2] + 2, tokens.SPACE[3], tokens.SPACE[2] + 2)
    v.setSpacing(tokens.SPACE[1])
    return f, v
