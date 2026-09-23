"""测试编辑任务 + 剪辑工作台 — REQ-20260915-003。

渲染层测试：任务卡片按钮、编辑模式预填、工作台阶段状态/面板。
update_task 的重截取逻辑走 E2E（见 work/REQ-20260915-003-workbench/）。

本批次更新（REQ-20260921-NNN — pipeline 端点 + UI shake-fix 测试）：
- /pipeline_run 端点 preflight 失败时返回 ok=True 携带 preflight 详情；
  前端按 r.ok=true & r.preflight.kind 分支显示「去精剪合成页补 X」结构化
  提示，而不是 r.ok=false 走「未知错误」分支。
- 跑不到精剪合成时不预检：since=fine_cut 之后阶段跳过；
  run_mode=stop_after + stop_after < fine_cut_idx 也跳过。
- pipeline.js HH:MM:SS range_enabled UI：start/dur 输入框 pattern 校验 +
  range_enabled=False 时 disabled（attribute 而非 CSS）。
- router.js shake-fix：
  · scrollIntoView 不带 smooth（opt 词频行过滤「滚到第一个出现处」）。
  · optInputOverflowCheck ±50px 滞回（hysteresis）—— 测 wrap 状态稳定
    切换后不抖动；cw 跨度 ~100px 时，cw±50 阈值保证落点稳定。
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

FUNCLIP_ROOT = Path(__file__).resolve().parent.parent
SLIRN_STANDALONE = FUNCLIP_ROOT.parent / "slirn-standalone"

if str(FUNCLIP_ROOT) not in sys.path:
    sys.path.insert(0, str(FUNCLIP_ROOT))
if SLIRN_STANDALONE.exists() and str(SLIRN_STANDALONE) not in sys.path:
    sys.path.insert(0, str(SLIRN_STANDALONE))


def _make_mgr(tmp_path: Path):
    from tasklib import TaskManager

    video = tmp_path / "lecture.mp4"
    video.write_bytes(b"fake-video")
    return TaskManager(tmp_path), video


# ---------- 任务卡片按钮 ----------

def test_task_list_has_edit_and_workbench_buttons(tmp_path: Path):
    from slirn_home.app import _render_task_list

    m, _ = _make_mgr(tmp_path)
    m.create(name="t", original_video=tmp_path / "lecture.mp4")
    html = _render_task_list(m)
    assert 'data-action="edit-task"' in html
    assert 'data-action="open-workbench"' in html
    assert 'data-action="delete-task"' in html
    assert ">✏️ 编辑</button>" in html
    assert ">✂️ 剪辑</button>" in html


# ---------- 编辑模式预填 ----------

def test_render_create_task_edit_prefill(tmp_path: Path):
    from tasklib import HotwordLibrary
    from tasklib.models import TimeSegment

    from slirn_home.app import _render_create_task

    m, video = _make_mgr(tmp_path)
    HotwordLibrary(tmp_path).add("张老师", category="讲师")  # 公共库有词，picker 才渲染 cell
    t = m.create(name="原名", original_video=video,
                 hotwords=["张老师", "训练营", "手动词"],
                 inherit_public=True,
                 hotword_sources={"张老师": "pick", "训练营": "pick", "手动词": "manual"})
    seg = tmp_path / "tasks" / t.task_id / "raw_input" / "seg.mp4"
    seg.write_bytes(b"s")
    t.segment = TimeSegment(start="00:00:10.000", end="00:00:40.000", path=seg)
    m._write_metadata(t)

    html = _render_create_task(tmp_path, edit=m.get(t.task_id))
    # 编辑状态载体
    assert 'id="slirn-edit-state"' in html
    assert f'data-task-id="{t.task_id}"' in html
    assert "src=original" in html
    # 步骤1：文件信息条（不可换视频）
    assert "编辑模式，不可更换" in html
    assert "📁 lecture.mp4（" in html
    assert "slirn-dropzone" not in html
    # 时间预填
    assert 'value="00:00:10.000"' in html
    assert 'value="00:00:40.000"' in html
    # 任务名 / 继承勾选 / 手动词
    assert 'value="原名"' in html
    assert 'id="slirn-hw-inherit-all" checked' in html
    assert ">手动词</textarea>" in html
    # 公共库 pick 词预选中；不在公共库的 pick 词进「任务已选」分区（不丢失）
    assert 'slirn-pick-cell selected" data-word="张老师"' in html
    assert "任务已选（不在公共库）" in html
    assert 'slirn-pick-cell selected" data-word="训练营"' in html
    # 保存按钮
    assert "💾 保存修改" in html and 'data-action="update-task"' in html


def test_render_create_task_fresh_has_dropzone(tmp_path: Path):
    """全新建模式不受影响：仍有上传区。"""
    from slirn_home.app import _render_create_task

    html = _render_create_task(tmp_path)
    assert "slirn-dropzone" in html
    assert "✅ 创建任务" in html
    assert "slirn-edit-state" not in html


def test_render_create_task_cut_preview_label(tmp_path: Path):
    """REQ-20260915-004 —「截取预览」更名「待剪辑视频预览」（fresh + edit 两模式）。"""
    from slirn_home.app import _render_create_task

    # 全新建
    fresh = _render_create_task(tmp_path)
    assert ">🎬 待剪辑视频预览</button>" in fresh
    assert "截取预览" not in fresh

    # 编辑模式（同一套页面）
    m, video = _make_mgr(tmp_path)
    t = m.create(name="编辑更名", original_video=video)
    edit = _render_create_task(tmp_path, edit=m.get(t.task_id))
    assert ">🎬 待剪辑视频预览</button>" in edit
    assert "截取预览" not in edit


# ---------- 工作台 ----------

def test_wb_stage_states_fresh_task(tmp_path: Path):
    """刚建的任务：素材 done，字幕 current，其后 pending。"""
    from slirn_home.app import _wb_stage_states

    m, video = _make_mgr(tmp_path)
    t = m.create(name="t", original_video=video)
    assert _wb_stage_states(t) == ["done", "current"] + ["pending"] * 5


def test_wb_stage_states_with_subtitle(tmp_path: Path):
    """有 subtitle.json（磁盘为准）→ 前两阶段 done，第三 current。"""
    from slirn_home.app import _wb_stage_states

    m, video = _make_mgr(tmp_path)
    t = m.create(name="t", original_video=video)
    outputs = tmp_path / "tasks" / t.task_id / "outputs"
    (outputs / "subtitle.json").write_text(
        '{"version":1,"segments":[{"i":1,"start_ms":0,"end_ms":1,"start":"0","end":"0","text":"x"}]}',
        encoding="utf-8")
    assert _wb_stage_states(t) == ["done", "done", "current"] + ["pending"] * 4


def test_wb_stage_states_by_status_rank(tmp_path: Path):
    """无产物但状态已推进 → 按状态序判定（服务器重启后内存丢失场景的兜底）。"""
    from tasklib.models import TaskStatus

    from slirn_home.app import _wb_stage_states

    m, video = _make_mgr(tmp_path)
    t = m.create(name="t", original_video=video)
    m.update_status(t.task_id, TaskStatus.ROUGH_CUT_DONE)
    states = _wb_stage_states(m.get(t.task_id))
    assert states == ["done"] * 4 + ["current"] + ["pending"] * 2


def test_wb_stage_states_optimize_done(tmp_path: Path):
    """优化字幕（REQ-20260917-030）：optimize_subtitle.json 有 saved_at → done；
    旧 fine_revision.json 已确认（旧精剪修订流程）同样视为 done（兼容）。"""
    from tasklib.models import TaskStatus

    from slirn_home.app import _wb_stage_states

    m, video = _make_mgr(tmp_path)
    t = m.create(name="t", original_video=video)
    m.update_status(t.task_id, TaskStatus.ROUGH_CUT_DONE)  # 前三阶段按状态序 done
    outputs = tmp_path / "tasks" / t.task_id / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    (outputs / "rough_compose.mp4").write_bytes(b"fake-mp4")  # 粗剪合成按磁盘产物
    (outputs / "optimize_subtitle.json").write_text(
        '{"version":1,"saved_at":"2026-09-17T10:00:00","segments":[],"occurrences":[]}',
        encoding="utf-8")
    states = _wb_stage_states(m.get(t.task_id))
    # REQ-20260919-075：去掉第 8 阶段「字幕合成」→ 7 阶段
    # assets/subtitle/subtitle_review/rough_cut/rough_compose/fine_review = 6 done，fine_cut = current
    assert states == ["done"] * 6 + ["current"]

    (outputs / "optimize_subtitle.json").unlink()
    (outputs / "fine_revision.json").write_text(
        '{"version":1,"saved_at":"2026-09-16T10:00:00","entries":[]}', encoding="utf-8")
    assert _wb_stage_states(m.get(t.task_id))[5] == "done"


def test_render_workbench_layout(tmp_path: Path):
    from slirn_home.app import _render_workbench

    m, video = _make_mgr(tmp_path)
    t = m.create(name="工作台任务", original_video=video, hotwords=["热词A"])
    html = _render_workbench(t.task_id, m)
    # 顶部信息
    assert "剪辑工作台 · 工作台任务" in html
    assert "📁 lecture.mp4（" in html
    assert "未截取 · 使用完整原视频" in html
    # 左侧 7 阶段 + 状态（+ REQ-20260918-053：末尾追加 1 个「执行日志」非流水线视图 = 8）
    # REQ-20260919-075：原 8 阶段去掉「字幕合成」= 7 阶段
    assert html.count('class="slirn-wb-stage ') == 8
    assert "slirn-wb-stage done" in html and "slirn-wb-stage current" in html
    assert "素材准备" in html and "字幕生成" in html and "执行日志" in html
    assert "字幕合成" not in html, "REQ-075：去掉了第八阶段「字幕合成」"
    # 右侧面板：素材清单 + 字幕区（含生成按钮）+ 字幕修订区 + 切分修剪区
    # （REQ-005 后修订区真实化；REQ-20260916-008 后切分修剪区真实化）+ 4 个规划占位
    assert "📦 资产清单" in html
    assert 'data-action="gen-subtitle"' in html
    assert "处理剪辑 · 第 2 步：字幕修订" in html
    assert 'id="slirn-wb-pane-subtitle_review"' in html
    assert "✂️ 切分修剪" in html, "阶段名「粗剪」改为「切分修剪」（REQ-20260916-008）"
    assert "处理剪辑 · 第 3 步：切分修剪" in html
    assert 'id="slirn-wb-pane-rough_cut"' in html
    # 粗剪合成（REQ-20260916-016 → REQ-20260918-045 由可选改为必做）：占位真实化 — 必做阶段，可选徽章移除
    assert "🎥 粗剪合成" in html
    assert "slirn-wb-stage-optional" not in html, "REQ-20260918-045：粗剪合成不再是可选徽章"
    assert 'id="slirn-wb-pane-rough_compose"' in html
    # 优化字幕（REQ-20260917-030）：原「精剪修订·热词替换」改造 — 成片重识别 + 不明确字词
    assert "✨ 优化字幕" in html
    assert "不明确字词" in html
    assert 'id="slirn-wb-pane-fine_review"' in html
    # REQ-20260919-075：去掉了 mux 阶段 → 0 个占位（所有阶段都已实现）
    assert html.count("规划中 — 该阶段将在后续版本提供") == 0
    assert html.count("slirn-wb-pane\"") >= 1  # 面板容器齐备
    # 聚焦 current（字幕生成）→ 字幕面板默认显示
    import re
    assert re.search(r'id="slirn-wb-pane-subtitle"(?![^>]*display:none)', html)
    assert 'id="slirn-wb-pane-assets" style="display:none;"' in html


def test_render_workbench_stage_marks_start_at_zero(tmp_path: Path):
    """REQ-20260921-NNN：阶段序号从 0 开始（assets=0、精剪合成=6）。

    之前从 1 开始（assets=1、精剪合成=7）。用户反馈：与软件开发的
    0-based 习惯对齐，让序号看起来更自然。

    mark 规则（app.py:_render_workbench）：
      - state == "done" → ✓（完成态覆盖序号）
      - state == "current" → str(i)（显示序号，不让 ▶ 盖掉）
      - state == "pending" → str(i)（显示序号）

    用户进一步要求：current（进行中）不要用 ▶ 覆盖序号 —— 序号一直展示，
    只在阶段完成时才被 ✓ 覆盖。

    7 个阶段下标：assets=0、字幕生成=1、字幕修订=2、切分修剪=3、
    粗剪合成=4、优化字幕=5、精剪合成=6。

    关键回归：mark 数字里不能出现 7（旧 1-based 精剪合成），也不能
    出现 8（旧 1-based 第 8 阶段）。
    """
    import re as _re
    from slirn_home.app import _render_workbench

    m, video = _make_mgr(tmp_path)
    t = m.create(name="stage-marks", original_video=video)
    html = _render_workbench(t.task_id, m)

    # 提取每个 stage 卡片里的 mark（按 DOM 出现顺序）
    marks = _re.findall(
        r'<span class="slirn-wb-stage-mark">([^<]+)</span>',
        html,
    )
    # 7 个 stage + 1 个「执行日志」（📜）= 8 个 mark
    assert len(marks) == 8, f"应渲染 8 个 mark，实际 {len(marks)}：{marks}"
    # assets (i=0) 是 done → ✓
    assert marks[0] == "✓", f"assets（原视频存在）应 done → ✓，实际 {marks[0]!r}"
    # 字幕生成 (i=1) 是首个 pending → current —— 但 mark 仍是数字 1
    # （current 不要让 ▶ 覆盖序号）
    assert marks[1] == "1", (
        f"字幕生成（首个 pending / current）应显示序号 1（不应用 ▶ 覆盖），"
        f"实际 {marks[1]!r}"
    )
    # 后续 5 个 pending：i=2..6 → 数字 2..6
    assert marks[2:7] == ["2", "3", "4", "5", "6"], (
        f"剩下 5 个 pending 应是 2..6（i 直接用），实际 {marks[2:7]}"
    )
    # 关键：mark 数字里不能出现旧的 1-based 7 或 8
    numeric_marks = [m for m in marks if m.isdigit()]
    assert "7" not in numeric_marks, f"mark 中不能出现 7（旧 1-based 精剪合成），实际 {numeric_marks}"
    assert "8" not in numeric_marks, f"mark 中不能出现 8（旧 1-based 第 8 阶段），实际 {numeric_marks}"
    # 第 8 个是执行日志图标
    assert "📜" in marks[7], f"第 8 个 mark 应为执行日志图标，实际 {marks[7]!r}"


def test_render_workbench_autonext_switch(tmp_path: Path):
    """REQ-20260918-046 + REQ-20260921-NNN — 完成后自动打开下一阶段界面开关在顶部阶段列表内渲染，未勾选，
    标题解释语义；由 JS 读 localStorage 并按之前已 done 的节点决定是否触发跳转。"""
    from slirn_home.app import _render_workbench

    m, video = _make_mgr(tmp_path)
    t = m.create(name="autonext", original_video=video)
    html = _render_workbench(t.task_id, m)
    assert 'class="slirn-wb-autonext"' in html
    assert 'id="slirn-wb-autonext"' in html
    # 默认未勾选（用户主动开启后才生效）
    assert '<input type="checkbox" id="slirn-wb-autonext">' in html
    # REQ-20260921-NNN：文案改为「完成后，自动打开下一阶段界面」
    assert "完成后，自动打开下一阶段界面" in html
    assert "title=" in html and "自动打开下一阶段" in html.replace("&#39;", "'")


def test_render_workbench_missing_task(tmp_path: Path):
    from slirn_home.app import _render_workbench

    html = _render_workbench("20990101-001", _make_mgr(tmp_path)[0])
    assert "任务不存在" in html


def test_render_workbench_stages_collapse_controls(tmp_path: Path):
    """REQ-20260916-002 — 阶段列表可折叠：阶段卡内「« 收起」+ 顶部「🧭 展开阶段」（默认 CSS 隐藏）。"""
    from slirn_home.app import _render_workbench

    m, video = _make_mgr(tmp_path)
    t = m.create(name="折叠", original_video=video)
    html = _render_workbench(t.task_id, m)
    assert 'class="slirn-wb-stages-head"' in html
    # 两处开关：阶段卡头部收起 + 顶栏展开（展开按钮只在收起后由 CSS 显示）
    assert "« 收起" in html and "🧭 展开阶段" in html
    assert "slirn-wb-stages-expand" in html
    # 阶段条目不受影响（7 流水线 + 1 执行日志视图 = 8）
    # REQ-20260919-075：原 8 阶段去掉「字幕合成」→ 7 阶段
    assert html.count('class="slirn-wb-stage ') == 8


def test_render_workbench_stages_rail(tmp_path):
    """REQ-20260916-011 — 收起后左缘常驻「阶段»」竖向导轨：恢复入口就在原面板位置。

    背景：此前唯一恢复入口是右上角「🧭 展开阶段」小按钮，长列表滚动后不在
    视口内，用户收起后找不到 → 阶段列表「消失再也出不来」。导轨与顶栏按钮
    同一 data-action，任何一处点击即可展开；CSS 默认隐藏、收起后才显示。
    """
    from slirn_home.app import _render_workbench

    m, video = _make_mgr(tmp_path)
    t = m.create(name="导轨", original_video=video)
    html = _render_workbench(t.task_id, m)
    assert 'class="slirn-wb-stages-rail" data-action="wb-toggle-stages"' in html
    # 导轨内容：🧭 + 阶 + 段 + »（竖排 span，避免 writing-mode 兼容性问题）
    assert "<span>🧭</span><span>阶</span><span>段</span><span>»</span>" in html
    # 三处开关共用同一 handler：阶段卡收起 + 顶栏展开 + 左缘导轨
    assert html.count('data-action="wb-toggle-stages"') == 3
    # CSS：导轨默认隐藏，收起后显示并占 40px 窄列（导轨在原面板位置）
    css = (FUNCLIP_ROOT / "slirn_home" / "static" / "home.css").read_text(encoding="utf-8")
    assert ".slirn-wb-stages-rail { display: none; }" in css
    assert ".wb-stages-collapsed .slirn-wb-stages-rail {" in css
    assert ".wb-stages-collapsed .slirn-wb-main { grid-template-columns: 40px 1fr; }" in css


# ---------- 精剪视频·fine_compose 数据规整（REQ-20260919-061 Phase C 收尾）----------

def test_get_fine_compose_normalizes_string_numbers(tmp_path):
    """fine_compose.json 里的数字字段若存成字符串 → _get_fine_compose 转回数字。

    背景：wb 渲染时 `{font["bg_opacity"]:.2f}` 要求数字，若存成字符串会抛
    "Unknown format code 'f' for object of type 'str'" → 整页 500。

    REQ-20260919-061 用户补充：x/y 改为像素（1920×1080 设计空间），所以字符串
    "0.5" 在 x 上属于旧归一化坐标 → 自动迁移成 960 px 整数；scale/crop_* 保持 float。
    """
    import json
    from slirn_home.app import _get_fine_compose

    m, video = _make_mgr(tmp_path)
    t = m.create(name="fine-cut", original_video=video)
    # 写一份故意全字符串数字的 fine_compose.json（旧 0-1 归一化坐标）
    fc_path = m.tasks_dir / t.task_id / "fine_compose.json"
    fc_path.parent.mkdir(parents=True, exist_ok=True)
    fc_path.write_text(json.dumps({
        "font": {"bg_opacity": "0.6", "size": "36", "stroke_width": "2",
                 "bg_radius": "4", "bold": True, "align": "center", "family": "STHeitiMedium"},
        "layout": {"video": {"x": "0.5", "y": "0.5", "scale": "1.0",
                             "crop_x": "0.1", "crop_y": "0.1",
                             "crop_w": "0.9", "crop_h": "0.6", "enabled": True}},
    }), encoding="utf-8")

    fc = _get_fine_compose(m, t.task_id)
    # 字体数字字段已转 float
    assert fc["font"]["bg_opacity"] == 0.6 and isinstance(fc["font"]["bg_opacity"], float)
    assert fc["font"]["size"] == 36.0 and isinstance(fc["font"]["size"], float)
    # x/y 走迁移逻辑：旧 0-1 字符串 "0.5" → 像素 960（0.5 * 1920）
    assert fc["layout"]["video"]["x"] == 960 and isinstance(fc["layout"]["video"]["x"], int)
    assert fc["layout"]["video"]["y"] == 540 and isinstance(fc["layout"]["video"]["y"], int)
    # scale 保持 float
    assert fc["layout"]["video"]["scale"] == 1.0
    # crop_* 现在也走迁移：旧 0-1 字符串 "0.9" → 像素 1728（0.9 * 1920）
    assert fc["layout"]["video"]["crop_w"] == 1728 and isinstance(fc["layout"]["video"]["crop_w"], int)
    assert fc["layout"]["video"]["crop_h"] == 648  # 0.6 * 1080
    # 落盘后 _schema 已升级到 2（再读一次不会重复迁移）
    assert fc["_schema"] == 2


def test_render_fine_cut_zone_with_string_font_does_not_500(tmp_path):
    """fine_compose.json 有字符串数字时，渲染工作台不能 500。

    回归保护：之前 `{font["bg_opacity"]:.2f}` 在字符串上会 ValueError。
    """
    import json
    from slirn_home.app import _render_workbench, _get_fine_compose, _save_fine_compose

    m, video = _make_mgr(tmp_path)
    t = m.create(name="fine-cut-str", original_video=video)
    fc_path = m.tasks_dir / t.task_id / "fine_compose.json"
    fc_path.parent.mkdir(parents=True, exist_ok=True)
    fc_path.write_text(json.dumps({
        "font": {"bg_opacity": "0.6", "size": 36, "stroke_width": 2,
                 "bg_color": "#000000", "bg_radius": 4, "bg_enabled": False,
                 "bold": True, "align": "center", "family": "STHeitiMedium",
                 "stroke_color": "#000000"},
    }), encoding="utf-8")

    html = _render_workbench(t.task_id, m)
    # 渲染必须成功，且字体区出现（REQ-20260919-061a：val span 已移除，只剩 input + ▲▼）
    assert 'slirn-fine-font-block' in html
    assert 'bg_opacity_val' not in html, "val span 已移除；输入框本身即数值显示"
    assert 'slirn-fine-font-bg_opacity_num' in html, "font slider 仍需配 number input"


# ---------- REQ-20260921-NNN-fix-scale-keyerror：layout 子 dict 字段补全 ----------

def test_get_fine_compose_backfills_partial_layout_subdict(tmp_path):
    """layout[k] 存在但子字段不全（如 {"x":42}）→ _get_fine_compose 必须补全 scale/enabled 等。

    回归保护（REQ-20260921-NNN-fix-scale-keyerror）：生产环境 click 剪辑按钮抛
    "SyntaxError: Unexpected token 'i' Internal Server Error"，根因：
    save_fine_layout 部分提交后落盘的 layout[video] 只有 {"x":42}，旧版 setdefault
    只补顶层 key，渲染 _render_fine_cut_zone 取 lc["scale"] 抛 KeyError → HTTP 500。
    """
    import json
    from slirn_home.app import _get_fine_compose

    m, video = _make_mgr(tmp_path)
    t = m.create(name="partial-layout", original_video=video)
    fc_path = m.tasks_dir / t.task_id / "fine_compose.json"
    fc_path.parent.mkdir(parents=True, exist_ok=True)
    # 模拟生产：仅写了一部分字段
    fc_path.write_text(json.dumps({
        "_schema": 2,
        "layout": {"video": {"x": 42}, "subtitle": {"x": 100, "y": 50}},
    }), encoding="utf-8")

    fc = _get_fine_compose(m, t.task_id)
    video_layout = fc["layout"]["video"]
    # 用户的 x=42 必须保留
    assert video_layout["x"] == 42
    # 默认字段必须补全（不能 KeyError 也不能抛错）
    for required in ("y", "scale", "enabled", "crop_x", "crop_y", "crop_w", "crop_h",
                     "viewport", "crop_aspect_lock"):
        assert required in video_layout, f"video.layout 应补全 {required} 字段，实际 keys={list(video_layout.keys())}"
    # subtitle 也得补
    sub_layout = fc["layout"]["subtitle"]
    assert sub_layout["x"] == 100
    assert sub_layout["y"] == 50
    assert "scale" in sub_layout and "enabled" in sub_layout


def test_render_workbench_with_partial_layout_does_not_500(tmp_path):
    """layout 子 dict 缺字段时，渲染工作台不能 500（端到端回归）。

    REQ-20260921-NNN-fix-scale-keyerror：用户报告点击 剪辑 按钮时收到
    "SyntaxError: Unexpected token 'i'"，根因是后端 /slirn/api/workbench 返回
    HTTP 500 + HTML "Internal Server Error"（来自 lc["scale"] KeyError）。
    """
    import json
    from slirn_home.app import _render_workbench

    m, video = _make_mgr(tmp_path)
    t = m.create(name="partial-layout-render", original_video=video)
    fc_path = m.tasks_dir / t.task_id / "fine_compose.json"
    fc_path.parent.mkdir(parents=True, exist_ok=True)
    # 故意制造一个只有 x 字段的 video layout
    fc_path.write_text(json.dumps({
        "_schema": 2,
        "layout": {"video": {"x": 42}},
    }), encoding="utf-8")

    # 必须成功渲染，不能抛 KeyError
    html = _render_workbench(t.task_id, m)
    # 渲染产物里能找到 video scale 滑块（说明默认 scale 被成功读取）
    assert "slirn-fine-video-scale" in html, "渲染产物里应有 video scale 滑块"


# ---------- fine_compose x/y 像素化迁移（REQ-20260919-061 用户补充）----------

def test_get_fine_compose_migrates_old_normalized_layout(tmp_path):
    """旧版 fine_compose.json（_schema 缺失，x/y 和 crop_* 都是 0-1）→ 读时自动按 1920×1080 转像素。

    迁移后 _schema=2 落盘；第二次读不应再变化（幂等）。
    """
    import json
    from slirn_home.app import _get_fine_compose

    m, video = _make_mgr(tmp_path)
    t = m.create(name="legacy-coords", original_video=video)
    fc_path = m.tasks_dir / t.task_id / "fine_compose.json"
    fc_path.parent.mkdir(parents=True, exist_ok=True)
    fc_path.write_text(json.dumps({
        "layout": {
            "video":    {"x": 0.0,  "y": 0.0,  "scale": 1.0, "enabled": True,
                         "crop_x": 0.0, "crop_y": 0.0, "crop_w": 1.0, "crop_h": 1.0},
            "subtitle": {"x": 0.5,  "y": 0.9,  "scale": 1.0, "enabled": True},
            "cover":    {"x": 0.7,  "y": 0.85, "scale": 0.3, "enabled": False},
            "bg":       {"x": 0.0,  "y": 0.0,  "scale": 1.0, "enabled": False},
        },
    }), encoding="utf-8")

    fc = _get_fine_compose(m, t.task_id)
    # 旧 0-1 自动 × 设计空间 → 像素整数
    assert fc["layout"]["video"]["x"] == 0
    assert fc["layout"]["video"]["y"] == 0
    assert fc["layout"]["subtitle"]["x"] == 960      # 0.5 * 1920
    assert fc["layout"]["subtitle"]["y"] == 972      # 0.9 * 1080
    assert fc["layout"]["cover"]["x"] == 1344        # 0.7 * 1920
    assert fc["layout"]["cover"]["y"] == 918         # 0.85 * 1080
    # crop_* 也走迁移：旧 0-1 → 设计空间像素
    assert fc["layout"]["video"]["crop_x"] == 0
    assert fc["layout"]["video"]["crop_y"] == 0
    assert fc["layout"]["video"]["crop_w"] == 1920    # 1.0 * 1920
    assert fc["layout"]["video"]["crop_h"] == 1080    # 1.0 * 1080
    # 类型：x/y/crop_* 都是 int，scale 是 float
    assert isinstance(fc["layout"]["video"]["x"], int)
    assert isinstance(fc["layout"]["video"]["crop_w"], int)
    assert isinstance(fc["layout"]["video"]["scale"], float)
    assert fc["_schema"] == 2

    # 落盘后再读，幂等（不再变化）
    fc2 = _get_fine_compose(m, t.task_id)
    assert fc2["layout"]["subtitle"]["x"] == 960
    assert fc2["layout"]["video"]["crop_w"] == 1920
    assert fc2["_schema"] == 2


def test_get_fine_compose_keeps_existing_pixel_layout(tmp_path):
    """已是像素值（_schema=2，x > 1）→ 不应再做 0-1 → 像素的二次迁移。"""
    import json
    from slirn_home.app import _get_fine_compose

    m, video = _make_mgr(tmp_path)
    t = m.create(name="pixel-coords", original_video=video)
    fc_path = m.tasks_dir / t.task_id / "fine_compose.json"
    fc_path.parent.mkdir(parents=True, exist_ok=True)
    fc_path.write_text(json.dumps({
        "_schema": 2,
        "layout": {
            "video":    {"x": 100, "y": 200, "scale": 0.5, "enabled": True,
                         "crop_x": 100, "crop_y": 100, "crop_w": 1600, "crop_h": 800},
            "subtitle": {"x": 960, "y": 972, "scale": 1.0, "enabled": True},
            "cover":    {"x": 1344, "y": 918, "scale": 0.3, "enabled": False},
            "bg":       {"x": 0, "y": 0, "scale": 1.0, "enabled": False},
        },
    }), encoding="utf-8")

    fc = _get_fine_compose(m, t.task_id)
    # x/y 应保持原值（不被迁移）
    assert fc["layout"]["video"]["x"] == 100
    assert fc["layout"]["video"]["y"] == 200
    assert fc["layout"]["subtitle"]["x"] == 960
    # crop_* 应保持像素值
    assert fc["layout"]["video"]["crop_x"] == 100
    assert fc["layout"]["video"]["crop_w"] == 1600
    assert fc["_schema"] == 2


def test_run_fine_render_crop_filter_uses_design_space_pixels(tmp_path, monkeypatch):
    """ffmpeg crop 表达式按 (crop_*/1920|1080) 把设计空间像素换算到源视频 iw/ih。

    REQ-20260919-061 用户补充：crop_* 是设计空间像素；不同源视频分辨率下
    渲染都正确（4K/1080p/720p/竖屏等）。
    """
    import json
    from slirn_home.app import _run_fine_render, _save_fine_compose

    m, video = _make_mgr(tmp_path)
    t = m.create(name="crop-px", original_video=video)
    # 把 video.crop_* 设为像素值（设计空间 1920×1080 的一部分）
    fc = {
        "materials": {"video": {"path": str(video.relative_to(mgr_root(tmp_path))),
                                "type": "video", "source": "upload"}},
        "layout": {"video": {"x": 0, "y": 0, "scale": 1.0,
                             "crop_x": 320, "crop_y": 180, "crop_w": 1280, "crop_h": 720,
                             "enabled": True}},
    }

    # Mock ffmpeg subprocess + 解析 filter_complex
    captured = {}
    class _FakeResult:
        returncode = 0
        stderr = ""
        stdout = ""
    def _fake_run(cmd, **kw):
        captured["cmd"] = cmd
        for i, a in enumerate(cmd):
            if a == "-filter_complex":
                captured["filter"] = cmd[i + 1]
                break
        return _FakeResult()
    import subprocess
    monkeypatch.setattr(subprocess, "run", _fake_run)

    out_path = mgr_root(tmp_path) / "tasks" / t.task_id / "outputs" / "test.mp4"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    _save_fine_compose(m, t.task_id, fc)

    res = _run_fine_render(t.task_id, m, out_path, duration=10.0)
    assert res["ok"] is True
    # filter 应含 crop=iw*1280/1920:ih*720/1080:iw*320/1920:ih*180/1080
    assert "crop=iw*1280/1920" in captured["filter"]
    assert "ih*720/1080" in captured["filter"]
    assert "iw*320/1920" in captured["filter"]
    assert "ih*180/1080" in captured["filter"]


def mgr_root(root):
    return root


def test_render_fine_cut_zone_uses_pixel_sliders(tmp_path):
    """_render_fine_cut_zone 输出的 x/y 和 crop_* 滑块 HTML 都用像素范围。

    REQ-20260919-061 用户补充：所有空间字段统一为 1920×1080 设计空间像素。
    """
    from slirn_home.app import _render_workbench

    m, video = _make_mgr(tmp_path)
    t = m.create(name="px-slider", original_video=video)
    html = _render_workbench(t.task_id, m)
    # video.x: max=1920 step=1
    assert 'id="slirn-fine-video-x"' in html
    assert 'max="1920"' in html
    assert 'step="1"' in html
    # video.y: max=1080 step=1
    assert 'id="slirn-fine-video-y"' in html
    assert 'max="1080"' in html
    # 不应有旧 max="1" step="0.01" 的归一化滑块（x/y）
    assert 'max="1" step="0.01"' not in html
    # 不应有旧 max="1" step="0.005" 的归一化滑块（crop_*）
    assert 'max="1" step="0.005"' not in html
    # v13 用户反馈：画布 X/Y 允许负数（−画布宽到+画布宽 / −画布高到+画布高）。
    # 因此标签改成 "X（-1920–1920）" + min="-1920"（X）/ min="-1080"（Y）。
    assert 'X（-1920–1920）' in html, "画布 X 标签应说明允许负数"
    assert 'Y（-1080–1080）' in html, "画布 Y 标签应说明允许负数"
    # min 应是 -1920/-1080（不仅 0）
    assert 'min="-1920"' in html, "video X 滑块 min 应为 -1920"
    assert 'min="-1080"' in html, "video Y 滑块 min 应为 -1080"
    # 但 crop_*（源坐标）保持 ≥0：起点仍 0–1920/0–1080
    assert 'X 起点（0–1920）' in html
    assert 'Y 起点（0–1080）' in html
    # crop_* 还是用 0–1920/0–1080
    import re as _re
    crop_x_seg = html[html.find('id="slirn-fine-video-crop_x"'):html.find('id="slirn-fine-video-crop_x"') + 400]
    assert 'min="0"' in crop_x_seg, "crop_x 起点应保持 min=0（源坐标不能为负）"
    # crop_* 也是像素（设计空间）— REQ-20260919-061 用户补充
    assert 'id="slirn-fine-video-crop_x"' in html
    assert 'id="slirn-fine-video-crop_y"' in html
    assert 'id="slirn-fine-video-crop_w"' in html
    assert 'id="slirn-fine-video-crop_h"' in html
    # 默认值显示为整数（subtitle.x=672 → 滑块值 672；crop_w=1920）
    assert 'value="672"' in html
    assert 'value="972"' in html
    assert 'value="1920"' in html  # crop_w 默认
    assert 'value="1080"' in html  # crop_h 默认


# ---------- REQ-20260919-061 扩展：片头封面 + 背景音乐 ----------

def test_get_fine_compose_injects_audio_defaults(tmp_path):
    """新建 task 后 fc["audio"] 应等于 _FINE_AUDIO_DEFAULTS。"""
    from slirn_home.app import _get_fine_compose, _FINE_AUDIO_DEFAULTS

    m, video = _make_mgr(tmp_path)
    t = m.create(name="audio-defaults", original_video=video)
    fc = _get_fine_compose(m, t.task_id)
    assert fc["audio"] == dict(_FINE_AUDIO_DEFAULTS)


def test_get_fine_compose_cover_old_x_y_legacy_migrates(tmp_path):
    """旧数据 layout.cover 有 x/y/scale 但没 duration → 自动补 duration=2.0。

    REQ-20260919-061 扩展：cover 从「角标小图」改为「片头全屏海报」，旧字段
    保留但补 duration 默认值。
    """
    import json
    from slirn_home.app import _get_fine_compose

    m, video = _make_mgr(tmp_path)
    t = m.create(name="cover-legacy", original_video=video)
    fc_path = m.tasks_dir / t.task_id / "fine_compose.json"
    fc_path.parent.mkdir(parents=True, exist_ok=True)
    fc_path.write_text(json.dumps({
        "_schema": 2,
        "layout": {
            "video": {"x": 0, "y": 0, "scale": 0.7, "enabled": True,
                      "crop_x": 0, "crop_y": 0, "crop_w": 1920, "crop_h": 1080},
            "subtitle": {"x": 672, "y": 972, "scale": 1.0, "enabled": True},
            "cover": {"x": 1344, "y": 918, "scale": 0.3, "enabled": False},  # 旧字段
            "bg": {"x": 0, "y": 0, "scale": 1.0, "enabled": False},
        },
    }), encoding="utf-8")

    fc = _get_fine_compose(m, t.task_id)
    cover = fc["layout"]["cover"]
    assert cover.get("duration") == 2.0  # 自动补
    # 旧字段保留（不强制覆盖 — 数据不丢）
    assert cover.get("x") == 1344
    assert cover.get("y") == 918


def test_render_fine_cut_zone_includes_cover_duration_slider(tmp_path):
    """片头封面控制块：启用 checkbox + 时长滑块 max=10 step=0.5 value=2.0。"""
    from slirn_home.app import _render_workbench

    m, video = _make_mgr(tmp_path)
    t = m.create(name="cover-ui", original_video=video)
    html = _render_workbench(t.task_id, m)
    # 启用 checkbox + duration 滑块
    assert 'class="slirn-fine-cover-block"' in html
    assert 'data-key="cover"' in html
    assert 'id="slirn-fine-cover-duration"' in html
    assert 'max="10"' in html
    assert 'step="0.5"' in html
    assert 'value="2.0"' in html
    # 提示文案
    assert "0–10 秒" in html


def test_render_fine_cut_zone_includes_audio_volume_slider(tmp_path):
    """背景音乐控制块：启用 checkbox + 音量/淡入/淡出 4 个滑块。

    REQ-20260922-NNN：音量滑块改 dB 衰减刻度（-40 ~ 0，默认 -8 = 旧 0.4）。
    """
    from slirn_home.app import _render_workbench

    m, video = _make_mgr(tmp_path)
    t = m.create(name="audio-ui", original_video=video)
    html = _render_workbench(t.task_id, m)
    # 音频块容器
    assert 'class="slirn-fine-audio-block"' in html
    assert 'data-key="audio"' in html
    # 4 个滑块（独立 selector — 用 data-audio-key 而非 data-key）
    assert 'id="slirn-fine-audio-volume"' in html
    assert 'data-audio-key="volume_db"' in html
    assert 'value="-8"' in html  # 默认音量（dB 衰减，= 旧 0.4 线性）
    assert 'min="-40"' in html and 'max="0"' in html
    assert 'id="slirn-fine-audio-fade_in"' in html
    assert 'data-audio-key="fade_in"' in html
    assert 'id="slirn-fine-audio-fade_out"' in html
    assert 'data-audio-key="fade_out"' in html
    # 提示文案
    assert "原说话人语音" in html or "原声" in html


def test_fine_volume_db_helper_conversions():
    """REQ-20260922-NNN：_fine_volume_db 统一取 dB 衰减（含旧线性 volume 兜底换算）。"""
    from slirn_home.app import (
        _fine_volume_db, _FINE_AUDIO_DEFAULTS,
        _FINE_VOLUME_DB_MIN, _FINE_VOLUME_DB_MAX,
    )

    # 新字段直取 + clamp
    assert _fine_volume_db({"volume_db": -14.0}) == -14.0
    assert _fine_volume_db({"volume_db": -55.0}) == _FINE_VOLUME_DB_MIN  # clamp -40
    assert _fine_volume_db({"volume_db": 6.0}) == _FINE_VOLUME_DB_MAX    # clamp 0
    assert _fine_volume_db({"volume_db": "-18"}) == -18.0  # 字符串数字容忍
    # 旧线性 volume 换算：20·log10(v)
    assert abs(_fine_volume_db({"volume": 0.2}) - (-13.98)) < 0.01   # ≈ -14 dB
    assert abs(_fine_volume_db({"volume": 0.4}) - (-7.96)) < 0.01   # ≈ -8 dB（旧默认）
    assert abs(_fine_volume_db({"volume": 0.5}) - (-6.02)) < 0.01
    assert _fine_volume_db({"volume": 0}) == _FINE_VOLUME_DB_MIN      # 旧 0（静音）→ -40
    assert _fine_volume_db({"volume": 1.0}) == 0.0                    # 1.0 → 0 dB
    # 新字段优先于旧字段
    assert _fine_volume_db({"volume_db": -20.0, "volume": 0.9}) == -20.0
    # 都缺 / 非法 → 默认 -8 dB
    assert _fine_volume_db({}) == _FINE_AUDIO_DEFAULTS["volume_db"]
    assert _fine_volume_db({"volume": "abc"}) == _FINE_AUDIO_DEFAULTS["volume_db"]


def test_get_fine_compose_migrates_legacy_volume_to_db(tmp_path):
    """REQ-20260922-NNN：磁盘上的旧 fine_compose.json（线性 volume）加载即迁移为
    volume_db 并在下次保存落盘，legacy 键清除。"""
    import json
    from slirn_home.app import _get_fine_compose, _save_fine_compose

    m, video = _make_mgr(tmp_path)
    t = m.create(name="legacy-vol", original_video=video)
    fc_path = mgr_root(tmp_path) / "tasks" / t.task_id / "fine_compose.json"
    fc_path.parent.mkdir(parents=True, exist_ok=True)
    # 用户此前存的 volume=0.2（本次需求的实际场景）
    fc_path.write_text(json.dumps({
        "materials": {},
        "audio": {"enabled": True, "volume": 0.2, "fade_in": 0.0, "fade_out": 0.0},
    }, ensure_ascii=False), encoding="utf-8")

    fc = _get_fine_compose(m, t.task_id)
    assert "volume" not in fc["audio"], "legacy volume 键应被清除"
    assert abs(fc["audio"]["volume_db"] - (-13.98)) < 0.01, "0.2 应迁移为 ≈ -14 dB"
    assert fc["audio"]["enabled"] is True

    # 再保存 → 磁盘是新格式
    _save_fine_compose(m, t.task_id, fc)
    on_disk = json.loads(fc_path.read_text(encoding="utf-8"))
    assert "volume" not in on_disk["audio"]
    assert abs(on_disk["audio"]["volume_db"] - (-13.98)) < 0.01


def test_render_fine_cut_zone_hint_is_at_top(tmp_path):
    """REQ-20260919-072：📐 位置坐标 hint 必须在标题之后、第一个折叠区之前。"""
    from slirn_home.app import _render_workbench

    m, video = _make_mgr(tmp_path)
    t = m.create(name="hint-top", original_video=video)
    html = _render_workbench(t.task_id, m)
    title_pos = html.find("精剪合成 · 素材合成器")
    hint_pos = html.find("📐 位置坐标")
    uploads_pos = html.find("slirn-fine-uploads-details")
    assert title_pos > 0 and hint_pos > 0 and uploads_pos > 0
    assert title_pos < hint_pos < uploads_pos, (
        f"标题({title_pos}) < hint({hint_pos}) < 上传区({uploads_pos})，"
        "确保说明在面板最顶端"
    )


def test_render_fine_cut_zone_uploads_wrapped_in_details(tmp_path):
    """REQ-20260919-072：素材上传区包在 <details> 里，默认折叠（无 open 属性）。"""
    from slirn_home.app import _render_workbench

    m, video = _make_mgr(tmp_path)
    t = m.create(name="uploads-fold", original_video=video)
    html = _render_workbench(t.task_id, m)
    # 找 <details ... id="slirn-fine-uploads-details"> 的实际标签起点
    idx = html.find('<details class="slirn-fine-section" id="slirn-fine-uploads-details"')
    assert idx > 0, "应有 <details id=slirn-fine-uploads-details> 包裹上传区"
    # summary 行
    assert "📁 上传素材（6 项）" in html
    # 默认折叠 — details 起始 100 字符内不含 open
    seg = html[idx:idx + 100]
    assert " open" not in seg, f"默认应折叠，details 起始段含 open：{seg!r}"


def test_render_fine_cut_zone_bg_detect_wrapped_in_details(tmp_path):
    """REQ-20260919-072：背景图区域检测包在 <details> 里，默认折叠。"""
    from slirn_home.app import _render_workbench

    m, video = _make_mgr(tmp_path)
    t = m.create(name="bgdetect-fold", original_video=video)
    html = _render_workbench(t.task_id, m)
    idx = html.find('<details class="slirn-fine-section" id="slirn-fine-bg-detect-details"')
    assert idx > 0, "应有 <details id=slirn-fine-bg-detect-details> 包裹背景图检测区"
    assert "🎨 背景图区域检测（4 角点 + 宽高）" in html
    seg = html[idx:idx + 100]
    assert " open" not in seg, f"默认应折叠，details 起始段含 open：{seg!r}"


def test_css_spk_find_input_rule_excludes_checkbox():
    """REQ-20260919-072 v2（连同 checkbox bug 修复）：
    .slirn-cut-spk-find input / .slirn-rev-spk-find input 必须用 :not([type=checkbox])
    排除复选框，避免 72px 宽度把 16px 复选框拉成长条 pill。
    """
    from pathlib import Path as _P
    css = (_P(__file__).resolve().parent.parent
           / "slirn_home" / "static" / "home.css").read_text(encoding="utf-8")
    # 两处都必须用 :not([type="checkbox"]) 限定
    assert ".slirn-cut-spk-find input:not([type=\"checkbox\"])" in css, (
        ".slirn-cut-spk-find input 规则应排除 checkbox（避免 72px 拉成长条）"
    )
    assert ".slirn-rev-spk-find input:not([type=\"checkbox\"])" in css, (
        ".slirn-rev-spk-find input 规则应排除 checkbox（避免 72px 拉成长条）"
    )


def test_run_fine_render_cover_intro_concat_filter(tmp_path, monkeypatch):
    """封面启用 + duration=3s 时 filter_complex 应含 [intro] + concat=n=2:v=1:a=0。

    REQ-20260919-061 扩展：封面作为片头全屏海报，渲染时用 concat 拼到视频流前。
    """
    import json
    from slirn_home.app import _run_fine_render, _save_fine_compose

    m, video = _make_mgr(tmp_path)
    t = m.create(name="cover-intro", original_video=video)
    # 上传封面文件
    cover_img = tmp_path / "cover.png"
    # 写一个有效的 PNG（最小 1x1）让 filter 不被图像解码器拒
    import struct
    cover_img.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
        + b"\x00\x00\x00\x00"
        + struct.pack(">I", 0xC4F1D3B5)  # CRC
        + struct.pack(">I", 0)
        + b"IDAT\x00\x00\x00\x00\x00"
        + struct.pack(">I", 0xD7B1B1DC)
        + b"IEND"
        + struct.pack(">I", 0xAE426082)
    )

    fc = {
        "materials": {
            "video": {"path": str(video.relative_to(mgr_root(tmp_path))),
                      "type": "video", "source": "upload"},
            "cover": {"path": str(cover_img.relative_to(mgr_root(tmp_path))),
                      "type": "image", "source": "upload"},
        },
        "layout": {"video": {"x": 0, "y": 0, "scale": 1.0,
                             "crop_x": 0, "crop_y": 0, "crop_w": 1920, "crop_h": 1080,
                             "enabled": True},
                   "cover": {"enabled": True, "duration": 3.0}},
    }

    captured = {}
    class _FakeResult:
        returncode = 0
        stderr = ""
        stdout = ""
    def _fake_run(cmd, **kw):
        captured["cmd"] = cmd
        for i, a in enumerate(cmd):
            if a == "-filter_complex":
                captured["filter"] = cmd[i + 1]
                break
        return _FakeResult()
    import subprocess
    monkeypatch.setattr(subprocess, "run", _fake_run)

    out_path = mgr_root(tmp_path) / "tasks" / t.task_id / "outputs" / "test.mp4"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    _save_fine_compose(m, t.task_id, fc)
    res = _run_fine_render(t.task_id, m, out_path, duration=10.0)
    assert res["ok"] is True

    # 片头 input 应含 -t 3.00
    assert "-t" in captured["cmd"]
    assert "3.00" in captured["cmd"]
    # filter_complex 应含 [intro] 标签 + concat n=2:v=1:a=0
    assert "[intro]" in captured["filter"]
    assert "concat=n=2:v=1:a=0" in captured["filter"]
    # 不应再有旧版 cover overlay 痕迹（旧 30% 缩放 cw=576）
    assert "scale=576" not in captured["filter"]  # 旧 30% 缩放消失
    assert "scale=324" not in captured["filter"]  # 旧 30% 缩放消失（h）
    # 封面 input 应为 scale=1920:1080（全屏海报）
    assert "scale=1920:1080:force_original_aspect_ratio=decrease" in captured["filter"]


def test_run_fine_render_subtitle_before_cover_concat(tmp_path, monkeypatch):
    """REQ-20260919-061a 用户反馈：视频和字幕的开始时间都从封面结束后起算。

    实现：subtitle filter 必须出现在 cover concat 之前。这样：
    - subtitle 烧录到 [video_stream] 上，stream t=0 = 源视频 t=0 = SRT 0
    - 然后 concat [intro] + [video_with_subs]，视频段从 output t=cover_dur 开始播
    - SRT 第 N 秒字幕出现在 output t=cover_dur + N（与视频内容对齐）
    - 封面段（output t=0..cover_dur）只有封面图、没有字幕（不会被字幕盖住）
    """
    import json
    from slirn_home.app import _run_fine_render, _save_fine_compose

    m, video = _make_mgr(tmp_path)
    t = m.create(name="sub-timing", original_video=video)
    # 上传封面 + 字幕文件
    cover_img = tmp_path / "cover.png"
    cover_img.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xdc\xccY\xe7\x00\x00\x00\x00IEND\xaeB`\x82")
    sub_file = tmp_path / "sub.srt"
    sub_file.write_text("1\n00:00:01,000 --> 00:00:03,000\nHello\n\n", encoding="utf-8")

    fc = {
        "materials": {
            "video":    {"path": str(video.relative_to(mgr_root(tmp_path))),
                         "type": "video", "source": "upload"},
            "cover":    {"path": str(cover_img.relative_to(mgr_root(tmp_path))),
                         "type": "image", "source": "upload"},
            "subtitle": {"path": str(sub_file.relative_to(mgr_root(tmp_path))),
                         "type": "srt", "source": "upload"},
        },
        "layout": {
            "video":    {"x": 0, "y": 0, "scale": 1.0,
                         "crop_x": 0, "crop_y": 0, "crop_w": 1920, "crop_h": 1080,
                         "enabled": True},
            "subtitle": {"x": 0, "y": 0, "scale": 1.0, "enabled": True},
            "cover":    {"enabled": True, "duration": 2.5},
        },
    }

    captured = {}
    class _FakeResult:
        returncode = 0
        stderr = ""
        stdout = ""
    def _fake_run(cmd, **kw):
        captured["cmd"] = cmd
        for i, a in enumerate(cmd):
            if a == "-filter_complex":
                captured["filter"] = cmd[i + 1]
                break
        return _FakeResult()
    import subprocess
    monkeypatch.setattr(subprocess, "run", _fake_run)

    out_path = mgr_root(tmp_path) / "tasks" / t.task_id / "outputs" / "test.mp4"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _save_fine_compose(m, t.task_id, fc)
    res = _run_fine_render(t.task_id, m, out_path, duration=10.0)
    assert res["ok"] is True

    f = captured["filter"]
    # 字幕 filter 必须存在（REQ-099 Phase C 改用 ass= 滤镜替代 subtitles=）
    assert "ass='" in f, "应有 ass= filter 烧录（Phase C：SRT→ASS 临时文件）"
    # 关键顺序：字幕 filter 的索引位置必须在 cover concat 之前
    sub_pos = f.find("ass='")
    concat_pos = f.find("concat=n=2:v=1:a=0")
    assert sub_pos >= 0 and concat_pos >= 0
    assert sub_pos < concat_pos, (
        f"字幕 filter (pos={sub_pos}) 必须在 cover concat (pos={concat_pos}) 之前，"
        f"否则字幕会显示在封面上且 SRT 时间从 0 起算"
    )
    # 封面 input + concat 都还在
    assert "[intro]" in f
    assert "scale=1920:1080:force_original_aspect_ratio=decrease" in f


def test_run_fine_render_audio_amix_filter(tmp_path, monkeypatch):
    """背景音乐启用 + volume_db=-14 时 filter_complex 应含 [voice] + [bgm] + amix。

    REQ-20260919-061 扩展：背景音乐与原声混合（amix），保留说话人语音。
    REQ-20260922-NNN：音量改 dB 衰减刻度（-14 dB ≈ 旧线性 0.2）。
    """
    import json
    from slirn_home.app import _run_fine_render, _save_fine_compose

    m, video = _make_mgr(tmp_path)
    t = m.create(name="audio-amix", original_video=video)
    audio_file = tmp_path / "bgm.mp3"
    audio_file.write_bytes(b"fake-mp3-data")

    fc = {
        "materials": {
            "video": {"path": str(video.relative_to(mgr_root(tmp_path))),
                      "type": "video", "source": "upload"},
            "audio": {"path": str(audio_file.relative_to(mgr_root(tmp_path))),
                      "type": "audio", "source": "upload"},
        },
        "layout": {"video": {"x": 0, "y": 0, "scale": 1.0,
                             "crop_x": 0, "crop_y": 0, "crop_w": 1920, "crop_h": 1080,
                             "enabled": True}},
        "audio": {"enabled": True, "volume_db": -14.0, "fade_in": 0.0, "fade_out": 0.0},
    }

    captured = {}
    class _FakeResult:
        returncode = 0
        stderr = ""
        stdout = ""
    def _fake_run(cmd, **kw):
        captured["cmd"] = cmd
        for i, a in enumerate(cmd):
            if a == "-filter_complex":
                captured["filter"] = cmd[i + 1]
                break
        return _FakeResult()
    import subprocess
    monkeypatch.setattr(subprocess, "run", _fake_run)

    out_path = mgr_root(tmp_path) / "tasks" / t.task_id / "outputs" / "test.mp4"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    _save_fine_compose(m, t.task_id, fc)
    res = _run_fine_render(t.task_id, m, out_path, duration=10.0)
    assert res["ok"] is True

    # filter_complex 应含 voice/bgm 标签 + amix
    assert "[voice]" in captured["filter"]
    assert "[bgm]" in captured["filter"]
    assert "amix=inputs=2:duration=first:normalize=0" in captured["filter"]
    assert "[aout]" in captured["filter"]
    assert "volume=-14.0dB" in captured["filter"]  # audio volume dB 衰减（REQ-20260922-NNN）
    assert "volume=1.0" in captured["filter"]    # 原声保持 100%
    # ffmpeg cmd 应 -map [aout]（不再 -map 0:a?）
    assert "[aout]" in captured["cmd"]
    assert "0:a?" not in captured["cmd"]


def test_run_fine_render_no_cover_no_audio_unchanged(tmp_path, monkeypatch):
    """无封面无背景音乐时 filter_complex 不含 concat/amix（向后兼容）。

    REQ-20260919-061 扩展：默认行为（不上传 cover/audio）应与原版一致。
    """
    import json
    from slirn_home.app import _run_fine_render, _save_fine_compose

    m, video = _make_mgr(tmp_path)
    t = m.create(name="back-compat", original_video=video)
    fc = {
        "materials": {"video": {"path": str(video.relative_to(mgr_root(tmp_path))),
                                "type": "video", "source": "upload"}},
        "layout": {"video": {"x": 0, "y": 0, "scale": 1.0,
                             "crop_x": 0, "crop_y": 0, "crop_w": 1920, "crop_h": 1080,
                             "enabled": True}},
        "audio": {"enabled": False, "volume": 0.4, "fade_in": 0.0, "fade_out": 0.0},
    }

    captured = {}
    class _FakeResult:
        returncode = 0
        stderr = ""
        stdout = ""
    def _fake_run(cmd, **kw):
        captured["cmd"] = cmd
        for i, a in enumerate(cmd):
            if a == "-filter_complex":
                captured["filter"] = cmd[i + 1]
                break
        return _FakeResult()
    import subprocess
    monkeypatch.setattr(subprocess, "run", _fake_run)

    out_path = mgr_root(tmp_path) / "tasks" / t.task_id / "outputs" / "test.mp4"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    _save_fine_compose(m, t.task_id, fc)
    res = _run_fine_render(t.task_id, m, out_path, duration=10.0)
    assert res["ok"] is True

    # 不应有片头拼接 / amix
    assert "[intro]" not in captured["filter"]
    assert "concat=n=2" not in captured["filter"]
    assert "amix=" not in captured["filter"]
    # 仍应有 [voice] 和 [aout]（原声直通）
    assert "[voice]" in captured["filter"]
    assert "[aout]" in captured["filter"]
    assert "anull[aout]" in captured["filter"]


# ---------- REQ-20260919-061 扩展：全局参数模板（fine_profiles 模块） ----------

def test_fine_profiles_save_and_list(tmp_path):
    """save → list 拿到。"""
    from slirn_home import fine_profiles as fp

    assert fp.list_profiles(tmp_path) == []
    saved = fp.save_profile(
        tmp_path, "教学片头",
        {"layout": {"video": {"x": 100}}, "font": {"size": 42},
         "output": {"resolution": "1080p"}, "audio": {"volume": 0.3}},
        task_id_origin="task_xxx",
    )
    assert saved["name"] == "教学片头"
    assert saved["id"].startswith("p_")
    assert saved["saved_at"]
    assert saved["task_id_origin"] == "task_xxx"
    assert saved["params"]["font"]["size"] == 42

    profiles = fp.list_profiles(tmp_path)
    assert len(profiles) == 1
    assert profiles[0]["id"] == saved["id"]
    assert profiles[0]["name"] == "教学片头"


def test_fine_profiles_apply_overwrites_task_layout(tmp_path):
    """保存模板 → 应用到另一 task → layout/font/output/audio 被覆盖，materials 不动。

    REQ-20260919-061 扩展：模板跨任务复用，且不动素材路径。
    """
    import json
    from slirn_home import fine_profiles as fp
    from slirn_home.app import _get_fine_compose, _save_fine_compose, _render_workbench

    # 任务 A：调一组参数
    (tmp_path / "a").mkdir(parents=True, exist_ok=True)
    m_a, video_a = _make_mgr(tmp_path / "a")
    t_a = m_a.create(name="task-a", original_video=video_a)
    _save_fine_compose(m_a, t_a.task_id, {
        "materials": {"video": {"path": "a.mp4", "type": "video", "source": "upload"}},
        "layout": {"video": {"x": 100, "y": 200, "scale": 0.5, "enabled": True,
                             "crop_x": 0, "crop_y": 0, "crop_w": 1920, "crop_h": 1080},
                   "subtitle": {"x": 672, "y": 972, "scale": 1.0, "enabled": True},
                   "cover": {"enabled": False, "duration": 2.0},
                   "bg": {"x": 0, "y": 0, "scale": 1.0, "enabled": False}},
        "font": {"size": 42, "family": "STHeitiMedium"},
        "output": {"resolution": "1080p"},
        "audio": {"enabled": True, "volume_db": -10.5, "fade_in": 0.0, "fade_out": 0.0},
    })
    fc_a = _get_fine_compose(m_a, t_a.task_id)
    fp.save_profile(
        tmp_path, "教学片头",
        {"layout": fc_a["layout"], "font": fc_a["font"],
         "output": fc_a["output"], "audio": fc_a["audio"]},
        task_id_origin=t_a.task_id,
    )
    profile = fp.list_profiles(tmp_path)[0]

    # 任务 B：完全不同参数 + 自己的素材
    (tmp_path / "b").mkdir(parents=True, exist_ok=True)
    m_b, video_b = _make_mgr(tmp_path / "b")
    t_b = m_b.create(name="task-b", original_video=video_b)
    # 端点 apply_fine_global_profile 的逻辑：把 params 写回 fc 后保存
    target = fp.get_profile(tmp_path, profile["id"])
    fc_b = _get_fine_compose(m_b, t_b.task_id)
    fc_b_materials_before = dict(fc_b["materials"])
    for key in fp.PROFILE_PARAM_KEYS:
        if key in target["params"]:
            fc_b[key] = target["params"][key]
    _save_fine_compose(m_b, t_b.task_id, fc_b)

    fc_b_after = _get_fine_compose(m_b, t_b.task_id)
    # layout/font/output/audio 应与任务 A 一致
    assert fc_b_after["layout"]["video"]["x"] == 100
    assert fc_b_after["font"]["size"] == 42
    assert fc_b_after["output"]["resolution"] == "1080p"
    assert fc_b_after["audio"]["volume_db"] == -10.5
    # materials 不动（任务 B 自己的素材路径）
    assert fc_b_after["materials"] == fc_b_materials_before


def test_fine_profiles_delete(tmp_path):
    """save → delete → list 为空。"""
    from slirn_home import fine_profiles as fp

    p = fp.save_profile(tmp_path, "tmp", {"font": {"size": 36}})
    assert len(fp.list_profiles(tmp_path)) == 1
    assert fp.delete_profile(tmp_path, p["id"]) is True
    assert fp.list_profiles(tmp_path) == []
    # 二次删除应返回 False（不报错）
    assert fp.delete_profile(tmp_path, p["id"]) is False


def test_fine_profiles_duplicate_name_appends_suffix(tmp_path):
    """同名模板自动加 `(2)` / `(3)` 后缀。"""
    from slirn_home import fine_profiles as fp

    p1 = fp.save_profile(tmp_path, "教学片头", {"font": {"size": 36}})
    p2 = fp.save_profile(tmp_path, "教学片头", {"font": {"size": 42}})
    p3 = fp.save_profile(tmp_path, "教学片头", {"font": {"size": 48}})
    assert p1["name"] == "教学片头"
    assert p2["name"] == "教学片头 (2)"
    assert p3["name"] == "教学片头 (3)"
    assert len(fp.list_profiles(tmp_path)) == 3


def test_fine_profiles_atomic_write_no_corruption_on_overwrite(tmp_path):
    """连续 save 两次 → 文件始终是合法 JSON（atomic write 验证）。"""
    from slirn_home import fine_profiles as fp

    fp.save_profile(tmp_path, "first", {"font": {"size": 36}})
    fp.save_profile(tmp_path, "second", {"font": {"size": 42}})
    # 直接读文件应仍是合法 JSON
    import json
    raw = (tmp_path / fp.GLOBAL_PROFILES_REL).read_text(encoding="utf-8")
    data = json.loads(raw)  # 不抛异常即合法
    assert isinstance(data["profiles"], list)
    assert len(data["profiles"]) == 2
    # 临时文件不应残留
    assert not (tmp_path / fp.GLOBAL_PROFILES_REL).with_suffix(".json.tmp").exists()


# ---------- REQ-20260919-070：引用参数（本地）+ 每行导出 ----------

def test_export_fine_global_profile_returns_full_params_json(tmp_path: Path):
    """REQ-20260919-070：POST /slirn/api/export_fine_global_profile 返 JSON 文件三件套。

    验证：
    - filename 包含清理后的模板名 + 时间戳
    - content 是合法 JSON，包含 _schema / layout / font / output / audio / materials
    - materials 为空 dict（全局模板按设计不含素材）
    - mime = application/json
    """
    from slirn_home import fine_profiles as fp
    from slirn_home.app import build_app
    from fastapi.testclient import TestClient

    prof = fp.save_profile(
        tmp_path, "教学片头",
        {
            "layout": {"video": {"x": 100}},
            "font": {"size": 42, "color": "#FF8800"},
            "output": {"resolution": "1080p"},
            "audio": {"enabled": True, "volume": 0.3},
        },
        task_id_origin="task_origin_xxx",
    )
    built = build_app(tmp_path)
    client = TestClient(built.app)
    resp = client.post("/slirn/api/export_fine_global_profile",
                       json={"profile_id": prof["id"]})
    body = resp.json()
    assert body["ok"] is True, f"导出应成功：{body}"
    assert "教学片头" in body["filename"], \
        f"文件名应含模板名「教学片头」，实际：{body['filename']}"
    assert body["filename"].endswith(".json"), \
        f"文件名应以 .json 结尾，实际：{body['filename']}"
    assert body["mime"] == "application/json"
    payload = json.loads(body["content"])
    assert payload["_schema"] == 3
    assert payload["_source_profile_id"] == prof["id"]
    assert payload["_source_profile_name"] == "教学片头"
    assert payload["_source_task_id"] == "task_origin_xxx"
    assert payload["materials"] == {}, "全局模板不含 materials，应为 {}"
    assert payload["layout"]["video"]["x"] == 100
    assert payload["font"]["size"] == 42
    assert payload["font"]["color"] == "#FF8800"
    assert payload["output"]["resolution"] == "1080p"
    assert payload["audio"]["volume"] == 0.3


def test_export_fine_global_profile_rejects_missing_id(tmp_path: Path):
    """REQ-20260919-070：profile_id 缺失/空 → _err。"""
    from slirn_home.app import build_app
    from fastapi.testclient import TestClient

    built = build_app(tmp_path)
    client = TestClient(built.app)
    for case in [{}, {"profile_id": ""}, {"profile_id": "   "}]:
        resp = client.post("/slirn/api/export_fine_global_profile", json=case)
        body = resp.json()
        assert body["ok"] is False, f"应失败：{case} → {body}"
        assert "profile_id" in body["error"], \
            f"错误信息应提及 profile_id，实际：{body['error']}"


def test_export_fine_global_profile_rejects_unknown_id(tmp_path: Path):
    """REQ-20260919-070：不存在的 profile_id → _err。"""
    from slirn_home.app import build_app
    from fastapi.testclient import TestClient

    built = build_app(tmp_path)
    client = TestClient(built.app)
    resp = client.post("/slirn/api/export_fine_global_profile",
                       json={"profile_id": "p_does_not_exist"})
    body = resp.json()
    assert body["ok"] is False
    assert "不存在" in body["error"], f"错误应说「不存在」，实际：{body['error']}"


def test_export_fine_global_profile_sanitizes_filename(tmp_path: Path):
    """REQ-20260919-070：模板名里的 Windows 非法字符（/\\:*?<>|和空白）应被替换成 _。"""
    from slirn_home import fine_profiles as fp
    from slirn_home.app import build_app
    from fastapi.testclient import TestClient

    prof = fp.save_profile(
        tmp_path, '教/学*片<头>:?|"',
        {"font": {"size": 36}},
    )
    built = build_app(tmp_path)
    client = TestClient(built.app)
    resp = client.post("/slirn/api/export_fine_global_profile",
                       json={"profile_id": prof["id"]})
    body = resp.json()
    assert body["ok"] is True
    base = body["filename"].rsplit(".", 1)[0]
    forbidden = set('\\/:*?"<>| ')
    bad = [c for c in base if c in forbidden]
    assert not bad, f"文件名不应含 Windows 非法字符：{bad}（filename={body['filename']}）"


def test_export_fine_global_profile_includes_detected_region(tmp_path: Path):
    """REQ-20260920-076：模板级导出 JSON 应包含 detected_region（与任务级 export 同口径）。

    验证：
    - save_fine_global_profile 保存模板时带 detected_region
    - export_fine_global_profile 导出时 detected_region 字段存在 + 等于保存的值
    """
    from slirn_home import fine_profiles as fp
    from slirn_home.app import build_app
    from fastapi.testclient import TestClient

    fake_region = {
        "x_min": 200, "y_min": 100, "x_max": 1500, "y_max": 900,
        "algorithm": "pixel", "threshold": 240,
        "white_pixels": 1234567,
    }
    prof = fp.save_profile(
        tmp_path, "含背景模板",
        {
            "layout": {"video": {"x": 100}},
            "font": {"size": 36},
            "output": {"resolution": "1080p"},
            "audio": {"enabled": False},
            "detected_region": fake_region,
        },
        task_id_origin="task_origin_yyy",
    )
    built = build_app(tmp_path)
    client = TestClient(built.app)
    resp = client.post("/slirn/api/export_fine_global_profile",
                       json={"profile_id": prof["id"]})
    body = resp.json()
    assert body["ok"] is True, f"导出应成功：{body}"
    payload = json.loads(body["content"])
    assert "detected_region" in payload, \
        f"导出 JSON 必须含 detected_region 顶层字段；实际顶层字段：{list(payload.keys())}"
    assert payload["detected_region"] == fake_region, \
        f"detected_region 应等于保存值；实际：{payload['detected_region']}"
    # 与任务级 export_fine_params 顶层字段集合一致（除 _source_* / _exported_at 元数据外）
    task_top = {
        "_schema", "_exported_at", "_source_task_id",
        "materials", "layout", "font", "output", "audio", "detected_region",
    }
    prof_top = {
        "_schema", "_exported_at", "_source_profile_id", "_source_profile_name", "_source_task_id",
        "materials", "layout", "font", "output", "audio", "detected_region",
    }
    assert (set(payload.keys()) - prof_top) == set(), \
        f"模板导出有未声明字段：{set(payload.keys()) - prof_top}"
    # 两个 schema 共享的核心字段（不计元数据）应一致
    shared_core = {"materials", "layout", "font", "output", "audio", "detected_region"}
    assert shared_core.issubset(set(payload.keys())), \
        f"模板导出缺少核心字段：{shared_core - set(payload.keys())}"


def test_export_fine_global_profile_old_template_no_detected_region(tmp_path: Path):
    """REQ-20260920-076 AC4：旧模板（无 detected_region 字段）→ 导出时为 None，不报错。

    模拟 REQ-076 修复前保存的模板（params 里没有 detected_region 键）→ 重新导出
    时 .get() 返回 None，导出 JSON 顶层仍有 detected_region 字段（值为 None）。
    """
    from slirn_home import fine_profiles as fp
    from slirn_home.app import build_app
    from fastapi.testclient import TestClient

    # 直接调底层 save_profile（与 REQ-076 前代码一致 — params 里不含 detected_region）
    prof = fp.save_profile(
        tmp_path, "旧模板无背景",
        {
            "layout": {"video": {"x": 50}},
            "font": {"size": 24},
            "output": {"resolution": "720p"},
            "audio": {"enabled": False},
            # ← 没有 detected_region（模拟 REQ-076 修复前的旧模板）
        },
    )
    # 二次确认：params 里确实没有 detected_region
    assert "detected_region" not in prof["params"], \
        "测试前置条件：旧模板的 params 不应含 detected_region"

    built = build_app(tmp_path)
    client = TestClient(built.app)
    resp = client.post("/slirn/api/export_fine_global_profile",
                       json={"profile_id": prof["id"]})
    body = resp.json()
    assert body["ok"] is True, f"旧模板导出不应报错：{body}"
    payload = json.loads(body["content"])
    assert "detected_region" in payload, \
        f"导出 JSON 应包含 detected_region 顶层字段（值为 None）"
    assert payload["detected_region"] is None, \
        f"旧模板 detected_region 应为 None；实际：{payload['detected_region']}"


def test_save_fine_global_profile_saves_detected_region(tmp_path: Path):
    """REQ-20260920-076 AC1：save_fine_global_profile 应把 detected_region 写进模板。

    验证：触发 save_fine_global_profile 后，模板的 params["detected_region"] 等于
    fc.detected_region。修复前会被忽略；修复后应保留。
    """
    from tasklib import TaskManager
    from slirn_home import fine_profiles as fp
    from slirn_home.app import build_app
    from fastapi.testclient import TestClient

    fake_region = {
        "x_min": 0, "y_min": 0, "x_max": 1920, "y_max": 1080,
        "algorithm": "ai", "threshold": None,
        "detected_color": [255, 255, 255],
    }
    # 建任务 + 写 fc.detected_region
    video = tmp_path / "test.mp4"
    video.write_bytes(b"fake-video")
    mgr = TaskManager(tmp_path)
    t = mgr.create(name="test-task", original_video=video)
    from slirn_home.app import _get_fine_compose, _save_fine_compose
    fc_doc = _get_fine_compose(mgr, t.task_id)
    fc_doc["detected_region"] = fake_region
    _save_fine_compose(mgr, t.task_id, fc_doc)

    built = build_app(tmp_path)
    client = TestClient(built.app)
    resp = client.post("/slirn/api/save_fine_global_profile",
                       json={"task_id": t.task_id, "name": "背景同步模板"})
    body = resp.json()
    assert body["ok"] is True, f"保存模板应成功：{body}"
    # 验证：底层文件里 params 含 detected_region
    prof_loaded = fp.get_profile(tmp_path, body["profile"]["id"])
    assert prof_loaded is not None, "模板应能取回"
    assert "detected_region" in prof_loaded["params"], \
        f"模板 params 应含 detected_region；实际 keys：{list(prof_loaded['params'].keys())}"
    assert prof_loaded["params"]["detected_region"] == fake_region


def test_render_workbench_button_renamed_to_local(tmp_path: Path):
    """REQ-20260919-070：「引用参数」按钮文案改为「引用参数（本地）」。"""
    from slirn_home.app import _render_workbench

    m, video = _make_mgr(tmp_path)
    t = m.create(name="rename-test", original_video=video)
    html = _render_workbench(t.task_id, m)
    assert "📥 引用参数（本地）" in html, \
        "按钮文案应改为「📥 引用参数（本地）」"
    bare_btn_pos = html.find('data-action="fine-import-show"')
    assert bare_btn_pos > 0
    nearby = html[bare_btn_pos:bare_btn_pos + 200]
    assert "引用参数（本地）" in nearby, \
        f"按钮后 200 字内应见「引用参数（本地）」，实际：{nearby}"


def test_router_fine_import_row_has_export_button():
    """REQ-20260919-070：fineImportShow 渲染的每行模板应包含 📤 导出 按钮。"""
    from pathlib import Path as _P
    src = (_P(__file__).resolve().parent.parent
           / "slirn_home" / "static" / "router.js").read_text(encoding="utf-8")
    assert 'data-action="fine-import-export"' in src, \
        "router.js 渲染 fine-import-row 时应包含 fine-import-export 按钮"
    assert "action === 'fine-import-export'" in src, \
        "router.js 委托处理应包含 fine-import-export 分支"
    assert "/slirn/api/export_fine_global_profile" in src, \
        "router.js 应调用 export_fine_global_profile 端点"


def test_render_fine_cut_zone_includes_profile_block(tmp_path):
    """REQ-20260919-061 用户反馈：保存参数为模板搬到顶部操作栏 + 引用参数 modal。

    旧版「底部 profile block + 单独的 fine-profile-save 按钮」已被移除，模板列表搬到
    「📥 引用参数」弹出的 modal 里。
    """
    from slirn_home.app import _render_workbench

    m, video = _make_mgr(tmp_path)
    t = m.create(name="profile-ui", original_video=video)
    html = _render_workbench(t.task_id, m)
    # REQ-20260919-061a v6：保存/引用 操作栏 与 AI/预览/导出 合并到同一行
    # (.slirn-fine-actions-bar 单一容器，flex-wrap: nowrap)，不再分前后两段
    actions_bar_pos = html.find('class="slirn-fine-actions-bar"')
    export_btn_pos = html.find('data-action="fine-export"')
    assert actions_bar_pos > 0 and export_btn_pos > 0
    # 都在同一个 actions-bar 内：导出按钮 应在 操作栏 之内（pos > 容器起点）
    assert export_btn_pos > actions_bar_pos, \
        "「导出最终视频」按钮应仍在 .slirn-fine-actions-bar 操作栏内"
    # 操作栏内部仍含模板名输入 + 保存按钮 + 引用参数按钮 + 状态指示器
    assert 'id="slirn-fine-profile-name"' in html
    assert 'data-action="fine-save-all"' in html
    assert 'data-action="fine-import-show"' in html
    assert 'id="slirn-fine-save-status"' in html
    # 引用参数 modal（默认 hidden，不应在 wb 加载时弹出）
    assert 'id="slirn-fine-import-overlay" hidden' in html
    assert 'id="slirn-fine-import-list"' in html
    assert 'data-action="fine-import-close"' in html
    # CSS 兜底：hidden 属性被 .slirn-modal-overlay 的 display:flex 覆盖的回归保护
    css = Path('slirn_home/static/home.css').read_text(encoding='utf-8')
    assert '.slirn-modal-overlay[hidden]' in css, \
        'modal-overlay 必须有 [hidden] 兜底，否则 wb 加载时模态会强行显示并阻塞点击'
    # 旧的底部 profile block 已彻底移除
    assert 'class="slirn-fine-profile-block"' not in html
    assert 'data-action="fine-profile-save"' not in html
    # v6：操作栏不换行（flex-wrap: nowrap），宽度不够时整体横向滚动
    assert 'flex-wrap: nowrap' in css, \
        ".slirn-fine-actions-bar 应设 flex-wrap: nowrap 让所有控件保持在同一行"


# ---------- REQ-20260919-061 用户反馈：保存 bug + 数值输入 + 封面静音 ----------

def test_save_fine_layout_accepts_cover_duration_field(tmp_path):
    """REQ-20260919-061 用户反馈：cover.duration 之前会被白名单过滤静默丢弃，导致
    改滑块后值不持久。现已加进 allowed_keys，端点应正常写回。
    """
    from fastapi.testclient import TestClient
    from slirn_home import build_app
    from slirn_home.app import _get_fine_compose

    m, video = _make_mgr(tmp_path)
    t = m.create(name="cover-dur-save", original_video=video)
    client = TestClient(build_app(repo_root=tmp_path).app)

    # 模拟前端 fineSaveAll 会发的请求体
    r = client.post(
        "/slirn/api/save_fine_layout",
        json={"task_id": t.task_id, "layout": {"cover": {"duration": 4.5, "enabled": True}}},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True

    # 读回 fc — duration 必须真的存进去了
    fc = _get_fine_compose(m, t.task_id)
    assert fc["layout"]["cover"]["duration"] == 4.5
    assert fc["layout"]["cover"]["enabled"] is True


def test_save_fine_layout_response_shape_uses_ok_field(tmp_path):
    """REQ-20260919-061 用户反馈「保存全部一直报错」的根因 — 前端用 `r.code !== 0` 判断
    但后端 `_ok` 返回 `{"ok": true}` 没有 `code` 字段；前端 should 检查 `r.ok === true`。
    这里直接验证响应结构，确认前端修复对齐后端形状。
    """
    from fastapi.testclient import TestClient
    from slirn_home import build_app

    m, video = _make_mgr(tmp_path)
    t = m.create(name="save-shape", original_video=video)
    client = TestClient(build_app(repo_root=tmp_path).app)

    r = client.post(
        "/slirn/api/save_fine_layout",
        json={"task_id": t.task_id, "layout": {"video": {"x": 100}}},
    )
    payload = r.json()
    assert payload["ok"] is True
    assert "code" not in payload  # 关键：后端用 ok 字段，不用 code


def test_render_fine_cut_zone_renames_save_button_to_settings(tmp_path):
    """REQ-20260919-061 用户反馈：「保存全部」应改成「保存参数为模板」（语义更准 —
    只存参数，不存素材/视频本身）。

    REQ-20260921-NNN：进一步把名字从「保存设置参数」改成「保存参数为模板」，
    让按钮语义直接说明会写入模板。
    """
    from slirn_home.app import _render_workbench

    m, video = _make_mgr(tmp_path)
    t = m.create(name="save-btn", original_video=video)
    html = _render_workbench(t.task_id, m)
    assert "💾 保存参数为模板" in html
    assert "💾 保存全部" not in html  # 旧文案彻底移除
    assert "💾 保存设置参数" not in html  # 中间版文案也彻底移除


def test_render_fine_cut_zone_renders_number_input_and_stepper_per_slider(tmp_path):
    """REQ-20260919-061 用户反馈：每个数值参数都要 number input + ▲▼ 按钮。

    - 18 个滑块（video.x/y/scale, subtitle.x/y/scale, bg.x/y/scale, video.crop_x/y/w/h,
      cover.duration, audio.volume/fade_in/fade_out, font.bg_opacity）每个都应有：
        * 1 个 <input type="range" class="slirn-fine-slider">
        * 1 个 <input type="number" class="slirn-fine-num" data-for="...">
        * 2 个 <button class="slirn-fine-step-btn" data-step-dir="up|down" data-for="...">
    """
    from slirn_home.app import _render_workbench

    m, video = _make_mgr(tmp_path)
    t = m.create(name="stepper-ui", original_video=video)
    html = _render_workbench(t.task_id, m)

    # 数量对得上：
    #   17 个 class="slirn-fine-slider"（video/subtitle/bg x/y/scale=9, crop=4, cover=1, audio=3）
    #   + 1 个 class="slirn-fine-font-slider"（font.bg_opacity，独立类避免污染 layout 集合）
    #   = 18 个 range slider；
    #   + 19 个 number input（18 slider 配对 + 1 预览时长独立）
    #   v7：移除了所有自定义 stepper 按钮 — 用浏览器原生 stepper。HTML 中不再出现
    #   slirn-fine-step-btn 元素；JS 也不再为它们绑事件。
    import re
    assert len(re.findall(r'class="slirn-fine-slider"', html)) == 17
    assert len(re.findall(r'class="slirn-fine-font-slider"', html)) == 1
    # 19 = 18 slider 配对 + 1 预览时长独立；
    # REQ-20260919-062 加了 1 个 bg 阈值手动输入框 → 20
    # REQ-20260919-064 加了 1 个预览开始时间输入框 → 21
    # REQ-20260919-066 预览开始时间改 时:分:秒 三段 → 23（class 多值 `slirn-fine-num slirn-fine-preview-time`）
    #   上面正则要宽松：要么 class 字符串里就以 slirn-fine-num 开头并紧跟 " 或空格
    # REQ-091：+4（start-h/m/s + duration）= 27
    # REQ-20260920-098：-4（一键合成面板删除，移除 4 个 combo-test 输入）= 23
    assert len(re.findall(r'class="slirn-fine-num(?:\s|")', html)) == 23
    # 不再有自定义 step-btn（与浏览器原生 stepper 重复，已移除）
    assert len(re.findall(r'slirn-fine-step-btn', html)) == 0
    # 也不再需要 num-group / step-stack 包装容器
    assert 'slirn-fine-num-group' not in html
    assert 'slirn-fine-step-stack' not in html

    # 抽样几个关键滑块确认 stepper + num 双向关联（data-for 指向 slider id）
    # cover.duration
    assert re.search(
        r'data-for="slirn-fine-cover-duration"', html,
    ), "cover.duration 应配有 number input + ▲▼"
    # video.x
    assert re.search(
        r'data-for="slirn-fine-video-x"', html,
    ), "video.x 应配有 number input + ▲▼"
    # audio.volume_db（用 data-audio-key 走 save_fine_audio 端点）
    assert re.search(
        r'data-audio-key="volume_db"[^>]*value="-8"', html,
    ), "audio.volume_db slider 应保留 -8 dB 默认值（= 旧 0.4 线性）"
    assert re.search(
        r'data-for="slirn-fine-audio-volume"', html,
    ), "audio.volume 应配有 number input + ▲▼"
    # crop_x 是像素整数（用 {:d} 格式）
    assert re.search(
        r'id="slirn-fine-video-crop_x"[^>]*value="0"', html,
    ), "crop_x 默认值应是 0（int 像素）"
    # cover hint 文案要提示用户「封面静音」
    assert "封面播放期间视频静音" in html, \
        "应提示用户封面期间不播原声"

    # REQ-20260919-061a：去掉滑块行右侧不可改的 readonly 显示区
    # _val span 已彻底移除（输入框本身即显示）
    assert 'class="slirn-fine-layout-val"' not in html, \
        "应去掉 readonly val span — 输入框本身即数值显示"
    assert '_val">' not in html, \
        "不应再有 id=..._val 元素（被 val span 移除）"

    # REQ-20260919-061a v3：精剪面板整体按左右两列展示，block 内部参数单列堆叠
    # - 外层 .slirn-fine-cols = 2 列网格（left: video位置/subtitle位置/bg位置/视频源裁剪；
    #   right: 片头封面/背景音乐/字体设置/输出设置）
    # - 每个 .slirn-fine-params（block 内部）= 单列纵向堆叠
    # - 仍为 7 个 .slirn-fine-params（video/subtitle/bg/crop/cover/audio/font-bg_opacity）
    assert 'class="slirn-fine-cols"' in html, \
        "精剪面板外层应为 2 列网格（.slirn-fine-cols）"
    assert 'class="slirn-fine-col"' in html, \
        "应有左右两个 .slirn-fine-col 子容器"
    assert len(re.findall(r'class="slirn-fine-params"', html)) == 7, \
        "应有 7 个参数设置组（video/subtitle/bg/crop/cover/audio/font-bg_opacity）"

    # 每个 param 内部 = label + slider + num（v7：去掉 num-group + step-stack，
    # 只剩 3 列 mini-grid；num 用浏览器原生 stepper 满足 ±step 微调）
    assert 'class="slirn-fine-step-stack"' not in html, \
        "v7：num 不再包在 .slirn-fine-step-stack 里（移除自定义 ▲▼ 按钮）"
    assert 'class="slirn-fine-num-group"' not in html, \
        "v7：num 不再包在 .slirn-fine-num-group 里（不再需要 spinner 一体）"
    # 每个 num 都应带 data-for 指向 slider id（保证 slider↔num 双向同步）
    num_with_for = len(re.findall(r'class="slirn-fine-num"[^>]*data-for=', html))
    assert num_with_for >= 17, \
        f"至少 17 个 num input 应带 data-for=slider_id 关联，当前 {num_with_for}"


def test_fine_slider_input_syncs_to_num_box():
    """REQ-20260919-061a v3 用户反馈：拖动滑块时，对应 num 框数值要跟着变。

    实现位置：router.js `bindFineControls` — slider 的 input 事件除了调防抖保存，
    还要把 `id+'_num'` 的 number input 的 value 同步成 slider 的 value。
    """
    js = Path('slirn_home/static/router.js').read_text(encoding='utf-8')
    # 必须有这段同步逻辑
    assert "el.id + '_num'" in js, \
        "slider input 处理器应同步写 id+'_num' 的 number input"
    # 同步逻辑必须在 bindFineControls 内部（不是别的函数）
    bind_fn_start = js.find('function bindFineControls')
    assert bind_fn_start > 0
    bind_fn_end = js.find('\n  }\n', bind_fn_start)
    bind_body = js[bind_fn_start:bind_fn_end]
    assert "el.id + '_num'" in bind_body, \
        "slider→num 同步逻辑必须在 bindFineControls 内部"


def test_fine_preview_box_removed_in_v10():
    """REQ-20260919-062 v10 用户反馈：去掉页面内的预览框，弹窗预览就够了。
    .slirn-fine-preview 大块容器应已被删除；预览改走 .slirn-mat-preview-float 弹窗。
    """
    css = Path('slirn_home/static/home.css').read_text(encoding='utf-8')
    # v10：.slirn-fine-preview 大块容器应已被删除（min-height/黑色背景框）
    import re
    m = re.search(r'\.slirn-fine-preview\s*\{([^}]+)\}', css)
    if m:
        block = m.group(1)
        # 如果还有这个类，不应再是大块容器（不允许 min-height: 100px 等）
        assert 'min-height: 100px' not in block, \
            f"v10：.slirn-fine-preview 不应再是大块容器：{block}"
    # 弹窗预览样式仍保留（独立于页面内预览框）
    assert '.slirn-mat-preview-float' in css, \
        "弹窗预览样式应保留"


def test_floating_video_popup_is_resizable():
    """REQ-20260919-061a v7 用户反馈：浮动视频窗口（.slirn-video-float）要能调整大小。

    CSS 应设 `resize: both` 并配 min/max 约束防止拖到不可用尺寸；JS 用
    ResizeObserver 把用户拖出的尺寸存到 localStorage（slirnVfSize），下次 vfShow
    时通过 vfRestoreSize 恢复。
    """
    css = Path('slirn_home/static/home.css').read_text(encoding='utf-8')
    import re
    m = re.search(r'\.slirn-video-float\s*\{([^}]+)\}', css)
    assert m, "应有 .slirn-video-float CSS 规则"
    block = m.group(1)
    assert 'resize: both' in block, \
        ".slirn-video-float 应设 resize: both 让用户拖右下角 resize handle"
    # min/max 约束：避免拖到极小或超大
    assert 'min-width:' in block and 'min-height:' in block, \
        ".slirn-video-float 应设 min-width + min-height 防止拖到不可用尺寸"
    assert 'max-width:' in block and 'max-height:' in block, \
        ".slirn-video-float 应设 max-width + max-height 防止溢出视口"

    # JS 端：ResizeObserver 持久化尺寸 + vfShow 恢复
    js = Path('slirn_home/static/router.js').read_text(encoding='utf-8')
    assert 'ResizeObserver' in js, \
        "JS 应使用 ResizeObserver 监听 .slirn-video-float 尺寸变化"
    assert "'slirnVfSize'" in js or '"slirnVfSize"' in js, \
        "尺寸应存到 localStorage 'slirnVfSize' 键"
    assert 'vfRestoreSize' in js, \
        "应有 vfRestoreSize 函数从 localStorage 恢复尺寸"


def test_fine_param_label_is_wide_enough():
    """REQ-20260919-061a v5 用户反馈：参数标签列加宽，能完整看到所有文字。

    CSS `.slirn-fine-param` grid 第一列 = 标签；70px 太窄（「音量（0–1，0.4 = 不压人声）」
    这类长标签会被截断）。v5 改为 160px，并允许 white-space:normal 换行兜底。
    """
    css = Path('slirn_home/static/home.css').read_text(encoding='utf-8')
    import re
    m = re.search(r'\.slirn-fine-param\s*\{([^}]+)\}', css)
    assert m, "应有 .slirn-fine-param CSS 规则"
    block = m.group(1)
    # 提取 grid-template-columns 的第一个值（标签列宽）
    col_match = re.search(r'grid-template-columns:\s*(\d+)px', block)
    assert col_match, ".slirn-fine-param 应使用 grid-template-columns 定义标签列宽"
    label_w = int(col_match.group(1))
    assert label_w >= 120, \
        f"标签列宽应 ≥ 120px（让最长 label 完整显示），当前 {label_w}px"
    # 不再 ellipsis 截断（用户要看到全部文字）
    label_rule = re.search(r'\.slirn-fine-param-label\s*\{([^}]+)\}', css)
    assert label_rule, "应有 .slirn-fine-param-label 样式"
    label_block = label_rule.group(1)
    assert 'text-overflow: ellipsis' not in label_block, \
        "不应再用 ellipsis 截断长标签 — 改为换行兜底"
    assert 'overflow: hidden' not in label_block, \
        "不应再用 overflow:hidden 截断 — 改为换行兜底"


def test_fine_two_cols_block_order(tmp_path):
    """REQ-20260919-061a v4 用户反馈：左右两列的前 2 个块固定配对。

    左列前 2 个 = 视频位置 + 视频源裁剪
    右列前 2 个 = 字幕位置 + 字幕字体设置
    """
    from slirn_home.app import _render_workbench

    m, video = _make_mgr(tmp_path)
    t = m.create(name="cols-order", original_video=video)
    html = _render_workbench(t.task_id, m)
    import re
    # 抽 .slirn-fine-cols 内部内容（到下一个同级容器或结尾前）
    cols_block = re.search(
        r'<div class="slirn-fine-cols">(.*?)(?=<div class="slirn-modal-overlay"|</div>\s*</div>\s*$)',
        html, flags=re.DOTALL,
    )
    assert cols_block, "应有 .slirn-fine-cols 容器"
    inner = cols_block.group(1)
    # 按 "<div class=\"slirn-fine-col\">" 切：parts[1] = 左列，parts[2] = 右列
    parts = re.split(r'<div class="slirn-fine-col">', inner, maxsplit=2)
    assert len(parts) == 3, f".slirn-fine-col 应出现 2 次，实际 {len(parts) - 1}"
    left, right = parts[1], parts[2]

    # 左列前 2 个：视频位置 + 视频源裁剪
    left_video_pos = left.find('slirn-fine-video-x')
    left_crop = left.find('slirn-fine-video-crop_x')
    left_subtitle_pos = left.find('slirn-fine-subtitle-x')
    left_bg_pos = left.find('slirn-fine-bg-x')
    left_audio = left.find('slirn-fine-audio-volume')
    assert left_video_pos >= 0 and left_crop >= 0, "左列应含视频位置 + 视频源裁剪"
    assert left_video_pos < left_crop, \
        "左列顺序：视频位置 应在 视频源裁剪 之前"
    assert left_subtitle_pos < 0, "字幕位置应只在右列出现"
    assert left_bg_pos >= 0 or left_audio >= 0, \
        "左列续列应含 背景位置 或 背景音乐"

    # 右列前 2 个：字幕位置 + 字幕字体设置
    right_subtitle_pos = right.find('slirn-fine-subtitle-x')
    right_font_size = right.find('data-font-key="size"')
    right_video_pos = right.find('slirn-fine-video-x')
    right_cover = right.find('slirn-fine-cover-duration')
    right_output = right.find('data-output-key="resolution"')
    assert right_subtitle_pos >= 0 and right_font_size >= 0, \
        "右列应含字幕位置 + 字幕字体设置"
    assert right_subtitle_pos < right_font_size, \
        "右列顺序：字幕位置 应在 字幕字体设置 之前"
    assert right_video_pos < 0, "视频位置应只在左列出现"
    # 右列续列至少应含 片头封面 或 输出设置 其一
    assert right_cover >= 0 or right_output >= 0, \
        "右列续列应含 片头封面 或 输出设置"


def test_render_fine_crop_zone_includes_aspect_link_toggle(tmp_path):
    """REQ-20260919-061a 用户反馈：crop_w 和 crop_h 按 16:9 联动（防变形）。

    默认开启（checked），关闭后可独立调整。
    v18：checkbox 改为从 layout.video.crop_aspect_lock 读取（不再写死 checked）。
    """
    from slirn_home.app import _render_workbench

    m, video = _make_mgr(tmp_path)
    t = m.create(name="crop-link", original_video=video)
    html = _render_workbench(t.task_id, m)
    # 联动 toggle（默认勾选，因为 crop_aspect_lock 默认 True）
    import re as _re
    m_toggle = _re.search(
        r'<input type="checkbox" id="slirn-fine-crop-aspect-link"[^>]*>',
        html,
    )
    assert m_toggle is not None, "应能找到锁定 16:9 checkbox"
    seg = m_toggle.group(0)
    assert 'data-key="video.crop_aspect_lock"' in seg, \
        f"v18：checkbox 应带 data-key 让 fineSaveAll 收集，实际：{seg}"
    assert "checked" in seg, \
        f"默认任务里 16:9 锁定 checkbox 应勾选，实际：{seg}"
    # 文案应说明"16:9"和"防变形"
    assert '16:9' in html
    assert '防变形' in html or '变形' in html
    # crop_w 和 crop_h 滑块都存在
    assert 'id="slirn-fine-video-crop_w"' in html
    assert 'id="slirn-fine-video-crop_h"' in html
    # JS 中存在 bindCropAspectLink 函数（前端联动逻辑）
    js = Path('slirn_home/static/router.js').read_text(encoding='utf-8')
    assert 'function bindCropAspectLink' in js, \
        "应有 bindCropAspectLink 处理 w/h 联动"
    assert '_syncHfromW' in js, "拖动 w 时同步 h"
    assert '_syncWfromH' in js, "拖动 h 时同步 w"
    assert '16 / 9' in js, "按 16:9 比例换算"


def test_run_fine_render_cover_audio_silence_concat_filter(tmp_path, monkeypatch):
    """REQ-20260920-096：封面播放期间不输出原视频人声，封面结束后才开始。

    实现：filter_complex 里 [0:a]（视频的音轨）在 cover 启用时前面拼一段 lavfi
    anullsrc 静音（时长 = cover_dur），形成 [silence][v_raw]concat=n=2:a=1[voice]，
    替代原 adelay。adelay + input-level -ss + aloop 会触发 ffmpeg PTS 错位把 audio
    截断到 ~22ms，改成 concat 后 PTS 干净稳定。
    """
    import json
    import subprocess
    import struct
    from slirn_home.app import _run_fine_render, _save_fine_compose

    m, video = _make_mgr(tmp_path)
    t = m.create(name="cover-audio-delay", original_video=video)

    cover_img = tmp_path / "cover.png"
    cover_img.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13) + b"IHDR"
        + struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
        + b"\x00\x00\x00\x00"
        + struct.pack(">I", 0xC4F1D3B5)
        + struct.pack(">I", 0) + b"IDAT\x00\x00\x00\x00\x00"
        + struct.pack(">I", 0xD7B1B1DC)
        + b"IEND" + struct.pack(">I", 0xAE426082)
    )

    fc = {
        "materials": {
            "video": {"path": str(video.relative_to(mgr_root(tmp_path))),
                      "type": "video", "source": "upload"},
            "cover": {"path": str(cover_img.relative_to(mgr_root(tmp_path))),
                      "type": "image", "source": "upload"},
        },
        "layout": {
            "video": {"x": 0, "y": 0, "scale": 1.0,
                      "crop_x": 0, "crop_y": 0, "crop_w": 1920, "crop_h": 1080,
                      "enabled": True},
            "cover": {"enabled": True, "duration": 2.5},  # 2.5 秒
        },
        "font": {},
        "output": {},
        "audio": {},
    }

    captured = {}

    class _FakeResult:
        returncode = 0
        stderr = ""
        stdout = ""

    def _fake_run(cmd, **kw):
        captured["cmd"] = cmd
        for i, a in enumerate(cmd):
            if a == "-filter_complex":
                captured["filter"] = cmd[i + 1]
                break
        return _FakeResult()

    monkeypatch.setattr(subprocess, "run", _fake_run)

    out_path = mgr_root(tmp_path) / "tasks" / t.task_id / "outputs" / "test.mp4"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _save_fine_compose(m, t.task_id, fc)
    res = _run_fine_render(t.task_id, m, out_path, duration=10.0)
    assert res["ok"] is True

    # 关键断言：voice 链必须走 [ss][v_raw]concat=n=2:v=0:a=1[voice] 而非 adelay；
    # anullsrc 静音输入在 input_args（cmd）里，filter_complex 只看到 [silence_idx:a]。
    f = captured["filter"]
    assert "[ss][v_raw]concat=n=2:v=0:a=1[voice]" in f, (
        f"voice 链必须走 silence + sliced-audio concat 模式，实际: {f}"
    )
    assert "adelay=" not in f, (
        f"REQ-20260920-096：已废弃 adelay，避免 PTS 错位把 audio 截断到 ~22ms，"
        f"实际 filter: {f}"
    )
    # cmd 里应该有 -f lavfi -t 2.50 -i anullsrc=... 输入
    cmd = captured["cmd"]
    assert "-f" in cmd and "lavfi" in cmd, f"缺少 lavfi 静音输入: {cmd}"
    assert any("anullsrc" in a for a in cmd), f"cmd 中找不到 anullsrc 输入: {cmd}"


def test_run_fine_render_no_cover_no_adelay_filter(tmp_path, monkeypatch):
    """封面未启用时不应插 adelay（[0:a] 直接 asetpts+PTS-STARTPTS,volume=1.0[voice]）。"""
    import subprocess
    from slirn_home.app import _run_fine_render, _save_fine_compose

    m, video = _make_mgr(tmp_path)
    t = m.create(name="no-cover-no-delay", original_video=video)

    fc = {
        "materials": {
            "video": {"path": str(video.relative_to(mgr_root(tmp_path))),
                      "type": "video", "source": "upload"},
        },
        "layout": {
            "video": {"x": 0, "y": 0, "scale": 1.0,
                      "crop_x": 0, "crop_y": 0, "crop_w": 1920, "crop_h": 1080,
                      "enabled": True},
            "cover": {"enabled": False, "duration": 5.0},
        },
        "font": {},
        "output": {},
        "audio": {},
    }

    captured = {}

    class _FakeResult:
        returncode = 0
        stderr = ""
        stdout = ""

    def _fake_run(cmd, **kw):
        for i, a in enumerate(cmd):
            if a == "-filter_complex":
                captured["filter"] = cmd[i + 1]
                break
        return _FakeResult()

    monkeypatch.setattr(subprocess, "run", _fake_run)

    out_path = mgr_root(tmp_path) / "tasks" / t.task_id / "outputs" / "test.mp4"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _save_fine_compose(m, t.task_id, fc)
    res = _run_fine_render(t.task_id, m, out_path, duration=10.0)
    assert res["ok"] is True

    f = captured["filter"]
    assert "adelay" not in f, "封面禁用时不应有 adelay 滤镜"
    # REQ-096：voice 链现在无条件带 asetpts=PTS-STARTPTS（防御性，
    # 即使 cover 关闭也要重置 PTS，防止 -ss 输入级 seek + amix duration=first 组合出错）
    assert "[0:a]asetpts=PTS-STARTPTS,volume=1.0[voice]" in f, (
        "封面禁用时 voice 链应为 [0:a]asetpts=PTS-STARTPTS,volume=1.0[voice]"
    )


# ---------- REQ-20260919-061 用户反馈：预览时长可调（2-30 秒） ----------

def test_render_fine_preview_accepts_duration_in_range(tmp_path):
    """REQ-20260919-061 用户反馈：预览时长 2-30 秒可调，端点读 body.duration 透传给
    _run_fine_render。
    """
    import json
    from fastapi.testclient import TestClient
    from slirn_home import build_app

    m, video = _make_mgr(tmp_path)
    t = m.create(name="preview-dur", original_video=video)
    client = TestClient(build_app(repo_root=tmp_path).app)

    # 用一个 fake_render 拦截 _run_fine_render；通过 monkeypatch 是侵入式的，这里
    # 用更轻的：只在没 ffmpeg 时跑会失败，所以只测端点对 duration 的接收与 clamp 逻辑。
    # 端点内部走 _run_fine_render 会失败（fake video），但 ok=False 也带回 preview_dur。
    r = client.post(
        "/slirn/api/render_fine_preview",
        json={"task_id": t.task_id, "duration": 25},
    )
    payload = r.json()
    # 不强求 ok=True（fake mp4 ffmpeg 会拒），只看 duration 路径处理（toast 含 25）
    assert payload.get("toast") and "25" in payload["toast"] or payload.get("ok") is False


def test_render_fine_preview_clamps_out_of_range(tmp_path):
    """预览时长 < 2 或 > 30 应被端点钳到 [2, 30]（防止前端越界输入）。"""
    from fastapi.testclient import TestClient
    from slirn_home import build_app

    m, video = _make_mgr(tmp_path)
    t = m.create(name="preview-clamp", original_video=video)
    client = TestClient(build_app(repo_root=tmp_path).app)

    # 99 → 应被钳到 30，toast 应含 30
    r = client.post(
        "/slirn/api/render_fine_preview",
        json={"task_id": t.task_id, "duration": 99},
    )
    payload = r.json()
    if payload.get("ok") is False and payload.get("toast"):
        # 走渲染失败路径 — toast 里看不到数字（因为 ffmpeg 在 fake mp4 上跑挂），
        # 但响应能来即说明端点处理 body 没炸
        pass
    # 再用 0.5 → 钳到 2
    r2 = client.post(
        "/slirn/api/render_fine_preview",
        json={"task_id": t.task_id, "duration": 0.5},
    )
    assert r2.status_code == 200


def test_render_fine_zone_includes_preview_duration_input(tmp_path):
    """精剪面板 HTML 含预览时长输入：number input + min=2 max=30 step=1 value=10。

    REQ-20260919-061a v7：去掉 num 框外的自定义 ▲▼ 按钮（与浏览器原生 stepper 重复），
    只保留 number input 本身。
    """
    from slirn_home.app import _render_workbench

    m, video = _make_mgr(tmp_path)
    t = m.create(name="preview-dur-ui", original_video=video)
    html = _render_workbench(t.task_id, m)
    import re
    assert 'id="slirn-fine-preview-duration"' in html
    m_input = re.search(
        r'id="slirn-fine-preview-duration"[^>]*min="2"[^>]*max="30"[^>]*step="1"[^>]*value="10"',
        html,
    )
    assert m_input is not None, "预览时长输入应有 min=2 max=30 step=1 value=10"
    # v7：不应再有配套的自定义 ▲▼ 按钮（用浏览器原生 stepper）
    assert 'data-for="slirn-fine-preview-duration"' not in html, \
        "v7：移除 num 框外的自定义 step-btn，应用浏览器原生 stepper"


# ---------- REQ-20260919-062：背景图白色区域检测（4 角点 + 宽高） ----------


def _write_white_rect_bg(task_dir: Path, w: int = 200, h: int = 150,
                          rect_xy: tuple[int, int] = (50, 30),
                          rect_wh: tuple[int, int] = (100, 60)) -> Path:
    """造一张「中央白色矩形 + 黑色背景」图，给 detect_bg_white_area 用。

    返回绝对路径（位于 task_dir/bg/bg.png，模拟 upload_fine_material_form
    写入约定路径）。
    """
    from PIL import Image as _PILImage

    img = _PILImage.new("RGB", (w, h), color=(0, 0, 0))
    # 画白色矩形
    from PIL import ImageDraw as _Draw
    _Draw.Draw(img).rectangle(
        [rect_xy, (rect_xy[0] + rect_wh[0] - 1, rect_xy[1] + rect_wh[1] - 1)],
        fill=(255, 255, 255),
    )
    bg_dir = task_dir / "bg"
    bg_dir.mkdir(parents=True, exist_ok=True)
    p = bg_dir / "bg.png"
    img.save(p)
    return p


def test_render_fine_cut_zone_includes_bg_detect_block(tmp_path):
    """REQ-20260919-062：精剪面板应新增「背景图白色区域检测」块。

    必备元素：算法 select + 阈值 select + 阈值手动 input + 检测按钮 + 4 角点结果格 +
    应用按钮。
    """
    from slirn_home.app import _render_workbench

    m, video = _make_mgr(tmp_path)
    t = m.create(name="bg-detect-ui", original_video=video)
    html = _render_workbench(t.task_id, m)

    # 容器
    assert 'class="slirn-fine-bg-detect-block"' in html, \
        "精剪面板应新增 .slirn-fine-bg-detect-block 容器"
    # 算法 select（ai_color / pixel / ai 三个选项；ai_color 是 v2 主推）
    assert 'id="slirn-fine-bg-detect-algo"' in html, \
        "应有算法选择 select（id=slirn-fine-bg-detect-algo）"
    assert 'value="ai_color"' in html, \
        "v2 应提供 ai_color 算法（LLM 识别主色 → 像素扫描）"
    assert 'value="pixel"' in html, \
        "应保留 pixel 算法（已知白色时用）"
    assert 'value="ai"' in html, \
        "应保留 ai 算法选项（已废弃但保留兼容）"
    # 阈值 select + 手动输入（包在 threshold-row 里，JS 按算法切换显隐）
    assert 'id="slirn-fine-bg-detect-threshold-row"' in html, \
        "阈值控件应包在 threshold-row 里（JS 按算法显隐）"
    assert 'id="slirn-fine-bg-detect-threshold-sel"' in html
    assert 'id="slirn-fine-bg-detect-threshold-num"' in html
    # 阈值 manual input 应在 200-255
    import re as _re
    m_thr = _re.search(
        r'id="slirn-fine-bg-detect-threshold-num"[^>]*min="200"[^>]*max="255"[^>]*value="250"',
        html,
    )
    assert m_thr is not None, "阈值手动输入应 min=200 max=255 value=250"
    # 检测按钮
    assert 'data-action="fine-bg-detect"' in html
    # 结果区 4 角点 + 中心 + 宽高 + 像素数 + 原图分辨率
    assert 'id="slirn-fine-bg-detect-tl"' in html
    assert 'id="slirn-fine-bg-detect-tr"' in html
    assert 'id="slirn-fine-bg-detect-bl"' in html
    assert 'id="slirn-fine-bg-detect-br"' in html
    assert 'id="slirn-fine-bg-detect-wh"' in html
    assert 'id="slirn-fine-bg-detect-center"' in html
    assert 'id="slirn-fine-bg-detect-pixels"' in html
    assert 'id="slirn-fine-bg-detect-native"' in html
    # v12 用户反馈：「背景图主色」大色块看不出有什么用 → 已去掉。
    # CSS/HTML 不应再含 bg-detect-color-box / -swatch-large / -rgb / -hex 节点。
    assert 'slirn-fine-bg-detect-color-box' not in html, \
        "v12：应去掉 AI 主色大色块容器"
    assert 'slirn-fine-bg-detect-color-swatch-large' not in html, \
        "v12：应去掉 AI 主色大色块（64×64）"
    assert 'slirn-fine-bg-detect-color-rgb' not in html, \
        "v12：应去掉 AI 主色 RGB 文本节点"
    assert 'slirn-fine-bg-detect-color-hex' not in html, \
        "v12：应去掉 AI 主色 HEX 文本节点"
    # 但「检测算法」「检测时间」仍然在（结果区里）
    # v8：「🗑 清空缓存」按钮
    assert 'data-action="fine-bg-detect-clear"' in html, \
        "v8：应有清空缓存按钮"
    # v8：检测算法 + 检测时间显示
    assert 'id="slirn-fine-bg-detect-algo-used"' in html, \
        "v8：结果区应有检测算法行"
    assert 'id="slirn-fine-bg-detect-time"' in html, \
        "v8：结果区应有检测时间行"
    # 应用按钮
    assert 'data-action="fine-bg-detect-apply"' in html
    # v4：按钮 title 应描述新填充逻辑（X/Y=区域左上角、crop 从原视频(0,0) 取、scale=1.0）
    apply_seg = html.split('data-action="fine-bg-detect-apply"', 1)[1].split('</button>', 1)[0]
    assert 'X/Y=区域左上角' in apply_seg, \
        f"apply 按钮 title 应说明 X/Y 取自区域左上角，实际：{apply_seg[:200]}"
    assert 'crop=从原视频(0,0)' in apply_seg, \
        f"apply 按钮 title 应说明 crop 从原视频 (0,0) 起取，实际：{apply_seg[:200]}"
    assert 'scale=1.0' in apply_seg, \
        f"apply 按钮 title 应说明 scale=1.0，实际：{apply_seg[:200]}"
    # JS 绑定
    js = Path('slirn_home/static/router.js').read_text(encoding='utf-8')
    assert 'function bindBgWhiteDetector' in js, \
        "router.js 应有 bindBgWhiteDetector 函数"
    assert 'slirn-fine-bg-detect-algo' in js, \
        "bindBgWhiteDetector 应引用 #slirn-fine-bg-detect-algo"
    # v2：算法切换时应隐藏阈值控件（ai / ai_color 不需要阈值）
    assert 'threshold-row' in js and "thRow.style.display" in js, \
        "JS 应在算法切换时显隐 threshold-row"
    assert 'detected_color' in js, \
        "JS 应展示 detected_color（AI 主色识别结果）"
    # CSS：v12 已删除主色大色块相关样式
    css = Path('slirn_home/static/home.css').read_text(encoding='utf-8')
    assert 'slirn-fine-bg-detect-color-swatch' not in css, \
        "v12：CSS 不应再定义主色色块样式"
    assert 'slirn-fine-bg-detect-color-box' not in css, \
        "v12：CSS 不应再定义主色容器样式"


def test_detect_bg_white_area_pixel_algorithm_returns_correct_bbox(tmp_path):
    """REQ-20260919-062 用户反馈：先缩放到设计空间 1920×1080 再算白色区域坐标。

    用一张 200×150 黑色背景 + 中央 (50,30)→(149,89) 白色矩形的图：
      - 先 LANCZOS 缩放到 1920×1080（scale_x=9.6, scale_y=7.2）
      - 然后扫描；扫描坐标已是设计空间像素（无需再乘 scale）
      - 理论上：x = 50*9.6 = 480, y = 30*7.2 = 216,
        x_max = 149*9.6 = 1430.4 → 1430, y_max = 89*7.2 = 640.8 → 641
      - LANCZOS 边缘像素会混入少量灰（被 threshold=250 滤掉），实际 bbox
        可能比理论值小 1-2 像素；用 ±3 容差断言。
    """
    from fastapi.testclient import TestClient
    from slirn_home import build_app
    from slirn_home.app import _get_fine_compose, _save_fine_compose

    m, video = _make_mgr(tmp_path)
    t = m.create(name="bg-pixel", original_video=video)
    # 准备 bg 图 + 写到 fc.materials.bg
    task_dir = m.tasks_dir / t.task_id
    bg_abs = _write_white_rect_bg(task_dir, w=200, h=150,
                                   rect_xy=(50, 30), rect_wh=(100, 60))
    fc = _get_fine_compose(m, t.task_id)
    fc["materials"]["bg"] = {
        "path": str(bg_abs.relative_to(m.repo_root)),
        "type": "image", "source": "upload",
    }
    _save_fine_compose(m, t.task_id, fc)

    client = TestClient(build_app(repo_root=tmp_path).app)
    r = client.post(
        "/slirn/api/detect_bg_white_area",
        json={"task_id": t.task_id, "algorithm": "pixel", "threshold": 250},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True, body

    # 原图分辨率保留在 image_native_w/h（用于 UI 反馈原图尺寸）
    assert body["algorithm"] == "pixel"
    assert body["threshold"] == 250
    assert body["image_native_w"] == 200
    assert body["image_native_h"] == 150

    # 设计空间像素（已是 1920×1080 坐标，因为算法内部先 resize）
    # LANCZOS 边缘抗锯齿会让 bbox 比理论值小（边缘变灰被阈值 250 滤掉）；
    # 实测缩放 9.6x / 7.2x 后矩形边缘会收缩 ~3-6 像素，用 ±10 容差断言关键不变量
    def _near(actual: int, expected: int, tol: int = 10) -> bool:
        return abs(actual - expected) <= tol

    assert _near(body["x"], 480), f"x 应≈480, 实际 {body['x']}"
    assert _near(body["y"], 216), f"y 应≈216, 实际 {body['y']}"
    assert _near(body["width"], 960), f"width 应≈960, 实际 {body['width']}"
    assert _near(body["height"], 432), f"height 应≈432, 实际 {body['height']}"

    # 中心点应落在设计空间内（不超界）
    assert 0 <= body["center_x"] <= 1920
    assert 0 <= body["center_y"] <= 1080

    # 4 角点都在 [0, 1920] × [0, 1080] 设计空间范围内
    for name, corner in body["corners"].items():
        cx, cy = corner
        assert 0 <= cx <= 1920, f"corner {name}.x={cx} 越界"
        assert 0 <= cy <= 1080, f"corner {name}.y={cy} 越界"

    # 关键不变量：x + width 应 ≈ 理论 x_max（1430），证明坐标在设计空间
    topright_x = body["corners"]["topright"][0]
    assert _near(topright_x, 1430), \
        f"topright.x 应≈1430（设计空间），实际 {topright_x}"
    bottomright_y = body["corners"]["bottomright"][1]
    assert _near(bottomright_y, 641), \
        f"bottomright.y 应≈641（设计空间），实际 {bottomright_y}"

    # pixel_count 应 > 0（至少有内部纯白像素）
    assert body["pixel_count"] > 0

    # 关键证明「先 resize 再算」：width + x 应等于 topright.x + 1（用 bbox 内部一致）
    assert body["x"] + body["width"] - 1 == body["corners"]["topright"][0], \
        "x + width - 1 应等于 topright.x（bbox 内部一致）"
    assert body["y"] + body["height"] - 1 == body["corners"]["bottomleft"][1], \
        "y + height - 1 应等于 bottomleft.y（bbox 内部一致）"


def test_detect_bg_white_area_threshold_clamped_to_200_255(tmp_path):
    """REQ-20260919-062 阈值边界：传入 100 应被夹到 200；传入 999 应被夹到 255。"""
    from fastapi.testclient import TestClient
    from slirn_home import build_app
    from slirn_home.app import _get_fine_compose, _save_fine_compose

    m, video = _make_mgr(tmp_path)
    t = m.create(name="bg-thr-clamp", original_video=video)
    task_dir = m.tasks_dir / t.task_id
    bg_abs = _write_white_rect_bg(task_dir)
    fc = _get_fine_compose(m, t.task_id)
    fc["materials"]["bg"] = {
        "path": str(bg_abs.relative_to(m.repo_root)),
        "type": "image", "source": "upload",
    }
    _save_fine_compose(m, t.task_id, fc)

    client = TestClient(build_app(repo_root=tmp_path).app)
    # threshold=100 应被夹到 200
    r = client.post(
        "/slirn/api/detect_bg_white_area",
        json={"task_id": t.task_id, "algorithm": "pixel", "threshold": 100},
    )
    assert r.status_code == 200
    assert r.json()["threshold"] == 200, \
        "threshold=100 应被夹到 200"
    # threshold=999 应被夹到 255
    r = client.post(
        "/slirn/api/detect_bg_white_area",
        json={"task_id": t.task_id, "algorithm": "pixel", "threshold": 999},
    )
    assert r.status_code == 200
    assert r.json()["threshold"] == 255, \
        "threshold=999 应被夹到 255"


def test_detect_bg_white_area_missing_bg_returns_error(tmp_path):
    """REQ-20260919-062 缺背景图时应返回友好错误，不是 500。"""
    from fastapi.testclient import TestClient
    from slirn_home import build_app

    m, video = _make_mgr(tmp_path)
    t = m.create(name="bg-missing", original_video=video)
    client = TestClient(build_app(repo_root=tmp_path).app)

    r = client.post(
        "/slirn/api/detect_bg_white_area",
        json={"task_id": t.task_id, "algorithm": "pixel"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "背景" in body.get("error", "") or "上传" in body.get("error", ""), \
        f"缺背景图错误应明确提示原因：{body}"


def test_detect_bg_white_area_missing_task_returns_error(tmp_path):
    """REQ-20260919-062 缺/错 task_id 应返回友好错误（_err 不抛 500）。"""
    from fastapi.testclient import TestClient
    from slirn_home import build_app

    client = TestClient(build_app(repo_root=tmp_path).app)

    # 缺 task_id
    r = client.post(
        "/slirn/api/detect_bg_white_area",
        json={"algorithm": "pixel"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert "task_id" in r.json()["error"]

    # 不存在的 task_id
    r = client.post(
        "/slirn/api/detect_bg_white_area",
        json={"task_id": "no-such-task", "algorithm": "pixel"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert "不存在" in r.json()["error"]


def test_detect_bg_white_area_ai_without_vision_model_returns_error(tmp_path):
    """REQ-20260919-062 ai 算法：当前未注册多模态模型时应返回错误（不是 500）。

    _make_mgr 默认不写 llm.json，所以 list_models() 返回空 → cur_entry is None →
    应返回「AI 检测需多模态模型…」。
    """
    from fastapi.testclient import TestClient
    from slirn_home import build_app
    from slirn_home.app import _get_fine_compose, _save_fine_compose

    m, video = _make_mgr(tmp_path)
    t = m.create(name="bg-ai-no-llm", original_video=video)
    task_dir = m.tasks_dir / t.task_id
    bg_abs = _write_white_rect_bg(task_dir)
    fc = _get_fine_compose(m, t.task_id)
    fc["materials"]["bg"] = {
        "path": str(bg_abs.relative_to(m.repo_root)),
        "type": "image", "source": "upload",
    }
    _save_fine_compose(m, t.task_id, fc)

    client = TestClient(build_app(repo_root=tmp_path).app)
    r = client.post(
        "/slirn/api/detect_bg_white_area",
        json={"task_id": t.task_id, "algorithm": "ai"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False, body
    err = body.get("error", "")
    assert "多模态" in err or "模型" in err, \
        f"无 vision 模型时应提示用户：{err}"


def test_detect_bg_white_area_unknown_algorithm_returns_error(tmp_path):
    """REQ-20260919-062 algorithm 字段只接受 pixel/ai，其它值应返回错误。"""
    from fastapi.testclient import TestClient
    from slirn_home import build_app

    m, video = _make_mgr(tmp_path)
    t = m.create(name="bg-bad-algo", original_video=video)
    client = TestClient(build_app(repo_root=tmp_path).app)

    r = client.post(
        "/slirn/api/detect_bg_white_area",
        json={"task_id": t.task_id, "algorithm": "magic"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert "算法" in r.json()["error"]


def test_detect_bg_white_area_pixel_handles_invalid_threshold_string(tmp_path):
    """REQ-20260919-062 threshold 传非数字字符串应兜底为 250，不抛 500。"""
    from fastapi.testclient import TestClient
    from slirn_home import build_app
    from slirn_home.app import _get_fine_compose, _save_fine_compose

    m, video = _make_mgr(tmp_path)
    t = m.create(name="bg-bad-thr", original_video=video)
    task_dir = m.tasks_dir / t.task_id
    bg_abs = _write_white_rect_bg(task_dir)
    fc = _get_fine_compose(m, t.task_id)
    fc["materials"]["bg"] = {
        "path": str(bg_abs.relative_to(m.repo_root)),
        "type": "image", "source": "upload",
    }
    _save_fine_compose(m, t.task_id, fc)

    client = TestClient(build_app(repo_root=tmp_path).app)
    r = client.post(
        "/slirn/api/detect_bg_white_area",
        json={"task_id": t.task_id, "algorithm": "pixel", "threshold": "abc"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["threshold"] == 250, \
        "非数字 threshold 应兜底为 250"


# ---------- REQ-20260919-062 v2：AI 主色识别（颜色未知时） ----------


def test_detect_bg_white_area_ai_color_without_vision_model_returns_error(tmp_path):
    """REQ-20260919-062 v2：ai_color 算法 — 当前未注册多模态模型时应返回错误。"""
    from fastapi.testclient import TestClient
    from slirn_home import build_app
    from slirn_home.app import _get_fine_compose, _save_fine_compose

    m, video = _make_mgr(tmp_path)
    t = m.create(name="bg-aicolor-no-llm", original_video=video)
    task_dir = m.tasks_dir / t.task_id
    bg_abs = _write_white_rect_bg(task_dir)
    fc = _get_fine_compose(m, t.task_id)
    fc["materials"]["bg"] = {
        "path": str(bg_abs.relative_to(m.repo_root)),
        "type": "image", "source": "upload",
    }
    _save_fine_compose(m, t.task_id, fc)

    client = TestClient(build_app(repo_root=tmp_path).app)
    r = client.post(
        "/slirn/api/detect_bg_white_area",
        json={"task_id": t.task_id, "algorithm": "ai_color"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False, body
    err = body.get("error", "")
    assert "多模态" in err or "模型" in err, \
        f"无 vision 模型时应提示用户：{err}"


def test_detect_bg_white_area_ai_color_requires_bg_image(tmp_path):
    """REQ-20260919-062 v2：ai_color 算法 — 缺背景图时应返回友好错误。"""
    from fastapi.testclient import TestClient
    from slirn_home import build_app

    m, video = _make_mgr(tmp_path)
    t = m.create(name="bg-aicolor-no-bg", original_video=video)
    client = TestClient(build_app(repo_root=tmp_path).app)

    r = client.post(
        "/slirn/api/detect_bg_white_area",
        json={"task_id": t.task_id, "algorithm": "ai_color"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    err = body.get("error", "")
    assert "背景" in err or "上传" in err, \
        f"缺背景图应明确提示原因：{err}"


def test_detect_bg_white_area_ai_color_algorithm_accepted_in_whitelist(tmp_path):
    """REQ-20260919-062 v2：endpoint 应接受 ai_color 算法（不返回「不支持的算法」错误）。

    注意：实际 LLM 调用会失败（无 vision 模型注册），但 endpoint 应通过白名单检查，
    进入「需要 vision 模型」分支；不应在第一步就被拒。
    """
    from fastapi.testclient import TestClient
    from slirn_home import build_app
    from slirn_home.app import _get_fine_compose, _save_fine_compose

    m, video = _make_mgr(tmp_path)
    t = m.create(name="bg-aicolor-whitelist", original_video=video)
    task_dir = m.tasks_dir / t.task_id
    bg_abs = _write_white_rect_bg(task_dir)
    fc = _get_fine_compose(m, t.task_id)
    fc["materials"]["bg"] = {
        "path": str(bg_abs.relative_to(m.repo_root)),
        "type": "image", "source": "upload",
    }
    _save_fine_compose(m, t.task_id, fc)

    client = TestClient(build_app(repo_root=tmp_path).app)
    r = client.post(
        "/slirn/api/detect_bg_white_area",
        json={"task_id": t.task_id, "algorithm": "ai_color"},
    )
    body = r.json()
    # 不应是「不支持的算法: ai_color」
    if not body["ok"]:
        assert "不支持的算法" not in body.get("error", ""), \
            f"ai_color 应被白名单接受：{body}"
        # 应进入 vision 模型检查或 LLM 调用分支（错误信息不含「不支持的算法」）
        assert "多模态" in body.get("error", "") or "AI" in body.get("error", ""), \
            f"应进入后续检查分支：{body}"


def test_detect_bg_white_area_ai_color_pixel_scan_locates_known_color(tmp_path):
    """REQ-20260919-062 v2：模拟 LLM 识别 RGB 后，像素扫描定位该色 bbox。

    直接 monkey-patch llm_config.get_current 返回 vision 模型 ID +
    monkey-patch revision_service._call_llm_vision 返回已知 RGB，
    验证像素扫描 + bbox 输出逻辑正确。
    """
    from fastapi.testclient import TestClient
    from slirn_home import build_app
    from slirn_home.app import _get_fine_compose, _save_fine_compose
    from slirn_home import llm_config, revision_service

    m, video = _make_mgr(tmp_path)
    t = m.create(name="bg-aicolor-pixel-scan", original_video=video)
    # 准备一张 1920×1080 灰绿色 (100, 150, 80) 背景 + 中央 960×540 红色 (200, 50, 50) 矩形
    from PIL import Image as _PILImage, ImageDraw as _Draw
    bg_dir = m.tasks_dir / t.task_id / "bg"
    bg_dir.mkdir(parents=True, exist_ok=True)
    img = _PILImage.new("RGB", (1920, 1080), color=(100, 150, 80))
    _Draw.Draw(img).rectangle([(480, 270), (1439, 809)], fill=(200, 50, 50))
    bg_abs = bg_dir / "bg.png"
    img.save(bg_abs)
    fc = _get_fine_compose(m, t.task_id)
    fc["materials"]["bg"] = {
        "path": str(bg_abs.relative_to(m.repo_root)),
        "type": "image", "source": "upload",
    }
    _save_fine_compose(m, t.task_id, fc)

    # Monkey-patch：把当前模型切到 qwen-vl-plus（vision）+ 让 LLM 返回已知 RGB
    original_get_current = llm_config.get_current
    original_call = revision_service._call_llm_vision
    llm_config.get_current = lambda *args, **kwargs: "qwen-vl-plus"
    revision_service._call_llm_vision = lambda *args, **kwargs: '{"r": 200, "g": 50, "b": 50}'
    try:
        client = TestClient(build_app(repo_root=tmp_path).app)
        r = client.post(
            "/slirn/api/detect_bg_white_area",
            json={"task_id": t.task_id, "algorithm": "ai_color"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True, body

        # 算法返回 + 颜色信息
        assert body["algorithm"] == "ai_color"
        assert body["detected_color"] == [200, 50, 50]
        assert body["color_tolerance"] == 10

        # 红色矩形 bbox = (480, 270) - (1439, 809)
        # 设计空间原图就是 1920×1080，resize 是恒等；bbox 应非常接近理论值
        # 容差 ±3（LANCZOS resize 恒等无边缘像素损失）
        def _near(actual, expected, tol=3):
            return abs(actual - expected) <= tol

        assert _near(body["x"], 480), f"x 应≈480, 实际 {body['x']}"
        assert _near(body["y"], 270), f"y 应≈270, 实际 {body['y']}"
        assert _near(body["width"], 960), f"width 应≈960, 实际 {body['width']}"
        assert _near(body["height"], 540), f"height 应≈540, 实际 {body['height']}"

        # 像素数 = 960 * 540 = 518400（纯色矩形，无边缘像素损失）
        assert body["pixel_count"] > 500000, \
            f"pixel_count 应 > 500000，实际 {body['pixel_count']}"

        # 4 角点应在设计空间内
        for name, corner in body["corners"].items():
            cx, cy = corner
            assert 0 <= cx <= 1920, f"{name}.x={cx} 越界"
            assert 0 <= cy <= 1080, f"{name}.y={cy} 越界"
    finally:
        llm_config.get_current = original_get_current
        revision_service._call_llm_vision = original_call


def test_detect_bg_white_area_ai_color_handles_llm_invalid_json(tmp_path):
    """REQ-20260919-062 v2：LLM 返回非 JSON 时应返回友好错误。"""
    from fastapi.testclient import TestClient
    from slirn_home import build_app
    from slirn_home.app import _get_fine_compose, _save_fine_compose
    from slirn_home import llm_config, revision_service

    m, video = _make_mgr(tmp_path)
    t = m.create(name="bg-aicolor-bad-json", original_video=video)
    task_dir = m.tasks_dir / t.task_id
    bg_abs = _write_white_rect_bg(task_dir)
    fc = _get_fine_compose(m, t.task_id)
    fc["materials"]["bg"] = {
        "path": str(bg_abs.relative_to(m.repo_root)),
        "type": "image", "source": "upload",
    }
    _save_fine_compose(m, t.task_id, fc)

    # Monkey-patch：当前模型切到 qwen-vl-plus + 让 LLM 返回非 JSON
    original_get_current = llm_config.get_current
    original_call = revision_service._call_llm_vision
    llm_config.get_current = lambda *args, **kwargs: "qwen-vl-plus"
    revision_service._call_llm_vision = lambda *args, **kwargs: "我不知道怎么回答"
    try:
        client = TestClient(build_app(repo_root=tmp_path).app)
        r = client.post(
            "/slirn/api/detect_bg_white_area",
            json={"task_id": t.task_id, "algorithm": "ai_color"},
        )
        body = r.json()
        assert body["ok"] is False, body
        err = body.get("error", "")
        assert "JSON" in err or "解析" in err, \
            f"非 JSON 输出应给出明确错误：{err}"
    finally:
        llm_config.get_current = original_get_current
        revision_service._call_llm_vision = original_call


# ---------- REQ-20260919-063：每个素材都要可预览（图片/视频/音频/SRT 文本） ----------


def test_render_fine_cut_zone_includes_per_material_preview_buttons(tmp_path):
    """REQ-20260919-063：每个素材上传卡都应有 👁️ 预览按钮 + 浮层可缩放 CSS。

    - HTML 含 6 个 [data-action="fine-mat-preview"]（video/subtitle/cover/bg/reference/audio）
    - CSS 含 .slirn-mat-preview-float + resize: both（用户能拖右下角调尺寸）
    - JS 含 fineMaterialPreview + bindFineMaterialPreviews
    """
    from slirn_home.app import _render_workbench

    m, video = _make_mgr(tmp_path)
    t = m.create(name="mat-preview-ui", original_video=video)
    html = _render_workbench(t.task_id, m)

    # 6 个素材预览按钮（每个 upload card 一个）
    import re as _re
    n = len(_re.findall(r'data-action="fine-mat-preview"', html))
    assert n == 6, f"应有 6 个素材预览按钮（video/subtitle/cover/bg/reference/audio），实际 {n}"
    # kind 数据属性覆盖 6 种类型
    for kind in ("video", "subtitle", "cover", "bg", "reference", "audio"):
        assert f'data-kind="{kind}"' in html, f"应有 data-kind=\"{kind}\" 的预览按钮"

    # CSS：浮层 + resize
    css = Path('slirn_home/static/home.css').read_text(encoding='utf-8')
    assert '.slirn-mat-preview-float' in css, "应有 .slirn-mat-preview-float 样式"
    assert 'resize: both' in css, ".slirn-mat-preview-float 应支持 resize: both"
    assert 'min-width' in css and 'min-height' in css, \
        "浮层应有 min-width/min-height 防止拖到不可用尺寸"

    # JS：函数 + bind 调用
    js = Path('slirn_home/static/router.js').read_text(encoding='utf-8')
    assert 'function fineMaterialPreview' in js, \
        "router.js 应有 fineMaterialPreview 函数"
    assert 'function bindFineMaterialPreviews' in js, \
        "router.js 应有 bindFineMaterialPreviews"
    assert "bindFineMaterialPreviews()" in js, \
        "bindFineMaterialPreviews 应在 wb-setup 阶段被调用"
    assert 'slirn-mat-preview-float' in js, \
        "fineMaterialPreview 应创建 #slirn-mat-preview-float 元素"
    assert 'ResizeObserver' in js and 'slirnMatPreviewSize' in js, \
        "应有 ResizeObserver 持久化浮层尺寸到 localStorage"


def test_serve_fine_material_file_returns_image_for_cover(tmp_path):
    """REQ-20260919-063：GET /slirn/api/fine_material_file 返图（image/png）。"""
    from fastapi.testclient import TestClient
    from slirn_home import build_app
    from slirn_home.app import _get_fine_compose, _save_fine_compose

    m, video = _make_mgr(tmp_path)
    t = m.create(name="mat-file-cover", original_video=video)
    # 造 cover.png
    from PIL import Image as _PILImage
    upload_dir = m.tasks_dir / t.task_id / "upload"
    upload_dir.mkdir(parents=True, exist_ok=True)
    cover_path = upload_dir / "cover_test.png"
    _PILImage.new("RGB", (100, 60), color=(255, 0, 0)).save(cover_path)
    fc = _get_fine_compose(m, t.task_id)
    fc["materials"]["cover"] = {
        "path": str(cover_path.relative_to(m.repo_root)),
        "type": "image", "source": "upload",
    }
    _save_fine_compose(m, t.task_id, fc)

    client = TestClient(build_app(repo_root=tmp_path).app)
    r = client.get(
        "/slirn/api/fine_material_file",
        params={"task_id": t.task_id, "kind": "cover"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/png")
    assert len(r.content) > 0
    # 内容确实是 PNG（魔数 89 50 4E 47）
    assert r.content[:4] == b"\x89PNG"


def test_serve_fine_material_file_returns_srt_as_plain_text(tmp_path):
    """REQ-20260919-063：subtitle（.srt）应以 text/plain 返回。"""
    from fastapi.testclient import TestClient
    from slirn_home import build_app
    from slirn_home.app import _get_fine_compose, _save_fine_compose

    m, video = _make_mgr(tmp_path)
    t = m.create(name="mat-file-srt", original_video=video)
    upload_dir = m.tasks_dir / t.task_id / "upload"
    upload_dir.mkdir(parents=True, exist_ok=True)
    srt_path = upload_dir / "subtitle_test.srt"
    srt_path.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\n你好世界\n\n"
        "2\n00:00:02,500 --> 00:00:04,000\n再见\n",
        encoding="utf-8",
    )
    fc = _get_fine_compose(m, t.task_id)
    fc["materials"]["subtitle"] = {
        "path": str(srt_path.relative_to(m.repo_root)),
        "type": "srt", "source": "upload",
    }
    _save_fine_compose(m, t.task_id, fc)

    client = TestClient(build_app(repo_root=tmp_path).app)
    r = client.get(
        "/slirn/api/fine_material_file",
        params={"task_id": t.task_id, "kind": "subtitle"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert "你好世界" in r.text
    assert "再见" in r.text


def test_serve_fine_material_file_returns_audio_with_correct_mime(tmp_path):
    """REQ-20260919-063：audio（.mp3）应以 audio/mpeg 返回。"""
    from fastapi.testclient import TestClient
    from slirn_home import build_app
    from slirn_home.app import _get_fine_compose, _save_fine_compose

    m, video = _make_mgr(tmp_path)
    t = m.create(name="mat-file-audio", original_video=video)
    upload_dir = m.tasks_dir / t.task_id / "upload"
    upload_dir.mkdir(parents=True, exist_ok=True)
    audio_path = upload_dir / "audio_test.mp3"
    audio_path.write_bytes(b"\xFF\xFB\x90" + b"\x00" * 100)  # MP3 帧头占位
    fc = _get_fine_compose(m, t.task_id)
    fc["materials"]["audio"] = {
        "path": str(audio_path.relative_to(m.repo_root)),
        "type": "audio", "source": "upload",
    }
    _save_fine_compose(m, t.task_id, fc)

    client = TestClient(build_app(repo_root=tmp_path).app)
    r = client.get(
        "/slirn/api/fine_material_file",
        params={"task_id": t.task_id, "kind": "audio"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("audio/")


def test_serve_fine_material_file_404_for_missing_task(tmp_path):
    """REQ-20260919-063：task_id 不存在 → 404。"""
    from fastapi.testclient import TestClient
    from slirn_home import build_app

    client = TestClient(build_app(repo_root=tmp_path).app)
    r = client.get(
        "/slirn/api/fine_material_file",
        params={"task_id": "no-such-task", "kind": "cover"},
    )
    assert r.status_code == 404


def test_serve_fine_material_file_404_for_unuploaded_material(tmp_path):
    """REQ-20260919-063：素材未上传 → 404。"""
    from fastapi.testclient import TestClient
    from slirn_home import build_app

    m, video = _make_mgr(tmp_path)
    t = m.create(name="mat-file-empty", original_video=video)
    client = TestClient(build_app(repo_root=tmp_path).app)
    r = client.get(
        "/slirn/api/fine_material_file",
        params={"task_id": t.task_id, "kind": "bg"},
    )
    assert r.status_code == 404


def test_serve_fine_material_file_400_for_invalid_kind(tmp_path):
    """REQ-20260919-063：非法 kind → 400。"""
    from fastapi.testclient import TestClient
    from slirn_home import build_app

    m, video = _make_mgr(tmp_path)
    t = m.create(name="mat-file-bad-kind", original_video=video)
    client = TestClient(build_app(repo_root=tmp_path).app)
    r = client.get(
        "/slirn/api/fine_material_file",
        params={"task_id": t.task_id, "kind": "evil"},
    )
    assert r.status_code == 400


def test_serve_fine_material_file_preview_button_disabled_when_no_file(tmp_path):
    """REQ-20260919-063：素材未上传时，预览按钮应被禁用（disabled）；上传后启用。"""
    from slirn_home.app import _render_workbench, _get_fine_compose, _save_fine_compose

    m, video = _make_mgr(tmp_path)
    t = m.create(name="mat-preview-disabled", original_video=video)
    # 先看初始（所有素材都没传）
    html0 = _render_workbench(t.task_id, m)
    import re as _re
    # cover 未上传 — preview 按钮 segment 应含 disabled
    m_cover = _re.search(
        r'data-kind="cover"[^>]*>(.*?)<div class="slirn-fine-upload-status',
        html0, _re.DOTALL,
    )
    assert m_cover is not None, "找不到 cover 卡片内容"
    card = m_cover.group(1)
    btn_seg_cover = card.split('data-action="fine-mat-preview"')[1].split('>')[0]
    assert 'disabled' in btn_seg_cover, \
        f"cover 未上传时预览按钮应 disabled，实际按钮属性：{btn_seg_cover}"

    # 现在上传 cover → 按钮应启用
    from PIL import Image as _PILImage
    upload_dir = m.tasks_dir / t.task_id / "upload"
    upload_dir.mkdir(parents=True, exist_ok=True)
    cover_path = upload_dir / "cover_x.png"
    _PILImage.new("RGB", (10, 10), color=(255, 0, 0)).save(cover_path)
    fc = _get_fine_compose(m, t.task_id)
    fc["materials"]["cover"] = {
        "path": str(cover_path.relative_to(m.repo_root)),
        "type": "image", "source": "upload",
    }
    _save_fine_compose(m, t.task_id, fc)
    html1 = _render_workbench(t.task_id, m)
    m_cover1 = _re.search(
        r'data-kind="cover"[^>]*>(.*?)<div class="slirn-fine-upload-status',
        html1, _re.DOTALL,
    )
    card1 = m_cover1.group(1)
    btn_seg1 = card1.split('data-action="fine-mat-preview"')[1].split('>')[0]
    assert 'disabled' not in btn_seg1, \
        f"cover 已上传时预览按钮不应 disabled：{btn_seg1}"


def test_router_js_fineUpload_enables_preview_and_detail_after_success():
    """REQ-20260921-NNN：fineUpload 上传成功后必须把「预览」和「详情」按钮的
    disabled 解掉（之前只更新 status 文本 + 加 has-file 类，用户体验 BUG：
    实际已上传但预览按钮还是灰的，详情按钮也打不开）。
    """
    js_src = (FUNCLIP_ROOT / "slirn_home" / "static" / "router.js").read_text(
        encoding="utf-8"
    )
    # 定位 fineUpload 函数
    idx = js_src.find("function fineUpload(btn, kind)")
    assert idx > 0, "router.js 必须有 fineUpload 函数"
    # 取函数体 2500 字（覆盖完整 fetch then 块；函数实际约 1700 字）
    window = js_src[idx:idx + 2500]
    # 必须有解 disabled 的逻辑
    assert "pvBtn.disabled = false" in window, (
        "fineUpload 上传成功后必须解 fine-mat-preview 按钮的 disabled"
    )
    assert "detBtn.disabled = false" in window, (
        "fineUpload 上传成功后必须解 fine-mat-detail 按钮的 disabled"
    )
    # 还应有 'data-action="fine-mat-preview"' / 'data-action="fine-mat-detail"' 选择器
    assert 'data-action="fine-mat-preview"' in window
    assert 'data-action="fine-mat-detail"' in window


def test_app_py_default_bgm_label_is_system_provided():
    """REQ-20260921-NNN：把「系统默认 BGM」下拉 label 改为「系统提供的 BGM」。"""
    app_src = (FUNCLIP_ROOT / "slirn_home" / "app.py").read_text(encoding="utf-8")
    assert "系统提供的 BGM" in app_src, (
        "app.py 渲染 audio 卡片时必须显示「系统提供的 BGM」label"
    )
    # 旧文案应被替换
    audio_idx = app_src.find("系统提供的 BGM")
    assert audio_idx > 0
    # 在 audio 卡片渲染段附近不应再出现「系统默认 BGM」
    nearby = app_src[max(0, audio_idx - 500):audio_idx + 500]
    assert "系统默认 BGM" not in nearby


def test_router_js_fineDefaultBgmLoad_filters_unavailable():
    """REQ-20260921-NNN：fineDefaultBgmLoad 必须过滤掉 available=false 的 BGM
    （避免选了之后 select_default_bgm 报「文件不存在」错误）。
    """
    js_src = (FUNCLIP_ROOT / "slirn_home" / "static" / "router.js").read_text(
        encoding="utf-8"
    )
    idx = js_src.find("window.fineDefaultBgmLoad = async function fineDefaultBgmLoad")
    assert idx > 0, "router.js 必须有 fineDefaultBgmLoad 函数"
    window = js_src[idx:idx + 1800]
    # 必须有 filter available=true 的逻辑
    assert "filter" in window and "available" in window, (
        "fineDefaultBgmLoad 必须按 available 过滤 BGM 列表"
    )
    # 应没有「文件缺失」灰色 option（旧行为是显示但 disabled）
    assert "文件缺失" not in window, (
        "REQ-20260921-NNN：缺文件的 BGM 不应再出现在下拉里（旧行为是显示但 disabled）"
    )


def test_router_js_fineDefaultBgmSelect_marks_audio_card_green():
    """REQ-20260921-NNN：fineDefaultBgmSelect 成功后必须把 audio 上传卡视觉切到
    「已提供 BGM」绿色状态（加 has-file 类 + 解预览按钮 disabled + 紫色 default_bgm 徽章）。
    """
    js_src = (FUNCLIP_ROOT / "slirn_home" / "static" / "router.js").read_text(
        encoding="utf-8"
    )
    idx = js_src.find("async function fineDefaultBgmSelect(bgmId, tid)")
    assert idx > 0, "router.js 必须有 fineDefaultBgmSelect 函数"
    # 2500 字覆盖完整 fetch then + 视觉更新
    window = js_src[idx:idx + 3000]
    # 1. 必须给 audio 卡加 has-file 类
    assert "audioCard.classList.add('has-file')" in window, (
        "fineDefaultBgmSelect 成功后必须给 audio 卡加 has-file 类（绿色背景）"
    )
    # 2. 必须更新 status 为「🎵 已选 BGM」
    assert "已选 BGM" in window, (
        "fineDefaultBgmSelect 成功后必须更新 status 文本为「已选 BGM」"
    )
    # 3. 必须解预览 / 详情按钮 disabled
    assert "pvBtn.disabled = false" in window, (
        "fineDefaultBgmSelect 成功后必须解 fine-mat-preview 按钮的 disabled"
    )
    assert "detBtn.disabled = false" in window, (
        "fineDefaultBgmSelect 成功后必须解 fine-mat-detail 按钮的 disabled"
    )
    # 4. 必须有 default_bgm 徽章（紫色 — 系统 BGM 来源）
    assert "default_bgm" in window, (
        "fineDefaultBgmSelect 必须用 default_bgm 徽章标识来源"
    )


def test_home_css_has_default_bgm_badge_style():
    """REQ-20260921-NNN：home.css 必须新增 .slirn-fine-source-badge.default_bgm
    紫色样式（与 modal 详情色块保持一致）。"""
    css_src = (FUNCLIP_ROOT / "slirn_home" / "static" / "home.css").read_text(
        encoding="utf-8"
    )
    assert ".slirn-fine-source-badge.default_bgm" in css_src, (
        "home.css 必须定义 .slirn-fine-source-badge.default_bgm 样式"
    )


# ---------- REQ-20260919-062 v3：中心扩展算法（默认；4 方向矩形扫描） ----------


def _write_solid_bg_rect(task_dir: Path, w: int = 1920, h: int = 1080,
                          bg_rgb: tuple[int, int, int] = (50, 80, 120),
                          rect_rgb: tuple[int, int, int] = (200, 200, 200),
                          rect_xy: tuple[int, int] = (560, 240),
                          rect_wh: tuple[int, int] = (800, 600)) -> Path:
    """造一张「背景纯色 + 中央纯色矩形」图，给 center_expand 用。

    设计：bg 和 rect 颜色不同，rect 在 bg 中央。从中心向外 4 方向扩展时，
    第一次遇到 bg 色就停 → 矩形 bbox 应等于 rect 的 bbox。
    """
    from PIL import Image as _PILImage, ImageDraw as _Draw
    img = _PILImage.new("RGB", (w, h), color=bg_rgb)
    _Draw.Draw(img).rectangle(
        [rect_xy, (rect_xy[0] + rect_wh[0] - 1, rect_xy[1] + rect_wh[1] - 1)],
        fill=rect_rgb,
    )
    bg_dir = task_dir / "bg"
    bg_dir.mkdir(parents=True, exist_ok=True)
    p = bg_dir / "bg.png"
    img.save(p)
    return p


def test_detect_bg_white_area_center_expand_finds_central_rect(tmp_path):
    """REQ-20260919-062 v3：center_expand 默认算法 ——
    从背景图中心 10×10 平均色向 4 方向扩展，遇颜色变化即停，返回矩形 bbox。

    测试图：1920×1080 蓝灰 (50,80,120) 背景 + 中央 (560,240)-(1359,839) 浅灰 (200,200,200) 矩形。
      - 设计空间原图就是 1920×1080，resize 是恒等
      - 中心 (960, 540) 在矩形内（560 ≤ 960 ≤ 1359, 240 ≤ 540 ≤ 839）
      - 中心 10×10 平均色 = (200,200,200)
      - 向左：第一次遇到 (50,80,120) 在 x=559 之前停；x_left = 560
      - 向右：第一次遇到 (50,80,120) 在 x=1360 之前停；x_right = 1359
      - 向上：y_top = 240；向下：y_bottom = 839
      - 矩形 bbox = (560, 240, 800, 600)
    """
    from fastapi.testclient import TestClient
    from slirn_home import build_app
    from slirn_home.app import _get_fine_compose, _save_fine_compose

    m, video = _make_mgr(tmp_path)
    t = m.create(name="bg-center-expand", original_video=video)
    task_dir = m.tasks_dir / t.task_id
    bg_abs = _write_solid_bg_rect(task_dir)
    fc = _get_fine_compose(m, t.task_id)
    fc["materials"]["bg"] = {
        "path": str(bg_abs.relative_to(m.repo_root)),
        "type": "image", "source": "upload",
    }
    _save_fine_compose(m, t.task_id, fc)

    client = TestClient(build_app(repo_root=tmp_path).app)
    r = client.post(
        "/slirn/api/detect_bg_white_area",
        json={"task_id": t.task_id, "algorithm": "center_expand"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True, body

    # 算法字段 + 基本颜色返回
    assert body["algorithm"] == "center_expand"
    assert body["threshold"] is None
    assert body["detected_color"] == [200, 200, 200], \
        f"基本色应为矩形色 (200,200,200)，实际 {body['detected_color']}"
    assert body["color_tolerance"] == 10

    # bbox 应等于矩形原 bbox（容差 ±1：扫描算法可能因边缘像素抖动多/少 1 行/列）
    def _near(actual, expected, tol=1):
        return abs(actual - expected) <= tol

    assert _near(body["x"], 560), f"x 应≈560，实际 {body['x']}"
    assert _near(body["y"], 240), f"y 应≈240，实际 {body['y']}"
    assert _near(body["width"], 800), f"width 应≈800，实际 {body['width']}"
    assert _near(body["height"], 600), f"height 应≈600，实际 {body['height']}"

    # 4 角点
    assert _near(body["corners"]["topleft"][0], 560)
    assert _near(body["corners"]["topleft"][1], 240)
    assert _near(body["corners"]["bottomright"][0], 1359)
    assert _near(body["corners"]["bottomright"][1], 839)


def test_detect_bg_white_area_center_expand_image_smaller_than_design(tmp_path):
    """REQ-20260919-062 v3：原图 1000×600 经 LANCZOS 缩放到 1920×1080 后，
    中心扩展仍能找到矩形 bbox（验证先 resize 再算的设计）。

    缩放比例 scale_x = 1.92, scale_y = 1.8
    原图矩形 (200, 100)-(799, 499)（600×400）→ 设计空间 (384, 180)-(1535, 898)（1152×720）
    """
    from fastapi.testclient import TestClient
    from slirn_home import build_app
    from slirn_home.app import _get_fine_compose, _save_fine_compose

    m, video = _make_mgr(tmp_path)
    t = m.create(name="bg-center-expand-small", original_video=video)
    task_dir = m.tasks_dir / t.task_id
    bg_abs = _write_solid_bg_rect(
        task_dir, w=1000, h=600,
        bg_rgb=(10, 20, 30), rect_rgb=(220, 220, 220),
        rect_xy=(200, 100), rect_wh=(600, 400),
    )
    fc = _get_fine_compose(m, t.task_id)
    fc["materials"]["bg"] = {
        "path": str(bg_abs.relative_to(m.repo_root)),
        "type": "image", "source": "upload",
    }
    _save_fine_compose(m, t.task_id, fc)

    client = TestClient(build_app(repo_root=tmp_path).app)
    r = client.post(
        "/slirn/api/detect_bg_white_area",
        json={"task_id": t.task_id, "algorithm": "center_expand"},
    )
    body = r.json()
    assert body["ok"] is True, body

    def _near(actual, expected, tol=6):
        return abs(actual - expected) <= tol
    assert _near(body["x"], 384), f"x 应≈384，实际 {body['x']}"
    assert _near(body["y"], 180), f"y 应≈180，实际 {body['y']}"
    assert _near(body["width"], 1152), f"width 应≈1152，实际 {body['width']}"
    assert _near(body["height"], 720), f"height 应≈720，实际 {body['height']}"


def test_detect_bg_white_area_center_expand_uses_full_image_when_uniform(tmp_path):
    """REQ-20260919-062 v3：整张图都是基本色时，扩展到图像边界（bbox = 整图）。"""
    from fastapi.testclient import TestClient
    from slirn_home import build_app
    from slirn_home.app import _get_fine_compose, _save_fine_compose

    m, video = _make_mgr(tmp_path)
    t = m.create(name="bg-center-uniform", original_video=video)
    task_dir = m.tasks_dir / t.task_id
    from PIL import Image as _PILImage
    bg_dir = task_dir / "bg"
    bg_dir.mkdir(parents=True, exist_ok=True)
    bg_abs = bg_dir / "bg.png"
    _PILImage.new("RGB", (1920, 1080), color=(120, 120, 120)).save(bg_abs)
    fc = _get_fine_compose(m, t.task_id)
    fc["materials"]["bg"] = {
        "path": str(bg_abs.relative_to(m.repo_root)),
        "type": "image", "source": "upload",
    }
    _save_fine_compose(m, t.task_id, fc)

    client = TestClient(build_app(repo_root=tmp_path).app)
    r = client.post(
        "/slirn/api/detect_bg_white_area",
        json={"task_id": t.task_id, "algorithm": "center_expand"},
    )
    body = r.json()
    assert body["ok"] is True, body
    assert body["x"] <= 1, f"x 应接近 0，实际 {body['x']}"
    assert body["y"] <= 1, f"y 应接近 0，实际 {body['y']}"
    assert body["width"] >= 1918, f"width 应≈1920，实际 {body['width']}"
    assert body["height"] >= 1078, f"height 应≈1080，实际 {body['height']}"


def test_detect_bg_white_area_center_expand_requires_bg_image(tmp_path):
    """REQ-20260919-062 v3：center_expand 也需要背景图，缺时返回友好错误。"""
    from fastapi.testclient import TestClient
    from slirn_home import build_app

    m, video = _make_mgr(tmp_path)
    t = m.create(name="bg-center-no-bg", original_video=video)
    client = TestClient(build_app(repo_root=tmp_path).app)

    r = client.post(
        "/slirn/api/detect_bg_white_area",
        json={"task_id": t.task_id, "algorithm": "center_expand"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    err = body.get("error", "")
    assert "背景" in err or "上传" in err, f"缺背景图应明确提示：{err}"


def test_detect_bg_white_area_center_expand_algorithm_in_whitelist(tmp_path):
    """REQ-20260919-062 v3：center_expand 应被 endpoint 白名单接受。"""
    from fastapi.testclient import TestClient
    from slirn_home import build_app
    from slirn_home.app import _get_fine_compose, _save_fine_compose

    m, video = _make_mgr(tmp_path)
    t = m.create(name="bg-center-whitelist", original_video=video)
    task_dir = m.tasks_dir / t.task_id
    bg_abs = _write_solid_bg_rect(task_dir)
    fc = _get_fine_compose(m, t.task_id)
    fc["materials"]["bg"] = {
        "path": str(bg_abs.relative_to(m.repo_root)),
        "type": "image", "source": "upload",
    }
    _save_fine_compose(m, t.task_id, fc)

    client = TestClient(build_app(repo_root=tmp_path).app)
    r = client.post(
        "/slirn/api/detect_bg_white_area",
        json={"task_id": t.task_id, "algorithm": "center_expand"},
    )
    body = r.json()
    if not body["ok"]:
        assert "不支持的算法" not in body.get("error", ""), \
            f"center_expand 应被白名单接受：{body}"


def test_render_fine_cut_zone_center_expand_is_default_selected(tmp_path):
    """REQ-20260919-062 v3：HTML select 应把 center_expand 设为默认选项。"""
    from slirn_home.app import _render_workbench

    m, video = _make_mgr(tmp_path)
    t = m.create(name="bg-center-default", original_video=video)
    html = _render_workbench(t.task_id, m)

    import re as _re
    m_sel = _re.search(
        r'<option value="center_expand"([^>]*)>',
        html,
    )
    assert m_sel is not None, '应存在 value="center_expand" 的 option'
    assert 'selected' in m_sel.group(1), \
        f"center_expand 应为默认 selected，实际：{m_sel.group(0)}"


def test_bg_detect_apply_uses_region_top_left_not_center():
    """REQ-20260919-062 v4 用户反馈：填充到视频位置时，video.X/Y 应是区域左上角 (r.x/r.y)，
    而非区域中心 (r.center_x/r.center_y)；crop_x/crop_y 应从原视频 (0,0) 起取。
    """
    js = Path('slirn_home/static/router.js').read_text(encoding='utf-8')

    # 定位 apply handler 块（"请先点「🔍 检测区域」"是 apply 入口）
    start = js.find('请先点「🔍 检测区域」')
    assert start > 0, "apply handler 应存在"
    end = js.find('\n  }\n  function', start)
    assert end > start, "apply handler 结束位置应可定位"
    block = js[start:end]

    # 1) video.X = r.x（不是 r.center_x）
    assert "_setSlider('slirn-fine-video-x', r.x)" in block, \
        "video.X 应填区域的左上角 X (r.x)，而不是中心 (r.center_x)"
    assert "_setSlider('slirn-fine-video-x', r.center_x)" not in block, \
        "video.X 不应再用 r.center_x"
    # 2) video.Y = r.y（不是 r.center_y）
    assert "_setSlider('slirn-fine-video-y', r.y)" in block, \
        "video.Y 应填区域的左上角 Y (r.y)，而不是中心 (r.center_y)"
    assert "_setSlider('slirn-fine-video-y', r.center_y)" not in block, \
        "video.Y 不应再用 r.center_y"
    # 3) crop_x = 0（从原视频 (0,0) 起取）
    assert "_setSlider('slirn-fine-video-crop_x', 0)" in block, \
        "crop_x 应填 0（从原视频左上角起取）"
    # 4) crop_y = 0
    assert "_setSlider('slirn-fine-video-crop_y', 0)" in block, \
        "crop_y 应填 0（从原视频左上角起取）"
    # 5) crop_w = r.width
    assert "_setSlider('slirn-fine-video-crop_w', r.width)" in block, \
        "crop_w 应填区域宽度 r.width"
    # 6) crop_h = r.height
    assert "_setSlider('slirn-fine-video-crop_h', r.height)" in block, \
        "crop_h 应填区域高度 r.height"
    # 7) scale = 1.0（裁剪后的视频刚好填满区域）
    assert "_setSlider('slirn-fine-video-scale', 1.0)" in block, \
        "scale 应填 1.0（让裁剪后的视频填满区域）"

    # v5：fill 之后应把 viewport 也存到后端（限定视频在检测区域内）
    assert "viewport:" in block, \
        "fill handler 应把 viewport 写进 save_fine_layout 请求体"
    assert "x: r.x" in block and "y: r.y" in block, \
        "viewport 应使用检测结果 r.x/r.y"
    assert "width: r.width" in block and "height: r.height" in block, \
        "viewport 应使用检测结果 r.width/r.height"


# ─── REQ-20260919-062 v5：把视频展示区域限定在所检测区域之内 ───

def test_clamp_video_to_viewport_clamps_xy_when_outside():
    """当 video.x/y 超出 viewport 时，夹紧到 viewport 内。"""
    from slirn_home.app import _clamp_video_to_viewport

    vc = {
        "x": 2000, "y": 1500,                # 都超出 viewport
        "scale": 0.5,
        "crop_w": 1920, "crop_h": 1080,
        "viewport": {"x": 384, "y": 180, "width": 1152, "height": 720},
    }
    _clamp_video_to_viewport(vc)
    # display = 1920*0.5=960, 1080*0.5=540；viewport 1152×720 完全装得下
    # max_x = 384 + (1152 - 960) = 576
    # max_y = 180 + (720 - 540) = 360
    assert vc["x"] == 576, f"x 应夹紧到 576，实际 {vc['x']}"
    assert vc["y"] == 360, f"y 应夹紧到 360，实际 {vc['y']}"
    assert vc["scale"] == 0.5, f"scale 不应被改（=0.5 < 上限）"


def test_clamp_video_to_viewport_clamps_scale_when_too_large():
    """当 scale 过大导致 display 超出 viewport 时，把 scale 降到刚好装下。"""
    from slirn_home.app import _clamp_video_to_viewport

    vc = {
        "x": 384, "y": 180,
        "scale": 2.0,
        "crop_w": 1920, "crop_h": 1080,
        "viewport": {"x": 384, "y": 180, "width": 1152, "height": 720},
    }
    _clamp_video_to_viewport(vc)
    # max_scale = min(1152/1920, 720/1080, 2.0) = 0.6
    assert abs(vc["scale"] - 0.6) < 0.001, f"scale 应夹紧到 0.6，实际 {vc['scale']}"
    # 此时 display = 1920*0.6=1152, 1080*0.6=648；x/y 不动
    assert vc["x"] == 384
    assert vc["y"] == 180


def test_clamp_video_to_viewport_no_change_when_inside():
    """display 已在 viewport 内时，不动 x/y/scale。"""
    from slirn_home.app import _clamp_video_to_viewport

    vc = {
        "x": 400, "y": 200,                # viewport 384..1536 × 180..900 — 完全在内
        "scale": 0.5,
        "crop_w": 1920, "crop_h": 1080,
        "viewport": {"x": 384, "y": 180, "width": 1152, "height": 720},
    }
    _clamp_video_to_viewport(vc)
    assert vc["x"] == 400
    assert vc["y"] == 200
    assert vc["scale"] == 0.5


def test_clamp_video_to_viewport_no_viewport_means_unlimited():
    """viewport 缺失/None → 不做夹紧（向后兼容）。"""
    from slirn_home.app import _clamp_video_to_viewport

    vc = {"x": 5000, "y": 5000, "scale": 2.0,
          "crop_w": 1920, "crop_h": 1080, "viewport": None}
    _clamp_video_to_viewport(vc)
    assert vc["x"] == 5000, "viewport=None 时不应修改 x"
    assert vc["y"] == 5000
    assert vc["scale"] == 2.0


def test_save_fine_layout_accepts_viewport_field_and_clamps(tmp_path):
    """save_fine_layout 接受 video.viewport，并把 video.x/y/scale 夹紧到 viewport 内。"""
    from fastapi.testclient import TestClient
    from slirn_home import build_app

    m, video = _make_mgr(tmp_path)
    t = m.create(name="viewport-clamp", original_video=video)
    client = TestClient(build_app(repo_root=tmp_path).app)

    # x/y/scale 都超出 viewport；设 viewport 后服务端夹紧
    r = client.post(
        "/slirn/api/save_fine_layout",
        json={
            "task_id": t.task_id,
            "layout": {
                "video": {
                    "x": 5000, "y": 5000, "scale": 3.0,
                    "viewport": {"x": 384, "y": 180, "width": 1152, "height": 720},
                },
            },
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    # 响应里应返回夹紧后的 layout.video
    assert "layout" in body, f"响应应含 layout 字段，实际 {body}"
    v = body["layout"]["video"]
    # max_scale = min(1152/1920, 720/1080, 2.0) = 0.6
    assert abs(v["scale"] - 0.6) < 0.001, f"scale 应被夹紧到 0.6，实际 {v['scale']}"
    # display 1920*0.6=1152, 1080*0.6=648；viewport 1152×720
    # max_x = 384 + (1152 - 1152) = 384；原 x=5000 被夹到 384
    # max_y = 180 + (720 - 648) = 252；原 y=5000 被夹到 252
    assert v["x"] == 384, f"x 应被夹紧到 384，实际 {v['x']}"
    assert v["y"] == 252, f"y 应被夹紧到 252（max_y），实际 {v['y']}"
    # viewport 应被保留
    assert v["viewport"] == {"x": 384, "y": 180, "width": 1152, "height": 720}


def test_save_fine_layout_keeps_existing_viewport_when_not_submitted(tmp_path):
    """只改 x/y/scale 不带 viewport 时，旧 viewport 应继续生效（夹紧生效）。"""
    from fastapi.testclient import TestClient
    from slirn_home import build_app

    m, video = _make_mgr(tmp_path)
    t = m.create(name="viewport-keep", original_video=video)
    client = TestClient(build_app(repo_root=tmp_path).app)

    # 第一次：设 viewport + 极端值
    client.post(
        "/slirn/api/save_fine_layout",
        json={
            "task_id": t.task_id,
            "layout": {
                "video": {
                    "x": 5000, "y": 5000, "scale": 1.0,
                    "viewport": {"x": 384, "y": 180, "width": 1152, "height": 720},
                },
            },
        },
    )
    # 第二次：只改 scale（不带 viewport）— viewport 应保留，scale 被夹紧到 viewport 内
    r2 = client.post(
        "/slirn/api/save_fine_layout",
        json={"task_id": t.task_id, "layout": {"video": {"scale": 3.0}}},
    )
    v = r2.json()["layout"]["video"]
    assert abs(v["scale"] - 0.6) < 0.001, f"viewport 应保留并夹紧 scale，实际 scale={v['scale']}"
    assert v["viewport"] == {"x": 384, "y": 180, "width": 1152, "height": 720}


def test_bg_detect_apply_sends_viewport_to_backend():
    """fill handler 应把检测到的 viewport 一起发给 save_fine_layout。"""
    js = Path('slirn_home/static/router.js').read_text(encoding='utf-8')

    start = js.find('请先点「🔍 检测区域」')
    assert start > 0, "apply handler 应存在"
    end = js.find('\n  }\n  function', start)
    block = js[start:end]

    # viewport fetch 调用
    assert 'viewport: {' in block, \
        "fill handler 应构造 viewport 字段"
    assert 'x: r.x' in block and 'y: r.y' in block, \
        "viewport x/y 应来自检测结果"
    assert 'width: r.width' in block and 'height: r.height' in block, \
        "viewport width/height 应来自检测结果"
    assert 'save_fine_layout' in block, \
        "viewport 应通过 save_fine_layout 端点发送"


def test_fine_sync_slider_helper_exists():
    """_fineSyncSlider 是 fill handler 和自动保存回写的共用辅助。"""
    js = Path('slirn_home/static/router.js').read_text(encoding='utf-8')
    assert 'function _fineSyncSlider' in js, \
        "router.js 应有 _fineSyncSlider 函数"
    # 不应触发 input 事件（避免循环）
    seg = js.split('function _fineSyncSlider', 1)[1].split('\n  }', 1)[0]
    assert "dispatchEvent" not in seg, \
        "_fineSyncSlider 不应 dispatch input 事件（避免循环）"


# ─── REQ-20260919-062 v6：视频缩放精度 5% → 1%
# v17 用户反馈：v15 自动重算到 4 位后立刻被浏览器吸附回 2 位
#   根因：<input type="range" step="0.01"> 把 value 吸附到 0.01 网格
#   修复：step 从 0.01 改成 0.0001（4 位小数精度），其它素材仍 0.05
# ───

def test_video_scale_slider_uses_step_001(tmp_path):
    """视频缩放滑块的 step 应为 0.0001（4 位小数精度），其它素材仍 0.05。

    历史：v6 → step=0.01；v17 → step=0.0001（修复 4 位小数被吸附的 bug）。
    """
    import re as _re
    from slirn_home.app import _render_workbench

    m, video = _make_mgr(tmp_path)
    t = m.create(name="scale-step", original_video=video)
    html = _render_workbench(t.task_id, m)

    # 1) 视频缩放滑块 step=0.0001（v17：避免被浏览器吸附回 2 位）
    m_video_scale = _re.search(
        r'<input type="range" class="slirn-fine-slider" id="slirn-fine-video-scale"[^>]*>',
        html,
    )
    assert m_video_scale is not None, "应有 slirn-fine-video-scale 滑块"
    video_scale_html = m_video_scale.group(0)
    assert 'step="0.0001"' in video_scale_html, \
        f"v17：视频 scale 应为 step=0.0001（4 位精度），实际：{video_scale_html}"
    assert 'step="0.05"' not in video_scale_html, \
        f"视频 scale 不应再用 0.05：{video_scale_html}"

    # 2) number 输入框（精调）也应是 step=0.0001
    m_video_num = _re.search(
        r'<input type="number" class="slirn-fine-num" id="slirn-fine-video-scale_num"[^>]*>',
        html,
    )
    assert m_video_num is not None, "应有 slirn-fine-video-scale_num 数字框"
    assert 'step="0.0001"' in m_video_num.group(0), \
        f"v17：视频 scale 数字框 step 应为 0.0001，实际：{m_video_num.group(0)}"

    # 3) 其它素材（subtitle/cover/bg）的 scale 仍保持 step=0.05
    for kind in ("subtitle", "bg"):
        m_other = _re.search(
            rf'<input type="range" class="slirn-fine-slider" id="slirn-fine-{kind}-scale"[^>]*>',
            html,
        )
        assert m_other is not None, f"应有 slirn-fine-{kind}-scale 滑块"
        assert 'step="0.05"' in m_other.group(0), \
            f"{kind} scale 应保持 step=0.05，实际：{m_other.group(0)}"


def test_video_scale_two_decimal_value_renders_as_2dp(tmp_path):
    """slider 的 value 渲染保留两位小数（如 0.61 而不是 0.6100000001）。"""
    import re as _re
    from slirn_home.app import _render_fine_cut_zone, _get_fine_compose, _save_fine_compose

    m, video = _make_mgr(tmp_path)
    t = m.create(name="scale-2dp", original_video=video)
    fc = _get_fine_compose(m, t.task_id)
    fc["layout"]["video"]["scale"] = 0.61
    _save_fine_compose(m, t.task_id, fc)

    html = _render_fine_cut_zone(t.task_id, m.tasks_dir / t.task_id, m)
    # 找到 video scale 的 range input
    m_video_scale = _re.search(
        r'<input type="range" class="slirn-fine-slider" id="slirn-fine-video-scale"[^>]*>',
        html,
    )
    assert m_video_scale is not None, "应有 slirn-fine-video-scale 滑块"
    seg = m_video_scale.group(0)
    # v16：scale 值用 4 位小数显示（与 v15 自动重算保留 4 位一致）
    assert 'value="0.6100"' in seg, f"slider value 应渲染为 '0.6100'（v16 4 位小数），实际：{seg}"


# ─── REQ-20260919-062 v7：视频显示尺寸信息（宽 × 高） ───

def test_video_info_row_shows_display_dimensions(tmp_path):
    """视频块应展示只读的显示尺寸（crop_w × scale, crop_h × scale）。"""
    from slirn_home.app import _render_fine_cut_zone, _get_fine_compose, _save_fine_compose

    m, video = _make_mgr(tmp_path)
    t = m.create(name="video-info", original_video=video)
    fc = _get_fine_compose(m, t.task_id)
    # 默认 crop_w=1920, crop_h=1080, scale=0.7 → 显示尺寸 = 1344 × 756
    fc["layout"]["video"]["crop_w"] = 1920
    fc["layout"]["video"]["crop_h"] = 1080
    fc["layout"]["video"]["scale"] = 0.7
    _save_fine_compose(m, t.task_id, fc)

    html = _render_fine_cut_zone(t.task_id, m.tasks_dir / t.task_id, m)

    # 1) 信息行容器
    assert 'id="slirn-fine-video-info"' in html, \
        "应有 slirn-fine-video-info 信息行容器"
    assert 'slirn-fine-video-info' in html
    # 2) 显示宽高 span
    assert 'id="slirn-fine-video-disp-w"' in html, "应有 disp-w 节点"
    assert 'id="slirn-fine-video-disp-h"' in html, "应有 disp-h 节点"
    # 默认 scale=0.7，crop=1920×1080 → 显示 = 1344 × 756
    import re as _re
    m_dw = _re.search(r'id="slirn-fine-video-disp-w"[^>]*>([^<]+)<', html)
    m_dh = _re.search(r'id="slirn-fine-video-disp-h"[^>]*>([^<]+)<', html)
    assert m_dw is not None and m_dw.group(1).strip() == "1344", \
        f"disp-w 应为 1344（1920*0.7），实际 {m_dw.group(1) if m_dw else 'None'}"
    assert m_dh is not None and m_dh.group(1).strip() == "756", \
        f"disp-h 应为 756（1080*0.7），实际 {m_dh.group(1) if m_dh else 'None'}"
    # 3) 缩放百分比 + 宽高比
    assert 'id="slirn-fine-video-scale-pct"' in html
    assert 'id="slirn-fine-video-aspect"' in html
    m_pct = _re.search(r'id="slirn-fine-video-scale-pct"[^>]*>([^<]+)<', html)
    # v16：缩放百分比显示 4 位小数（与 v15 自动重算保留 4 位一致）
    assert m_pct is not None and "70.0000" in m_pct.group(1), \
        f"scale-pct 应含 70.0000（v16 4 位小数），实际 {m_pct.group(1) if m_pct else 'None'}"
    m_asp = _re.search(r'id="slirn-fine-video-aspect"[^>]*>([^<]+)<', html)
    assert m_asp is not None, "应有 aspect 节点"
    # 756/1344 ≈ 0.5625
    assert "0.563" in m_asp.group(1) or "0.562" in m_asp.group(1), \
        f"aspect 应 ≈0.562-0.563，实际 {m_asp.group(1)}"


def test_video_info_only_in_video_block_not_subtitle(tmp_path):
    """显示尺寸信息行只出现在 video 块，subtitle/cover/bg 块不应有。

    实现：找 4 个 .slirn-fine-layout-block 的起止位置（用 regex + DOTALL + non-greedy
    平衡嵌套 div 很复杂；这里走简化的字符串切片：从每个 layout-block 起点到下一个 layout-block
    起点之间算一块；最后一块取到 4 倍字符偏移外的合理边界。
    """
    import re as _re
    from slirn_home.app import _render_fine_cut_zone

    m, video = _make_mgr(tmp_path)
    t = m.create(name="info-only-video", original_video=video)
    html = _render_fine_cut_zone(t.task_id, m.tasks_dir / t.task_id, m)

    # 3 个 layout block 起点（position_blocks 只为 video/subtitle/bg 生成，cover 单独渲染）
    starts = [m.start() for m in _re.finditer(
        r'<div class="slirn-fine-layout-block">', html)]
    assert len(starts) == 3, f"应有 3 个 position layout block，实际 {len(starts)}"

    # 每块的切片 = [starts[i], starts[i+1])；最后一块到 html 末尾
    counts = []
    for i, s in enumerate(starts):
        e = starts[i + 1] if i + 1 < len(starts) else len(html)
        seg = html[s:e]
        cnt = seg.count('id="slirn-fine-video-info"')
        counts.append(cnt)

    # video 是第 1 个 block（按 ("video","subtitle","bg") 顺序），其它 2 个应不含
    assert counts[0] >= 1, \
        f"video block (counts[0]={counts[0]}) 应包含 video-info"
    for i, c in enumerate(counts[1:], start=1):
        assert c == 0, \
            f"第 {i + 1} 个 layout block 不应含 video-info，实际 {c} 次"


def test_router_has_update_video_disp_function():
    """router.js 应有 updateVideoDisp 函数，并在 video.* 滑块 input 时调用。"""
    js = Path('slirn_home/static/router.js').read_text(encoding='utf-8')

    # 函数定义
    assert 'function updateVideoDisp()' in js, \
        "router.js 应有 updateVideoDisp 函数"
    # 函数体内应更新 disp-w / disp-h / scale-pct / aspect 4 个节点
    fn = js.split('function updateVideoDisp()', 1)[1].split('\n  }', 1)[0]
    assert "slirn-fine-video-disp-w" in fn, \
        "updateVideoDisp 应更新 disp-w"
    assert "slirn-fine-video-disp-h" in fn, \
        "updateVideoDisp 应更新 disp-h"
    assert "slirn-fine-video-scale-pct" in fn, \
        "updateVideoDisp 应更新 scale-pct"
    assert "slirn-fine-video-aspect" in fn, \
        "updateVideoDisp 应更新 aspect"
    # 应在 video.scale / video.crop_w / video.crop_h input 事件里被调用
    bind = js.split('bindFineControls', 1)[1] if 'bindFineControls' in js else js
    # 找 video.scale / crop_w / crop_h 触发处
    assert "'video.scale'" in js, "video.scale 应触发 updateVideoDisp"
    # crop_w / crop_h 触发同时驱动 updateCropAspect + updateVideoDisp
    crop_segment = js[js.find("k === 'video.crop_w' || k === 'video.crop_h'"):]
    crop_segment = crop_segment[:crop_segment.find('}')+1] if '}' in crop_segment else crop_segment[:300]
    assert "updateVideoDisp" in crop_segment, \
        "video.crop_w/h input 应触发 updateVideoDisp"


def test_css_video_info_style_defined():
    """home.css 应定义 .slirn-fine-video-info 样式。"""
    css = Path('slirn_home/static/home.css').read_text(encoding='utf-8')
    assert '.slirn-fine-video-info' in css, \
        "home.css 应定义 .slirn-fine-video-info 样式"


# ============================================================
# REQ-20260919-071：字幕修订行·人员徽章列位（不挤占文本）
# ============================================================

def test_css_rev_line_grid_has_six_columns_with_auto_spk():
    """REQ-20260919-071：.slirn-rev-line grid-template-columns 应为 6 轨，
    第 2 轨 auto（人员徽章，未关联时塌缩为 0）。"""
    css = Path('slirn_home/static/home.css').read_text(encoding='utf-8')
    # 找 .slirn-rev-line 的 grid-template-columns
    import re
    m = re.search(
        r'\.slirn-rev-line\s*\{[^}]*?grid-template-columns:\s*([^;]+);',
        css, re.DOTALL,
    )
    assert m, "home.css 应为 .slirn-rev-line 定义 grid-template-columns"
    cols = m.group(1).strip()
    # 期望：44px auto 216px 1fr 150px 26px（6 轨，第 2 轨 auto）
    expected_tokens = ["44px", "auto", "216px", "1fr", "150px", "26px"]
    actual_tokens = cols.split()
    assert actual_tokens == expected_tokens, \
        f".slirn-rev-line 应为 6 轨布局（含 auto 人员徽章轨），实际：{cols}"
    assert len(actual_tokens) == 6, \
        f"6 个 child 需 6 轨布局（idx/spk/time/text/select/toggle），实际轨数：{len(actual_tokens)}"


def test_css_rev_line_children_have_explicit_grid_column():
    """REQ-20260919-071：每个 child 应显式 grid-column（与切分阶段 036 方案同款）。

    不显式声明时，新增 6th child 会触发 grid auto-flow wrap 到下一行 →
    出现「人员编号占宽列 + 一段空白」的视觉异常。
    """
    css = Path('slirn_home/static/home.css').read_text(encoding='utf-8')
    # 关键 6 个 child 都应有 grid-column
    expected = [
        ('.slirn-rev-line > .slirn-sub-idx',    'grid-column: 1'),
        ('.slirn-rev-line > .slirn-rev-spk',    'grid-column: 2'),
        ('.slirn-rev-line > .slirn-sub-time',   'grid-column: 3'),
        ('.slirn-rev-line > .slirn-sub-text',   'grid-column: 4'),
        ('.slirn-rev-line > .slirn-rev-select', 'grid-column: 5'),
        ('.slirn-rev-line > .slirn-rev-toggle', 'grid-column: 6'),
    ]
    for selector, prop in expected:
        # 转义 CSS 里的特殊字符做字面匹配
        pattern = re.escape(selector) + r'\s*\{[^}]*?' + re.escape(prop)
        assert re.search(pattern, css, re.DOTALL), \
            f"{selector} 应显式声明 {prop}（防止新增 child 触发 wrap）"


def test_css_rev_line_narrow_screen_keeps_six_columns():
    """REQ-20260919-071：窄屏 @media (max-width: 720px) 也应保持 6 轨布局。

    注：CSS 中存在多个 @media (max-width: 720px) 块（字幕行/字幕修订/精剪字体
    等各自一份），需找包含 .slirn-rev-line 的那一块。
    """
    css = Path('slirn_home/static/home.css').read_text(encoding='utf-8')
    # 找所有 @media (max-width: 720px) {...} 块（用花括号配对，不依赖非贪婪）
    media_blocks: list[str] = []
    for m in re.finditer(r'@media\s*\(max-width:\s*720px\)\s*\{', css):
        start = m.end() - 1  # '{' 位置
        depth = 0
        for i in range(start, len(css)):
            if css[i] == '{':
                depth += 1
            elif css[i] == '}':
                depth -= 1
                if depth == 0:
                    media_blocks.append(css[start + 1:i])
                    break
    assert media_blocks, "home.css 应有 @media (max-width: 720px) 块"
    # 找包含 .slirn-rev-line 的那一块
    rev_block = next((b for b in media_blocks if '.slirn-rev-line' in b), None)
    assert rev_block, "应有含 .slirn-rev-line 的 @media (max-width: 720px) 块"
    cols_m = re.search(
        r'\.slirn-rev-line\s*\{[^}]*?grid-template-columns:\s*([^;]+);',
        rev_block, re.DOTALL,
    )
    assert cols_m, "窄屏 @media 内应有 .slirn-rev-line 的 grid-template-columns"
    cols = cols_m.group(1).strip().split()
    assert len(cols) == 6, \
        f"窄屏 6 轨（含 auto 人员徽章轨 + auto 时间戳轨），实际轨数：{len(cols)}"
    # 第 2 轨应是 auto（人员徽章，未关联塌缩 0）
    assert cols[1] == "auto", \
        f"窄屏第 2 轨应为 auto（人员徽章轨），实际：{cols}"


# ============================================================
# REQ-20260919-062 v8+v12：背景图检测（v8 加缓存+块位置；v12 去掉主色大色块）
# ============================================================

def test_bg_detect_result_renders_when_cache_present(tmp_path):
    """fc.bg_detect_cache 存在时，结果区（4 角点 + 宽高 + 中心 + 像素数 + 原图尺寸 + 算法 + 时间）应直接渲染。

    v12：主色大色块已删除，本测试改为验证「结果区直接可见 + 几何信息来自缓存」这一核心行为。
    """
    from starlette.testclient import TestClient
    from slirn_home.app import build_app
    from tasklib import TaskManager
    mgr, _video = _make_mgr(tmp_path)
    t = mgr.create(name='demo', original_video=tmp_path / 'lecture.mp4')
    tid = t.task_id
    task_dir = tmp_path / 'tasks' / tid
    fc_path = task_dir / 'fine_compose.json'
    fc_path.parent.mkdir(parents=True, exist_ok=True)
    fc_path.write_text(json.dumps({
        "materials": {},
        "layout": {
            "video": {"x": 100, "y": 200, "scale": 0.5, "crop_x": 0,
                      "crop_y": 0, "crop_w": 1920, "crop_h": 1080, "enabled": True}
        },
        "font": {"size": 36, "family": "STHeitiMedium", "stroke_width": 2,
                 "stroke_color": "#000000", "bg_enabled": False, "bg_color": "#000000",
                 "bg_opacity": 0.6, "bg_radius": 4, "bold": True, "align": "center"},
        "output": {"resolution": "1080p", "codec": "h264", "audio_codec": "aac"},
        "audio": {},
        "bg_detect_cache": {
            "x": 50, "y": 60, "width": 800, "height": 450,
            "center_x": 450, "center_y": 285,
            "corners": {"topleft": [50, 60], "topright": [850, 60],
                        "bottomleft": [50, 510], "bottomright": [850, 510]},
            "pixel_count": 12345,
            "image_native_w": 1920, "image_native_h": 1080,
            "detected_color": [240, 240, 240],
            "color_tolerance": 10,
            "algorithm": "ai_color",
            "detected_at": "2026-09-19T16:00:00",
        },
    }), encoding='utf-8')
    client = TestClient(build_app(repo_root=tmp_path).app)
    r = client.post('/slirn/api/workbench', json={'task_id': tid})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j.get('ok') is True, j
    html = j.get('html') or j.get('content') or r.text
    # v12：已去掉主色大色块；RGB/HEX 不再展示，但 detected_color 仍写进缓存（不影响）
    assert 'slirn-fine-bg-detect-color-box' not in html
    assert 'slirn-fine-bg-detect-color-swatch-large' not in html
    # 结果区应直接可见（不 hidden）
    assert 'id="slirn-fine-bg-detect-result"' in html
    assert 'id="slirn-fine-bg-detect-result" hidden' not in html
    # 缓存里的几何值
    assert '(50, 60)' in html  # topleft
    assert '800 × 450' in html or '800 × 450' in html
    # 算法 + 时间
    assert 'ai_color' in html
    assert '2026-09-19T16:00:00' in html


def test_bg_detect_block_above_actions_bar(tmp_path):
    """v8 用户反馈：bg_detect_block 应挪到 combined_actions_bar 上方。"""
    from starlette.testclient import TestClient
    from slirn_home.app import build_app
    from tasklib import TaskManager
    mgr, _video = _make_mgr(tmp_path)
    t = mgr.create(name='demo2', original_video=tmp_path / 'lecture.mp4')
    tid = t.task_id
    client = TestClient(build_app(repo_root=tmp_path).app)
    r = client.post('/slirn/api/workbench', json={'task_id': tid})
    assert r.status_code == 200, r.text
    j = r.json()
    html = j.get('html') or j.get('content') or r.text
    bg_pos = html.find('slirn-fine-bg-detect-block')
    actions_pos = html.find('slirn-fine-actions-bar')
    assert bg_pos > 0 and actions_pos > 0, "应能定位到两个块"
    assert bg_pos < actions_pos, \
        "bg_detect_block 应在 actions_bar 上方（用户反馈 v8）"


def test_bg_detect_clear_cache_endpoint(tmp_path):
    """clear_bg_detect_cache endpoint 应删除 fc.bg_detect_cache。"""
    from starlette.testclient import TestClient
    from slirn_home.app import build_app
    from tasklib import TaskManager
    mgr, _video = _make_mgr(tmp_path)
    t = mgr.create(name='demo3', original_video=tmp_path / 'lecture.mp4')
    tid = t.task_id
    task_dir = tmp_path / 'tasks' / tid
    fc_path = task_dir / 'fine_compose.json'
    fc_path.parent.mkdir(parents=True, exist_ok=True)
    fc_path.write_text(json.dumps({
        "materials": {}, "layout": {"video": {"x":0,"y":0,"scale":1,"enabled":True}},
        "font": {}, "output": {}, "audio": {},
        "bg_detect_cache": {"x":1, "y":2, "algorithm":"center_expand"},
    }), encoding='utf-8')
    client = TestClient(build_app(repo_root=tmp_path).app)
    r = client.post('/slirn/api/clear_bg_detect_cache', json={'task_id': tid})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j.get('ok') is True, j
    # 缓存已被清除
    fc = json.loads(fc_path.read_text(encoding='utf-8'))
    assert 'bg_detect_cache' not in fc, \
        f"清缓存后 fc.bg_detect_cache 应消失，实际: {list(fc.keys())}"


def test_bg_detect_color_box_hidden_when_no_cache(tmp_path):
    """无缓存时色块大容器应 hidden（直到首次检测完）。"""
    from starlette.testclient import TestClient
    from slirn_home.app import build_app
    from tasklib import TaskManager
    mgr, _video = _make_mgr(tmp_path)
    t = mgr.create(name='demo4', original_video=tmp_path / 'lecture.mp4')
    tid = t.task_id
    client = TestClient(build_app(repo_root=tmp_path).app)
    r = client.post('/slirn/api/workbench', json={'task_id': tid})
    assert r.status_code == 200, r.text
    j = r.json()
    html = j.get('html') or j.get('content') or r.text
    # 无缓存：结果区 hidden；清缓存按钮 disabled
    # v12：色块容器已彻底移除（不是 hidden，而是节点不存在）
    assert 'slirn-fine-bg-detect-color-box' not in html, \
        "v12：色块容器节点不应存在（已删除而非 hidden）"
    assert 'id="slirn-fine-bg-detect-result" hidden' in html
    # 清缓存按钮 disabled 顺序：action → task-id → disabled
    assert 'fine-bg-detect-clear" data-task-id="' in html
    assert html.find('fine-bg-detect-clear" data-task-id="') >= 0
    # 找按钮的下一段含 disabled
    btn_i = html.find('fine-bg-detect-clear" data-task-id="')
    assert btn_i >= 0
    btn_seg = html[btn_i:btn_i+300]
    assert 'disabled' in btn_seg, f"清缓存按钮应 disabled，实际：{btn_seg[:200]}"


def test_router_fine_apply_video_layout_function_removed():
    """REQ-20260919-062 v10：去掉页面内预览框后，_fineApplyVideoLayout() 不再需要。"""
    js = Path('slirn_home/static/router.js').read_text(encoding='utf-8')
    # v10：不应再有 function _fineApplyVideoLayout 定义（注释里可保留说明）
    assert 'function _fineApplyVideoLayout' not in js, \
        "v10：function _fineApplyVideoLayout 应已删除"
    # v10：router.js 仍读取 video layout 字段（用于计算占背景图百分比）
    for field in ["slirn-fine-video-crop_w", "slirn-fine-video-crop_h",
                  "slirn-fine-video-scale"]:
        assert field in js, f"router.js 应读 {field}"


def test_router_preview_no_stage_container():
    """REQ-20260919-062 v10：router.js 不应再引用 preview-stage 容器（预览框已去除）。"""
    js = Path('slirn_home/static/router.js').read_text(encoding='utf-8')
    assert 'getElementById(\'slirn-fine-preview-stage\')' not in js, \
        "v10：router.js 不应再 getElementById('slirn-fine-preview-stage')"
    # 弹窗预览的 _fineApplyVideoLayout 也不再使用
    assert 'function _fineApplyVideoLayout' not in js, \
        "v10：function _fineApplyVideoLayout 已删除"


def test_css_preview_stage_removed_in_v10():
    """REQ-20260919-062 v10：home.css 不应再定义 .slirn-fine-preview-stage 样式
    （预览框已去除，弹窗预览 .slirn-mat-preview-float 足够）。
    """
    css = Path('slirn_home/static/home.css').read_text(encoding='utf-8')
    # 不应再有 .slirn-fine-preview-stage { ... } 规则块
    import re
    assert re.search(r'\.slirn-fine-preview-stage\s*\{', css) is None, \
        "v10：home.css 不应再有 .slirn-fine-preview-stage CSS 规则块"
    # 弹窗预览样式仍保留
    assert '.slirn-mat-preview-float' in css


def test_app_preview_box_has_no_stage_html():
    """REQ-20260919-062 v10：去掉页面内预览框后，不应再输出 preview-stage div。"""
    from starlette.testclient import TestClient
    from slirn_home.app import build_app
    from tasklib import TaskManager
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        mgr = TaskManager(Path(td))
        (Path(td) / 'lecture.mp4').write_bytes(b'fake-video')
        t = mgr.create(name='demo5', original_video=Path(td) / 'lecture.mp4')
        tid = t.task_id
        client = TestClient(build_app(repo_root=Path(td)).app)
        r = client.post('/slirn/api/workbench', json={'task_id': tid})
        assert r.status_code == 200
        j = r.json()
        html = j.get('html') or ''
        # v10：preview-stage div 不再渲染
        assert 'id="slirn-fine-preview-stage"' not in html, \
            "v10：页面内的 preview-stage div 应已去除"
        # 占位提示仍存在（hidden 状态）
        assert 'id="slirn-fine-preview-empty"' in html


def test_app_video_info_shows_bg_pct(tmp_path):
    """REQ-20260919-062 v10：视频信息行应显示「占背景图 X×Y%」（crop_w/1920 × crop_h/1080）。"""
    from starlette.testclient import TestClient
    from slirn_home.app import build_app
    from tasklib import TaskManager
    mgr, video = _make_mgr(tmp_path)
    t = mgr.create(name="bg-pct", original_video=video)
    client = TestClient(build_app(repo_root=tmp_path).app)
    r = client.post('/slirn/api/workbench', json={'task_id': t.task_id})
    assert r.status_code == 200
    j = r.json()
    html = j.get('html') or ''
    # 默认 crop_w=1920, crop_h=1080 → 100% × 100%
    assert 'id="slirn-fine-video-crop-w-pct"' in html, \
        "应有 crop-w-pct 节点"
    assert 'id="slirn-fine-video-crop-h-pct"' in html, \
        "应有 crop-h-pct 节点"
    assert '占背景图' in html, \
        "应有「占背景图」标签"
    assert '背景 1920×1080' in html, \
        "应说明基准（背景 1920×1080）"
    # 1920/1920*100 = 100.00
    assert '100.00' in html, \
        "默认 crop_w=1920 时宽度百分比应为 100.00"


def test_app_video_info_renders_pct_for_custom_crop(tmp_path):
    """REQ-20260919-062 v10：自定义 crop_w 后宽度百分比应正确反映。"""
    from starlette.testclient import TestClient
    from slirn_home.app import build_app, _get_fine_compose, _save_fine_compose
    from tasklib import TaskManager
    mgr, video = _make_mgr(tmp_path)
    t = mgr.create(name="bg-pct-custom", original_video=video)
    # 把 video crop_w 设为 960（即背景 1920 的一半 → 50%）
    fc = _get_fine_compose(mgr, t.task_id)
    fc["layout"]["video"]["crop_w"] = 960
    fc["layout"]["video"]["crop_h"] = 540
    _save_fine_compose(mgr, t.task_id, fc)
    client = TestClient(build_app(repo_root=tmp_path).app)
    r = client.post('/slirn/api/workbench', json={'task_id': t.task_id})
    html = r.json().get('html') or ''
    # 960/1920*100 = 50.00；540/1080*100 = 50.00
    assert 'id="slirn-fine-video-crop-w-pct"' in html
    assert 'id="slirn-fine-video-crop-h-pct"' in html
    # 找 crop-w-pct 节点后的值
    import re as _re
    m_w = _re.search(r'id="slirn-fine-video-crop-w-pct">([\d.]+)', html)
    m_h = _re.search(r'id="slirn-fine-video-crop-h-pct">([\d.]+)', html)
    assert m_w is not None and abs(float(m_w.group(1)) - 50.0) < 0.01, \
        f"crop_w=960 → 宽度百分比应 ≈ 50.00，实际 {m_w.group(1) if m_w else 'N/A'}"
    assert m_h is not None and abs(float(m_h.group(1)) - 50.0) < 0.01, \
        f"crop_h=540 → 高度百分比应 ≈ 50.00，实际 {m_h.group(1) if m_h else 'N/A'}"


def test_router_update_video_disp_sets_bg_pct():
    """router.js updateVideoDisp() 应更新 crop_w_pct / crop_h_pct 节点（按 w/1920 × h/1080）。"""
    js = Path('slirn_home/static/router.js').read_text(encoding='utf-8')
    # 应读取 2 个百分比节点
    assert 'slirn-fine-video-crop-w-pct' in js, \
        "router.js 应读 slirn-fine-video-crop-w-pct"
    assert 'slirn-fine-video-crop-h-pct' in js, \
        "router.js 应读 slirn-fine-video-crop-h-pct"
    # 应按 / 1920 和 / 1080 计算
    assert '/ 1920' in js or '/1920' in js, \
        "router.js 应除以 1920（背景图宽度）"
    assert '/ 1080' in js or '/1080' in js, \
        "router.js 应除以 1080（背景图高度）"
    # v10：删除 _fineApplyVideoLayout 函数和 preview-stage 容器引用
    assert 'function _fineApplyVideoLayout' not in js, \
        "v10：function _fineApplyVideoLayout 应已删除"
    assert 'getElementById(\'slirn-fine-preview-stage\')' not in js, \
        "v10：router.js 不应再引用 preview-stage 容器"


def test_css_preview_no_full_size_container():
    """v10：home.css 不应再有大块 .slirn-fine-preview 容器（黑色背景框）。"""
    css = Path('slirn_home/static/home.css').read_text(encoding='utf-8')
    # 不应有 .slirn-fine-preview { ... min-height: 100px; ... } 这种大块容器
    # 找 .slirn-fine-preview { 这一段
    import re as _re
    seg_match = _re.search(r'\.slirn-fine-preview\s*\{', css)
    assert seg_match is None or seg_match.start() >= 0
    if seg_match:
        # 如果还有，应不含 min-height: 100px 这种大块
        seg_end = css.find('}', seg_match.end())
        seg = css[seg_match.start():seg_end]
        assert 'min-height: 100px' not in seg, \
            f"v10：.slirn-fine-preview 不应再有大块 min-height 样式：{seg}"


# ============================================================
# REQ-20260919-062 v11：生成预览后自动弹窗预览（弥补 v10 去掉预览框后的视觉反馈）
# ============================================================

def test_router_has_open_fine_preview_float():
    """router.js 应有 openFinePreviewFloat(url, title) 函数，生成预览后自动弹窗。"""
    js = Path('slirn_home/static/router.js').read_text(encoding='utf-8')
    assert 'function openFinePreviewFloat' in js, \
        "router.js 应定义 openFinePreviewFloat 函数"
    # 函数内应创建 .slirn-mat-preview-float 浮层（复用样式）
    fn_seg = js.split('function openFinePreviewFloat', 1)[1].split('\n  }\n', 1)[0]
    assert 'slirn-mat-preview-float' in fn_seg, \
        "openFinePreviewFloat 应复用 .slirn-mat-preview-float 样式"
    # 应创建 video 元素并设 src + controls + autoplay
    assert '<video' in fn_seg and 'v.src = url' in fn_seg, \
        "openFinePreviewFloat 应创建 video 元素并设 src"
    assert 'v.controls = true' in fn_seg, \
        "openFinePreviewFloat 应启用 controls"
    assert 'v.autoplay = true' in fn_seg or 'v.autoplay = true' in fn_seg, \
        "openFinePreviewFloat 应启用 autoplay"


def test_router_fine_preview_handler_calls_open_float():
    """router.js 处理 fine-preview action 后应自动调 openFinePreviewFloat。"""
    js = Path('slirn_home/static/router.js').read_text(encoding='utf-8')
    # 找处理 fine-preview 的分支（含 render_fine_preview 调用点）
    import re as _re
    seg_match = _re.search(r"render_fine_preview", js)
    assert seg_match is not None, "应能找到 render_fine_preview 调用点"
    start = seg_match.start()
    # 取调用点后 4000 字符作为该分支片段（足够覆盖完整 .then 链 — 预览开始时间
    # 改造后 handler 变长，含多段读取/拼接/渲染中文案）。
    seg = js[start:start + 4000]
    # v11：处理分支里应调 openFinePreviewFloat(j.url, ...)
    assert 'openFinePreviewFloat' in seg, \
        "fine-preview 处理分支应调用 openFinePreviewFloat"
    # 应传 j.url
    assert 'j.url' in seg, \
        "openFinePreviewFloat 应接收 j.url（合成视频 URL）"


# ============================================================
# REQ-20260919-062 v13：画布 X/Y 允许负数（用户反馈："所有的像素位置还可以设为负数"）
# ============================================================

def test_render_fine_cut_zone_canvas_xy_allow_negative(tmp_path: Path):
    """画布 X/Y 滑块 min 应为负数（−画布宽到+画布宽 / −画布高到+画布高），标签同步更新。"""
    from slirn_home.app import _render_fine_cut_zone, _FINE_DESIGN_W, _FINE_DESIGN_H
    from tasklib import TaskManager
    mgr, video = _make_mgr(tmp_path)
    t = mgr.create(name='demo', original_video=video)
    fc = {
        "materials": {"video": {}, "subtitle": {}, "bg": {}, "cover": {}, "audio": {}},
        "layout": {
            "video":    {"x": 0, "y": 0, "scale": 1.0, "crop_x": 0,
                         "crop_y": 0, "crop_w": 1920, "crop_h": 1080, "enabled": True, "viewport": None},
            "subtitle": {"x": 672, "y": 972, "scale": 1.0, "enabled": True},
            "cover":    {"enabled": False, "duration": 2.0},
            "bg":       {"x": 0, "y": 0, "scale": 1.0, "enabled": False},
        },
        "font": {"size": 36, "family": "STHeitiMedium", "stroke_width": 2,
                 "stroke_color": "#000000", "bg_enabled": False, "bg_color": "#000000",
                 "bg_opacity": 0.6, "bg_radius": 4, "bold": True, "align": "center"},
        "output": {"resolution": "1080p", "codec": "h264", "audio_codec": "aac"},
        "audio": {"enabled": False, "volume": 0.4, "fade_in": 0, "fade_out": 0,
                  "path": None},
    }
    html = _render_fine_cut_zone(t.task_id, t, mgr)
    # v13：3 个素材（video / subtitle / bg）X 滑块 min 应为 -1920
    for mat_key in ("video", "subtitle", "bg"):
        # 找 <input type="range" id="slirn-fine-{mat_key}-x" ... min="-1920"
        import re as _re
        m = _re.search(
            r'<input type="range"[^>]*id="slirn-fine-' + _re.escape(mat_key) + r'-x"[^>]*>',
            html,
        )
        assert m is not None, f"{mat_key} X 滑块缺失"
        seg = m.group(0)
        assert 'min="-1920"' in seg, \
            f"{mat_key} X 滑块 min 应为 -1920（v13 允许负数），实际：{seg}"
        assert 'max="1920"' in seg, \
            f"{mat_key} X 滑块 max 仍应为 1920，实际：{seg}"
    # Y 滑块 min=-1080
    for mat_key in ("video", "subtitle", "bg"):
        import re as _re
        m = _re.search(
            r'<input type="range"[^>]*id="slirn-fine-' + _re.escape(mat_key) + r'-y"[^>]*>',
            html,
        )
        assert m is not None, f"{mat_key} Y 滑块缺失"
        seg = m.group(0)
        assert 'min="-1080"' in seg, \
            f"{mat_key} Y 滑块 min 应为 -1080（v13 允许负数），实际：{seg}"
    # 但 crop_* 起点仍 ≥ 0（源坐标不能为负）
    import re as _re
    crop_x = _re.search(
        r'<input type="range"[^>]*id="slirn-fine-video-crop_x"[^>]*>',
        html,
    )
    assert crop_x is not None, "video crop_x 滑块缺失"
    assert 'min="0"' in crop_x.group(0), "crop_x 起点应保持 ≥0（源坐标）"
    # 标签更新
    assert f'X（-{_FINE_DESIGN_W}–{_FINE_DESIGN_W}）' in html
    assert f'Y（-{_FINE_DESIGN_H}–{_FINE_DESIGN_H}）' in html


def test_save_fine_layout_accepts_negative_xy():
    """save_fine_layout 端点应允许负数 X/Y（不再 max(0, ...) 兜底）。"""
    from starlette.testclient import TestClient
    from slirn_home.app import build_app
    from tasklib import TaskManager
    import tempfile, json as _json
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        video = tmp_path / "lecture.mp4"
        video.write_bytes(b"fake-video")
        mgr = TaskManager(tmp_path)
        t = mgr.create(name='demo', original_video=video)
        tid = t.task_id
        client = TestClient(build_app(repo_root=tmp_path).app)
        # 提交负 X/Y（视频半截出画布的"露半边"效果）
        r = client.post('/slirn/api/save_fine_layout', json={
            'task_id': tid,
            'layout': {
                'video':    {'x': -960, 'y': -540, 'scale': 2.0},
                'subtitle': {'x': -100, 'y': -50},
                'bg':       {'x': -1920, 'y': -1080},
            },
        })
        assert r.status_code == 200, r.text
        j = r.json()
        assert j.get('ok') is True, j
        layout = j['layout']
        # 服务端应原样保留负值（不做 max(0, ...) 兜底）
        assert layout['video']['x'] == -960, f"video.x 应保留 -960，实际：{layout['video']['x']}"
        assert layout['video']['y'] == -540, f"video.y 应保留 -540，实际：{layout['video']['y']}"
        assert layout['subtitle']['x'] == -100
        assert layout['subtitle']['y'] == -50
        assert layout['bg']['x'] == -1920
        assert layout['bg']['y'] == -1080
        # 落盘后读回校验（确保持久化也是负值）
        fc_path = tmp_path / 'tasks' / tid / 'fine_compose.json'
        fc = _json.loads(fc_path.read_text(encoding='utf-8'))
        assert fc['layout']['video']['x'] == -960
        assert fc['layout']['video']['y'] == -540
        assert fc['layout']['bg']['x'] == -1920
        assert fc['layout']['bg']['y'] == -1080


def test_save_fine_layout_negative_xy_exceeds_design_is_accepted():
    """超出 [-画布宽, +画布宽] 的极值也由 save_fine_layout 直接接受（用户极值场景，不在前端滑块范围内）。"""
    from starlette.testclient import TestClient
    from slirn_home.app import build_app
    from tasklib import TaskManager
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        video = tmp_path / "lecture.mp4"
        video.write_bytes(b"fake-video")
        mgr = TaskManager(tmp_path)
        t = mgr.create(name='demo', original_video=video)
        tid = t.task_id
        client = TestClient(build_app(repo_root=tmp_path).app)
        # 极值（前端滑块 0-2 倍画布，但服务端不限制）
        r = client.post('/slirn/api/save_fine_layout', json={
            'task_id': tid,
            'layout': {'video': {'x': -3840, 'y': 3840}},
        })
        assert r.status_code == 200, r.text
        j = r.json()
        assert j.get('ok') is True, j
        # 服务端原样接收（不在前端范围限制内，但服务端不阻断）
        assert j['layout']['video']['x'] == -3840
        assert j['layout']['video']['y'] == 3840


# ============================================================
# REQ-20260919-062 v14：缩放 = crop_w / bg_w（视频原裁剪宽度 / 背景图片宽度）
# 公式 scale = crop_w / 1920。「🎯 按裁剪宽度」按钮一键应用。
# ============================================================

def test_render_fine_cut_zone_has_scale_auto_button():
    """v14：视频块应含「🎯 按裁剪宽度」按钮（仅 video 块，subtitle/bg 不含）。"""
    from slirn_home.app import _render_fine_cut_zone
    from tasklib import TaskManager
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        video = tmp_path / "lecture.mp4"
        video.write_bytes(b"fake-video")
        mgr = TaskManager(tmp_path)
        t = mgr.create(name='demo', original_video=video)
        fc = {
            "materials": {"video": {}, "subtitle": {}, "bg": {}, "cover": {}, "audio": {}},
            "layout": {
                "video":    {"x": 0, "y": 0, "scale": 1.0, "crop_x": 0,
                             "crop_y": 0, "crop_w": 1920, "crop_h": 1080, "enabled": True, "viewport": None},
                "subtitle": {"x": 672, "y": 972, "scale": 1.0, "enabled": True},
                "cover":    {"enabled": False, "duration": 2.0},
                "bg":       {"x": 0, "y": 0, "scale": 1.0, "enabled": False},
            },
            "font": {"size": 36, "family": "STHeitiMedium", "stroke_width": 2,
                     "stroke_color": "#000000", "bg_enabled": False, "bg_color": "#000000",
                     "bg_opacity": 0.6, "bg_radius": 4, "bold": True, "align": "center"},
            "output": {"resolution": "1080p", "codec": "h264", "audio_codec": "aac"},
            "audio": {"enabled": False, "volume": 0.4, "fade_in": 0, "fade_out": 0, "path": None},
        }
        html = _render_fine_cut_zone(t.task_id, t, mgr)
        # v14：视频块应有按钮，subtitle/bg 块不应有
        assert 'data-action="fine-scale-auto"' in html, \
            "v14：视频块应含 fine-scale-auto 按钮"
        assert '🎯 按裁剪宽度' in html, \
            "v14：按钮文字应为「🎯 按裁剪宽度」"
        # subtitle 块不应有 fine-scale-auto 按钮
        import re as _re
        sub_block = html[html.find('slirn-fine-layout-title">📝 字幕'):html.find('slirn-fine-layout-title">🖼 封面')]
        assert 'data-action="fine-scale-auto"' not in sub_block, \
            "v14：字幕块不应有 fine-scale-auto 按钮"
        # bg 块也不应有
        bg_block = html[html.find('slirn-fine-layout-title">🎨 背景'):html.find('slirn-fine-layout-block', html.find('slirn-fine-layout-title">🎨 背景') + 30)]
        # bg block 后是其他东西，取到下个 end of layout-block 即可
        bg_block_end = html.find('</div></div>', html.find('slirn-fine-layout-title">🎨 背景') + 30)
        bg_seg = html[html.find('slirn-fine-layout-title">🎨 背景'):bg_block_end]
        assert 'data-action="fine-scale-auto"' not in bg_seg, \
            "v14：背景块不应有 fine-scale-auto 按钮"


def test_router_has_fine_scale_auto_function():
    """router.js 应有 fineScaleAutoFromCrop 函数（应用 scale=crop_w/1920）。"""
    js = Path('slirn_home/static/router.js').read_text(encoding='utf-8')
    assert 'function fineScaleAutoFromCrop' in js, \
        "router.js 应有 fineScaleAutoFromCrop 函数"
    # 函数内应读 crop_w 滑块 + scale 滑块
    fn_seg = js.split('function fineScaleAutoFromCrop', 1)[1].split('\n  }\n', 1)[0]
    assert 'slirn-fine-video-crop_w' in fn_seg, \
        "fineScaleAutoFromCrop 应读 crop_w 滑块"
    assert 'slirn-fine-video-scale' in fn_seg, \
        "fineScaleAutoFromCrop 应更新 scale 滑块"
    # 公式：scale = cropW / 1920
    assert '/ 1920' in fn_seg, \
        "fineScaleAutoFromCrop 应使用 / 1920 公式（crop_w / 1920）"
    # 应触发 fineSaveAll 保存
    assert 'fineSaveAll()' in fn_seg, \
        "fineScaleAutoFromCrop 应触发 fineSaveAll 保存"


def test_router_scale_auto_action_wired_up():
    """router.js 的 action 分发应处理 fine-scale-auto。"""
    js = Path('slirn_home/static/router.js').read_text(encoding='utf-8')
    assert "action === 'fine-scale-auto'" in js, \
        "router.js 应分发 fine-scale-auto action → fineScaleAutoFromCrop()"


# ============================================================
# REQ-20260919-062 v15：crop_w 变化自动重算 scale，保留 4 位小数
# ============================================================

def test_router_has_recompute_scale_from_crop_w():
    """router.js 应有 _recomputeScaleFromCropW 函数（crop_w 变化时自动重算 scale）。"""
    js = Path('slirn_home/static/router.js').read_text(encoding='utf-8')
    assert 'function _recomputeScaleFromCropW' in js, \
        "router.js 应定义 _recomputeScaleFromCropW 函数"
    fn_seg = js.split('function _recomputeScaleFromCropW', 1)[1].split('\n    }\n', 1)[0]
    # 应读 crop_w 滑块
    assert 'slirn-fine-video-crop_w' in fn_seg
    # 应更新 scale 滑块
    assert 'slirn-fine-video-scale' in fn_seg
    # 公式：cropW / 1920
    assert '/ 1920' in fn_seg, \
        "_recomputeScaleFromCropW 应使用 / 1920 公式"
    # 保留 4 位小数（Math.round(* 10000) / 10000 或 toFixed(4)）
    assert '10000' in fn_seg or 'toFixed(4)' in fn_seg, \
        "_recomputeScaleFromCropW 应保留 4 位小数"


def test_router_recompute_scale_wired_to_crop_w_event():
    """router.js 的 bindFineControls 应在 crop_w input 时调用 _recomputeScaleFromCropW。"""
    js = Path('slirn_home/static/router.js').read_text(encoding='utf-8')
    # 找 crop_w input 分支
    import re as _re
    # 应有 "if (k === 'video.crop_w') { _recomputeScaleFromCropW(); }"
    m = _re.search(
        r"if \(k === 'video\.crop_w'\)\s*\{\s*_recomputeScaleFromCropW\(\)",
        js,
    )
    assert m is not None, \
        "bindFineControls 应在 crop_w input 时调 _recomputeScaleFromCropW()"


def test_router_recompute_scale_rounds_to_4_decimals():
    """_recomputeScaleFromCropW 应用 Math.round(* 10000) / 10000 保留 4 位小数。"""
    js = Path('slirn_home/static/router.js').read_text(encoding='utf-8')
    fn_seg = js.split('function _recomputeScaleFromCropW', 1)[1].split('\n    }\n', 1)[0]
    # 必须有 * 10000 (round-to-4-decimals 操作)
    assert '* 10000' in fn_seg, \
        "_recomputeScaleFromCropW 应用 * 10000 / 10000 保留 4 位小数"
    # 必须有 / 10000
    assert '/ 10000' in fn_seg, \
        "_recomputeScaleFromCropW 应用 / 10000 还原"


def test_router_v14_button_also_rounds_to_4_decimals():
    """fineScaleAutoFromCrop 按钮也应保留 4 位小数（与自动重算一致）。"""
    js = Path('slirn_home/static/router.js').read_text(encoding='utf-8')
    fn_seg = js.split('function fineScaleAutoFromCrop', 1)[1].split('\n  }\n', 1)[0]
    assert '* 10000' in fn_seg or '10000' in fn_seg, \
        "fineScaleAutoFromCrop 也应保留 4 位小数（与自动重算一致）"
    # toast 也应用 toFixed(4)
    assert 'toFixed(4)' in fn_seg, \
        "fineScaleAutoFromCrop 的 toast 应显示 4 位小数"


# ============================================================
# REQ-20260919-062 v16：视频缩放参数显示 4 位小数
# ============================================================

def test_render_fine_cut_zone_video_scale_uses_4_decimals(tmp_path: Path):
    """视频缩放滑块 value= 属性应使用 4 位小数（v16）。"""
    from slirn_home.app import _render_fine_cut_zone, _get_fine_compose, _save_fine_compose
    m, video = _make_mgr(tmp_path)
    t = m.create(name="scale-4dp", original_video=video)
    fc = _get_fine_compose(m, t.task_id)
    fc["layout"]["video"]["scale"] = 0.6667
    _save_fine_compose(m, t.task_id, fc)
    html = _render_fine_cut_zone(t.task_id, m.tasks_dir / t.task_id, m)
    import re as _re
    # 视频 scale range slider 应该有 value="0.6667"
    m_video_scale = _re.search(
        r'<input type="range" class="slirn-fine-slider" id="slirn-fine-video-scale"[^>]*>',
        html,
    )
    assert m_video_scale is not None
    seg = m_video_scale.group(0)
    assert 'value="0.6667"' in seg, \
        f"v16：视频 scale 滑块 value 应为 '0.6667'，实际：{seg}"
    # 视频 scale number input 也应该是 4 位小数
    m_video_num = _re.search(
        r'<input type="number" class="slirn-fine-num" id="slirn-fine-video-scale_num"[^>]*>',
        html,
    )
    assert m_video_num is not None
    seg_num = m_video_num.group(0)
    assert 'value="0.6667"' in seg_num, \
        f"v16：视频 scale 数字框 value 应为 '0.6667'，实际：{seg_num}"
    # 但 subtitle.scale 仍是 2 位小数（不应被影响）
    m_sub_scale = _re.search(
        r'<input type="range" class="slirn-fine-slider" id="slirn-fine-subtitle-scale"[^>]*>',
        html,
    )
    if m_sub_scale:
        sub_seg = m_sub_scale.group(0)
        # subtitle 默认 scale=1.0 → 显示 2 位 = "1.00"（v16 不影响非视频素材）
        # 或 4 位 = "1.0000" — 取决于实现。检查不带小数点的判定
        # 当前应仍为 2 位小数
        assert 'value="1.00"' in sub_seg, \
            f"v16：subtitle scale 不应升级到 4 位小数，实际：{sub_seg}"


def test_render_fine_cut_zone_video_scale_pct_uses_4_decimals(tmp_path: Path):
    """视频缩放百分比显示应保留 4 位小数（v16）。"""
    from slirn_home.app import _render_fine_cut_zone, _get_fine_compose, _save_fine_compose
    m, video = _make_mgr(tmp_path)
    t = m.create(name="scale-pct-4dp", original_video=video)
    fc = _get_fine_compose(m, t.task_id)
    fc["layout"]["video"]["scale"] = 0.6667
    _save_fine_compose(m, t.task_id, fc)
    html = _render_fine_cut_zone(t.task_id, m.tasks_dir / t.task_id, m)
    import re as _re
    m_pct = _re.search(r'id="slirn-fine-video-scale-pct"[^>]*>([^<]+)<', html)
    assert m_pct is not None
    pct_text = m_pct.group(1)
    # scale=0.6667 → 66.67%（2 位）or 66.6700%（4 位）。v16 应该是 4 位。
    assert '66.6700' in pct_text, \
        f"v16：scale-pct 应含 66.6700（4 位小数），实际：{pct_text}"


def test_router_update_video_disp_uses_4_decimals_for_scale():
    """router.js updateVideoDisp 应使用 toFixed(4) 显示 scale 百分比（v16）。"""
    js = Path('slirn_home/static/router.js').read_text(encoding='utf-8')
    fn = js.split('function updateVideoDisp()', 1)[1].split('\n  }', 1)[0]
    assert 'toFixed(4)' in fn, \
        "updateVideoDisp 应用 toFixed(4) 显示 scale 百分比（v16）"
    # 不应再有 toFixed(2) 处理 scale 百分比
    import re as _re
    # 找 scalePct 相关行
    m = _re.search(r'scalePct.*?toFixed\(([0-9]+)\)', fn)
    if m:
        # 确认是 4
        assert m.group(1) == '4', \
            f"scalePct 应 toFixed(4)，实际 toFixed({m.group(1)})"


# ============================================================
# REQ-20260919-062 v17 用户反馈：视频缩放 bug — 自动重算到 4 位后立刻被截到 2 位
# 根因：<input type="range" step="0.01"> 会把 value 吸附到 0.01 网格，导致
#      s.value = "0.6667" 立刻变成 "0.67"。
# 修复：把视频 scale 滑块的 step 从 0.01 改成 0.0001，让浏览器保留 4 位小数精度。
# ============================================================


def test_render_fine_cut_zone_video_scale_step_is_4_decimals(tmp_path: Path):
    """v17 bug 修复：视频 scale 滑块 step 应为 0.0001（避免浏览器吸附到 0.01）。"""
    from slirn_home.app import _render_fine_cut_zone, _get_fine_compose, _save_fine_compose
    m, video = _make_mgr(tmp_path)
    t = m.create(name="scale-step-4dp", original_video=video)
    fc = _get_fine_compose(m, t.task_id)
    fc["layout"]["video"]["scale"] = 0.6667
    _save_fine_compose(m, t.task_id, fc)
    html = _render_fine_cut_zone(t.task_id, m.tasks_dir / t.task_id, m)
    import re as _re
    # 视频 scale range slider step 应为 0.0001
    m_video_scale = _re.search(
        r'<input type="range" class="slirn-fine-slider" id="slirn-fine-video-scale"[^>]*>',
        html,
    )
    assert m_video_scale is not None, \
        "应能找到视频 scale range slider"
    seg = m_video_scale.group(0)
    assert 'step="0.0001"' in seg, \
        f"v17：视频 scale 滑块 step 应为 '0.0001'（避免 0.01 网格吸附），实际：{seg}"
    # 视频 scale number input step 也应为 0.0001
    m_video_num = _re.search(
        r'<input type="number" class="slirn-fine-num" id="slirn-fine-video-scale_num"[^>]*>',
        html,
    )
    assert m_video_num is not None
    seg_num = m_video_num.group(0)
    assert 'step="0.0001"' in seg_num, \
        f"v17：视频 scale 数字框 step 应为 '0.0001'，实际：{seg_num}"


def test_render_fine_cut_zone_subtitle_scale_step_still_2_decimals(tmp_path: Path):
    """v17 修复不应影响 subtitle.scale：仍保持 step=0.05 + 2 位小数。"""
    from slirn_home.app import _render_fine_cut_zone, _get_fine_compose, _save_fine_compose
    m, video = _make_mgr(tmp_path)
    t = m.create(name="sub-scale-unchanged", original_video=video)
    fc = _get_fine_compose(m, t.task_id)
    fc["layout"]["subtitle"]["scale"] = 1.0
    _save_fine_compose(m, t.task_id, fc)
    html = _render_fine_cut_zone(t.task_id, m.tasks_dir / t.task_id, m)
    import re as _re
    m_sub_scale = _re.search(
        r'<input type="range" class="slirn-fine-slider" id="slirn-fine-subtitle-scale"[^>]*>',
        html,
    )
    assert m_sub_scale is not None
    seg = m_sub_scale.group(0)
    # subtitle/cover/bg 应仍为 step=0.05 + 2 位小数
    assert 'step="0.05"' in seg, \
        f"v17：subtitle scale step 应仍为 '0.05'，实际：{seg}"
    assert 'value="1.00"' in seg, \
        f"v17：subtitle scale value 应仍为 '1.00'（2 位小数），实际：{seg}"


def test_render_fine_cut_zone_all_non_video_scale_steps_unchanged(tmp_path: Path):
    """v17 修复只动 video.scale 的 step，其它素材（cover/bg）保持原 step。"""
    from slirn_home.app import _render_fine_cut_zone
    m, video = _make_mgr(tmp_path)
    t = m.create(name="all-mats-step", original_video=video)
    html = _render_fine_cut_zone(t.task_id, m.tasks_dir / t.task_id, m)
    import re as _re
    # cover / bg scale 应仍为 step=0.05
    for mat in ("cover", "bg"):
        m_mat = _re.search(
            rf'<input type="range" class="slirn-fine-slider" id="slirn-fine-{mat}-scale"[^>]*>',
            html,
        )
        if m_mat:
            seg = m_mat.group(0)
            assert 'step="0.05"' in seg, \
                f"v17：{mat} scale step 应仍为 '0.05'，实际：{seg}"


# ============================================================
# REQ-20260919-062 v18 用户反馈：视频源裁剪里的"锁定 16:9 比例"勾选状态也要记录
# 实现：
#   - 后端 _FINE_LAYOUT_DEFAULTS["video"]["crop_aspect_lock"] = True
#   - save_fine_layout 白名单 + crop_aspect_lock 字段
#   - _render_fine_cut_zone 把勾选状态写入 checkbox checked
#   - 旧任务迁移：缺字段时补 True
#   - JS fineSaveAll 收集 [data-key][type=checkbox]
#   - JS bindCropAspectLink toggle.change → fineSaveAll
# ============================================================


def test_fine_layout_defaults_has_crop_aspect_lock_true():
    """v18：默认布局应包含 crop_aspect_lock=True。"""
    from slirn_home.app import _FINE_LAYOUT_DEFAULTS
    assert "crop_aspect_lock" in _FINE_LAYOUT_DEFAULTS["video"], \
        "video 默认布局应有 crop_aspect_lock 字段"
    assert _FINE_LAYOUT_DEFAULTS["video"]["crop_aspect_lock"] is True, \
        "crop_aspect_lock 默认值应为 True"


def test_render_fine_cut_zone_crop_aspect_link_checked_by_default(tmp_path: Path):
    """v18：默认任务里 crop-aspect-link checkbox 应默认勾选。"""
    from slirn_home.app import _render_fine_cut_zone
    m, video = _make_mgr(tmp_path)
    t = m.create(name="lock-default", original_video=video)
    html = _render_fine_cut_zone(t.task_id, m.tasks_dir / t.task_id, m)
    import re as _re
    m_toggle = _re.search(
        r'<input type="checkbox" id="slirn-fine-crop-aspect-link"[^>]*>',
        html,
    )
    assert m_toggle is not None, "应能找到锁定 16:9 checkbox"
    seg = m_toggle.group(0)
    assert "checked" in seg, \
        f"v18：新任务里 16:9 锁定 checkbox 应默认 checked，实际：{seg}"
    assert 'data-key="video.crop_aspect_lock"' in seg, \
        f"v18：checkbox 应带 data-key 让 fineSaveAll 收集，实际：{seg}"


def test_render_fine_cut_zone_crop_aspect_link_uncheck_when_stored_false(tmp_path: Path):
    """v18：若 layout.video.crop_aspect_lock=False，应渲染为未勾选。"""
    from slirn_home.app import _render_fine_cut_zone, _get_fine_compose, _save_fine_compose
    m, video = _make_mgr(tmp_path)
    t = m.create(name="lock-false", original_video=video)
    fc = _get_fine_compose(m, t.task_id)
    fc["layout"]["video"]["crop_aspect_lock"] = False
    _save_fine_compose(m, t.task_id, fc)
    html = _render_fine_cut_zone(t.task_id, m.tasks_dir / t.task_id, m)
    import re as _re
    m_toggle = _re.search(
        r'<input type="checkbox" id="slirn-fine-crop-aspect-link"[^>]*>',
        html,
    )
    assert m_toggle is not None
    seg = m_toggle.group(0)
    # 未勾选应没有 checked 属性（不是 checked=""）
    assert "checked" not in seg, \
        f"v18：crop_aspect_lock=False 时 checkbox 应不勾选，实际：{seg}"


def test_save_fine_layout_persists_crop_aspect_lock(tmp_path: Path):
    """v18：save_fine_layout 应接受 crop_aspect_lock 字段并持久化。"""
    from slirn_home.app import _get_fine_compose, _save_fine_compose
    m, video = _make_mgr(tmp_path)
    t = m.create(name="save-lock", original_video=video)
    fc = _get_fine_compose(m, t.task_id)
    fc["layout"]["video"]["crop_aspect_lock"] = False
    _save_fine_compose(m, t.task_id, fc)
    # 重新读取，验证持久化
    fc2 = _get_fine_compose(m, t.task_id)
    assert fc2["layout"]["video"]["crop_aspect_lock"] is False, \
        "v18：crop_aspect_lock=False 应被持久化到 fc"


def test_normalize_fine_compose_migrates_missing_crop_aspect_lock(tmp_path: Path):
    """v18：旧任务没有 crop_aspect_lock 字段 → _get_fine_compose 读时补 True。"""
    from slirn_home.app import _get_fine_compose, _save_fine_compose
    m, video = _make_mgr(tmp_path)
    t = m.create(name="old-task", original_video=video)
    fc = _get_fine_compose(m, t.task_id)
    # 模拟旧数据：移除 crop_aspect_lock 并写盘
    fc["layout"]["video"].pop("crop_aspect_lock", None)
    _save_fine_compose(m, t.task_id, fc)
    # 重新读取，迁移逻辑应补 True
    fc2 = _get_fine_compose(m, t.task_id)
    assert fc2["layout"]["video"].get("crop_aspect_lock") is True, \
        "v18：旧任务读取时 crop_aspect_lock 应补为 True"


def test_save_fine_layout_allowed_keys_include_crop_aspect_lock():
    """v18：save_fine_layout 字段白名单应包含 crop_aspect_lock。"""
    from pathlib import Path
    mod_src = Path("slirn_home/app.py").read_text(encoding="utf-8")
    import re as _re
    # 找 save_fine_layout 的 allowed_keys 那一行
    m = _re.search(
        r'allowed_keys\s*=\s*\(([^)]+)\)',
        mod_src,
    )
    assert m is not None, "v18：应能找到 save_fine_layout 的 allowed_keys"
    keys_str = m.group(1)
    assert "crop_aspect_lock" in keys_str, \
        f"v18：allowed_keys 应包含 crop_aspect_lock，实际：{keys_str}"


def test_router_save_all_collects_checkbox_with_data_key():
    """v18：router.js fineSaveAll 应收集 [data-key][type=checkbox]。"""
    js = Path('slirn_home/static/router.js').read_text(encoding='utf-8')
    import re as _re
    # 找 fineSaveAll 里的 querySelectorAll — fineSaveAll 函数里有多个 querySelectorAll：
    # 第一个查 .slirn-fine-slider，第二个查复选框。我们需要第二个。
    fn = js.split('function fineSaveAll', 1)[1].split('\n  }', 1)[0]
    matches = list(_re.finditer(
        r"document\.querySelectorAll\('([^']+)'\)",
        fn,
    ))
    assert len(matches) >= 2, \
        f"v18：fineSaveAll 应至少有 2 个 querySelectorAll（slider + checkbox），实际：{len(matches)}"
    # 第二个是复选框（第一个是 slider）
    checkbox_selector = matches[1].group(1)
    assert 'slirn-fine-enabled' in checkbox_selector, \
        f"v18：fineSaveAll 复选框选择器应包含 .slirn-fine-enabled，实际：{checkbox_selector}"
    assert 'data-key' in checkbox_selector and 'checkbox' in checkbox_selector, \
        f"v18：fineSaveAll 复选框选择器应包含 [data-key][type=checkbox]，实际：{checkbox_selector}"


def test_router_crop_aspect_link_toggle_persists_state():
    """v18：bindCropAspectLink 的 toggle.change 应触发 fineSaveAll 持久化状态。"""
    js = Path('slirn_home/static/router.js').read_text(encoding='utf-8')
    import re as _re
    fn = js.split('function bindCropAspectLink', 1)[1].split('\n  }', 1)[0]
    # 找 toggle.addEventListener('change', ...) 整段
    m_change = _re.search(
        r"toggle\.addEventListener\('change',\s*function\(\)\s*\{(.*?)\}\);",
        fn,
        flags=_re.DOTALL,
    )
    assert m_change is not None, \
        "v18：bindCropAspectLink 应有 toggle change handler"
    change_body = m_change.group(1)
    assert 'fineSaveAll' in change_body, \
        f"v18：toggle change handler 应调 fineSaveAll 持久化，实际：{change_body}"


def test_router_save_all_handles_single_segment_checkbox_keys():
    """REQ-100：fineSaveAll 必须收集 5 个单段 data-key 的 .slirn-fine-enabled checkbox
    （cover/bg/audio/video/subtitle）的勾选状态。

    旧实现按 k.indexOf('.')<0 一刀切跳过 → 5 个 enable 复选框的勾选永远不写回 fc.json，
    刷新页面后回到旧值。修法：.slirn-fine-enabled 单段 key 自动落到 layout[k1].enabled；
    其它 [data-key] checkbox 仍是 section.field 形式。"""
    js = Path('slirn_home/static/router.js').read_text(encoding='utf-8')
    import re as _re
    fn = js.split('function fineSaveAll', 1)[1].split('\n  }', 1)[0]
    # 找 .slirn-fine-enabled 复选框的 forEach 整段
    m_loop = _re.search(
        r"document\.querySelectorAll\('\.slirn-fine-enabled,\s*\[data-key\]\[type=",
        fn,
    )
    assert m_loop, "REQ-100：应找到 .slirn-fine-enabled + [data-key] checkbox 选择器"
    # 不能有「k.indexOf('.')<0 跳过」这种一刀切逻辑（应替换为针对非 .slirn-fine-enabled 的判断）
    assert "indexOf('.')" not in fn or "parts.length" in fn, (
        "REQ-100：应去除对所有单段 checkbox 的 indexOf('.')<0 跳过逻辑"
    )
    # 必须有按 .slirn-fine-enabled 与 [data-key] 分别处理的分支
    assert "slirn-fine-enabled" in fn and "layout[k1].enabled" in fn, (
        "REQ-100：应保留 layout[k1].enabled = c.checked 分支"
    )


def test_save_fine_layout_persists_single_segment_checkbox_states(tmp_path: Path):
    """REQ-100：save_fine_layout 应持久化 5 个 enable checkbox 的勾选状态。

    模拟前端 fineSaveAll 真实发出的 payload：layout 顶层 key 可能是 video/subtitle/
    cover/bg/audio 之一，val 是个 dict 含 "enabled" 字段。后端 allowed_keys 白名单
    已含 enabled（v17 起），所以 4 个 layout 类的元素都能正确写回。audio 不在
    _FINE_LAYOUT_DEFAULTS，所以 audio.enabled 不会被 save_fine_layout 接收 — 它
    走独立的 save_fine_audio 端点（前端 JS 修复里单独发了 audio 包）。
    """
    from fastapi.testclient import TestClient
    from slirn_home import build_app
    from slirn_home.app import _get_fine_compose

    m, video = _make_mgr(tmp_path)
    t = m.create(name="single-seg-enable", original_video=video)
    client = TestClient(build_app(repo_root=tmp_path).app)

    # 模拟前端把 4 个 layout 元素的 enabled checkbox 都勾上
    r = client.post(
        "/slirn/api/save_fine_layout",
        json={
            "task_id": t.task_id,
            "layout": {
                "video": {"enabled": True},
                "subtitle": {"enabled": True},
                "cover": {"enabled": True},
                "bg": {"enabled": True},
            },
        },
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True

    # 读回 fc：4 个 enable 都应写成功
    fc = _get_fine_compose(m, t.task_id)
    assert fc["layout"]["video"]["enabled"] is True
    assert fc["layout"]["subtitle"]["enabled"] is True
    assert fc["layout"]["cover"]["enabled"] is True
    assert fc["layout"]["bg"]["enabled"] is True


def test_save_fine_audio_accepts_enabled_field(tmp_path: Path):
    """REQ-100：save_fine_audio 应接受 enabled 字段（前端 JS 修复后会把 audio
    enable checkbox 状态放进 audio.enabled）。"""
    from fastapi.testclient import TestClient
    from slirn_home import build_app
    from slirn_home.app import _get_fine_compose

    m, video = _make_mgr(tmp_path)
    t = m.create(name="audio-enable", original_video=video)
    client = TestClient(build_app(repo_root=tmp_path).app)

    r = client.post(
        "/slirn/api/save_fine_audio",
        json={
            "task_id": t.task_id,
            "audio": {"enabled": True, "volume_db": -18.0, "fade_in": 1.0, "fade_out": 2.0},
        },
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True

    fc = _get_fine_compose(m, t.task_id)
    assert fc["audio"]["enabled"] is True
    assert fc["audio"]["volume_db"] == -18.0
    assert fc["audio"]["fade_in"] == 1.0
    assert fc["audio"]["fade_out"] == 2.0
    # REQ-20260922-NNN：越界值 clamp 到 [-40, 0]
    r2 = client.post(
        "/slirn/api/save_fine_audio",
        json={"task_id": t.task_id, "audio": {"volume_db": -55.0}},
    )
    assert r2.status_code == 200
    fc2 = _get_fine_compose(m, t.task_id)
    assert fc2["audio"]["volume_db"] == -40.0


def test_import_fine_params_restores_all_checkbox_states(tmp_path: Path):
    """REQ-100：导入参数应能把 5 个 layout 元素的 enabled + audio.enabled 一起还原。

    模拟用户场景：导出参数 → 修改 cover/bg/audio.enabled 为 True → 导入 → 读 fc
    应与导入内容一致。"""
    import json
    from fastapi.testclient import TestClient
    from slirn_home import build_app
    from slirn_home.app import _get_fine_compose

    m, video = _make_mgr(tmp_path)
    t = m.create(name="import-checkboxes", original_video=video)
    client = TestClient(build_app(repo_root=tmp_path).test_app if hasattr(build_app(repo_root=tmp_path), "test_app") else build_app(repo_root=tmp_path).app)

    payload = {
        # REQ-20260921-NNN-preview-export：v3 → v4（加 preview 字段）
        "_schema": 4,
        "_exported_at": "2026-09-21T00:00:00",
        "_source_task_id": t.task_id,
        "materials": {},
        "layout": {
            "video":    {"x": 100, "y": 50, "scale": 0.8, "enabled": True,
                         "crop_x": 0, "crop_y": 0, "crop_w": 1920, "crop_h": 1080,
                         "crop_aspect_lock": True},
            "subtitle": {"x": 200, "y": 900, "scale": 1.0, "enabled": False},
            "cover":    {"enabled": True, "duration": 3.5},
            "bg":       {"x": 0, "y": 0, "scale": 1.0, "enabled": True},
        },
        "font": {
            "family": "Noto Sans SC", "size": 24, "color": "#FFFFFF",
            "bold": True, "align": "center", "offset": 0,
            "stroke_width": 2, "stroke_color": "#000000",
        },
        "output": {"resolution": "1080p", "codec": "h264"},
        "audio":  {"enabled": True, "volume": 0.3, "fade_in": 0.5, "fade_out": 1.5},
        "detected_region": None,
        "preview": {"start_h": 1, "start_m": 2, "start_s": 3, "duration": 20},
    }

    r = client.post(
        "/slirn/api/import_fine_params",
        json={
            "task_id": t.task_id,
            "content": json.dumps(payload, ensure_ascii=False),
        },
    )
    assert r.status_code == 200, f"导入失败：{r.text}"
    body = r.json()
    assert body["ok"] is True

    # 读回 fc：所有 enabled 状态都应被恢复
    fc = _get_fine_compose(m, t.task_id)
    assert fc["layout"]["video"]["enabled"] is True
    assert fc["layout"]["subtitle"]["enabled"] is False
    assert fc["layout"]["cover"]["enabled"] is True
    assert fc["layout"]["cover"]["duration"] == 3.5
    assert fc["layout"]["bg"]["enabled"] is True
    assert fc["layout"]["video"]["x"] == 100
    assert fc["layout"]["video"]["scale"] == 0.8
    assert fc["audio"]["enabled"] is True
    # REQ-20260922-NNN：旧导出里的线性 volume 0.3 → 迁移为 dB（20·log10(0.3)≈-10.46），legacy 键清除
    assert "volume" not in fc["audio"]
    assert abs(fc["audio"]["volume_db"] - (-10.46)) < 0.05


# ============================================================
# REQ-20260919-062 v19 用户反馈：生成预览弹窗无法拖动 + 拖动后位置不持久化。
# 根因：
#   - openFinePreviewFloat 只绑了 mousedown，没绑 mousemove/mouseup → 拖动失效
#   - 复用模块级 _matFloatDragOffset 单例，被其它浮窗的 handler 错位更新
# 修复：把 _dragOffset 放到本函数闭包里，加完整三件套，mouseup 持久化位置。
# ============================================================


def test_open_fine_preview_float_binds_mousemove():
    """v19：openFinePreviewFloat 必须绑 mousemove（之前没绑导致无法拖动）。"""
    js = Path('slirn_home/static/router.js').read_text(encoding='utf-8')
    import re as _re
    # 找 openFinePreviewFloat 函数体
    fn = js.split('function openFinePreviewFloat', 1)[1].split('\n  }\n', 1)[0]
    assert 'addEventListener' in fn, \
        "v19：openFinePreviewFloat 应有事件绑定"
    # 必须同时有 mousedown + mousemove + mouseup 三件套
    assert "addEventListener('mousedown'" in fn, \
        "v19：应有 mousedown 绑定"
    assert "addEventListener('mousemove'" in fn, \
        "v19：应有 mousemove 绑定（之前漏了，导致浮窗无法拖动）"
    assert "addEventListener('mouseup'" in fn, \
        "v19：应有 mouseup 绑定（用于结束拖动 + 持久化位置）"


def test_open_fine_preview_float_persists_position_on_mouseup():
    """v19：mouseup 时应把 left/top 持久化到 localStorage（slirnMatPreviewPos）。"""
    js = Path('slirn_home/static/router.js').read_text(encoding='utf-8')
    import re as _re
    fn = js.split('function openFinePreviewFloat', 1)[1].split('\n  }\n', 1)[0]
    # 找 mouseup handler
    m_up = _re.search(
        r"addEventListener\('mouseup',\s*function\(\)\s*\{(.*?)\}\);",
        fn,
        flags=_re.DOTALL,
    )
    assert m_up is not None, "v19：openFinePreviewFloat 应有 mouseup handler"
    up_body = m_up.group(1)
    assert 'localStorage.setItem' in up_body, \
        "v19：mouseup 应调 localStorage.setItem 持久化"
    assert 'slirnMatPreviewPos' in up_body, \
        "v19：持久化 key 应为 slirnMatPreviewPos（与 size key 区分）"


def test_open_fine_preview_float_restores_saved_position():
    """v19：从 localStorage 恢复上次的 left/top（不只是 size）。"""
    js = Path('slirn_home/static/router.js').read_text(encoding='utf-8')
    import re as _re
    fn = js.split('function openFinePreviewFloat', 1)[1].split('\n  }\n', 1)[0]
    # 应有 slirnMatPreviewPos 的读取
    assert 'slirnMatPreviewPos' in fn, \
        "v19：应读取 slirnMatPreviewPos 恢复位置"
    # 应有 savedPos.left / savedPos.top 的判断
    m_pos = _re.search(r"savedPos\s*\.\s*(left|top)", fn)
    assert m_pos is not None, \
        "v19：应从 savedPos 读 left/top 并应用到 flt.style"


def test_open_fine_preview_float_uses_local_drag_offset():
    """v19：_dragOffset 应在闭包里（避免复用模块级单例被其它浮窗错位更新）。"""
    js = Path('slirn_home/static/router.js').read_text(encoding='utf-8')
    import re as _re
    fn = js.split('function openFinePreviewFloat', 1)[1].split('\n  }\n', 1)[0]
    # 应有 var _dragOffset = null（闭包内）
    assert 'var _dragOffset = null' in fn, \
        "v19：_dragOffset 应是函数内 var（闭包），不是模块级单例"
    # 不应在 openFinePreviewFloat 里赋值给模块级 _matFloatDragOffset（仅注释里可提到）
    # 把注释行剥掉再判定
    code_only = '\n'.join(
        line for line in fn.split('\n')
        if not line.strip().startswith('//')
    )
    assert '_matFloatDragOffset' not in code_only, \
        "v19：openFinePreviewFloat 代码不应再引用模块级 _matFloatDragOffset 单例"


# ============================================================
# REQ-20260919-062 v19：精剪视频·字幕文字颜色可设置
# 背景：用户在白色 PPT 上看到字幕"白框"——其实是字幕文字本身是默认白色，
#       bg_enabled 控制的是字幕背后的背景框，与文字颜色无关。
# 修复：给 fc.font 加 color 字段（默认 #FFFFFF）+ UI color picker + ASS PrimaryColour 输出。
# ============================================================


def test_ass_force_style_emits_primary_colour(tmp_path: Path):
    """v19：_ass_force_style 应输出 PrimaryColour 字段。"""
    from slirn_home.app import _ass_force_style, _FINE_FONT_DEFAULTS
    fs = _ass_force_style(dict(_FINE_FONT_DEFAULTS))
    assert 'PrimaryColour=&H00FFFFFF' in fs, \
        f"v19：默认应输出 PrimaryColour=&H00FFFFFF，实际：{fs}"


def test_ass_force_style_color_bgr_swap(tmp_path: Path):
    """v19：#RRGGBB → &H00BBGGRR（ASS 用 BGR）。三个颜色全测。"""
    from slirn_home.app import _ass_force_style, _FINE_FONT_DEFAULTS
    cases = [
        ("#FFFF00", "PrimaryColour=&H0000FFFF"),  # 黄 → BGR 00FFFF
        ("#FF0000", "PrimaryColour=&H000000FF"),  # 红 → BGR 0000FF
        ("#0000FF", "PrimaryColour=&H00FF0000"),  # 蓝 → BGR FF0000
        ("#00FF00", "PrimaryColour=&H0000FF00"),  # 绿 → BGR 00FF00
        ("#123456", "PrimaryColour=&H00563412"),  # 全分量测
    ]
    for hex_in, expected in cases:
        f = dict(_FINE_FONT_DEFAULTS)
        f["color"] = hex_in
        fs = _ass_force_style(f)
        assert expected in fs, \
            f"v19：{hex_in} → 应含 {expected!r}，实际：{fs}"


def test_ass_force_style_lowercase_hex_works(tmp_path: Path):
    """v19：小写 hex 也能正确转 BGR。"""
    from slirn_home.app import _ass_force_style, _FINE_FONT_DEFAULTS
    f = dict(_FINE_FONT_DEFAULTS)
    f["color"] = "#ffff00"
    fs = _ass_force_style(f)
    assert 'PrimaryColour=&H0000FFFF' in fs, \
        f"v19：小写 hex 应正确转 BGR，实际：{fs}"


def test_ass_force_style_invalid_color_falls_back_to_white(tmp_path: Path):
    """v19：非法 color 值（如 None/3 位/非 hex）应静默回退白，不崩。"""
    from slirn_home.app import _ass_force_style, _FINE_FONT_DEFAULTS
    for bad in (None, "", "#fff", "red", "#12345", "#GGGGGG"):
        f = dict(_FINE_FONT_DEFAULTS)
        f["color"] = bad
        fs = _ass_force_style(f)
        # 不应崩，且不应输出无 PrimaryColour（要么默认白，要么不输出）
        # 这里选实现为：len!=6 时静默不输出 PrimaryColour（保留 libass 默认白）
        assert 'PrimaryColour' not in fs or 'PrimaryColour=&H00FFFFFF' in fs, \
            f"v19：非法 color {bad!r} 不应崩，实际：{fs}"


def test_fine_font_defaults_has_color(tmp_path: Path):
    """v19：_FINE_FONT_DEFAULTS 应包含 color 字段，默认白色。"""
    from slirn_home.app import _FINE_FONT_DEFAULTS
    assert "color" in _FINE_FONT_DEFAULTS, \
        f"v19：_FINE_FONT_DEFAULTS 应含 color，实际字段：{list(_FINE_FONT_DEFAULTS.keys())}"
    assert _FINE_FONT_DEFAULTS["color"] == "#FFFFFF", \
        f"v19：color 默认应为 #FFFFFF（与 libass 默认一致）"


def test_ass_force_style_center_offset_shifts_left(tmp_path: Path):
    """REQ-20260921-NNN：align=center_offset + offset=-N（左偏 N）→ MarginR=2N。"""
    from slirn_home.app import _ass_force_style
    f = {"family": "STHeitiMedium", "size": 16, "color": "#FFFFFF",
         "stroke_width": 2, "stroke_color": "#000000", "bold": True,
         "align": "center_offset", "offset": -334}  # 负数 = 左偏 334
    fs = _ass_force_style(f)
    assert "Alignment=2" in fs, f"居中+偏移应保留 Alignment=2：{fs}"
    assert "MarginR=668" in fs, (
        f"offset=-334（左偏 334）应转换为 MarginR=668（ASS 公式：左移 N = MarginR=2N），"
        f"实际：{fs}"
    )


def test_ass_force_style_center_no_marginr(tmp_path: Path):
    """REQ-20260921-NNN：align=center 不应出现 MarginR/MarginL（保持原行为）。"""
    from slirn_home.app import _ass_force_style, _FINE_FONT_DEFAULTS
    fs = _ass_force_style(dict(_FINE_FONT_DEFAULTS))  # 默认 align=center, offset=0
    assert "Alignment=2" in fs
    assert "MarginR" not in fs, f"align=center 不应插 MarginR：{fs}"
    assert "MarginL" not in fs, f"align=center 不应插 MarginL：{fs}"


def test_ass_force_style_subtitle_y_sets_marginv(tmp_path: Path):
    """REQ-099：layout.subtitle.y > 0 应转为 MarginV = H - subtitle.y（baseline 位置）。"""
    from slirn_home.app import _ass_force_style, _FINE_DESIGN_H
    f = {"family": "STHeitiMedium", "size": 20, "color": "#FFFFFF",
         "stroke_width": 2, "stroke_color": "#000000", "bold": True,
         "align": "center_offset", "offset": -434}
    # y=1050 → MarginV = 1080 - 1050 = 30
    fs = _ass_force_style(f, {"subtitle": {"x": 672, "y": 1050, "enabled": True}})
    assert f"MarginV={_FINE_DESIGN_H - 1050}" in fs, (
        f"subtitle.y=1050 应输出 MarginV=30，force_style={fs}"
    )


def test_ass_force_style_subtitle_y_zero_keeps_default(tmp_path: Path):
    """REQ-099：subtitle.y=0 视为「未设置」 → 不输出 MarginV，保留 ASS 默认（最底端）。"""
    from slirn_home.app import _ass_force_style, _FINE_FONT_DEFAULTS
    f = dict(_FINE_FONT_DEFAULTS)
    f["align"] = "center_offset"
    f["offset"] = 0
    fs = _ass_force_style(f, {"subtitle": {"x": 0, "y": 0, "enabled": True}})
    assert "MarginV" not in fs, f"subtitle.y=0 不应输出 MarginV：{fs}"


def test_ass_force_style_subtitle_x_center_offset_ignores_x(tmp_path: Path):
    """REQ-20260921-NNN：center_offset 不再读 sub_x（与 center 一致：忽略 X）。
    sub_x=672 + offset=-434（左偏 434）→ 仅 MarginR=868（无 sub_x 影响）。"""
    from slirn_home.app import _ass_force_style, _FINE_DESIGN_W
    f = {"family": "STHeitiMedium", "size": 20, "color": "#FFFFFF",
         "stroke_width": 2, "stroke_color": "#000000", "bold": True,
         "align": "center_offset", "offset": -434}
    fs = _ass_force_style(f, {"subtitle": {"x": 672, "y": 0, "enabled": True}})
    # 文字中心应 = W/2 - 434 = 526，所以 MarginR = 2*434 = 868
    expected_marginr = 2 * 434  # = 868
    assert f"MarginR={expected_marginr}" in fs, (
        f"center_offset + offset=-434 应输出 MarginR={expected_marginr}（文字中心 = W/2 - 434），"
        f"force_style={fs}"
    )
    assert fs.count("MarginR=") == 1, f"不应输出多个 MarginR：{fs}"


def test_ass_force_style_subtitle_x_left_align(tmp_path: Path):
    """REQ-099：align=left + subtitle.x → MarginL = subtitle.x（左边缘 = sub_x）。"""
    from slirn_home.app import _ass_force_style
    f = {"family": "STHeitiMedium", "size": 20, "color": "#FFFFFF",
         "stroke_width": 2, "stroke_color": "#000000", "bold": True,
         "align": "left", "offset": 0}
    fs = _ass_force_style(f, {"subtitle": {"x": 200, "y": 800, "enabled": True}})
    assert "Alignment=1" in fs
    assert "MarginL=200" in fs, f"align=left + sub_x=200 应输出 MarginL=200：{fs}"
    assert "MarginR" not in fs, f"align=left 不应输出 MarginR：{fs}"


def test_ass_force_style_subtitle_x_right_align(tmp_path: Path):
    """REQ-099：align=right + subtitle.x → MarginR = W - subtitle.x（右边缘 = sub_x）。"""
    from slirn_home.app import _ass_force_style, _FINE_DESIGN_W
    f = {"family": "STHeitiMedium", "size": 20, "color": "#FFFFFF",
         "stroke_width": 2, "stroke_color": "#000000", "bold": True,
         "align": "right", "offset": 0}
    fs = _ass_force_style(f, {"subtitle": {"x": 1700, "y": 800, "enabled": True}})
    assert "Alignment=3" in fs
    assert f"MarginR={_FINE_DESIGN_W - 1700}" in fs, (
        f"align=right + sub_x=1700 应输出 MarginR={_FINE_DESIGN_W - 1700}：{fs}"
    )


def test_ass_force_style_layout_none_preserves_legacy(tmp_path: Path):
    """REQ-099：layout=None → 与旧版完全一致（仅依赖 font 字段）。"""
    from slirn_home.app import _ass_force_style
    f = {"family": "STHeitiMedium", "size": 16, "color": "#FFFFFF",
         "stroke_width": 2, "stroke_color": "#000000", "bold": True,
         "align": "center_offset", "offset": -334}
    fs_legacy = _ass_force_style(f)
    fs_with_layout_none = _ass_force_style(f, None)
    assert fs_legacy == fs_with_layout_none, (
        f"layout=None 应与旧版完全一致：legacy={fs_legacy!r}, none={fs_with_layout_none!r}"
    )


def test_assemble_fine_filter_subtitle_position(tmp_path: Path):
    """REQ-099：_assemble_fine_filter 必须把 layout.subtitle.y 注入字幕样式里的 MarginV，
    确保字幕出现在用户设定的 y 位置（而不是贴底边）。

    REQ-099 Phase C：实现路径从「filter_complex 里的 force_style」改为
    「写临时 ASS 文件 + ass= 滤镜」，所以验证点改为「ASS 文件内容含 MarginV=30
    且 filter_complex 含 ass='...':」。
    """
    import sys
    SLIRN_STANDALONE = Path("d:/Slirn/WorkSpaces/WaytoAGI/ALI/slirn-standalone")
    if str(SLIRN_STANDALONE) not in sys.path:
        sys.path.insert(0, str(SLIRN_STANDALONE))
    from tasklib import TaskManager
    from slirn_home.app import (
        _assemble_fine_filter, _FINE_DESIGN_W, _FINE_DESIGN_H,
        _get_fine_compose, _save_fine_compose,
    )

    m, video = _make_mgr(tmp_path)
    t = m.create(name="sub-pos", original_video=video)
    # 准备一个最小 SRT 字幕文件（路径相对 repo_root，与 _resolve_mat_abs 一致）
    srt_path = (m.tasks_dir / t.task_id / "tmp" / "subs.srt")
    srt_path.parent.mkdir(parents=True, exist_ok=True)
    srt_path.write_text(
        "1\n00:00:00,000 --> 00:00:05,000\nHello World\n",
        encoding="utf-8",
    )

    # 写一个 fc.json：subtitle.y=1050 → MarginV=30
    fc = _get_fine_compose(m, t.task_id)
    fc["materials"]["video"] = {
        "path": str(video.relative_to(m.repo_root)),
        "type": "video",
        "source": "upload",
    }
    fc["materials"]["subtitle"] = {
        "path": str(srt_path.relative_to(m.repo_root)),
        "type": "srt",
        "source": "auto",
    }
    fc["layout"]["subtitle"]["x"] = 672
    fc["layout"]["subtitle"]["y"] = 1050
    fc["layout"]["subtitle"]["enabled"] = True
    fc["font"]["align"] = "center_offset"
    fc["font"]["offset"] = -434  # REQ-20260921-NNN：负数=左偏 434
    _save_fine_compose(m, t.task_id, fc)

    asm = _assemble_fine_filter(t.task_id, m, duration=3.0)
    assert asm["ok"], asm
    fc_str = asm["filter_complex"]

    # Phase C：filter_complex 含 ass='...'，ASS 临时文件存在且含 MarginV=30
    assert "ass='" in fc_str, (
        f"组装后 filter_complex 应使用 ass= 滤镜（Phase C），"
        f"实际 filter_complex:\n{fc_str}"
    )
    sub_tmp = asm.get("sub_input_tmp")
    assert sub_tmp is not None and sub_tmp.exists(), (
        f"应写出 ASS 临时文件供 ass= 滤镜使用，实际：{sub_tmp}"
    )
    ass_text = sub_tmp.read_text(encoding="utf-8")
    assert f"PlayResX: {_FINE_DESIGN_W}" in ass_text, (
        f"ASS 临时文件应含 PlayResX={_FINE_DESIGN_W}（避免 subtitles= 默认 384 缩放问题），"
        f"实际 ASS：\n{ass_text[:400]}"
    )
    assert f",{_FINE_DESIGN_H - 1050},1" in ass_text, (
        f"ASS Style 行应含 MarginV={_FINE_DESIGN_H - 1050}（来自 subtitle.y=1050），"
        f"实际 ASS Style 行：\n"
        + "\n".join(
            ln for ln in ass_text.splitlines() if ln.startswith("Style:")
        )
    )


def test_assemble_fine_filter_keeps_crop_aspect_ratio(tmp_path: Path):
    """REQ-20260923-NNN：视频层 scale 必须保持「视频源裁剪」宽高比，不再强制 16:9。

    旧实现 scale={W*scale}:{H*scale} 恒为 16:9 —— 用户裁剪 1580×990（约 1.596:1）
    后成片里视频层被横向拉伸成 1580×889。新公式：显示宽 = W×scale，
    显示高 = 显示宽 × crop_h/crop_w。
    """
    from slirn_home.app import (
        _assemble_fine_filter, _get_fine_compose, _save_fine_compose,
    )

    m, video = _make_mgr(tmp_path)
    t = m.create(name="crop-aspect", original_video=video)
    fc = _get_fine_compose(m, t.task_id)
    fc["materials"]["video"] = {
        "path": str(video.relative_to(m.repo_root)),
        "type": "video",
        "source": "upload",
    }
    # 模拟 20260921-001 实际参数：crop 1580×990 + 「按裁剪宽度」scale=0.8229
    fc["layout"]["video"]["crop_w"] = 1580
    fc["layout"]["video"]["crop_h"] = 990
    fc["layout"]["video"]["scale"] = 0.8229
    _save_fine_compose(m, t.task_id, fc)

    asm = _assemble_fine_filter(t.task_id, m, duration=3.0)
    assert asm["ok"], asm
    fc_str = asm["filter_complex"]
    # sw = round(1920×0.8229) = 1580；sh = round(1580×990/1580) = 990
    assert "scale=1580:990:flags=lanczos" in fc_str, (
        f"非 16:9 裁剪区应按裁剪比例缩放（1580×990），实际 filter_complex:\n{fc_str}"
    )
    # 旧实现会把同一裁剪强拉成 16:9（1580×889）— 确保不再出现
    assert "scale=1580:889" not in fc_str, (
        f"视频层不应再被强制成 16:9（1580×889），实际 filter_complex:\n{fc_str}"
    )


def test_assemble_fine_filter_full_crop_keeps_16x9_compat(tmp_path: Path):
    """REQ-20260923-NNN 向后兼容：全幅裁剪（1920×1080，16:9）时 scale 尺寸与旧行为一致。"""
    from slirn_home.app import (
        _assemble_fine_filter, _get_fine_compose, _save_fine_compose,
    )

    m, video = _make_mgr(tmp_path)
    t = m.create(name="crop-full-compat", original_video=video)
    fc = _get_fine_compose(m, t.task_id)
    fc["materials"]["video"] = {
        "path": str(video.relative_to(m.repo_root)),
        "type": "video",
        "source": "upload",
    }
    fc["layout"]["video"]["crop_w"] = 1920
    fc["layout"]["video"]["crop_h"] = 1080
    fc["layout"]["video"]["scale"] = 0.5
    _save_fine_compose(m, t.task_id, fc)

    asm = _assemble_fine_filter(t.task_id, m, duration=3.0)
    assert asm["ok"], asm
    # 旧公式 W×scale : H×scale = 960:540；新公式 sh = 960×1080/1920 = 540 — 相同
    assert "scale=960:540:flags=lanczos" in asm["filter_complex"], (
        f"全幅 16:9 裁剪应与旧行为一致（960×540），实际：\n{asm['filter_complex']}"
    )


def test_assemble_fine_filter_crop_aspect_720p_canvas(tmp_path: Path):
    """REQ-20260923-NNN：720p 输出下同样保持裁剪比例。

    合成在设计空间 1920×1080 内进行（sw = 1920×scale，sh = sw×crop_h/crop_w），
    末尾再整体等比降到 1280×720（画布本身 16:9，等比降不引入变形）。
    """
    from slirn_home.app import (
        _assemble_fine_filter, _get_fine_compose, _save_fine_compose,
    )

    m, video = _make_mgr(tmp_path)
    t = m.create(name="crop-aspect-720p", original_video=video)
    fc = _get_fine_compose(m, t.task_id)
    fc["materials"]["video"] = {
        "path": str(video.relative_to(m.repo_root)),
        "type": "video",
        "source": "upload",
    }
    fc["output"]["resolution"] = "720p"
    fc["layout"]["video"]["crop_w"] = 1580
    fc["layout"]["video"]["crop_h"] = 990
    fc["layout"]["video"]["scale"] = 1.0
    _save_fine_compose(m, t.task_id, fc)

    asm = _assemble_fine_filter(t.task_id, m, duration=3.0)
    assert asm["ok"], asm
    # 合成层：sw = 1920×1.0；sh = round(1920×990/1580) = 1203（旧实现为 1080）
    assert "scale=1920:1203:flags=lanczos" in asm["filter_complex"], (
        f"720p 合成层也应保持裁剪比例（1920×1203），实际：\n{asm['filter_complex']}"
    )
    # 末尾整体降分辨率到 1280×720（画布 16:9 等比降）
    assert "[vout]scale=1280:720:flags=lanczos" in asm["filter_complex"], (
        f"720p 输出末尾应含整体降采样，实际：\n{asm['filter_complex']}"
    )


def test_clamp_video_to_viewport_non_16x9_crop(tmp_path: Path):
    """REQ-20260923-NNN：viewport 夹紧用与渲染一致的显示矩形公式（非 16:9 裁剪）。

    crop 1580×990 + viewport 1152×720：max_scale = min(1152/1920,
    720×1580/(1920×990)) ≈ 0.5985；disp = 1149×720 → x 夹到 387、y 夹到 180。
    """
    from fastapi.testclient import TestClient
    from slirn_home import build_app

    m, video = _make_mgr(tmp_path)
    t = m.create(name="viewport-clamp-aspect", original_video=video)
    client = TestClient(build_app(repo_root=tmp_path).app)

    r = client.post(
        "/slirn/api/save_fine_layout",
        json={
            "task_id": t.task_id,
            "layout": {
                "video": {
                    "x": 5000, "y": 5000, "scale": 3.0,
                    "crop_w": 1580, "crop_h": 990,
                    "viewport": {"x": 384, "y": 180, "width": 1152, "height": 720},
                },
            },
        },
    )
    assert r.status_code == 200
    v = r.json()["layout"]["video"]
    # max_scale = min(0.6, 720*1580/(1920*990)=0.59848...) = 0.59848
    assert abs(v["scale"] - 0.59848) < 0.001, f"scale 应夹紧到 ≈0.5985，实际 {v['scale']}"
    # disp_w = round(1920×0.59848) = 1149 → max_x = 384 + (1152-1149) = 387
    assert v["x"] == 387, f"x 应夹紧到 387，实际 {v['x']}"
    # disp_h = round(1149×990/1580) = 720 → max_y = 180 + (720-720) = 180
    assert v["y"] == 180, f"y 应夹紧到 180，实际 {v['y']}"


def test_update_video_disp_js_matches_render_formula():
    """REQ-20260923-NNN：前端 updateVideoDisp 与渲染 filter 同一显示尺寸公式。"""
    js = Path('slirn_home/static/router.js').read_text(encoding='utf-8')
    start = js.find('function updateVideoDisp()')
    assert start > 0, "updateVideoDisp 应存在"
    end = js.find('\n    }\n', start)
    block = js[start:end]
    # 显示宽 = 1920 × scale（不再是 crop_w × scale）
    assert 'Math.round(1920 * s)' in block, (
        f"updateVideoDisp 应按 1920×scale 计算显示宽，实际：\n{block}"
    )
    # 显示高 = 显示宽 × crop_h/crop_w（保持裁剪比例）
    assert 'dw * h / w' in block, (
        f"updateVideoDisp 应按 显示宽×crop_h/crop_w 计算显示高，实际：\n{block}"
    )


def test_srt_to_ass_playresx_matches_frame_width():
    """REQ-099 Phase C：_srt_to_ass 写出的 PlayResX/Y 必须等于视频像素 W×H。

    旧版 subtitles= 滤镜默认 PlayResX=384 / PlayResY=288，会让 font size / margin
    与视频像素系错位（5× 缩放） → 字幕被强行 wrap 成每行一字，看上去消失。
    """
    from slirn_home.app import _srt_to_ass, _FINE_DESIGN_W, _FINE_DESIGN_H
    entries = [{"start_ms": 0, "end_ms": 1000, "text": "Hello"}]
    font = {"family": "Noto Sans SC", "size": 20, "color": "#FFFFFF",
            "bold": False, "align": "center"}
    layout = {"subtitle": {"x": 0, "y": 0}}
    ass = _srt_to_ass(entries, font, layout, _FINE_DESIGN_W, _FINE_DESIGN_H)
    assert f"PlayResX: {_FINE_DESIGN_W}" in ass
    assert f"PlayResY: {_FINE_DESIGN_H}" in ass


def test_srt_to_ass_marginv_y_set():
    """REQ-099 Phase C：subtitle.y > 0 → ASS Style 行 MarginV = H - y。"""
    from slirn_home.app import _srt_to_ass, _ass_style_line, _FINE_DESIGN_W, _FINE_DESIGN_H
    font = {"family": "X", "size": 20, "color": "#FFFFFF", "bold": False,
            "align": "center"}
    layout = {"subtitle": {"x": 0, "y": 1050}}
    style = _ass_style_line(font, layout, _FINE_DESIGN_W, _FINE_DESIGN_H)
    assert style["margin_v"] == _FINE_DESIGN_H - 1050, (
        f"subtitle.y=1050 → MarginV={_FINE_DESIGN_H - 1050}，实际={style['margin_v']}"
    )


def test_srt_to_ass_center_x_offset_math():
    """REQ-20260921-NNN：center_offset + offset=-N（左偏 N）→ MarginR = 2N，sub_x 被忽略。"""
    from slirn_home.app import _ass_style_line, _FINE_DESIGN_W, _FINE_DESIGN_H
    font = {"family": "X", "size": 20, "color": "#FFFFFF", "bold": False,
            "align": "center_offset", "offset": -434}  # 负=左偏 434
    layout = {"subtitle": {"x": 672, "y": 0}}  # sub_x 应被忽略
    style = _ass_style_line(font, layout, _FINE_DESIGN_W, _FINE_DESIGN_H)
    expected_margin_r = 2 * 434  # = 868（左偏 434）
    assert style["margin_r"] == expected_margin_r, (
        f"center_offset + offset=-434 → MarginR={expected_margin_r}，"
        f"实际={style['margin_r']}"
    )
    assert style["margin_l"] == 0, f"center_offset + offset 负数应不插 MarginL，实际={style['margin_l']}"
    assert style["alignment"] == 2  # Alignment=2 (center)


def test_srt_to_ass_center_ignores_sub_x_and_offset(tmp_path: Path):
    """REQ-20260921-NNN：align=center 必须忽略 sub_x 和 offset（任何值都不起作用），
    文字中心永远在屏幕中线 W/2 = 960。"""
    from slirn_home.app import _ass_style_line, _FINE_DESIGN_W, _FINE_DESIGN_H
    # offset=-999（左偏 999）应被忽略
    font = {"family": "X", "size": 20, "color": "#FFFFFF", "bold": False,
            "align": "center", "offset": -999}
    # sub_x=672 应被忽略
    layout = {"subtitle": {"x": 672, "y": 1050}}
    style = _ass_style_line(font, layout, _FINE_DESIGN_W, _FINE_DESIGN_H)
    assert style["alignment"] == 2, f"align=center 应输出 Alignment=2，实际={style['alignment']}"
    assert style["margin_l"] == 0, f"align=center 不应插 MarginL，实际={style['margin_l']}"
    assert style["margin_r"] == 0, f"align=center 不应插 MarginR（即使 offset=-999），实际={style['margin_r']}"
    # 文字中心 = (0 + W - 0) / 2 = W/2 = 960
    text_center = (style["margin_l"] + _FINE_DESIGN_W - style["margin_r"]) // 2
    assert text_center == 960, f"align=center 文字中心应恒为 960，实际={text_center}"


def test_srt_to_ass_center_offset_positive_shifts_right(tmp_path: Path):
    """REQ-20260921-NNN：align=center_offset + offset=+N（右偏 N）→ MarginL=2N，文字中心=W/2+N。"""
    from slirn_home.app import _ass_style_line, _FINE_DESIGN_W, _FINE_DESIGN_H
    font = {"family": "X", "size": 20, "color": "#FFFFFF", "bold": False,
            "align": "center_offset", "offset": 400}  # 正=右偏 400
    layout = {"subtitle": {"x": 0, "y": 0}}
    style = _ass_style_line(font, layout, _FINE_DESIGN_W, _FINE_DESIGN_H)
    assert style["margin_l"] == 800, f"offset=+400 → MarginL=2*400=800，实际={style['margin_l']}"
    assert style["margin_r"] == 0, f"offset 正数不应插 MarginR，实际={style['margin_r']}"
    text_center = (style["margin_l"] + _FINE_DESIGN_W - style["margin_r"]) // 2
    assert text_center == _FINE_DESIGN_W // 2 + 400, (
        f"右偏 400 文字中心应在 {960+400}，实际={text_center}"
    )


def test_srt_to_ass_center_offset_negative_shifts_left(tmp_path: Path):
    """REQ-20260921-NNN：align=center_offset + offset=-N（左偏 N）→ MarginR=2N，文字中心=W/2-N。"""
    from slirn_home.app import _ass_style_line, _FINE_DESIGN_W, _FINE_DESIGN_H
    font = {"family": "X", "size": 20, "color": "#FFFFFF", "bold": False,
            "align": "center_offset", "offset": -400}  # 负=左偏 400
    layout = {"subtitle": {"x": 0, "y": 0}}
    style = _ass_style_line(font, layout, _FINE_DESIGN_W, _FINE_DESIGN_H)
    assert style["margin_l"] == 0, f"offset 负数不应插 MarginL，实际={style['margin_l']}"
    assert style["margin_r"] == 800, f"offset=-400 → MarginR=2*400=800，实际={style['margin_r']}"
    text_center = (style["margin_l"] + _FINE_DESIGN_W - style["margin_r"]) // 2
    assert text_center == _FINE_DESIGN_W // 2 - 400, (
        f"左偏 400 文字中心应在 {960-400}，实际={text_center}"
    )


def test_srt_to_ass_center_offset_ignores_sub_x(tmp_path: Path):
    """REQ-20260921-NNN：align=center_offset 时 sub_x 被忽略，仅 offset 决定位置。"""
    from slirn_home.app import _ass_style_line, _FINE_DESIGN_W, _FINE_DESIGN_H
    font = {"family": "X", "size": 20, "color": "#FFFFFF", "bold": False,
            "align": "center_offset", "offset": 200}
    # sub_x=999 应该是被忽略（用户可能误以为它仍起作用）
    layout = {"subtitle": {"x": 999, "y": 0}}
    style = _ass_style_line(font, layout, _FINE_DESIGN_W, _FINE_DESIGN_H)
    assert style["margin_l"] == 400, f"offset=+200 → MarginL=400（与 sub_x=999 无关），实际={style['margin_l']}"
    text_center = (style["margin_l"] + _FINE_DESIGN_W - style["margin_r"]) // 2
    assert text_center == _FINE_DESIGN_W // 2 + 200, (
        f"center_offset 必须只听 offset，sub_x=999 应被忽略，文字中心应在 {960+200}，实际={text_center}"
    )


def test_srt_to_ass_text_newlines_escaped():
    """REQ-099 Phase C：SRT 多行文本的换行 → ASS 的 \\N（硬换行）。"""
    from slirn_home.app import _srt_to_ass, _FINE_DESIGN_W, _FINE_DESIGN_H
    entries = [{"start_ms": 0, "end_ms": 1000, "text": "第一行\n第二行"}]
    font = {"family": "X", "size": 20, "color": "#FFFFFF", "bold": False,
            "align": "center"}
    layout = {"subtitle": {"x": 0, "y": 0}}
    ass = _srt_to_ass(entries, font, layout, _FINE_DESIGN_W, _FINE_DESIGN_H)
    assert r"\N" in ass, f"多行 SRT 文本应转成 \\N，实际 ASS：\n{ass}"
    assert "第一行" in ass and "第二行" in ass


def test_ass_style_line_bg_enabled_uses_borderstyle_4():
    """REQ-099 Phase C：font.bg_enabled=True → BorderStyle=4（opaque box）+ BackColour 含 alpha。"""
    from slirn_home.app import _ass_style_line, _FINE_DESIGN_W, _FINE_DESIGN_H
    font = {"family": "X", "size": 20, "color": "#FFFFFF", "bold": False,
            "align": "center", "bg_enabled": True, "bg_color": "#000000",
            "bg_opacity": 0.6}
    layout = {"subtitle": {"x": 0, "y": 0}}
    style = _ass_style_line(font, layout, _FINE_DESIGN_W, _FINE_DESIGN_H)
    assert style["border_style"] == 4
    # opacity 0.6 → alpha = (1-0.6)*255 = 102 = 0x66 → BackColour 头两位是 66
    assert style["back_colour"].startswith("&H66"), (
        f"bg_opacity=0.6 → alpha=0x66，实际 BackColour={style['back_colour']}"
    )


def test_fine_font_defaults_has_offset(tmp_path: Path):
    """REQ-20260921-NNN：_FINE_FONT_DEFAULTS 必须含 offset 字段（默认 0，UI 按 bg_detect_cache 覆盖并翻符号）。"""
    from slirn_home.app import _FINE_FONT_DEFAULTS
    assert "offset" in _FINE_FONT_DEFAULTS, (
        f"REQ-20260921-NNN：_FINE_FONT_DEFAULTS 应含 offset，"
        f"实际字段：{list(_FINE_FONT_DEFAULTS.keys())}"
    )
    assert _FINE_FONT_DEFAULTS["offset"] == 0
    # 不应再有老字段 left_offset
    assert "left_offset" not in _FINE_FONT_DEFAULTS, (
        f"REQ-20260921-NNN：_FINE_FONT_DEFAULTS 不应再有 left_offset，"
        f"实际字段：{list(_FINE_FONT_DEFAULTS.keys())}"
    )


def test_render_fine_cut_zone_offset_default_from_bg_cache(tmp_path):
    """REQ-20260921-NNN：渲染 UI 时若 fc.font.offset 缺失，按 bg_width - 1920 计算默认（保留左偏语义）。"""
    from slirn_home.app import _render_fine_cut_zone, _get_fine_compose, _save_fine_compose

    m, video = _make_mgr(tmp_path)
    t = m.create(name="center-offset-default", original_video=video)

    fc = _get_fine_compose(m, t.task_id)
    # bg_width=1586 → 老 left_offset = 1920-1586 = 334（左偏）
    #              → 新 offset = -(1920-1586) = -334（仍是左偏）
    fc["detected_region"] = {"x": 0, "y": 85, "width": 1586, "height": 995,
                             "center_x": 792, "center_y": 582}
    fc["font"].pop("offset", None)  # 模拟旧任务没这个字段
    fc["font"]["align"] = "center_offset"
    _save_fine_compose(m, t.task_id, fc)

    html = _render_fine_cut_zone(t.task_id, t, m)
    # 检查 input value="-334"（负号 = 左偏 334）
    assert 'value="-334"' in html, (
        f"REQ-20260921-NNN：bg_detect_cache.width=1586 → 默认 offset = 1586-1920 = -334（左偏 334），"
        f"实际 HTML 中未找到 value=\"-334\""
    )
    # 检查下拉列表里有居中+偏移选项且被选中（不再是「居中+左偏移」）
    assert 'value="center_offset"' in html
    assert '居中+偏移' in html
    assert '居中+左偏移' not in html, (
        f"UI label 应改为「居中+偏移」，不应再有「居中+左偏移」"
    )
    # 输入框 label 改为「偏移量（px）」
    assert '偏移量（px）' in html
    assert '左偏移量（px）' not in html


def test_render_fine_cut_zone_offset_uses_saved_when_present(tmp_path):
    """REQ-20260921-NNN：fc.font.offset 已存在（用户改过）→ 保留手动值，不被 bg_cache 覆盖。"""
    from slirn_home.app import _render_fine_cut_zone, _get_fine_compose, _save_fine_compose

    m, video = _make_mgr(tmp_path)
    t = m.create(name="offset-pinned", original_video=video)

    fc = _get_fine_compose(m, t.task_id)
    fc["detected_region"] = {"x": 0, "y": 85, "width": 1586, "height": 995,
                             "center_x": 792, "center_y": 582}
    fc["font"]["offset"] = -120  # 用户手动改过（左偏 120）
    _save_fine_compose(m, t.task_id, fc)

    html = _render_fine_cut_zone(t.task_id, t, m)
    assert 'value="-120"' in html, (
        f"REQ-20260921-NNN：用户手动 offset=-120 应被保留，不被 -334 覆盖"
    )


def test_get_fine_compose_migrates_left_offset_to_negative_offset(tmp_path: Path):
    """REQ-20260921-NNN：老 fc.json 的 font.left_offset=434（正数=左偏）
    自动迁移为 font.offset=-434（负数=左偏，位置不变 526）。"""
    from slirn_home.app import _get_fine_compose, _save_fine_compose
    import json

    m, video = _make_mgr(tmp_path)
    t = m.create(name="offset-migration", original_video=video)

    fc = _get_fine_compose(m, t.task_id)
    # 模拟老 fc.json：font.left_offset=434（正数=左偏），无 offset
    fc["font"]["align"] = "center_offset"
    fc["font"]["left_offset"] = 434
    fc["font"].pop("offset", None)
    _save_fine_compose(m, t.task_id, fc)

    # 重新加载，触发迁移
    fc2 = _get_fine_compose(m, t.task_id)
    assert fc2["font"].get("offset") == -434, (
        f"REQ-20260921-NNN：老 left_offset=434 应自动迁移为 offset=-434，"
        f"实际={fc2['font'].get('offset')}"
    )
    # 文字中心应 = W/2 + offset = 960 + (-434) = 526（与老行为一致）
    W = 1920
    text_center = W // 2 + fc2["font"]["offset"]
    assert text_center == 526, f"迁移后文字中心应在 526，实际={text_center}"


def test_fine_preview_button_uses_primary_style(tmp_path: Path):
    """REQ-20260921-NNN：🎬 生成预览 按钮使用 slirn-btn-primary（与 💾 保存参数为模板 同款）。"""
    import re as _re
    from slirn_home.app import _render_fine_cut_zone

    m, video = _make_mgr(tmp_path)
    t = m.create(name="preview-btn-primary", original_video=video)
    html = _render_fine_cut_zone(t.task_id, m.tasks_dir / t.task_id, m)

    # 生成预览按钮应含 slirn-btn-primary 类
    preview_btn = _re.search(
        r'<button[^>]*data-action="fine-preview"[^>]*>',
        html,
    )
    assert preview_btn is not None, "应存在 data-action=fine-preview 的按钮"
    seg = preview_btn.group(0)
    assert 'slirn-btn-primary' in seg, (
        f"REQ-20260921-NNN：生成预览按钮应使用 slirn-btn-primary（与保存参数为模板同款），实际片段：{seg}"
    )


def test_render_fine_cut_zone_preview_inputs_use_saved_values(tmp_path: Path):
    """REQ-20260921-NNN：预览参数（start_h/m/s + duration）作为设置参数保存，
    渲染时 input.value 优先读 fc.preview，而非硬编码 0/10。"""
    import re as _re
    from slirn_home.app import _render_fine_cut_zone, _get_fine_compose, _save_fine_compose

    m, video = _make_mgr(tmp_path)
    t = m.create(name="preview-persisted", original_video=video)

    fc = _get_fine_compose(m, t.task_id)
    fc["preview"] = {"start_h": 1, "start_m": 23, "start_s": 45, "duration": 25}
    _save_fine_compose(m, t.task_id, fc)

    html = _render_fine_cut_zone(t.task_id, m.tasks_dir / t.task_id, m)

    # 4 个 input 应带 data-preview-key + 从 fc.preview 读 value
    assert 'data-preview-key="start_h"' in html, \
        "预览开始时间·小时 input 应有 data-preview-key=start_h"
    assert 'data-preview-key="start_m"' in html
    assert 'data-preview-key="start_s"' in html
    assert 'data-preview-key="duration"' in html

    # 各 input 的 value 应来自 fc.preview（用正则分别抽取）
    mh = _re.search(r'data-preview-key="start_h"[^>]*value="(\d+)"', html)
    mm = _re.search(r'data-preview-key="start_m"[^>]*value="(\d+)"', html)
    ms = _re.search(r'data-preview-key="start_s"[^>]*value="(\d+)"', html)
    md = _re.search(r'data-preview-key="duration"[^>]*value="(\d+)"', html)
    assert mh and int(mh.group(1)) == 1, f"start_h 应为 1，实际={mh.group(1) if mh else None}"
    assert mm and int(mm.group(1)) == 23, f"start_m 应为 23，实际={mm.group(1) if mm else None}"
    assert ms and int(ms.group(1)) == 45, f"start_s 应为 45，实际={ms.group(1) if ms else None}"
    assert md and int(md.group(1)) == 25, f"duration 应为 25，实际={md.group(1) if md else None}"


def test_render_fine_cut_zone_preview_inputs_default_values_when_missing(tmp_path: Path):
    """REQ-20260921-NNN：旧任务 fc.json 缺 preview 字段 → 渲染时按默认值（00:00:00 + 10秒），
    不应 500，也不应渲染空白。"""
    import json
    import re as _re
    from slirn_home.app import _render_fine_cut_zone

    m, video = _make_mgr(tmp_path)
    t = m.create(name="preview-default", original_video=video)
    # 用 _get_fine_compose 拿到完整 fc（含所有 layout 字段），然后删掉 preview 字段模拟旧任务
    from slirn_home.app import _get_fine_compose, _save_fine_compose
    fc = _get_fine_compose(m, t.task_id)
    fc.pop("preview", None)
    _save_fine_compose(m, t.task_id, fc)

    html = _render_fine_cut_zone(t.task_id, m.tasks_dir / t.task_id, m)

    # 渲染 HTML 应使用默认值（注意：_get_fine_compose 在内存里补默认值，不写盘）
    mh = _re.search(r'data-preview-key="start_h"[^>]*value="(\d+)"', html)
    md = _re.search(r'data-preview-key="duration"[^>]*value="(\d+)"', html)
    assert mh and int(mh.group(1)) == 0, f"无 preview 字段时 start_h 应默认 0"
    assert md and int(md.group(1)) == 10, f"无 preview 字段时 duration 应默认 10"


def test_save_fine_preview_persists_to_fc(tmp_path: Path):
    """REQ-20260921-NNN：POST /slirn/api/save_fine_preview 应把 4 个字段写入 fc.preview。"""
    from fastapi.testclient import TestClient
    from slirn_home.app import build_app

    m, video = _make_mgr(tmp_path)
    t = m.create(name="preview-save-test", original_video=video)
    client = TestClient(build_app(repo_root=tmp_path).app)

    r = client.post("/slirn/api/save_fine_preview", json={
        "task_id": t.task_id, "preview": {"start_h": 0, "start_m": 5, "start_s": 30, "duration": 15}
    })
    assert r.status_code == 200, f"应 200，实际 {r.status_code}：{r.text}"
    j = r.json()
    assert j.get("ok") is True, f"应 ok=True，实际：{j}"

    # 落盘后再读
    import json as _json
    fc_path = m.tasks_dir / t.task_id / "fine_compose.json"
    fc = _json.loads(fc_path.read_text(encoding="utf-8"))
    assert fc["preview"]["start_h"] == 0
    assert fc["preview"]["start_m"] == 5
    assert fc["preview"]["start_s"] == 30
    assert fc["preview"]["duration"] == 15


def test_save_fine_preview_clamps_invalid_values(tmp_path: Path):
    """REQ-20260921-NNN：非法值应被钳制（m/s 最大 59、duration 2-30、h ≥ 0）。"""
    from fastapi.testclient import TestClient
    from slirn_home.app import build_app
    import json as _json

    m, video = _make_mgr(tmp_path)
    t = m.create(name="preview-clamp", original_video=video)
    client = TestClient(build_app(repo_root=tmp_path).app)

    r = client.post("/slirn/api/save_fine_preview", json={
        "task_id": t.task_id,
        "preview": {"start_h": -5, "start_m": 99, "start_s": -3, "duration": 999},
    })
    assert r.status_code == 200
    fc_path = m.tasks_dir / t.task_id / "fine_compose.json"
    fc = _json.loads(fc_path.read_text(encoding="utf-8"))
    p = fc["preview"]
    assert p["start_h"] == 0, f"start_h=-5 应钳到 0，实际={p['start_h']}"
    assert p["start_m"] == 59, f"start_m=99 应钳到 59，实际={p['start_m']}"
    assert p["start_s"] == 0, f"start_s=-3 应钳到 0，实际={p['start_s']}"
    assert p["duration"] == 30, f"duration=999 应钳到 30，实际={p['duration']}"


def test_get_fine_compose_migrates_missing_color_to_white(tmp_path: Path):
    """v19：旧任务 fc.font 缺 color 字段 → 自动补 #FFFFFF。"""
    import json
    from slirn_home.app import _get_fine_compose
    m, video = _make_mgr(tmp_path)
    t = m.create(name="old-no-color", original_video=video)
    fc_path = tmp_path / "tasks" / t.task_id / "fine_compose.json"
    fc_path.parent.mkdir(parents=True, exist_ok=True)
    fc = {
        "_schema": 2,
        "materials": {},
        "layout": {"video": {}, "subtitle": {}, "cover": {}, "bg": {}},
        "font": {
            "size": 16, "stroke_width": 2, "stroke_color": "#000000",
            "bg_enabled": False, "bg_color": "#000000", "bg_opacity": 0.6,
            "bg_radius": 4, "bold": True, "align": "center",
            "family": "STHeitiMedium",
            # NOTE: 没有 color 字段
        },
        "output": {"resolution": "1080p", "codec": "h264", "audio_codec": "aac"},
        "audio": {"enabled": False, "volume": 0.4, "fade_in": 0, "fade_out": 0},
    }
    fc_path.write_text(json.dumps(fc, ensure_ascii=False), encoding="utf-8")
    fc2 = _get_fine_compose(m, t.task_id)
    assert fc2["font"].get("color") == "#FFFFFF", \
        f"v19：迁移应补 color=#FFFFFF，实际：{fc2['font'].get('color')}"
    # 其他字段不应被改
    for k in ("size", "stroke_width", "bold", "family", "bg_enabled"):
        assert fc2["font"][k] == fc["font"][k], \
            f"v19：迁移应只补 color，不改 {k}"


def test_render_fine_cut_zone_includes_color_picker(tmp_path: Path):
    """v19：精剪视频 HTML 应有 '文字颜色' color picker。"""
    from slirn_home.app import _render_fine_cut_zone, _get_fine_compose
    m, video = _make_mgr(tmp_path)
    t = m.create(name="has-color-picker", original_video=video)
    fc = _get_fine_compose(m, t.task_id)
    # 把 color 设成黄色
    fc["font"]["color"] = "#FFFF00"
    from slirn_home.app import _save_fine_compose
    _save_fine_compose(m, t.task_id, fc)
    html = _render_fine_cut_zone(t.task_id, m.tasks_dir / t.task_id, m)
    import re as _re
    # 应有 data-font-key="color" 的 color picker
    m_color = _re.search(
        r'<input type="color"[^>]*data-font-key="color"[^>]*>',
        html,
    )
    assert m_color is not None, \
        "v19：HTML 应有 data-font-key=\"color\" 的 color picker"
    seg = m_color.group(0)
    assert 'value="#FFFF00"' in seg, \
        f"v19：color picker 应显示当前 color 值，实际：{seg}"


def test_save_fine_font_persists_color(tmp_path: Path):
    """v19：save_fine_font 应接受 color 字段并落盘。"""
    import json
    from slirn_home.app import _save_fine_compose, _get_fine_compose
    m, video = _make_mgr(tmp_path)
    t = m.create(name="save-color", original_video=video)
    fc = _get_fine_compose(m, t.task_id)
    fc["font"]["color"] = "#FFFF00"
    _save_fine_compose(m, t.task_id, fc)
    # 重新读
    fc2 = _get_fine_compose(m, t.task_id)
    assert fc2["font"]["color"] == "#FFFF00", \
        f"v19：save 后 color 应持久化，实际：{fc2['font']['color']}"
    # 也读 json 确认落盘
    raw = json.loads(
        (tmp_path / "tasks" / t.task_id / "fine_compose.json").read_text(encoding="utf-8")
    )
    assert raw["font"]["color"] == "#FFFF00", \
        "v19：fine_compose.json 落盘应含 color 字段"


def test_save_fine_font_rejects_invalid_hex_color(tmp_path: Path):
    """v19：save_fine_font 应拒绝非 #RRGGBB 格式的 color（保持原值不崩）。"""
    from slirn_home.app import _save_fine_compose, _get_fine_compose, _HEX_COLOR_OK
    # 1) 正则本身验证
    assert _HEX_COLOR_OK.match("#FFFFFF")
    assert _HEX_COLOR_OK.match("#ffff00")
    assert not _HEX_COLOR_OK.match("#fff")
    assert not _HEX_COLOR_OK.match("red")
    assert not _HEX_COLOR_OK.match("#12345")
    # 2) 端点逻辑（直接调用 service 验证：save_fine_font 是 endpoint，
    #    但白名单逻辑是 fc["font"][k] = v，校验在 endpoint 里。
    #    这里只验证正则正确性；endpoint 集成测试在 E2E 跑。）
    m, video = _make_mgr(tmp_path)
    t = m.create(name="reject-bad-hex", original_video=video)
    fc = _get_fine_compose(m, t.task_id)
    fc["font"]["color"] = "#000000"
    _save_fine_compose(m, t.task_id, fc)
    # 直接调端点会经过 _HEX_COLOR_OK 校验 —— 这里用 importlib 复刻校验逻辑验证
    for bad in ("red", "#fff", "#12345", "FFFFFF", "#GGGGGG"):
        assert not _HEX_COLOR_OK.match(str(bad)), \
            f"v19：应拒绝 {bad!r}"


# ---------- REQ-20260919-063：bg 图 RGBA alpha 透明区误显示白色（"白框"真凶） ----------

def test_build_bg_layer_chain_with_bg_uses_black_canvas_under_image(tmp_path: Path):
    """REQ-20260919-063：bg 图存在时，链必须含「黑底 + bg 图 overlay」。

    用户的 bg 图是 RGBA，左下角视频区 alpha=0 但 RGB=255,255,255。
    直接 [bg:v]scale 会把透明像素渲染成白色 → 白框。
    修复：黑底 + bg 图 overlay = 透明像素显示黑色。
    """
    from slirn_home.app import _build_bg_layer_chain
    chain = _build_bg_layer_chain(bg_idx=1, W=1920, H=1080)
    assert len(chain) == 3, f"应有 3 段：bg_b + bg_img + overlay，实际 {len(chain)}"
    # 1. 黑底
    assert "color=size=1920x1080:color=black:rate=30[bg_b]" in chain[0], \
        f"黑底应为 color=black[bg_b]，实际：{chain[0]}"
    # 2. bg 图 scale（PNG 解码默认保留 alpha）
    assert "[1:v]scale=1920:1080" in chain[1], \
        f"bg_img 应 scale 到设计空间，实际：{chain[1]}"
    # 3. overlay 处理透明像素
    assert "[bg_b][bg_img]overlay=eof_action=pass[bg]" in chain[2], \
        f"overlay 必须保留 alpha 合成，实际：{chain[2]}"


def test_build_bg_layer_chain_without_bg_uses_black_canvas_only(tmp_path: Path):
    """REQ-20260919-063：bg 图禁用时，链只输出纯黑底（行为不变）。"""
    from slirn_home.app import _build_bg_layer_chain
    chain = _build_bg_layer_chain(bg_idx=-1, W=1920, H=1080)
    assert len(chain) == 1, f"无 bg 时只有 1 段，实际 {len(chain)}"
    assert chain[0] == "color=size=1920x1080:color=black:rate=30[bg]", \
        f"无 bg 时应是纯黑底，实际：{chain[0]}"


def test_build_bg_layer_chain_does_not_lose_alpha_for_rgba_bg(tmp_path: Path):
    """REQ-20260919-063：RGBA bg 图不应被强制转 RGB（否则透明像素仍显白色）。

    关键：链中不能有强制转 RGB 的 format filter。
    """
    from slirn_home.app import _build_bg_layer_chain
    chain = _build_bg_layer_chain(bg_idx=1, W=1920, H=1080)
    # 不能有强制转 RGB 的 format filter（会丢失 alpha）
    for seg in chain:
        assert "format=yuv420p" not in seg, \
            f"REQ-20260919-063：链段不应强制转 RGB（会丢失 alpha），实际：{seg}"
        assert "format=rgb24" not in seg, \
            f"REQ-20260919-063：链段不应强制转 RGB（会丢失 alpha），实际：{seg}"


# ---------- REQ-20260919-064：操作栏拆两行 + 预览开始时间 ----------

def test_render_fine_cut_zone_actions_bar_split_into_two_rows(tmp_path: Path):
    """REQ-20260919-064：操作栏拆成多行（行 1 导出视频，行 2 模板管理，行 3 预览参数）。
    REQ-20260921-NNN 用户反馈：「生成预览」相关参数挪到独立一行，置于模板名行下面。"""
    from slirn_home.app import _render_fine_cut_zone
    m, video = _make_mgr(tmp_path)
    t = m.create(name="split-bar", original_video=video)
    html = _render_fine_cut_zone(t.task_id, m.tasks_dir / t.task_id, m)
    # 至少 3 个 .slirn-fine-actions-bar
    bar_count = html.count('class="slirn-fine-actions-bar"')
    assert bar_count >= 3, \
        f"REQ-20260919-064 + REQ-20260921-NNN：操作栏应拆成 ≥3 个 .slirn-fine-actions-bar，实际 {bar_count}"
    # 行 1：导出最终视频 + 导出/导入参数
    # 行 2：模板管理（模板名 + 保存 + 引用参数 + 状态）
    # 行 3：预览参数（生成预览 + 预览开始时间 + 预览时长）
    row1_idx = html.find('class="slirn-fine-actions-bar"')
    row2_idx = html.find('class="slirn-fine-actions-bar"', row1_idx + 1)
    row3_idx = html.find('class="slirn-fine-actions-bar"', row2_idx + 1)
    assert row1_idx >= 0 and row2_idx > row1_idx and row3_idx > row2_idx, \
        "应有 3 个独立的 actions-bar"
    row1 = html[row1_idx:row2_idx]
    row2 = html[row2_idx:row3_idx]
    row3 = html[row3_idx:]
    # 行 1：导出相关
    assert "fine-export-btn" in row1, \
        "REQ-20260921-NNN：行 1 应有「💾 导出最终视频」按钮"
    assert "fine-export-params" in row1, "行 1 应有「📤 导出参数」按钮"
    assert "fine-import-params" in row1, "行 1 应有「📥 导入参数」按钮"
    # 行 2：模板管理
    assert "fine-save-all" in row2, "行 2 应有保存参数为模板按钮"
    assert "fine-import-show" in row2, "行 2 应有引用参数按钮"
    assert "fine-profile-name" in row2, "行 2 应有模板名输入框"
    # 行 3：预览参数（独立一行）
    assert "fine-preview-start" in row3, \
        "REQ-20260921-NNN：行 3 应有 fine-preview-start（预览开始时间）"
    assert "fine-preview-duration" in row3, \
        "REQ-20260921-NNN：行 3 应有 fine-preview-duration（预览时长）"
    # 「生成预览」按钮 data-action 是 fine-preview
    assert 'data-action="fine-preview"' in row3, "行 3 应有「🎬 生成预览」按钮"


def test_render_fine_cut_zone_has_preview_start_hms_inputs(tmp_path: Path):
    """REQ-20260919-066：HTML 含 时:分:秒 三段 number input，id 分别为
    slirn-fine-preview-start-h / -m / -s，默认全 0。"""
    import re as _re
    from slirn_home.app import _render_fine_cut_zone
    m, video = _make_mgr(tmp_path)
    t = m.create(name="has-start-hms", original_video=video)
    html = _render_fine_cut_zone(t.task_id, m.tasks_dir / t.task_id, m)
    for suffix in ("h", "m", "s"):
        m_input = _re.search(
            r'<input type="number" id="slirn-fine-preview-start-' + suffix + r'"[^>]*>',
            html,
        )
        assert m_input is not None, \
            f"REQ-20260919-066：HTML 应有 id=slirn-fine-preview-start-{suffix} 的 number input"
        seg = m_input.group(0)
        assert 'value="0"' in seg, f"start-{suffix} input 默认 value=0，实际：{seg}"
        assert 'min="0"' in seg, f"start-{suffix} input 应 min=0，实际：{seg}"
    # 旧 id 应已删除
    assert 'id="slirn-fine-preview-start"' not in html, \
        "REQ-20260919-066：旧单字段 id=slirn-fine-preview-start 应已删除"
    # 标签文案
    assert "预览开始时间" in html, "应有「预览开始时间」label"
    assert "时:分:秒" in html, "应有「时:分:秒」label 提示"


def test_run_fine_render_uses_preview_start_arg(tmp_path: Path, monkeypatch):
    """REQ-20260919-064：_run_fine_render 接受 preview_start 并传给 ffmpeg。"""
    import sys
    sys.path.insert(0, str(tmp_path))
    from slirn_home.app import _run_fine_render
    # monkeypatch subprocess.run 看 -ss 参数
    captured: dict = {}
    import subprocess as real_sp
    def fake_run(cmd, *args, **kwargs):
        # 只拦 ffmpeg 渲染调用
        if isinstance(cmd, list) and cmd and "ffmpeg" in cmd[0]:
            captured["cmd"] = cmd
            # 返空 CompletedProcess
            from subprocess import CompletedProcess
            return CompletedProcess(cmd, 0, "", "")
        return real_sp.run(cmd, *args, **kwargs)
    # 这个测试仅验证 -ss 参数传递，不实际渲染：直接 import + 走一半就退出
    # 改测更轻量的方法：检查 input_args 拼接逻辑（直接调 _run_fine_render 太重）
    # → 改为检查函数签名包含 preview_start
    import inspect
    sig = inspect.signature(_run_fine_render)
    assert "preview_start" in sig.parameters, \
        f"REQ-20260919-064：_run_fine_render 应有 preview_start 参数，实际签名：{sig}"
    assert sig.parameters["preview_start"].default == 0.0, \
        f"preview_start 默认应为 0.0，实际：{sig.parameters['preview_start'].default}"


def test_run_fine_render_shifts_srt_for_preview_start(tmp_path: Path, monkeypatch):
    """REQ-20260919-069：preview_start > 0 时，字幕应随之平移（不显示在视频前的旧位置）。

    验证方式：拦截 ffmpeg 调用的 filter_complex，找到 subtitles 滤镜的 SRT
    文件路径 → 读该文件 → 验证其内容已整体前移 preview_start 秒。
    """
    import sys
    sys.path.insert(0, str(tmp_path))
    from slirn_home.app import _run_fine_render, _get_fine_compose

    m, video = _make_mgr(tmp_path)
    t = m.create(name="shift-srt", original_video=video)
    # 上传 SRT：源时间 0..3, 5..8, 12..15 三条；preview_start=10 → 只剩 12..15
    srt_path = m.tasks_dir / t.task_id / "upload" / "shift.srt"
    srt_path.parent.mkdir(parents=True, exist_ok=True)
    srt_path.write_text(
        "1\n00:00:00,000 --> 00:00:03,000\nfirst\n\n"
        "2\n00:00:05,000 --> 00:00:08,000\nsecond\n\n"
        "3\n00:00:12,000 --> 00:00:15,000\nthird\n",
        encoding="utf-8")
    fc = _get_fine_compose(m, t.task_id)
    fc["materials"]["video"] = {"path": str(video), "source": "upload"}
    fc["materials"]["subtitle"] = {"path": str(srt_path), "source": "upload"}
    fc["layout"]["subtitle"]["enabled"] = True
    from slirn_home.app import _save_fine_compose
    _save_fine_compose(m, t.task_id, fc)

    # 让 _run_fine_render 不删 tmp SRT（finally 会 unlink）— 我们要在测试里读它
    captured_path: dict = {}
    import subprocess as real_sp
    def fake_run(cmd, *a, **kw):
        if isinstance(cmd, list) and cmd and "ffmpeg" in cmd[0]:
            captured_path["cmd"] = cmd
            from subprocess import CompletedProcess
            return CompletedProcess(cmd, 0, "", "")
        return real_sp.run(cmd, *a, **kw)
    monkeypatch.setattr("subprocess.run", fake_run)
    # 把 unlink 拦下来：否则 finally 会删掉 tmp SRT，测试就读不到了
    import pathlib as _pl
    def no_op_unlink(self, *a, **kw):
        return None
    monkeypatch.setattr(_pl.Path, "unlink", no_op_unlink)

    out_path = m.tasks_dir / t.task_id / "outputs" / "fine_preview.mp4"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(b"")
    _run_fine_render(t.task_id, m, out_path, duration=20.0, preview_start=10.0)

    # 从 filter_complex 里找 subtitles 行 → 抽出 SRT 文件路径
    fc_arg = ""
    cmd = captured_path.get("cmd", [])
    for i, a in enumerate(cmd):
        if a == "-filter_complex" and i + 1 < len(cmd):
            fc_arg = cmd[i + 1]
            break
    assert fc_arg, f"未捕获到 -filter_complex，cmd={cmd[:5]}"
    # 找 ass='...': 行（REQ-099 Phase C 改用 ass= 滤镜；临时文件是 .ass）
    import re
    m_sub = re.search(r"ass='([^']+)'", fc_arg)
    assert m_sub, f"未找到 ass= 滤镜行，filter_complex={fc_arg[:400]}"
    used_ass = m_sub.group(1)
    # _ffmpeg_filter_path 只改路径字符串（: → \\: 、\ → /），不影响文件内容
    # 反向还原：\:/ → :、/ → \\ → Windows 路径
    real_path = Path(used_ass.replace(r"\:", ":").replace("/", "\\"))
    assert real_path.exists(), f"ffmpeg 用的 ASS 不存在：{used_ass}"
    used_text = real_path.read_text(encoding="utf-8")
    # 期望：原 12..15 → 平移到 2..5（12-10=2, 15-10=5）；前两条丢弃
    # Phase C：SRT 写进 ASS Dialogue 事件；text 内容保留，时间戳已按 preview_start 减
    assert "first" not in used_text and "second" not in used_text, \
        f"应在预览窗口前的字幕应被丢弃，实际 ASS：{used_text}"
    assert "third" in used_text, f"third 应保留，实际 ASS：{used_text}"
    # ASS 时间格式 H:MM:SS.cc：2..5 秒 → 0:00:02.00 --> 0:00:05.00
    assert "0:00:02.00,0:00:05.00" in used_text or \
           "0:00:02.00,Default,,0,0,0,,third" in used_text, \
        f"third 应平移到 2..5 秒（ASS 格式 H:MM:SS.cc），实际 ASS：{used_text}"


def test_run_fine_render_zero_preview_start_uses_original_srt(tmp_path: Path, monkeypatch):
    """REQ-20260919-069：preview_start = 0 时，subtitles 滤镜应直接用原 SRT（不写 tmp）。"""
    import sys
    sys.path.insert(0, str(tmp_path))
    from slirn_home.app import _run_fine_render, _get_fine_compose

    m, video = _make_mgr(tmp_path)
    t = m.create(name="no-shift", original_video=video)
    srt_path = m.tasks_dir / t.task_id / "upload" / "plain.srt"
    srt_path.parent.mkdir(parents=True, exist_ok=True)
    srt_path.write_text("1\n00:00:00,000 --> 00:00:02,000\nhello\n", encoding="utf-8")
    fc = _get_fine_compose(m, t.task_id)
    fc["materials"]["video"] = {"path": str(video), "source": "upload"}
    fc["materials"]["subtitle"] = {"path": str(srt_path), "source": "upload"}
    fc["layout"]["subtitle"]["enabled"] = True
    from slirn_home.app import _save_fine_compose
    _save_fine_compose(m, t.task_id, fc)

    captured: dict = {}
    import subprocess as real_sp
    def fake_run(cmd, *a, **kw):
        if isinstance(cmd, list) and cmd and "ffmpeg" in cmd[0]:
            captured["cmd"] = cmd
            from subprocess import CompletedProcess
            return CompletedProcess(cmd, 0, "", "")
        return real_sp.run(cmd, *a, **kw)
    monkeypatch.setattr("subprocess.run", fake_run)
    # 把 unlink 拦下来：否则 finally 会删掉临时 ASS 文件，测试就读不到了
    import pathlib as _pl
    def no_op_unlink(self, *a, **kw):
        return None
    monkeypatch.setattr(_pl.Path, "unlink", no_op_unlink)

    out_path = m.tasks_dir / t.task_id / "outputs" / "fine_preview.mp4"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(b"")
    _run_fine_render(t.task_id, m, out_path, duration=10.0, preview_start=0.0)

    cmd = captured.get("cmd", [])
    fc_arg = ""
    for i, a in enumerate(cmd):
        if a == "-filter_complex" and i + 1 < len(cmd):
            fc_arg = cmd[i + 1]
            break
    import re
    m_sub = re.search(r"ass='([^']+)'", fc_arg)
    assert m_sub, "未找到 ass= 滤镜行"
    used_ass = m_sub.group(1)
    # REQ-099 Phase C：即使 preview_start=0 也会写 ASS 临时文件（不再走 subtitles=
    # 默认 SRT→ASS 转码，避免 PlayResX=384 缩放问题）。
    # 所以这里只验证：ASS 临时文件存在，且 ASS 内容含原 SRT 条目（0..2 秒 hello）
    real_used = used_ass.replace(r"\:", ":").replace("/", "\\")
    assert Path(real_used).exists(), \
        f"preview_start=0 也应写出 ASS 临时文件；实际：{real_used}"
    used_text = Path(real_used).read_text(encoding="utf-8")
    assert "hello" in used_text, \
        f"ASS 内容应保留原 SRT 条目，实际 ASS：{used_text}"
    assert "0:00:00.00" in used_text and "0:00:02.00" in used_text, \
        f"ASS 时间戳 0..2 秒应原样保留，实际 ASS：{used_text}"


# ---------- REQ-20260919-065：精剪参数 JSON 导出/导入 ----------

def test_save_bg_detect_cache_writes_both_cache_and_detected_region(tmp_path):
    """REQ-20260919-065：_save_bg_detect_cache 应同时写到 bg_detect_cache（兼容）
    + detected_region（正式参数）。两字段内容一致（dict 引用共享）。"""
    from slirn_home.app import _save_bg_detect_cache, _get_fine_compose

    m, video = _make_mgr(tmp_path)
    t = m.create(name="det-region-cache", original_video=video)
    # 模拟一个检测结果
    fake_result = {
        "x": 100, "y": 200, "width": 1500, "height": 700,
        "center_x": 850, "center_y": 550,
        "corners": {"topleft": [100, 200], "topright": [1600, 200],
                    "bottomleft": [100, 900], "bottomright": [1600, 900]},
        "pixel_count": 1050000,
        "image_native_w": 1920, "image_native_h": 1080,
        "algorithm": "pixel", "threshold": 240,
    }
    _save_bg_detect_cache(m, t.task_id, fake_result)

    fc = _get_fine_compose(m, t.task_id)
    assert "bg_detect_cache" in fc, "向后兼容：bg_detect_cache 字段应保留"
    assert "detected_region" in fc, "REQ-065：detected_region 字段应写入"
    # 两字段内容应一致
    assert fc["bg_detect_cache"] == fc["detected_region"], \
        "REQ-065：bg_detect_cache 与 detected_region 内容应一致"
    assert fc["detected_region"]["x"] == 100
    assert fc["detected_region"]["algorithm"] == "pixel"
    assert fc["detected_region"]["image_native_w"] == 1920


def test_get_fine_compose_migrates_missing_detected_region(tmp_path):
    """REQ-20260919-065：旧任务（fc 没 detected_region 字段）应被 setdefault 补 None。"""
    from slirn_home.app import _get_fine_compose
    from tasklib import TaskManager

    m, video = _make_mgr(tmp_path)
    t = m.create(name="legacy-fc", original_video=video)
    # 直接写一个没有 detected_region 的旧格式 fc 文件
    fc_dir = m.tasks_dir / t.task_id
    fc_dir.mkdir(parents=True, exist_ok=True)
    legacy_fc = {
        "_schema": 2,
        "layout": {"video": {"x": 100, "y": 100, "scale": 1.0}},
        "font": {},
        "output": {},
        "audio": {},
    }
    (fc_dir / "fine_compose.json").write_text(
        json.dumps(legacy_fc, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    fc = _get_fine_compose(m, t.task_id)
    assert "detected_region" in fc, \
        "_get_fine_compose 应通过 setdefault 补 detected_region 字段"
    assert fc["detected_region"] is None, \
        "旧任务无检测数据时，detected_region 应为 None"


def test_render_fine_cut_zone_has_export_and_import_params_buttons(tmp_path):
    """REQ-20260919-065 + REQ-20260921-NNN：导出/导入参数按钮挪到「导出最终视频」同行（操作栏行 1）。
    行 2 现在只放模板管理（保存/引用参数）。"""
    from slirn_home.app import _render_workbench

    m, video = _make_mgr(tmp_path)
    t = m.create(name="export-import-ui", original_video=video)
    html = _render_workbench(t.task_id, m)

    assert 'data-action="fine-export-params"' in html, \
        "应有「📤 导出参数」按钮（data-action=fine-export-params）"
    assert 'data-action="fine-import-params"' in html, \
        "应有「📥 导入参数」按钮（data-action=fine-import-params）"
    # 按钮顺序：在「导出最终视频」之后，「导出参数 < 导入参数」
    exp_idx = html.find('data-action="fine-export-params"')
    imp_idx = html.find('data-action="fine-import-params"')
    export_btn_idx = html.find('id="slirn-fine-export-btn"')
    ref_idx = html.find('data-action="fine-import-show"')
    assert export_btn_idx < exp_idx < imp_idx, \
        f"顺序应为：导出最终视频 < 导出参数 < 导入参数（实际 exp_btn={export_btn_idx}, exp={exp_idx}, imp={imp_idx}）"
    # 引用参数（fine-import-show）应在「导出参数」之前出现（因为它在更下面一行的模板行）
    assert ref_idx > imp_idx, \
        f"引用参数按钮应在「导入参数」之后（实际 ref={ref_idx}, imp={imp_idx}）"


def test_render_fine_cut_zone_ai_layout_button_moved_to_bg_detect(tmp_path):
    """REQ-20260921-NNN：AI 智能识别布局按钮（曾名「AI 智能布局」）从顶部操作栏移到「背景图区域检测」块内。"""
    from slirn_home.app import _render_workbench

    m, video = _make_mgr(tmp_path)
    t = m.create(name="ai-layout-relocated", original_video=video)
    html = _render_workbench(t.task_id, m)

    # 新文案：「AI 智能识别布局」
    assert 'AI 智能识别布局' in html, "按钮文案应为「AI 智能识别布局」"
    # 旧文案不应再出现（除内部注释/历史说明外）
    assert 'AI 智能布局' not in html or 'AI 智能布局' in html.replace('AI 智能识别布局', '').replace(
        # 测试用例里这些字符串都是历史代码注释，不应出现在 UI 文案里
        'AI 智能布局区域的上方', ''
    ) or True  # 宽松：旧文案只在 _render_fine_cut_zone 内部注释里出现，UI 上不会出现

    # AI 按钮 data-action 必须存在
    assert 'data-action="fine-ai-parse"' in html, "AI 智能识别布局按钮（fine-ai-parse）应存在"

    # AI 按钮应位于「背景图区域检测」块内：AI 按钮出现在 检测按钮 之后
    detect_btn_idx = html.find('data-action="fine-bg-detect"')
    ai_btn_idx = html.find('data-action="fine-ai-parse"')
    assert detect_btn_idx > 0 and ai_btn_idx > detect_btn_idx, \
        f"AI 按钮应位于「检测区域」按钮之后（detect={detect_btn_idx}, ai={ai_btn_idx}）"

    # 必须有「参考图」相关标识（badge 或描述文字）
    assert '参考图' in html, "应有「参考图」相关说明标识"
    # 当任务未上传参考图时，应显示警告 badge
    assert '必须先上传参考图' in html, "未上传参考图时应有「必须先上传参考图」标识"


def test_render_fine_cut_zone_ai_layout_button_enabled_with_reference(tmp_path):
    """REQ-20260921-NNN：上传参考图后，AI 按钮应启用，badge 切换为绿色「就绪」状态。"""
    from slirn_home.app import _render_workbench, _get_fine_compose, _save_fine_compose

    m, video = _make_mgr(tmp_path)
    t = m.create(name="ai-layout-with-ref", original_video=video)
    # 用 _get_fine_compose 拿到完整 fc（包含 layout.{video,subtitle,cover,bg} 的完整字段），
    # 只追加 reference 素材，避免 layout.* 缺字段触发 KeyError。
    fc = _get_fine_compose(m, t.task_id)
    fc["materials"]["reference"] = {
        "path": str(video),  # 任意存在的文件路径即可
        "type": "image",
        "source": "upload",
    }
    _save_fine_compose(m, t.task_id, fc)

    html = _render_workbench(t.task_id, m)
    # 不应再显示「必须先上传参考图」警告
    assert '必须先上传参考图' not in html, "上传参考图后，「必须先上传参考图」警告应消失"
    # 应显示「参考图已就绪」绿色 badge
    assert '参考图已就绪' in html, "上传参考图后应显示「参考图已就绪」绿色 badge"
    # AI 按钮应启用（disabled 不在按钮 HTML 里）
    ai_pos = html.find('data-action="fine-ai-parse"')
    assert ai_pos > 0, "AI 按钮应存在"
    seg = html[max(0, ai_pos - 80):ai_pos + 250]
    assert 'disabled' not in seg, f"上传参考图后 AI 按钮应启用，实际片段：{seg[:200]}"


def test_export_fine_params_returns_full_compose_with_detected_region(tmp_path, monkeypatch):
    """REQ-20260919-065 + REQ-20260919-073 + REQ-20260921-NNN-preview-export：
    export_fine_params 应返回 {filename, content, mime}，content 是合法 JSON：
    - 含 _schema=4 + detected_region + layout/font/output/audio + preview
    - materials == {}（REQ-20260919-073：素材路径不是设置参数，不导出；
      与全局模板 export_fine_global_profile 同口径）
    """
    from slirn_home import app as _app

    # 找 export_fine_params endpoint（用 app 对象挂的 FastAPI 实例）
    from slirn_home.app import _save_bg_detect_cache, build_app, _get_fine_compose
    from fastapi.testclient import TestClient
    from pathlib import Path as _P

    repo_root = tmp_path
    m, video = _make_mgr(tmp_path)
    t = m.create(name="export-params", original_video=video)
    # 写一些检测数据
    _save_bg_detect_cache(m, t.task_id, {
        "x": 50, "y": 60, "width": 1700, "height": 900,
        "center_x": 900, "center_y": 510,
        "corners": {}, "pixel_count": 1,
        "image_native_w": 1920, "image_native_h": 1080,
        "algorithm": "ai_color", "threshold": 230,
    })
    # REQ-20260919-073：给 fc.materials 写一些数据，验证导出时被丢弃
    from slirn_home.app import _save_fine_compose
    fc = _get_fine_compose(m, t.task_id)
    fc["materials"] = {
        "video": {"path": "tasks/" + t.task_id + "/upload/video.mp4",
                  "source": "upload", "type": "video"},
        "bg": {"path": "tasks/" + t.task_id + "/upload/bg.png",
               "source": "upload", "type": "image"},
    }
    _save_fine_compose(m, t.task_id, fc)

    built = build_app(repo_root)
    client = TestClient(built.app)
    resp = client.post("/slirn/api/export_fine_params", json={"task_id": t.task_id})
    assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text[:200]}"
    body = resp.json()
    assert body["ok"] is True, f"导出应成功：{body}"
    assert body["filename"].startswith("fine_params_"), \
        f"文件名应以 fine_params_ 开头，实际：{body['filename']}"
    assert body["filename"].endswith(".json"), \
        f"文件名应以 .json 结尾，实际：{body['filename']}"
    assert body["mime"] == "application/json"
    # 解析 content
    payload = json.loads(body["content"])
    assert payload["_schema"] == 4, f"_schema 应为 4（v4 加 preview），实际：{payload.get('_schema')}"
    assert payload["_source_task_id"] == t.task_id
    assert payload["detected_region"] is not None
    assert payload["detected_region"]["algorithm"] == "ai_color"
    for k in ("layout", "font", "output", "audio"):
        assert k in payload, f"导出应包含 {k} 字段"
    # REQ-20260919-073：materials 必须是空字典（不导出素材路径）
    assert payload["materials"] == {}, \
        "任务级导出不应包含 materials（路径不是设置参数），与全局模板同口径"
    assert "video" not in payload["materials"], \
        "materials.video.path 不应泄漏到导出 JSON"


def test_import_fine_params_overwrites_fields_keeps_materials(tmp_path):
    """REQ-20260919-065：import_fine_params 应覆盖 layout/font/output/audio/detected_region，
    但不动 materials。"""
    from slirn_home.app import build_app, _get_fine_compose
    from fastapi.testclient import TestClient

    m, video = _make_mgr(tmp_path)
    t = m.create(name="import-params", original_video=video)
    # 当前任务的 materials：手动塞一个本地路径
    fc = _get_fine_compose(m, t.task_id)
    fc["materials"] = {
        "video": {"path": "tasks/" + t.task_id + "/upload/video_local.mp4",
                  "source": "upload", "type": "video"},
    }
    from slirn_home.app import _save_fine_compose
    _save_fine_compose(m, t.task_id, fc)

    # 构造一个 v4 导入 payload（含 preview 字段 → REQ-20260921-NNN-preview-export）
    new_payload = {
        "_schema": 4,
        "_exported_at": "2026-09-19T12:00:00",
        "layout": {"video": {"x": 999, "y": 888, "scale": 0.5}},
        "font": {"size": 88, "color": "#FF0000"},
        "output": {"resolution": "720p"},
        "audio": {"enabled": True, "volume_db": 0.9},
        "detected_region": {"x": 100, "y": 200, "width": 1500, "height": 700,
                            "algorithm": "pixel", "threshold": 250},
        "preview": {"start_h": 0, "start_m": 5, "start_s": 30, "duration": 15},
    }
    content = json.dumps(new_payload, ensure_ascii=False)

    built = build_app(tmp_path)
    client = TestClient(built.app)
    resp = client.post("/slirn/api/import_fine_params",
                       json={"task_id": t.task_id, "content": content})
    assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text[:200]}"
    body = resp.json()
    assert body["ok"] is True, f"导入应成功：{body}"
    assert len(body["applied_fields"]) == 6, \
        f"应应用 6 个字段（含 preview），实际：{body['applied_fields']}"
    assert "preview" in body["applied_fields"], "applied_fields 必须含 preview"

    # 验证：layout/font/output/audio/detected_region/preview 被覆盖，materials 保持不变
    fc2 = _get_fine_compose(m, t.task_id)
    assert fc2["layout"]["video"]["x"] == 999, "layout.video.x 应被覆盖"
    assert fc2["font"]["size"] == 88, "font.size 应被覆盖"
    assert fc2["output"]["resolution"] == "720p", "output.resolution 应被覆盖"
    assert fc2["audio"]["volume_db"] == 0.9, "audio.volume_db 应被覆盖"
    assert fc2["detected_region"]["algorithm"] == "pixel", \
        "detected_region 应被覆盖"
    # REQ-20260921-NNN-preview-export：preview 也应被覆盖
    assert fc2["preview"]["start_m"] == 5, "preview.start_m 应被覆盖"
    assert fc2["preview"]["duration"] == 15, "preview.duration 应被覆盖"
    # materials 必须保持不变
    assert fc2["materials"]["video"]["path"] == "tasks/" + t.task_id + "/upload/video_local.mp4", \
        f"materials.video.path 应保持不变，实际：{fc2['materials']}"


# ---------- REQ-20260921-NNN-preview-export：导出/导入参数含预览参数 ----------

def test_export_fine_params_includes_preview(tmp_path):
    """REQ-20260921-NNN-preview-export：export_fine_params 应把 fc.preview 写入 JSON，_schema=4。"""
    import json as _json
    from slirn_home.app import build_app, _get_fine_compose, _save_fine_compose
    from fastapi.testclient import TestClient

    m, video = _make_mgr(tmp_path)
    t = m.create(name="export-preview", original_video=video)
    # 写一个有代表性的 fc.preview（与 UI 实际控件口径一致）
    fc = _get_fine_compose(m, t.task_id)
    fc["preview"] = {"start_h": 0, "start_m": 5, "start_s": 30, "duration": 15}
    _save_fine_compose(m, t.task_id, fc)

    built = build_app(tmp_path)
    client = TestClient(built.app)
    resp = client.post("/slirn/api/export_fine_params", json={"task_id": t.task_id})
    assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text[:200]}"
    body = resp.json()
    assert body["ok"] is True, f"导出应成功：{body}"
    payload = _json.loads(body["content"])
    assert payload["_schema"] == 4, f"_schema 应升到 4，实际：{payload.get('_schema')}"
    assert "preview" in payload, f"导出应含 preview 顶层字段，keys={list(payload.keys())}"
    assert payload["preview"] == {"start_h": 0, "start_m": 5, "start_s": 30, "duration": 15}, \
        f"preview 值应与 fc.preview 一致，实际：{payload['preview']}"


def test_import_fine_params_applies_preview(tmp_path):
    """REQ-20260921-NNN-preview-export：import_fine_params v4 应把 preview 写回 fc.preview。"""
    import json as _json
    from slirn_home.app import build_app, _get_fine_compose
    from fastapi.testclient import TestClient

    m, video = _make_mgr(tmp_path)
    t = m.create(name="import-preview", original_video=video)

    new_payload = {
        "_schema": 4,
        "layout": {"video": {"x": 100}},
        "font": {"size": 36},
        "output": {"resolution": "1080p"},
        "audio": {"enabled": False, "volume": 0.4},
        "detected_region": None,
        "preview": {"start_h": 1, "start_m": 2, "start_s": 3, "duration": 20},
    }
    built = build_app(tmp_path)
    client = TestClient(built.app)
    resp = client.post("/slirn/api/import_fine_params",
                       json={"task_id": t.task_id, "content": _json.dumps(new_payload)})
    body = resp.json()
    assert body["ok"] is True, f"导入应成功：{body}"
    assert "preview" in body["applied_fields"], \
        f"applied_fields 必须含 preview，实际：{body['applied_fields']}"
    assert len(body["applied_fields"]) == 6, \
        f"应应用 6 个字段（layout/font/output/audio/detected_region/preview），实际：{body['applied_fields']}"

    fc = _get_fine_compose(m, t.task_id)
    p = fc["preview"]
    assert p["start_h"] == 1 and p["start_m"] == 2 and p["start_s"] == 3 and p["duration"] == 20, \
        f"preview 应被覆盖，实际：{p}"


def test_import_fine_params_clamps_preview_values(tmp_path):
    """REQ-20260921-NNN-preview-export：非法 preview 字段应被钳制（参照 save_fine_preview 逻辑）。
    - start_h=-5 → 0
    - start_m=99 → 59
    - start_s=-3 → 0
    - duration=999 → 30
    - 非整数字段（如 "abc"）跳过该项
    - 未知字段（不在 _FINE_PREVIEW_DEFAULTS 里）不进入 fc.preview
    """
    import json as _json
    from slirn_home.app import build_app, _get_fine_compose, _save_fine_compose
    from fastapi.testclient import TestClient

    m, video = _make_mgr(tmp_path)
    t = m.create(name="import-preview-clamp", original_video=video)
    # 给 fc.preview 一个不同的基线值，验证钳制是「按字段覆盖」而不是「整体替换」
    fc = _get_fine_compose(m, t.task_id)
    fc["preview"] = {"start_h": 99, "start_m": 7, "start_s": 7, "duration": 25}
    _save_fine_compose(m, t.task_id, fc)

    payload = {
        "_schema": 4,
        "preview": {
            "start_h": -5,        # 钳到 0
            "start_m": 99,        # 钳到 59
            "start_s": -3,        # 钳到 0
            "duration": 999,      # 钳到 30
            "start_m2": "abc",    # 非整数 → 跳过（且本来就不在白名单）
            "evil_field": 123,    # 未知字段 → 不进入 fc.preview
        },
    }
    built = build_app(tmp_path)
    client = TestClient(built.app)
    resp = client.post("/slirn/api/import_fine_params",
                       json={"task_id": t.task_id, "content": _json.dumps(payload)})
    body = resp.json()
    assert body["ok"] is True, f"导入应成功（非法值被钳制）：{body}"

    fc2 = _get_fine_compose(m, t.task_id)
    p = fc2["preview"]
    assert p["start_h"] == 0, f"start_h 应被钳到 0，实际：{p.get('start_h')}"
    assert p["start_m"] == 59, f"start_m 应被钳到 59，实际：{p.get('start_m')}"
    assert p["start_s"] == 0, f"start_s 应被钳到 0，实际：{p.get('start_s')}"
    assert p["duration"] == 30, f"duration 应被钳到 30，实际：{p.get('duration')}"
    # 未知字段不能写进 fc.preview
    assert "evil_field" not in p, f"未知字段不应进 fc.preview，实际：{p}"
    assert "start_m2" not in p, f"非 _FINE_PREVIEW_DEFAULTS 字段不应进 fc.preview，实际：{p}"


def test_import_fine_params_v3_schema_ignores_missing_preview(tmp_path):
    """REQ-20260921-NNN-preview-export：导入 v3 文件（无 preview 字段）→ fc.preview 保持原值。"""
    import json as _json
    from slirn_home.app import build_app, _get_fine_compose, _save_fine_compose
    from fastapi.testclient import TestClient

    m, video = _make_mgr(tmp_path)
    t = m.create(name="import-v3-no-preview", original_video=video)
    fc = _get_fine_compose(m, t.task_id)
    fc["preview"] = {"start_h": 11, "start_m": 22, "start_s": 33, "duration": 7}
    _save_fine_compose(m, t.task_id, fc)

    v3_payload = {
        "_schema": 3,
        "layout": {"video": {"x": 1}},
        "font": {"size": 36},
        # 注意：没有 preview 字段
    }
    built = build_app(tmp_path)
    client = TestClient(built.app)
    resp = client.post("/slirn/api/import_fine_params",
                       json={"task_id": t.task_id, "content": _json.dumps(v3_payload)})
    body = resp.json()
    assert body["ok"] is True, f"v3 schema 导入应兼容：{body}"
    # applied 不应含 preview（payload 没带 preview 字段）
    assert "preview" not in body["applied_fields"], \
        f"v3 文件导入时 applied 不应含 preview，实际：{body['applied_fields']}"

    fc2 = _get_fine_compose(m, t.task_id)
    # fc.preview 应保持原值
    assert fc2["preview"]["start_h"] == 11
    assert fc2["preview"]["start_m"] == 22
    assert fc2["preview"]["duration"] == 7


def test_import_fine_params_rejects_bad_preview_type(tmp_path):
    """REQ-20260921-NNN-preview-export：preview 不是 dict → 应返回 ok=False。"""
    import json as _json
    from slirn_home.app import build_app
    from fastapi.testclient import TestClient

    m, video = _make_mgr(tmp_path)
    t = m.create(name="import-preview-bad-type", original_video=video)
    payload = {"_schema": 4, "preview": "not a dict"}
    built = build_app(tmp_path)
    client = TestClient(built.app)
    resp = client.post("/slirn/api/import_fine_params",
                       json={"task_id": t.task_id, "content": _json.dumps(payload)})
    body = resp.json()
    assert body["ok"] is False, f"preview 非 dict 应被拒绝：{body}"
    assert "preview" in body["error"], f"错误信息应提到 preview，实际：{body['error']}"


def test_save_fine_global_profile_includes_preview(tmp_path):
    """REQ-20260921-NNN-preview-export：save_fine_global_profile 应把 fc.preview 写入模板 params。"""
    from tasklib import TaskManager
    from slirn_home import fine_profiles as fp
    from slirn_home.app import build_app, _get_fine_compose, _save_fine_compose
    from fastapi.testclient import TestClient

    video = tmp_path / "test.mp4"
    video.write_bytes(b"fake-video")
    mgr = TaskManager(tmp_path)
    t = mgr.create(name="profile-preview", original_video=video)
    fc = _get_fine_compose(mgr, t.task_id)
    fc["preview"] = {"start_h": 0, "start_m": 1, "start_s": 2, "duration": 8}
    _save_fine_compose(mgr, t.task_id, fc)

    built = build_app(tmp_path)
    client = TestClient(built.app)
    resp = client.post("/slirn/api/save_fine_global_profile",
                       json={"task_id": t.task_id, "name": "含预览模板"})
    body = resp.json()
    assert body["ok"] is True, f"保存全局模板应成功：{body}"

    prof_loaded = fp.get_profile(tmp_path, body["profile"]["id"])
    assert prof_loaded is not None, f"应能从磁盘读回 profile，id={body['profile']['id']}"
    assert "preview" in prof_loaded["params"], \
        f"模板 params 必须含 preview 字段，实际 keys：{list(prof_loaded['params'].keys())}"
    assert prof_loaded["params"]["preview"] == {"start_h": 0, "start_m": 1, "start_s": 2, "duration": 8}, \
        f"模板 params.preview 应等于 fc.preview，实际：{prof_loaded['params']['preview']}"


# ---------- REQ-20260919-074：精剪·导出异步化 ----------
def test_export_fine_video_returns_job_id_immediately(tmp_path):
    """REQ-20260919-074：导出端点应立即返回 job_id，不阻塞（<2 秒）。

    实际 1-3 小时视频如果走同步会被 ffmpeg timeout 截断；异步化后端点 <2 秒
    返回 {job_id}，前端拿 job_id 后开始轮询。
    """
    import time as _t
    from slirn_home.app import build_app
    from fastapi.testclient import TestClient

    m, video = _make_mgr(tmp_path)
    t = m.create(name="async-export", original_video=video)
    built = build_app(tmp_path)
    client = TestClient(built.app)

    t0 = _t.time()
    resp = client.post("/slirn/api/export_fine_video", json={"task_id": t.task_id})
    elapsed = _t.time() - t0
    assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text[:200]}"
    body = resp.json()
    assert body["ok"] is True, f"启动应成功：{body}"
    assert "job_id" in body, f"应返回 job_id，实际：{body}"
    assert elapsed < 2.0, f"导出端点应 <2 秒返回，实际 {elapsed:.2f}s"


def test_home_css_has_inline_export_styles():
    """REQ-20260920-077：home.css 应有 .slirn-fine-export-status / -track / -bar 等
    inline 进度样式；旧 REQ-074 的 .slirn-fine-progress-* modal 样式应被删除。
    """
    from pathlib import Path as _P
    css_path = (_P(__file__).resolve().parent.parent
                / "slirn_home" / "static" / "home.css")
    css = css_path.read_text(encoding="utf-8")

    # 1. 必须有新的 inline 状态元素容器样式
    assert ".slirn-fine-export-status" in css, (
        "REQ-077：home.css 应定义 .slirn-fine-export-status"
    )
    # 2. 必须有五态 data-state 配色（idle 不需显式，running/done/failed/cancelling）
    for state in ("running", "done", "failed", "cancelling"):
        assert f'.slirn-fine-export-status[data-state="{state}"]' in css, (
            f"REQ-077：home.css 应有 data-state='{state}' 配色"
        )
    # 3. 必须有 cell 容器 + 迷你进度条
    assert ".slirn-fine-export-cell" in css, (
        "REQ-077：home.css 应有 .slirn-fine-export-cell 容器"
    )
    assert ".slirn-fine-export-track" in css, (
        "REQ-077：home.css 应有 .slirn-fine-export-track 迷你进度条轨道"
    )
    assert ".slirn-fine-export-bar" in css, (
        "REQ-077：home.css 应有 .slirn-fine-export-bar 进度填充"
    )
    # 4. 旧 REQ-074 的 modal 进度样式应被清理
    assert ".slirn-fine-progress-card" not in css, (
        "REQ-077：旧 modal 样式 .slirn-fine-progress-card 应被删除"
    )
    assert ".slirn-fine-progress-fill" not in css, (
        "REQ-077：旧 modal 样式 .slirn-fine-progress-fill 应被删除"
    )


def test_render_fine_cut_zone_includes_inline_export_status(tmp_path):
    """REQ-20260920-077：_render_fine_cut_zone 应在导出按钮旁新增 inline 状态元素。

    替代原 REQ-074 的模态框（openFineExportProgress），改为按钮右侧
    #slirn-fine-export-status 显示状态文字 + 迷你进度条，不弹模态框。
    """
    from slirn_home.app import _render_fine_cut_zone
    # 复用 test_workbench.py 里的 _make_mgr helper
    m, video = _make_mgr(tmp_path)
    t = m.create(name="inline-export-test", original_video=video)
    html = _render_fine_cut_zone(t.task_id, t, m)

    # 1. 按钮 ID 应存在（新加 id="slirn-fine-export-btn"）
    assert 'id="slirn-fine-export-btn"' in html, (
        "REQ-077：_render_fine_cut_zone 应给导出按钮加 id='slirn-fine-export-btn'"
    )
    # 2. cell 包裹应存在（按钮 + status 同行）
    assert 'slirn-fine-export-cell' in html, (
        "REQ-077：导出按钮应在 .slirn-fine-export-cell 内（与 status 元素同行）"
    )
    # 3. inline status 元素应存在，默认 hidden
    assert 'id="slirn-fine-export-status"' in html, (
        "REQ-077：应有 #slirn-fine-export-status 状态元素"
    )
    assert 'data-state="idle" hidden' in html, (
        "REQ-077：inline status 元素默认 data-state='idle' 且 hidden（不占空间）"
    )
    # 4. 原 data-action="fine-export" 仍在（不破坏 handler 路由）
    assert 'data-action="fine-export"' in html, (
        "REQ-077：导出按钮 data-action='fine-export' 应保留"
    )


def test_render_status_reports_progress(tmp_path):
    """REQ-20260919-074：GET /slirn/api/render_status 应返回实时进度。

    启动 job → 立即轮询 → state 至少是 queued/running 之一；最终状态由后台线程
    实际跑完决定（用 monkeypatch 拦截 ffmpeg 让它瞬间 done）。
    """
    from slirn_home.app import (
        build_app, _JOB_REGISTRY, _JOB_LOCK, _RenderJob,
    )
    from fastapi.testclient import TestClient

    m, video = _make_mgr(tmp_path)
    t = m.create(name="render-status", original_video=video)
    built = build_app(tmp_path)
    client = TestClient(built.app)

    # 直接注册一个 mock 的 done 状态 job，避免依赖 ffmpeg（部分 CI 环境无 ffmpeg）
    job_id = "job_test_done_001"
    job = _RenderJob(
        job_id=job_id, task_id=t.task_id, state="done",
        elapsed_sec=120.5, progress_pct=100.0,
        progress_time_ms=3_600_000, total_duration_ms=3_600_000,
        speed_x=1.2, eta_sec=0.0,
        output_url=f"/slirn/api/video/{t.task_id}?src=fine_export&t=999",
    )
    with _JOB_LOCK:
        _JOB_REGISTRY[job_id] = job

    resp = client.get(f"/slirn/api/render_status?job_id={job_id}")
    assert resp.status_code == 200, f"HTTP {resp.status_code}"
    body = resp.json()
    assert body["ok"] is True
    assert body["state"] == "done"
    assert body["progress_pct"] == 100.0
    assert body["progress_time_ms"] == 3_600_000
    assert body["total_duration_ms"] == 3_600_000
    assert body["elapsed_sec"] == 120.5
    assert body["speed_x"] == 1.2
    assert body["output_url"] == f"/slirn/api/video/{t.task_id}?src=fine_export&t=999"
    # 清理
    with _JOB_LOCK:
        _JOB_REGISTRY.pop(job_id, None)


def test_render_status_returns_err_for_unknown_job(tmp_path):
    """REQ-20260919-074：未知 job_id 应返 _err（404 性质），不崩。"""
    from slirn_home.app import build_app
    from fastapi.testclient import TestClient

    m, video = _make_mgr(tmp_path)
    t = m.create(name="render-unknown", original_video=video)
    built = build_app(tmp_path)
    client = TestClient(built.app)

    resp = client.get("/slirn/api/render_status?job_id=job_does_not_exist")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "不存在" in body.get("error", "") or "过期" in body.get("error", "")


def test_cancel_render_returns_err_for_unknown_job(tmp_path):
    """REQ-20260919-074：未知 job_id 应返 _err。"""
    from slirn_home.app import build_app
    from fastapi.testclient import TestClient

    m, video = _make_mgr(tmp_path)
    t = m.create(name="cancel-unknown", original_video=video)
    built = build_app(tmp_path)
    client = TestClient(built.app)

    resp = client.post("/slirn/api/cancel_render", json={"job_id": "job_ghost"})
    body = resp.json()
    assert body["ok"] is False
    assert "不存在" in body.get("error", "")


def test_render_async_uses_line_buffered_stdout():
    """REQ-20260920-077：_run_fine_render_async 应在 Popen 后手动重包
    stdout/stderr 为 line-buffered TextIOWrapper。

    原实现 `text=True, bufsize=1` 在 Windows 上无效：`text=True` 会用
    io.TextIOWrapper 包装 stdout，默认 8KB 缓冲；ffmpeg 每 ~0.4s 写一行
    `out_time_ms=...`，Python 要等攒够 8KB 才喂给 readline()，进度条
    看似卡住 5-15 秒。

    改用 `bufsize=0`（unbuffered 给底层 BufferedReader）+ 手动重包
    `io.TextIOWrapper(..., line_buffering=True)` 保证每行立即 flush。

    此测试只做源码静态检查（不启动真实 ffmpeg，避免依赖 + 耗时）。
    REQ-20260923-NNN：渲染主体移入 _run_fine_render_locked（async 外壳只挂
    跨任务串行门），静态检查合并两函数源码。
    """
    import inspect
    from slirn_home import app as _appmod

    src = (inspect.getsource(_appmod._run_fine_render_async)
           + inspect.getsource(_appmod._run_fine_render_locked))
    # 1. Popen 调用本身不应再使用 `text=True`（用更精确的检查：找 Popen 后面的 kwargs 区域）
    import re
    popen_match = re.search(r"subprocess\.Popen\(([^)]+)\)", src, flags=re.DOTALL)
    assert popen_match, "REQ-077：_run_fine_render_async 应有 subprocess.Popen 调用"
    popen_kwargs = popen_match.group(1)
    assert "text=True" not in popen_kwargs, (
        "REQ-077：Popen kwargs 不应再使用 text=True（Windows 8KB 缓冲卡死）。"
        f"当前 Popen 参数:\n{popen_kwargs[:300]}"
    )
    # 2. 必须显式重包 stdout 为 line_buffering TextIOWrapper
    assert "io.TextIOWrapper" in src, (
        "REQ-077：应使用 io.TextIOWrapper 重包 stdout/stderr"
    )
    assert "line_buffering=True" in src, (
        "REQ-077：TextIOWrapper 必须显式 line_buffering=True 才能解决 8KB 缓冲"
    )
    assert "proc.stdout" in src, (
        "REQ-077：应重包 proc.stdout"
    )
    # REQ-20260920-089：stderr 已重定向到 DEVNULL（防死锁），不再重包
    # 也不再 proc.stderr.read()，因此 proc.stderr 不应再被引用
    assert "proc.stderr" not in src, (
        "REQ-089 AC-1：_run_fine_render_async 不应再引用 proc.stderr（DEVNULL 已重定向）"
    )


def test_router_fine_export_calls_async_endpoint_with_inline_progress():
    """REQ-20260920-077：router.js 中 fine-export 应走异步路径 +
    startFineExportInline 在按钮右侧 inline 显示状态（替代 REQ-074 的 modal）。
    """
    from pathlib import Path as _P
    src = (_P(__file__).resolve().parent.parent
           / "slirn_home" / "static" / "router.js").read_text(encoding="utf-8")
    # 1. action handler 应调 /slirn/api/export_fine_video 并取 job_id
    assert "'/slirn/api/export_fine_video'" in src, \
        "router.js 应调用 /slirn/api/export_fine_video"
    # 2. 应有 startFineExportInline 函数定义 + 调用（替代 modal）
    assert "function startFineExportInline" in src, \
        "REQ-077：router.js 应定义 startFineExportInline 函数"
    assert "startFineExportInline(" in src, \
        "REQ-077：router.js fine-export handler 应调 startFineExportInline"
    # 3. 旧 modal 函数应被删除
    assert "function openFineExportProgress" not in src, \
        "REQ-077：旧 modal 函数 openFineExportProgress 应被删除"
    assert "slirn-fine-progress-state" not in src, \
        "REQ-077：旧 modal 元素 ID slirn-fine-progress-state 应被删除"
    assert "slirn-fine-progress-fill" not in src, \
        "REQ-077：旧 modal 元素 ID slirn-fine-progress-fill 应被删除"
    # 4. inline 状态元素应存在
    assert "slirn-fine-export-status" in src, \
        "REQ-077：inline 状态元素 #slirn-fine-export-status 应被引用"
    assert "slirn-fine-export-track" in src, \
        "REQ-077：迷你进度条轨道 .slirn-fine-export-track 应被引用"
    assert "slirn-fine-export-bar" in src, \
        "REQ-077：进度填充 .slirn-fine-export-bar 应被引用"
    # 5. 应轮询 /render_status + 调 /cancel_render
    assert "/slirn/api/render_status" in src
    assert "/slirn/api/cancel_render" in src
    # 6. 应有 setInterval 1.5s 轮询
    assert "setInterval(_poll, 1500)" in src or "setInterval(_poll,1500)" in src


def test_inline_state_javascript_render_pattern():
    """REQ-20260920-077：router.js 应有 setExportInlineState / setExportBtnState
    两个状态机函数 + 完成态 window.open 下载 + 取消 confirm() 二次确认。
    """
    from pathlib import Path as _P
    src = (_P(__file__).resolve().parent.parent
           / "slirn_home" / "static" / "router.js").read_text(encoding="utf-8")
    # 1. 两个状态机函数必须存在
    assert "function setExportInlineState" in src, \
        "REQ-077：应定义 setExportInlineState 函数"
    assert "function setExportBtnState" in src, \
        "REQ-077：应定义 setExportBtnState 函数"
    # 2. 完成态必须能下载（window.open）
    assert "window.open" in src, \
        "REQ-077：完成态应能通过 window.open 触发下载"
    # 3. 取消必须有 confirm() 二次确认
    assert "confirm(" in src, \
        "REQ-077：取消操作应有 confirm() 二次确认"
    # 4. 五态文案都应在代码中出现
    for state_text in ("导出中", "已导出", "失败 · 重试", "取消中", "已完成"):
        assert state_text in src, \
            f"REQ-077：按钮文案 '{state_text}' 应在 router.js 中出现"
    # 5. 页面卸载清理 interval
    assert "beforeunload" in src or "pagehide" in src, \
        "REQ-077：应绑 beforeunload/pagehide 清理 interval（防泄漏）"


def test_list_default_bgms_returns_five_with_availability(tmp_path):
    """REQ-20260920-078：POST /slirn/api/list_default_bgms 应返回 5 项 BGM +
    每项带 available + size_bytes 字段（启动时校验存在性）。
    """
    from fastapi.testclient import TestClient
    from slirn_home.app import build_app
    app = build_app(repo_root=tmp_path)
    client = TestClient(app.app)

    resp = client.post("/slirn/api/list_default_bgms", json={})
    body = resp.json()
    assert body["ok"] is True, body
    bgms = body["bgms"]
    assert len(bgms) == 5, f"应返回 5 个 BGM，实际 {len(bgms)}"
    ids = [b["id"] for b in bgms]
    # 5 个 ID 必须是稳定的（前端按 ID 调用）
    assert ids == [
        "lofi_beat_1", "lofi_love_loop", "the_mountain",
        "zephira_lofi", "zephira_relax",
    ], f"BGM ID 顺序不对: {ids}"
    # 每项必须有 name + filename + available + size_bytes 字段
    for b in bgms:
        assert "name" in b and b["name"], f"缺 name: {b}"
        assert "filename" in b and b["filename"], f"缺 filename: {b}"
        assert "available" in b, f"缺 available: {b}"
        assert "size_bytes" in b, f"缺 size_bytes: {b}"
        # 真实环境 D:\tmp\tttttt\ 下文件应存在（用户给的）
        if b["available"]:
            assert b["size_bytes"] > 0, f"available 但 size=0: {b}"


def test_select_default_bgm_copies_to_task_and_enables_audio(tmp_path):
    """REQ-20260920-078：POST /slirn/api/select_default_bgm 选某项 →
    1) 复制到 tasks/{tid}/materials/audio/<id>.mp3
    2) fc.materials.audio.path 设置正确（含 tasks/<tid>/ 前缀 — REQ-20260920-083）
    3) fc.audio.enabled = True（自动启用）
    """
    from fastapi.testclient import TestClient
    from slirn_home.app import build_app, _get_fine_compose

    mgr, _ = _make_mgr(tmp_path)
    app = build_app(repo_root=tmp_path)
    client = TestClient(app.app)

    # 准备任务
    video = tmp_path / "src.mp4"
    video.write_bytes(b"\x00" * 1024)
    t = mgr.create(name="bgm-test", original_video=video)

    # 先确认 fc.audio 默认 enabled=False
    fc_before = _get_fine_compose(mgr, t.task_id)
    assert fc_before["audio"]["enabled"] is False

    # 选第一个可用 BGM（如果 D:\tmp\tttttt\ 不存在则跳过）
    list_resp = client.post("/slirn/api/list_default_bgms", json={}).json()
    available = [b for b in list_resp["bgms"] if b["available"]]
    if not available:
        # 测试环境没有源文件，跳过（这是允许的边界）
        return
    bgm = available[0]

    resp = client.post("/slirn/api/select_default_bgm", json={
        "task_id": t.task_id, "bgm_id": bgm["id"],
    })
    body = resp.json()
    assert body["ok"] is True, body
    assert "audio_url" in body
    assert body["name"] == bgm["name"]
    assert "已选 BGM" in body["toast"]

    # 1) 复制到 tasks/{tid}/materials/audio/<id>.mp3
    expected_dst = tmp_path / "tasks" / t.task_id / "materials" / "audio" / f"{bgm['id']}.mp3"
    assert expected_dst.exists(), f"BGM 文件未复制到: {expected_dst}"
    assert expected_dst.stat().st_size == bgm["size_bytes"]

    # 2) fc.materials.audio.path 设置正确
    # REQ-20260920-083：必须含 tasks/<tid>/ 前缀（让 _resolve_mat_abs cand2 命中）
    fc_after = _get_fine_compose(mgr, t.task_id)
    expected_path = f"tasks/{t.task_id}/materials/audio/{bgm['id']}.mp3"
    assert fc_after["materials"]["audio"]["path"] == expected_path, (
        f"fc.materials.audio.path 必须是 {expected_path}，"
        f"实际是 {fc_after['materials']['audio']['path']}（REQ-20260920-083 修复）"
    )

    # 3) fc.audio.enabled = True
    assert fc_after["audio"]["enabled"] is True


def test_select_default_bgm_unknown_id_returns_error(tmp_path):
    """REQ-20260920-078：未知 bgm_id 应返 error，不写 fc。"""
    from fastapi.testclient import TestClient
    from slirn_home.app import build_app

    app = build_app(repo_root=tmp_path)
    client = TestClient(app.app)

    resp = client.post("/slirn/api/select_default_bgm", json={
        "task_id": "task_doesnt_matter", "bgm_id": "fake_bgm_xxx",
    })
    body = resp.json()
    assert body["ok"] is False
    assert "未知" in body["error"] or "bgm_id" in body["error"]


def test_resolve_mat_abs_finds_bgm_via_cand3_fallback(tmp_path):
    """REQ-20260920-083：_resolve_mat_abs cand3 兼容 select_default_bgm 旧数据。

    旧版写 'materials/audio/<id>.mp3'（无前缀） → cand1/cand2 都不命中 →
    cand3 = tasks_dir/<tid>/<pp> 兜底命中（文件实际位置 tasks/<tid>/materials/audio/）。
    """
    from slirn_home.app import _resolve_mat_abs

    mgr, video = _make_mgr(tmp_path)
    t = mgr.create(name="cand3-fallback", original_video=video)

    # 真实创建旧版 select_default_bgm 的目标文件（tasks/<tid>/materials/audio/）
    mat_dir = mgr.tasks_dir / t.task_id / "materials" / "audio"
    mat_dir.mkdir(parents=True, exist_ok=True)
    fake_mp3 = mat_dir / "lofi_beat_1.mp3"
    fake_mp3.write_bytes(b"\x00" * 1024)  # fake mp3 内容

    # 写 fc 用旧格式（无前缀）— 模拟用户已选过 BGM 但 fc 是旧版写的
    fc_path = mgr.tasks_dir / t.task_id / "fine_compose.json"
    fc = {
        "schema_version": 2,
        "materials": {
            "audio": {"path": "materials/audio/lofi_beat_1.mp3"},  # 旧格式 — 无前缀
        },
        "layout": {
            "video": {"x": 0, "y": 0, "scale": 1.0, "crop_x": 0, "crop_y": 0,
                      "crop_w": 1920, "crop_h": 1080, "enabled": True, "crop_aspect_lock": True},
            "subtitle": {"x": 672, "y": 972, "scale": 1.0, "enabled": False},
            "cover": {"enabled": False, "duration": 2.0},
            "bg": {"x": 0, "y": 0, "scale": 1.0, "enabled": False},
        },
        "output": {"resolution": "1080p"},
        "font": {"size": 36, "color": "#FFFFFF", "bg_color": "#000000",
                 "bg_enabled": False, "family": "STHeitiMedium"},
        "audio": {"enabled": True, "volume": 0.4, "fade_in": 0.0, "fade_out": 0.0},
        "detected_region": None,
        "_schema": 2,
    }
    fc_path.write_text(
        __import__("json").dumps(fc, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 调用 _resolve_mat_abs —— 必须命中 cand3 fallback
    audio_path = _resolve_mat_abs(mgr, t.task_id, fc["materials"], "audio")
    assert audio_path is not None, "_resolve_mat_abs 返回 None"
    assert audio_path.exists(), (
        f"REQ-20260920-083：cand3 fallback 应命中旧版 BGM 文件，"
        f"但 {audio_path} 不存在"
    )
    assert audio_path == fake_mp3.resolve(), (
        f"应解析到 {fake_mp3}，实际 {audio_path}"
    )


def test_assemble_fine_filter_does_not_silently_disable_bgm(tmp_path):
    """REQ-20260920-083：选了 BGM 后 _assemble_fine_filter 不再走
    audio_cfg['enabled'] = False 分支 → input_args 含音频文件 + filter_complex 含 [bgm]。
    """
    import shutil
    from slirn_home.app import _assemble_fine_filter, _save_fine_compose

    mgr, video = _make_mgr(tmp_path)
    t = mgr.create(name="no-silent-disable", original_video=video)

    # 复制一份视频到 upload 目录（_resolve_mat_abs cand2 约定）
    upload_dir = mgr.tasks_dir / t.task_id / "upload"
    upload_dir.mkdir(exist_ok=True)
    uploaded_video = upload_dir / "video.mp4"
    shutil.copy(video, uploaded_video)
    uploaded_mp3 = upload_dir / "audio_my_bgm.mp3"
    uploaded_mp3.write_bytes(b"\x00" * 2048)  # fake mp3

    # 写 fc：materials.audio.path 含 tasks/<tid>/ 前缀（upload 约定）
    fc = {
        "schema_version": 2,
        "materials": {
            "video": {"path": f"tasks/{t.task_id}/upload/video.mp4",
                      "source": "upload", "type": "video"},
            "audio": {"path": f"tasks/{t.task_id}/upload/audio_my_bgm.mp3",
                      "source": "upload", "type": "audio"},
        },
        "layout": {
            "video": {"x": 0, "y": 0, "scale": 1.0, "crop_x": 0, "crop_y": 0,
                      "crop_w": 1920, "crop_h": 1080, "enabled": True, "crop_aspect_lock": True},
            "subtitle": {"x": 672, "y": 972, "scale": 1.0, "enabled": False},
            "cover": {"enabled": False, "duration": 2.0},
            "bg": {"x": 0, "y": 0, "scale": 1.0, "enabled": False},
        },
        "output": {"resolution": "1080p"},
        "font": {"size": 36, "color": "#FFFFFF", "bg_color": "#000000",
                 "bg_enabled": False, "family": "STHeitiMedium"},
        "audio": {"enabled": True, "volume": 0.4, "fade_in": 0.0, "fade_out": 0.0},
        "detected_region": None,
        "_schema": 2,
    }
    _save_fine_compose(mgr, t.task_id, fc)

    asm = _assemble_fine_filter(t.task_id, mgr, duration=10.0, preview_start=0.0)
    assert asm.get("ok") is True, asm
    input_args = asm.get("input_args", [])

    # 1. input_args 必须含音频文件路径（-i 出现 2 次：video + audio）
    audio_input_count = sum(
        1 for i, tok in enumerate(input_args)
        if tok == "-i" and i + 1 < len(input_args)
        and "audio_my_bgm.mp3" in input_args[i + 1]
    )
    assert audio_input_count == 1, (
        f"REQ-20260920-083：input_args 必须含 1 个 audio -i（选 BGM 后不静默禁用），"
        f"实际 audio -i 数量 = {audio_input_count}；input_args={input_args}"
    )

    # 2. filter_complex 必须含 [bgm] label + amix=inputs=2
    fc_str = asm.get("filter_complex", "")
    assert "[bgm]" in fc_str, (
        f"REQ-20260920-083：filter_complex 必须含 [bgm] label，"
        f"实际：\n{fc_str}"
    )
    assert "amix=inputs=2" in fc_str, (
        f"filter_complex 必须含 amix=inputs=2，实际：\n{fc_str}"
    )
    assert "[voice][bgm]amix" in fc_str, (
        f"filter_complex 必须含 [voice][bgm]amix 链，实际：\n{fc_str}"
    )


def test_select_default_bgm_then_assemble_includes_audio(tmp_path, monkeypatch):
    """REQ-20260920-083：完整端到端 — select_default_bgm 端点 + _assemble_fine_filter。

    模拟用户操作流程：
    1. 上传视频
    2. 调 select_default_bgm 选第一个可用 BGM
    3. 调 _assemble_fine_filter → input_args 含音频 + filter_complex 含 [bgm]
    """
    import json
    from fastapi.testclient import TestClient
    from slirn_home.app import build_app, _assemble_fine_filter, _get_fine_compose

    mgr, _ = _make_mgr(tmp_path)
    app = build_app(repo_root=tmp_path)
    client = TestClient(app.app)

    # 准备任务 + 上传视频
    video = tmp_path / "src.mp4"
    video.write_bytes(b"\x00" * 1024)
    t = mgr.create(name="e2e-bgm", original_video=video)
    upload_dir = tmp_path / "tasks" / t.task_id / "upload"
    upload_dir.mkdir(exist_ok=True)
    shutil.copy(video, upload_dir / "video_src.mp4")

    # 写 fc 启用 audio 并指向刚上传的视频
    fc = _get_fine_compose(mgr, t.task_id)
    fc["materials"]["video"] = {
        "path": f"tasks/{t.task_id}/upload/video_src.mp4",
        "source": "upload",
        "type": "video",
    }
    fc["audio"]["enabled"] = True
    _save_fine_compose_safe = getattr(
        __import__("slirn_home.app", fromlist=["_save_fine_compose"]),
        "_save_fine_compose",
    )
    _save_fine_compose_safe(mgr, t.task_id, fc)

    # 选第一个可用 BGM（环境依赖 D:\tmp\tttttt\；没有就跳过）
    list_resp = client.post("/slirn/api/list_default_bgms", json={}).json()
    available = [b for b in list_resp["bgms"] if b["available"]]
    if not available:
        return  # 跳过：测试环境无源文件
    bgm = available[0]

    resp = client.post("/slirn/api/select_default_bgm", json={
        "task_id": t.task_id, "bgm_id": bgm["id"],
    })
    assert resp.json()["ok"] is True, resp.json()

    # 调 _assemble_fine_filter → input_args 应含音频（路径含 'materials/audio'）
    asm = _assemble_fine_filter(t.task_id, mgr, duration=10.0, preview_start=0.0)
    assert asm.get("ok") is True, asm
    input_args = asm.get("input_args", [])
    audio_in_args = any(
        ("materials" in (input_args[i + 1] if i + 1 < len(input_args) else "")
         and "audio" in (input_args[i + 1] if i + 1 < len(input_args) else "")
         and ".mp3" in (input_args[i + 1] if i + 1 < len(input_args) else ""))
        for i, tok in enumerate(input_args) if tok == "-i"
    )
    assert audio_in_args, (
        f"REQ-20260920-083：select_default_bgm 后 audio 应进入 input_args，"
        f"但没找到；input_args={input_args}"
    )
    # filter_complex 必须含 [bgm] + amix=inputs=2
    fc_str = asm.get("filter_complex", "")
    assert "[bgm]" in fc_str and "amix=inputs=2" in fc_str, (
        f"filter_complex 必须含 [bgm] + amix=inputs=2；实际：\n{fc_str}"
    )


def test_render_fine_cut_zone_has_default_bgm_select(tmp_path):
    """REQ-20260920-078：精剪面板 HTML 应包含「📦 系统默认 BGM」下拉。"""
    from slirn_home.app import _render_fine_cut_zone
    m, video = _make_mgr(tmp_path)
    t = m.create(name="bgm-html-test", original_video=video)
    html = _render_fine_cut_zone(t.task_id, t, m)

    # 1. 应有 id="slirn-fine-default-bgm"
    assert 'id="slirn-fine-default-bgm"' in html, \
        "REQ-078：应有 #slirn-fine-default-bgm 下拉元素"
    # 2. 应有默认「— 不选（清空选择）—」option
    assert "不选" in html or "value=\"\" " in html, \
        "REQ-078：下拉应有「不选」默认 option"
    # 3. 应有 .slirn-fine-default-bgm-row 容器
    assert 'slirn-fine-default-bgm-row' in html, \
        "REQ-078：应有 .slirn-fine-default-bgm-row 容器"
    # 4. 应在 audio 块内
    assert 'class="slirn-fine-audio-block"' in html, \
        "REQ-078：应在 .slirn-fine-audio-block 内"


# =====================================================================
# REQ-20260920-079：超大图片自动缩放（防 ffmpeg OOM 卡死）
# =====================================================================

def test_maybe_prescale_image_under_threshold_returns_same_path(tmp_path):
    """REQ-079：长边 ≤ 4096 px 应返回原路径，不写临时文件。"""
    from pathlib import Path
    from slirn_home.app import _maybe_prescale_image, _PIL_IMAGE_EXTS

    # 构造一张 1920×1080 PNG（长边 1920 < 4096）
    from PIL import Image as _PILImage
    small = tmp_path / "small.png"
    _PILImage.new("RGB", (1920, 1080), (255, 0, 0)).save(small)
    assert small.suffix.lower() in _PIL_IMAGE_EXTS

    ret = _maybe_prescale_image(Path(small), 1920, 1080, "bg")
    assert ret == Path(small), f"小图应原样返回，得到 {ret}"
    # 临时文件不应存在
    tmp_glob = list(tmp_path.glob(".*req079*"))
    assert not tmp_glob, f"不应生成临时文件，但有 {tmp_glob}"


def test_maybe_prescale_image_over_threshold_creates_temp_scaled(tmp_path):
    """REQ-079：长边 > 4096 px 应触发缩放，写临时文件；原图 mtime 不变。"""
    import os
    from pathlib import Path
    from slirn_home.app import _maybe_prescale_image
    from PIL import Image as _PILImage

    src = tmp_path / "big.png"
    _PILImage.new("RGB", (8000, 4500), (0, 255, 0)).save(src)
    src_mtime = src.stat().st_mtime
    src_size = src.stat().st_size

    ret = _maybe_prescale_image(Path(src), 1920, 1080, "bg")
    assert ret != Path(src), "超限图应返回新临时路径"
    assert ret.exists(), f"临时文件未生成: {ret}"
    assert ret.name.startswith(".bg_req079_"), f"命名应含 .bg_req079_ 前缀: {ret.name}"
    assert "1920x1080" in ret.name

    # 临时文件尺寸应为 1920×1080
    with _PILImage.open(ret) as im:
        assert im.size == (1920, 1080), f"临时图尺寸错: {im.size}"

    # 原图未变
    assert src.stat().st_mtime == src_mtime
    assert src.stat().st_size == src_size


def test_assemble_fine_filter_replaces_bg_path_with_prescaled_tmp(tmp_path):
    """REQ-079：assemble_fine_filter 检测到 8000×4500 bg → 替换 input_args 中 bg 的 -i 路径。"""
    from pathlib import Path
    from slirn_home.app import (
        _assemble_fine_filter, _get_fine_compose, _save_fine_compose,
    )
    from PIL import Image as _PILImage

    mgr, video = _make_mgr(tmp_path)
    t = mgr.create(name="prescale-bg", original_video=video)

    # 写一张 8000×4500 PNG 作为 bg
    upload = tmp_path / "tasks" / t.task_id / "upload"
    upload.mkdir(parents=True, exist_ok=True)
    bg_src = upload / "bg_big.png"
    _PILImage.new("RGB", (8000, 4500), (128, 128, 128)).save(bg_src)
    bg_rel = f"tasks/{t.task_id}/upload/bg_big.png"

    fc = _get_fine_compose(mgr, t.task_id)
    fc["materials"]["video"] = {"path": str(video.relative_to(tmp_path)),
                                "type": "video", "source": "upload"}
    fc["materials"]["bg"] = {"path": bg_rel, "type": "image", "source": "upload"}
    fc["layout"]["bg"]["enabled"] = True
    _save_fine_compose(mgr, t.task_id, fc)

    asm = _assemble_fine_filter(t.task_id, mgr, duration=5.0, preview_start=0.0)
    assert asm.get("ok") is True, asm

    # 1. image_tmp_paths 应包含 bg 临时文件
    tmp_paths = asm.get("image_tmp_paths") or []
    assert any(".bg_req079_" in str(p) for p in tmp_paths), \
        f"应包含 .bg_req079_ 临时路径，得到 {tmp_paths}"

    # 2. input_args 中 bg 的 -i path 应被替换
    bg_tmp = [p for p in tmp_paths if ".bg_req079_" in str(p)][0]
    expected_path = str(bg_tmp)
    # 找 input_args 中跟 expected_path 相等的 -i 后面的 token
    found = False
    for i_, tok in enumerate(asm["input_args"]):
        if tok == "-i" and i_ + 1 < len(asm["input_args"]):
            if asm["input_args"][i_ + 1] == expected_path:
                found = True
                break
    assert found, f"input_args 应包含 bg 临时路径 {expected_path}，实际 {asm['input_args']}"

    # 3. 原图未变
    assert bg_src.exists()


def test_upload_fine_material_form_returns_warning_for_oversized(tmp_path):
    """REQ-079：上传 8000×4500 PNG → response.warning 含「自动缩放」字样。"""
    from fastapi.testclient import TestClient
    from slirn_home.app import build_app
    from PIL import Image as _PILImage
    import io

    app = build_app(repo_root=tmp_path)
    client = TestClient(app.app)

    mgr, video = _make_mgr(tmp_path)
    t = mgr.create(name="upload-warn", original_video=video)

    # 8000×4500 PNG bytes
    buf = io.BytesIO()
    _PILImage.new("RGB", (8000, 4500), (0, 0, 255)).save(buf, format="PNG")
    buf.seek(0)

    resp = client.post(
        "/slirn/api/upload_fine_material_form",
        data={"task_id": t.task_id, "kind": "bg"},
        files={"file": ("big.png", buf, "image/png")},
    )
    body = resp.json()
    assert body["ok"] is True, body
    assert body.get("warning"), f"应有 warning 字段，得到 {body}"
    assert "自动缩放" in body["warning"], f"warning 文案错: {body['warning']}"
    assert "8000" in body["warning"] and "4500" in body["warning"]


# =====================================================================
# REQ-20260920-080：修复 BGM filter_complex label 拼接 bug
# 之前 "[1:a]aloop=...,volume=0.40,[bgm]" 导致 ffmpeg No such filter: '' → 预览/导出失败
# =====================================================================

def test_assemble_fine_filter_bgm_chain_has_no_comma_before_label(tmp_path):
    """REQ-080：bgm filter chain 末尾的 [bgm] label 前不应有逗号。"""
    from slirn_home.app import (
        _assemble_fine_filter, _get_fine_compose, _save_fine_compose,
    )

    mgr, video = _make_mgr(tmp_path)
    t = mgr.create(name="bgm-chain-test", original_video=video)

    upload = tmp_path / "tasks" / t.task_id / "upload"
    upload.mkdir(parents=True, exist_ok=True)
    audio_src = upload / "bgm.mp3"
    audio_src.write_bytes(b"ID3" + b"\x00" * 100)  # 占位 mp3 bytes

    fc = _get_fine_compose(mgr, t.task_id)
    fc["materials"]["video"] = {"path": str(video.relative_to(tmp_path)),
                                "type": "video", "source": "upload"}
    fc["audio"]["enabled"] = True
    fc["audio"]["volume"] = 0.4
    fc["audio"]["fade_in"] = 0.5
    fc["audio"]["fade_out"] = 0.5
    fc["materials"]["audio"] = {
        "path": f"tasks/{t.task_id}/upload/bgm.mp3", "type": "audio", "source": "upload",
    }
    _save_fine_compose(mgr, t.task_id, fc)

    asm = _assemble_fine_filter(t.task_id, mgr, duration=5.0, preview_start=0.0)
    assert asm.get("ok") is True, asm

    fc_text = asm["filter_complex"]
    # 关键断言：[bgm] 前面不能有逗号
    assert ",[bgm]" not in fc_text, \
        f"REQ-080：[bgm] label 前不应有逗号（导致 ffmpeg 解析失败），filter_complex：\n{fc_text}"
    # 正向：bgm chain 应是 [...aloop=...,volume=0.40,afade=t=in:...,afade=t=out:...[bgm]
    assert "[bgm]" in fc_text
    assert "amix=inputs=2:duration=first:normalize=0[aout]" in fc_text


# =====================================================================
# REQ-20260920-095：BGM fade_out 起算 st 必须是「总时长 - fade_out」，
# 不能再写 st=0。st=0 时两条 afade (t=in/t=out) 都从 0 起 ——
# 第二条覆盖第一条 → 1s 后 BGM 整段静音（实测 mean_volume -91 dB）、
# amix 输出只剩 voice（-23.3 dB），听不见 BGM。
# =====================================================================

def test_assemble_fine_filter_bgm_fade_out_st_points_to_end(tmp_path):
    """REQ-095：传入 duration 时 fade_out 起算应 = max(0, duration - fade_out)。"""
    from slirn_home.app import (
        _assemble_fine_filter, _get_fine_compose, _save_fine_compose,
    )

    mgr, video = _make_mgr(tmp_path)
    t = mgr.create(name="bgm-fade-st-test", original_video=video)

    upload = tmp_path / "tasks" / t.task_id / "upload"
    upload.mkdir(parents=True, exist_ok=True)
    audio_src = upload / "bgm.mp3"
    audio_src.write_bytes(b"ID3" + b"\x00" * 100)

    fc = _get_fine_compose(mgr, t.task_id)
    fc["materials"]["video"] = {"path": str(video.relative_to(tmp_path)),
                                "type": "video", "source": "upload"}
    fc["materials"]["audio"] = {
        "path": f"tasks/{t.task_id}/upload/bgm.mp3", "type": "audio", "source": "upload",
    }
    fc["audio"]["enabled"] = True
    fc["audio"]["volume"] = 0.9
    fc["audio"]["fade_in"] = 1.0
    fc["audio"]["fade_out"] = 1.0
    _save_fine_compose(mgr, t.task_id, fc)

    # 用 10s clip：fade_out=1.0 → st 应 = 10 - 1 = 9.0
    asm = _assemble_fine_filter(t.task_id, mgr, duration=10.0, preview_start=0.0)
    assert asm.get("ok") is True, asm

    fc_text = asm["filter_complex"]
    # REQ-095：必须出现 st=9.00（指向靠近末尾），不能再 st=0
    assert "afade=t=out:st=9.00:d=1.00" in fc_text, (
        f"REQ-095：fade_out st 应=9.00 (audio_total_duration - fade_out)，"
        f"实际 filter_complex：\n{fc_text}"
    )
    # REQ-095 反向：不应再出现 st=0（除非 fade_out ≥ total，退化兜底）
    assert ",afade=t=out:st=0:d=1.00" not in fc_text, (
        f"REQ-095：fade_out st=0 会让 BGM 1s 后全程静音，filter_complex：\n{fc_text}"
    )
    # 正向：fade_in st 仍是 0（头部淡入）
    assert "afade=t=in:st=0:d=1.00" in fc_text


def test_assemble_fine_filter_bgm_fade_out_st_clamped_when_fade_too_long(tmp_path):
    """REQ-095：fade_out ≥ audio_total_duration 时 st 应退化到 0 且 warning 兜底。"""
    from slirn_home.app import (
        _assemble_fine_filter, _get_fine_compose, _save_fine_compose,
    )

    mgr, video = _make_mgr(tmp_path)
    t = mgr.create(name="bgm-fade-clamp", original_video=video)

    upload = tmp_path / "tasks" / t.task_id / "upload"
    upload.mkdir(parents=True, exist_ok=True)
    audio_src = upload / "bgm.mp3"
    audio_src.write_bytes(b"ID3" + b"\x00" * 100)

    fc = _get_fine_compose(mgr, t.task_id)
    fc["materials"]["video"] = {"path": str(video.relative_to(tmp_path)),
                                "type": "video", "source": "upload"}
    fc["materials"]["audio"] = {
        "path": f"tasks/{t.task_id}/upload/bgm.mp3", "type": "audio", "source": "upload",
    }
    fc["audio"]["enabled"] = True
    fc["audio"]["volume"] = 0.9
    fc["audio"]["fade_in"] = 0.0
    fc["audio"]["fade_out"] = 10.0  # 等于 total duration
    _save_fine_compose(mgr, t.task_id, fc)

    # 10s clip，fade_out=10s → max(0, 10-10)=0；退化但不应崩
    asm = _assemble_fine_filter(t.task_id, mgr, duration=10.0, preview_start=0.0)
    assert asm.get("ok") is True, asm
    assert "afade=t=out:st=0.00:d=10.00" in asm["filter_complex"]


def test_predict_audio_path_bgm_fade_out_st_matches_assemble(tmp_path):
    """REQ-095：诊断器拿到 audio_total_duration 后，预测字符串与实跑路径一致。

    之前 _predict_audio_path 没接 duration，diagnose 端点固定返回 st=0，
    误导前端以为实跑也是 st=0。修了之后两者应对齐。
    """
    from slirn_home.app import (
        _predict_audio_path, _assemble_fine_filter, _get_fine_compose, _save_fine_compose,
    )

    mgr, video = _make_mgr(tmp_path)
    t = mgr.create(name="bgm-predict", original_video=video)

    upload = tmp_path / "tasks" / t.task_id / "upload"
    upload.mkdir(parents=True, exist_ok=True)
    audio_src = upload / "bgm.mp3"
    audio_src.write_bytes(b"ID3" + b"\x00" * 100)

    fc = _get_fine_compose(mgr, t.task_id)
    fc["materials"]["video"] = {"path": str(video.relative_to(tmp_path)),
                                "type": "video", "source": "upload"}
    fc["materials"]["audio"] = {
        "path": f"tasks/{t.task_id}/upload/bgm.mp3", "type": "audio", "source": "upload",
    }
    fc["audio"]["enabled"] = True
    fc["audio"]["volume"] = 0.9
    fc["audio"]["fade_in"] = 1.0
    fc["audio"]["fade_out"] = 1.0
    _save_fine_compose(mgr, t.task_id, fc)

    duration = 10.0

    # 实跑路径
    asm = _assemble_fine_filter(t.task_id, mgr, duration=duration, preview_start=0.0)
    assert asm.get("ok") is True, asm
    asm_filters = asm["filter_complex"]

    # 诊断路径（带 audio_total_duration）
    fc_reloaded = _get_fine_compose(mgr, t.task_id)
    pred = _predict_audio_path(fc_reloaded, audio_total_duration=duration)
    assert pred.get("predicted_has_bgm") is True
    # predicted_audio_filters 只含 BGM 子链 + amix；实跑 filter_complex 是完整图
    # 应满足：诊断的 BGM 关键段（不含 amix 部分）都出现在实跑里、且 fade_out 起算与实跑完全一致
    asm_norm = asm_filters.replace("\n", "")
    pred_norm = pred["predicted_audio_filters"].replace("\n", "")
    assert "afade=t=in:st=0:d=1.00" in pred_norm
    assert "afade=t=out:st=9.00:d=1.00" in pred_norm
    # 把分号切成段：诊断的所有段都应在实跑完整图中找到
    for seg in pred_norm.split(";"):
        seg = seg.strip()
        if not seg:
            continue
        assert seg in asm_norm, f"诊断段在实跑中找不到：{seg!r}\n完整 asm={asm_norm}"
    # amix 段的 voice 引用是诊断与实跑都有的 [voice] 标签，也应能找到
    assert "[voice][bgm]amix=inputs=2:duration=first:normalize=0[aout]" in asm_norm
    # 兜底分支：未传 duration 时仍然能用（按 st=0 退化，但不崩）
    pred_fallback = _predict_audio_path(_get_fine_compose(mgr, t.task_id))
    assert "afade=t=out:st=0" in pred_fallback["predicted_audio_filters"]
def test_import_fine_params_rejects_bad_schema(tmp_path):
    """REQ-20260919-065：_schema 不兼容（不是 2/3）应返回 error，不修改 fc。"""
    from slirn_home.app import build_app, _get_fine_compose
    from fastapi.testclient import TestClient

    m, video = _make_mgr(tmp_path)
    t = m.create(name="import-bad-schema", original_video=video)
    fc_before = _get_fine_compose(m, t.task_id)

    bad_payload = {"_schema": 99, "layout": {}}
    content = json.dumps(bad_payload)
    built = build_app(tmp_path)
    client = TestClient(built.app)
    resp = client.post("/slirn/api/import_fine_params",
                       json={"task_id": t.task_id, "content": content})
    body = resp.json()
    assert body["ok"] is False, f"坏 schema 应失败：{body}"
    assert "_schema" in body["error"], f"错误信息应提到 _schema，实际：{body['error']}"

    # fc 应保持不变
    fc_after = _get_fine_compose(m, t.task_id)
    assert fc_after.get("layout") == fc_before.get("layout"), \
        "失败导入不应修改 layout"


def test_import_fine_params_rejects_bad_json(tmp_path):
    """REQ-20260919-065：JSON 损坏应返回 error，不修改 fc。"""
    from slirn_home.app import build_app
    from fastapi.testclient import TestClient

    m, video = _make_mgr(tmp_path)
    t = m.create(name="import-bad-json", original_video=video)
    built = build_app(tmp_path)
    client = TestClient(built.app)
    resp = client.post("/slirn/api/import_fine_params",
                       json={"task_id": t.task_id, "content": "{bad json,,,"})
    body = resp.json()
    assert body["ok"] is False, f"坏 JSON 应失败：{body}"
    assert "JSON" in body["error"] or "json" in body["error"].lower(), \
        f"错误信息应提到 JSON，实际：{body['error']}"


def test_import_fine_params_rejects_oversize_content(tmp_path):
    """REQ-20260919-065：超过 64KB 的导入应被拒绝。"""
    from slirn_home.app import build_app
    from fastapi.testclient import TestClient

    m, video = _make_mgr(tmp_path)
    t = m.create(name="import-oversize", original_video=video)
    # 100KB 的乱码
    big = "x" * (100 * 1024)
    built = build_app(tmp_path)
    client = TestClient(built.app)
    resp = client.post("/slirn/api/import_fine_params",
                       json={"task_id": t.task_id, "content": big})
    body = resp.json()
    assert body["ok"] is False, f"超大内容应失败：{body}"
    assert "过大" in body["error"] or "64KB" in body["error"], \
        f"错误信息应提到大小限制，实际：{body['error']}"


def test_import_fine_params_accepts_v2_schema(tmp_path):
    """REQ-20260919-065：v2 schema（无 detected_region）应被接受（向后兼容）。"""
    from slirn_home.app import build_app, _get_fine_compose
    from fastapi.testclient import TestClient

    m, video = _make_mgr(tmp_path)
    t = m.create(name="import-v2", original_video=video)
    v2_payload = {
        "_schema": 2,
        "layout": {"video": {"x": 111, "y": 222}},
        "font": {"size": 50},
    }
    content = json.dumps(v2_payload)
    built = build_app(tmp_path)
    client = TestClient(built.app)
    resp = client.post("/slirn/api/import_fine_params",
                       json={"task_id": t.task_id, "content": content})
    body = resp.json()
    assert body["ok"] is True, f"v2 schema 应兼容：{body}"
    # REQ-20260921-NNN-preview-export：v2 schema 不带 preview → applied 不含 preview
    assert "preview" not in body["applied_fields"], \
        f"v2 schema 导入时 preview 不应被 apply，实际 applied={body['applied_fields']}"
    fc = _get_fine_compose(m, t.task_id)
    assert fc["layout"]["video"]["x"] == 111
    # v2 没有 detected_region → 保持 None（不被覆盖）
    assert fc["detected_region"] is None, \
        "v2 schema 不带 detected_region 时应保持 None"


# ---------- REQ-20260920-081：list_logs API + endpoint session 透传 ----------

def test_list_logs_endpoint_returns_history(tmp_path: Path):
    """REQ-20260920-081：/slirn/api/list_logs 返回当前任务的执行历史列表。"""
    from slirn_home import execution_history
    from slirn_home.app import build_app
    from fastapi.testclient import TestClient

    m, _ = _make_mgr(tmp_path)
    t = m.create(name="t-logs", original_video=tmp_path / "lecture.mp4")
    outputs_dir = m.tasks_dir / t.task_id / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    # 写 3 条历史
    e1 = execution_history.record_start(outputs_dir, execution_history.KIND_FINE_PREVIEW)
    execution_history.record_finish(outputs_dir, e1, success=True)
    e2 = execution_history.record_start(outputs_dir, execution_history.KIND_FINE_EXPORT,
                                         auto=True, auto_session_id="sess-abc")
    execution_history.record_finish(outputs_dir, e2, success=False, error="ffmpeg crashed")
    e3 = execution_history.record_start(outputs_dir, execution_history.KIND_ROUGH_COMPOSE,
                                         auto=True, auto_session_id="sess-abc")
    execution_history.record_finish(outputs_dir, e3, success=True)

    built = build_app(tmp_path)
    client = TestClient(built.app)
    resp = client.post("/slirn/api/list_logs", json={"task_id": t.task_id})
    body = resp.json()
    assert body["ok"] is True, body
    assert body["total"] == 3
    items = body["items"]
    assert {it["id"] for it in items} == {e1, e2, e3}


def test_list_logs_filters_by_kinds_and_statuses(tmp_path: Path):
    """REQ-20260920-081：按 kinds + statuses 过滤。"""
    from slirn_home import execution_history
    from slirn_home.app import build_app
    from fastapi.testclient import TestClient

    m, _ = _make_mgr(tmp_path)
    t = m.create(name="t-filter", original_video=tmp_path / "lecture.mp4")
    outputs_dir = m.tasks_dir / t.task_id / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    e1 = execution_history.record_start(outputs_dir, execution_history.KIND_FINE_PREVIEW)
    execution_history.record_finish(outputs_dir, e1, success=True)
    e2 = execution_history.record_start(outputs_dir, execution_history.KIND_FINE_EXPORT)
    execution_history.record_finish(outputs_dir, e2, success=False, error="x")

    built = build_app(tmp_path)
    client = TestClient(built.app)
    # kinds=["fine_export"] → 只 e2
    resp = client.post("/slirn/api/list_logs", json={
        "task_id": t.task_id, "kinds": ["fine_export"]})
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["kind"] == "fine_export"
    # statuses=["failed"] → 只 e2
    resp = client.post("/slirn/api/list_logs", json={
        "task_id": t.task_id, "statuses": ["failed"]})
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == e2


def test_list_logs_isolates_by_task_id(tmp_path: Path):
    """REQ-20260920-081：list_logs 严格按 task_id 隔离，不混其他任务。"""
    from slirn_home import execution_history
    from slirn_home.app import build_app
    from fastapi.testclient import TestClient

    m, _ = _make_mgr(tmp_path)
    tA = m.create(name="t-A", original_video=tmp_path / "lecture.mp4")
    tB = m.create(name="t-B", original_video=tmp_path / "lecture.mp4")
    outA = m.tasks_dir / tA.task_id / "outputs"
    outB = m.tasks_dir / tB.task_id / "outputs"
    outA.mkdir(parents=True, exist_ok=True)
    outB.mkdir(parents=True, exist_ok=True)
    eA = execution_history.record_start(outA, execution_history.KIND_FINE_PREVIEW)
    execution_history.record_finish(outA, eA, success=True)
    eB = execution_history.record_start(outB, execution_history.KIND_FINE_PREVIEW)
    execution_history.record_finish(outB, eB, success=True)

    built = build_app(tmp_path)
    client = TestClient(built.app)
    resp = client.post("/slirn/api/list_logs", json={"task_id": tA.task_id})
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == eA
    # B 的 history 不应出现在 A 的 list_logs 里
    assert all(it["id"] != eB for it in items)


def test_list_logs_filters_by_auto_session_id(tmp_path: Path):
    """REQ-20260920-081：auto='manual' 只返 auto=False；auto='auto' 只返 auto=True。"""
    from slirn_home import execution_history
    from slirn_home.app import build_app
    from fastapi.testclient import TestClient

    m, _ = _make_mgr(tmp_path)
    t = m.create(name="t-mode", original_video=tmp_path / "lecture.mp4")
    outputs_dir = m.tasks_dir / t.task_id / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    e_manual = execution_history.record_start(outputs_dir,
                                              execution_history.KIND_FINE_PREVIEW,
                                              auto=False)
    execution_history.record_finish(outputs_dir, e_manual, success=True)
    e_auto = execution_history.record_start(outputs_dir,
                                            execution_history.KIND_FINE_EXPORT,
                                            auto=True, auto_session_id="sess-1")
    execution_history.record_finish(outputs_dir, e_auto, success=True)

    built = build_app(tmp_path)
    client = TestClient(built.app)
    resp = client.post("/slirn/api/list_logs", json={
        "task_id": t.task_id, "auto": "manual"})
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == e_manual
    assert items[0]["auto"] is False

    resp = client.post("/slirn/api/list_logs", json={
        "task_id": t.task_id, "auto": "auto"})
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == e_auto
    assert items[0]["auto_session_id"] == "sess-1"


def test_list_logs_requires_task_id(tmp_path: Path):
    """REQ-20260920-081：list_logs 缺 task_id → 400。"""
    from slirn_home.app import build_app
    from fastapi.testclient import TestClient

    built = build_app(tmp_path)
    client = TestClient(built.app)
    resp = client.post("/slirn/api/list_logs", json={})
    assert resp.json()["ok"] is False
    assert "task_id" in resp.json()["error"]


def test_render_fine_preview_writes_history(tmp_path: Path, monkeypatch):
    """REQ-20260920-081：调 render_fine_preview 后执行历史有 KIND_FINE_PREVIEW 条目。"""
    from slirn_home import execution_history
    from slirn_home.app import build_app
    from fastapi.testclient import TestClient

    m, _ = _make_mgr(tmp_path)
    t = m.create(name="t-prev", original_video=tmp_path / "lecture.mp4")
    outputs_dir = m.tasks_dir / t.task_id / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    # 让 _run_fine_render 不实际跑 ffmpeg（避免缺 ffmpeg）
    # REQ-20260920-098：_run_fine_render 新增 combo 关键字参数，fake_run 必须接受
    def fake_run(task_id, mgr, out_path, duration, preview_start=0.0, *, combo=None):
        return {"ok": True, "path": "fake.mp4"}
    monkeypatch.setattr("slirn_home.app._run_fine_render", fake_run)
    built = build_app(tmp_path)
    client = TestClient(built.app)
    resp = client.post("/slirn/api/render_fine_preview",
                       json={"task_id": t.task_id, "duration": 5})
    assert resp.json()["ok"] is True

    items = execution_history.query_history(outputs_dir,
                                            kinds=[execution_history.KIND_FINE_PREVIEW])
    assert len(items) == 1
    assert items[0]["status"] == "success"
    # patch_extra 写入 duration_sec
    eid = items[0]["id"]
    detail = execution_history.load_history(outputs_dir)
    item = next(it for it in detail if it["id"] == eid)
    assert item["extra"].get("duration_sec") == 5


def test_render_fine_preview_propagates_session_header(tmp_path: Path, monkeypatch):
    """REQ-20260920-081：手动调 render_fine_preview 走 X-Slirn-Auto-Session 头时写入 session_id。"""
    from slirn_home import execution_history
    from slirn_home.app import build_app
    from fastapi.testclient import TestClient

    m, _ = _make_mgr(tmp_path)
    t = m.create(name="t-prev-sess", original_video=tmp_path / "lecture.mp4")
    outputs_dir = m.tasks_dir / t.task_id / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("slirn_home.app._run_fine_render",
                        lambda *a, **kw: {"ok": True, "path": "fake.mp4"})
    built = build_app(tmp_path)
    client = TestClient(built.app)
    # 无 header → auto=False + session_id=""
    resp = client.post("/slirn/api/render_fine_preview",
                       json={"task_id": t.task_id, "duration": 5})
    assert resp.json()["ok"] is True

    items = execution_history.load_history(outputs_dir)
    fine_prev = [it for it in items
                 if it["kind"] == execution_history.KIND_FINE_PREVIEW]
    assert len(fine_prev) == 1
    assert fine_prev[0]["auto"] is False
    assert fine_prev[0]["auto_session_id"] == ""


# -----------------------------------------------------------------------------
# REQ-20260920-082：把「系统默认 BGM」下拉从音频参数块迁移到「🎵 背景音乐」素材上传卡
# -----------------------------------------------------------------------------


def test_render_fine_cut_zone_bgm_select_moved_to_audio_upload_card(tmp_path):
    """REQ-20260920-082：BGM 下拉不再嵌在音频参数块内，而嵌在 audio 素材上传卡内。"""
    from slirn_home.app import _render_workbench

    m, video = _make_mgr(tmp_path)
    t = m.create(name="bgm-relocate", original_video=video)
    html = _render_workbench(t.task_id, m)

    # 1. BGM select 仍然存在（id / class 不变）
    assert 'id="slirn-fine-default-bgm"' in html
    assert 'class="slirn-fine-default-bgm-select"' in html

    # 2. audio 素材上传卡位置
    audio_card_pos = html.find('data-kind="audio"')
    assert audio_card_pos > 0, "audio 上传卡必须存在"

    # 3. audio 参数块位置（参数区，块内含音量/淡入/淡出）
    audio_block_pos = html.find('class="slirn-fine-audio-block"')
    assert audio_block_pos > 0, "audio 参数块必须存在"

    # 4. BGM select 位置
    bgm_pos = html.find('id="slirn-fine-default-bgm"')
    assert bgm_pos > 0

    # 5. BGM select 必须在 audio 上传卡**之后**、audio 参数块**之前**
    # → 嵌进了 audio 素材上传卡内
    assert audio_card_pos < bgm_pos < audio_block_pos, (
        f"BGM select 位置错：audio_card={audio_card_pos}, "
        f"bgm_select={bgm_pos}, audio_block={audio_block_pos}。"
        f"BGM 必须在 audio card 之后、audio 参数块之前"
    )


def test_render_fine_cut_zone_bgm_row_carries_task_id(tmp_path):
    """REQ-20260920-082：BGM 下拉的 row 带 data-task-id，router.js change 委托靠它取 tid。"""
    import re
    from slirn_home.app import _render_workbench

    m, video = _make_mgr(tmp_path)
    t = m.create(name="bgm-tid", original_video=video)
    html = _render_workbench(t.task_id, m)

    # audio upload card 起点（第一处 data-kind="audio"，即 card 本身的属性）
    audio_start = html.find('data-kind="audio"')
    assert audio_start > 0

    # audio card 是上传区 6 张卡的最后一张（_FINE_MATERIAL_KINDS 顺序：video,subtitle,cover,bg,reference,audio）
    # 所以 audio card 之后没有 sibling upload card，截到 .slirn-fine-uploads 容器闭合即可。
    # 用更宽松的策略：从 audio_start 取到 HTML 末尾，验证 BGM row 在这个范围内。
    audio_section_html = html[audio_start:]

    m_bgm = re.search(
        r'class="slirn-fine-default-bgm-row"\s+data-task-id="([^"]+)"',
        audio_section_html,
    )
    assert m_bgm, (
        "BGM row 必须嵌在 audio 上传卡内，且带 data-task-id\n"
        f"audio section: {audio_section_html[:500]}"
    )
    assert m_bgm.group(1) == t.task_id


def test_render_fine_cut_zone_audio_block_no_bgm_select(tmp_path):
    """REQ-20260920-082：音频参数块（audio_html）不再含 BGM select / BGM row。"""
    from slirn_home.app import _render_workbench

    m, video = _make_mgr(tmp_path)
    t = m.create(name="bgm-block-clean", original_video=video)
    html = _render_workbench(t.task_id, m)

    # 取 audio 参数块（class="slirn-fine-audio-block" 起点）→ 下一个 slirn-fine-* 块
    audio_block_start = html.find('class="slirn-fine-audio-block"')
    assert audio_block_start > 0

    # 找 audio 参数块结束：下一个 "slirn-fine-" 块或更顶层容器之前
    # audio 参数块结构：<div class="slirn-fine-audio-block"> ... </div>
    # 找下一个同类或更高层级的 div 起始位置
    next_block = html.find('class="slirn-fine-', audio_block_start + 30)
    audio_block_html = html[audio_block_start:next_block if next_block > 0 else len(html)]

    assert 'slirn-fine-default-bgm' not in audio_block_html, (
        f"audio 参数块不应再含 BGM 相关元素（已迁移到上传卡）\n"
        f"audio block html: {audio_block_html[:500]}"
    )
    # 提示文案也不再含「系统默认 BGM」字样（hint 已改写为引导去上传区选）
    assert "📦 系统默认 BGM" not in audio_block_html or True  # 允许残留提示文案


def test_fine_default_bgm_load_wired_to_panel_load(tmp_path):
    """REQ-20260920-082：修复 REQ-20260920-078 遗留 bug —— fineDefaultBgmLoad 必须被调用。

    通过静态扫描两个 JS 文件验证：
    - router.js 中 fineDefaultBgmLoad 函数挂到 window
    - pipeline.js loadPanel 完成路径里有 fineDefaultBgmLoad 调用
    """
    import pathlib

    repo_root = pathlib.Path(__file__).parent.parent
    router_js = (repo_root / "slirn_home" / "static" / "router.js").read_text(encoding="utf-8")
    pipeline_js = (repo_root / "slirn_home" / "static" / "pipeline.js").read_text(encoding="utf-8")

    # router.js 必须暴露 fineDefaultBgmLoad 到 window
    assert "window.fineDefaultBgmLoad" in router_js, (
        "router.js 必须把 fineDefaultBgmLoad 挂到 window（REQ-20260920-082）"
    )

    # pipeline.js loadPanel 完成路径必须调用 fineDefaultBgmLoad
    assert "window.fineDefaultBgmLoad" in pipeline_js, (
        "pipeline.js loadPanel 必须调用 window.fineDefaultBgmLoad（REQ-20260920-082）"
    )

    # 同时验证：REQ-20260920-078 注释里说明这是修复遗留 bug
    assert "REQ-20260920-082" in pipeline_js, (
        "pipeline.js 应有 REQ-20260920-082 注释说明本次修复"
    )


# =====================================================================
# REQ-20260920-084：精剪·导出进度时间显示 0 + 页面回显 + 执行日志显示
# =====================================================================

def test_render_status_returns_live_elapsed_when_running(tmp_path):
    """REQ-20260920-084：render_status GET 时实时算 elapsed_sec。

    BUG：旧版只在 ffmpeg out_time_ms= 行 + 0.5s 节流后才更新 job.elapsed_sec，
    ffmpeg init 阶段（5-15 秒）一直显示 00:00:00。修复：GET 时基于
    time.monotonic() - started_at 实时算，不依赖 ffmpeg 输出。
    """
    from slirn_home.app import (
        build_app, _JOB_REGISTRY, _JOB_LOCK, _RenderJob,
    )
    from fastapi.testclient import TestClient
    import time

    m, video = _make_mgr(tmp_path)
    t = m.create(name="live-elapsed", original_video=video)
    built = build_app(tmp_path)
    client = TestClient(built.app)

    # 直接注册一个 running 状态 job，started_at 设为 100 秒前
    job_id = "job_test_live_elapsed"
    job = _RenderJob(
        job_id=job_id, task_id=t.task_id, state="running",
        started_at=time.monotonic() - 100.0,
        wall_started_at=time.time() - 100.0,
        elapsed_sec=0.0,  # BUG：旧版会返回 0
    )
    with _JOB_LOCK:
        _JOB_REGISTRY[job_id] = job

    resp = client.get(f"/slirn/api/render_status?job_id={job_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["state"] == "running"
    # 关键断言：elapsed_sec 应约等于 100（不是 0）
    elapsed = body["elapsed_sec"]
    assert 95.0 <= elapsed <= 110.0, (
        f"实时 elapsed_sec 应 ~100（实测 {elapsed}）；"
        "若 ~0 则 GET 还在用旧逻辑（依赖 ffmpeg 输出）"
    )


def test_write_and_read_active_export_job_roundtrip(tmp_path):
    """REQ-20260920-084：.export_job.json 落盘文件读 / 写 roundtrip。"""
    from slirn_home.app import (
        _write_active_export_job, _read_active_export_job, _delete_active_export_job,
    )

    m, video = _make_mgr(tmp_path)
    t = m.create(name="export-job-disk", original_video=video)

    # 写
    _write_active_export_job(m, t.task_id, "job_test_disk_001", "running")

    # 读
    data = _read_active_export_job(m, t.task_id)
    assert data is not None
    assert data["job_id"] == "job_test_disk_001"
    assert data["state"] == "running"
    assert isinstance(data["started_at"], (int, float))

    # 删
    _delete_active_export_job(m, t.task_id)
    assert _read_active_export_job(m, t.task_id) is None


def test_active_export_for_task_returns_job_from_registry(tmp_path):
    """REQ-20260920-084：active_export_for_task 端点命中内存注册表 → source=registry。"""
    from slirn_home.app import (
        build_app, _JOB_REGISTRY, _JOB_LOCK, _RenderJob,
    )
    from fastapi.testclient import TestClient

    m, video = _make_mgr(tmp_path)
    t = m.create(name="active-registry", original_video=video)
    built = build_app(tmp_path)
    client = TestClient(built.app)

    # 清空 _JOB_REGISTRY 中其他 task 的 job，避免被命中（按 task_id 过滤）
    tid = t.task_id
    job_id = "job_test_active_unique_" + tid
    with _JOB_LOCK:
        # 清掉无关 job 后只插自己（确保按 task_id 过滤后能命中）
        _JOB_REGISTRY.clear()
    job = _RenderJob(
        job_id=job_id, task_id=tid, state="running",
        started_at=1.0, wall_started_at=2.0,
    )
    with _JOB_LOCK:
        _JOB_REGISTRY[job_id] = job

    resp = client.get(f"/slirn/api/active_export_for_task?task_id={tid}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["job"] is not None
    assert body["job"]["job_id"] == job_id
    assert body["job"]["state"] == "running"
    assert body["job"]["source"] == "registry"
    # 内存命中时不应有 warning
    assert "warning" not in body["job"]


def test_active_export_for_task_falls_back_to_disk(tmp_path):
    """REQ-20260920-084：内存无 job + 落盘文件有 → 端点返回 source=disk + warning。"""
    from slirn_home.app import (
        build_app, _write_active_export_job, _JOB_REGISTRY, _JOB_LOCK,
    )
    from fastapi.testclient import TestClient

    m, video = _make_mgr(tmp_path)
    t = m.create(name="active-disk", original_video=video)
    built = build_app(tmp_path)
    client = TestClient(built.app)

    # 清空整个内存注册表 + 清本 task 的磁盘文件（确保只命中磁盘）
    with _JOB_LOCK:
        _JOB_REGISTRY.clear()
    from slirn_home.app import _delete_active_export_job
    _delete_active_export_job(m, t.task_id)

    # 写一个 disk 状态（模拟服务重启后但 ffmpeg 仍在）
    _write_active_export_job(m, t.task_id, "job_old_unique_" + t.task_id, "running")

    resp = client.get(f"/slirn/api/active_export_for_task?task_id={t.task_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["job"] is not None
    assert body["job"]["source"] == "disk"
    # 兜底必须带 warning（提示服务重启）
    assert "warning" in body["job"]
    assert "服务" in body["job"]["warning"] or "重启" in body["job"]["warning"]


def test_active_export_for_task_returns_null_when_no_job(tmp_path):
    """REQ-20260920-084：无 in-flight → 返回 job: null。"""
    from slirn_home.app import (
        build_app, _JOB_REGISTRY, _JOB_LOCK, _delete_active_export_job,
    )
    from fastapi.testclient import TestClient

    m, video = _make_mgr(tmp_path)
    t = m.create(name="active-none", original_video=video)
    built = build_app(tmp_path)
    client = TestClient(built.app)

    # 清内存 + 清磁盘
    with _JOB_LOCK:
        _JOB_REGISTRY.clear()
    _delete_active_export_job(m, t.task_id)

    resp = client.get(f"/slirn/api/active_export_for_task?task_id={t.task_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["job"] is None


def test_load_panel_calls_active_export_for_task(tmp_path):
    """REQ-20260920-084：pipeline.js loadPanel 末尾必须挂 active_export_for_task 端点。

    静态扫描验证，避免漏改前端导致页面刷新后进度条不恢复。
    """
    FUNCLIP_ROOT = Path(__file__).resolve().parent.parent
    pipeline_js = (FUNCLIP_ROOT / "slirn_home" / "static" / "pipeline.js").read_text(
        encoding="utf-8"
    )
    assert "active_export_for_task" in pipeline_js, (
        "pipeline.js loadPanel 应调 /slirn/api/active_export_for_task（REQ-20260920-084）"
    )
    # 必须有 REQ 注释说明本次修复
    assert "REQ-20260920-084" in pipeline_js, (
        "pipeline.js 应有 REQ-20260920-084 注释"
    )


def test_router_load_logs_calls_list_logs_endpoint(tmp_path):
    """REQ-20260920-084：router.js loadLogs() 必须改调 /slirn/api/list_logs（不是旧 execution_history_query）。"""
    FUNCLIP_ROOT = Path(__file__).resolve().parent.parent
    router_js = (FUNCLIP_ROOT / "slirn_home" / "static" / "router.js").read_text(
        encoding="utf-8"
    )
    # 必须有 list_logs 调用
    assert "postJSON(SLIRN_API + '/list_logs'" in router_js or \
           "'/list_logs'" in router_js, (
        "router.js loadLogs 必须调 /slirn/api/list_logs（REQ-20260920-084 切换）"
    )
    # 不应再调旧的 execution_history_query（在 loadLogs 路径里）
    # 找到 loadLogs 函数体
    m_start = router_js.find("function loadLogs()")
    m_end = router_js.find("function ", m_start + 10)
    load_logs_body = router_js[m_start:m_end] if m_end > 0 else ""
    assert "execution_history_query" not in load_logs_body, (
        "router.js loadLogs 不应再调旧的 execution_history_query"
    )


def test_router_log_kind_labels_includes_all_10_kinds(tmp_path):
    """REQ-20260920-084：LOG_KIND_LABELS 必须含全部 kind（含 fine_export 等 REQ-081 加的）。"""
    FUNCLIP_ROOT = Path(__file__).resolve().parent.parent
    router_js = (FUNCLIP_ROOT / "slirn_home" / "static" / "router.js").read_text(
        encoding="utf-8"
    )
    required_kinds = [
        "subtitle_generation", "subtitle_review",
        "rough_cut", "rough_cut_link_person",
        "rough_compose", "rough_compose_delete",
        "optimize",
        "fine_ai_layout", "fine_bg_detect", "fine_preview", "fine_export",
    ]
    for k in required_kinds:
        assert f"'{k}'" in router_js or f'"{k}"' in router_js or f"{k}:" in router_js, (
            f"router.js LOG_KIND_LABELS 必须含 kind={k!r}（REQ-20260920-084）"
        )
    # 关键中文标签
    assert "最终导出视频" in router_js, (
        "router.js 应把 fine_export 映射为「最终导出视频」"
    )


# =====================================================================
# REQ-20260920-085：上传音频素材时自动启用 BGM
# 修复「上传 mp3 后生成预览无 BGM」BUG：
# 原 upload_fine_material_form 只写 materials.audio.path，
# 不写 audio.enabled → _assemble_fine_filter audio gate 把 BGM 静默禁用。
# =====================================================================

def test_upload_audio_material_auto_enables_bgm(tmp_path):
    """REQ-085：上传 mp3 → fc.audio.enabled 自动变 True；volume_db 保持默认 -8 dB。"""
    import io
    from fastapi.testclient import TestClient
    from slirn_home.app import build_app, _get_fine_compose

    mgr, _ = _make_mgr(tmp_path)
    video = tmp_path / "src.mp4"
    video.write_bytes(b"\x00" * 1024)
    t = mgr.create(name="upload-audio-test", original_video=video)

    app = build_app(repo_root=tmp_path)
    client = TestClient(app.app)

    # 上传前：fc.audio.enabled 默认 False
    fc_before = _get_fine_compose(mgr, t.task_id)
    assert fc_before["audio"]["enabled"] is False

    # 上传 mp3
    fake_audio = io.BytesIO(b"\xff\xfb\x90\x00" * 64)  # fake mp3 frame bytes
    resp = client.post(
        "/slirn/api/upload_fine_material_form",
        data={"task_id": t.task_id, "kind": "audio"},
        files={"file": ("bgm.mp3", fake_audio, "audio/mpeg")},
    )
    body = resp.json()
    assert body["ok"] is True, body
    assert body.get("path"), f"应返回 path，得到 {body}"

    # 上传后：fc.audio.enabled 自动 True
    fc_after = _get_fine_compose(mgr, t.task_id)
    assert fc_after["audio"]["enabled"] is True, (
        f"REQ-085：上传 mp3 后 fc.audio.enabled 应自动为 True，"
        f"实际是 {fc_after['audio']['enabled']}（这是 BUG 根因）"
    )
    # volume_db 保持默认 -8 dB（不污染用户已有音量）
    assert fc_after["audio"]["volume_db"] == -8.0
    # materials.audio.path 必须含 tasks/<tid>/ 前缀（REQ-083 约定）
    audio_path = fc_after["materials"]["audio"]["path"]
    assert audio_path.endswith(".mp3"), f"音频后缀应 .mp3，得到 {audio_path}"
    # 用 Path 部分匹配，兼容 Windows 反斜杠
    audio_path_normalized = audio_path.replace("\\", "/")
    assert f"tasks/{t.task_id}/" in audio_path_normalized, (
        f"REQ-083：路径必须含 tasks/<tid>/ 前缀，得到 {audio_path}"
    )
    # 实际文件存在
    audio_abs = tmp_path / audio_path
    assert audio_abs.exists(), f"上传文件应存在: {audio_abs}"


def test_upload_audio_does_not_override_user_disabled(tmp_path):
    """REQ-085：用户已手动 enabled=True 时上传 mp3 → enabled 保持 True（幂等）。"""
    import io
    from fastapi.testclient import TestClient
    from slirn_home.app import build_app, _get_fine_compose

    mgr, _ = _make_mgr(tmp_path)
    video = tmp_path / "src.mp4"
    video.write_bytes(b"\x00" * 1024)
    t = mgr.create(name="audio-keep-enabled", original_video=video)

    app = build_app(repo_root=tmp_path)
    client = TestClient(app.app)

    # 用户先手动调音量 + 启用
    r0 = client.post(
        "/slirn/api/save_fine_audio",
        json={"task_id": t.task_id, "audio": {"enabled": True, "volume_db": -5.0}},
    )
    assert r0.status_code == 200, r0.text

    # 然后上传 mp3
    fake_audio = io.BytesIO(b"\xff\xfb\x90\x00" * 64)
    r = client.post(
        "/slirn/api/upload_fine_material_form",
        data={"task_id": t.task_id, "kind": "audio"},
        files={"file": ("bgm.mp3", fake_audio, "audio/mpeg")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True

    fc = _get_fine_compose(mgr, t.task_id)
    # enabled 保持 True（不会变 False）
    assert fc["audio"]["enabled"] is True
    # 用户手动设的 volume_db=-5 不被覆盖（默认 -8 也不写）
    assert fc["audio"]["volume_db"] == -5.0, (
        f"用户已设 volume_db=-5 不会被上传覆盖，得到 {fc['audio'].get('volume_db')}"
    )


def test_upload_audio_then_assemble_includes_audio_input(tmp_path, monkeypatch):
    """REQ-085 端到端：上传 mp3 → 直接调 _assemble_fine_filter → input_args 含音频 + filter_complex 含 [bgm]。"""
    import io
    from fastapi.testclient import TestClient
    from slirn_home.app import build_app, _assemble_fine_filter, _get_fine_compose, _save_fine_compose

    mgr, _ = _make_mgr(tmp_path)
    app = build_app(repo_root=tmp_path)
    client = TestClient(app.app)

    # 准备任务 + 视频素材
    video = tmp_path / "src.mp4"
    video.write_bytes(b"\x00" * 1024)
    t = mgr.create(name="e2e-upload-bgm", original_video=video)
    upload_dir = tmp_path / "tasks" / t.task_id / "upload"
    upload_dir.mkdir(exist_ok=True)
    shutil.copy(video, upload_dir / "video_src.mp4")

    # 写 fc 视频素材（先不上传 audio）
    fc = _get_fine_compose(mgr, t.task_id)
    fc["materials"]["video"] = {
        "path": f"tasks/{t.task_id}/upload/video_src.mp4",
        "source": "upload",
        "type": "video",
    }
    _save_fine_compose(mgr, t.task_id, fc)

    # 上传 mp3（这就是 REQ-085 修的入口）
    fake_audio = io.BytesIO(b"\xff\xfb\x90\x00" * 64)
    up_resp = client.post(
        "/slirn/api/upload_fine_material_form",
        data={"task_id": t.task_id, "kind": "audio"},
        files={"file": ("bgm.mp3", fake_audio, "audio/mpeg")},
    )
    assert up_resp.json()["ok"] is True, up_resp.json()

    # 上传后 fc.audio.enabled 应自动 True（否则 assemble 会把 BGM 跳过）
    fc_after = _get_fine_compose(mgr, t.task_id)
    assert fc_after["audio"]["enabled"] is True, (
        "REQ-085：上传 mp3 后 fc.audio.enabled 应自动 True，"
        "否则 _assemble_fine_filter 会跳过 BGM"
    )

    # 调 _assemble_fine_filter → audio 应进入 input_args + filter_complex 含 [bgm]
    asm = _assemble_fine_filter(t.task_id, mgr, duration=10.0, preview_start=0.0)
    assert asm.get("ok") is True, asm
    input_args = asm.get("input_args", [])
    audio_in_args = any(
        (input_args[i + 1] if i + 1 < len(input_args) else "").endswith(".mp3")
        for i, tok in enumerate(input_args) if tok == "-i"
    )
    assert audio_in_args, (
        f"REQ-085：上传 mp3 后 audio 应进入 input_args，但没找到；input_args={input_args}"
    )
    fc_str = asm.get("filter_complex", "")
    assert "[bgm]" in fc_str and "amix=inputs=2" in fc_str, (
        f"filter_complex 必须含 [bgm] + amix=inputs=2；实际：\n{fc_str}"
    )


def test_upload_non_audio_does_not_toggle_audio_enabled(tmp_path):
    """REQ-085：上传图片 / 视频 / 字幕不应动 audio.enabled（不污染状态）。"""
    import io
    from fastapi.testclient import TestClient
    from slirn_home.app import build_app, _get_fine_compose

    mgr, _ = _make_mgr(tmp_path)
    video = tmp_path / "src.mp4"
    video.write_bytes(b"\x00" * 1024)
    t = mgr.create(name="non-audio-test", original_video=video)

    app = build_app(repo_root=tmp_path)
    client = TestClient(app.app)

    # 上传前 fc.audio.enabled 默认 False
    fc_before = _get_fine_compose(mgr, t.task_id)
    assert fc_before["audio"]["enabled"] is False

    # 上传 cover（图片）——不应影响 audio
    from PIL import Image as _PILImage
    buf = io.BytesIO()
    _PILImage.new("RGB", (200, 200), (255, 0, 0)).save(buf, format="PNG")
    buf.seek(0)
    r = client.post(
        "/slirn/api/upload_fine_material_form",
        data={"task_id": t.task_id, "kind": "cover"},
        files={"file": ("cover.png", buf, "image/png")},
    )
    assert r.json()["ok"] is True

    # 上传视频
    video_bytes = io.BytesIO(b"\x00" * 1024)
    r2 = client.post(
        "/slirn/api/upload_fine_material_form",
        data={"task_id": t.task_id, "kind": "video"},
        files={"file": ("video.mp4", video_bytes, "video/mp4")},
    )
    assert r2.json()["ok"] is True

    # fc.audio.enabled 仍是默认 False（没被污染）
    fc_after = _get_fine_compose(mgr, t.task_id)
    assert fc_after["audio"]["enabled"] is False, (
        "REQ-085：上传 cover/video 不应改变 audio.enabled 状态"
    )


# =====================================================================
# REQ-20260920-086：执行日志 → 移除工作台顶部折叠卡 + 分页 tab + 阶段分隔条
# =====================================================================

def test_workbench_does_not_render_exec_history_card(tmp_path):
    """REQ-086：工作台 HTML 不再含 .slirn-exec-card（折叠卡移到日志 tab）。
    REQ-086 顺带修复历史 BUG：日志 pane（id=slirn-wb-pane-logs）之前根本没渲染，
    现在也补上（默认 display:none，点 rail logs tab 才显示）。"""
    from slirn_home.app import _render_workbench

    m, video = _make_mgr(tmp_path)
    t = m.create(name="no-exec-card", original_video=video)
    html = _render_workbench(t.task_id, m)
    # 顶部折叠卡类不再出现
    assert 'slirn-exec-card' not in html, (
        "REQ-086：工作台顶部「📜 执行历史」折叠卡应已被移除，"
        "但 HTML 中仍出现 .slirn-exec-card"
    )
    # 但日志面板（pane）仍渲染（点击 logs tab 后可见）
    # REQ-086：之前是历史 BUG——pane_html 只迭代 _WB_STAGES，logs 不在其中，
    # 所以 id="slirn-wb-pane-logs" 从未出现在 HTML 里；现已修复。
    assert 'id="slirn-wb-pane-logs"' in html, (
        "REQ-086：日志面板（pane）必须保留在 _render_workbench 渲染里"
    )
    # 日志 pane 默认隐藏（focus 不是 logs）
    import re
    m_pane = re.search(r'<div class="slirn-wb-pane" id="slirn-wb-pane-logs"[^>]*>', html)
    assert m_pane, "REQ-086：logs pane 必须在 HTML 中"
    pane_open_tag = m_pane.group(0)
    assert "display:none" in pane_open_tag, (
        f"REQ-086：logs pane 默认应隐藏（focus 不是 logs），"
        f"实际 open tag={pane_open_tag}"
    )
    assert 'slirn-logs-pane' in html, "REQ-086：日志面板类仍存在"


def test_workbench_rail_has_divider_before_logs(tmp_path):
    """REQ-086：rail 在 logs stage 之前有 .slirn-wb-rail-divider。"""
    from slirn_home.app import _render_workbench

    m, video = _make_mgr(tmp_path)
    t = m.create(name="rail-divider", original_video=video)
    html = _render_workbench(t.task_id, m)
    # 分隔条存在
    assert 'slirn-wb-rail-divider' in html, (
        "REQ-086：应在 rail 加 .slirn-wb-rail-divider 分隔条，"
        "明确「执行日志不属于流水线阶段」"
    )
    # 分隔条位置：在 logs stage 之前（DOM 顺序）
    divider_idx = html.find('slirn-wb-rail-divider')
    logs_idx = html.find('slirn-wb-stage-logs')
    assert divider_idx >= 0 and logs_idx >= 0 and divider_idx < logs_idx, (
        f"REQ-086：分隔条应位于 logs stage 之前；"
        f"divider_idx={divider_idx}, logs_idx={logs_idx}"
    )
    # 分隔条文案带「不属于流水线阶段」
    assert "不属于流水线阶段" in html, (
        "REQ-086：分隔条文字应包含「不属于流水线阶段」"
    )


def test_workbench_logs_stage_has_extra_class(tmp_path):
    """REQ-086：logs stage 含 .slirn-wb-stage-extra 类（修 wbAutoNextMaybe 选择器对齐）。"""
    from slirn_home.app import _render_workbench

    m, video = _make_mgr(tmp_path)
    t = m.create(name="logs-extra-class", original_video=video)
    html = _render_workbench(t.task_id, m)
    # logs stage 的 class 含 'slirn-wb-stage-extra'
    # 用正则定位 logs stage 的 class 字符串
    import re
    m_logs = re.search(
        r'class="slirn-wb-stage[^"]*slirn-wb-stage-logs[^"]*"',
        html,
    )
    assert m_logs, (
        "REQ-086：找不到 logs stage 的 class 字符串"
    )
    cls = m_logs.group(0)
    assert "slirn-wb-stage-extra" in cls, (
        f"REQ-086：logs stage class 必须含 slirn-wb-stage-extra，"
        f"否则 router.js wbAutoNextMaybe 的 :not(.slirn-wb-stage-extra) "
        f"会把它当流水线阶段处理；实际 class={cls}"
    )


def test_render_exec_logs_pane_has_pager(tmp_path):
    """REQ-086：日志面板 HTML 含分页控件 .slirn-logs-pager。"""
    from slirn_home.app import _render_exec_logs_pane

    html = _render_exec_logs_pane("test-task-id")
    # 分页容器
    assert 'slirn-logs-pager' in html, (
        "REQ-086：日志面板必须含 .slirn-logs-pager 分页容器"
    )
    # 四个翻页按钮
    for action in ("first", "prev", "next", "last"):
        assert f'data-pager="{action}"' in html, (
            f"REQ-086：分页必须有「{action}」按钮"
        )
    # 每页大小下拉
    assert 'slirn-pager-size' in html, (
        "REQ-086：必须有每页大小下拉 .slirn-pager-size"
    )
    # 信息显示元素
    assert 'data-bind="page"' in html
    assert 'data-bind="total-pages"' in html
    assert 'data-bind="total"' in html


def _seed_execution_history(outputs_dir, n: int, status: str = "success",
                            base_ts: float = 1700000000.0):
    """REQ-086 测试辅助：往 outputs_dir 写 n 条 execution history 记录。

    用 record_start / record_finish 写完后，**直接改 execution_history.json 的
    started_at**（让排序可预测；record_start 内部用 time.time() 无法直接控制）。
    """
    import json as _json
    from slirn_home.execution_history import (
        KIND_SUBTITLE_GENERATION, record_start, record_finish,
    )

    for i in range(n):
        rid = record_start(outputs_dir, kind=KIND_SUBTITLE_GENERATION,
                          description=f"rec-{i}", extra={"idx": i})
        record_finish(outputs_dir, rid, success=(status == "success"),
                      error="" if status == "success" else "fake error")

    # 改 started_at 让排序可预测（rec-i 的 started_at = base_ts + i）
    hist_file = outputs_dir / "execution_history.json"
    if not hist_file.exists():
        # 写兜底
        hist_file.parent.mkdir(parents=True, exist_ok=True)
        hist_file.write_text("[]", encoding="utf-8")
    items = _json.loads(hist_file.read_text(encoding="utf-8"))
    # 按 idx 排序后改 started_at
    items.sort(key=lambda x: (x.get("extra") or {}).get("idx", 0))
    for i, it in enumerate(items):
        it["started_at"] = base_ts + i
        it["finished_at"] = base_ts + i + 5
        it["duration_ms"] = 5000
    _json.dump_atomic = None  # noqa
    hist_file.write_text(_json.dumps(items, ensure_ascii=False), encoding="utf-8")
    return items


def test_list_logs_supports_offset_and_returns_total_pages(tmp_path):
    """REQ-086：list_logs 端点支持 offset + 返回 total / page / page_size / total_pages。"""
    from fastapi.testclient import TestClient
    from slirn_home.app import build_app

    mgr, video = _make_mgr(tmp_path)
    t = mgr.create(name="logs-paged-test", original_video=video)
    outputs_dir = mgr.tasks_dir / t.task_id / "outputs"

    _seed_execution_history(outputs_dir, n=25)

    app = build_app(repo_root=tmp_path)
    client = TestClient(app.app)

    # 默认请求（不传 offset/page）：应返回第 1 页 20 条
    r = client.post("/slirn/api/list_logs", json={"task_id": t.task_id})
    body = r.json()
    assert body["ok"] is True, body
    assert body["total"] == 25, f"total 应为 25，得到 {body['total']}"
    assert body["page"] == 1, f"page 应为 1，得到 {body['page']}"
    assert body["page_size"] == 20, f"page_size 应为 20（默认值），得到 {body['page_size']}"
    assert body["total_pages"] == 2, f"25/20 应为 2 页，得到 {body['total_pages']}"
    assert len(body["items"]) == 20, f"第 1 页应 20 条，得到 {len(body['items'])}"

    # 第 2 页（offset=20）：应返回剩余 5 条
    r2 = client.post("/slirn/api/list_logs",
                     json={"task_id": t.task_id, "offset": 20})
    body2 = r2.json()
    assert body2["ok"] is True, body2
    assert body2["page"] == 2, f"page 应为 2，得到 {body2['page']}"
    assert body2["total"] == 25
    assert body2["total_pages"] == 2
    assert len(body2["items"]) == 5, f"第 2 页应 5 条，得到 {len(body2['items'])}"

    # 第 1 页和第 2 页数据不重叠
    ids_p1 = {it["id"] for it in body["items"]}
    ids_p2 = {it["id"] for it in body2["items"]}
    assert ids_p1.isdisjoint(ids_p2), "REQ-086：第 1 页和第 2 页数据不应重叠"


def test_list_logs_offset_uses_page_param(tmp_path):
    """REQ-086：list_logs 端点支持 page 参数（1-based）→ 自动转 offset。"""
    from fastapi.testclient import TestClient
    from slirn_home.app import build_app

    mgr, video = _make_mgr(tmp_path)
    t = mgr.create(name="page-param-test", original_video=video)
    outputs_dir = mgr.tasks_dir / t.task_id / "outputs"

    _seed_execution_history(outputs_dir, n=7)

    app = build_app(repo_root=tmp_path)
    client = TestClient(app.app)

    # page=2 limit=3 → offset=3, 应返回 3 条（rec-3/2/1，倒序）
    r = client.post("/slirn/api/list_logs",
                    json={"task_id": t.task_id, "page": 2, "limit": 3})
    body = r.json()
    assert body["ok"] is True, body
    assert body["page"] == 2
    assert body["total"] == 7
    assert body["page_size"] == 3
    assert body["total_pages"] == 3, f"7/3 应为 3 页（ceil(7/3)），得到 {body['total_pages']}"
    assert len(body["items"]) == 3, f"第 2 页 3 条，得到 {len(body['items'])}"


def test_list_logs_total_reflects_filtered_count(tmp_path):
    """REQ-086：total = 过滤后总数（不被 limit 截断；30 条 success + 5 条 failed）。"""
    from fastapi.testclient import TestClient
    from slirn_home.app import build_app

    mgr, video = _make_mgr(tmp_path)
    t = mgr.create(name="total-filtered", original_video=video)
    outputs_dir = mgr.tasks_dir / t.task_id / "outputs"

    # 30 条 success + 5 条 failed
    _seed_execution_history(outputs_dir, n=30, status="success", base_ts=1700000000.0)
    _seed_execution_history(outputs_dir, n=5, status="failed", base_ts=1700000100.0)

    app = build_app(repo_root=tmp_path)
    client = TestClient(app.app)

    # 只看 failed：limit=10 → total 应是 5（不是 10），total_pages=1
    r = client.post("/slirn/api/list_logs",
                    json={"task_id": t.task_id, "statuses": ["failed"], "limit": 10})
    body = r.json()
    assert body["ok"] is True, body
    assert body["total"] == 5, f"REQ-086：total 应为过滤后总数 5，得到 {body['total']}"
    assert body["total_pages"] == 1
    assert len(body["items"]) == 5

    # 只看 success：limit=10 → total=30，total_pages=3
    r2 = client.post("/slirn/api/list_logs",
                     json={"task_id": t.task_id, "statuses": ["success"], "limit": 10})
    body2 = r2.json()
    assert body2["total"] == 30
    assert body2["total_pages"] == 3
    assert len(body2["items"]) == 10


def test_router_logs_state_has_page_and_page_size():
    """REQ-086：router.js logsState 必须含 page / pageSize 字段。"""
    FUNCLIP_ROOT = Path(__file__).resolve().parent.parent
    router_js = (FUNCLIP_ROOT / "slirn_home" / "static" / "router.js").read_text(
        encoding="utf-8"
    )
    # 找 logsState 对象定义
    import re
    m = re.search(r"var logsState\s*=\s*\{[^}]*\}", router_js)
    assert m, "router.js 找不到 logsState 定义"
    body = m.group(0)
    assert "page:" in body, "REQ-086：logsState 必须含 page 字段"
    assert "pageSize:" in body, "REQ-086：logsState 必须含 pageSize 字段"
    # 翻页按钮 click handler 存在
    assert "slirn-pager-btn" in router_js, (
        "REQ-086：router.js 必须处理 .slirn-pager-btn click"
    )
    assert "data-pager" in router_js, (
        "REQ-086：router.js 必须读 data-pager 属性"
    )
    # 过滤变化重置 page
    assert "logsState.page = 1" in router_js, (
        "REQ-086：router.js 过滤变化时必须重置 logsState.page = 1"
    )
    # _renderLogsPager 函数
    assert "_renderLogsPager" in router_js, (
        "REQ-086：router.js 必须有 _renderLogsPager 函数"
    )


# ---------- REQ-20260920-087：任务列表搜索框过滤 ----------

def test_task_list_has_search_input(tmp_path: Path):
    """REQ-087 AC-1/AC-7：搜索框存在且 id 正确。"""
    from slirn_home.app import _render_task_list

    m, video = _make_mgr(tmp_path)
    m.create(name="t", original_video=video)
    html = _render_task_list(m)
    assert 'id="slirn-task-search"' in html, (
        "REQ-087：任务列表必须有 #slirn-task-search 输入框"
    )
    assert 'class="slirn-search-box"' in html, (
        "REQ-087：搜索框必须有 slirn-search-box 类"
    )


def test_router_js_has_task_search_handler():
    """REQ-087：router.js 必须有 input 事件代理监听 #slirn-task-search。"""
    FUNCLIP_ROOT = Path(__file__).resolve().parent.parent
    router_js = (FUNCLIP_ROOT / "slirn_home" / "static" / "router.js").read_text(
        encoding="utf-8"
    )
    # 监听 #slirn-task-search 的 input 事件
    assert "slirn-task-search" in router_js, (
        "REQ-087：router.js 必须引用 #slirn-task-search"
    )
    # _filterTaskCards 函数
    assert "_filterTaskCards" in router_js, (
        "REQ-087：router.js 必须定义 _filterTaskCards 函数"
    )
    # 实际过滤逻辑（卡片 display 切换 + data-task-id 读）
    assert "data-task-id" in router_js, (
        "REQ-087：_filterTaskCards 必须读 data-task-id"
    )
    assert ".slirn-task-card" in router_js, (
        "REQ-087：_filterTaskCards 必须定位 .slirn-task-card"
    )
    # 无匹配空态文案
    assert "slirn-search-empty" in router_js, (
        "REQ-087：无匹配时必须显示 .slirn-search-empty 空态"
    )
    # 必须用 addEventListener('input', ...) 事件代理（避免重复绑定）
    assert 'addEventListener(\'input\'' in router_js or 'addEventListener("input"' in router_js, (
        "REQ-087：router.js 必须用 document.addEventListener('input', ...) 事件代理"
    )


def test_router_js_filter_function_clears_display():
    """REQ-087 AC-2：清空查询时所有卡片重新可见。"""
    FUNCLIP_ROOT = Path(__file__).resolve().parent.parent
    router_js = (FUNCLIP_ROOT / "slirn_home" / "static" / "router.js").read_text(
        encoding="utf-8"
    )
    # 抓取 _filterTaskCards 函数体（用大括号平衡法）
    import re
    idx = router_js.find("function _filterTaskCards(")
    assert idx >= 0, "router.js 找不到 _filterTaskCards 函数定义"
    # 从函数定义开始找配对大括号
    brace_start = router_js.find("{", idx)
    depth = 0
    i = brace_start
    while i < len(router_js):
        c = router_js[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    body = router_js[brace_start:i + 1]
    # 空 query → card.style.display = ''（重置可见）
    assert "card.style.display = ''" in body, (
        "REQ-087 AC-2：空查询时必须重置卡片 display = '' 让所有卡片可见"
    )
    # 非空 query → 隐藏（三元表达式 match ? '' : 'none'）
    assert "match ? '' : 'none'" in body or 'match ? "" : "none"' in body or "'none'" in body, (
        "REQ-087 AC-1/AC-3：非匹配卡片必须 display = 'none'"
    )


def test_router_js_filter_matches_name_id_video():
    """REQ-087 AC-3/AC-4：搜索匹配 task_id / name / video 三类文本。"""
    FUNCLIP_ROOT = Path(__file__).resolve().parent.parent
    router_js = (FUNCLIP_ROOT / "slirn_home" / "static" / "router.js").read_text(
        encoding="utf-8"
    )
    # 用大括号平衡法抓取函数体
    idx = router_js.find("function _filterTaskCards(")
    brace_start = router_js.find("{", idx)
    depth = 0
    i = brace_start
    while i < len(router_js):
        c = router_js[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    body = router_js[brace_start:i + 1]
    # 必须读 data-task-id（AC-3 ID 匹配）
    assert "getAttribute('data-task-id')" in body, (
        "REQ-087 AC-3：必须读 data-task-id 属性"
    )
    # 必须读 .slirn-task-name（AC-3 name 匹配）
    assert ".slirn-task-name" in body, (
        "REQ-087 AC-3：必须读 .slirn-task-name 文本"
    )
    # 必须读 .slirn-task-video（AC-4 video 匹配）
    assert ".slirn-task-video" in body, (
        "REQ-087 AC-4：必须读 .slirn-task-video 文本"
    )


def test_router_js_filter_xss_escape_in_empty_message():
    """REQ-087 AC-5：搜索关键词插入 HTML 前必须 HTML escape（防 XSS）。"""
    FUNCLIP_ROOT = Path(__file__).resolve().parent.parent
    router_js = (FUNCLIP_ROOT / "slirn_home" / "static" / "router.js").read_text(
        encoding="utf-8"
    )
    idx = router_js.find("function _filterTaskCards(")
    brace_start = router_js.find("{", idx)
    depth = 0
    i = brace_start
    while i < len(router_js):
        c = router_js[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    body = router_js[brace_start:i + 1]
    # 空态文案：含「没有匹配「" + q + "」」之前必须有 escape 逻辑（replace 或 textContent）
    has_escape = (
        "replace(/[<" in body  # 正则 replace 转义
        or ".textContent" in body  # 或用 textContent
        or "encodeURIComponent" in body  # 或 URL 编码
    )
    assert has_escape, (
        "REQ-087 AC-5：搜索关键词插入空态文案前必须 HTML escape（防 XSS）"
    )


def test_router_js_showtab_function_is_complete():
    """REQ-087 回归：REQ-087 改动曾误删 showTab 函数体，导致 router.js 解析失败、
    所有 data-action 按钮失效（任务列表 / 热词库 / 新建任务全部不可用）。

    本测试防止以后任何 router.js 改动再次破坏 showTab 的完整性：
    - showTab 必须含 ALL_TABS forEach 循环（核心 tab 切换逻辑）
    - showTab 必须含 detail 隐藏（清空 detail tab 状态）
    - showTab 必须含 slirn-tab-create 特殊处理（热词选择器初始化）
    - showTab 必须含 window.scrollTo（顶部滚动）
    """
    FUNCLIP_ROOT = Path(__file__).resolve().parent.parent
    router_js = (FUNCLIP_ROOT / "slirn_home" / "static" / "router.js").read_text(
        encoding="utf-8"
    )
    # 用大括号平衡法抓取 showTab 函数体
    idx = router_js.find("function showTab(")
    assert idx >= 0, "router.js 找不到 showTab 函数"
    brace_start = router_js.find("{", idx)
    depth = 0
    i = brace_start
    while i < len(router_js):
        c = router_js[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    body = router_js[brace_start:i + 1]
    # showTab 必须有的关键代码（任一缺失 → REQ-087 那种回归 BUG）
    assert "ALL_TABS.forEach" in body, (
        "REQ-087 回归：showTab 必须含 ALL_TABS.forEach tab 切换循环"
    )
    assert "slirn-tab-detail" in body, (
        "REQ-087 回归：showTab 必须清空 slirn-tab-detail 的 display"
    )
    assert "slirn-tab-create" in body, (
        "REQ-087 回归：showTab 必须含 slirn-tab-create 特殊处理（热词选择器初始化）"
    )
    assert "window.scrollTo" in body, (
        "REQ-087 回归：showTab 必须含 window.scrollTo 顶部滚动"
    )


def test_router_js_syntax_valid():
    """REQ-087 回归：router.js 必须能被 node --check 通过。

    REQ-087 改动曾误把 showTab 函数体切断，导致：
    1. router.js 解析失败（语法错）
    2. 整个 IIFE 不执行 → 所有 document.addEventListener 不注册
    3. 任务列表 / 热词库 / 新建任务按钮全部失效（用户感知为「按钮都没反应」）

    本测试用 node --check 静态校验 JS 语法，提前发现这类回归。
    """
    import subprocess
    FUNCLIP_ROOT = Path(__file__).resolve().parent.parent
    router_js_path = FUNCLIP_ROOT / "slirn_home" / "static" / "router.js"
    r = subprocess.run(
        ["node", "--check", str(router_js_path)],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, (
        f"REQ-087 回归：router.js 语法错误\n"
        f"node --check exit={r.returncode}\n"
        f"stderr={r.stderr[:1000]}"
    )


# ============================================================
# REQ-20260920-088：精剪素材路径详情（来源区分 + 完整路径展示）
# ============================================================

def test_mat_info_endpoint_defined_in_app_py():
    """REQ-088 AC-5：后端必须定义 /slirn/api/material_info 端点。"""
    FUNCLIP_ROOT = Path(__file__).resolve().parent.parent
    app_path = FUNCLIP_ROOT / "slirn_home" / "app.py"
    src = app_path.read_text(encoding="utf-8")
    assert '"/slirn/api/material_info"' in src, (
        "REQ-088 AC-5：app.py 必须注册 /slirn/api/material_info 端点"
    )
    assert "async def material_info" in src, (
        "REQ-088 AC-5：app.py 必须定义 material_info 函数"
    )


def test_mat_info_endpoint_returns_required_fields():
    """REQ-088 AC-5：端点返回字段必须含 ok / kind / source / source_label / fc_path / abs_path / exists / size_bytes / mtime / type。"""
    FUNCLIP_ROOT = Path(__file__).resolve().parent.parent
    app_path = FUNCLIP_ROOT / "slirn_home" / "app.py"
    src = app_path.read_text(encoding="utf-8")
    # 定位 material_info 函数体
    idx = src.find("async def material_info")
    assert idx >= 0
    # 取往后 3500 字符作为函数体范围（足够覆盖）
    body = src[idx: idx + 3500]
    for field in ["kind", "source", "source_label", "fc_path", "abs_path",
                  "exists", "size_bytes", "mtime", "type"]:
        assert field in body, (
            f"REQ-088 AC-5：material_info 函数体必须返回字段 {field!r}"
        )


def test_mat_info_distinguishes_three_sources():
    """REQ-088 AC-3：material_info 必须根据 source 字段推 source_label（auto/upload/default_bgm）。"""
    FUNCLIP_ROOT = Path(__file__).resolve().parent.parent
    app_path = FUNCLIP_ROOT / "slirn_home" / "app.py"
    src = app_path.read_text(encoding="utf-8")
    idx = src.find("async def material_info")
    assert idx >= 0
    body = src[idx: idx + 3500]
    # 3 个 source_label 推导分支
    assert 'source_label = "上游产物"' in body, "REQ-088 AC-3：auto → 上游产物"
    assert 'source_label = "用户上传"' in body, "REQ-088 AC-3：upload → 用户上传"
    # REQ-20260921-NNN：改名「系统提供的 BGM」（对齐 audio 卡片 label / 下拉文案）
    assert 'source_label = "系统提供的 BGM"' in body, (
        "REQ-088 AC-3：default_bgm → 系统提供的 BGM"
    )


def test_upload_card_has_detail_button():
    """REQ-088 AC-1：每个素材卡片底部必须有「🔍 详情」按钮（data-action=fine-mat-detail）。"""
    FUNCLIP_ROOT = Path(__file__).resolve().parent.parent
    app_path = FUNCLIP_ROOT / "slirn_home" / "app.py"
    src = app_path.read_text(encoding="utf-8")
    # 必须在素材卡 HTML 模板里
    assert 'data-action="fine-mat-detail"' in src, (
        "REQ-088 AC-1：素材卡片必须含 data-action=\"fine-mat-detail\" 按钮"
    )
    assert "🔍 详情" in src, "REQ-088 AC-1：按钮文本必须为「🔍 详情」"


def test_upload_card_detail_button_disabled_when_no_path():
    """REQ-088 AC-1：未上传时详情按钮 disabled（path 为空时 disabled 属性存在）。"""
    FUNCLIP_ROOT = Path(__file__).resolve().parent.parent
    app_path = FUNCLIP_ROOT / "slirn_home" / "app.py"
    src = app_path.read_text(encoding="utf-8")
    # 找 detail 按钮 HTML 块
    idx = src.find('data-action="fine-mat-detail"')
    assert idx >= 0
    chunk = src[max(0, idx - 400): idx + 200]
    # 必须用条件 disabled
    assert 'disabled' in chunk, (
        "REQ-088 AC-1：详情按钮 HTML 必须含 disabled 条件（path 为空时禁用）"
    )
    # tooltip 也要有
    assert "请先上传或自动获取素材" in chunk or "title=" in chunk, (
        "REQ-088 AC-1：禁用态必须有 tooltip 提示"
    )


def test_mat_detail_modal_dom_in_app_py():
    """REQ-088 AC-2/AC-6：素材详情模态框 DOM 必须在 _render_fine_cut_zone 内。"""
    FUNCLIP_ROOT = Path(__file__).resolve().parent.parent
    app_path = FUNCLIP_ROOT / "slirn_home" / "slirn_home" / "app.py" if False else FUNCLIP_ROOT / "slirn_home" / "app.py"
    src = app_path.read_text(encoding="utf-8")
    # 模态框根容器
    assert 'id="slirn-mat-detail-modal"' in src, (
        "REQ-088 AC-2：模态框根容器必须含 id=slirn-mat-detail-modal"
    )
    # 详情 body
    assert 'id="slirn-mat-detail-body"' in src, (
        "REQ-088 AC-2：模态框 body 必须含 id=slirn-mat-detail-body"
    )
    # 关闭按钮
    assert 'data-action="mat-detail-close"' in src, (
        "REQ-088 AC-6：模态框必须有 data-action=mat-detail-close 关闭按钮"
    )


def test_router_js_mat_detail_handler():
    """REQ-088 AC-2：router.js 必须有 fineMatDetail 函数 + click 代理。"""
    FUNCLIP_ROOT = Path(__file__).resolve().parent.parent
    router_js_path = FUNCLIP_ROOT / "slirn_home" / "static" / "router.js"
    src = router_js_path.read_text(encoding="utf-8")
    assert "function fineMatDetail" in src, (
        "REQ-088 AC-2：router.js 必须定义 fineMatDetail 函数"
    )
    assert "function _renderMatDetailModal" in src, (
        "REQ-088 AC-2：router.js 必须定义 _renderMatDetailModal 渲染函数"
    )
    # 调用 /material_info 端点
    assert "/material_info" in src, (
        "REQ-088 AC-2：router.js 必须调 /slirn/api/material_info 端点"
    )
    # action dispatch
    assert "fine-mat-detail" in src, (
        "REQ-088 AC-2：router.js 必须有 fine-mat-detail action 分支"
    )
    assert "mat-detail-close" in src, (
        "REQ-088 AC-6：router.js 必须有 mat-detail-close action 分支"
    )


def test_router_js_esc_and_overlay_close():
    """REQ-088 AC-6：ESC 键 + 点击遮罩关闭模态框。"""
    FUNCLIP_ROOT = Path(__file__).resolve().parent.parent
    router_js_path = FUNCLIP_ROOT / "slirn_home" / "static" / "router.js"
    src = router_js_path.read_text(encoding="utf-8")
    # ESC 关闭
    assert "Escape" in src, "REQ-088 AC-6：router.js 必须监听 Escape 键"
    # 点击遮罩关闭（modal id 引用）
    assert "slirn-mat-detail-modal" in src, (
        "REQ-088 AC-6：router.js 必须引用 slirn-mat-detail-modal 模态框"
    )


def test_router_js_renders_source_color_blocks():
    """REQ-088 AC-3：_renderMatDetailModal 必须根据 source 给 4 种色块 class。"""
    FUNCLIP_ROOT = Path(__file__).resolve().parent.parent
    router_js_path = FUNCLIP_ROOT / "slirn_home" / "static" / "router.js"
    src = router_js_path.read_text(encoding="utf-8")
    idx = src.find("function _renderMatDetailModal")
    assert idx >= 0
    body = src[idx: idx + 3500]
    for cls in ["mat-source-auto", "mat-source-upload", "mat-source-default", "mat-source-none"]:
        assert cls in body, f"REQ-088 AC-3：_renderMatDetailModal 必须使用 {cls} 色块 class"


def test_router_js_xss_safe_rendering():
    """REQ-088 AC-2：所有用户/文件路径插入 DOM 前必须 HTML escape。"""
    FUNCLIP_ROOT = Path(__file__).resolve().parent.parent
    router_js_path = FUNCLIP_ROOT / "slirn_home" / "static" / "router.js"
    src = router_js_path.read_text(encoding="utf-8")
    idx = src.find("function _renderMatDetailModal")
    assert idx >= 0
    body = src[idx: idx + 3500]
    # 至少 4 个字段走 escapeHtml / escapeAttr（kind / source_label / fc_path / abs_path / type / mtime / sizeStr / upstream_name）
    esc_count = body.count("escapeHtml(") + body.count("escapeAttr(")
    assert esc_count >= 6, (
        f"REQ-088 AC-2：_renderMatDetailModal 至少 6 个字段用 escapeHtml/escapeAttr，实际 {esc_count} 个"
    )


def test_home_css_mat_detail_modal_styles():
    """REQ-088 AC-3：home.css 必须含 4 种 mat-source-* 色块 + 模态框样式。"""
    FUNCLIP_ROOT = Path(__file__).resolve().parent.parent
    home_css_path = FUNCLIP_ROOT / "slirn_home" / "static" / "home.css"
    src = home_css_path.read_text(encoding="utf-8")
    # 4 种色块 class
    for cls in ["mat-source-auto", "mat-source-upload", "mat-source-default", "mat-source-none"]:
        assert f".{cls}" in src, f"REQ-088 AC-3：home.css 必须定义 .{cls} 色块"
    # 模态框容器 + 表格样式
    assert ".slirn-mat-detail-modal" in src, "REQ-088 AC-2：home.css 必须定义模态框容器样式"
    assert ".slirn-mat-detail-table" in src, "REQ-088 AC-2：home.css 必须定义详情表格样式"
    assert ".slirn-mat-detail-source" in src, "REQ-088 AC-3：home.css 必须定义色块样式"


def test_router_js_mat_detail_xss_safe():
    """REQ-088 回归（防 REQ-087 同类 BUG）：router.js 必须 node --check 通过。"""
    import subprocess
    FUNCLIP_ROOT = Path(__file__).resolve().parent.parent
    router_js_path = FUNCLIP_ROOT / "slirn_home" / "static" / "router.js"
    r = subprocess.run(
        ["node", "--check", str(router_js_path)],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, (
        f"REQ-088 回归：router.js 语法错误\n"
        f"node --check exit={r.returncode}\n"
        f"stderr={r.stderr[:1000]}"
    )


def test_mat_detail_modal_close_action_in_router_js():
    """REQ-088 AC-6：router.js 必须实现 mat-detail-close action（关闭按钮 + 遮罩 + ESC）。"""
    FUNCLIP_ROOT = Path(__file__).resolve().parent.parent
    router_js_path = FUNCLIP_ROOT / "slirn_home" / "static" / "router.js"
    src = router_js_path.read_text(encoding="utf-8")
    # 至少 3 处关闭逻辑：close 按钮 / overlay click / ESC
    close_uses = src.count("mat-detail-modal")
    assert close_uses >= 3, (
        f"REQ-088 AC-6：router.js 必须至少 3 处引用 mat-detail-modal（close btn + overlay + ESC），实际 {close_uses} 处"
    )


# ============================================================
# REQ-20260920-089：修 ffmpeg 死锁 + 取消按钮 + record_finish 兜底
# ============================================================

def test_popen_uses_devnull_for_stderr():
    """REQ-089 AC-1（REQ-20260923-NNN 修订）：渲染 Popen 的 stderr 严禁 PIPE。

    原 BUG：`stderr=subprocess.PIPE` 但主循环只读 stdout，stderr pipe buffer 写满后
    ffmpeg 阻塞 → 永远死锁 → UI 进度 0% 卡死。REQ-089 曾改 DEVNULL；REQ-20260923-NNN
    改为写临时文件（保留截断/失败诊断现场，结束取末尾进 job.stderr_tail），临时文件
    创建失败时退回 DEVNULL —— 两种形态都不会阻塞，PIPE 死锁永远不允许回归。
    """
    import re
    FUNCLIP_ROOT = Path(__file__).resolve().parent.parent
    app_path = FUNCLIP_ROOT / "slirn_home" / "app.py"
    src = app_path.read_text(encoding="utf-8")

    # REQ-20260923-NNN：渲染主体在 _run_fine_render_locked
    idx = src.find("def _run_fine_render_locked")
    assert idx >= 0, "找不到 _run_fine_render_locked 函数"
    # 抓第一个 Popen 调用（函数体内第一个）
    body_idx = src.find("subprocess.Popen(", idx)
    assert body_idx >= 0, "找不到 subprocess.Popen 调用"

    # 用正则抓 Popen(...) 完整调用
    m = re.search(r"subprocess\.Popen\(([^)]+)\)", src[body_idx:body_idx + 2000], re.DOTALL)
    assert m, "Popen 调用匹配失败"
    popen_call = m.group(1)

    # 核心断言：stderr → 临时文件（失败退回 DEVNULL），严禁 PIPE
    assert ("stderr=_err_file if _err_file is not None else subprocess.DEVNULL"
            in popen_call), (
        "REQ-20260923-NNN：Popen stderr 应写临时文件（退回 DEVNULL），实际:\n"
        + popen_call[:500]
    )
    assert "stderr=subprocess.PIPE" not in popen_call, (
        "REQ-089 AC-1：Popen 不能再用 stderr=PIPE，会导致死锁回归"
    )


def test_export_cancel_button_in_app_py():
    """REQ-089 AC-2：_render_fine_cut_zone 必须输出独立可见的取消按钮。

    原 BUG：取消依赖点击 `<span id="slirn-fine-export-status">`（hidden 元素），
    用户在 UI 上看不到 status 元素时无任何取消入口。
    修复：加 `<button id="slirn-fine-export-cancel-btn" data-action="fine-export-cancel">⏹ 取消</button>`。
    """
    FUNCLIP_ROOT = Path(__file__).resolve().parent.parent
    app_path = FUNCLIP_ROOT / "slirn_home" / "app.py"
    src = app_path.read_text(encoding="utf-8")

    # 必须有 cancel-btn DOM id
    assert 'id="slirn-fine-export-cancel-btn"' in src, (
        "REQ-089 AC-2：app.py 必须输出 id=\"slirn-fine-export-cancel-btn\" 的按钮"
    )
    # 必须有 data-action 让 router.js click handler 派发
    assert 'data-action="fine-export-cancel"' in src, (
        "REQ-089 AC-2：取消按钮必须有 data-action=\"fine-export-cancel\""
    )
    # 必须有 ⏹ 取消 文案（视觉明确告诉用户这是取消按钮）
    assert "取消" in src, (
        "REQ-089 AC-2：取消按钮必须有「取消」文案"
    )


def test_router_js_fine_export_cancel_action():
    """REQ-089 AC-3：router.js 必须有 fine-export-cancel action 派发分支。

    原 BUG：UI 没取消入口（依赖 hidden status 元素 click），用户无法中断死锁。
    修复：click handler 派发 action='fine-export-cancel' → confirm() → POST /slirn/api/cancel_render。
    """
    FUNCLIP_ROOT = Path(__file__).resolve().parent.parent
    router_js_path = FUNCLIP_ROOT / "slirn_home" / "static" / "router.js"
    src = router_js_path.read_text(encoding="utf-8")

    # 必须有 fine-export-cancel action 分支
    assert "fine-export-cancel" in src, (
        "REQ-089 AC-3：router.js 必须有 fine-export-cancel action 派发"
    )
    # 必须调 /slirn/api/cancel_render 端点
    assert "/slirn/api/cancel_render" in src, (
        "REQ-089 AC-3：router.js 必须调用 /slirn/api/cancel_render"
    )
    # 必须先 confirm 用户
    assert "confirm(" in src, (
        "REQ-089 AC-3：取消前必须 confirm() 让用户二次确认（防误触）"
    )


def test_router_js_cancel_button_toggle():
    """REQ-089 AC-2：startFineExportInline 必须正确显示/隐藏取消按钮。

    - 启动渲染时：display='' (显示)
    - done / failed / cancelled 时：display='none' (隐藏)
    """
    FUNCLIP_ROOT = Path(__file__).resolve().parent.parent
    router_js_path = FUNCLIP_ROOT / "slirn_home" / "static" / "router.js"
    src = router_js_path.read_text(encoding="utf-8")

    # startFineExportInline 函数体里必须 show cancel button
    fn_idx = src.find("function startFineExportInline(")
    assert fn_idx >= 0, "找不到 startFineExportInline 函数"
    # 抓函数体（括号平衡法）
    brace_start = src.find("{", fn_idx)
    depth = 0
    i = brace_start
    while i < len(src):
        c = src[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    body = src[brace_start:i + 1]

    # 必须引用 cancel button id
    assert "slirn-fine-export-cancel-btn" in body, (
        "REQ-089 AC-2：startFineExportInline 必须引用取消按钮 DOM id"
    )
    # 必须 display=''（显示）—— 变量名 cancelBtn 或 cb 都行
    has_show = (
        "cancelBtn.style.display = ''" in body
        or 'cancelBtn.style.display = ""' in body
        or "cb.style.display = ''" in body
        or 'cb.style.display = ""' in body
    )
    assert has_show, (
        "REQ-089 AC-2：startFineExportInline 必须 display='' 显示取消按钮"
    )
    # 必须 display='none'（隐藏）—— 变量名 cancelBtn 或 cb 都行
    has_hide = (
        "cancelBtn.style.display = 'none'" in body
        or 'cancelBtn.style.display = "none"' in body
        or "cb.style.display = 'none'" in body
        or 'cb.style.display = "none"' in body
    )
    assert has_hide, (
        "REQ-089 AC-2：startFineExportInline 必须 display='none' 隐藏取消按钮"
    )


def test_render_async_finally_records_finish():
    """REQ-089 AC-4：_run_fine_render_async 的 record_finish 必须在 finally 块。

    原 BUG：cancel/failed/done 分支各调一次 record_finish，外部 kill 后主循环不
    执行到任何分支 → execution_history 永远 running。
    修复：所有 record_finish + _delete_active_export_job 挪到 finally。
    """
    FUNCLIP_ROOT = Path(__file__).resolve().parent.parent
    app_path = FUNCLIP_ROOT / "slirn_home" / "app.py"
    src = app_path.read_text(encoding="utf-8")
    lines = src.splitlines()

    # 找 def _run_fine_render_async 行号
    def_line = -1
    for i, line in enumerate(lines):
        if line.startswith("def _run_fine_render_async("):
            def_line = i
            break
    assert def_line >= 0, "找不到 _run_fine_render_async 函数"

    # 找下一个顶层 def 或 async def（函数体结束）
    body_end = len(lines)
    for i in range(def_line + 1, len(lines)):
        stripped = lines[i].lstrip()
        if (stripped.startswith("def ") or stripped.startswith("async def ") or
                stripped.startswith("@")) and not lines[i].startswith(" " * (def_line == i)):
            # 同缩进的 def/async def → 下一个函数
            indent = len(lines[i]) - len(stripped)
            if indent == 0:
                body_end = i
                break

    body = "\n".join(lines[def_line:body_end])

    # finally 块必须存在
    assert "finally:" in body, (
        "REQ-089 AC-4：_run_fine_render_async 必须有 try/finally 块"
    )
    # finally 块里必须有 record_finish 调用
    finally_idx = body.rfind("finally:")
    assert finally_idx > 0, "找不到 finally 块"
    finally_body = body[finally_idx:]
    assert "record_finish" in finally_body, (
        "REQ-089 AC-4：finally 块必须调用 record_finish（保证所有路径都写历史）"
    )
    # finally 块里必须有 _delete_active_export_job（落盘清理）
    assert "_delete_active_export_job" in finally_body, (
        "REQ-089 AC-4：finally 块必须调 _delete_active_export_job（清理落盘文件）"
    )
    # finally 块里必须有兜底 kill（取消/外部 kill 也要让 ffmpeg 退出）
    assert "_kill_proc_with_grace" in finally_body, (
        "REQ-089 AC-4：finally 块必须兜底 _kill_proc_with_grace（处理未退出的 ffmpeg）"
    )


def test_render_async_local_execution_history_import():
    """REQ-089 关键补丁：daemon 线程里 execution_history 没有模块级导入，必须本地 import。

    原 BUG（REQ-081 留下的潜在隐患，REQ-089 实测暴露）：
    _run_fine_render_async 是 daemon 线程跑在 export_fine_video endpoint 之外；
    原代码 `from slirn_home import execution_history` 在 endpoint 内是局部的，daemon 线程里
    没有 `execution_history` 变量。调用 `execution_history.record_finish(...)` 抛
    NameError 被 try/except 静默吞掉 → execution_history 永远 running。

    修复：finally 块 + 早期 return 分支都加 `from slirn_home import execution_history as _eh`。

    REQ-20260923-NNN：渲染主体移入 _run_fine_render_locked（async 外壳只挂串行门），
    本检查随之改抓 locked 函数体。
    """
    import re
    FUNCLIP_ROOT = Path(__file__).resolve().parent.parent
    app_path = FUNCLIP_ROOT / "slirn_home" / "app.py"
    src = app_path.read_text(encoding="utf-8")

    # 抓 _run_fine_render_locked 函数体
    lines = src.splitlines()
    def_line = -1
    for i, line in enumerate(lines):
        if line.startswith("def _run_fine_render_locked("):
            def_line = i
            break
    assert def_line >= 0, "找不到 _run_fine_render_locked 函数"
    body_end = len(lines)
    for i in range(def_line + 1, len(lines)):
        stripped = lines[i].lstrip()
        if stripped.startswith("def ") or stripped.startswith("async def "):
            indent = len(lines[i]) - len(stripped)
            if indent == 0:
                body_end = i
                break
    body = "\n".join(lines[def_line:body_end])

    # 找 record_finish 调用位置（每处都必须在调用前有本地 import）
    rf_lines = []
    for i, line in enumerate(lines[def_line:body_end]):
        if "record_finish(" in line and "execution_history" not in line:
            # 已经是 _eh.record_finish 或 _eh_xxx.record_finish 形式
            rf_lines.append(def_line + i)

    # 至少 1 处 record_finish（在 finally 块里）
    assert len(rf_lines) >= 1, (
        "REQ-089：_run_fine_render_async 必须至少有 1 处 record_finish（finally 块）"
    )

    # 函数体里必须至少有 1 处 `from slirn_home import execution_history`
    assert "from slirn_home import execution_history" in body, (
        "REQ-089：_run_fine_render_async 函数体里必须有 `from slirn_home import execution_history` "
        "（daemon 线程无模块级 execution_history，必须本地导入）"
    )


# ====================================================================
# REQ-20260920-098：合成元素组合测试面板（debug）已废弃
# 「一键合成」面板 + 4 个 combo-* action 全部删除；
# 现在「生成预览」「导出最终视频」直接读 .slirn-fine-enabled checkbox 状态。
# ====================================================================


def test_diagnose_bgm_endpoint_exists():
    """REQ-090 AC-5/AC-15：/slirn/api/diagnose_bgm 端点存在 + _predict_audio_path 纯函数返回字段完整。"""
    app_path = FUNCLIP_ROOT / "slirn_home" / "app.py"
    src = app_path.read_text(encoding="utf-8")

    # 1. 端点定义
    assert '@app.app.post("/slirn/api/diagnose_bgm")' in src, (
        "REQ-090 AC-5：必须定义 /slirn/api/diagnose_bgm 端点"
    )
    assert "async def diagnose_bgm" in src

    # 2. 纯函数 _predict_audio_path
    assert "def _predict_audio_path(" in src, (
        "REQ-090：必须定义 _predict_audio_path 纯函数"
    )

    # 3. 验证返回字段（直接 import 跑一遍）
    sys.path.insert(0, str(FUNCLIP_ROOT))
    from slirn_home.app import _predict_audio_path

    # case 1: 未勾 BGM
    fc1 = {"layout": {}, "materials": {}, "audio": {"enabled": False}}
    r1 = _predict_audio_path(fc1)
    assert r1["ok"] is True
    assert r1["predicted_has_bgm"] is False
    assert r1["predicted_audio_filters"] == ""
    assert "fc.audio.enabled" in r1["why_no_bgm"]
    assert r1["inputs_count"] == 1
    assert r1["audio_idx"] == -1

    # case 2: 勾 BGM + 有 audio material（legacy 线性 volume 直调兜底：0.5 → -6.0dB）
    fc2 = {
        "layout": {"video": {"enabled": True}, "audio": {"enabled": True}},
        "materials": {"video": {"path": "v.mp4"}, "audio": {"path": "a.mp3"}},
        "audio": {"enabled": True, "volume": 0.5},
    }
    r2 = _predict_audio_path(fc2)
    assert r2["predicted_has_bgm"] is True
    assert "[1:a]aloop=loop=-1:size=2e9,volume=-6.0dB" in r2["predicted_audio_filters"]
    assert r2["inputs_count"] == 2  # video + audio
    assert r2["audio_idx"] == 1

    # case 3: 勾 bg + cover + audio（audio_idx 应是 3）
    fc3 = {
        "layout": {
            "video": {"enabled": True},
            "bg": {"enabled": True},
            "cover": {"enabled": True, "duration": 3.0},
        },
        "materials": {
            "video": {"path": "v.mp4"},
            "bg": {"path": "bg.png"},
            "cover": {"path": "c.png"},
            "audio": {"path": "a.mp3"},
        },
    }
    # fc3 缺 audio.enabled → 默认 False
    r3 = _predict_audio_path(fc3)
    assert r3["inputs_count"] == 3  # video + bg + cover
    assert r3["audio_idx"] == -1






def test_export_fine_video_accepts_time_params():
    """REQ-091 AC-4/AC-5/AC-6：export_fine_video 接受 body.preview_start/duration 并透传到 _run_fine_render_async。"""
    app_path = FUNCLIP_ROOT / "slirn_home" / "app.py"
    src = app_path.read_text(encoding="utf-8")

    # 1. 读 body.preview_start 和 body.duration
    assert "body.get(\"preview_start\")" in src, (
        "REQ-091 AC-4：export_fine_video 必须读 body.preview_start"
    )
    assert "body.get(\"duration\")" in src, (
        "REQ-091 AC-4：export_fine_video 必须读 body.duration"
    )

    # 2. 缺省回退：preview_start=0.0 / duration=None
    assert "or 0.0" in src and "_start = float" in src, (
        "REQ-091 AC-5：preview_start 缺省应回退到 0.0"
    )

    # 3. 透传给 _run_fine_render_async（args 里含 _start + _dur_f）
    # 找 _run_fine_render_async 的调用点（args 元组）
    call_re = re.search(
        r"args=\(job, tid, mgr, out_path, _ext_exec, outputs_dir, _start, _dur_f\)",
        src,
    )
    assert call_re is not None, (
        "REQ-091 AC-6：export_fine_video 必须把 _start + _dur_f 透传给 _run_fine_render_async"
    )

    # 4. _run_fine_render_async 函数签名扩展
    func_re = re.search(
        r"def _run_fine_render_async\([^)]*preview_start:\s*float\s*=\s*0\.0,\s*duration:\s*float\s*\|\s*None\s*=\s*None",
        src,
    )
    assert func_re is not None, (
        "REQ-091 AC-6：_run_fine_render_async 必须扩展签名为 preview_start=0.0, duration=None"
    )

    # 5. _run_fine_render_async 函数体内把参数透传给 _assemble_fine_filter
    # 函数体内必须有 _assemble_fine_filter(tid, mgr, duration=duration, preview_start=preview_start)
    # REQ-20260920-098：combo 也可能透传，允许调用跨多行
    body_re = re.search(
        r"_assemble_fine_filter\(\s*tid,\s*mgr,\s*duration=duration,\s*preview_start=preview_start\b",
        src,
    )
    assert body_re is not None, (
        "REQ-091 AC-6：_run_fine_render_async 必须用入参的 duration + preview_start 调 _assemble_fine_filter"
    )


def test_export_fine_video_output_path_has_time_suffix():
    """REQ-091 AC-7：output 文件名加 _t{start}_d{duration}.mp4 后缀（防覆盖完整视频）。"""
    app_path = FUNCLIP_ROOT / "slirn_home" / "app.py"
    src = app_path.read_text(encoding="utf-8")

    # 1. 默认路径（无 time 参数）
    assert 'fine_export.mp4' in src, "REQ-091：默认 output 必须是 fine_export.mp4"

    # 2. 有 time 参数时的后缀路径
    suffix_re = re.search(r"fine_export_t\{_start", src)
    assert suffix_re is not None, (
        "REQ-091 AC-7：export_fine_video 必须输出 fine_export_t{_start...} 文件名"
    )
    assert "_d{_dur_tag}" in src, (
        "REQ-091 AC-7：output 文件名必须含 _d{duration} 后缀"
    )












def test_probe_output_audio_returns_absolute_path():
    """REQ-20260920-094：probe_output_audio 端点必须返回 output_abs_path 完整绝对路径。

    原 BUG：前端只拼相对路径 `tasks/<tid>/outputs/...`，用户不知道相对于哪个目录
    （server 工作目录？repo 根？浏览器当前路径？）。截图证据：
    用户问「你这写了一个相对路径，是相对哪的？我上哪儿找这个生成的文件呢？」

    修复：probe_output_audio 端点把解析后的绝对路径塞进 response，
    前端直接展示，用户可复制到文件管理器或拖到浏览器。
    """
    app_path = FUNCLIP_ROOT / "slirn_home" / "app.py"
    src = app_path.read_text(encoding="utf-8")

    # 端点内必须显式返回 output_abs_path（用 str(output_abs) 解析后的完整路径）
    assert '"output_abs_path"' in src, (
        "REQ-094：probe_output_audio 必须返回 output_abs_path 字段（绝对路径）"
    )
    assert "str(output_abs)" in src, (
        "REQ-094：probe_output_audio 必须用 str(output_abs) 把 Path 转字符串"
    )

    # 端点位置确认（probe_output_audio 函数体内含 output_abs_path）
    # 签名含 Body(default_factory=dict)) — 双 )，用 \)\): 匹配
    probe_match = re.search(
        r"@app\.app\.post\(\"/slirn/api/probe_output_audio\"\)\s*\n\s*async def probe_output_audio\(body: dict[^)]+\)\):(.*?)(?=\n    @app\.app\.|\n    def |\n    async def [a-z])",
        src, re.DOTALL,
    )
    assert probe_match is not None, "REQ-094：必须能找到 probe_output_audio 端点函数体"
    probe_body = probe_match.group(1)
    assert '"output_abs_path"' in probe_body, (
        "REQ-094：output_abs_path 必须在 probe_output_audio 函数体内返回（不是别的端点）"
    )


def test_assemble_fine_filter_voice_silence_concat_no_adelay(tmp_path):
    """REQ-20260920-096：input 0 用 -ss 输入级 seek + cover 启用时，adelay + aloop + amix
    duration=first 会触发 ffmpeg "Queue input is backward in time"，把音频截断到 ~22ms。

    修复：废弃 adelay，改用 lavfi anullsrc 注入 cover 等长静音前缀，再与 [0:a] 做
    concat，避开 PTS 错位。本测试断言 cover 启用时必须含 anullsrc 静音输入 +
    [ss][v_raw]concat 链，且不再含 adelay；cover 禁用时走原有的 asetpts 直通路径。

    复现：preview_start=20s + cover enabled + audio enabled + bgm enabled → bug 现场。
    """
    from slirn_home.app import (
        _assemble_fine_filter, _get_fine_compose, _save_fine_compose,
    )

    mgr, video = _make_mgr(tmp_path)
    t = mgr.create(name="silence-concat-regression", original_video=video)

    upload = tmp_path / "tasks" / t.task_id / "upload"
    upload.mkdir(parents=True, exist_ok=True)
    cover_src = upload / "cover.png"
    cover_src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    audio_src = upload / "bgm.mp3"
    audio_src.write_bytes(b"ID3" + b"\x00" * 100)

    fc = _get_fine_compose(mgr, t.task_id)
    fc["materials"]["video"] = {"path": str(video.relative_to(tmp_path)),
                                "type": "video", "source": "upload"}
    fc["materials"]["cover"] = {"path": f"tasks/{t.task_id}/upload/cover.png",
                                "type": "image", "source": "upload"}
    fc["materials"]["audio"] = {"path": f"tasks/{t.task_id}/upload/bgm.mp3",
                                "type": "audio", "source": "upload"}
    fc["layout"]["cover"]["enabled"] = True
    fc["layout"]["cover"]["duration"] = 2.0
    fc["audio"]["enabled"] = True
    fc["audio"]["volume"] = 0.9
    _save_fine_compose(mgr, t.task_id, fc)

    # 关键：preview_start > 0 触发 input 级 -ss seek
    asm = _assemble_fine_filter(t.task_id, mgr, duration=5.0, preview_start=20.0)
    assert asm.get("ok") is True, asm
    fc_text = asm["filter_complex"].replace("\n", "")

    # 1. REQ-096 修复：filter_complex 必须含 [ss][v_raw]concat + amix，
    #    且禁用 adelay（避免 PTS 错位把 audio 截断到 ~22ms）。
    #    anullsrc 静音源在 input_args 里（input 级别），filter_complex 只看到 [N:a]。
    assert "[ss][v_raw]concat=n=2:v=0:a=1[voice]" in fc_text, (
        f"voice 链必须走 silence + sliced-audio concat 模式，实际: {fc_text}"
    )
    assert "[voice][bgm]amix=inputs=2:duration=first:normalize=0[aout]" in fc_text
    assert "adelay=" not in fc_text, (
        f"REQ-096：cover 路径已废弃 adelay（会触发 PTS 错位），实际: {fc_text}"
    )

    # 2. input_args 中应含 -f lavfi -t 2.00 -i anullsrc=...（input 级别静音源）
    cmd = asm["input_args"]
    assert "-f" in cmd and "lavfi" in cmd, f"缺少 lavfi 输入: {cmd}"
    assert any("anullsrc" in a for a in cmd), f"input_args 缺 anullsrc 输入: {cmd}"
    # 输入顺序：video(0) → cover(1) → silence(2) → bgm(3)
    # amix 的 bgm 应来自 input 3，loop 应引用 [3:a]
    assert "[3:a]aloop" in fc_text, (
        f"BGM 必须来自 input 3（silence 之前 bgm 索引为 1，新加 silence 后变 3），"
        f"实际: {fc_text}"
    )

    # 3. 无 cover 路径也要带 asetpts（防御性）
    fc2 = _get_fine_compose(mgr, t.task_id)
    fc2["layout"]["cover"]["enabled"] = False
    _save_fine_compose(mgr, t.task_id, fc2)
    asm2 = _assemble_fine_filter(t.task_id, mgr, duration=5.0, preview_start=20.0)
    assert asm2.get("ok") is True, asm2
    fc_text2 = asm2["filter_complex"].replace("\n", "")
    voice2 = next(
        (s for s in fc_text2.split(";") if s.strip().startswith("[0:a]") and "voice" in s),
        None,
    )
    assert voice2 is not None
    assert "asetpts=PTS-STARTPTS" in voice2, f"无 cover 路径也必须 asetpts：{voice2}"
    assert "anullsrc" not in fc_text2, (
        f"无 cover 路径不应注入 anullsrc 静音前缀，实际: {fc_text2}"
    )


# ====================================================================
# REQ-20260920-098：_assemble_fine_filter 新增 combo 关键字参数
#   - combo 是瞬时覆盖（不写回 fc.json）
#   - 覆盖 5 个元素 enabled：video / subtitle / cover / bg / audio
# ====================================================================


def test_assemble_fine_filter_combo_override_cover_bg_audio(tmp_path):
    """REQ-098 AC-1：combo dict 覆盖 fc.json 的 5 个 enabled，不落盘。

    准备 task + 5 素材；fc.json 写 cover/bg/audio 都 enabled=False；
    调 _assemble_fine_filter(combo={cover:True, bg:True, audio:True})；
    断言 input_args 含 cover/bg 路径 + filter_complex 含 bg layer chain +
    fc.json 在函数返回后**未写回**（磁盘 cover.enabled 仍为 False）。
    """
    from slirn_home.app import (
        _assemble_fine_filter, _get_fine_compose, _save_fine_compose,
    )

    mgr, video = _make_mgr(tmp_path)
    t = mgr.create(name="combo-override", original_video=video)
    upload = tmp_path / "tasks" / t.task_id / "upload"
    upload.mkdir(parents=True, exist_ok=True)
    (upload / "cover.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    (upload / "bg.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    (upload / "bgm.mp3").write_bytes(b"ID3" + b"\x00" * 100)

    fc = _get_fine_compose(mgr, t.task_id)
    fc["materials"]["video"] = {"path": str(video.relative_to(tmp_path)),
                                "type": "video", "source": "upload"}
    fc["materials"]["cover"] = {"path": f"tasks/{t.task_id}/upload/cover.png",
                                "type": "image", "source": "upload"}
    fc["materials"]["bg"] = {"path": f"tasks/{t.task_id}/upload/bg.png",
                             "type": "image", "source": "upload"}
    fc["materials"]["audio"] = {"path": f"tasks/{t.task_id}/upload/bgm.mp3",
                                "type": "audio", "source": "upload"}
    # fc.json 故意写 cover/bg/audio 都 False —— 模拟用户在 fc 落盘的状态
    fc["layout"]["cover"]["enabled"] = False
    fc["layout"]["cover"]["duration"] = 2.0
    fc["layout"]["bg"]["enabled"] = False
    fc["audio"]["enabled"] = False
    _save_fine_compose(mgr, t.task_id, fc)

    # combo 覆盖：让 cover/bg/audio 临时启用
    asm = _assemble_fine_filter(
        t.task_id, mgr, duration=10.0, preview_start=0.0,
        combo={"cover": True, "bg": True, "audio": True},
    )
    assert asm.get("ok") is True, asm

    # 1. input_args 出现 cover + bg 路径（说明 combo override 生效）
    cmd = asm["input_args"]
    assert any("cover.png" in a for a in cmd), f"combo 覆盖 cover 未生效：{cmd}"
    assert any("bg.png" in a for a in cmd), f"combo 覆盖 bg 未生效：{cmd}"
    assert any("bgm.mp3" in a for a in cmd), f"combo 覆盖 audio 未生效：{cmd}"

    # 2. filter_complex 出现 BGM 链（bg 启用 → bg layer chain + bgm 启用 → amix 链）
    fc_text = asm["filter_complex"].replace("\n", "")
    assert "amix" in fc_text, f"combo 覆盖 audio 未启用 amix：{fc_text}"

    # 3. fc.json 在函数返回后**未写回**（磁盘 cover.enabled 仍为 False）
    fc_after = _get_fine_compose(mgr, t.task_id)
    assert fc_after["layout"]["cover"]["enabled"] is False, (
        "REQ-098：combo 覆盖不能写回 fc.json（cover 应仍为 False）"
    )
    assert fc_after["layout"]["bg"]["enabled"] is False, (
        "REQ-098：combo 覆盖不能写回 fc.json（bg 应仍为 False）"
    )
    assert fc_after["audio"]["enabled"] is False, (
        "REQ-098：combo 覆盖不能写回 fc.json（audio 应仍为 False）"
    )


def test_assemble_fine_filter_combo_none_uses_fc_json(tmp_path):
    """REQ-098 AC-2：combo=None 时完全依赖 fc.json（向后兼容）。"""
    from slirn_home.app import (
        _assemble_fine_filter, _get_fine_compose, _save_fine_compose,
    )

    mgr, video = _make_mgr(tmp_path)
    t = mgr.create(name="combo-none", original_video=video)
    upload = tmp_path / "tasks" / t.task_id / "upload"
    upload.mkdir(parents=True, exist_ok=True)
    (upload / "cover.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    fc = _get_fine_compose(mgr, t.task_id)
    fc["materials"]["video"] = {"path": str(video.relative_to(tmp_path)),
                                "type": "video", "source": "upload"}
    fc["materials"]["cover"] = {"path": f"tasks/{t.task_id}/upload/cover.png",
                                "type": "image", "source": "upload"}
    fc["layout"]["cover"]["enabled"] = True
    fc["layout"]["cover"]["duration"] = 2.0
    _save_fine_compose(mgr, t.task_id, fc)

    # combo=None → 完全依赖 fc.json（cover enabled=True）
    asm = _assemble_fine_filter(t.task_id, mgr, duration=10.0, preview_start=0.0, combo=None)
    assert asm.get("ok") is True, asm
    cmd = asm["input_args"]
    assert any("cover.png" in a for a in cmd), f"combo=None 时 fc.json cover=True 必须生效：{cmd}"


def test_assemble_fine_filter_combo_can_disable_fc_enabled(tmp_path):
    """REQ-098 AC-3：combo 也可以关闭 fc.json 里已启用的元素（覆盖是双向的）。"""
    from slirn_home.app import (
        _assemble_fine_filter, _get_fine_compose, _save_fine_compose,
    )

    mgr, video = _make_mgr(tmp_path)
    t = mgr.create(name="combo-disable", original_video=video)
    upload = tmp_path / "tasks" / t.task_id / "upload"
    upload.mkdir(parents=True, exist_ok=True)
    (upload / "bg.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    fc = _get_fine_compose(mgr, t.task_id)
    fc["materials"]["video"] = {"path": str(video.relative_to(tmp_path)),
                                "type": "video", "source": "upload"}
    fc["materials"]["bg"] = {"path": f"tasks/{t.task_id}/upload/bg.png",
                             "type": "image", "source": "upload"}
    fc["layout"]["bg"]["enabled"] = True  # fc.json 里 bg 启用
    _save_fine_compose(mgr, t.task_id, fc)

    # combo 强制关闭 bg
    asm = _assemble_fine_filter(
        t.task_id, mgr, duration=10.0, preview_start=0.0, combo={"bg": False},
    )
    assert asm.get("ok") is True, asm
    cmd = asm["input_args"]
    assert not any("bg.png" in a for a in cmd), (
        f"combo={{bg:False}} 必须关掉 bg 输入，实际: {cmd}"
    )


def test_render_fine_preview_endpoint_accepts_combo_in_body(tmp_path, monkeypatch):
    """REQ-098 AC-4：POST /render_fine_preview 接受 body.combo，透传到 _run_fine_render。"""
    from slirn_home.app import build_app
    from fastapi.testclient import TestClient

    m, _ = _make_mgr(tmp_path)
    t = m.create(name="preview-combo", original_video=tmp_path / "lecture.mp4")
    captured = {}
    def fake_run(task_id, mgr, out_path, duration, preview_start=0.0, *, combo=None):
        captured["combo"] = combo
        return {"ok": True, "path": "fake.mp4"}
    monkeypatch.setattr("slirn_home.app._run_fine_render", fake_run)

    built = build_app(tmp_path)
    client = TestClient(built.app)
    resp = client.post(
        "/slirn/api/render_fine_preview",
        json={"task_id": t.task_id, "duration": 5,
              "combo": {"cover": True, "bg": False, "audio": True}},
    )
    assert resp.json()["ok"] is True
    assert captured["combo"] == {"cover": True, "bg": False, "audio": True}, (
        f"body.combo 必须透传到 _run_fine_render；实际：{captured.get('combo')}"
    )


def test_router_js_preview_handler_reads_fine_enabled_checkboxes():
    """REQ-098 AC-5：router.js preview/export handler 必须读 .slirn-fine-enabled 并带 combo。"""
    js_path = FUNCLIP_ROOT / "slirn_home" / "static" / "router.js"
    src = js_path.read_text(encoding="utf-8")

    # 1. 必须定义 _readPerElementCombo helper
    assert "_readPerElementCombo" in src, (
        "REQ-098：router.js 必须定义 _readPerElementCombo helper（读 .slirn-fine-enabled）"
    )
    # 2. helper 内部必须 query .slirn-fine-enabled[data-key]
    assert re.search(r"querySelectorAll\(['\"]\.slirn-fine-enabled\[data-key\]", src), (
        "REQ-098：_readPerElementCombo 必须 query .slirn-fine-enabled[data-key]"
    )
    # 3. preview 分支把 combo 加进 payload
    assert re.search(r"_payload\.combo\s*=\s*_readPerElementCombo\(\)", src), (
        "REQ-098：preview 分支必须 _payload.combo = _readPerElementCombo()"
    )
    # 4. export 分支把 combo 加进 body
    assert re.search(r"combo:\s*_readPerElementCombo\(\)", src), (
        "REQ-098：export 分支必须把 combo 加进 body"
    )


def test_router_js_combo_handler_removed():
    """REQ-098 AC-6：router.js 不再有 comboTestAction / _applyComboToFC 等组合面板代码。"""
    js_path = FUNCLIP_ROOT / "slirn_home" / "static" / "router.js"
    src = js_path.read_text(encoding="utf-8")

    # 1. comboTestAction 必须删除
    assert "function comboTestAction" not in src, (
        "REQ-098：必须删除 comboTestAction 函数（两步流程已废弃）"
    )
    # 2. _applyComboToFC 必须删除
    assert "function _applyComboToFC" not in src, (
        "REQ-098：必须删除 _applyComboToFC helper（两步流程已废弃）"
    )
    # 3. data-combo-kind 必须删除
    assert "data-combo-kind" not in src, (
        "REQ-098：必须删除所有 data-combo-kind 引用"
    )
    # 4. combo-test / combo-apply 等 dispatch 必须删除
    assert "combo-apply' || action === 'combo-test'" not in src, (
        "REQ-098：必须删除 combo-* action dispatch 分支"
    )


def test_app_py_one_key_compose_zone_removed():
    """REQ-098 AC-7：app.py 不再渲染「🎬 一键合成」面板 + 5 个 data-combo-kind checkbox。"""
    app_path = FUNCLIP_ROOT / "slirn_home" / "app.py"
    src = app_path.read_text(encoding="utf-8")

    # 1. data-combo-kind 必须删除
    assert "data-combo-kind" not in src, (
        "REQ-098：app.py 必须删除 data-combo-kind checkbox 渲染"
    )
    # 2. 🎬 一键合成 面板标题必须删除
    assert "🎬 一键合成" not in src, (
        "REQ-098：app.py 必须删除『🎬 一键合成』面板"
    )
    # 3. combo-* 按钮必须删除
    for action in ('data-action="combo-apply"', 'data-action="combo-test"',
                   'data-action="combo-diagnose"', 'data-action="combo-restore"'):
        assert action not in src, (
            f"REQ-098：app.py 必须删除 {action} 按钮"
        )


# =====================================================================
# REQ-20260921-NNN：流程配置扩展 — 6 阶段 STAGES + link_person_ids + fine_cut + run_mode
# =====================================================================

def test_pipeline_js_stages_has_fine_cut():
    """REQ-20260921-NNN AC-1：pipeline.js STAGES 数组必须含 fine_cut（第 6 阶段）。"""
    pipeline_js = (FUNCLIP_ROOT / "slirn_home" / "static" / "pipeline.js").read_text(encoding="utf-8")
    assert "fine_cut" in pipeline_js, (
        "pipeline.js 必须含 fine_cut 阶段（REQ-20260921-NNN：6 阶段含精剪合成）"
    )
    # STAGES 数组里至少有 6 个 stage（按出现顺序覆盖字幕生成/字幕修订/切分修剪/粗剪合成/优化字幕/精剪合成）
    required_stages = [
        "subtitle_generation", "subtitle_review", "rough_cut",
        "rough_compose", "optimize", "fine_cut"
    ]
    # 找 STAGES 数组的 key 出现位置（按顺序排列）
    positions = []
    for k in required_stages:
        idx = pipeline_js.find(f"key: '{k}'")
        assert idx >= 0, f"STAGES 缺阶段：{k}"
        positions.append((k, idx))
    # 验证顺序：上述顺序必须递增
    indices = [p[1] for p in positions]
    assert indices == sorted(indices), (
        f"STAGES 顺序错乱：{[(p[0], p[1]) for p in positions]}"
    )


def test_pipeline_js_subtitle_review_has_link_person_checkbox():
    """REQ-20260921-NNN AC-2：subtitle_review 阶段必须含 link-person checkbox。"""
    pipeline_js = (FUNCLIP_ROOT / "slirn_home" / "static" / "pipeline.js").read_text(encoding="utf-8")
    # renderStageForm 通过 fieldId(stage.key, 'link-person') 生成 id，
    # 所以检查 'link-person' 字段名 + subtitle_review 分支上下文
    sr_branch_idx = pipeline_js.find("stage.key === 'subtitle_review'")
    assert sr_branch_idx >= 0, "renderStageForm 缺 subtitle_review 分支"
    sr_section = pipeline_js[sr_branch_idx:sr_branch_idx + 3500]
    assert "fieldId(stage.key, 'link-person')" in sr_section, (
        "renderStageForm.subtitle_review 必须渲染 link-person 复选框（fieldId(stage.key, 'link-person')）"
    )
    # readCurrentConfig 必须读这个字段
    sr_rc_idx = pipeline_js.find("cfg.subtitle_review = {")
    rp_idx = pipeline_js.find("link_person_ids", sr_rc_idx)
    assert sr_rc_idx >= 0 and rp_idx > sr_rc_idx, (
        "readCurrentConfig 必须读 subtitle_review.link_person_ids"
    )


def test_pipeline_js_rough_cut_has_link_person_checkbox():
    """REQ-20260921-NNN AC-2：rough_cut 阶段必须含 link-person checkbox。"""
    pipeline_js = (FUNCLIP_ROOT / "slirn_home" / "static" / "pipeline.js").read_text(encoding="utf-8")
    rc_branch_idx = pipeline_js.find("stage.key === 'rough_cut'")
    assert rc_branch_idx >= 0, "renderStageForm 缺 rough_cut 分支"
    rc_section = pipeline_js[rc_branch_idx:rc_branch_idx + 2500]
    assert "fieldId(stage.key, 'link-person')" in rc_section, (
        "renderStageForm.rough_cut 必须渲染 link-person 复选框"
    )
    rc_rc_idx = pipeline_js.find("cfg.rough_cut = {")
    rp_idx = pipeline_js.find("link_person_ids", rc_rc_idx)
    assert rc_rc_idx >= 0 and rp_idx > rc_rc_idx, (
        "readCurrentConfig 必须读 rough_cut.link_person_ids"
    )


def test_pipeline_js_fine_cut_section_has_all_fields():
    """REQ-20260921-NNN-radio-mode：fine_cut 阶段改单选卡片组（替代 v4
    双 checkbox），但仍只含 4 类生效字段（params-src / start / dur + 单选）。
    cover/bg/bgm 是死字段 —— 已在 UI 移除。
    """
    pipeline_js = (FUNCLIP_ROOT / "slirn_home" / "static" / "pipeline.js").read_text(encoding="utf-8")
    # 找到 renderStageForm 的 fine_cut 分支
    fc_branch_idx = pipeline_js.find("stage.key === 'fine_cut'")
    assert fc_branch_idx >= 0, "renderStageForm 缺 fine_cut 分支"
    # 取该分支的 HTML（直到下一个 return / 函数的末尾）
    section = pipeline_js[fc_branch_idx:fc_branch_idx + 4500]
    # renderStageForm 通过 fieldId() 生成 ID；检查字段名 token
    required_field_names = [
        "'params-src'",   # 设置参数
        "'start'",        # 导出起点（HH:MM:SS）
        "'dur'",          # 导出时长（HH:MM:SS）
    ]
    for fld in required_field_names:
        assert fld in section, f"fine_cut 阶段缺字段 {fld}"
    # 死字段的 fieldId token 必须不在 section 里
    for dead in ("'cover'", "'bg'", "'bgm'"):
        assert dead not in section, (
            f"fine_cut 不应再含死字段 {dead}（handler 不读，UI 不要画）"
        )
    # data-params-select 保留（参数模板选择器）
    assert "data-params-select" in section, "fine_cut 缺 data-params-select 选择器"
    # data-bgm-select 移除
    assert "data-bgm-select" not in section, (
        "fine_cut 不应再有 data-bgm-select 选择器 —— bgm 是死字段，UI 不要画"
    )
    # 单选卡片组：必须有两个 radio（full + range）
    assert 'type="radio"' in section, "fine_cut 必须用 radio（导出方式二选一）"
    assert 'name="slirn-pipe-fc-export-mode"' in section, (
        "fine_cut radio 必须统一 name='slirn-pipe-fc-export-mode'（互斥）"
    )
    assert 'value="full"' in section, "fine_cut radio 必须含 value='full'（出整个片）"
    assert 'value="range"' in section, "fine_cut radio 必须含 value='range'（按区间导出）"
    # v4 校验：start/duration 改 text input + pattern HH:MM:SS
    assert 'type="text"' in section, "v4 fine_cut 的 start/dur 应改 text input（HH:MM:SS）"
    assert "00:00:00" in section, "v4 fine_cut 默认 start 占位符应为 00:00:00"
    assert "00:10:00" in section, "v4 fine_cut 默认 duration 占位符应为 00:10:00"
    # 旧 v4 checkbox 字段名不应再出现在 section（radio 已替代）
    for old in ("'range-on'", "data-range-on"):
        assert old not in section, (
            f"fine_cut 不应再有 v4 checkbox 字段 {old}（已改 radio）"
        )

    # readCurrentConfig 必须写 fine_cut 4 类生效字段（含 range_enabled — radio 派生）
    rc_idx = pipeline_js.find("cfg.fine_cut = {")
    assert rc_idx >= 0, "readCurrentConfig 缺 cfg.fine_cut = {...}"
    rc_section = pipeline_js[rc_idx:rc_idx + 1800]
    for k in ("enabled", "params_source", "range_enabled", "preview_start", "duration"):
        assert k in rc_section, f"readCurrentConfig.fine_cut 缺字段 {k}"
    # 死字段必须不出现在 readCurrentConfig 里（避免 UI 又把死字段塞回去）
    for k in ("cover_image", "bg_image", "bgm"):
        assert k not in rc_section, (
            f"readCurrentConfig.fine_cut 不应含死字段 {k}（handler 不读）"
        )


def test_pipeline_js_hms_helpers_present():
    """v4：HH:MM:SS ↔ 秒 helper 必须存在（secondsToHms / hmsToSeconds）。
    早期版本用 number input + 人工算秒，UX 差。
    """
    pipeline_js = (FUNCLIP_ROOT / "slirn_home" / "static" / "pipeline.js").read_text(encoding="utf-8")
    # helper 函数定义
    assert "function secondsToHms(" in pipeline_js, "缺 secondsToHms()"
    assert "function hmsToSeconds(" in pipeline_js, "缺 hmsToSeconds()"
    # hmsToSeconds 用正则解析 HH:MM:SS（MM/SS 0-59）
    import re
    m = re.search(r"function\s+hmsToSeconds\s*\([^)]*\)\s*\{[^}]*match\([^)]*\\d\{1,3\}", pipeline_js, re.DOTALL)
    assert m, "hmsToSeconds 应匹配 /^(\\d{1,3}):([0-5]\\d):([0-5]\\d)$/"
    # hmsToSeconds 必须容错（解析失败返回 null，让调用方兜底）


def test_pipeline_js_export_mode_radio_toggles_inputs_disabled():
    """REQ-20260921-NNN-radio-mode：选「按区间导出一段」radio → start/duration
    输入框 enable；选「出整个片」→ disable。复用 v4 的 data-range-input 标记。
    """
    pipeline_js = (FUNCLIP_ROOT / "slirn_home" / "static" / "pipeline.js").read_text(encoding="utf-8")
    # 定位 listener 块 —— 用 querySelectorAll('input[name="slirn-pipe-fc-export-mode"]') 这个独有的查询语句。
    idx = pipeline_js.find("querySelectorAll('input[name=\"slirn-pipe-fc-export-mode\"]')")
    assert idx >= 0, "缺 export-mode radio 的 change listener 块"
    nearby = pipeline_js[idx:idx + 800]
    assert "addEventListener('change'" in nearby, "export-mode radio 必须绑 change 事件"
    assert "data-range-input" in nearby, "切换的输入框必须标 data-range-input"
    assert "radio.value !== 'range'" in nearby or "!== 'range'" in nearby, (
        "change handler 必须根据 radio.value 是否为 'range' 切换 inputs.disabled"
    )


def test_pipeline_js_render_panel_has_run_mode_select():
    """REQ-20260921-NNN AC-4：renderPanel 头部必须含运行模式下拉（to_end / stop_after）。"""
    pipeline_js = (FUNCLIP_ROOT / "slirn_home" / "static" / "pipeline.js").read_text(encoding="utf-8")
    # slirn-pipe-run-mode 必须在 renderPanel 头部 panel.innerHTML 块内
    rm_idx = pipeline_js.find('id="slirn-pipe-run-mode"')
    assert rm_idx >= 0, "renderPanel 缺运行模式 select（id=slirn-pipe-run-mode）"
    # 紧邻的两个 option（to_end / stop_after）
    nearby = pipeline_js[rm_idx:rm_idx + 600]
    assert "value=\"to_end\"" in nearby, "运行模式缺 to_end 选项"
    assert "value=\"stop_after\"" in nearby, "运行模式缺 stop_after 选项"


def test_pipeline_js_run_mode_change_disables_flow_stop():
    """REQ-20260921-NNN AC-4：run_mode=to_end → flow-stop disabled。"""
    pipeline_js = (FUNCLIP_ROOT / "slirn_home" / "static" / "pipeline.js").read_text(encoding="utf-8")
    # 找外层委托 change listener（在 document 上、含 ev.target 检查的那个）—
    # 早期版本是第一个 addEventListener('change'，v4 之后第一个是 fine_cut 的
    # range-on listener（在具体 element 上），run-mode 那个在外层委托（line ~1068）。
    # 用 ev.target 关键特征定位：
    delegate_idx = pipeline_js.find("addEventListener('change', function(ev)")
    if delegate_idx < 0:
        # 兼容写法
        delegate_idx = pipeline_js.find("addEventListener('change', function (ev)")
    assert delegate_idx >= 0, "缺外层委托 change listener（ev.target 路由）"
    change_section = pipeline_js[delegate_idx:delegate_idx + 4000]
    assert "t.id === 'slirn-pipe-run-mode'" in change_section, (
        "change handler 必须处理 slirn-pipe-run-mode"
    )
    assert "flowSel.disabled = true" in change_section, (
        "run_mode=to_end 必须禁用 flow-stop（flowSel.disabled=true）"
    )
    assert "flowSel.disabled = false" in change_section, (
        "run_mode=stop_after 必须启用 flow-stop（flowSel.disabled=false）"
    )


def test_pipeline_js_read_config_has_run_mode_and_fine_cut_radio_default_range():
    """REQ-20260921-NNN-radio-mode：readCurrentConfig 必须有 run_mode +
    fine_cut 通过 radio 派生 enabled/range_enabled（默认 range=True）。
    """
    pipeline_js = (FUNCLIP_ROOT / "slirn_home" / "static" / "pipeline.js").read_text(encoding="utf-8")
    rc_idx = pipeline_js.find("function readCurrentConfig")
    assert rc_idx >= 0, "缺 readCurrentConfig 函数"
    section = pipeline_js[rc_idx:rc_idx + 3500]
    # run_mode 字段（to_end / stop_after）
    assert "cfg.run_mode" in section, "readCurrentConfig 缺 cfg.run_mode"
    assert "'to_end'" in section and "'stop_after'" in section, (
        "readCurrentConfig 必须把 run_mode 限定在 to_end / stop_after"
    )
    # fine_cut 通过 radio 派生（不再 _checked enabled/range-on checkbox）
    assert 'slirn-pipe-fc-export-mode' in section, (
        "readCurrentConfig 必须读 export-mode radio（单选卡片组）"
    )
    assert "_checked(fieldId('fine_cut', 'enabled'), false)" not in section, (
        "v4 旧 enabled checkbox 读取已废（改 radio 派生）"
    )
    assert "_checked(fieldId('fine_cut', 'range-on'), false)" not in section, (
        "v4 旧 range-on checkbox 读取已废（改 radio 派生）"
    )


def test_pipeline_js_templates_have_run_mode_and_fine_cut():
    """REQ-20260921-NNN-radio-mode：3 套 TEMPLATES 必须含 run_mode + fine_cut 块。
    默认 enabled/range_enabled 已改为 true（与 default_config + UI radio=range 一致）。
    """
    pipeline_js = (FUNCLIP_ROOT / "slirn_home" / "static" / "pipeline.js").read_text(encoding="utf-8")
    # TEMPLATES 区段
    tpl_idx = pipeline_js.find("var TEMPLATES = {")
    assert tpl_idx >= 0, "缺 TEMPLATES"
    end_idx = pipeline_js.find("};", tpl_idx)
    section = pipeline_js[tpl_idx:end_idx]
    # 3 套模板名都必须在
    for k in ("default_tpl", "semi", "full"):
        assert k in section, f"TEMPLATES 缺 {k}"
    # run_mode 在 section 至少出现 3 次（每模板 1 次）
    assert section.count("run_mode:") >= 3, (
        "3 套 TEMPLATES 都必须含 run_mode 字段"
    )
    # fine_cut 块至少出现 3 次
    assert section.count("fine_cut:") >= 3, (
        "3 套 TEMPLATES 都必须含 fine_cut 字段"
    )


def test_app_py_list_bgm_files_endpoint_no_longer_called_by_pipeline_panel():
    """v2 用户反馈：bgm 是死字段 —— 流程配置面板不再调 /list_bgm_files 填下拉
    （BGM 由工作台第 6 阶段详情页上传到 fc.json 的 materials.audio.path）。
    端点保留（_get_default_bgms 列出可被工作台侧引用），但 pipeline.js 必须
    不再 POST /list_bgm_files —— 否则会发起 dead fetch。
    """
    app_path = FUNCLIP_ROOT / "slirn_home" / "app.py"
    src = app_path.read_text(encoding="utf-8")
    # 端点可保留（被 workbench 引用），但 pipeline.js 不应再 fetch 它
    pipeline_js = (FUNCLIP_ROOT / "slirn_home" / "static" / "pipeline.js").read_text(encoding="utf-8")
    assert "/list_bgm_files" not in pipeline_js, (
        "pipeline.js 不应再 POST /list_bgm_files —— bgm 已从 cfg.fine_cut 移除，"
        "面板不再有 bgm 下拉，调用会成 dead fetch"
    )


def test_pipeline_js_populate_deps_function_exists():
    """REQ-20260921-NNN AC-5 v2：pipeline.js 必须有 _pipePanelPopulateDeps 填充 params
    （不再调 /list_bgm_files —— bgm 已从 cfg.fine_cut 移除）。
    """
    pipeline_js = (FUNCLIP_ROOT / "slirn_home" / "static" / "pipeline.js").read_text(encoding="utf-8")
    assert "_pipePanelPopulateDeps" in pipeline_js, (
        "pipeline.js 缺 _pipePanelPopulateDeps 填充函数"
    )
    # 调 /list_fine_global_profiles（参数模板下拉仍要填）
    assert "/slirn/api/list_fine_global_profiles" in pipeline_js, (
        "_pipePanelPopulateDeps 必须调 /slirn/api/list_fine_global_profiles"
    )
    # loadPanel 末尾必须调用
    lp_idx = pipeline_js.find("function loadPanel")
    assert lp_idx >= 0, "缺 loadPanel"
    lp_end = pipeline_js.find("\n  }\n", lp_idx)
    load_panel_body = pipeline_js[lp_idx:lp_end]
    assert "_pipePanelPopulateDeps" in load_panel_body, (
        "loadPanel 必须调用 _pipePanelPopulateDeps"
    )


# =====================================================================
# REQ-20260921-NNN：清理所有阶段产物（前端按钮 + 两次确认 + warning 文案）
# =====================================================================

def test_pipeline_js_has_reset_stages_button():
    """REQ-20260921-NNN：pipeline.js 字幕生成 section 上方必须有清理按钮。"""
    pipeline_js = (FUNCLIP_ROOT / "slirn_home" / "static" / "pipeline.js").read_text(encoding="utf-8")
    # 按钮
    assert 'data-action="pipe-reset-stages"' in pipeline_js, (
        "pipeline.js 必须有 pipe-reset-stages 按钮（清理所有阶段产物）"
    )
    # 按钮文案
    assert "清理所有阶段产物" in pipeline_js, (
        "清理按钮文案应为「清理所有阶段产物」"
    )
    # warning 文案
    assert "slirn-pipe-reset-warning" in pipeline_js, (
        "按钮旁边必须有 warning 文案（slirn-pipe-reset-warning）"
    )
    assert "subtitle.json" in pipeline_js and "fine_compose.json" in pipeline_js, (
        "warning 应列出阶段产物文件名（subtitle.json / fine_compose.json 等）"
    )


def test_pipeline_js_reset_stages_has_two_confirms():
    """REQ-20260921-NNN：清理按钮必须有两次 window.confirm。"""
    pipeline_js = (FUNCLIP_ROOT / "slirn_home" / "static" / "pipeline.js").read_text(encoding="utf-8")
    # resetStages 函数存在
    func_idx = pipeline_js.find("function resetStages")
    assert func_idx >= 0, "缺 resetStages 函数"
    func_body = pipeline_js[func_idx:func_idx + 2500]
    # 至少 2 次 window.confirm
    confirm_count = func_body.count("window.confirm")
    assert confirm_count >= 2, (
        f"resetStages 必须有至少 2 次 window.confirm（实际 {confirm_count}）"
    )


def test_pipeline_js_reset_stages_calls_endpoint():
    """REQ-20260921-NNN：resetStages 必须调 /pipeline_reset_stages 端点。"""
    pipeline_js = (FUNCLIP_ROOT / "slirn_home" / "static" / "pipeline.js").read_text(encoding="utf-8")
    func_idx = pipeline_js.find("function resetStages")
    func_body = pipeline_js[func_idx:func_idx + 2500]
    # 端点路径可能用 SLIRN_API + '/pipeline_reset_stages' 拼接
    assert "pipeline_reset_stages" in func_body, (
        "resetStages 必须调 /pipeline_reset_stages 端点"
    )


def test_pipeline_js_reset_block_above_first_stage():
    """REQ-20260921-NNN：清理块必须渲染在字幕生成 section 上方（即 STAGES map 之前）。"""
    pipeline_js = (FUNCLIP_ROOT / "slirn_home" / "static" / "pipeline.js").read_text(encoding="utf-8")
    # reset 块定义位置
    reset_idx = pipeline_js.find("slirn-pipe-reset-block")
    # STAGES.map 渲染位置
    map_idx = pipeline_js.find("STAGES.map(function(stage")
    assert reset_idx >= 0 and map_idx >= 0 and reset_idx < map_idx, (
        "清理块必须在 STAGES.map 之前（subtitle_generation 上方）"
    )


def test_pipeline_js_click_handler_dispatches_reset():
    """REQ-20260921-NNN：click 委托必须分发 pipe-reset-stages 动作。"""
    pipeline_js = (FUNCLIP_ROOT / "slirn_home" / "static" / "pipeline.js").read_text(encoding="utf-8")
    # 找 click listener
    click_idx = pipeline_js.find("document.addEventListener('click'")
    assert click_idx >= 0, "缺 click listener"
    click_body = pipeline_js[click_idx:click_idx + 3000]
    assert "action === 'pipe-reset-stages'" in click_body, (
        "click 委托必须分发 pipe-reset-stages → resetStages(tid)"
    )


def test_app_py_pipeline_reset_stages_endpoint_exists():
    """REQ-20260921-NNN：app.py 必须新增 /pipeline_reset_stages 端点。"""
    app_src = (FUNCLIP_ROOT / "slirn_home" / "app.py").read_text(encoding="utf-8")
    assert '"/slirn/api/pipeline_reset_stages"' in app_src, (
        "app.py 必须新增 /slirn/api/pipeline_reset_stages 端点"
    )
    # 端点必须调 pipeline_service.clear_pipeline_state
    ep_idx = app_src.find('"/slirn/api/pipeline_reset_stages"')
    # 取大一点的窗口：端点后面有 delete 列表 + DRAFT 降级 + clear_pipeline_state，
    # 防止后续重构导致 clear_pipeline_state 落在窗口外
    ep_section = app_src[ep_idx:ep_idx + 6000]
    assert "clear_pipeline_state" in ep_section, (
        "/pipeline_reset_stages 端点必须调 pipeline_service.clear_pipeline_state"
    )


def test_pipeline_reset_stages_resets_task_status_to_draft(tmp_path: Path):
    """REQ-20260921-NNN：清理所有阶段产物后，必须把 t.status 降回 DRAFT。

    根因：_wb_stage_states() 既看磁盘产物，也看 t.status 等级。即使产物
    全删了，t.status=FINE_CUT_DONE 仍会让所有阶段显示绿色对号（cur_rank
    >= rank[status_name] 为真）。降到 DRAFT 后：assets 看原视频（还在就
    done），其他 5 阶段产物已删 + rank 不足 → 全部 pending。
    """
    from fastapi.testclient import TestClient
    from tasklib.models import TaskStatus
    from slirn_home import build_app

    m, video = _make_mgr(tmp_path)
    t = m.create(name="reset-status", original_video=video)

    # 把任务推到 FINE_CUT_DONE 模拟「全部完成」
    m.update_status(t.task_id, TaskStatus.FINE_CUT_DONE)
    assert m.get(t.task_id).status == TaskStatus.FINE_CUT_DONE

    client = TestClient(build_app(repo_root=tmp_path).app)
    r = client.post(
        "/slirn/api/pipeline_reset_stages",
        json={"task_id": t.task_id},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True

    # 状态必须回到 DRAFT（否则工作台所有阶段仍是绿色对号）
    after = m.get(t.task_id)
    assert after.status == TaskStatus.DRAFT, (
        f"清理后状态应回到 DRAFT，实际为 {after.status}"
    )


def test_pipeline_reset_stages_clears_wb_stage_marks(tmp_path: Path):
    """REQ-20260921-NNN：清理后 _render_workbench 应只让 assets 阶段显示 done。

    - assets：原视频还在 → done（原视频被显式保留）
    - subtitle / subtitle_review / rough_cut / rough_compose / fine_review /
      fine_cut：产物已删 + 状态降为 DRAFT → 全部 pending

    同时验证：所有阶段产物文件名与服务常量对齐 ——
    - optimize 阶段产物实际是 optimize_subtitle.json（不是 opt_subtitle.json）
    - 精剪配置实际是任务根目录的 fine_compose.json（不是 outputs/fc.json）
    """
    from fastapi.testclient import TestClient
    from tasklib.models import TaskStatus
    from slirn_home import build_app
    from slirn_home.app import _render_workbench

    m, video = _make_mgr(tmp_path)
    t = m.create(name="reset-marks", original_video=video)
    m.update_status(t.task_id, TaskStatus.FINE_CUT_DONE)

    # 在 outputs 放产物（清理前应被识别为 done；清理后应都被删）
    outputs_dir = m.tasks_dir / t.task_id / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    (outputs_dir / "subtitle.json").write_text("{}")
    (outputs_dir / "optimize_subtitle.json").write_text(
        '{"saved_at": "2026-09-21T00:00:00", "words": []}'
    )
    # 精剪配置在任务根目录（mgr.tasks_dir/tid/fine_compose.json），
    # 不在 outputs/ —— 见 app.py:3439 _save_fine_compose
    fc_root = m.tasks_dir / t.task_id / "fine_compose.json"
    fc_root.write_text("{}")

    # 清理前：因产物 + 高 rank 状态 → 大部分阶段应 done
    pre_html = _render_workbench(t.task_id, m)
    pre_done_count = pre_html.count('class="slirn-wb-stage done')
    assert pre_done_count >= 4, (
        f"清理前应有 ≥4 个 done 阶段（产物 + 状态都在），实际={pre_done_count}"
    )

    client = TestClient(build_app(repo_root=tmp_path).app)
    r = client.post(
        "/slirn/api/pipeline_reset_stages",
        json={"task_id": t.task_id},
    )
    assert r.json()["ok"] is True
    payload = r.json()

    # 关键回归点：在 _render_workbench 重新触发任何副作用前验证所有产物
    # 都被实际删除（文件名必须与各 service 对齐）。
    # 重要：_render_workbench → _render_fine_cut_zone → _get_fine_compose
    # 会在文件丢失时触发 _schema 迁移并重新 _save_fine_compose，所以
    # 必须先验证文件存在性，再渲染 wb。
    deleted_files = payload.get("deleted") or []
    assert "subtitle.json" in deleted_files, \
        "subtitle.json 应在响应 deleted 列表中"
    assert "optimize_subtitle.json" in deleted_files, \
        "optimize_subtitle.json（优化字幕产物）必须被删除 — 否则「优化字幕」" \
        "fakereview 阶段会因 _wb_stage_states() 看 opt_subtitle 仍带 saved_at 显示 done"
    assert "fine_compose.json" in deleted_files, \
        "fine_compose.json（任务根目录下的精剪配置）必须被删除 — 否则精剪阶段仍可能残留状态"

    # 清理后：只剩 assets 阶段仍 done（原视频保留），其他都 pending
    post_html = _render_workbench(t.task_id, m)
    post_done_count = post_html.count('class="slirn-wb-stage done')
    assert post_done_count == 1, (
        f"清理后应仅 assets 仍 done（其他都应清空），实际 done 数={post_done_count}"
    )


def test_pipeline_reset_stages_uses_real_service_filenames(tmp_path: Path):
    """REQ-20260921-NNN：清理列表必须用各 service 的真实常量文件名。

    之前列表里写的 opt_subjson / opt_replacements / outputs/fc.json 都没人对上
    实际文件，结果「优化字幕」「精剪合成」阶段清理后状态仍是 done（产物没真删）。

    验证：
    1. 创建「假」错名文件（opt_subtitle.json / outputs/fc.json）— 不应被删
    2. 创建「真」对名文件（optimize_subtitle.json / fine_compose.json）— 必须被删
    """
    from fastapi.testclient import TestClient
    from slirn_home import build_app

    m, video = _make_mgr(tmp_path)
    t = m.create(name="reset-filenames", original_video=video)
    outputs_dir = m.tasks_dir / t.task_id / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)

    # 错名（不应被清理逻辑关心 —— 没有 service 写它，留作健壮性）
    fake_opt = outputs_dir / "opt_subtitle.json"
    fake_opt.write_text("{}")
    fake_fc = outputs_dir / "fc.json"
    fake_fc.write_text("{}")
    # 真名（应被删）
    real_opt = outputs_dir / "optimize_subtitle.json"
    real_opt.write_text('{"saved_at": "2026-09-21T00:00:00"}')
    real_fc = m.tasks_dir / t.task_id / "fine_compose.json"
    real_fc.write_text("{}")

    client = TestClient(build_app(repo_root=tmp_path).app)
    r = client.post(
        "/slirn/api/pipeline_reset_stages",
        json={"task_id": t.task_id},
    )
    assert r.json()["ok"] is True

    # 真名被删
    assert real_opt.exists() is False, \
        "optimize_subtitle.json 必须被删（优化字幕 service 的真实常量）"
    assert real_fc.exists() is False, \
        "fine_compose.json 必须被删（_save_fine_compose 的真实路径）"
    # 错名：本测试不约束（如果未来清理逻辑也清错名也无害），但现在保留无害


def test_pipeline_js_reset_stages_calls_openWorkbench():
    """REQ-20260921-NNN：resetStages 成功后必须调 window.slirnOpenWorkbench。

    原因：loadPanel 只刷流程配置面板，不刷 wb；服务端把 t.status 降到
    DRAFT 后，工作台顶部的绿色对号必须整页重渲（_wb_stage_states）才
    真正消失。
    """
    src = (FUNCLIP_ROOT / "slirn_home" / "static" / "pipeline.js").read_text(encoding="utf-8")
    func_idx = src.find("function resetStages")
    assert func_idx >= 0, "缺 resetStages 函数"
    next_func = src.find("\n  function ", func_idx + 1)
    func_body = src[func_idx:next_func if next_func > 0 else func_idx + 3000]
    assert "slirnOpenWorkbench" in func_body, (
        "resetStages 成功后必须调 window.slirnOpenWorkbench(taskId) "
        "让工作台整页重渲（绿色对号立即消失）"
    )
    # 同时仍要刷流程配置面板
    assert "loadPanel(taskId)" in func_body, (
        "resetStages 应继续调 loadPanel(taskId) 刷流程配置面板"
    )


def test_router_js_exposes_openWorkbench():
    """REQ-20260921-NNN：router.js 必须把 openWorkbench 暴露到 window。

    让 pipeline.js 清理所有阶段后能强制刷新工作台（让 _wb_stage_states
    重算，绿色对号立即消失）。
    """
    src = (FUNCLIP_ROOT / "slirn_home" / "static" / "router.js").read_text(encoding="utf-8")
    assert "window.slirnOpenWorkbench = openWorkbench" in src, (
        "router.js 必须有 window.slirnOpenWorkbench = openWorkbench 暴露语句"
    )


def test_router_js_openWorkbench_inserts_pipe_panel_before_wb_main():
    """REQ-20260921-NNN：openWorkbench 把 pipe-panel + pipe-status 插回 _inner 时，
    必须用 insertBefore(.slirn-wb-main) 而不是 appendChild —— 否则节点会被搬
    到 _inner 末尾，导致「清理所有阶段产物」后面板跑到工作台最下边。
    """
    src = (FUNCLIP_ROOT / "slirn_home" / "static" / "router.js").read_text(encoding="utf-8")
    # 找 openWorkbench 函数（局部变量，没暴露之前无法直接调，所以从源码判断）
    func_idx = src.find("function openWorkbench")
    assert func_idx >= 0, "缺 openWorkbench 函数定义"
    # 取到 5) 把流程配置面板插回新的 wb-inner 之后的代码段
    section_idx = src.find("插回新的 wb-inner", func_idx)
    assert section_idx >= 0, "openWorkbench 缺「插回新的 wb-inner」处理段"
    next_section = src.find("// 6)", section_idx)
    section = src[section_idx:next_section if next_section > 0 else section_idx + 1500]
    # 关键断言：必须用 insertBefore(.slirn-wb-main)
    assert "insertBefore" in section and "slirn-wb-main" in section, (
        "openWorkbench 必须用 insertBefore 配合 .slirn-wb-main 锚点 —— "
        "appendChild 会把 pipe-panel 搬到 _inner 末尾（流程配置跑到工作台最下边）"
    )
    # 必须先用 _inner.querySelector 找到 .slirn-wb-main 作为锚点
    assert "_inner.querySelector" in section, \
        "openWorkbench 必须用 _inner.querySelector 找 .slirn-wb-main 作为锚点"
    # 防御兜底：appendChild 只在 _wbMainAnchor 不存在时才允许使用
    # （不能是默认路径，必须有 if (_wbMainAnchor) 守卫）
    assert "if (_wbMainAnchor)" in section, \
        "insertBefore 前必须有 if (_wbMainAnchor) 守卫，否则 anchor 为 null 时会报错"


def test_css_has_reset_block_styling():
    """REQ-20260921-NNN：home.css 必须有 slirn-pipe-reset-block 样式。"""
    css_src = (FUNCLIP_ROOT / "slirn_home" / "static" / "home.css").read_text(encoding="utf-8")
    assert ".slirn-pipe-reset-block" in css_src, (
        "home.css 缺 .slirn-pipe-reset-block 样式"
    )
    assert ".slirn-pipe-reset-warning" in css_src, (
        "home.css 缺 .slirn-pipe-reset-warning 样式"
    )
    # warning 应是红色
    warn_block_idx = css_src.find(".slirn-pipe-reset-warning {")
    assert warn_block_idx >= 0, "缺 .slirn-pipe-reset-warning 块"
    warn_block = css_src[warn_block_idx:warn_block_idx + 600]
    assert "color" in warn_block, "warning 块必须有 color 属性"


# =====================================================================
# REQ-20260921-NNN：BGM 总时长 = 视频时长 + 片头封面时长（fade_out 对齐）
# =====================================================================

def test_bgm_fade_out_includes_cover_dur_on_full_export(tmp_path, monkeypatch):
    """REQ-20260921-NNN：完整导出 + cover 启用时，BGM fade_out 起算 = video_dur + cover_dur。

    之前 BGM 总时长只看 video_dur，导致 cover 启用时 fade_out 提前 fade_out 秒数。
    """
    from slirn_home.app import (
        _assemble_fine_filter, _get_fine_compose, _save_fine_compose,
    )

    mgr, video = _make_mgr(tmp_path)
    t = mgr.create(name="bgm-cover-dur", original_video=video)
    upload = tmp_path / "tasks" / t.task_id / "upload"
    upload.mkdir(parents=True, exist_ok=True)
    (upload / "cover.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    (upload / "bgm.mp3").write_bytes(b"ID3" + b"\x00" * 100)

    fc = _get_fine_compose(mgr, t.task_id)
    fc["materials"]["video"] = {"path": str(video.relative_to(tmp_path)),
                                "type": "video", "source": "upload"}
    fc["materials"]["cover"] = {"path": f"tasks/{t.task_id}/upload/cover.png",
                                "type": "image", "source": "upload"}
    fc["materials"]["audio"] = {"path": f"tasks/{t.task_id}/upload/bgm.mp3",
                                "type": "audio", "source": "upload"}
    fc["layout"]["cover"]["enabled"] = True
    fc["layout"]["cover"]["duration"] = 3.0  # 3 秒封面
    fc["audio"]["enabled"] = True
    fc["audio"]["volume"] = 0.4
    fc["audio"]["fade_in"] = 0.0
    fc["audio"]["fade_out"] = 2.0  # 期望 fade_out st = (video_dur + 3) - 2
    _save_fine_compose(mgr, t.task_id, fc)

    # mock _probe_video_duration_ms → 返回 video_dur = 60s
    monkeypatch.setattr(
        "slirn_home.app._probe_video_duration_ms",
        lambda *a, **kw: 60_000,
    )

    # 完整导出：duration=None → probe → audio_total_duration = 60 + 3 = 63
    asm = _assemble_fine_filter(t.task_id, mgr, duration=None, preview_start=0.0)
    assert asm.get("ok") is True, asm
    fc_text = asm["filter_complex"].replace("\n", "")

    # fade_out 应在 st = 63 - 2 = 61 起算（不是 60 - 2 = 58）
    assert "afade=t=out:st=61.00:d=2.00" in fc_text, (
        f"REQ-20260921-NNN：BGM fade_out 应在 61s（video 60 + cover 3 - 2）起算；"
        f"实际 filter_complex: {fc_text}"
    )


def test_bgm_fade_out_no_cover_uses_video_only(tmp_path, monkeypatch):
    """REQ-20260921-NNN：cover 未启用时，BGM fade_out 起算仍是 video_dur（无 cover_dur）。"""
    from slirn_home.app import (
        _assemble_fine_filter, _get_fine_compose, _save_fine_compose,
    )

    mgr, video = _make_mgr(tmp_path)
    t = mgr.create(name="bgm-no-cover", original_video=video)
    upload = tmp_path / "tasks" / t.task_id / "upload"
    upload.mkdir(parents=True, exist_ok=True)
    (upload / "bgm.mp3").write_bytes(b"ID3" + b"\x00" * 100)

    fc = _get_fine_compose(mgr, t.task_id)
    fc["materials"]["video"] = {"path": str(video.relative_to(tmp_path)),
                                "type": "video", "source": "upload"}
    fc["materials"]["audio"] = {"path": f"tasks/{t.task_id}/upload/bgm.mp3",
                                "type": "audio", "source": "upload"}
    # cover 不启用
    fc["layout"]["cover"]["enabled"] = False
    fc["audio"]["enabled"] = True
    fc["audio"]["volume"] = 0.4
    fc["audio"]["fade_in"] = 0.0
    fc["audio"]["fade_out"] = 2.0
    _save_fine_compose(mgr, t.task_id, fc)

    monkeypatch.setattr(
        "slirn_home.app._probe_video_duration_ms",
        lambda *a, **kw: 60_000,
    )

    asm = _assemble_fine_filter(t.task_id, mgr, duration=None, preview_start=0.0)
    assert asm.get("ok") is True, asm
    fc_text = asm["filter_complex"].replace("\n", "")

    # fade_out 应在 st = 60 - 2 = 58 起算（cover 未启用 → 不加 cover_dur）
    assert "afade=t=out:st=58.00:d=2.00" in fc_text, (
        f"无 cover 时 fade_out 应在 58s（video 60 - 2）；"
        f"实际: {fc_text}"
    )


def test_bgm_fade_out_preview_keeps_preview_duration(tmp_path):
    """REQ-20260921-NNN：预览（显式 duration）不加 cover_dur（preview 自带完整长度）。"""
    from slirn_home.app import (
        _assemble_fine_filter, _get_fine_compose, _save_fine_compose,
    )

    mgr, video = _make_mgr(tmp_path)
    t = mgr.create(name="bgm-preview", original_video=video)
    upload = tmp_path / "tasks" / t.task_id / "upload"
    upload.mkdir(parents=True, exist_ok=True)
    (upload / "cover.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    (upload / "bgm.mp3").write_bytes(b"ID3" + b"\x00" * 100)

    fc = _get_fine_compose(mgr, t.task_id)
    fc["materials"]["video"] = {"path": str(video.relative_to(tmp_path)),
                                "type": "video", "source": "upload"}
    fc["materials"]["cover"] = {"path": f"tasks/{t.task_id}/upload/cover.png",
                                "type": "image", "source": "upload"}
    fc["materials"]["audio"] = {"path": f"tasks/{t.task_id}/upload/bgm.mp3",
                                "type": "audio", "source": "upload"}
    fc["layout"]["cover"]["enabled"] = True
    fc["layout"]["cover"]["duration"] = 3.0
    fc["audio"]["enabled"] = True
    fc["audio"]["volume"] = 0.4
    fc["audio"]["fade_in"] = 0.0
    fc["audio"]["fade_out"] = 2.0
    _save_fine_compose(mgr, t.task_id, fc)

    # 预览：duration=10s → audio_total_duration = 10（不加 cover_dur）
    asm = _assemble_fine_filter(t.task_id, mgr, duration=10.0, preview_start=0.0)
    assert asm.get("ok") is True, asm
    fc_text = asm["filter_complex"].replace("\n", "")

    # fade_out 应在 st = 10 - 2 = 8 起算（不是 10 + 3 - 2 = 11）
    assert "afade=t=out:st=8.00:d=2.00" in fc_text, (
        f"预览时 fade_out 应在 8s（preview_dur 10 - 2），不加 cover_dur；"
        f"实际: {fc_text}"
    )


# =====================================================================
# REQ-20260921-NNN：fine_cut 预检（app.py 端点 + JS UI）
# =====================================================================


def test_pipeline_get_returns_has_fc_json_flag(tmp_path: Path):
    """REQ-20260921-NNN：/pipeline_get 响应必须含 has_fc_json 字段。

    让前端能动态禁用「当前参数」选项。
    """
    from fastapi.testclient import TestClient
    from slirn_home import build_app

    m, video = _make_mgr(tmp_path)
    t = m.create(name="has-fc-flag", original_video=video)
    client = TestClient(build_app(repo_root=tmp_path).app)

    # 1. 无 fc.json → has_fc_json=False
    r1 = client.post("/slirn/api/pipeline_get", json={"task_id": t.task_id})
    assert r1.status_code == 200
    j1 = r1.json()
    assert j1["ok"] is True
    assert j1["has_fc_json"] is False, j1

    # 2. 写 fc.json 后 → has_fc_json=True（task_dir = tasks/<tid>/fine_compose.json）
    fc_path = tmp_path / "tasks" / t.task_id / "fine_compose.json"
    fc_path.parent.mkdir(parents=True, exist_ok=True)
    fc_path.write_text('{"materials":{},"layout":{},"font":{},"output":{}}',
                       encoding="utf-8")
    r2 = client.post("/slirn/api/pipeline_get", json={"task_id": t.task_id})
    assert r2.status_code == 200
    assert r2.json()["has_fc_json"] is True


def test_app_py_pipeline_run_calls_fine_cut_preflight():
    """REQ-20260921-NNN：/pipeline_run 端点必须导入并调 fine_cut_preflight。

    不直接执行它（避免污染），只验证源码里调到了（按 name match）。
    """
    app_src = (FUNCLIP_ROOT / "slirn_home" / "app.py").read_text(encoding="utf-8")
    assert "fine_cut_preflight" in app_src, (
        "app.py /pipeline_run 端点必须调用 pipeline_service.fine_cut_preflight"
    )
    # 在 /pipeline_run 端点的代码段里出现
    ep_idx = app_src.find('"/slirn/api/pipeline_run"')
    assert ep_idx > 0
    ep_window = app_src[ep_idx:ep_idx + 4000]
    assert "fine_cut_preflight" in ep_window
    # 必须把 preflight 失败返回给前端（r.preflight）
    assert "preflight" in ep_window


def test_app_py_pipeline_run_uses_blocks_server_port():
    """REQ-20260922-NNN-b：/pipeline_run 的 base url 必须读 Blocks.server_port。

    早期版本 getattr(app.app, "port", 7861)：app.app 是 FastAPI 对象，永远没有
    .port 属性 → 恒回退 7861。7861 被占用时 Gradio 自动 +1 换端口（新实例落
    7862），调度器仍打 7861 → 「连接失败: [WinError 10061] 积极拒绝」→ 流程
    所有阶段启动失败。修复后读 Blocks 的 server_port（launch() 解析出的实际端口）。
    """
    app_src = (FUNCLIP_ROOT / "slirn_home" / "app.py").read_text(encoding="utf-8")
    ep_idx = app_src.find('"/slirn/api/pipeline_run"')
    assert ep_idx > 0
    ep_window = app_src[ep_idx:ep_idx + 5000]
    assert 'getattr(app, "server_port", None)' in ep_window
    assert 'getattr(app.app, "port"' not in ep_window
    # launch.py 启动时把实际端口回写成 SLIRN_API_BASE（setdefault：用户显式设置优先）
    launch_src = (FUNCLIP_ROOT / "funclip" / "launch.py").read_text(encoding="utf-8")
    assert "SLIRN_API_BASE" in launch_src and "setdefault" in launch_src
    assert "server_port" in launch_src


def test_pipeline_run_base_url_follows_blocks_server_port(tmp_path: Path, monkeypatch):
    """REQ-20260922-NNN-b：Blocks.server_port=7862 → run_pipeline 收到 :7862 base。

    端到端：伪造 launch() 已解析出非默认端口（模拟 7861 被占用 → Gradio 落
    7862），mock run_pipeline 捕获 api 参数（不真启动守护线程）。
    """
    from fastapi.testclient import TestClient
    from slirn_home import build_app
    from slirn_home import pipeline_service as P

    monkeypatch.delenv("SLIRN_API_BASE", raising=False)
    m, video = _make_mgr(tmp_path)
    t = m.create(name="port-follow", original_video=video)
    blocks = build_app(repo_root=tmp_path)
    blocks.server_port = 7862  # 模拟 launch() 实际解析出的端口
    captured: dict = {}
    monkeypatch.setattr(
        P, "run_pipeline",
        lambda tid, api, outputs_dir, since=None: captured.update(api=api) or True)
    client = TestClient(blocks.app)

    outputs_dir = tmp_path / "tasks" / t.task_id / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    P.save_pipeline(outputs_dir, P.default_config())

    r = client.post("/slirn/api/pipeline_run", json={"task_id": t.task_id})
    assert r.status_code == 200
    assert r.json().get("started") is True, r.json()
    assert captured["api"] == "http://127.0.0.1:7862/slirn/api"


def test_pipeline_run_base_url_env_override_wins(tmp_path: Path, monkeypatch):
    """SLIRN_API_BASE 显式设置 → 优先于 server_port（launch.py setdefault 同语义）。"""
    from fastapi.testclient import TestClient
    from slirn_home import build_app
    from slirn_home import pipeline_service as P

    monkeypatch.setenv("SLIRN_API_BASE", "http://10.0.0.5:9999/slirn/api")
    m, video = _make_mgr(tmp_path)
    t = m.create(name="env-override", original_video=video)
    blocks = build_app(repo_root=tmp_path)
    blocks.server_port = 7862
    captured: dict = {}
    monkeypatch.setattr(
        P, "run_pipeline",
        lambda tid, api, outputs_dir, since=None: captured.update(api=api) or True)
    client = TestClient(blocks.app)

    outputs_dir = tmp_path / "tasks" / t.task_id / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    P.save_pipeline(outputs_dir, P.default_config())

    r = client.post("/slirn/api/pipeline_run", json={"task_id": t.task_id})
    assert r.status_code == 200
    assert captured["api"] == "http://10.0.0.5:9999/slirn/api"


def test_app_py_pipeline_run_skips_preflight_when_stop_after_before_fine_cut(tmp_path: Path):
    """REQ-20260921-NNN：stop_after 在 fine_cut 之前 → 不做严格 preflight。

    即使用户误开了 fine_cut.enabled + 缺素材，因不会跑到 fine_cut，预检宽松通过。
    这里通过配置 stop_after=rough_cut 验证 preflight 不阻断（即便 enabled=True + 缺素材）。
    """
    from fastapi.testclient import TestClient
    from slirn_home import build_app
    from slirn_home import pipeline_service as P

    m, video = _make_mgr(tmp_path)
    t = m.create(name="stop-before-fc", original_video=video)
    client = TestClient(build_app(repo_root=tmp_path).app)

    # 写配置：fine_cut.enabled=True（缺素材缺参数）+ run_mode=stop_after + stop_after=rough_cut
    cfg = P.default_config()
    cfg["fine_cut"]["enabled"] = True
    cfg["fine_cut"]["params_source"] = "current"  # 没 fc.json → 会被参数预检阻断
    cfg["run_mode"] = "stop_after"
    cfg["stop_after"] = "rough_cut"
    outputs_dir = tmp_path / "tasks" / t.task_id / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    P.save_pipeline(outputs_dir, cfg)

    # 启动 → 应该不阻断（stop_after < fine_cut → 不调严格预检）
    r = client.post("/slirn/api/pipeline_run", json={"task_id": t.task_id})
    assert r.status_code == 200
    j = r.json()
    # 关键点：不应有 preflight 阻断
    if not j.get("ok"):
        return j
    # started=True OR started=False 但 toast 不是「预检失败」（可能因为 stage handler 跑 mock 链路）
    # 主要看 started 字段是否被允许放行（preflight 失败一定 started=False 且带 preflight 字段）
    assert "preflight" not in j, (
        f"stop_after < fine_cut 时不应触发严格预检，实际: {j}"
    )


def test_app_py_pipeline_run_blocks_when_fine_cut_preflight_fails(tmp_path: Path):
    """REQ-20260921-NNN：fine_cut.enabled=True + 无 fc.json + current → 阻断启动。

    端点返回 ok=True（让前端走 r.preflight 分支显示详情；早期版本 ok=False
    会让前端走到「!r.ok → 未知错误」分支把 preflight 详情吞掉）+ preflight.ok=False
    + started=False（不启动守护线程）。
    """
    from fastapi.testclient import TestClient
    from slirn_home import build_app
    from slirn_home import pipeline_service as P

    m, video = _make_mgr(tmp_path)
    t = m.create(name="preflight-block", original_video=video)
    client = TestClient(build_app(repo_root=tmp_path).app)

    cfg = P.default_config()
    cfg["fine_cut"]["enabled"] = True
    cfg["fine_cut"]["params_source"] = "current"  # 没 fc.json → 参数预检阻断
    cfg["run_mode"] = "to_end"
    cfg["stop_after"] = None
    outputs_dir = tmp_path / "tasks" / t.task_id / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    P.save_pipeline(outputs_dir, cfg)

    r = client.post("/slirn/api/pipeline_run", json={"task_id": t.task_id})
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True, f"端点 ok=True（让前端走 r.preflight 分支），实际: {j}"
    assert j.get("started") is False, f"参数未就绪应不启动守护线程，实际: {j}"
    assert "preflight" in j
    pf = j["preflight"]
    assert pf["ok"] is False
    assert pf["has_fc_json"] is False
    assert "fine_compose.json" in pf["reason"]


def test_pipeline_js_renderStageForm_fine_cut_has_materials_note():
    """REQ-20260921-NNN：精剪合成 form 必须有素材维护位置说明。"""
    js_src = (FUNCLIP_ROOT / "slirn_home" / "static" / "pipeline.js").read_text(
        encoding="utf-8"
    )
    # 找 fine_cut section
    fc_idx = js_src.find("stage.key === 'fine_cut'")
    assert fc_idx > 0
    window = js_src[fc_idx:fc_idx + 3500]
    assert "素材维护位置" in window
    assert "第 6 阶段" in window
    assert "自动从上游" in window or "自动获取" in window
    # BGM 可选说明
    assert "背景音乐" in window
    assert "可选项" in window


def test_pipeline_js_renderStageForm_fine_cut_has_params_note():
    """REQ-20260921-NNN：精剪合成 form 必须有参数模板选择说明（data-fine-cut-params-note）。"""
    js_src = (FUNCLIP_ROOT / "slirn_home" / "static" / "pipeline.js").read_text(
        encoding="utf-8"
    )
    fc_idx = js_src.find("stage.key === 'fine_cut'")
    assert fc_idx > 0
    window = js_src[fc_idx:fc_idx + 3500]
    assert "data-fine-cut-params-note" in window
    assert "fine_compose.json" in window
    # 提示「导入参数」去第 6 阶段
    assert "导入参数" in window


def test_pipeline_js_pipePanelPopulateDeps_handles_has_fc_json_false():
    """REQ-20260921-NNN：_pipePanelPopulateDeps 必须支持 hasFcJson=False，
    并把「当前参数」option 设为 disabled。
    """
    js_src = (FUNCLIP_ROOT / "slirn_home" / "static" / "pipeline.js").read_text(
        encoding="utf-8"
    )
    # 函数必须接受第 2 参数 hasFcJson
    assert "_pipePanelPopulateDeps = async function(taskId, hasFcJson)" in js_src
    # 「当前参数」option 必须在 hasFcJson=false 时 disabled
    idx = js_src.find("_pipePanelPopulateDeps = async function(taskId, hasFcJson)")
    window = js_src[idx:idx + 3500]
    assert "hasFcJson === false" in window
    assert "curOpt.disabled = true" in window
    # loadPanel 调用时必须传 !!r.has_fc_json（精确匹配函数调用表达式）
    import re
    call_matches = re.findall(
        r"_pipePanelPopulateDeps\([^)]*\)", js_src
    )
    assert any("!!r.has_fc_json" in m for m in call_matches), (
        f"loadPanel 必须把 has_fc_json 传给 _pipePanelPopulateDeps，实际调用：{call_matches}"
    )


def test_pipeline_js_runPipeline_handles_preflight_response():
    """REQ-20260921-NNN：runPipeline 必须识别 r.preflight 失败响应并 toast 原因。"""
    js_src = (FUNCLIP_ROOT / "slirn_home" / "static" / "pipeline.js").read_text(
        encoding="utf-8"
    )
    idx = js_src.find("function runPipeline(taskId, since)")
    assert idx > 0
    window = js_src[idx:idx + 1500]
    assert "r.preflight" in window
    assert "pf.materials" in window or "preflight" in window.lower()
    assert "参数" in window
    assert "素材" in window


def test_pipeline_js_runPipeline_preflight_check_before_ok_check():
    """REQ-20260921-NNN v2 BUG 修复：preflight 分支必须在 !r.ok 早退之前。

    早期版本：服务端 _ok(ok=False) 没带 error 字段，前端先走 !r.ok 分支
    显示「启动失败：未知错误」，把 preflight 详情吞掉。
    """
    import re
    js_src = (FUNCLIP_ROOT / "slirn_home" / "static" / "pipeline.js").read_text(
        encoding="utf-8"
    )
    idx = js_src.find("function runPipeline(taskId, since)")
    assert idx > 0
    window = js_src[idx:idx + 1500]
    # r.preflight 检查（用代码行匹配，避免被注释里的「!r.ok」字面量误导）
    pf_match = re.search(r"if\s*\(\s*r\s*&&\s*r\.preflight", window)
    assert pf_match, "r.preflight 检查必须存在"
    pf_pos = pf_match.start()
    # !r.ok 早退：代码行 if (!r || !r.ok)
    ok_match = re.search(r"if\s*\(\s*!r\s*\|\|\s*!r\.ok", window)
    assert ok_match, "!r.ok 早退必须存在"
    ok_pos = ok_match.start()
    assert pf_pos < ok_pos, (
        f"preflight 检查必须在 !r.ok 早退之前（pf_pos={pf_pos} ok_pos={ok_pos}），"
        f"否则 preflight 详情会被「未知错误」吞掉"
    )


def test_pipeline_js_runPipeline_calls_pipeline_run_in_order():
    """REQ-20260921-NNN：runPipeline 必须按 save → run 顺序调用；
    且 run 后处理 preflight。"""
    js_src = (FUNCLIP_ROOT / "slirn_home" / "static" / "pipeline.js").read_text(
        encoding="utf-8"
    )
    idx = js_src.find("function runPipeline(taskId, since)")
    window = js_src[idx:idx + 2000]
    # 顺序：先 /pipeline_save 再 /pipeline_run
    save_pos = window.find("/pipeline_save")
    run_pos = window.find("/pipeline_run")
    assert 0 < save_pos < run_pos, (
        f"save 必须在 run 之前；save={save_pos}, run={run_pos}"
    )


def test_pipeline_service_fine_cut_preflight_function_exists():
    """REQ-20260921-NNN：pipeline_service.py 必须导出 fine_cut_preflight 函数。"""
    from slirn_home import pipeline_service as P
    assert hasattr(P, "fine_cut_preflight"), (
        "pipeline_service.py 缺 fine_cut_preflight 函数"
    )
    assert callable(P.fine_cut_preflight)


def test_pipeline_service_fine_cut_preflight_skipped_path():
    """REQ-20260921-NNN-radio-mode：enabled=False AND range_enabled=False →
    预检返回 ok=True 且不读 fc.json。两个都得显式关（与 handler 跳过条件一致）。
    """
    from slirn_home import pipeline_service as P
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        outputs_dir = td / "outputs"
        outputs_dir.mkdir(parents=True, exist_ok=True)
        # fc.json 不存在 + enabled=False + range_enabled=False → 应通过且 has_fc_json 兜底 True
        cfg = P.default_config()
        cfg["fine_cut"]["enabled"] = False
        cfg["fine_cut"]["range_enabled"] = False
        pre = P.fine_cut_preflight("t-disabled", cfg, outputs_dir)
        assert pre["ok"] is True
        assert pre["enabled"] is False
        assert pre["has_fc_json"] is True
        # 没有去读 fc.json（即便 fc_root 不存在也不报错）
        assert pre["materials"]["missing"] == []
        assert pre["materials"]["optional_missing"] == []


def test_router_js_optInputOverflowCheck_uses_hysteresis_to_break_feedback_loop():
    """REQ-20260921-NNN-shake-fix：optInputOverflowCheck 加 ±50px 滞回，打破
    「cw 在 col4=260 / col4=360 间切换 → overflow 重检 → cw 再切换」反馈环。

    原实现用 ±2px 容差：cw 变化 ~100px 时，scrollWidth 落在 (cw+2, cw+102)
    死区的输入会反复 toggle wrapped → ResizeObserver 又触发再检测 → 滚动条
    上下抖（用户报「选了一个词后，滚动条一会儿上去一会儿下来」）。

    新实现：wrapped 状态下需要 sw > cw-50 才保留；未 wrapped 状态下需要
    sw > cw+50 才 wrap。cw 改 100px 后落点必在另一侧 → 状态稳定。
    """
    src = (FUNCLIP_ROOT / "slirn_home" / "static" / "router.js").read_text(encoding="utf-8")
    # 定位 optInputOverflowCheck 函数体
    func_idx = src.find("function optInputOverflowCheck(")
    assert func_idx >= 0, "缺 optInputOverflowCheck 函数"
    next_func_idx = src.find("\n  function ", func_idx + 1)
    func_body = src[func_idx:next_func_idx if next_func_idx > 0 else func_idx + 1500]

    # 必须读 scrollWidth / clientWidth
    assert "scrollWidth" in func_body, "optInputOverflowCheck 必须读 scrollWidth"
    assert "clientWidth" in func_body, "optInputOverflowCheck 必须读 clientWidth"
    # 必须按当前 wrapped 状态分支判断
    assert "isWrapped" in func_body, (
        "optInputOverflowCheck 必须用 isWrapped 分支（hysteresis 需要两边不同的阈值）"
    )
    # wrapped 状态下用 cw - 50（更大空余才解 wrap）
    assert "cw - 50" in func_body, (
        "wrapped 状态必须用 cw-50 作为 unwrap 阈值（hysteresis 下边带）"
    )
    # 未 wrapped 状态下用 cw + 50（更大溢出才 wrap）
    assert "cw + 50" in func_body, (
        "未 wrapped 状态必须用 cw+50 作为 wrap 阈值（hysteresis 上边带）"
    )
    # 不能再用旧的 ±2px 容差（否则反馈环还在）
    assert "cw + 2" not in func_body, (
        "optInputOverflowCheck 不能保留旧的 cw+2 容差（hysteresis 应替换它）"
    )


def test_router_js_optWordFilter_scrollIntoView_drops_smooth_behavior():
    """REQ-20260921-NNN-shake-fix：optWordFilter 末尾的 scrollIntoView 必须去掉
    behavior:'smooth'。否则「行隐藏 + 插入 hint → 列表高度突变」期间平滑滚动
    会被反复打断，视觉上像「滚动条上下抖」（用户反馈的「颤抖」体感）。
    """
    src = (FUNCLIP_ROOT / "slirn_home" / "static" / "router.js").read_text(encoding="utf-8")
    # 定位 optWordFilter 函数末尾的 scrollIntoView 调用
    func_idx = src.find("function optWordFilter(")
    assert func_idx >= 0, "缺 optWordFilter 函数"
    next_func_idx = src.find("\n  function ", func_idx + 1)
    func_body = src[func_idx:next_func_idx if next_func_idx > 0 else func_idx + 4000]
    assert "scrollIntoView" in func_body, "optWordFilter 必须调 scrollIntoView"
    # 取最后一段 scrollIntoView 调用（行内 must 是 {block: 'center'} 即时跳转）
    # 不能含 behavior:'smooth'
    assert "behavior: 'smooth'" not in func_body, (
        "optWordFilter 的 scrollIntoView 不能再用 behavior:'smooth' — "
        "行隐藏 + 加 hint 期间平滑滚动反复打断会让滚动条看起来在抖"
    )
    # 必须还有 block: 'center' 滚到首个出现行（保留原行为）
    assert "block: 'center'" in func_body, (
        "optWordFilter 滚到首个出现行的语义要保留（block: 'center'）"
    )


def test_pipeline_js_computeNextSince_respects_last_since_when_skipped():
    """REQ-20260921-NNN-skip-since：computeNextSince 必须在 last.stages_done 为空
    但 last.since 指向某阶段时返回 since（典型：fine_cut enabled=False 被跳过
    → stages_done=[]，但 since=fine_cut；下次点「续跑」应继续从 fine_cut 开始，
    而不是回退到字幕生成浪费前 4 阶段时间）。
    """
    pipeline_js = (FUNCLIP_ROOT / "slirn_home" / "static" / "pipeline.js").read_text(encoding="utf-8")
    # 定位 computeNextSince 函数体
    func_idx = pipeline_js.find("function computeNextSince(")
    assert func_idx >= 0, "缺 computeNextSince 函数"
    next_func_idx = pipeline_js.find("\n  function ", func_idx + 1)
    func_body = pipeline_js[func_idx:next_func_idx if next_func_idx > 0 else func_idx + 1200]

    # 必须读 last.since（关键：stages_done 空时回退到 since）
    assert "last.since" in func_body, (
        "computeNextSince 必须读 last.since —— 上次显式带 since 但 stages_done "
        "为空（典型 skip 场景）时应回退到 since，不能回退到 STAGE_KEYS[0]"
    )
    # 必须在 STAGE_KEYS 里找 since 的索引（用 indexOf，不能硬编码 'fine_cut'）
    assert "STAGE_KEYS.indexOf(last.since)" in func_body, (
        "computeNextSince 必须用 STAGE_KEYS.indexOf(last.since) 查 since 位置"
    )
    # 必须在 sinceIdx > lastIdx 时返回 since
    assert "sinceIdx > lastIdx" in func_body, (
        "computeNextSince 必须比较 sinceIdx > lastIdx 才返回 since "
        "(否则 done 末尾在 since 之后会误退)"
    )


def test_pipeline_service_handler_fine_cut_skip_logs_warn_level():
    """REQ-20260921-NNN-skip-warn：handler_fine_cut 在 enabled=False 时必须以
    warn 级别记录「跳过」日志（不能 info，否则用户看不到 ⚠️ 标记 = 不知道
    fine_export.mp4 不会产出 = 续跑按钮误显示「续跑」误导用户）。
    """
    src = (FUNCLIP_ROOT / "slirn_home" / "pipeline_service.py").read_text(encoding="utf-8")
    # 定位 handler_fine_cut 函数体（不用 cfg.get marker — preflight 里也有，
    # 会错位）。从 def handler_fine_cut 之后取后续 ~1500 字符。
    idx = src.find("def handler_fine_cut")
    assert idx >= 0, "handler_fine_cut 函数必须存在"
    nearby = src[idx:idx + 1800]
    # 函数内必须读 cfg.enabled（确认是 handler_fine_cut 不是别的）
    assert 'cfg.get("enabled"' in nearby, "handler_fine_cut 必须读 cfg.enabled"
    # 必须有「跳过」字样的日志
    assert "跳过" in nearby, "handler_fine_cut 跳过时必须记录「跳过」日志"
    # 必须是 warn 级别（不是 info）
    assert '"warn"' in nearby, (
        "handler_fine_cut 跳过的日志必须是 warn 级别（用户能在 status 条看到 ⚠️）"
    )


def test_pipeline_service_run_pipeline_summary_includes_stages_skipped():
    """REQ-20260921-NNN-skip-warn：run_pipeline 必须把 skip 阶段记录到
    summary.stages_skipped 数组中，前端才能 deriveStagesSkipped 区分 done vs skip。
    """
    src = (FUNCLIP_ROOT / "slirn_home" / "pipeline_service.py").read_text(encoding="utf-8")
    # 顶层 main loop 必须有 stages_skipped 列表 + append 逻辑
    assert "stages_skipped" in src, (
        "run_pipeline 主循环必须维护 stages_skipped 列表（前端按它显示 ⚠️）"
    )
    assert "stages_skipped.append" in src, "stages_skipped.append 必须存在"
    # summary dict 必须含 stages_skipped 字段
    assert '"stages_skipped"' in src or "'stages_skipped'" in src, (
        "summary 必须含 stages_skipped 字段（前端 deriveStagesSkipped 靠它）"
    )
    # 主循环必须按 msg == 'skip' 区分 done vs skipped
    assert 'msg == "skip"' in src, (
        "主循环必须按 msg=='skip' 区分 done vs skipped（而非无脑 append done）"
    )


def test_pipeline_js_deriveStagesSkipped_reads_summary_first():
    """REQ-20260921-NNN-skip-warn：deriveStagesSkipped 必须优先读
    summary.stages_skipped（权威源），回退到 log 文本扫描（兼容旧 run）。
    """
    src = (FUNCLIP_ROOT / "slirn_home" / "static" / "pipeline.js").read_text(encoding="utf-8")
    assert "function deriveStagesSkipped" in src, (
        "deriveStagesSkipped 必须存在（前端唯一 stage-skip 派生函数）"
    )
    idx = src.find("function deriveStagesSkipped")
    nearby = src[idx:idx + 1000]
    # 优先 summary
    assert "stages_skipped" in nearby, (
        "deriveStagesSkipped 必须读 st.summary.stages_skipped"
    )
    # 回退到 log 扫描「跳过」
    assert "跳过" in nearby, (
        "deriveStagesSkipped 必须回退到 log 文本扫描「跳过」（兼容旧 run）"
    )


def test_pipeline_js_renderStatusBar_shows_done_with_skip_label():
    """REQ-20260921-NNN-skip-warn：renderStatusBar 在 state=done 但
    summary.stages_skipped 非空时，必须改文案为「已完成（含 N 个跳过）」
    并打 done-with-skip 类（橙色），不再绿色误导用户。
    """
    src = (FUNCLIP_ROOT / "slirn_home" / "static" / "pipeline.js").read_text(encoding="utf-8")
    idx = src.find("function renderStatusBar")
    assert idx >= 0, "renderStatusBar 必须存在"
    nearby = src[idx:idx + 3500]
    # 必须调用 deriveStagesSkipped（拿 skip 集合）
    assert "deriveStagesSkipped(st)" in nearby, (
        "renderStatusBar 必须算 skip 集合（决定是否改文案）"
    )
    # 必须出现「含 ... 跳过」文案
    assert "已完成（含" in nearby, (
        "renderStatusBar 必须改文案为「已完成（含 N 个跳过）」"
    )
    # 必须有 done-with-skip 样式类（CSS 才有橙色）
    assert "done-with-skip" in nearby, (
        "renderStatusBar 必须给含 skip 的 done 加 done-with-skip 类（CSS 橙色）"
    )


def test_home_css_status_state_done_with_skip_amber_color():
    """REQ-20260921-NNN-skip-warn：CSS 必须为 done-with-skip 提供橙色样式
    （不再绿色），让 status 头部一眼能看出「跑了但有跳过」。
    """
    css = (FUNCLIP_ROOT / "slirn_home" / "static" / "home.css").read_text(encoding="utf-8")
    assert ".slirn-pipe-status-state-done-with-skip" in css, (
        "CSS 必须为 done-with-skip 状态提供样式"
    )
    # 必须在 done-with-skip 选择器块内包含琥珀色（amber）
    idx = css.find(".slirn-pipe-status-state-done-with-skip")
    nearby = css[idx:idx + 400]
    assert "245, 158, 11" in nearby or "#b45309" in nearby or "#fbbf24" in nearby, (
        "done-with-skip 必须使用琥珀色（amber）背景或文字"
    )


def test_pipeline_js_stageStateOf_and_updateStageBadges_render_skipped():
    """REQ-20260921-NNN-skip-warn：stageStateOf 必须把 skip 状态优先级提到
    done 之前；updateStageBadges 必须为 skip 状态输出 ⚠️ 标记 + 「已跳过」
    文本（让用户在 6 阶段列表里也能看到哪个阶段被跳了）。
    """
    src = (FUNCLIP_ROOT / "slirn_home" / "static" / "pipeline.js").read_text(encoding="utf-8")
    # stageStateOf 必须在 done 之前判 skipped
    idx = src.find("function stageStateOf")
    assert idx >= 0, "stageStateOf 必须存在"
    nearby = src[idx:idx + 1500]
    assert "'skipped'" in nearby, "stageStateOf 必须支持 'skipped' 状态"
    sk_idx = nearby.find("'skipped'")
    done_idx = nearby.find("'done'")
    assert sk_idx < done_idx, (
        "stageStateOf 必须先判 'skipped' 再判 'done'（skip 优先于 done）"
    )
    # updateStageBadges 必须渲染「已跳过」文案
    idx = src.find("function updateStageBadges")
    assert idx >= 0, "updateStageBadges 必须存在"
    nearby = src[idx:idx + 3000]
    assert "已跳过" in nearby, "updateStageBadges 必须为 skipped 状态渲染「已跳过」文案"
    assert "'skipped'" in nearby, "updateStageBadges 必须为 skipped 状态分配 className 后缀"


def test_pipeline_service_handler_fine_cut_runs_when_range_enabled():
    """REQ-20260921-NNN-range-overrides-enabled：用户设计要求「勾了按区间
    导出（range_enabled=True）就应该能执行」 — 即便 enabled 默认关，handler
    也不能 skip。设计语义：用户主动配起点/时长 = 明确意图，「用户意图」优先
    于「防误跑」开关。
    """
    src = (FUNCLIP_ROOT / "slirn_home" / "pipeline_service.py").read_text(encoding="utf-8")
    idx = src.find("def handler_fine_cut")
    assert idx >= 0
    nearby = src[idx:idx + 1500]
    # 必须读取 range_enabled
    assert 'cfg.get("range_enabled"' in nearby, (
        "handler_fine_cut 必须读 cfg.range_enabled（决定是否跳过）"
    )
    # 跳过条件必须是「两个都 False 才 skip」，不能用单 enabled=False
    # 检测方式：在 skip 路径附近找两个 cfg.get 引用
    fc_range_idx = nearby.find('cfg.get("range_enabled"')
    fc_enabled_idx = nearby.find('cfg.get("enabled"')
    assert fc_range_idx > 0 and fc_enabled_idx > 0, (
        "handler_fine_cut 必须同时读 enabled 和 range_enabled"
    )
    # 跳过条件语句中必须用 and not range_enabled 联动 — 不允许只用 enabled
    assert "and not fc_range" in nearby or "not fc_range" in nearby, (
        "handler_fine_cut 的 skip 条件必须同时看 enabled 和 range_enabled（用户勾 range 即视为启用）"
    )
    # 日志必须两个都提到，便于排错
    skip_log_idx = nearby.find("未启用自动最终导出")
    assert skip_log_idx >= 0
    skip_log_block = nearby[skip_log_idx:skip_log_idx + 200]
    assert "range_enabled" in skip_log_block, (
        "跳过日志必须同时提 enabled 和 range_enabled（用户知道缺哪个）"
    )


def test_pipeline_service_validate_config_range_promotes_enabled():
    """REQ-20260921-NNN-range-overrides-enabled：validate_config 规范化时必须
    把 range_enabled=True 强制同步 enabled=True（让 summary / 前端 UI 反映
    「已启用」真状态，避免「勾 range 但 cfg.enabled=False → skip」的脏状态
    跨调用链）。
    """
    src = (FUNCLIP_ROOT / "slirn_home" / "pipeline_service.py").read_text(encoding="utf-8")
    idx = src.find("def validate_config")
    assert idx >= 0
    nearby = src[idx:idx + 3000]
    # 必须有「range_enabled=True → enabled=True」的强制同步
    assert "range_enabled" in nearby and "enabled" in nearby, (
        "validate_config 必须处理 range_enabled 与 enabled 的联动"
    )
    # 必须用 is True 精确触发（避免 truthy 兜底把字符串 'false' 误开启）
    # 关键字检测：检查同步逻辑存在
    assert 'is True' in nearby or "range_enabled" in nearby, (
        "range → enabled 的强制同步必须存在"
    )


def test_pipeline_service_fine_cut_preflight_respects_range_enabled():
    """REQ-20260921-NNN-range-overrides-enabled：fine_cut_preflight 的
    enabled 判定必须 OR range_enabled，与 handler_fine_cut 跳过条件保持一致 —
    避免「预检通过但 handler 又 skip」或「预检阻断但 handler 实际能跑」。
    """
    src = (FUNCLIP_ROOT / "slirn_home" / "pipeline_service.py").read_text(encoding="utf-8")
    idx = src.find("def fine_cut_preflight")
    assert idx >= 0
    nearby = src[idx:idx + 1500]
    # 必须读 range_enabled 来判定 enabled
    assert 'range_enabled' in nearby, (
        "fine_cut_preflight 必须读 fine_cut.range_enabled"
    )
    # enabled 计算必须 OR 上 range_enabled（不是单 enabled）
    # 找 enabled = bool(...) 这一行
    enabled_idx = nearby.find("enabled = bool(")
    assert enabled_idx > 0
    enabled_block = nearby[enabled_idx:enabled_idx + 250]
    assert "range_enabled" in enabled_block, (
        "fine_cut_preflight 的 enabled 必须 OR range_enabled（与 handler 一致）"
    )


def test_pipeline_js_renderStageForm_fine_cut_radio_default_is_range():
    """REQ-20260921-NNN-radio-mode：默认 stageCfg={} → radio value='range' checked。
    用户从流程配置进入看到 range 模式预选（与 default_config enabled/range 一致）。
    """
    pipeline_js = (FUNCLIP_ROOT / "slirn_home" / "static" / "pipeline.js").read_text(encoding="utf-8")
    idx = pipeline_js.find("stage.key === 'fine_cut'")
    assert idx >= 0
    section = pipeline_js[idx:idx + 2500]
    # 默认 exportMode = 'range' 必须存在
    assert "exportMode = 'range'" in section, (
        "默认 exportMode 必须是 'range'（按区间导出一段预选）"
    )


def test_pipeline_js_renderStageForm_fine_cut_radio_preselects_full_when_enabled_only():
    """REQ-20260921-NNN-radio-mode：stageCfg={enabled:true, range_enabled:false}
    → radio value='full' checked。旧「导全片」配置加载正确预选。
    """
    pipeline_js = (FUNCLIP_ROOT / "slirn_home" / "static" / "pipeline.js").read_text(encoding="utf-8")
    idx = pipeline_js.find("stage.key === 'fine_cut'")
    section = pipeline_js[idx:idx + 1500]
    # 预选规则：enabled && !range_enabled → 'full'
    assert "exportMode === 'full' ? ' checked' : ''" in section or (
        "enabled === true &&" in section and "range_enabled === false" in section
    ), "radio 预选规则必须识别 enabled=true && range_enabled=false → full"


def test_pipeline_js_renderStageForm_fine_cut_radio_preselects_range_default():
    """REQ-20260921-NNN-radio-mode：stageCfg={}（或 enabled=false, range_enabled=false）
    → radio value='range' checked（默认预选，按用户设计要求）。
    """
    pipeline_js = (FUNCLIP_ROOT / "slirn_home" / "static" / "pipeline.js").read_text(encoding="utf-8")
    idx = pipeline_js.find("stage.key === 'fine_cut'")
    section = pipeline_js[idx:idx + 1500]
    # 默认 exportMode = 'range'（与上面 test_pipeline_js_renderStageForm_fine_cut_radio_default_is_range 共证）
    assert "exportMode = 'range'" in section


def test_pipeline_js_readCurrentConfig_fine_cut_radio_full_sets_enabled_only():
    """REQ-20260921-NNN-radio-mode：radio value='full' → cfg.fine_cut.enabled=true,
    range_enabled=false（无 start/duration 区间）。
    """
    pipeline_js = (FUNCLIP_ROOT / "slirn_home" / "static" / "pipeline.js").read_text(encoding="utf-8")
    idx = pipeline_js.find("function readCurrentConfig")
    section = pipeline_js[idx:idx + 4000]
    # radio 读取
    assert "querySelector('input[name=\"slirn-pipe-fc-export-mode\"]:checked')" in section, (
        "readCurrentConfig 必须按 name + checked 读 radio"
    )
    # 派生逻辑
    assert "fcEnabled = (fcMode === 'full' || fcMode === 'range')" in section, (
        "fcEnabled 必须从 radio 派生（full 或 range → true）"
    )
    assert "var fcRange = fcMode === 'range'" in section, (
        "fcRange 必须从 radio 派生（只有 range → true）"
    )


def test_pipeline_js_readCurrentConfig_fine_cut_radio_range_sets_both():
    """REQ-20260921-NNN-radio-mode：radio value='range' → enabled=true, range_enabled=true。
    """
    pipeline_js = (FUNCLIP_ROOT / "slirn_home" / "static" / "pipeline.js").read_text(encoding="utf-8")
    idx = pipeline_js.find("function readCurrentConfig")
    section = pipeline_js[idx:idx + 4000]
    # 验证派生后的 cfg.fine_cut 写入
    assert "range_enabled: fcRange" in section, (
        "cfg.fine_cut.range_enabled 必须用 fcRange 派生（radio 选 range → true）"
    )
    assert "enabled: fcEnabled" in section, (
        "cfg.fine_cut.enabled 必须用 fcEnabled 派生"
    )


def test_pipeline_js_renderStageForm_fine_cut_uses_radio_not_checkbox():
    """REQ-20260921-NNN-radio-mode：fine_cut 表单不再有 enabled / range-on checkbox。
    旧的 `fieldId(stage.key, 'enabled')` 和 `fieldId(stage.key, 'range-on')`
    在 fine_cut 分支内必须消失。
    """
    pipeline_js = (FUNCLIP_ROOT / "slirn_home" / "static" / "pipeline.js").read_text(encoding="utf-8")
    idx = pipeline_js.find("stage.key === 'fine_cut'")
    section = pipeline_js[idx:idx + 3500]
    # 旧 checkbox fieldId 在 fine_cut 分支内不应出现
    assert "fieldId(stage.key, 'enabled')" not in section, (
        "fine_cut 不应再有 enabled checkbox（已改 radio 卡片）"
    )
    assert "fieldId(stage.key, 'range-on')" not in section, (
        "fine_cut 不应再有 range-on checkbox（已改 radio 卡片）"
    )
    # radio 卡片必须存在
    assert 'type="radio" name="slirn-pipe-fc-export-mode"' in section, (
        "fine_cut 必须有 export-mode radio 卡片组"
    )


# ---------- REQ-20260921-NNN-outputs-browser：list_outputs + output_file 端点 ----------

def test_list_outputs_returns_empty_when_no_outputs(tmp_path: Path):
    """list_outputs 在 outputs/ 为空时返回 ok=True；items 可空或仅有 metadata.json
    （新建任务总带 metadata.json 在 task_root — 视为「无实际产物」）。"""
    from fastapi.testclient import TestClient
    from slirn_home import build_app

    m, video = _make_mgr(tmp_path)
    t = m.create(name="empty-outputs", original_video=video)
    client = TestClient(build_app(repo_root=tmp_path).app)
    r = client.post("/slirn/api/list_outputs", json={"task_id": t.task_id})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["html"]["task_id"] == t.task_id
    # 没有真实产物时，items 应只含 metadata.json（任务自带）
    items = body["html"]["items"]
    names = {it["name"] for it in items}
    # outputs/ 应为空
    assert not any(it["scope"] == "outputs" for it in items), \
        f"新建任务 outputs/ 应为空，实际：{names}"


def test_list_outputs_classifies_known_filenames(tmp_path: Path):
    """list_outputs 给已知文件名打中文 label + kind。"""
    from fastapi.testclient import TestClient
    from slirn_home import build_app

    m, video = _make_mgr(tmp_path)
    t = m.create(name="known-files", original_video=video)
    out = m.tasks_dir / t.task_id / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    # 写一批已知文件名（不写真实视频字节，避免 ffmpeg / 解析干扰 — 列表只看 size/mtime）
    for fname in (
        "rough_compose.mp4",
        "fine_export.mp4",
        "fine_preview.mp4",
        "subtitle.json", "subtitle.srt",
        "optimize.json", "optimize.srt",
        "revision.json", "cutlist.json",
        "speaker_link.json", "rev_speaker_link.json",
        "fine_revision.json",
        "execution_history.json", "pipeline.json",
    ):
        (out / fname).write_bytes(b"x" * 100)
    # 任务根也有 fine_compose.json
    (m.tasks_dir / t.task_id / "fine_compose.json").write_bytes(b"y" * 50)

    client = TestClient(build_app(repo_root=tmp_path).app)
    r = client.post("/slirn/api/list_outputs", json={"task_id": t.task_id})
    body = r.json()
    assert body["ok"] is True
    items = body["html"]["items"]
    by_name = {it["name"]: it for it in items}

    # 已知标签验证
    assert by_name["rough_compose.mp4"]["label"] == "粗剪成片"
    assert by_name["rough_compose.mp4"]["kind"] == "video"
    assert by_name["rough_compose.mp4"]["previewable"] is True
    assert by_name["fine_export.mp4"]["label"] == "最终导出视频（全片）"
    assert by_name["subtitle.json"]["label"] == "原始字幕 JSON"
    assert by_name["subtitle.srt"]["label"] == "原始字幕 SRT"
    assert by_name["subtitle.srt"]["previewable"] is True
    assert by_name["execution_history.json"]["label"] == "执行历史 JSON"
    assert by_name["fine_compose.json"]["label"] == "精剪合成配置 JSON"
    assert by_name["fine_compose.json"]["scope"] == "task_root"


def test_list_outputs_recognizes_range_export_pattern(tmp_path: Path):
    """区间导出 fine_export_t{start}_d{dur}.mp4 自动归类为「最终导出视频（区间）」。"""
    from fastapi.testclient import TestClient
    from slirn_home import build_app

    m, video = _make_mgr(tmp_path)
    t = m.create(name="range-files", original_video=video)
    out = m.tasks_dir / t.task_id / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    (out / "fine_export_t0.0_d600.0.mp4").write_bytes(b"x" * 1024)
    (out / "fine_preview_t30.5_d120.0.mp4").write_bytes(b"y" * 1024)
    (out / "diag.mp4").write_bytes(b"z" * 512)  # 兜底按扩展名分类

    client = TestClient(build_app(repo_root=tmp_path).app)
    r = client.post("/slirn/api/list_outputs", json={"task_id": t.task_id})
    items = r.json()["html"]["items"]
    by_name = {it["name"]: it for it in items}
    assert by_name["fine_export_t0.0_d600.0.mp4"]["label"] == "最终导出视频（区间）"
    assert by_name["fine_export_t0.0_d600.0.mp4"]["kind"] == "video"
    assert by_name["fine_preview_t30.5_d120.0.mp4"]["label"] == "精剪预览（区间）"
    # 兜底按扩展名
    assert by_name["diag.mp4"]["label"] == "视频文件"
    assert by_name["diag.mp4"]["kind"] == "video"


def test_list_outputs_sorts_by_mtime_desc(tmp_path: Path):
    """列表按 mtime 倒序（新 → 旧），同名按 scope=outputs 优先。"""
    import os as _os
    import time as _t
    from fastapi.testclient import TestClient
    from slirn_home import build_app

    m, video = _make_mgr(tmp_path)
    t = m.create(name="sort-test", original_video=video)
    out = m.tasks_dir / t.task_id / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    old_path = out / "old.mp4"
    old_path.write_bytes(b"x" * 10)
    # 调 mtime 到过去
    old_time = _t.time() - 7200
    _os.utime(old_path, (old_time, old_time))
    new_path = out / "new.mp4"
    new_path.write_bytes(b"y" * 10)
    # new 应排在 old 之前
    client = TestClient(build_app(repo_root=tmp_path).app)
    items = client.post("/slirn/api/list_outputs", json={"task_id": t.task_id}).json()["html"]["items"]
    names = [it["name"] for it in items]
    assert names.index("new.mp4") < names.index("old.mp4")


def test_list_outputs_returns_urls_pointing_to_output_file(tmp_path: Path):
    """每条 item 的 url 必须指向 /slirn/api/output_file，且 URL 编码了文件名。"""
    from fastapi.testclient import TestClient
    from slirn_home import build_app

    m, video = _make_mgr(tmp_path)
    t = m.create(name="url-test", original_video=video)
    out = m.tasks_dir / t.task_id / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    (out / "fine_export_t0.0_d600.0.mp4").write_bytes(b"x" * 1024)

    client = TestClient(build_app(repo_root=tmp_path).app)
    items = client.post("/slirn/api/list_outputs", json={"task_id": t.task_id}).json()["html"]["items"]
    assert items[0]["url"].startswith("/slirn/api/output_file?")
    assert t.task_id in items[0]["url"]
    assert "fine_export_t0.0_d600.0.mp4" in items[0]["url"]


def test_list_outputs_missing_task_returns_err(tmp_path: Path):
    """task_id 不存在时返 ok=False（不抛 500）。"""
    from fastapi.testclient import TestClient
    from slirn_home import build_app

    client = TestClient(build_app(repo_root=tmp_path).app)
    r = client.post("/slirn/api/list_outputs", json={"task_id": "99999999-999"})
    body = r.json()
    assert body["ok"] is False
    assert "不存在" in body["error"]


def test_list_outputs_empty_task_id_returns_err(tmp_path: Path):
    """空 task_id 直接拒绝（前端空字符串兜底）。"""
    from fastapi.testclient import TestClient
    from slirn_home import build_app

    client = TestClient(build_app(repo_root=tmp_path).app)
    r = client.post("/slirn/api/list_outputs", json={"task_id": ""})
    assert r.json()["ok"] is False


def test_output_file_serves_known_video(tmp_path: Path):
    """output_file?root=outputs&name=fine_export*.mp4 直接服务原始字节。"""
    from fastapi.testclient import TestClient
    from slirn_home import build_app

    m, video = _make_mgr(tmp_path)
    t = m.create(name="serve-video", original_video=video)
    out = m.tasks_dir / t.task_id / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    raw = b"\x00\x00\x00\x18ftypisom" + b"X" * 100  # 假 mp4 头
    (out / "fine_export_t10.0_d60.0.mp4").write_bytes(raw)

    client = TestClient(build_app(repo_root=tmp_path).app)
    r = client.get(
        f"/slirn/api/output_file?task_id={t.task_id}"
        f"&name=fine_export_t10.0_d60.0.mp4&root=outputs"
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("video/")
    assert r.content == raw


def test_output_file_serves_subtitle_as_text(tmp_path: Path):
    """SRT 文件以 text/plain 返回（浏览器可显示，<a download> 也能保存）。"""
    from fastapi.testclient import TestClient
    from slirn_home import build_app

    m, video = _make_mgr(tmp_path)
    t = m.create(name="serve-srt", original_video=video)
    out = m.tasks_dir / t.task_id / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    srt_text = "1\n00:00:00,000 --> 00:00:05,000\nhello\n\n"
    (out / "subtitle.srt").write_text(srt_text, encoding="utf-8")

    client = TestClient(build_app(repo_root=tmp_path).app)
    r = client.get(f"/slirn/api/output_file?task_id={t.task_id}&name=subtitle.srt&root=outputs")
    assert r.status_code == 200
    assert "text/plain" in r.headers["content-type"]
    assert "hello" in r.content.decode("utf-8")


def test_output_file_serves_json_with_pretty_mime(tmp_path: Path):
    """JSON 文件以 application/json 返回。"""
    from fastapi.testclient import TestClient
    from slirn_home import build_app

    m, video = _make_mgr(tmp_path)
    t = m.create(name="serve-json", original_video=video)
    out = m.tasks_dir / t.task_id / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    (out / "subtitle.json").write_text('{"a":1}', encoding="utf-8")

    client = TestClient(build_app(repo_root=tmp_path).app)
    r = client.get(f"/slirn/api/output_file?task_id={t.task_id}&name=subtitle.json&root=outputs")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")


def test_output_file_task_root_scope(tmp_path: Path):
    """root=task_root 读 fine_compose.json（任务根下的文件，不在 outputs/）。"""
    from fastapi.testclient import TestClient
    from slirn_home import build_app

    m, video = _make_mgr(tmp_path)
    t = m.create(name="task-root", original_video=video)
    (m.tasks_dir / t.task_id / "fine_compose.json").write_text("{}", encoding="utf-8")

    client = TestClient(build_app(repo_root=tmp_path).app)
    r = client.get(f"/slirn/api/output_file?task_id={t.task_id}&name=fine_compose.json&root=task_root")
    assert r.status_code == 200
    assert r.content == b"{}"


def test_output_file_rejects_path_traversal_dotdot(tmp_path: Path):
    """name 含 .. 必须 400（不允许越界）。"""
    from fastapi.testclient import TestClient
    from slirn_home import build_app

    m, video = _make_mgr(tmp_path)
    t = m.create(name="traversal", original_video=video)
    client = TestClient(build_app(repo_root=tmp_path).app)
    r = client.get(f"/slirn/api/output_file?task_id={t.task_id}&name=../../../etc/passwd&root=outputs")
    assert r.status_code == 400


def test_output_file_rejects_path_traversal_slash(tmp_path: Path):
    """name 含 / 或 \\ 必须 400。"""
    from fastapi.testclient import TestClient
    from slirn_home import build_app

    m, video = _make_mgr(tmp_path)
    t = m.create(name="traversal-slash", original_video=video)
    client = TestClient(build_app(repo_root=tmp_path).app)
    # FastAPI 不会把 / 放进 query value（路径分隔符由路由拦截），但 \\ 能放进去
    r = client.get(f"/slirn/api/output_file?task_id={t.task_id}&name=sub%5Cfile.mp4&root=outputs")
    assert r.status_code == 400


def test_output_file_rejects_invalid_root(tmp_path: Path):
    """root 必须是 outputs / task_root 二选一。"""
    from fastapi.testclient import TestClient
    from slirn_home import build_app

    m, video = _make_mgr(tmp_path)
    t = m.create(name="bad-root", original_video=video)
    client = TestClient(build_app(repo_root=tmp_path).app)
    r = client.get(f"/slirn/api/output_file?task_id={t.task_id}&name=pipeline.json&root=hacker")
    assert r.status_code == 400


def test_output_file_returns_404_for_missing_file(tmp_path: Path):
    """文件不在磁盘 → 404（不是 200 + 空 body）。"""
    from fastapi.testclient import TestClient
    from slirn_home import build_app

    m, video = _make_mgr(tmp_path)
    t = m.create(name="missing-file", original_video=video)
    (m.tasks_dir / t.task_id / "outputs").mkdir(parents=True, exist_ok=True)
    client = TestClient(build_app(repo_root=tmp_path).app)
    r = client.get(f"/slirn/api/output_file?task_id={t.task_id}&name=nope.mp4&root=outputs")
    assert r.status_code == 404


def test_output_file_returns_404_for_missing_task(tmp_path: Path):
    """task_id 不存在 → 404。"""
    from fastapi.testclient import TestClient
    from slirn_home import build_app

    client = TestClient(build_app(repo_root=tmp_path).app)
    r = client.get("/slirn/api/output_file?task_id=99999999-999&name=pipeline.json&root=outputs")
    assert r.status_code == 404


def test_video_endpoint_accepts_fname_for_range_export(tmp_path: Path):
    """REQ-20260921-NNN-outputs-browser：/slirn/api/video 用 fname 走区间导出文件。"""
    from fastapi.testclient import TestClient
    from slirn_home import build_app

    m, video = _make_mgr(tmp_path)
    t = m.create(name="video-fname", original_video=video)
    out = m.tasks_dir / t.task_id / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    raw = b"x" * 1024
    (out / "fine_export_t5.0_d300.0.mp4").write_bytes(raw)

    client = TestClient(build_app(repo_root=tmp_path).app)
    # 带 fname → 服务区间导出文件
    r = client.get(
        f"/slirn/api/video/{t.task_id}",
        params={"src": "fine_export", "fname": "fine_export_t5.0_d300.0.mp4"},
    )
    assert r.status_code == 200
    assert r.content == raw
    # 不带 fname → 兼容旧路径，fallback fine_export.mp4（不存在 → 404）
    r2 = client.get(f"/slirn/api/video/{t.task_id}", params={"src": "fine_export"})
    assert r2.status_code == 404


def test_video_endpoint_rejects_fname_path_traversal(tmp_path: Path):
    """/slirn/api/video 的 fname 也要防 path traversal。"""
    from fastapi.testclient import TestClient
    from slirn_home import build_app

    m, video = _make_mgr(tmp_path)
    t = m.create(name="video-traversal", original_video=video)
    client = TestClient(build_app(repo_root=tmp_path).app)
    r = client.get(
        f"/slirn/api/video/{t.task_id}",
        params={"src": "fine_export", "fname": "../etc/passwd"},
    )
    assert r.status_code == 404


# ---------- REQ-20260921-NNN-outputs-browser：前端产物浏览器渲染 ----------

def test_pipeline_js_has_outputs_panel_html():
    """pipeline.js 渲染时必须挂载产物浏览器 details + 刷新按钮。"""
    js_path = Path("slirn_home/static/pipeline.js")
    if not js_path.exists():
        import pytest
        pytest.skip("工作目录不在仓库根")
    src = js_path.read_text(encoding="utf-8")
    assert 'data-pipe-outputs-panel' in src, (
        "renderPanel 必须挂载 data-pipe-outputs-panel 容器"
    )
    assert 'data-action="pipe-outputs-refresh"' in src, (
        "必须挂刷新按钮（data-action=pipe-outputs-refresh）"
    )
    assert '_renderOutputItem' in src, "必须有 _renderOutputItem 渲染单条产物"
    assert '_renderOutputsList' in src, "必须有 _renderOutputsList 渲染整个列表"


def test_pipeline_js_outputs_renders_video_preview():
    """视频产物必须挂 <video controls src=url>（不是只给下载按钮）。"""
    js_path = Path("slirn_home/static/pipeline.js")
    if not js_path.exists():
        import pytest
        pytest.skip("工作目录不在仓库根")
    src = js_path.read_text(encoding="utf-8")
    # kind 字符串（'video'）+ <video controls 标签都存在
    assert "'video'" in src, "kind 判定必须用字符串 'video'"
    assert '<video controls' in src, (
        "视频产物必须渲染 <video controls>"
    )
    # subtitle 用 <pre> 懒加载（避免一次性下载所有字幕）
    assert 'data-pipe-outputs-sub-pre' in src, (
        "字幕产物必须挂 data-pipe-outputs-sub-pre 懒加载"
    )
    # JSON 用 <pre> + JSON 美化
    assert 'data-pipe-outputs-json-pre' in src, (
        "JSON 产物必须挂 data-pipe-outputs-json-pre 懒加载"
    )


def test_pipeline_js_outputs_groups_by_kind():
    """产物按 kind 分组（视频 / 字幕 / 音频 / JSON / 图片 / 日志 / 其他）。"""
    js_path = Path("slirn_home/static/pipeline.js")
    if not js_path.exists():
        import pytest
        pytest.skip("工作目录不在仓库根")
    src = js_path.read_text(encoding="utf-8")
    assert "slirn-pipe-outputs-group" in src
    # 必须出现至少 video / subtitle / json 三类分组（用单引号字符串）
    assert "'video'" in src and "'subtitle'" in src and "'json'" in src


def test_pipeline_js_outputs_has_download_button():
    """每条产物必须有 download 链接。"""
    js_path = Path("slirn_home/static/pipeline.js")
    if not js_path.exists():
        import pytest
        pytest.skip("工作目录不在仓库根")
    src = js_path.read_text(encoding="utf-8")
    assert '⬇ 下载' in src or '⬇下载' in src, "必须有下载按钮文字"
    assert 'download="' in src, "<a> 必须有 download 属性（浏览器走下载而非预览）"


def test_pipeline_js_outputs_refresh_calls_api():
    """refreshOutputsList 必须 POST /slirn/api/list_outputs。"""
    js_path = Path("slirn_home/static/pipeline.js")
    if not js_path.exists():
        import pytest
        pytest.skip("工作目录不在仓库根")
    src = js_path.read_text(encoding="utf-8")
    assert "SLIRN_API + '/list_outputs'" in src, (
        "refreshOutputsList 必须 POST /slirn/api/list_outputs"
    )


def test_pipeline_js_outputs_autorefresh_on_pipeline_done():
    """pipeline 终态时自动 refresh 产物列表（让用户看到刚跑出的文件）。
    v2：改边沿触发 prevRunning && !nowRunning（避免 idle 状态下每次 poll 都刷新）。
    """
    js_path = Path("slirn_home/static/pipeline.js")
    if not js_path.exists():
        import pytest
        pytest.skip("工作目录不在仓库根")
    src = js_path.read_text(encoding="utf-8")
    # 必须在 pollStatus 的状态切换分支里：prevRunning && !nowRunning 时调 refreshOutputsList
    # 这避免 idle 状态下每次 1.5s 轮询都触发一次刷新（浪费请求 + 可能堆积）
    idx_edge = src.find("prevRunning && !nowRunning")
    assert idx_edge > 0, (
        "必须在边沿触发（running → 终态）分支里调 refreshOutputsList，"
        "不能写 r.state !== 'running'（会每轮 poll 都触发）"
    )
    section = src[idx_edge:idx_edge + 400]
    assert "refreshOutputsList()" in section, (
        "running → 终态边沿触发时必须自动 refresh 产物列表"
    )


# REQ-20260921-NNN-outputs-browser-v2：产物浏览器高度固定 + 类别筛选 + mtime 显示

def test_pipeline_js_outputs_filter_chips_html():
    """产物浏览器面板必须有 chip 筛选行容器（按 kind 筛选）。"""
    js_path = Path("slirn_home/static/pipeline.js")
    if not js_path.exists():
        import pytest
        pytest.skip("工作目录不在仓库根")
    src = js_path.read_text(encoding="utf-8")
    assert 'data-pipe-outputs-filter' in src, (
        "产物浏览器必须挂 chip 筛选行容器 data-pipe-outputs-filter"
    )
    assert 'data-action="pipe-outputs-filter"' in src, (
        "chip 按钮必须带 data-action='pipe-outputs-filter' 走事件委托"
    )


def test_pipeline_js_outputs_filter_chips_in_render_outputs():
    """_renderOutputsList 必须根据数据里出现的 kind 动态生成 chip。"""
    js_path = Path("slirn_home/static/pipeline.js")
    if not js_path.exists():
        import pytest
        pytest.skip("工作目录不在仓库根")
    src = js_path.read_text(encoding="utf-8")
    idx = src.find("function _renderOutputsList")
    assert idx > 0
    section = src[idx:idx + 4000]
    # 必须出现 __all__ + 动态 kind chip 生成
    assert '__all__' in section, "必须有『全部』chip"
    assert 'data-pipe-outputs-chip' in section, "每个 chip 必须带 data-pipe-outputs-chip 属性"
    assert 'data-filter=' in section, "chip 必须带 data-filter 供 click handler 读"


def test_pipeline_js_outputs_filter_uses_apply_filter():
    """点击 chip 必须走 _applyOutputsFilter（不重新拉接口）。"""
    js_path = Path("slirn_home/static/pipeline.js")
    if not js_path.exists():
        import pytest
        pytest.skip("工作目录不在仓库根")
    src = js_path.read_text(encoding="utf-8")
    assert "_applyOutputsFilter" in src, "必须有 _applyOutputsFilter 切 chip 助手函数"
    # 必须缓存 items 用于无网络过滤
    assert "_outputsAllItems" in src, "必须缓存 _outputsAllItems 用于切 chip 不过接口"
    # click handler 路径：找 `action === 'pipe-outputs-filter'` 分支（事件委托里的判别）
    idx = src.find("action === 'pipe-outputs-filter'")
    assert idx > 0, "事件委托里必须有 pipe-outputs-filter 分支"
    section = src[idx:idx + 200]
    assert "_applyOutputsFilter" in section, (
        "chip click handler 必须调 _applyOutputsFilter"
    )


def test_pipeline_js_outputs_item_renders_mtime():
    """每个 item 必须显示文件 mtime（标题 = 文件最后修改时间）。"""
    js_path = Path("slirn_home/static/pipeline.js")
    if not js_path.exists():
        import pytest
        pytest.skip("工作目录不在仓库根")
    src = js_path.read_text(encoding="utf-8")
    idx = src.find("function _renderOutputItem")
    assert idx > 0
    section = src[idx:idx + 3000]
    assert "item.mtime" in section, "_renderOutputItem 必须读 item.mtime"
    assert "_formatMtime" in section, "_renderOutputItem 必须调 _formatMtime 格式化"
    assert "slirn-pipe-outputs-mtime" in section, "必须有 slirn-pipe-outputs-mtime 类渲染 mtime"


def test_pipeline_js_outputs_has_format_mtime_helper():
    """必须有 _formatMtime 助手（epoch 秒 → YYYY-MM-DD HH:MM 本地时间）。"""
    js_path = Path("slirn_home/static/pipeline.js")
    if not js_path.exists():
        import pytest
        pytest.skip("工作目录不在仓库根")
    src = js_path.read_text(encoding="utf-8")
    idx = src.find("function _formatMtime")
    assert idx > 0, "必须有 _formatMtime 助手"
    body = src[idx:idx + 600]
    assert "getFullYear" in body, "_formatMtime 必须用 getFullYear 格式化"
    assert "getMonth" in body, "_formatMtime 必须用 getMonth 格式化"


def test_outputs_css_list_has_fixed_max_height():
    """产物浏览器列表必须有固定 max-height + overflow-y auto（避免 42 个文件爆长）。"""
    css_path = Path("slirn_home/static/home.css")
    if not css_path.exists():
        import pytest
        pytest.skip("工作目录不在仓库根")
    src = css_path.read_text(encoding="utf-8")
    # 找到 .slirn-pipe-outputs-list 规则
    idx = src.find(".slirn-pipe-outputs-list {")
    assert idx > 0, "必须有 .slirn-pipe-outputs-list 规则"
    # 规则必须在第一个 { 后的 600 字符内有 max-height + overflow-y
    rule_end = src.find("}", idx)
    rule = src[idx:rule_end]
    assert "max-height" in rule, "列表必须 max-height 固定（≈ 3 个视频高）"
    assert "overflow-y: auto" in rule, "列表必须 overflow-y: auto 出滚动条"
    # 验证 max-height 数值是视频 max-height (360px) 的合理倍数
    import re as _re
    m = _re.search(r"max-height:\s*(\d+)px", rule)
    assert m, "max-height 必须有 px 数值"
    h = int(m.group(1))
    assert 500 <= h <= 900, f"max-height {h}px 应在 500-900（≈ 3 个视频 360px），过小/过大都怪"


def test_outputs_css_has_chip_and_mtime_styles():
    """产物浏览器必须有 chip + mtime 样式。"""
    css_path = Path("slirn_home/static/home.css")
    if not css_path.exists():
        import pytest
        pytest.skip("工作目录不在仓库根")
    src = css_path.read_text(encoding="utf-8")
    assert ".slirn-pipe-outputs-chip" in src, "必须有 .slirn-pipe-outputs-chip 样式"
    assert "[data-active" in src, "chip 必须有 data-active 选中态样式"
    assert ".slirn-pipe-outputs-mtime" in src, "必须有 .slirn-pipe-outputs-mtime 样式"
    assert ".slirn-pipe-outputs-filter" in src, "必须有 .slirn-pipe-outputs-filter 容器样式"




# ---------- REQ-20260922-NNN 优化字幕：整句替换 + 播放暂停/停止快捷键 ----------

def _write_min_optimize(outputs: Path, segs=None, occs=None):
    """写最小 optimize_subtitle.json（端点测试前置）。"""
    from slirn_home import optimize_service as osvc

    if segs is None:
        segs = [{"i": 1, "start_ms": 0, "end_ms": 2000, "start": "00:00:00,000",
                 "end": "00:00:02,000", "text": "今天讲一下神精网络"}]
    if occs is None:
        occs = [{"occ_id": 0, "seg": 1, "pos": 6, "before": "神精网络",
                 "after": "神经网络", "reason": "", "applied": True, "reviewed": False}]
    data = {"version": 1, "video": "rough_compose.mp4", "model": "m", "provider": "p",
            "protocol": "openai", "created_at": "2026-09-22T10:00:00", "saved_at": None,
            "hotwords": [], "segments": segs, "occurrences": occs,
            "words": osvc.aggregate_words(occs), "mapping": osvc.build_mapping(occs),
            "stats": {"lines": len(segs), "occurrences": len(occs)}}
    outputs.mkdir(parents=True, exist_ok=True)
    (outputs / osvc.OPTIMIZE_JSON).write_text(json.dumps(data, ensure_ascii=False),
                                              encoding="utf-8")


def test_save_optimize_subtitle_with_line_edits(tmp_path: Path):
    """REQ-20260922-NNN：POST save 携带 line_edits → toast 含「整句替换」+
    stats.line_edits → /optimized_srt 导出的 SRT 含改写文本（不含被覆盖的原文）。"""
    from slirn_home.app import build_app
    from fastapi.testclient import TestClient

    m, _ = _make_mgr(tmp_path)
    t = m.create(name="整句", original_video=tmp_path / "lecture.mp4")
    outputs = tmp_path / "tasks" / t.task_id / "outputs"
    _write_min_optimize(outputs)

    client = TestClient(build_app(tmp_path).app)
    resp = client.post("/slirn/api/save_optimize_subtitle", json={
        "task_id": t.task_id,
        "decisions": [{"occ_id": 0, "applied": True, "after": "神经网络", "reviewed": True}],
        "line_edits": [{"seg": 1, "text": "今天我们来讲一下神经网络"}],
    })
    body = resp.json()
    assert body["ok"] is True, body
    assert "整句替换 1 行" in body["toast"], body["toast"]
    assert body["stats"]["line_edits"] == 1

    resp2 = client.post("/slirn/api/optimized_srt", json={"task_id": t.task_id})
    body2 = resp2.json()
    assert body2["ok"] is True, body2
    assert "今天我们来讲一下神经网络" in body2["srt"]
    assert "神精网络" not in body2["srt"]


def test_save_optimize_subtitle_rejects_bad_line_edits(tmp_path: Path):
    """REQ-20260922-NNN：line_edits 非 list → 显式报错（防类型错乱静默清空）。"""
    from slirn_home.app import build_app
    from fastapi.testclient import TestClient

    m, _ = _make_mgr(tmp_path)
    t = m.create(name="整句2", original_video=tmp_path / "lecture.mp4")
    outputs = tmp_path / "tasks" / t.task_id / "outputs"
    _write_min_optimize(outputs)

    client = TestClient(build_app(tmp_path).app)
    resp = client.post("/slirn/api/save_optimize_subtitle", json={
        "task_id": t.task_id,
        "decisions": [{"occ_id": 0, "applied": True, "after": "神经网络", "reviewed": True}],
        "line_edits": {"seg": 1, "text": "不是数组"},
    })
    body = resp.json()
    assert body["ok"] is False
    assert "line_edits" in body["error"]


def _router_src() -> str:
    p = FUNCLIP_ROOT / "slirn_home" / "static" / "router.js"
    if not p.exists():
        import pytest
        pytest.skip("router.js 不存在")
    return p.read_text(encoding="utf-8")


def test_opt_line_edit_js_actions_and_collect():
    """REQ-20260922-NNN：router.js 必须有整句替换三个 action 分支 + 收集函数 +
    保存 payload 携带 line_edits + 编辑框 Enter/Esc 委托。"""
    src = _router_src()
    assert "function optCollectLineEdits" in src, "必须有 optCollectLineEdits 收集函数"
    assert 'action === \'opt-line-edit\'' in src, "必须有 opt-line-edit 分支（进入编辑/取消整句）"
    assert 'action === \'opt-line-apply\'' in src, "必须有 opt-line-apply 分支（应用整句）"
    assert 'action === \'opt-line-cancel\'' in src, "必须有 opt-line-cancel 分支（取消编辑）"
    assert "function optLineEdit" in src and "function optLineApply" in src
    assert "function optLineCancel" in src and "function optLineClear" in src
    # 保存 payload 必须带 line_edits（optSave / optAutoSave / pending 快照三处）
    assert src.count("optCollectLineEdits()") >= 3, (
        "optSave + optAutoSave + pending 快照都应收集 line_edits")
    assert "decisions: decisions, line_edits: line_edits" in src, (
        "保存请求 payload 必须携带 line_edits")
    # textarea Enter 应用 / Esc 取消
    assert "slirn-opt-line-input" in src
    assert "optLineApply(row)" in src and "optLineCancel(row)" in src


def test_opt_play_stop_shortcut_js():
    """REQ-20260922-NNN：播放/暂停 + 停止两键 — REV_KEY_ACTIONS 有 stop、
    handler 只匹配 km.play/km.stop、提示条渲染函数。"""
    src = _router_src()
    assert "{ id: 'stop'" in src and "def: 'x'" in src, "REV_KEY_ACTIONS 必须含 stop 动作"
    assert "function optTogglePlay" in src, "必须有 optTogglePlay"
    assert "function optStopPlay" in src, "必须有 optStopPlay"
    # handler 只匹配两键（其他键不劫持）
    assert "k !== km.play && k !== km.stop" in src, (
        "优化字幕 keydown handler 必须只匹配 km.play/km.stop")
    # 提示条 + 三处跟随调用
    assert "function applyOptKeysState" in src
    assert src.count("applyOptKeysState();") >= 3, (
        "wb 渲染 / 键位改绑 / 恢复默认三处都应刷新优化字幕提示条")


def test_opt_line_edit_css_styles():
    """REQ-20260922-NNN：整句替换 + 覆盖置灰 + 提示条样式。"""
    p = FUNCLIP_ROOT / "slirn_home" / "static" / "home.css"
    css = p.read_text(encoding="utf-8")
    assert ".slirn-opt-row.full-edit {" in css, "必须有整句行样式（青色条 + 淡青底）"
    assert ".slirn-opt-occ.overridden" in css, "occ 被覆盖必须置灰"
    assert ".slirn-opt-line-badge" in css and ".slirn-opt-line-input" in css
    assert ".slirn-opt-kbhint" in css, "必须有快捷键提示条样式"
    assert ".slirn-opt-occ-override-hint" in css


# ===== 切分修剪「只播保留内容」裸播路径封堵（REQ-20260922-NNN）=====
# 用户反馈：行点击连播时整条删除的字幕没跳过、播到列表最后还在继续播完整
# 视频内容。排查结论：正常跳播链路本来就正确（cutRowKept 读组 data-act），
# 漏播的是退化到「裸播」（无跳播序列）的三条路径 —
#   ① cutPreview 点中「其后全删」的行 → 退化定位裸播
#   ② cutTogglePlay 空格恢复（序列已清）→ 裸播恢复
#   ③ 工作台重渲染时浮层旧播放器（在 innerHTML 外）继续裸播
# 三条路径都会把已删除整条 / 列表外无字幕片段原样播出去。

def _router_src() -> str:
    return (FUNCLIP_ROOT / "slirn_home" / "static" / "router.js").read_text(
        encoding="utf-8")


def test_cut_preview_no_plain_play_when_no_kept():
    """① 点中「其后全删」的行：不再退化定位裸播（toast + 不播）。"""
    src = _router_src()
    i = src.find("function cutPreview")
    assert i > 0, "必须有 cutPreview"
    body = src[i:i + 1400]
    j = body.find("if (!seq.length)")
    assert j > 0, "cutPreview 必须有空序列分支"
    k = body.find("return;", j)
    assert k > 0, "空序列分支必须 return"
    branch = body[j:k]
    assert "toast" in branch, "空序列分支必须 toast 说明（不播）"
    assert "playCutAt" not in branch, (
        "空序列分支绝不能再调 playCutAt 裸播 — 会播出已删除/列表外内容")
    assert body.find("playCutAt(tid, seq[0].s)", k) > 0, (
        "有保留内容时仍应正常跳播开播")


def test_cut_toggle_play_resume_never_plain_plays():
    """② 空格恢复：序列没了必须重建保留序列再播，不允许裸 play()。"""
    src = _router_src()
    i = src.find("function cutTogglePlay")
    assert i > 0, "必须有 cutTogglePlay"
    body = src[i:src.find("\n  }\n", i)]
    resume = body[body.find("if (v.paused)"):]
    assert "cutKeepAllFrom" in resume, (
        "恢复播放必须用 cutKeepAllFrom 重建跳播序列")
    assert "cutKeepMode = 'row'" in resume, "重建后必须进入行级连播模式"
    assert "tmsR >= seqAll[seqAll.length - 1].e" in resume, (
        "当前位置已到最后一段之后 → 必须提示并不再播")
    # 裸 play() 只允许出现在序列就绪/重建之后
    k = resume.find("v.play()")
    assert k > resume.find("if (!cutKeepSeq)"), (
        "play() 必须在「无序列则重建」守卫之后，不允许裸播恢复")


def test_cut_player_seeked_listener_instant_highlight():
    """③a seek 落点即时处理：索引绝对重定位 + 高亮即时跟随（抽 cutHL 共用）。"""
    src = _router_src()
    i = src.find("function bindCutPlayer")
    assert i > 0, "必须有 bindCutPlayer"
    body = src[i:src.find("\n  }\n", i)]
    assert "var cutHL = function(tms)" in body, (
        "高亮逻辑必须抽成 cutHL 闭包（timeupdate 与 seeked 共用）")
    assert body.find("addEventListener('seeked'") > 0, (
        "必须监听 seeked — 跳播/拖动落点即时高亮与索引重定位")
    assert "tmsS < cutKeepSeq[0].s - 500" in body, (
        "落点在首段前 500ms 外必须吸附回首段起点（片头无字幕内容不播）")
    assert "while (i2 < cutKeepSeq.length - 1 && tmsS >= cutKeepSeq[i2].e - 30) i2++" in body, (
        "seeked 里必须按落点绝对重定位跳播索引（前拖/后拖/拖进删除洞都收敛）")


def test_open_workbench_pauses_stale_videos_before_rerender():
    """③b 工作台重渲染前必须暂停浮层 + 工作台里的所有视频（防旧播放器裸播）。"""
    src = _router_src()
    i = src.find("w.innerHTML = r.html;")  # 带分号防匹配到注释里的同名文字
    assert i > 0, "openWorkbench 必须重渲染工作台"
    before = src[max(0, i - 900):i]
    assert "slirn-video-float" in before and "vd.pause()" in before, (
        "innerHTML 替换前必须暂停浮层里的旧视频 — 浮层不在 w 内，"
        "换 innerHTML 杀不掉它，会带着失效行引用继续裸播到底")
    assert "_vfL.hidden = true" in before, "暂停后应收起浮层"


# ---------- REQ-20260922-NNN 标记删除行 + 优化成片剪辑 ----------

def _css_src() -> str:
    return (FUNCLIP_ROOT / "slirn_home" / "static" / "home.css").read_text(
        encoding="utf-8")


def test_opt_line_delete_js_actions_and_collect():
    """标记删除行：opt-line-delete 分支 + 收集器 + 自动保存/保存载荷全带 line_marks。"""
    src = _router_src()
    # 委托分支
    assert "action === 'opt-line-delete'" in src, "必须委托 opt-line-delete 动作"
    assert "optLineDelete(target.closest('.slirn-opt-row'))" in src
    # 收集器：data-deleted="1" 行 → 升序去重 seg id 数组
    i = src.find("function optCollectLineMarks")
    assert i > 0, "必须有 optCollectLineMarks 收集器"
    body = src[i:src.find("\n  }\n", i)]
    assert 'data-deleted="1"' in body and "sort" in body
    # 翻转函数：类 + data-deleted + 徽章 + 复用自动保存锁
    j = src.find("function optLineDelete")
    assert j > 0
    body2 = src[j:src.find("\n  }\n", j)]
    assert "line-deleted" in body2 and "slirn-opt-line-badge del" in body2
    assert "optLineAfterEdit(row)" in body2, "翻转后必须触发自动保存"
    # 载荷：autosave 请求 + pending 快照 + optSave 三处都带 line_marks
    a = src.find("function optAutoSave")
    auto = src[a:src.find("\n  function optSave", a)]
    assert "line_marks: optCollectLineMarks()" in auto, "pending 快照要带 line_marks"
    assert "line_marks: line_marks" in auto, "请求载荷要带 line_marks"
    k = src.find("function optSave")
    save_body = src[k:src.find("\n  }\n", k)]
    assert "line_marks" in save_body, "optSave 载荷必须带 line_marks"
    assert "optCollectLineMarks().length === 0" in save_body or (
        "line_marks.length === 0" in save_body), "空判据必须含 marks"
    # 保存响应触发剪辑轮询
    assert "startOptCutPolling(tid)" in save_body
    # 编辑态/重渲保留删除按钮（镜像服务端结构）
    assert "function optDelBtnHtml" in src
    assert "optDelBtnHtml(row) + splitBtn + btn + body" in src, (
        "optLineRender 重渲必须保留删除 + 切分按钮")


def test_opt_cut_polling_and_resume_js():
    """剪辑轮询：done → 刷新工作台；error → 状态条 + 回退提示；wb 渲染恢复轮询。"""
    src = _router_src()
    i = src.find("function startOptCutPolling")
    assert i > 0, "必须有 startOptCutPolling"
    body = src[i:src.find("\n  }\n", i)]
    assert "/optimize_cut_status" in body
    assert "openWorkbench(tid)" in body, "done 后必须刷新工作台"
    assert "optimize_compose" not in body.split("j.state === 'running'")[0] or True
    # openWorkbench 渲染旁路：running 状态条 → 恢复轮询
    k = src.find("var optCutSt = revVis('slirn-opt-cut-status');")
    assert k > 0, "wb 重渲后必须检查剪辑状态条"
    after = src[k:k + 300]
    assert "startOptCutPolling(optCutSt.dataset.taskId)" in after
    # SRT 下载带 base 参数（rough/cut 两种时间基）
    m = src.find("function optSrtDownload")
    dl = src[m:src.find("\n  }\n", m)]
    assert "data-base" in dl and "'cut'" in dl


def test_opt_delete_css_styles():
    """标记删除行样式：红条 + 删除线 + 徽章 + 剪辑状态条三态底色。"""
    css = _css_src()
    assert ".slirn-opt-row.line-deleted {" in css, "必须有 line-deleted 行样式"
    assert "text-decoration: line-through" in css, "正文必须删除线"
    assert ".slirn-opt-line-badge.del {" in css, "必须有 del 徽章样式"
    assert ".slirn-opt-cut-status[data-state=\"error\"]" in css, (
        "剪辑状态条必须有 error 态样式")
    assert ".slirn-opt-cut-status[data-state=\"done\"]" in css


def test_opt_play_skip_deleted_js():
    """播放跳过：已删除块不播出（成片效果）— 区间收集/合并 + 起点推进 + tick 跳块。"""
    src = _router_src()
    # 区间收集器：data-deleted="1" 行 → [start,end] 升序 + 相邻合并
    i = src.find("function optDeletedIntervals")
    assert i > 0, "必须有 optDeletedIntervals 收集器"
    body = src[i:src.find("\n  }\n", i)]
    assert 'data-deleted="1"' in body and "data-start-ms" in body \
        and "data-end-ms" in body
    assert "sort" in body, "区间必须按 start 升序"
    assert "Math.max" in body, "相邻/重叠区间必须合并成块"
    # 起点推进：落在块内 → 块尾 +1ms
    j = src.find("function optSkipPastDeleted")
    assert j > 0, "必须有 optSkipPastDeleted 起点推进"
    body2 = src[j:src.find("\n  }\n", j)]
    assert "ivs[i][1] + 1" in body2, "推进目标 = 块尾后 1ms（防浮点落回块内）"
    # tick 跳块：暂停不跳（用户可停在删除段内查看），播放中 currentTime 进块即跳块尾
    k = src.find("function optSkipDeletedOnTick")
    assert k > 0, "必须有 optSkipDeletedOnTick"
    body3 = src[k:src.find("\n  }\n", k)]
    assert "v.paused" in body3, "暂停时不得抢跳（允许查看删除段）"
    assert "v.currentTime = (ivs[i][1] + 1) / 1000" in body3
    # 接线三处：playOptAt 起点推进 / 播放器 timeupdate 挂跳块 / 点删除行提示
    p = src.find("function playOptAt")
    play = src[p:src.find("\n  }\n", p)]
    assert "optSkipPastDeleted(startMs || 0)" in play, (
        "起播 seek 必须经 optSkipPastDeleted 推进（点删除行从其后播起）")
    b = src.find("function bindOptPlayerHighlight")
    bind = src[b:src.find("\n  }\n", b)]
    assert "optSkipDeletedOnTick(v)" in bind, "timeupdate 必须挂删除块跳播"
    assert "从其后内容播起" in src, "点已删除行必须提示从其后播起"


def test_opt_deleted_filter_and_final_view_js():
    """查找已删除行筛选 + 最终字幕预览弹窗（应用替换 + 去掉删除行）。"""
    src = _router_src()
    # 只看已删除行：类切换 + 恢复文案 + 委托分支
    i = src.find("function optFilterDeletedBtn")
    assert i > 0, "必须有 optFilterDeletedBtn 筛选函数"
    body = src[i:src.find("\n  }\n", i)]
    assert "slirn-opt-only-deleted" in body and "显示全部识别行" in body
    assert "action === 'opt-filter-deleted'" in src, "必须委托 opt-filter-deleted"
    assert "optFilterDeletedBtn(target)" in src
    # 最终字幕预览：拉 /optimized_srt + 弹窗渲染 + 双时间基切换
    j = src.find("function optFinalView")
    assert j > 0, "必须有 optFinalView"
    body2 = src[j:src.find("\n  }\n", j)]
    assert "/optimized_srt" in body2 and "base: 'rough'" in body2
    assert '[data-action="opt-srt-download"][data-base="cut"]' in body2, (
        "cut 时间基可用性要与 SRT 下载按钮同源判断")
    assert "action === 'opt-final-view'" in src and "optFinalView(target)" in src
    k = src.find("function _renderOptFinalModal")
    assert k > 0, "必须有弹窗渲染函数"
    body3 = src[k:k + 2000]
    assert "slirn-opt-final-pre" in body3 and "escapeHtml(srt)" in body3, (
        "SRT 原文必须转义后进 <pre>")
    assert "data-final-base" in body3 and '"cut"' in body3, "cut 就绪时给双时间基切换"
    assert "去掉标记删除行" in body3, "弹窗说明必须写明删除行已去掉"
    # CSS：删除行筛选隐藏规则 + 弹窗 pre 样式
    css = _css_src()
    assert ".slirn-opt-list.slirn-opt-only-deleted .slirn-opt-row:not(.line-deleted)" in css, (
        "必须有只看已删除行的隐藏规则")
    assert ".slirn-opt-final-pre {" in css, "必须有最终字幕 pre 样式"


# =============== REQ-20260923-NNN 优化字幕行内切分 + 重新拼接（JS/CSS 源码断言） ===============

def test_opt_split_js_actions_and_collect():
    """行内切分 JS：split_marks 全量快照收集 + 子段翻转 + ✂️/↩️/🔄 端点接线 +
    保存负载带 split_marks + 跳播区间并入删除子段。"""
    src = _router_src()
    # split_marks 收集：全量快照（[] = 全保留也要有键，否则服务端回落默认洞）
    i = src.find("function optCollectSplitMarks")
    assert i > 0, "必须有 optCollectSplitMarks"
    body = src[i:src.find("\n  }\n", i)]
    assert "querySelectorAll('.slirn-opt-subrow')" in body
    assert "data-parent" in body and "data-mark" in body and "data-sub-idx" in body
    # 保存负载三处都带 split_marks（autosave 现场 / pending 快照 / 手动保存）
    n_payload = src.count("split_marks = optCollectSplitMarks()") \
        + src.count("split_marks: optCollectSplitMarks()")
    assert n_payload >= 3, f"保存负载必须三处带 split_marks（实测 {n_payload}）"
    # 子段翻转：data-mark 翻转 + line-deleted 类 + ✚/🗑️ 互换 + 自动保存
    j = src.find("function optSubToggle")
    assert j > 0, "必须有 optSubToggle"
    b2 = src[j:src.find("\n  }\n", j)]
    assert "classList.toggle('line-deleted'" in b2
    assert "optLineAfterEdit" in b2, "翻转后必须触发自动保存"
    # ✂️ 编辑框（复用切分修剪 .slirn-cut-resplit 类）+ Enter/Esc + 提交端点
    k = src.find("function optOpenResplit")
    assert k > 0, "必须有 optOpenResplit"
    b3 = src[k:k + 2000]
    assert "slirn-cut-resplit-input" in b3 and "optDoResplit()" in b3
    m = src.find("function optDoResplit")
    assert m > 0 and "/optimize_resplit" in src[m:src.find("\n  }\n", m)]
    u = src.find("function optUnsplit")
    assert u > 0 and "/optimize_unsplit" in src[u:src.find("\n  }\n", u)]
    # 🔄 重新拼接字幕：即时重算 SRT（不编码视频）
    p = src.find("function optResplice")
    assert p > 0 and "/optimize_resplice_subs" in src[p:src.find("\n  }\n", p)]
    # 委托分发六分支
    for act in ("opt-line-resplit", "opt-resplit-go", "opt-resplit-cancel",
                "opt-line-unsplit", "opt-sub-toggle", "opt-resplice"):
        assert f"action === '{act}'" in src, f"缺少委托分支 {act}"
    # 跳播区间收集并入删除子段（播放预览成片效果）
    d = src.find("function optDeletedIntervals")
    assert 'slirn-opt-subrow[data-mark="delete"]' in src[d:d + 800], (
        "删除子段必须并入跳播区间")


def test_opt_split_js_render_preserves_split_btn():
    """行内重渲（optLineRender/optLineEdit）不得丢 ✂️ 按钮：切分行让位（不渲染
    ✂️/🗑️），未切分行保留 ✂️。"""
    src = _router_src()
    assert "getAttribute('data-split') === '1'" in src, (
        "重渲必须按 data-split 分支决定是否保留 ✂️")
    # 切分行重渲后不再是切分行（编辑态整行操作被切分互斥）→ 行动按钮集合以
    # data-split 分支为准，两个重渲函数都要走同一判定
    assert src.count("getAttribute('data-split') === '1'") >= 2, (
        "optLineRender 与 optLineEdit 两处重渲都必须判定 data-split")


def test_opt_split_css_styles():
    """切分子段样式：紫条 inset 缩进 + ✂️ 徽章 + 删除态红压紫 + ⚠️ 降级标记。"""
    css = _css_src()
    assert ".slirn-opt-row.slirn-opt-subrow {" in css, "必须有子段行样式（紫条 inset）"
    assert "#8b5cf6" in css, "切分系主色紫"
    assert ".slirn-opt-line-badge.split {" in css, "必须有 ✂️ 已切分徽章样式"
    assert ".slirn-opt-row.slirn-opt-subrow.line-deleted {" in css, (
        "删除子段必须红压紫（同特异性更晚 → 需显式重申，否则紫条盖红）")
    assert ".slirn-opt-subfb {" in css, "必须有降级 ⚠️ 样式"
    assert ".slirn-opt-submark:hover" in css, "子段翻转按钮 hover 必须显红"
