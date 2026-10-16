"""REQ-20261016-135 — Stage 6 tab 路由 JS 扩展。

用户原话：「显示方式参考解析短视频内容阶段的形式」。

实现：
- ``switchHlTab`` 选择器扩 stage6 类
- ``_restoreHlTab`` 扩 stage6 类
- 路由表新增 ``sv-stage6-hl-tab`` action

覆盖：
1. ``switchHlTab`` 选择器含 .slirn-stage6-hl-tab
2. 路由表含 sv-stage6-hl-tab
"""

from pathlib import Path

FUNCLIP_ROOT = Path(__file__).resolve().parent.parent
JS = (FUNCLIP_ROOT / "slirn_home" / "static" / "short_video.js").read_text(
    encoding="utf-8")


def test_js_switches_stage6_hl_tabs():
    """switchHlTab 选择器必须含 .slirn-stage6-hl-tab + .slirn-stage6-hl-pane。"""
    # 第一个 querySelectorAll 含 stage6 tab 类
    assert ".slirn-stage6-hl-tab" in JS, (
        "switchHlTab 选择器应包含 .slirn-stage6-hl-tab"
    )
    assert ".slirn-stage6-hl-pane" in JS, (
        "switchHlTab pane 选择器应包含 .slirn-stage6-hl-pane"
    )
    # 复用现有 switchHlTab 函数（避免重写）
    assert "function switchHlTab(" in JS


def test_js_dispatches_stage6_hl_tab_action():
    """路由表必须含 sv-stage6-hl-tab → switchHlTab 派发。"""
    # 仿 stage3/stage4 路由派发模式
    assert "action === 'sv-stage6-hl-tab'" in JS, (
        "路由表应派发 sv-stage6-hl-tab action"
    )
