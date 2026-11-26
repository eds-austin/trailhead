from trailhead.app import create_app
from trailhead.ui import theme


def test_fonts_and_gallery(qtbot):
    create_app()
    fams = theme.load_fonts()
    assert fams == {"ui": "Inter", "num": "JetBrains Mono"}
    from trailhead.ui.gallery import GalleryWindow

    g = GalleryWindow()
    qtbot.addWidget(g)
    g.show()
    g.toasts.show("Hello", "World", "warning")
    assert len(g.toasts.toasts()) == 1


def test_dialog_without_fields_opens_without_button_focus_ring(ctx, window, qtbot):
    from PySide6.QtCore import Qt

    from trailhead.ui.dialogs.splits_dialog import SplitsDialog

    d = SplitsDialog(ctx, parent=window)
    d.show()
    qtbot.wait(20)
    assert not d.primary_btn.hasFocus()  # the focus ring would read as clipped button ends
    assert d.primary_btn.width() >= 80
    qtbot.keyClick(d, Qt.Key.Key_Return)
    assert d.result() == d.DialogCode.Accepted  # Enter still means Done
