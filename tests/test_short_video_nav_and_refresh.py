"""2026-10-12 修订：导航改名 + 刷新保持当前页面 + 截图区块标题。

- 「任务列表」→「长视频任务列表」；「短视频混剪」→「短视频任务列表」
- 短视频项目编辑页刷新后应回到该项目页（router.js 恢复 tab +
  short_video.js 按记忆恢复项目），不应跳回首页/列表
- Stage 3 截图区块标题 →「智能纪要课程重点截图」

修订三（顶栏/hero 布局调整）：
- 顶栏删「短视频列表」按钮（入口留在 hero）；热词库移入顶栏、
  位于「大模型设置」左边；设置图标按钮（⚙️）改为文字「大模型设置」
- hero 按钮顺序：新建任务 → 短视频列表 → 长视频列表
- 大模型设置弹窗宽度加倍（420→840px）

接线类断言走本仓库既有 source-grep 模式（见 test_auth.py 接线守卫）。
"""

from pathlib import Path

FUNCLIP_ROOT = Path(__file__).resolve().parent.parent


def _src(rel: str) -> str:
    return (FUNCLIP_ROOT / "slirn_home" / rel).read_text(encoding="utf-8")


# ---------- 导航改名 ----------

def test_nav_labels_renamed():
    src = _src("app.py")
    assert "📋 长视频列表" in src, "「任务列表」按钮应改为「长视频列表」"
    assert "🎞 短视频列表" in src, "hero「短视频混剪」按钮应改为「短视频列表」"
    # 修订三：顶栏「短视频列表」按钮已删（红框），入口留在 hero
    assert ">短视频列表<" not in src, "顶栏不再放「短视频列表」按钮（入口在 hero）"
    # 旧文案不再出现在导航区（default 项目名里的「短视频混剪」不受影响）
    assert ">短视频混剪<" not in src
    assert ">短视频任务列表<" not in src
    assert "📋 任务列表<" not in src
    assert "📋 长视频任务列表" not in src
    assert "🎞 短视频混剪" not in src
    assert "短视频混剪加载中" not in src
    assert "短视频任务列表加载中" not in src


def test_topbar_and_hero_layout():
    """修订三：热词库移入顶栏（大模型设置左边）+ 设置改文字 + hero 重排。"""
    src = _src("app.py")
    topbar = src.split("slirn-topbar-actions")[1]
    topbar = topbar[: topbar.index("</div>")]
    assert 'data-action="goto-hotwords"' in topbar, "热词库按钮应在顶栏"
    assert topbar.index("热词库") < topbar.index("大模型设置"), \
        "热词库应在「大模型设置」按钮左边"
    assert 'data-action="open-llm-settings"' in topbar
    assert "⚙️" not in topbar, "设置图标按钮应改为文字「大模型设置」"

    hero = src.split("slirn-hero-actions")[1]
    hero = hero[: hero.index("</div>")]
    assert "热词库" not in hero, "热词库已移到顶栏，hero 不再保留"
    assert hero.index("新建任务") < hero.index("短视频列表") < hero.index("长视频列表"), \
        "hero 按钮顺序：新建任务 → 短视频列表 → 长视频列表"


def test_llm_hint_texts_follow_button_rename():
    """修订三：用户可见提示从「顶栏 ⚙️」改指「顶栏「大模型设置」」（按钮已无图标）。"""
    src = _src("app.py")
    assert "顶栏「大模型设置」" in src
    assert "顶栏 ⚙️" not in src
    assert "顶栏「大模型设置」" in _src("short_video_analyze.py")


def test_llm_settings_modal_width_doubled():
    """修订三：大模型设置弹窗宽度加倍（420→840）。"""
    css = _src("static/home.css")
    rule = ".slirn-modal-card.slirn-llm-card"
    assert rule in css, \
        "宽度规则需双类特异度，否则被后面的通用 .slirn-modal-card{width:380px} 压回去"
    seg = css.split(rule, 1)[1][:300]
    assert "width: 840px" in seg
    assert "min-width: 840px" in seg
    assert "max-width: calc(100vw - 48px)" in seg, "小屏兜底，不溢出视口"


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
