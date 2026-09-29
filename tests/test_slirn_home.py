"""测试 slirn_home 任务列表渲染 — REQ-B AC-B.3 / AC-B.4。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 把 funclip-main 仓库根 + slirn-standalone 加到 sys.path
FUNCLIP_ROOT = Path(__file__).resolve().parent.parent
SLIRN_STANDALONE = FUNCLIP_ROOT.parent / "slirn-standalone"

if str(FUNCLIP_ROOT) not in sys.path:
    sys.path.insert(0, str(FUNCLIP_ROOT))
if SLIRN_STANDALONE.exists() and str(SLIRN_STANDALONE) not in sys.path:
    sys.path.insert(0, str(SLIRN_STANDALONE))


@pytest.fixture
def mgr(tmp_path: Path):
    """构造一个临时仓库 + 假视频 + TaskManager 实例。"""
    from tasklib import TaskManager

    video = tmp_path / "lecture.mp4"
    video.write_bytes(b"fake")

    m = TaskManager(tmp_path)
    m.create(name="讲座 1", original_video=video, hotwords=["FunASR"])
    m.create(name="讲座 2", original_video=video)
    return m


# ---------- render_task_list ----------

def test_render_task_list_returns_rows_and_meta(mgr):
    from slirn_home.task_list import render_task_list

    rows, meta = render_task_list(mgr)
    assert len(rows) == 2
    assert meta == {"当前任务数": 2}


def test_render_task_list_columns(mgr):
    """AC-B.3.2 — 列表字段完整。"""
    from slirn_home.task_list import render_task_list

    rows, _ = render_task_list(mgr)
    assert len(rows[0]) == 6  # ID / 任务名 / 视频 / 状态 / 创建 / 修改


def test_render_task_list_uses_chinese_status_label(mgr):
    """AC-B.3.6 — 状态显示用中文。"""
    from tasklib import TASK_STATUS_LABEL, TaskStatus

    from slirn_home.task_list import render_task_list

    rows, _ = render_task_list(mgr)
    # DRAFT 的中文标签是 "草稿"
    assert rows[0][3] == TASK_STATUS_LABEL[TaskStatus.DRAFT]


def test_render_task_list_sorted_by_updated_desc(mgr):
    """AC-B.3.3 — 按 updated_at 倒序。"""
    from tasklib import TaskStatus

    from slirn_home.task_list import render_task_list

    rows, _ = render_task_list(mgr)
    # 后建的应该在前面
    assert "讲座 2" in rows[0][1]
    # 更新第一个任务的状态后，刷新列表
    mgr.update_status(mgr.list()[1].task_id, TaskStatus.ASSETS_READY)
    rows2, _ = render_task_list(mgr)
    # "讲座 1" 现在 updated 更新，应该排第一
    assert rows2[0][1] == "讲座 1"


def test_render_task_list_empty(tmp_path: Path):
    """AC-B.3.4 — 空列表处理。"""
    from tasklib import TaskManager

    from slirn_home.task_list import render_task_list

    m = TaskManager(tmp_path)
    rows, meta = render_task_list(m)
    assert rows == []
    assert meta == {"当前任务数": 0}


# ---------- show_detail ----------

def test_show_detail_returns_markdown(mgr):
    from slirn_home.task_list import show_detail

    task_id = mgr.list()[0].task_id
    md = show_detail(mgr, task_id)
    assert "### 任务详情" in md
    assert task_id in md
    assert "讲座" in md


def test_show_detail_handles_missing_task(mgr):
    """AC-B.4.2 — 不存在任务返回错误提示。"""
    from slirn_home.task_list import show_detail

    md = show_detail(mgr, "99999999-999")
    assert "❌" in md
    assert "不存在" in md


# ---------- arm / cancel / confirm delete ----------

def test_arm_delete(mgr):
    """AC-B.4.4-5 — arm 状态。"""
    from slirn_home.task_list import arm_delete

    btn_text, cancel_text, cancel_visible = arm_delete()
    assert "确认删除" in btn_text
    assert cancel_text == "取消"
    assert cancel_visible is True


def test_cancel_delete(mgr):
    from slirn_home.task_list import cancel_delete

    btn_text, cancel_text, cancel_visible = cancel_delete()
    assert "删除" in btn_text
    assert "⚠️" not in btn_text
    assert cancel_visible is False


def test_confirm_delete_removes_task(mgr):
    """AC-B.4.6 — 确认后真正删除。"""
    from slirn_home.task_list import confirm_delete

    task_id = mgr.list()[0].task_id
    rows, meta, msg, btn_text, cancel_visible = confirm_delete(mgr, task_id)
    assert "已删除" in msg
    assert len(rows) == 1  # 少了一个任务


def test_confirm_delete_handles_missing(mgr):
    from slirn_home.task_list import confirm_delete

    rows, meta, msg, btn_text, cancel_visible = confirm_delete(mgr, "99999999-999")
    assert "不存在" in msg


# ---------- placeholder_action ----------

def test_placeholder_action_returns_toast():
    """AC-B.4.8 — 占位按钮返回 toast。"""
    from slirn_home.task_list import placeholder_action

    msg = placeholder_action("开始剪辑")
    assert "开始剪辑" in msg
    assert "后续 REQ" in msg


# ---------- paths ----------

def test_find_slirn_standalone_root_via_sibling(tmp_path: Path, monkeypatch):
    """通过 sibling 目录查找。"""
    from slirn_home.paths import find_slirn_standalone_root

    # 模拟 funclip-main + sibling slirn-standalone 结构
    fake_parent = tmp_path / "parent"
    fake_parent.mkdir()
    fake_funclip = fake_parent / "FunClip-main"
    fake_funclip.mkdir()
    fake_slirn = fake_parent / "slirn-standalone"
    fake_slirn.mkdir()
    (fake_slirn / "tasklib").mkdir()

    # 改 __file__ 让 find_repo_root 返回 fake_funclip
    import slirn_home.paths as paths_mod
    monkeypatch.setattr(paths_mod, "__file__", str(fake_funclip / "slirn_home" / "paths.py"))

    root = find_slirn_standalone_root()
    assert root == fake_slirn.resolve()


def test_find_slirn_standalone_root_via_env(tmp_path: Path, monkeypatch):
    """通过环境变量查找。"""
    from slirn_home.paths import find_slirn_standalone_root

    env_root = tmp_path / "env_slirn"
    env_root.mkdir()
    (env_root / "tasklib").mkdir()
    monkeypatch.setenv("SLIRN_STANDALONE_ROOT", str(env_root))

    root = find_slirn_standalone_root()
    assert root == env_root.resolve()


# ---------- 时区显示（UTC 存储转本地，2026-09-17 修复「新建任务显示 8 小时前」） ----------

def test_time_ago_utc_aware_is_now():
    """tasklib 存的是带时区 UTC — 刚创建的任务应显示「刚刚」，不是「8 小时前」。"""
    from datetime import datetime, timezone

    from slirn_home.app import _time_ago

    just_now = datetime.now(timezone.utc)
    assert _time_ago(just_now) == "刚刚"


def test_time_ago_utc_iso_string_is_now():
    from datetime import datetime, timezone

    from slirn_home.app import _time_ago
    iso = datetime.now(timezone.utc).isoformat()
    assert _time_ago(iso) == "刚刚"


def test_time_ago_naive_treated_as_local():
    """naive 输入（本地时间）行为不变。"""
    from datetime import datetime, timedelta

    from slirn_home.app import _time_ago

    assert _time_ago(datetime.now() - timedelta(hours=2)) == "2 小时前"


def test_fmt_local_converts_utc_to_local():
    """_fmt_local 输出本地时间（与本地 now 同一天同一小时），而非 UTC 原值。"""
    from datetime import datetime, timezone

    from slirn_home.app import _fmt_local

    now_utc = datetime.now(timezone.utc)
    s = _fmt_local(now_utc)
    local = now_utc.astimezone().replace(tzinfo=None)
    assert s == local.strftime("%Y-%m-%d %H:%M:%S")


def test_time_ago_invalid_string():
    from slirn_home.app import _time_ago

    assert _time_ago("not-a-date") == "未知"
    assert _time_ago(None) == "未知"


# ---------- REQ-20260926-NNN：编辑任务页顶部「进入剪辑工作台」跳转 ----------

def test_edit_task_page_has_open_workbench_jump():
    """编辑任务页（_render_create_task edit 分支）顶部必须有
    data-action="open-workbench" 按钮，data-task-id 用 edit.task_id 插值。
    复用 router.js:7313 的 open-workbench action（→ openWorkbench）。"""
    src = (FUNCLIP_ROOT / "slirn_home" / "app.py").read_text(encoding="utf-8")
    i = src.find('id="slirn-edit-state"')
    assert i > 0, "必须有 slirn-edit-state（编辑页隐藏状态 div）"
    # 取整个 edit 分支 f-string 内容（到下一个 ''' 结束）
    end = src.find("'''", i)
    assert end > i, "edit 分支 f-string 未闭合"
    edit_block = src[i:end]
    assert 'data-action="open-workbench"' in edit_block, (
        "编辑页顶部缺少 data-action=\"open-workbench\" 跳转按钮")
    assert 'data-task-id="{_esc(edit.task_id)}"' in edit_block, (
        "跳转按钮必须用 edit.task_id 插值（不能写死）")
    # 必须出现中文「进入剪辑工作台」按钮文案
    assert "进入剪辑工作台" in edit_block, "跳转按钮文案缺失"


# ---------- REQ-20260926-NNN：编辑/剪辑页 URL hash 互斥持久化 ----------

def test_task_page_hash_helpers_exist():
    """REQ-20260926-NNN：编辑/剪辑页独立刷新需要 #edit=/#wb= hash 互斥持久化。"""
    src = (FUNCLIP_ROOT / "slirn_home" / "static" / "router.js").read_text(encoding="utf-8")
    for fn in ("_setTaskPageHash", "_clearTaskPageHash", "_loadEditTaskHTML",
               "_restoreTaskPageFromHash"):
        assert "function " + fn in src, f"缺少 helper {fn}"


