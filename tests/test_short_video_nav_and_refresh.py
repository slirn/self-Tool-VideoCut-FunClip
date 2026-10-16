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
    # 修订四：「新建任务」挪进短视频列表（新建短视频任务），hero 只留两个列表入口
    assert "新建任务" not in hero, "「新建任务」按钮应挪到短视频列表，hero 不再保留"
    assert hero.index("短视频列表") < hero.index("长视频列表"), \
        "hero 按钮顺序：短视频列表 → 长视频列表"


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
    # 刷新后恢复：_restoreLastTab + DOMContentLoaded 时机
    assert "function _restoreLastTab" in src, "router.js 应定义 _restoreLastTab"
    assert "DOMContentLoaded', _restoreLastTab" in src, \
        "router.js 应在 DOMContentLoaded 后恢复 tab"
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


def test_short_video_load_waits_for_script_readiness():
    """回归（2026-10-12 bug）：更新重启后自动弹出的页面短视频列表永远「加载中」。

    三个 JS 由 Gradio 前端运行时动态插入 <head>，defer 不生效、相互执行顺序
    不保证 —— router.js 先执行时 window.slirnShortVideoLoad 尚未定义，旧的
    `window.slirnShortVideoLoad && ...` 守卫被静默跳过且永不重试。
    showTab 必须经 _callShortVideoLoadWhenReady：就绪前轮询等待（有上限）、
    就绪后调用；就绪判定用 typeof，等待期间有 seq 接管防重复加载。
    """
    src = _src("static/router.js")
    assert "function _callShortVideoLoadWhenReady(forceList)" in src, \
        "showTab 应通过就绪等待函数调 short_video.js 的加载入口"
    assert "_callShortVideoLoadWhenReady(forceShortVideoList)" in src
    # 就绪判定 + 有界轮询 + 新调用接管
    assert "typeof window.slirnShortVideoLoad === 'function'" in src
    assert "setTimeout(tick, 100)" in src
    assert "tries > 100" in src, "轮询必须有上限（防死循环）"
    assert "_svLoadSeq" in src, "后续调用应接管旧轮询（防重复加载）"
    # 旧的静默跳过写法不应残留
    assert "window.slirnShortVideoLoad && window.slirnShortVideoLoad(" not in src


def test_explicit_short_video_list_entry_ignores_saved_editor_view():
    router_src = _src("static/router.js")
    sv_src = _src("static/short_video.js")
    assert "forceShortVideoList: action === 'goto-short-video'" in router_src
    assert "window.slirnShortVideoLoad(forceList)" in router_src
    assert "window.slirnShortVideoLoad = function(forceList)" in sv_src
    assert "if (forceList) return loadList();" in sv_src


def test_short_video_stage_navigation_shows_one_pane():
    from slirn_home import short_video_ui as ui

    project = {
        "id": "p1",
        "task_id": "t1",
        "name": "demo",
        "materials": [],
        "pipeline": {
            "stage1_source": {"status": "done"},
            "stage2_extract": {"status": "running"},
        },
    }
    html = ui.render_project(project)
    assert 'class="slirn-sv-pipeline"' in html
    assert html.count('data-action="sv-stage-nav"') == 7
    assert html.count("slirn-sv-stage-pane active") == 1
    assert (
        'class="slirn-sv-stage-nav done" data-action="sv-stage-nav" '
        'data-stage-key="stage1" aria-current="false"'
    ) in html
    assert (
        'class="slirn-sv-stage-nav current active" data-action="sv-stage-nav" '
        'data-stage-key="stage2" aria-current="step"'
    ) in html
    assert (
        'class="slirn-sv-stage-pane active" id="slirn-sv-stage-pane-stage2"'
    ) in html

    sv_src = _src("static/short_video.js")
    css = _src("static/home.css")
    assert "function switchSvStage(stageKey)" in sv_src
    assert "action === 'sv-stage-nav'" in sv_src
    pane_rule = css.split(".slirn-sv-stage-pane {", 1)[1].split("}", 1)[0]
    active_pane_rule = css.split(".slirn-sv-stage-pane.active {", 1)[1].split("}", 1)[0]
    assert "display: none;" in pane_rule
    assert "display: block;" in active_pane_rule


# ---------- REQ-20261016-131：阶段导航重设计（去 ✓ 去逗号 / 卡片背景表达状态） ----------

