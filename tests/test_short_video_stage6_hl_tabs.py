"""REQ-20261016-135 — Stage 6 字幕优化面板 tab 化。

用户原话：「在字幕优化阶段，要把对应着前一阶段所有的视频的标题显示上，
显示方式参考解析短视频内容阶段的形式」。

修复：_stage6_refine 改为 tab 化（仿 Stage 3 大按钮）+ 单 pane
（仿 Stage 4 REQ-109），避免 N 张卡片堆叠看不到标题。

覆盖：
1. 每条 clip 渲染一个 tab + 一个 pane
2. tab 含 #N + 完整标题 + ▾ 箭头（仿 REQ-130）
3. pane 内有 .slirn-stage5-clip（REQ-133 per-row 编辑不破）
4. 第 1 个 tab/pane 默认 active
5. 无 title 时 fallback「片段 #N」
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
    assert s6_idx > 0, "Stage 6 面板没渲染"
    return html[s6_idx:s7_idx] if s7_idx > s6_idx else html[s6_idx:]


def test_stage6_renders_hl_tabs_per_clip():
    """3 条 clip → 3 个 tab + 3 个 pane（tab 化结构）"""
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
                {"index": 2, "status": "done", "title": "反差点",
                 "coarse_mp4": "tasks/t1/short_video/p1/stage4/c2.mp4", "duration_sec": 38.0},
                {"index": 3, "status": "done", "title": "结尾",
                 "coarse_mp4": "tasks/t1/short_video/p1/stage4/c3.mp4", "duration_sec": 44.8},
            ],
        },
    })
    chunk = _s6_chunk(html)
    # 3 个 tab button（slirn-stage6-hl-tab 类名，精确匹配避免误中子元素）
    import re
    tab_count = len(re.findall(r'class="slirn-stage6-hl-tab(?:\s|")', chunk))
    assert tab_count == 3, f"Stage 6 应有 3 个 hl-tab，实际={tab_count}"
    # 3 个 pane（slirn-stage6-hl-pane 类名）
    pane_count = len(re.findall(r'class="slirn-stage6-hl-pane(?:\s|")', chunk))
    assert pane_count == 3, f"Stage 6 应有 3 个 hl-pane，实际={pane_count}"
    # 3 个 sv-stage6-hl-tab action
    assert chunk.count('data-action="sv-stage6-hl-tab"') == 3
    # 1 个 active tab（首个）
    assert chunk.count('class="slirn-stage6-hl-tab active"') == 1
    # 1 个 active pane（首个）
    assert chunk.count('class="slirn-stage6-hl-pane active"') == 1


def test_stage6_hl_tab_includes_title_and_arrow():
    """tab 内含 #N + 完整标题 + ▾ 箭头（仿 REQ-130 Stage 3 大按钮）"""
    html = _stage6_html({
        "stage3_analyze": {"status": "done", "highlights": [
            {"index": 1, "title": "钩子：3秒抓住注意力"},
        ]},
        "stage5_coarse": {
            "status": "done",
            "highlights": [
                {"index": 1, "status": "done", "title": "钩子：3秒抓住注意力",
                 "coarse_mp4": "tasks/t1/short_video/p1/stage4/c1.mp4", "duration_sec": 73.8},
            ],
        },
    })
    chunk = _s6_chunk(html)
    # 完整 3 span 包裹（仿 Stage 3 REQ-130）
    assert '<span class="slirn-stage6-hl-tab-no">#1</span>' in chunk
    assert '<span class="slirn-stage6-hl-tab-title">钩子：3秒抓住注意力</span>' in chunk
    assert '<span class="slirn-stage6-hl-tab-arrow" aria-hidden="true">▾</span>' in chunk
    # title 属性
    assert 'title="钩子：3秒抓住注意力"' in chunk


def test_stage6_pane_contains_clip_card():
    """pane 内有 .slirn-stage5-clip 卡片（REQ-133 per-row 编辑不破）"""
    html = _stage6_html({
        "stage3_analyze": {"status": "done", "highlights": [
            {"index": 1, "title": "钩子"},
        ]},
        "stage5_coarse": {
            "status": "done",
            "highlights": [
                {"index": 1, "status": "done", "title": "钩子",
                 "coarse_mp4": "tasks/t1/short_video/p1/stage4/c1.mp4", "duration_sec": 73.8},
            ],
        },
    })
    chunk = _s6_chunk(html)
    # pane 内有 slirn-stage5-clip 卡片
    assert 'class="slirn-stage5-clip"' in chunk
    # 旧 buttons 都在（per-row 编辑 + 重新 ASR 等）
    assert 'data-action="sv-stage5-asr"' in chunk
    assert 'data-action="sv-stage5-save"' in chunk
    assert 'data-action="sv-stage6-optimize"' in chunk
    # REQ-20261016-138：切换源按钮已移除
    assert 'data-action="sv-stage6-toggle-source"' not in chunk
    # 标题置顶元素（REQ-134 样式）
    assert 'class="slirn-stage4-clip-title-top"' in chunk
    assert '钩子' in chunk