def test_open_workbench_writes_wb_hash():
    """openWorkbench 必须写 #wb=<tid>（通过 _setTaskPageHash，自动清 #edit=）。"""
    src = (FUNCLIP_ROOT / "slirn_home" / "static" / "router.js").read_text(encoding="utf-8")
    i = src.find("function openWorkbench")
    assert i > 0
    body = src[i:src.find("\n  }\n", i)]
    assert "_setTaskPageHash('wb'" in body, (
        "openWorkbench 必须通过 _setTaskPageHash('wb', tid) 写 hash")


def test_edit_task_action_writes_edit_hash():
    """edit-task 成功路径必须写 #edit=<tid>。"""
    src = (FUNCLIP_ROOT / "slirn_home" / "static" / "router.js").read_text(encoding="utf-8")
    i = src.find("action === 'edit-task'")
    assert i > 0
    body = src[i:src.find("else if (action === 'open-workbench')")]
    assert "_setTaskPageHash('edit'" in body, (
        "edit-task 成功分支必须写 #edit=<tid> hash（刷新保留）")


def test_cancel_create_and_goto_tabs_clear_hash():
    """cancel-create 与 TAB_BUTTONS 跳转必须清 hash（防刷新跳回）。"""
    src = (FUNCLIP_ROOT / "slirn_home" / "static" / "router.js").read_text(encoding="utf-8")
    i = src.find("action === 'cancel-create'")
    assert i > 0
    body = src[i:src.find("else if (action === 'hw-add')")]
    assert "_clearTaskPageHash" in body, "cancel-create 必须清 hash"
    i = src.find("if (TAB_BUTTONS[action])")
    assert i > 0
    body = src[i:i + 400]
    assert "_clearTaskPageHash" in body, (
        "TAB_BUTTONS 跳转必须清 hash（防切走非 wb 页后刷新跳回任务页）")
    assert "slirn-tab-workbench'" in body and "!== 'slirn-tab-workbench'" in body, (
        "TAB_BUTTONS 跳转需豁免 workbench（进 wb 不清，由 openWorkbench 自己写）")


