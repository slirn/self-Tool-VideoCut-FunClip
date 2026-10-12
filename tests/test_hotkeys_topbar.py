"""REQ-20261013-101：快捷键入口提到顶栏「热词库」前统一设置。

- 顶栏顺序：⌨ 快捷键 → 📚 热词库 → 大模型设置
- 顶栏「⌨ 快捷键」按钮复用 router.js 现成 `revkeys-open` 弹窗
- 面板内「⚙ 自定义」入口仍存在（双入口，不破坏现有 UX）
"""

import re
from pathlib import Path


def _topbar_html() -> str:
    """调 _build_topbar 拿顶栏 HTML（REQ-101 拆分出的纯函数）。"""
    from slirn_home.app import _build_topbar

    return _build_topbar(str(Path.cwd()))


def test_topbar_hotkeys_button_before_hotwords():
    """顶栏顺序：⌨ 快捷键 在 📚 热词库 前面。"""
    bar = _topbar_html()
    # 顶栏按钮
    assert 'data-action="revkeys-open"' in bar, "顶栏缺 ⌨ 快捷键 按钮"
    assert 'data-action="goto-hotwords"' in bar, "顶栏缺 📚 热词库 按钮"
    # 顺序：⌨ 快捷键 出现在 📚 热词库 之前
    assert bar.index('data-action="revkeys-open"') < bar.index('data-action="goto-hotwords"'), \
        "⌨ 快捷键 应在 📚 热词库 前面"


def test_topbar_hotkeys_button_uses_reuse_dispatch():
    """复用 router.js 现有 revkeys-open 派发（0 新增逻辑）。"""
    js_path = Path(__file__).resolve().parent.parent / "slirn_home" / "static" / "router.js"
    js = js_path.read_text(encoding="utf-8")
    # dispatch 已存在（7106 附近）
    assert "if (action === 'revkeys-open')" in js
    assert "revKeysOpenModal();" in js


def test_panel_revkeys_open_still_present():
    """面板内「⚙ 自定义」入口仍存在（双入口，不破坏现有 UX）。"""
    from pathlib import Path
    py_path = Path(__file__).resolve().parent.parent / "slirn_home" / "app.py"
    src = py_path.read_text(encoding="utf-8")
    # 顶栏 _build_topbar 内有 1 个 + 至少 1 处面板 → >= 2
    assert src.count('data-action="revkeys-open"') >= 2, \
        "面板内的 ⚙ 自定义（数据点 > 1：顶栏 + 至少一处面板）"


def test_topbar_does_not_introduce_new_service_endpoint():
    """入口提升是纯 UI 改动，不新增后端端点（localStorage 即可）。"""
    from pathlib import Path
    py = (Path(__file__).resolve().parent.parent / "slirn_home" / "app.py").read_text(encoding="utf-8")
    # grep 'revkeys' 路由 — 应该没有
    assert "@app.app.post(\"/slirn/api/revkeys" not in py
    assert "@app.app.get(\"/slirn/api/revkeys" not in py
