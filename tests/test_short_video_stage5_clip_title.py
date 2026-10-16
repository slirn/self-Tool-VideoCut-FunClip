"""REQ-20261016-134 — Stage 5 clip 卡片标题置顶。

用户原话：红框所选的区域，这个3个短视频相关信息以列表形式展现，
先显示标题，然后是其他的信息及按钮放在后面。

覆盖：
1. Stage 5 卡片顶部含 `.slirn-stage4-clip-title`，文字来自 stage3 highlights.title
2. 无标题时用「片段 #N」占位（永远不为空）
3. head 行（状态/时长/按钮）在 title 之后
4. CSS 标题样式：font-size 14px / font-weight 600 / 2 行截断
"""

from pathlib import Path

FUNCLIP_ROOT = Path(__file__).resolve().parent.parent


def _src(rel: str) -> str:
    return (FUNCLIP_ROOT / "slirn_home" / rel).read_text(encoding="utf-8")


def _stage5_html(pipeline_extra: dict) -> str:
    from slirn_home import short_video_ui as ui
    base = {
        "id": "p1", "task_id": "t1", "name": "demo", "materials": [],
        "pipeline": {"stage1_source": {"status": "done"}},
    }
    base["pipeline"].update(pipeline_extra)
    return ui.render_project(base)


def _s5_chunk(html: str) -> str:
    s5_idx = html.find('data-stage="5"')
    s6_idx = html.find('data-stage="6"')
    assert s5_idx > 0, "Stage 5 面板没渲染"
    return html[s5_idx:s6_idx] if s6_idx > s5_idx else html[s5_idx:]


def test_stage5_clip_title_appears_before_head():
    """每张 clip 卡片有 title 元素，且在 head 之前。"""
    html = _stage5_html({
        "stage3_analyze": {"status": "done", "highlights": [
            {"index": 1, "title": "钩子：3秒抓住注意力"},
            {"index": 2, "title": "反差点1：常识误区"},
            {"index": 3, "title": "结尾：行动召唤"},
        ]},
        "stage5_coarse": {
            "status": "done",
            "highlights": [
                {"index": 1, "status": "done", "title": "钩子：3秒抓住注意力",
                 "coarse_mp4": "tasks/t1/short_video/p1/stage4/c1.mp4", "duration_sec": 73.8},
                {"index": 2, "status": "done", "title": "反差点1：常识误区",
                 "coarse_mp4": "tasks/t1/short_video/p1/stage4/c2.mp4", "duration_sec": 38.0},
                {"index": 3, "status": "done", "title": "结尾：行动召唤",
                 "coarse_mp4": "tasks/t1/short_video/p1/stage4/c3.mp4", "duration_sec": 44.8},
            ],
        },
    })
    chunk = _s5_chunk(html)
    # 3 个标题元素（用 *-top 避免与 L6329 的 slirn-stage4-clip-title 冲突）
    assert chunk.count('class="slirn-stage4-clip-title-top"') == 3, \
        "Stage 5 卡片应有 3 个 title-top 元素"
    # 标题文案（HTML 转义后）
    assert "钩子：3秒抓住注意力" in chunk
    assert "反差点1：常识误区" in chunk
    assert "结尾：行动召唤" in chunk
    # title 元素必须在 head 之前（按行序）
    title_pos = chunk.find('<div class="slirn-stage4-clip-title-top"')
    head_pos = chunk.find('<div class="slirn-stage4-clip-head"')
    assert title_pos > 0 and head_pos > 0, "缺 title 或 head 块"
    assert title_pos < head_pos, "title 必须在 head 之前（先显示标题）"


def test_stage5_clip_title_falls_back_to_index():
    """无 title 时用「片段 #N」占位（永远不为空）。"""
    html = _stage5_html({
        "stage3_analyze": {"status": "done", "highlights": [
            {"index": 1, "title": ""},
            {"index": 2},  # 无 title 字段
        ]},
        "stage5_coarse": {
            "status": "done",
            "highlights": [
                {"index": 1, "status": "done", "coarse_mp4": "tasks/t1/short_video/p1/stage4/c1.mp4",
                 "duration_sec": 5.0},
                {"index": 2, "status": "done", "coarse_mp4": "tasks/t1/short_video/p1/stage4/c2.mp4",
                 "duration_sec": 6.0},
            ],
        },
    })
    chunk = _s5_chunk(html)
    # 占位符（title 字段缺失 → 走 fallback "片段 #N"）
    assert "片段 #1" in chunk
    assert "片段 #2" in chunk
    # title 属性也带占位文案（hover 可见）
    assert 'title="片段 #1"' in chunk
    assert 'title="片段 #2"' in chunk


def test_stage5_clip_head_contains_status_and_buttons():
    """head 行含状态 badge + 时长 + 下载按钮（用户原话「其他的信息及按钮放在后面」）。"""
    html = _stage5_html({
        "stage3_analyze": {"status": "done", "highlights": [
            {"index": 1, "title": "钩子"},
        ]},
        "stage5_coarse": {
            "status": "done",
            "highlights": [
                {"index": 1, "status": "done", "title": "钩子",
                 "coarse_mp4": "tasks/t1/short_video/p1/stage4/c1.mp4",
                 "coarse_srt": "tasks/t1/short_video/p1/stage4/c1.srt", "duration_sec": 73.8},
            ],
        },
    })
    chunk = _s5_chunk(html)
    head_idx = chunk.find('<div class="slirn-stage4-clip-head"')
    head_chunk = chunk[head_idx:]
    # head 内必须含：#1 + 状态 + 时长 + 下载视频 + 复制SRT + 下载SRT
    assert "#1" in head_chunk
    assert "73.8s" in head_chunk
    assert "下载视频" in head_chunk
    assert "复制 SRT" in head_chunk
    assert "下载 SRT" in head_chunk


def test_css_title_style_defined():
    """CSS 含 .slirn-stage4-clip-title-top 样式：14px / 600 / 2 行截断。"""
    css = _src("static/home.css")
    assert ".slirn-stage4-clip-title-top {" in css, "缺 .slirn-stage4-clip-title-top 样式"
    block = css.split(".slirn-stage4-clip-title-top {", 1)[1].split("}", 1)[0]
    assert "font-size: 14px" in block, "标题应 14px"
    assert "font-weight: 600" in block, "标题应 600（半粗）"
    # 2 行截断
    assert "-webkit-line-clamp: 2" in block, "标题应 2 行截断（line-clamp: 2）"


def test_stage5_clip_layout_order_is_title_head():
    """卡片内元素顺序必须是：title-top → head → preview-btn。"""
    html = _stage5_html({
        "stage3_analyze": {"status": "done", "highlights": [
            {"index": 1, "title": "钩子"},
        ]},
        "stage5_coarse": {
            "status": "done",
            "highlights": [
                {"index": 1, "status": "done", "title": "钩子",
                 "coarse_mp4": "tasks/t1/short_video/p1/stage4/c1.mp4",
                 "duration_sec": 73.8},
            ],
        },
    })
    chunk = _s5_chunk(html)
    title_pos = chunk.find('slirn-stage4-clip-title-top')
    head_pos = chunk.find('slirn-stage4-clip-head')
    preview_pos = chunk.find('sv-stage5-preview')
    assert title_pos < head_pos < preview_pos, \
        f"顺序错：title={title_pos} head={head_pos} preview={preview_pos}"
