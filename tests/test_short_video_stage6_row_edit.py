"""REQ-20261016-133 — Stage 6 per-row 编辑（仿 Stage 4 风格）。

覆盖：
1. _render_stage6_srt_rows 解析 SRT → 每行 3 按钮
2. _stage6_refine 渲染含 .slirn-stage6-srt-rows-wrap + 隐藏 textarea
3. 缺 SRT 时显示「点「提取短视频字幕」生成字幕」占位（REQ-136 按钮改名）
4. JS 路由表里有 sv-stage6-line-edit / line-toggle / line-resplit / toggle-source
5. JS 函数实现存在
6. CSS 块有 per-row 样式（含 deleted 红删线）
"""

from pathlib import Path

FUNCLIP_ROOT = Path(__file__).resolve().parent.parent


def _src(rel: str) -> str:
    return (FUNCLIP_ROOT / "slirn_home" / rel).read_text(encoding="utf-8")


def _stage6_html(pipeline_extra: dict, *, with_refined: bool = True) -> str:
    from slirn_home import short_video_ui as ui
    base = {
        "id": "p1", "task_id": "t1", "name": "demo", "materials": [],
        "pipeline": {"stage1_source": {"status": "done"}},
    }
    base["pipeline"].update(pipeline_extra)
    return ui.render_project(base)


SAMPLE_SRT = (
    "1\n00:00:00,000 --> 00:00:02,000\n你好世界\n\n"
    "2\n00:00:02,000 --> 00:00:04,500\n这是第二行\n\n"
    "3\n00:00:04,500 --> 00:00:07,000\n第三行字幕\n"
)


# ---------- 1. _render_stage6_srt_rows 单元测试 ----------

def test_render_stage6_srt_rows_returns_3_rows_for_3_entries():
    from slirn_home import short_video_ui as ui
    html = ui._render_stage6_srt_rows(SAMPLE_SRT, 1)
    # 3 行 → 3 个 .slirn-stage6-srt-row
    assert html.count('class="slirn-stage6-srt-row"') == 3
    # 第 1 行有 #1
    assert 'data-seg-no="1"' in html
    assert 'data-seg-no="2"' in html
    assert 'data-seg-no="3"' in html


def test_render_stage6_srt_rows_has_3_buttons_per_row():
    from slirn_home import short_video_ui as ui
    html = ui._render_stage6_srt_rows(SAMPLE_SRT, 1)
    # 每行 1 个 ✏️ + 1 个 ✂️ + 1 个 🗑️ = 3 按钮
    assert html.count('data-action="sv-stage6-line-edit"') == 3
    assert html.count('data-action="sv-stage6-line-resplit"') == 3
    assert html.count('data-action="sv-stage6-line-toggle"') == 3


def test_render_stage6_srt_rows_empty_shows_placeholder():
    from slirn_home import short_video_ui as ui
    html = ui._render_stage6_srt_rows("", 1)
    assert "暂无字幕行" in html
    # REQ-20261016-136：按钮名「重新 ASR」→「提取短视频字幕」
    assert "点「提取短视频字幕」生成" in html


def test_render_stage6_srt_rows_preserves_text_and_time():
    from slirn_home import short_video_ui as ui
    html = ui._render_stage6_srt_rows(SAMPLE_SRT, 1)
    # 时间段保留
    assert "00:00:00,000 → 00:00:02,000" in html
    assert "00:00:04,500 → 00:00:07,000" in html
    # 文字保留
    assert "你好世界" in html
    assert "第三行字幕" in html


# ---------- 2. _stage6_refine 集成测试 ----------

