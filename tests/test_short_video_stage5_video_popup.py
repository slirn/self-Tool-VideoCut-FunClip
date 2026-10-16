"""REQ-20261016-132 — Stage 5 视频预览改成可拖动浮窗。

覆盖：
1. Stage 5 clip 卡片不再内嵌 `<video controls preload="metadata">`
2. 改为「▶ 预览」按钮，data-action / data-url / data-clip-name 都齐
3. 没 URL 时不渲染预览按钮（用 muted「未生成」占位）
4. JS 路由派发表里有 `sv-stage5-preview`
5. JS 源码里含 `svStage5Preview(btn)` 函数 + 创建 lazy popup
6. CSS 里有 `.slirn-sv-video-popup` 浮窗样式（position: fixed / 不遮父 / resize）
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


def test_stage5_clip_no_inline_video():
    """Stage 5 视频不再内嵌在 clip 卡片里。"""
    html = _stage5_html({
        "stage3_analyze": {"status": "done", "highlights": [{"index": 1, "title": "t"}]},
        "stage5_coarse": {
            "status": "done",
            "highlights": [
                {"index": 1, "status": "done", "coarse_mp4": "tasks/t1/short_video/p1/stage4/1.mp4",
                 "coarse_srt": "tasks/t1/short_video/p1/stage4/1.srt", "duration_sec": 5.0},
            ],
        },
    })
    # 抓 stage5 面板（避免误中 stage4/6 视频）
    s5_idx = html.find('data-stage="5"')
    assert s5_idx > 0, "Stage 5 面板没渲染"
    s6_idx = html.find('data-stage="6"')
    s5_chunk = html[s5_idx:s6_idx] if s6_idx > s5_idx else html[s5_idx:]
    assert "<video controls" not in s5_chunk, "Stage 5 仍内嵌 <video>，应改为预览按钮"


def test_stage5_preview_button_has_required_attrs():
    """「▶ 预览」按钮 data-action / data-url / data-clip-name 都齐。"""
    html = _stage5_html({
        "stage3_analyze": {"status": "done", "highlights": [{"index": 1, "title": "t"}]},
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
    s5_idx = html.find('data-stage="5"')
    s6_idx = html.find('data-stage="6"')
    s5_chunk = html[s5_idx:s6_idx] if s6_idx > s5_idx else html[s5_idx:]
    # 2 条 clip → 2 个预览按钮
    assert s5_chunk.count('data-action="sv-stage5-preview"') == 2
    # URL 经过 _file_url() 转成端点路径
    assert 'data-url="/slirn/api/short_video_file?task_id=t1&amp;project_id=p1&amp;kind=stage4&amp;name=c1.mp4"' in s5_chunk
    assert 'data-url="/slirn/api/short_video_file?task_id=t1&amp;project_id=p1&amp;kind=stage4&amp;name=c2.mp4"' in s5_chunk
    # clip name 用 #N
    assert 'data-clip-name="#1"' in s5_chunk
    assert 'data-clip-name="#2"' in s5_chunk
    # 按钮文字是「▶ 预览」
    assert '▶ 预览</button>' in s5_chunk


def test_stage5_no_url_shows_placeholder_not_button():
    """无粗剪 mp4 时不渲染预览按钮，用「未生成」占位。"""
    html = _stage5_html({
        "stage3_analyze": {"status": "done", "highlights": [{"index": 1, "title": "t"}]},
        "stage5_coarse": {
            "status": "done",
            "highlights": [
                {"index": 1, "status": "done", "coarse_mp4": "", "duration_sec": 0},
            ],
        },
    })
    s5_idx = html.find('data-stage="5"')
    s6_idx = html.find('data-stage="6"')
    s5_chunk = html[s5_idx:s6_idx] if s6_idx > s5_idx else html[s5_idx:]
    assert 'data-action="sv-stage5-preview"' not in s5_chunk
    assert "未生成" in s5_chunk


def test_short_video_js_dispatches_preview_action():
    """JS 路由派发表里有 sv-stage5-preview。"""
    src = _src("static/short_video.js")
    assert "action === 'sv-stage5-preview'" in src, "缺 sv-stage5-preview 路由派发"
    assert "svStage5Preview(btn)" in src, "缺 svStage5Preview 函数实现"


def test_short_video_js_defines_video_popup_factory():
    """JS 含 ensureSvVideoPopup + bindSvPopupDrag（拖动 + 调大小）。"""
    src = _src("static/short_video.js")
    assert "function ensureSvVideoPopup()" in src, "缺 ensureSvVideoPopup"
    assert "function bindSvPopupDrag" in src, "缺 bindSvPopupDrag"
    # 浮窗根 id 唯一
    assert "'slirn-sv-video-popup'" in src, "缺 popup id"
    # 关闭按钮 + 拖动手柄
    assert "slirn-sv-video-popup-close" in src
    assert "slirn-sv-video-popup-head" in src


def test_css_video_popup_is_floating_draggable_resizable():
    """CSS 浮窗：position fixed + resize both + 不遮 modal。"""
    css = _src("static/home.css")
    # 找 popup 块
    assert ".slirn-sv-video-popup {" in css, "缺 .slirn-sv-video-popup 样式"
    popup_block = css.split(".slirn-sv-video-popup {", 1)[1].split("}", 1)[0]
    assert "position: fixed" in popup_block, "popup 必须是 fixed 定位"
    assert "resize: both" in popup_block, "popup 必须原生可调大小（resize: both）"
    # 拖动 head 必须 cursor: move
    head_block = css.split(".slirn-sv-video-popup-head {", 1)[1].split("}", 1)[0]
    assert "cursor: move" in head_block, "popup head 必须是 move 光标（拖动手柄）"
    # z-index 必须低于 modal（10000）但高于普通卡
    assert "z-index: 9500" in popup_block, "popup z-index 应明确为 9500"
    # 没有全屏 overlay（不遮父页）
    overlay_block = css.split(".slirn-sv-video-popup-overlay", 1)
    # 故意访问不存在的 split 索引 → 长度 1 → 不会有 overlay 块
    assert len(overlay_block) == 1, "不要给浮窗加全屏 overlay（会遮父页）"