def test_restore_hash_supports_both_wb_and_edit():
    """_restoreTaskPageFromHash 必须同时识别 #wb= 和 #edit=。"""
    src = (FUNCLIP_ROOT / "slirn_home" / "static" / "router.js").read_text(encoding="utf-8")
    i = src.find("function _restoreTaskPageFromHash")
    assert i > 0
    body = src[i:src.find("\n  }\n", i)]
    assert "kind === 'wb'" in body, "恢复函数必须识别 #wb="
    assert "kind === 'edit'" in body, "恢复函数必须识别 #edit="
    assert "_loadEditTaskHTML" in body, (
        "#edit= 恢复必须调 _loadEditTaskHTML 注入编辑 HTML")
    assert "openWorkbench" in body, "#wb= 恢复必须调 openWorkbench"
    assert "DOMContentLoaded" in src, "DOMContentLoaded 绑定必须存在"


# ---------- 精剪合成·自动获取素材 → 局部刷新（不做整页刷新） ----------

def test_fine_source_auto_local_card_swap_no_reload():
    """自动获取素材成功 → 局部换卡（card_html + outerHTML），不得整页刷新。

    背景：旧实现 `if (typeof refreshWb === 'function') refreshWb(); else
    location.reload();` — refreshWb 从未定义，必然 location.reload()，
    用户反馈「点自动获取整页刷新，体验很不友好」。"""
    src = (FUNCLIP_ROOT / "slirn_home" / "static" / "router.js").read_text(encoding="utf-8")
    i = src.find("function fineSourceAuto")
    assert i > 0, "fineSourceAuto 函数应存在"
    body = src[i:src.find("\n  }\n", i)]
    assert "location.reload" not in body, "自动获取后不得整页刷新"
    assert "refreshWb" not in body, "refreshWb 从未定义（死分支），不得保留"
    assert "card_html" in body and "outerHTML" in body, (
        "应使用服务端重渲染的单卡（card_html）做 outerHTML 换卡")
    assert "fineEnableOutputBtns" in body, "视频就绪应解除「生成预览/导出」按钮"