def test_stage6_panel_contains_per_row_editor_and_hidden_textarea():
    """Stage 6 面板必须含 .slirn-stage6-srt-rows-wrap + 隐藏 textarea。"""
    html = _stage6_html({
        "stage5_coarse": {"highlights": [
            {"index": 1, "coarse_mp4": "tasks/t1/short_video/p1/stage4/1.mp4",
             "coarse_srt": "tasks/t1/short_video/p1/stage4/1.srt", "duration_sec": 5.0},
        ]},
        "stage6_refine": {"highlights": [
            {"index": 1, "status": "done",
             "asr_srt": "tasks/t1/short_video/p1/stage5/1.srt",
             "refined_srt": "tasks/t1/short_video/p1/stage5/1_refined.srt",
             "asr_chars": 10},
        ]},
    })
    s5_idx = html.find('data-stage="6"')
    s6_idx = html.find('data-stage="7"')
    s5_chunk = html[s5_idx:s6_idx] if s6_idx > s5_idx else html[s5_idx:]
    # per-row 容器
    assert 'class="slirn-stage6-srt-rows-wrap"' in s5_chunk
    # 隐藏 textarea
    assert 'class="slirn-stage5-srt"' in s5_chunk
    assert 'style="display:none;"' in s5_chunk
    # 切换按钮
    assert 'data-action="sv-stage6-toggle-source"' in s5_chunk
    assert "📝 切换源" in s5_chunk


def test_stage6_panel_empty_highlights_shows_placeholder():
    """无 SRT 文件时显示「点「提取短视频字幕」生成字幕」占位（REQ-136 改名）。"""
    html = _stage6_html({
        "stage5_coarse": {"highlights": [
            {"index": 1, "coarse_mp4": "x", "duration_sec": 1.0},
        ]},
        "stage6_refine": {"highlights": [
            {"index": 1, "status": "pending"},  # 无 refined_srt / asr_srt
        ]},
    })
    s5_idx = html.find('data-stage="6"')
    s6_idx = html.find('data-stage="7"')
    s5_chunk = html[s5_idx:s6_idx] if s6_idx > s5_idx else html[s5_idx:]
    assert "点「提取短视频字幕」生成字幕" in s5_chunk


# ---------- 3. JS 路由 + 函数 ----------

def test_short_video_js_dispatches_stage6_row_actions():
    """JS 路由表里 4 个新 action。"""
    src = _src("static/short_video.js")
    assert "action === 'sv-stage6-line-edit'" in src, "缺 sv-stage6-line-edit 路由"
    assert "action === 'sv-stage6-line-toggle'" in src, "缺 sv-stage6-line-toggle 路由"
    assert "action === 'sv-stage6-line-resplit'" in src, "缺 sv-stage6-line-resplit 路由"
    assert "action === 'sv-stage6-toggle-source'" in src, "缺 sv-stage6-toggle-source 路由"


def test_short_video_js_defines_stage6_row_helpers():
    """JS 含 stage6LineEdit / stage6LineToggle / stage6LineResplit / stage6RebuildSrt。"""
    src = _src("static/short_video.js")
    assert "function stage6LineEdit(btn)" in src
    assert "function stage6LineToggle(btn)" in src
    assert "function stage6LineResplit(btn)" in src
    assert "function stage6RebuildSrt" in src
    assert "function stage6ToggleSource" in src


def test_short_video_js_save_uses_rebuild():
    """stage5Save 必须先调 stage6RebuildSrt（把 per-row 状态回写 textarea）。"""
    src = _src("static/short_video.js")
    # 找 stage5Save 函数体
    save_fn = src.split("function stage5Save(btn)", 1)[1].split("function ", 1)[0]
    assert "stage6RebuildSrt" in save_fn, (
        "stage5Save 必须先调 stage6RebuildSrt 把 per-row 状态回写 textarea，"
        "否则保存按钮只能保存 textarea 的旧内容，per-row 改动丢失"
    )


# ---------- 4. CSS ----------

def test_css_stage6_row_styles_defined():
    """CSS 含 .slirn-stage6-srt-row + deleted 标。"""
    css = _src("static/home.css")
    assert ".slirn-stage6-srt-row {" in css, "缺 .slirn-stage6-srt-row 样式"
    assert ".slirn-stage6-srt-row-deleted {" in css, "缺 deleted 红删线样式"
    # deleted 块必须含 line-through
    deleted_block = css.split(".slirn-stage6-srt-row.slirn-stage6-srt-row-deleted {", 1)[1].split("}", 1)[0]
    assert "line-through" in deleted_block, "deleted 行必须有 line-through 视觉"