def test_short_video_stage_nav_no_checkmark_no_dot_in_text():
    """REQ-131：mark 永远显示数字（不写 ✓ / ! 替代），
    text 区域只显示 title（不写「1 · 选择源视频」式前缀）——数字在左侧 mark 已表达。"""
    from slirn_home import short_video_ui as ui

    project = {
        "id": "p1",
        "task_id": "t1",
        "name": "demo",
        "materials": [],
        "pipeline": {
            "stage1_source": {"status": "done"},
            "stage2_extract": {"status": "running"},
        },
    }
    html = ui.render_project(project)
    import re
    # 每个 stage-nav 块里的 mark span + copy 块；用正则抠出 stage1 (done) 的卡片
    s1_match = re.search(
        r'<button[^>]*data-stage-key="stage1"[^>]*>(.*?)</button>', html, re.DOTALL)
    assert s1_match, "找不到 stage1 卡片"
    s1 = s1_match.group(1)
    # mark 里的内容应是数字 "1"，不是 "✓"
    s1_mark = re.search(r'<span class="slirn-sv-stage-mark">([^<]*)</span>', s1)
    assert s1_mark, f"stage1 mark 缺失：{s1[:200]}"
    assert s1_mark.group(1).strip() == "1", f"done 态 mark 应是数字，实际 {s1_mark.group(1)!r}"
    # strong 里应只有 title（无「1 · 」前缀、无 · 分隔符）
    s1_strong = re.search(r'<strong>([^<]*)</strong>', s1)
    assert s1_strong, f"stage1 strong 缺失：{s1[:200]}"
    assert s1_strong.group(1) == "选择源视频", f"strong 应只剩 title，实际 {s1_strong.group(1)!r}"
    # 不能有 "·" 分隔符
    assert "·" not in s1, f"text 区不应有 · 分隔符：{s1[:200]}"


def test_short_video_stage_nav_state_card_bg():
    """REQ-131：done / current / failed 各自有卡片背景色（用户视觉区分状态）。"""
    css = _src("static/home.css")
    # 三态卡片背景必须不同
    assert ".slirn-sv-stage-nav.done" in css, "缺 .done 卡片背景"
    assert ".slirn-sv-stage-nav.current" in css, "缺 .current 卡片背景"
    assert ".slirn-sv-stage-nav.failed" in css, "缺 .failed 卡片背景"
    # 实际取三段 CSS 比对
    def _bg(sel):
        rule = css.split(sel + " {", 1)[1].split("}", 1)[0]
        for line in rule.splitlines():
            line = line.strip().rstrip(";").strip()
            if line.startswith("background:"):
                return line
        return ""
    assert _bg(".slirn-sv-stage-nav.done") != _bg(".slirn-sv-stage-nav.current"), "done/current 背景应不同"
    assert _bg(".slirn-sv-stage-nav.current") != _bg(".slirn-sv-stage-nav.failed"), "current/failed 背景应不同"


def test_short_video_stage_nav_active_no_blue_border():
    """REQ-131：active 改用 inset 左 3px 蓝条（不与 current 蓝卡背景冲突）。"""
    css = _src("static/home.css")
    active_rule = css.split(".slirn-sv-stage-nav.active {", 1)[1].split("}", 1)[0]
    assert "box-shadow" in active_rule, f"active 应用 box-shadow：{active_rule}"
    assert "inset" in active_rule, f"active 应用 inset 左条：{active_rule}"


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


# ---------- REQ-20261013-117：空列表提示按上游真实状态分流 ----------
# 用户截图反馈：Stage 3 已 done，Stage 5 空列表仍显示「先完成 Stage 3 再粗剪」。
# 根因：提示条件 = 自身产物列表空，与上游状态无关 → 文案误导。

def _hint_project(pipeline_extra: dict) -> dict:
    base = {
        "id": "p1", "task_id": "t1", "name": "demo", "materials": [],
        "pipeline": {"stage1_source": {"status": "done"}},
    }
    base["pipeline"].update(pipeline_extra)
    return base


def test_stage5_empty_hint_reflects_stage3_status():
    from slirn_home import short_video_ui as ui

    # ① Stage 3 未完成 → 保留「先完成 Stage 3」引导
    html = ui.render_project(_hint_project({"stage3_analyze": {"status": "pending"}}))
    assert "先完成 Stage 3 解析后再粗剪" in html
    assert "尚无粗剪成品" not in html
    # ② Stage 3 done 但未合成 → 引导点合成按钮，不再误报
    html = ui.render_project(_hint_project({"stage3_analyze": {
        "status": "done",
        "highlights": [{"index": 1, "title": "t", "subtitle_lines": []}],
    }}))
    assert "尚无粗剪成品 — 点击上方「🎬 合成粗剪」生成" in html
    assert "先完成 Stage 3 解析后再粗剪" not in html, (
        "Stage 5 的空列表提示不应再是「先完成 Stage 3」"
        "（Stage 4 面板另有同字样兜底文案，与 Stage 5 无关）"
    )


