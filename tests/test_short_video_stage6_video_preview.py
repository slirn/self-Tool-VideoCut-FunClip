"""REQ-20261016-135 — Stage 6 视频预览按钮。

用户原话：「短视频展现时用弹出窗口，并且可以调整大小」。

实现：每条 clip pane 加「▶ 预览」按钮（用 REQ-132 可拖动 + 可调大小浮窗），
URL 从 stage5_coarse.highlights[i].coarse_mp4 取，kind=stage4（mp4 真实落点）。
无 coarse_mp4 时显示「未生成」占位。

覆盖：
1. 有 coarse_mp4 → 渲染预览按钮 + URL 正确
2. URL 含 kind=stage4（与 Stage 5 同款）
3. 无 coarse_mp4 → 显示「未生成」+ 0 按钮
4. 合成占位也带 coarse_mp4（REQ-134 升级版：可点预览）
5. 按钮 data-clip-name 与 REQ-132 一致
"""

from pathlib import Path

FUNCLIP_ROOT = Path(__file__).resolve().parent.parent


def _stage6_html(pipeline_extra: dict) -> str:
    from slirn_home import short_video_ui as ui
    base = {
        "id": "p1", "task_id": "t1", "name": "demo", "materials": [],
        "pipeline": {"stage1_source": {"status": "done"}},
    }
    base["pipeline"].update(pipeline_extra)
    return ui.render_project(base)


def _s6_chunk(html: str) -> str:
    s6_idx = html.find('data-stage="6"')
    s7_idx = html.find('data-stage="7"', s6_idx)
    assert s6_idx > 0
    return html[s6_idx:s7_idx] if s7_idx > s6_idx else html[s6_idx:]


def test_stage6_has_preview_button_when_coarse_mp4_present():
    """3 条 clip 都有 coarse_mp4 → 3 个预览按钮 + URL 正确"""
    html = _stage6_html({
        "stage3_analyze": {"status": "done", "highlights": [
            {"index": 1, "title": "A"},
            {"index": 2, "title": "B"},
            {"index": 3, "title": "C"},
        ]},
        "stage5_coarse": {
            "status": "done",
            "highlights": [
                {"index": 1, "status": "done", "title": "A",
                 "coarse_mp4": "tasks/t1/short_video/p1/stage4/c1.mp4", "duration_sec": 73.8},
                {"index": 2, "status": "done", "title": "B",
                 "coarse_mp4": "tasks/t1/short_video/p1/stage4/c2.mp4", "duration_sec": 38.0},
                {"index": 3, "status": "done", "title": "C",
                 "coarse_mp4": "tasks/t1/short_video/p1/stage4/c3.mp4", "duration_sec": 44.8},
            ],
        },
    })
    chunk = _s6_chunk(html)
    # 3 个预览按钮（data-action="sv-stage5-preview"）
    assert chunk.count('data-action="sv-stage5-preview"') == 3
    # 按钮文本
    assert "▶ 预览" in chunk
    # 0 个「未生成」占位
    assert "未生成" not in chunk


def test_stage6_preview_button_uses_stage4_kind():
    """URL kind 必须为 stage4（mp4 真实落点，与 Stage 5 一致）"""
    html = _stage6_html({
        "stage3_analyze": {"status": "done", "highlights": [
            {"index": 1, "title": "A"},
        ]},
        "stage5_coarse": {
            "status": "done",
            "highlights": [
                {"index": 1, "status": "done", "title": "A",
                 "coarse_mp4": "tasks/t1/short_video/p1/stage4/c1.mp4", "duration_sec": 73.8},
            ],
        },
    })
    chunk = _s6_chunk(html)
    # URL 包含 kind=stage4 + name=c1.mp4
    assert "kind=stage4" in chunk
    assert "name=c1.mp4" in chunk
    # data-url 完整路径
    assert ('data-url="/slirn/api/short_video_file?task_id=t1&amp;project_id=p1&amp;kind=stage4&amp;name=c1.mp4"'
            in chunk)


def test_stage6_no_preview_when_no_coarse_mp4():
    """无 coarse_mp4 → 显示「未生成」+ 0 个预览按钮"""
    html = _stage6_html({
        "stage3_analyze": {"status": "done", "highlights": [
            {"index": 1, "title": "A"},
        ]},
        "stage5_coarse": {
            "status": "done",
            "highlights": [
                {"index": 1, "status": "done", "title": "A",
                 "duration_sec": 73.8},  # 无 coarse_mp4
            ],
        },
    })
    chunk = _s6_chunk(html)
    # 0 个预览按钮
    assert "data-action=\"sv-stage5-preview\"" not in chunk
    # 「未生成」占位
    assert "未生成" in chunk


def test_stage6_preview_synthesized_from_coarse():
    """stage6_refine 空但 stage5_coarse 有产物 → 合成占位也带 coarse_mp4（可点预览）"""
    html = _stage6_html({
        "stage3_analyze": {"status": "done", "highlights": [
            {"index": 1, "title": "A"},
            {"index": 2, "title": "B"},
        ]},
        "stage5_coarse": {
            "status": "done",
            "highlights": [
                {"index": 1, "status": "done", "title": "A",
                 "coarse_mp4": "tasks/t1/short_video/p1/stage4/c1.mp4"},
                {"index": 2, "status": "done", "title": "B",
                 "coarse_mp4": "tasks/t1/short_video/p1/stage4/c2.mp4"},
            ],
        },
        # stage6_refine.highlights 故意空（用户首次进 Stage 6）
    })
    chunk = _s6_chunk(html)
    # 2 个预览按钮（合成占位也带 coarse_mp4）
    assert chunk.count('data-action="sv-stage5-preview"') == 2
    # URL 都正确
    assert "name=c1.mp4" in chunk
    assert "name=c2.mp4" in chunk


def test_stage6_preview_includes_clip_name_attr():
    """data-clip-name 与 REQ-132 一致（#N 预览）"""
    html = _stage6_html({
        "stage3_analyze": {"status": "done", "highlights": [
            {"index": 1, "title": "A"},
            {"index": 2, "title": "B"},
        ]},
        "stage5_coarse": {
            "status": "done",
            "highlights": [
                {"index": 1, "status": "done", "title": "A",
                 "coarse_mp4": "tasks/t1/short_video/p1/stage4/c1.mp4"},
                {"index": 2, "status": "done", "title": "B",
                 "coarse_mp4": "tasks/t1/short_video/p1/stage4/c2.mp4"},
            ],
        },
    })
    chunk = _s6_chunk(html)
    # 每个按钮带 data-clip-name（REQ-132 浮窗用它做标题）
    assert 'data-clip-name="#1 预览"' in chunk
    assert 'data-clip-name="#2 预览"' in chunk
    # data-clip-index
    assert 'data-clip-index="1"' in chunk
    assert 'data-clip-index="2"' in chunk
