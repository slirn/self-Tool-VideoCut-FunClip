"""测试编辑任务 + 剪辑工作台 — REQ-20260915-003。

渲染层测试：任务卡片按钮、编辑模式预填、工作台阶段状态/面板。
update_task 的重截取逻辑走 E2E（见 work/REQ-20260915-003-workbench/）。
"""

from __future__ import annotations

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
    assert _wb_stage_states(t) == ["done", "current"] + ["pending"] * 6


def test_wb_stage_states_with_subtitle(tmp_path: Path):
    """有 subtitle.json（磁盘为准）→ 前两阶段 done，第三 current。"""
    from slirn_home.app import _wb_stage_states

    m, video = _make_mgr(tmp_path)
    t = m.create(name="t", original_video=video)
    outputs = tmp_path / "tasks" / t.task_id / "outputs"
    (outputs / "subtitle.json").write_text(
        '{"version":1,"segments":[{"i":1,"start_ms":0,"end_ms":1,"start":"0","end":"0","text":"x"}]}',
        encoding="utf-8")
    assert _wb_stage_states(t) == ["done", "done", "current"] + ["pending"] * 5


def test_wb_stage_states_by_status_rank(tmp_path: Path):
    """无产物但状态已推进 → 按状态序判定（服务器重启后内存丢失场景的兜底）。"""
    from tasklib.models import TaskStatus

    from slirn_home.app import _wb_stage_states

    m, video = _make_mgr(tmp_path)
    t = m.create(name="t", original_video=video)
    m.update_status(t.task_id, TaskStatus.ROUGH_CUT_DONE)
    states = _wb_stage_states(m.get(t.task_id))
    assert states == ["done"] * 4 + ["current"] + ["pending"] * 3


def test_render_workbench_layout(tmp_path: Path):
    from slirn_home.app import _render_workbench

    m, video = _make_mgr(tmp_path)
    t = m.create(name="工作台任务", original_video=video, hotwords=["热词A"])
    html = _render_workbench(t.task_id, m)
    # 顶部信息
    assert "剪辑工作台 · 工作台任务" in html
    assert "📁 lecture.mp4（" in html
    assert "未截取 · 使用完整原视频" in html
    # 左侧 8 阶段 + 状态
    assert html.count('class="slirn-wb-stage ') == 8
    assert "slirn-wb-stage done" in html and "slirn-wb-stage current" in html
    assert "素材准备" in html and "字幕生成" in html and "字幕合成" in html
    # 右侧面板：素材清单 + 字幕区（含生成按钮）+ 字幕修订区 + 切分修剪区
    # （REQ-005 后修订区真实化；REQ-20260916-008 后切分修剪区真实化）+ 4 个规划占位
    assert "📦 资产清单" in html
    assert 'data-action="gen-subtitle"' in html
    assert "处理剪辑 · 第 2 步：字幕修订" in html
    assert 'id="slirn-wb-pane-subtitle_review"' in html
    assert "✂️ 切分修剪" in html, "阶段名「粗剪」改为「切分修剪」（REQ-20260916-008）"
    assert "处理剪辑 · 第 3 步：切分修剪" in html
    assert 'id="slirn-wb-pane-rough_cut"' in html
    assert html.count("规划中 — 该阶段将在后续版本提供") == 4
    assert html.count("slirn-wb-pane\"") >= 1  # 面板容器齐备
    # 聚焦 current（字幕生成）→ 字幕面板默认显示
    import re
    assert re.search(r'id="slirn-wb-pane-subtitle"(?![^>]*display:none)', html)
    assert 'id="slirn-wb-pane-assets" style="display:none;"' in html


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
    # 阶段条目不受影响
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
