from trailhead.ui.theme import tokens
from trailhead.ui.theme.qss import build_qss


def test_alpha_and_blend():
    assert tokens.alpha("#4C9AFF", 0.14) == "rgba(76,154,255,0.14)"
    assert tokens.blend("#FFFFFF", 0.0, "#000000") == "#000000"
    assert tokens.blend("#FFFFFF", 1.0, "#000000") == "#FFFFFF"


def test_qss_uses_tokens_only():
    qss = build_qss(tokens.DARK)
    assert tokens.DARK.accent in qss and tokens.DARK.surface in qss
    assert "{" in qss and "{{" not in qss
