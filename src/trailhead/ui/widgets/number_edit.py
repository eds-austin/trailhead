"""Numeric line edit: typed input only, mono font, selects all on focus, value() → Decimal | None."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from PySide6.QtCore import QRegularExpression, Qt, QTimer
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import QLineEdit

from .. import theme


class NumberEdit(QLineEdit):
    def __init__(self, places: int = 4, allow_negative: bool = False, placeholder: str = "", parent=None):
        super().__init__(parent)
        self.setProperty("numeric", True)
        self.setFont(theme.font("num"))
        self.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        sign = "-?" if allow_negative else ""
        frac = rf"(\.\d{{0,{places}}})?" if places > 0 else ""
        self.setValidator(QRegularExpressionValidator(QRegularExpression(rf"^{sign}[\d,]*{frac}$"), self))
        self.setPlaceholderText(placeholder)
        self._places = places
        self.user_edited = False
        self.textEdited.connect(lambda _t: setattr(self, "user_edited", True))

    def value(self) -> Decimal | None:
        t = self.text().replace(",", "").strip()
        if t in ("", "-", "."):
            return None
        try:
            return Decimal(t)
        except InvalidOperation:
            return None

    def set_value(self, v: Decimal | int | float | None, places: int | None = None) -> None:
        if v is None:
            self.clear()
            return
        d = Decimal(str(v))
        p = self._places if places is None else places
        if p == 0:
            text = f"{d:.0f}"
        else:
            text = f"{d.normalize():f}"
            if "." not in text or len(text.split(".")[1]) < 2:
                text = f"{d:.2f}"
        self.setText(text)

    def focusInEvent(self, e) -> None:  # noqa: N802
        super().focusInEvent(e)
        QTimer.singleShot(0, self.selectAll)