def test_fine_enable_output_btns_helper_exists():
    """fineEnableOutputBtns 助手存在且覆盖预览/导出两个主按钮；上传路径同样调用。"""
    src = (FUNCLIP_ROOT / "slirn_home" / "static" / "router.js").read_text(encoding="utf-8")
    i = src.find("function fineEnableOutputBtns")
    assert i > 0, "缺少 fineEnableOutputBtns 助手"
    body = src[i:src.find("\n  }\n", i)]
    assert "'fine-preview'" in body and "'fine-export'" in body
    j = src.find("function fineUpload")
    up = src[j:src.find("\n  }\n", j)]
    assert "fineEnableOutputBtns" in up, "手动上传视频就绪后同样解除主按钮 disabled"


# ---------- 优化字幕：当前播放行居中于字幕区域 + 选中态跟随 ----------

def test_opt_player_highlight_centers_row_in_list_container():
    """optPlayerHighlight 必须只滚列表容器居中，不得用 scrollIntoView（会连动
    整页滚动，行居中的是视口而非 560px 字幕框，页面会被拽走）。

    2026-09-29 用户原话：「让当前播放的字幕始终在字幕区域的垂直中间区域」。"""
    src = (FUNCLIP_ROOT / "slirn_home" / "static" / "router.js").read_text(encoding="utf-8")
    i = src.find("function optPlayerHighlight")
    assert i > 0, "optPlayerHighlight 函数应存在"
    body = src[i:src.find("\n  }\n", i)]
    assert "scrollIntoView" not in body, (
        "高亮跟随不得用 scrollIntoView（连动页面滚动）；应调 optCenterRowInList")
    assert "optCenterRowInList" in body, "命中行变化时应调 optCenterRowInList 居中"


def test_opt_center_row_in_list_helper_scrolls_container_only():
    """optCenterRowInList 只改列表 scrollTop（容器内居中），不碰外层滚动。"""
    src = (FUNCLIP_ROOT / "slirn_home" / "static" / "router.js").read_text(encoding="utf-8")
    i = src.find("function optCenterRowInList")
    assert i > 0, "缺少 optCenterRowInList 助手"
    body = src[i:src.find("\n  }\n", i)]
    assert "getBoundingClientRect" in body, "需用矩形差值计算居中偏移"
    assert "scrollTop +=" in body, "只滚列表容器（scrollTop）"
    assert "scrollIntoView" not in body, "不得连动页面滚动"


