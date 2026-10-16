"""REQ-20261016-138 — 移除「📝 切换源」按钮。

用户原话：
> 这个按钮的名称也没有变啊，并且这里面有个切换源是啥意思？为什么要切换源呢？
> 你先说明白有哪些源？第一个短视频不就一个源吗
> 字幕直接显示在下面，为什么要切换？

「切换源」按钮的「源」字容易误解为「数据源」（短视频只有 1 个输入视频），
实际是「切换视图」（行编辑 ↔ SRT 源文）。per-row 编辑器已覆盖 99% 场景，
隐藏 textarea 仍作为后端 stage5Save 的数据源保留。

覆盖（反向断言锁住「按钮不存在」）：
1. _stage6_refine 不再渲染 data-action="sv-stage6-toggle-source"
2. _stage6_refine 不再渲染「📝 切换源」文字
3. JS 路由表不再含 sv-stage6-toggle-source
4. JS 不再有 stage6ToggleSource 函数
5. 隐藏 textarea（slirn-stage5-srt）仍存在（保存时数据源不破）
6. per-row 编辑器（slirn-stage6-srt-rows-wrap）仍存在
7. 其他 actions 不受影响：sv-stage5-asr / sv-stage5-save / sv-stage6-optimize / sv-stage5-preview
"""

import re
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
    assert s6_idx > 0, "Stage 6 面板没渲染"
    return html[s6_idx:s7_idx] if s7_idx > s6_idx else html[s6_idx:]


def test_toggle_source_action_removed():
    """_stage6_refine 不再渲染 data-action="sv-stage6-toggle-source" 按钮"""
    html = _stage6_html({
        "stage3_analyze": {"status": "done", "highlights": [{"index": 1, "title": "钩子"}]},
        "stage5_coarse": {"status": "done", "highlights": [
            {"index": 1, "status": "done", "coarse_mp4": "tasks/t1/short_video/p1/stage4/c1.mp4"},
        ]},
    })
    chunk = _s6_chunk(html)
    assert 'data-action="sv-stage6-toggle-source"' not in chunk, (
        "REQ-138：Stage 6 面板不再渲染「📝 切换源」按钮（per-row 编辑器已足够）"
    )


def test_toggle_source_text_removed():
    """_stage6_refine 不再渲染「📝 切换源」文字"""
    html = _stage6_html({
        "stage3_analyze": {"status": "done", "highlights": [{"index": 1, "title": "钩子"}]},
        "stage5_coarse": {"status": "done", "highlights": [
            {"index": 1, "status": "done", "coarse_mp4": "tasks/t1/short_video/p1/stage4/c1.mp4"},
        ]},
    })
    chunk = _s6_chunk(html)
    assert "切换源" not in chunk, "REQ-138：「切换源」文字必须从 Stage 6 消失"


def test_toggle_source_action_removed_for_all_clips():
    """所有 3 个 clip 都不渲染切换源按钮（验证完整遍历）"""
    html = _stage6_html({
        "stage3_analyze": {"status": "done", "highlights": [
            {"index": i, "title": f"clip {i}"} for i in [1, 2, 3]
        ]},
        "stage5_coarse": {"status": "done", "highlights": [
            {"index": i, "status": "done", "coarse_mp4": f"tasks/t1/short_video/p1/stage4/c{i}.mp4"}
            for i in [1, 2, 3]
        ]},
    })
    chunk = _s6_chunk(html)
    assert chunk.count('data-action="sv-stage6-toggle-source"') == 0, (
        "REQ-138：3 个 clip 全部不应有切换源按钮"
    )


def test_js_route_removed():
    """JS 路由表不再含 sv-stage6-toggle-source action"""
    js_path = (FUNCLIP_ROOT / "slirn_home" / "static" / "short_video.js").read_text(
        encoding="utf-8")
    # 必须不存在
    assert "action === 'sv-stage6-toggle-source'" not in js_path, (
        "REQ-138：JS 路由表应已移除 sv-stage6-toggle-source"
    )


def test_js_function_removed():
    """JS 不再有 stage6ToggleSource 函数"""
    js_path = (FUNCLIP_ROOT / "slirn_home" / "static" / "short_video.js").read_text(
        encoding="utf-8")
    # 抽出 function 定义
    fn_defs = re.findall(r"function stage6ToggleSource\s*\(", js_path)
    assert len(fn_defs) == 0, "REQ-138：stage6ToggleSource 函数应已移除"


def test_hidden_textarea_still_present():
    """隐藏 textarea（slirn-stage5-srt）仍存在 — 保存时数据源不破"""
    html = _stage6_html({
        "stage3_analyze": {"status": "done", "highlights": [{"index": 1, "title": "钩子"}]},
        "stage5_coarse": {"status": "done", "highlights": [
            {"index": 1, "status": "done", "coarse_mp4": "tasks/t1/short_video/p1/stage4/c1.mp4"},
        ]},
    })
    chunk = _s6_chunk(html)
    # 隐藏 textarea 必须保留（stage5Save 端点从它读 SRT）
    assert 'class="slirn-stage5-srt"' in chunk, "隐藏 textarea 必须保留（后端数据源）"
    assert 'style="display:none;"' in chunk, "textarea 必须仍是 display:none"


def test_per_row_editor_still_present():
    """per-row 编辑器（slirn-stage6-srt-rows-wrap）仍存在 — 不破 REQ-133"""
    html = _stage6_html({
        "stage3_analyze": {"status": "done", "highlights": [{"index": 1, "title": "钩子"}]},
        "stage5_coarse": {"status": "done", "highlights": [
            {"index": 1, "status": "done", "coarse_mp4": "tasks/t1/short_video/p1/stage4/c1.mp4"},
        ]},
    })
    chunk = _s6_chunk(html)
    assert 'class="slirn-stage6-srt-rows-wrap"' in chunk, "per-row 编辑器必须保留"


def test_other_actions_unaffected():
    """其他 actions 不受影响（移除切换源是独立操作）"""
    html = _stage6_html({
        "stage3_analyze": {"status": "done", "highlights": [{"index": 1, "title": "钩子"}]},
        "stage5_coarse": {"status": "done", "highlights": [
            {"index": 1, "status": "done", "coarse_mp4": "tasks/t1/short_video/p1/stage4/c1.mp4"},
        ]},
    })
    chunk = _s6_chunk(html)
    # 仍存在的 actions
    assert 'data-action="sv-stage5-asr"' in chunk, "提取短视频字幕按钮必须保留"
    assert 'data-action="sv-stage5-save"' in chunk, "保存修正按钮必须保留"
    assert 'data-action="sv-preview-srt"' in chunk, "查看 ASR 字幕按钮必须保留"
    assert 'data-action="sv-stage5-preview"' in chunk, "预览按钮必须保留"
    assert 'data-action="sv-stage6-optimize"' in chunk, "LLM 优化按钮必须保留"
    # 不应再有切换源
    assert 'data-action="sv-stage6-toggle-source"' not in chunk


def test_js_syntax_still_valid():
    """JS 文件 node --check 通过（删除函数后语法未破）"""
    import subprocess
    result = subprocess.run(
        ["node", "--check", str(FUNCLIP_ROOT / "slirn_home" / "static" / "short_video.js")],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, f"JS 语法错：stderr={result.stderr[:500]}"