def test_stage6_first_pane_active_by_default():
    """第 1 个 tab 和 pane 都默认带 active（其余不带）"""
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
                 "coarse_mp4": "tasks/t1/short_video/p1/stage4/c1.mp4"},
                {"index": 2, "status": "done", "title": "B",
                 "coarse_mp4": "tasks/t1/short_video/p1/stage4/c2.mp4"},
                {"index": 3, "status": "done", "title": "C",
                 "coarse_mp4": "tasks/t1/short_video/p1/stage4/c3.mp4"},
            ],
        },
    })
    chunk = _s6_chunk(html)
    # 第 1 个 tab 有 active
    first_tab_active = chunk.find('class="slirn-stage6-hl-tab active"')
    assert first_tab_active > 0
    # 第 1 个 pane 有 active
    first_pane_active = chunk.find('class="slirn-stage6-hl-pane active"')
    assert first_pane_active > 0
    # 没有多个 active（只有 1 个 tab active + 1 个 pane active）
    assert chunk.count('slirn-stage6-hl-tab active') == 1
    assert chunk.count('slirn-stage6-hl-pane active') == 1


def test_stage6_falls_back_to_index_when_no_title():
    """无 title 时 tab/pane 都显示「片段 #N」占位（永远不为空）"""
    html = _stage6_html({
        "stage3_analyze": {"status": "done", "highlights": [
            {"index": 1, "title": ""},
            {"index": 2},  # 无 title 字段
        ]},
        "stage5_coarse": {
            "status": "done",
            "highlights": [
                {"index": 1, "status": "done", "coarse_mp4": "tasks/t1/short_video/p1/stage4/c1.mp4"},
                {"index": 2, "status": "done", "coarse_mp4": "tasks/t1/short_video/p1/stage4/c2.mp4"},
            ],
        },
    })
    chunk = _s6_chunk(html)
    # tab title 区域显示 fallback
    assert '<span class="slirn-stage6-hl-tab-title">片段 #1</span>' in chunk
    assert '<span class="slirn-stage6-hl-tab-title">片段 #2</span>' in chunk
    # pane 顶部也显示 fallback（标题置顶）
    assert chunk.count('片段 #1') >= 2  # tab + pane-top
    assert chunk.count('片段 #2') >= 2


def test_stage6_shows_all_clips_when_stage6_partial():
    """REQ-20261016-135：即使 stage6 只有部分条目，也要显示所有 coarse 高亮。

    之前 REQ-134 只在 stage6 完全空时合成占位；
    现在以 coarse 为全集，已处理的用真实数据，未处理的补占位。
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
                 "coarse_mp4": "tasks/t1/short_video/p1/stage4/c1.mp4"},
                {"index": 2, "status": "done", "title": "反差点",
                 "coarse_mp4": "tasks/t1/short_video/p1/stage4/c2.mp4"},
                {"index": 3, "status": "done", "title": "结尾",
                 "coarse_mp4": "tasks/t1/short_video/p1/stage4/c3.mp4"},
            ],
        },
        # stage6_refine 只有 1 条（用户只处理过 #1）
        "stage6_refine": {
            "status": "partial",
            "highlights": [
                {"index": 1, "status": "confirmed", "asr_srt": "stage5/refined_01_asr.srt",
                 "refined_srt": "stage5/refined_01.srt", "asr_chars": 120},
            ],
        },
    })
    chunk = _s6_chunk(html)
    import re
    tabs = re.findall(r'class="slirn-stage6-hl-tab(?:\s|")', chunk)
    panes = re.findall(r'class="slirn-stage6-hl-pane(?:\s|")', chunk)
    # 全部 3 条都显示（不是只有 stage6 那 1 条）
    assert len(tabs) == 3, f"应显示 3 个 tab（coarse 全集），实际={len(tabs)}"
    assert len(panes) == 3, f"应显示 3 个 pane，实际={len(panes)}"
    # 3 个预览按钮（合成占位也带 coarse_mp4）
    assert chunk.count('data-action="sv-stage5-preview"') == 3
    # 3 个 tab title 都渲染
    assert "钩子" in chunk
    assert "反差点" in chunk
    assert "结尾" in chunk