def test_opt_player_highlight_marks_kbsel_selection():
    """当前播放行必须同时置为选中态（kbsel，与 cut/rev 播放跟随同款）。"""
    src = (FUNCLIP_ROOT / "slirn_home" / "static" / "router.js").read_text(encoding="utf-8")
    i = src.find("function optPlayerHighlight")
    assert i > 0
    body = src[i:src.find("\n  }\n", i)]
    assert "kbsel" in body, "播放行应挂 kbsel 选中态"
    # 选中态样式必须存在（与 .slirn-cut-row.kbsel / .slirn-rev-row.kbsel 同款）
    css = (FUNCLIP_ROOT / "slirn_home" / "static" / "home.css").read_text(encoding="utf-8")
    assert ".slirn-opt-row.kbsel" in css, "home.css 缺少 .slirn-opt-row.kbsel 选中态样式"


# ---------- 2026-09-29：点词筛选 hint 不再被包进「说明」壳（空壳堆积） ----------

def test_opt_filter_hint_marked_skip_col_wrap():
    """点词筛选的临时 hint（slirn-opt-filter-hint）创建时必须预标 slirnCol，
    让 colEnhance 跳过 — 否则每次点词它被包进「ℹ️ 说明」折叠壳，删 hint 时
    壳残留，每处理一个词净增一个空说明区（用户反馈：每次保存都多一个）。"""
    src = (FUNCLIP_ROOT / "slirn_home" / "static" / "router.js").read_text(encoding="utf-8")
    i = src.find("hint.className = 'slirn-form-hint slirn-opt-filter-hint'")
    assert i > 0, "点词筛选 hint 创建处应存在"
    # 取 hint 创建块（className 赋值起 500 字符内）
    block = src[i:i + 500]
    assert "hint.dataset.slirnCol = '1'" in block, (
        "临时 hint 必须预标 slirnCol 跳过 colEnhance（不产生折叠壳）")


def test_col_enhance_removes_empty_col_shells():
    """colEnhance 必须自愈清扫空壳：内容元素已被移除、只剩胶囊头的 .slirn-col
    直接删掉 — 兜底所有「动态 hint 删后壳残留」类泄漏。"""
    src = (FUNCLIP_ROOT / "slirn_home" / "static" / "router.js").read_text(encoding="utf-8")
    i = src.find("function colEnhance")
    assert i > 0
    body = src[i:src.find("\n  }\n", i)]
    assert "querySelectorAll('.slirn-col')" in body, "必须扫描 .slirn-col 壳"
    assert "slirn-col-head" in body, "清扫时需排除胶囊头判断是否有内容"
    assert "removeChild" in body, "空壳必须从 DOM 移除"


# ---------- REQ-20260926-NNN：用户管理 + 登录 + 成员守卫 ----------

def test_app_imports_auth_and_routes():
    """app.py 必须 import AuthStore + register_auth_endpoints + install_auth_middleware。"""
    src = (FUNCLIP_ROOT / "slirn_home" / "app.py").read_text(encoding="utf-8")
    assert "from slirn_home.auth import AuthStore" in src or "AuthStore as _AuthStore" in src, (
        "app.py 必须 import AuthStore")
    assert "register_auth_endpoints" in src, "app.py 必须调 register_auth_endpoints"
    assert "install_auth_middleware" in src, "app.py 必须调 install_auth_middleware"


def test_app_renders_login_modal_and_topbar_user_indicator():
    """UI 必须有登录 modal + topbar 用户指示 + 🔑 登录按钮。"""
    src = (FUNCLIP_ROOT / "slirn_home" / "app.py").read_text(encoding="utf-8")
    assert "slirn-login-modal" in src, "缺少登录 modal"
    assert "slirn-current-user" in src, "缺少 topbar 用户指示"
    assert 'data-action="show-login"' in src, "缺少登录按钮 (show-login)"
    assert 'data-action="do-login"' in src, "缺少登录提交按钮 (do-login)"
    assert 'data-action="logout"' in src, "缺少登出按钮 (logout)"


