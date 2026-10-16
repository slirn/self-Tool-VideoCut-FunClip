"""REQ-20261016-134 — Stage 6 卡片必须可见（即使 stage6_refine.highlights 为空）。

用户原话：「第六阶段，这个详情区域根本没有可操作的按钮，要如何执行字幕的优化呀？」

修复：_stage6_refine 渲染时，如果 stage6_refine.highlights 为空但
stage5_coarse.highlights 有产物，则用 stage5 数据合成占位条目并渲染。
否则用户进 Stage 6 看不到任何 clip 卡片 / 按钮。

覆盖：
1. 仅有 stage5_coarse.highlights 时，Stage 6 也渲染出每条 clip 卡片
2. 合成卡片含「重新 ASR / ✨ LLM 优化」按钮
3. 真正 stage6_refine.highlights 存在时优先用它（不合成）
4. coarse 空时仍走「先完成 Stage 5 粗剪」提示
"""

from pathlib import Path

FUNCLIP_ROOT = Path(__file__).resolve().parent.parent


def _src(rel: str) -> str:
    return (FUNCLIP_ROOT / "slirn_home" / rel).read_text(encoding="utf-8")


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
    assert s6_idx > 0, "Stage 6 面板没渲染"
    # 截到下一个 stage 之前（如果有）
    next_idx = html.find('data-stage="7"', s6_idx)
    return html[s6_idx:next_idx] if next_idx > s6_idx else html[s6_idx:]


def test_stage6_renders_cards_from_coarse_when_stage6_empty():
    """当 stage6_refine.highlights 空但 stage5_coarse.highlights 有产物时，
    Stage 6 必须渲染出对应的 clip 卡片。
    """
    html = _stage6_html({
        "stage3_analyze": {"status": "done", "highlights": [
            {"index": 1, "title": "钩子"},
            {"index": 2, "title": "反差点"},
            {"index": 3, "title": "结尾"},
        ]},
        "stage5_coarse": {
            "status": "done",
            "highlights": [
                {"index": 1, "status": "done", "title": "钩子",
                 "coarse_mp4": "tasks/t1/short_video/p1/stage4/c1.mp4",
                 "duration_sec": 73.8},
                {"index": 2, "status": "done", "title": "反差点",
                 "coarse_mp4": "tasks/t1/short_video/p1/stage4/c2.mp4",
                 "duration_sec": 38.0},
                {"index": 3, "status": "done", "title": "结尾",
                 "coarse_mp4": "tasks/t1/short_video/p1/stage4/c3.mp4",
                 "duration_sec": 44.8},
            ],
        },
        # stage6_refine.highlights 故意空（用户首次进 Stage 6 的真实场景）
    })
    chunk = _s6_chunk(html)
    # 3 张 clip 卡片（用 .slirn-stage5-clip 类名，Stage 6 与 Stage 5 共用卡片骨架）
    clip_class = 'class="slirn-stage5-clip"'
    clip_count = chunk.count(clip_class)
    assert clip_count == 3, f"Stage 6 应有 3 张卡片（合成自 stage5），实际={clip_count}"
    # 3 个「重新 ASR」按钮
    assert chunk.count('data-action="sv-stage5-asr"') == 3
    # 3 个「✨ LLM 优化」按钮
    assert chunk.count('data-action="sv-stage6-optimize"') == 3


def test_stage6_uses_existing_highlights_when_present():
    """真正有 stage6_refine.highlights 时，优先用它（不合成）。"""
    html = _stage6_html({
        "stage3_analyze": {"status": "done", "highlights": [
            {"index": 1, "title": "钩子"},
            {"index": 2, "title": "反差点"},
            {"index": 3, "title": "结尾"},
        ]},
        "stage5_coarse": {
            "status": "done",
            "highlights": [
                {"index": 1, "status": "done", "title": "钩子",
                 "coarse_mp4": "tasks/t1/short_video/p1/stage4/c1.mp4", "duration_sec": 73.8},
            ],
        },
        "stage6_refine": {
            "status": "running",
            "highlights": [
                {"index": 1, "status": "asr_done", "asr_srt": "stage5/refined_01_asr.srt",
                 "refined_srt": "stage5/refined_01.srt", "asr_chars": 120},
            ],
        },
    })
    chunk = _s6_chunk(html)
    # 只有 1 张（仅 stage6_refine 那条）
    assert chunk.count('class="slirn-stage5-clip"') == 1
    assert chunk.count('data-action="sv-stage5-asr"') == 1


def test_stage6_empty_when_no_coarse():
    """coarse 也空时，Stage 6 显示「先完成 Stage 5 粗剪」占位。"""
    html = _stage6_html({
        "stage1_source": {"status": "done"},
        # 故意不给 stage5_coarse
    })
    chunk = _s6_chunk(html)
    # 没有 clip 卡片
    assert 'class="slirn-stage5-clip"' not in chunk
    # 显示提示
    assert "先完成 Stage 5 粗剪" in chunk
