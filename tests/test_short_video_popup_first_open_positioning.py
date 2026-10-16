"""REQ-20261016-137 — 视频预览浮窗首次打开定位 bug 修复。

用户原话：
> 点预览的时候，除了这个弹出的视频播放窗口只看到左边那小条，在窗口的右边，
> 所有的视频根本都看不到。这个 bug 是第一次进到页面的时候出现。

根因（[short_video.js:1407-1413](slirn_home/static/short_video.js#L1407)）：
首次打开浮窗时，代码读 `popup.offsetWidth` 算 left 位置。但此时 popup 还
是 `hidden=true`（display:none），offsetWidth=0，left 被设为 innerWidth-24
（屏幕右外），结果弹窗从屏幕右边外开始 + 宽度 60% 向右延伸，绝大部分不可见。

修复：直接用刚算出的 w/h 推 left，不再依赖 offsetWidth。

覆盖：
1. 静态分析：svStage5Preview 不再读 popup.offsetWidth（修过的版本）
2. 静态分析：svStage5Preview 用 w 变量推 left
3. 节点语法：JS 文件 node --check 通过
4. CSS：max-width 防御仍然存在
"""

from pathlib import Path

FUNCLIP_ROOT = Path(__file__).resolve().parent.parent
JS = (FUNCLIP_ROOT / "slirn_home" / "static" / "short_video.js").read_text(
    encoding="utf-8")
CSS = (FUNCLIP_ROOT / "slirn_home" / "static" / "home.css").read_text(
    encoding="utf-8")


def test_first_open_no_longer_reads_offset_width():
    """首次打开分支不再用 popup.offsetWidth 算 left（这就是 bug 根因）。

    之前代码：
        popup.style.left = (window.innerWidth - popup.offsetWidth - 24) + 'px';

    修复后：用刚算出的 w 变量推 left（offsetWidth 在 hidden=true 时为 0）。
    """
    # 抽出 svStage5Preview 函数体
    import re
    m = re.search(r"function svStage5Preview\(btn\) \{.*?\n  \}", JS, re.DOTALL)
    assert m, "找不到 svStage5Preview 函数"
    fn_body = m.group(0)
    # 去掉注释和字符串字面量，只看代码
    code_only = re.sub(r"//[^\n]*\n", "\n", fn_body)
    code_only = re.sub(r"/\*.*?\*/", "", code_only, flags=re.DOTALL)
    code_only = re.sub(r"'[^']*'", "''", code_only)
    code_only = re.sub(r'"[^"]*"', '""', code_only)
    # 核心：代码里不再出现 popup.offsetWidth（注释里允许，作为历史标记）
    assert "popup.offsetWidth" not in code_only, (
        "svStage5Preview 代码里不应再读 popup.offsetWidth（hidden=true 时为 0，会算成 left=innerWidth-24 让弹窗跑到屏幕外）"
    )


def test_first_open_uses_w_variable_for_left():
    """修复后用 w 变量推 left：left = innerWidth - w - 24。"""
    import re
    m = re.search(r"function svStage5Preview\(btn\) \{.*?\n  \}", JS, re.DOTALL)
    assert m, "找不到 svStage5Preview 函数"
    fn_body = m.group(0)
    # 必须在 first-open 分支里定义 w/h，然后用 w 推 left
    first_open_block = re.search(
        r"if \(!popup\.style\.width\) \{(.*?)\}",
        fn_body, re.DOTALL
    )
    assert first_open_block, "找不到 first-open 分支"
    block = first_open_block.group(1)
    assert "var w =" in block, "first-open 必须有 var w = ..."
    assert "var h =" in block, "first-open 必须有 var h = ..."
    assert "popup.style.width = w" in block, "first-open 宽度应用 w"
    assert "popup.style.height = h" in block, "first-open 高度应用 h"
    # 关键：left 用 w 推，而不是 offsetWidth
    assert "innerWidth - w - 24" in block, "left 公式必须是 innerWidth - w - 24"


def test_first_open_left_is_within_viewport():
    """逻辑校验：left = innerWidth - w - 24，w 至少 1px ≤ 60% innerWidth，
    所以 left 一定在 [innerWidth - 0.6*innerWidth - 24, innerWidth - 24] 范围内。

    即使 innerWidth=320（极小屏），w = min(640, 192) = 192，left = 320-192-24 = 104
    仍在屏幕内；不会超过 innerWidth。"""
    import re
    m = re.search(r"function svStage5Preview\(btn\) \{.*?\n  \}", JS, re.DOTALL)
    fn_body = m.group(0)
    first_open_block = re.search(
        r"if \(!popup\.style\.width\) \{(.*?)\}",
        fn_body, re.DOTALL
    )
    block = first_open_block.group(1)
    # 校验公式：left = innerWidth - w - 24（不是 - 0 也不是 - offsetWidth）
    assert re.search(r"popup\.style\.left\s*=\s*\(?window\.innerWidth\s*-\s*w\s*-\s*24\)?", block), (
        "left 公式应为 (window.innerWidth - w - 24)"
    )


def test_js_syntax_valid():
    """JS 文件 node --check 通过（避免意外语法错）。"""
    import subprocess
    result = subprocess.run(
        ["node", "--check", str(FUNCLIP_ROOT / "slirn_home" / "static" / "short_video.js")],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, (
        f"JS 语法错：stderr={result.stderr[:500]}"
    )


def test_css_max_width_defense_still_present():
    """CSS 的 max-width 防御仍存在（避免 width 写穿屏）。"""
    popup_block = CSS.split(".slirn-sv-video-popup {", 1)[1].split("}", 1)[0]
    assert "max-width" in popup_block, "缺 max-width 防御"
    # 验证 max-width 是 100vw - 24
    import re
    m = re.search(r"max-width:\s*([^;]+);", popup_block)
    assert m, "找不到 max-width 声明"
    val = m.group(1).strip()
    # 允许 calc(100vw - 24px) 或等价值
    assert "100vw" in val, f"max-width 应基于 100vw：{val}"
    assert "24" in val, f"max-width 应留 24px 边距：{val}"


def test_ensure_popup_creates_hidden_element():
    """ensureSvVideoPopup 创建的浮窗初始 hidden=true（确认 first-open 路径必然走）。"""
    import re
    fn = re.search(r"function ensureSvVideoPopup\(\) \{.*?\n  \}", JS, re.DOTALL)
    assert fn, "找不到 ensureSvVideoPopup"
    body = fn.group(0)
    assert "el.hidden = true" in body, "ensureSvVideoPopup 必须初始 hidden=true"


def test_sv_stage5_preview_routes_to_function():
    """sv-stage5-preview action 派发到 svStage5Preview（路由不破）。"""
    assert "action === 'sv-stage5-preview'" in JS
    assert "return svStage5Preview(btn)" in JS