def test_refresh_tasks_uses_scope_and_user():
    """refresh_tasks 端点必须按 user + scope 过滤（经 _task_list_for_cur_user）。"""
    src = (FUNCLIP_ROOT / "slirn_home" / "app.py").read_text(encoding="utf-8")
    i = src.find('"/slirn/api/refresh_tasks"')
    assert i > 0
    j = src.find("\n    @app.app.post", i + 1)
    body = src[i:j]
    assert "scope" in body, "refresh_tasks 必须支持 scope 参数"
    # REQ-20260926-NNN 修复：端点委托 _task_list_for_cur_user（读 contextvar user
    # + list_user_tasks 成员 + _render_task_list 里 list_for_user 过滤）
    assert "_task_list_for_cur_user(scope=scope)" in body, (
        "refresh_tasks 必须经 _task_list_for_cur_user 按当前用户渲染")
    # helper 本体必须真的按用户过滤
    k = src.find("def _task_list_for_cur_user")
    helper = src[k:src.find("\n    def ", k + 10)]
    assert "_get_cur_user()" in helper
    assert "list_user_tasks" in helper


def test_router_js_credentials_and_login_actions():
    """router.js 必须带 credentials + 处理登录/登出 actions + 401 拦截。"""
    src = (FUNCLIP_ROOT / "slirn_home" / "static" / "router.js").read_text(encoding="utf-8")
    assert "credentials: 'same-origin'" in src, (
        "router.js fetch 必须带 credentials:'same-origin' 带 cookie")
    assert "status === 401" in src, "必须拦截 401 弹登录"
    assert "slirnShowLogin" in src, "必须定义 slirnShowLogin"
    assert "slirnHideLogin" in src, "必须定义 slirnHideLogin"
    assert "/auth/login" in src, "必须 POST /auth/login"
    assert "/auth/logout" in src, "必须 POST /auth/logout"
    assert "/auth/me" in src, "启动必须 GET /auth/me 拉当前用户"
    assert "name === 'slirn-task-scope'" in src, (
        "scope radio change 必须触发 /refresh_tasks 重 fetch")


def test_launch_py_wires_auth_middleware_and_bootstrap_check():
    """launch.py 必须在 _register_slirn_api 之后调 install_auth_middleware；无用户时打印提示。"""
    src = (FUNCLIP_ROOT / "funclip" / "launch.py").read_text(encoding="utf-8")
    assert "_install_auth" in src or "install_auth_middleware" in src, (
        "launch.py 必须调 install_auth_middleware 修复 Gradio 6 重建丢失")
    assert "count_users() == 0" in src, "launch.py 必须检测无用户并打印提示"


def test_update_task_stays_on_edit_page_not_jump_to_list():
    """REQ-20260926-NNN：编辑页保存成功必须原地刷新编辑页（_loadEditTaskHTML），
    不能跳到任务列表（showTab('slirn-tab-tasks')）。"""
    src = (FUNCLIP_ROOT / "slirn_home" / "static" / "router.js").read_text(encoding="utf-8")
    i = src.find("action === 'update-task'")
    assert i > 0
    body = src[i:src.find("else if (action === 'set-start')")]
    assert "_loadEditTaskHTML" in body, (
        "update-task 成功必须原地刷新编辑页（_loadEditTaskHTML）")
    assert "showTab('slirn-tab-tasks')" not in body, (
        "update-task 成功不能再切到任务列表（用户要求停留编辑页）")
    assert "handleResp(r, 'slirn-tab-tasks')" not in body, (
        "update-task 成功不能再 handleResp 任务列表 tab")


# ---------- REQ-20260926-NNN：热词网格紧凑化（flex-wrap，不再固定 5 列） ----------

def test_hotword_grid_uses_flex_wrap_not_fixed_columns():
    """热词库展示区必须改用 flex-wrap 自适应换行（词宽 + 小 gap），不再固定 5 列。"""
    css = (FUNCLIP_ROOT / "slirn_home" / "static" / "home.css").read_text(encoding="utf-8")
    # 定位 .slirn-hotword-grid 块
    i = css.find(".slirn-hotword-grid {")
    assert i > 0, "必须有 .slirn-hotword-grid 样式块"
    block = css[i:css.find("}", i)]
    assert "display: flex" in block, "热词网格必须改用 flex（自适应词宽换行）"
    assert "flex-wrap: wrap" in block, "热词网格必须 flex-wrap 换行"
    assert "grid-template-columns: repeat(5" not in block, (
        "不应再固定 5 列（用 flex-wrap 自适应换行）")
    # gap 应较小（紧凑）
    assert "gap: 6px" in block, "gap 应缩小到 6px（紧凑化）"
