"""Small widget helpers shared across the UI."""

from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QSizePolicy, QToolButton, QWidget

from .. import theme


def repolish(w: QWidget) -> None:
    s = w.style()
    s.unpolish(w)
    s.polish(w)
    w.update()


def set_prop(w: QWidget, name: str, value) -> None:
    if w.property(name) == value:
        return
    w.setProperty(name, value)
    repolish(w)


def label(text: str = "", role: str | None = None, tone: str | None = None,
          font_token: str | None = None) -> QLabel:
    lb = QLabel(text)
    if role:
        lb.setProperty("role", role)
    if tone:
        lb.setProperty("tone", tone)
    if font_token:
        lb.setFont(theme.font(font_token))
    return lb


def button(text: str = "", variant: str = "secondary", icon: str | None = None,
           icon_color: str | None = None, tooltip: str | None = None, size: str | None = None) -> QPushButton:
    b = QPushButton(text)
    if variant != "secondary":
        b.setProperty("variant", variant)
    if size:
        b.setProperty("scale", size)
    if icon:
        p = theme.palette()
        default = {"primary": p.text_on_accent, "danger": p.negative}.get(variant, p.text_muted)
        b.setIcon(theme.icon(icon, icon_color or default, size=16))
        b.setIconSize(QSize(16, 16))
    if tooltip:
        b.setToolTip(tooltip)
    return b


def icon_button(icon: str, tooltip: str, color: str | None = None, size: int = 18) -> QToolButton:
    b = QToolButton()
    b.setProperty("variant", "ghost")
    b.setIcon(theme.icon(icon, color or theme.palette().text_muted, size=size,
                         active_color=theme.palette().text))
    b.setIconSize(QSize(size, size))
    b.setToolTip(tooltip)
    b.setAutoRaise(True)
    return b


def hline() -> QFrame:
    f = QFrame()
    f.setProperty("role", "hairline")
    f.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return f


def frame(role: str) -> QFrame:
    f = QFrame()
    f.setProperty("role", role)
    return f
