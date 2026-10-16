"""REQ-20261016-135 — Stage 6 tab 化 CSS 样式。

覆盖：
1. ``.slirn-stage6-hl-tab`` 样式含 active 态背景色 + 箭头旋转
2. ``.slirn-stage6-hl-pane`` 默认 display:none，.active 时 display:block
"""

from pathlib import Path

FUNCLIP_ROOT = Path(__file__).resolve().parent.parent
CSS = (FUNCLIP_ROOT / "slirn_home" / "static" / "home.css").read_text(
    encoding="utf-8")


def test_css_stage6_hl_tab_style_defined():
    """CSS 必须含 .slirn-stage6-hl-tab 样式块（active 态 + 箭头旋转）。"""
    assert ".slirn-stage6-hl-tab {" in CSS, "缺 .slirn-stage6-hl-tab 样式"
    # active 态背景色（仿 Stage 3 大按钮）
    assert ".slirn-stage6-hl-tab.active" in CSS, "缺 active 态样式"
    # 箭头旋转 180°（仿 REQ-130）
    assert "rotate(180deg)" in CSS, "缺 active 态箭头旋转 180°"


def test_css_stage6_hl_pane_display_none_by_default():
    """pane 默认 display:none，.active 时 display:block。"""
    assert ".slirn-stage6-hl-pane {" in CSS, "缺 .slirn-stage6-hl-pane 样式"
    # 取 .slirn-stage6-hl-pane 样式块（不含 .active 子选择器）
    block = CSS.split(".slirn-stage6-hl-pane {", 1)[1].split("}", 1)[0]
    assert "display: none" in block, "pane 默认 display: none"
    # active 态必须 display: block
    active_block = CSS.split(".slirn-stage6-hl-pane.active {", 1)[1].split("}", 1)[0]
    assert "display: block" in active_block, "pane active 态应 display: block"
