"""2026-10-12 修订：导航改名 + 刷新保持当前页面 + 截图区块标题。

- 「任务列表」→「长视频任务列表」；「短视频混剪」→「短视频任务列表」
- 短视频项目编辑页刷新后应回到该项目页（router.js 恢复 tab +
  short_video.js 按记忆恢复项目），不应跳回首页/列表
- Stage 3 截图区块标题 →「智能纪要课程重点截图」

接线类断言走本仓库既有 source-grep 模式（见 test_auth.py 接线守卫）。
"""

from pathlib import Path

FUNCLIP_ROOT = Path(__file__).resolve().parent.parent


def _src(rel: str) -> str:
    return (FUNCLIP_ROOT / "slirn_home" / rel).read_text(encoding="utf-8")


# ---------- 导航改名 ----------

def test_nav_labels_renamed():
    src = _src("app.py")
    assert "📋 长视频任务列表" in src, "「任务列表」按钮应改为「长视频任务列表」"
    assert "🎞 短视频任务列表" in src, "hero「短视频混剪」按钮应改为「短视频任务列表」"
    assert ">短视频任务列表<" in src, "顶栏「短视频混剪」按钮应改为「短视频任务列表」"
    # 旧文案不再出现在导航区（default 项目名里的「短视频混剪」不受影响）
    assert ">短视频混剪<" not in src
    assert "📋 任务列表<" not in src
    assert "🎞 短视频混剪" not in src
    assert "短视频混剪加载中" not in src


# ---------- 刷新保持当前页面 ----------

def test_router_tab_restore_wiring():
    src = _src("static/router.js")
    # showTab 记住最后所在 tab
    assert "localStorage.setItem('slirn.lastTab'" in src, \
        "showTab 应把当前 tab 写入 localStorage（slirn.lastTab）"
    # 刷新后恢复：_restoreLastTab + DOMContentLoaded 时机（等 defer 的 short_video.js）
    assert "function _restoreLastTab" in src, "router.js 应定义 _restoreLastTab"
    assert "DOMContentLoaded', _restoreLastTab" in src, \
        "router.js 应在 DOMContentLoaded 后恢复 tab（defer 脚本全部执行完）"
    # 恢复范围只限主导航 tab（TAB_BUTTONS 的值），不含 detail/workbench
    assert "TAB_BUTTONS).map" in src


def test_short_video_view_persistence_wiring():
    src = _src("static/short_video.js")
    assert "slirn.sv.view" in src, "short_video.js 应有视图记忆 key（slirn.sv.view）"
    # 打开项目时记忆；返回列表 / 打开失败时清空
    assert src.count("_saveSvView('', '')") >= 2, \
        "loadList 与 openProject 失败分支都应清空记忆"
    # 进入短视频 tab：有记忆 → 恢复项目页，否则列表
    assert "window.slirnShortVideoLoad = function" in src, \
        "slirnShortVideoLoad 应改为按记忆恢复的包装函数"
    assert "openProject(v.t, v.p)" in src


# ---------- 截图区块标题 ----------

def test_stage3_image_block_label():
    src = _src("short_video_ui.py")
    assert "智能纪要课程重点截图：" in src, "截图区块标题应为「智能纪要课程重点截图：」"
    assert "slirn-stage3-image-send-label" not in src, \
        "上一版加在复选框前的重复 label 已并入区块标题，应移除"


# ---------- JS 语法守卫（REQ-087 同类回归：误删函数体 → 全部按钮失效） ----------

def test_short_video_js_syntax_valid():
    import subprocess
    r = subprocess.run(
        ["node", "--check", str(FUNCLIP_ROOT / "slirn_home" / "static" / "short_video.js")],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, (
        f"short_video.js 语法错误\nnode --check exit={r.returncode}\nstderr={r.stderr[:1000]}"
    )


def test_router_js_syntax_still_valid():
    import subprocess
    r = subprocess.run(
        ["node", "--check", str(FUNCLIP_ROOT / "slirn_home" / "static" / "router.js")],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, (
        f"router.js 语法错误\nnode --check exit={r.returncode}\nstderr={r.stderr[:1000]}"
    )