def test_stage6_empty_hint_reflects_stage5_artifacts():
    from slirn_home import short_video_ui as ui

    # ① Stage 5 无产物 → 保留「先完成 Stage 5 粗剪」
    html = ui.render_project(_hint_project({"stage5_coarse": {"status": "pending"}}))
    assert "先完成 Stage 5 粗剪" in html
    assert "尚无优化结果" not in html
    # ② Stage 5 有产物但未优化 → 引导开始优化，不再误报
    html = ui.render_project(_hint_project({"stage5_coarse": {
        "status": "done",
        "highlights": [{"index": 1, "coarse_mp4": "a.mp4", "duration_sec": 60}],
    }}))
    assert "尚无优化结果" in html
    assert "先完成 Stage 5 粗剪" not in html, "粗剪已有产物时不应再出现「先完成 Stage 5 粗剪」"


# ---------- REQ-20261013-116：短视频「完成后自动打开下一阶段界面」开关 ----------
# 同步长视频 REQ-20260918-046 模式：UI checkbox + localStorage 记忆 + 渲染
# 后对比快照决定是否切下一阶段。默认关（保持现状 = server focus_key 自动
# 跳到第一个未 done 阶段）；用户勾选 = 当前焦点阶段完成时自动切下阶段。

def test_sv_autonext_checkbox_rendered():
    """render_project 渲染的 HTML 必须含 #slirn-sv-autonext checkbox + 标签文字。"""
    src = _src("short_video_ui.py")
    assert '<input type="checkbox" id="slirn-sv-autonext">' in src, (
        "render_project 模板里必须有 #slirn-sv-autonext checkbox（同步长视频）"
    )
    assert "完成后，自动打开下一阶段界面" in src, (
        "checkbox 旁必须有同款文案（用户截图同款）"
    )
    assert 'class="slirn-sv-autonext"' in src, (
        "label 必须挂 .slirn-sv-autonext class（与 CSS 配套）"
    )


def test_sv_autonext_css_rule_exists():
    """CSS 须定义 .slirn-sv-autonext 样式（对齐 .slirn-wb-autonext 风格）。"""
    css = _src("static/home.css")
    # 提取 .slirn-sv-autonext 块（直到下一个 } 即可，不贪心匹配）
    assert ".slirn-sv-autonext {" in css, (
        "CSS 必须定义 .slirn-sv-autonext 基础样式"
    )
    assert ".slirn-sv-autonext input[type=\"checkbox\"]" in css, (
        "checkbox 自身大小/光标样式必须有"
    )


def test_sv_autonext_js_helpers_wired():
    """JS 必须实现：① 读/写 localStorage ② change 事件改 localStorage
    ③ svAutoNextMaybe 在 openProject 后调用 ④ switchSvStage 写当前 stage。
    """
    js = _src("static/short_video.js")
    # 1. localStorage 键
    assert "slirn.sv.autonext" in js, "localStorage 开关键必须存在"
    assert "slirn.sv.currentStage" in js, "localStorage 当前 stage 键必须存在"
    # 2. 读 / 写 / 应用状态
    assert "function _svAutoNextOn" in js
    assert "function _saveSvAutoNext" in js
    assert "function applySvAutoNextState" in js
    # 3. change 事件 + toast
    assert "e.target.id === 'slirn-sv-autonext'" in js, (
        "checkbox change 事件必须挂在 id 选择器上"
    )
    assert "已开启：当前阶段完成后自动打开下一阶段界面" in js
    assert "已关闭：阶段完成后保持在原页面" in js
    # 4. 自动跳函数 = svAutoNextMaybe（仿 wb 的 wbAutoNextMaybe）
    assert "function svAutoNextMaybe" in js
    assert "switchSvStage(stages[idx + 1])" in js, (
        "自动跳必须通过 switchSvStage 切到下一阶段（保证同步 localStorage）"
    )
    # 5. openProject 渲染后调 apply + maybe
    open_p_idx = js.find("function openProject(")
    assert open_p_idx >= 0
    open_p_body = js[open_p_idx: open_p_idx + 2400]
    assert "applySvAutoNextState()" in open_p_body, (
        "openProject 必须调 applySvAutoNextState 恢复开关"
    )
    assert "svAutoNextMaybe(prevDone, hadSv, prevActive)" in open_p_body, (
        "openProject 必须传 prevDone/hadSv/prevActive 快照给 svAutoNextMaybe"
    )
    # 6. switchSvStage 写当前 stage（避免用户主动点 tab 后被 server 抢走）
    fn_idx = js.find("function switchSvStage(")
    assert fn_idx >= 0
    fn_body = js[fn_idx: fn_idx + 800]
    assert "SV_STAGE_KEY" in fn_body, (
        "switchSvStage 末尾必须把 stageKey 写进 localStorage"
    )
