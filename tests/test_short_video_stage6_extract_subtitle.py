"""REQ-20261016-136 — Stage 6「提取短视频字幕」按钮改名 + ASR SRT inline fallback。

用户原话：
> 把红框所框的重新ASR改成提取短视频字幕，提取之后，把字幕显示在按钮的下面，
> 这样提取之后直接就能看到，并且可以在下面这个区域进行修改，
> 修改的功能包括删除陈星切分、修改文字，这个功能跟内容优化中对字幕的操作一样

覆盖：
1. 按钮文字是「提取短视频字幕」（不再有「重新 ASR」字样）
2. 按钮的 data-action="sv-stage5-asr" 不变（避免 JS 路由回归）
3. 核心修复：refined_srt 缺失时 fallback 读 asr_srt，老项目也能 inline 显示
4. refined 优先于 asr（用户已编辑的最权威）
5. 文件不存在时优雅降级到空态
6. 占位文案含「提取短视频字幕」
7. 隐藏 textarea placeholder 同步改名
8. JS toast「重新 ASR 中…」已改成「提取短视频字幕 中…」
"""

from pathlib import Path

FUNCLIP_ROOT = Path(__file__).resolve().parent.parent


def _stage6_html(pipeline_extra: dict, with_files: bool = False) -> str:
    """生成 Stage 6 HTML。可选创建真实 SRT 文件测试 fallback 行为。"""
    from slirn_home import short_video_ui as ui
    base = {
        "id": "p1", "task_id": "t1", "name": "demo", "materials": [],
        "pipeline": {"stage1_source": {"status": "done"}},
    }
    base["pipeline"].update(pipeline_extra)
    if with_files:
        # 真实创建 SRT 文件，让 fallback 读得到
        srt_dir = (FUNCLIP_ROOT / "tasks" / "t1" / "short_video" / "p1" / "stage5")
        srt_dir.mkdir(parents=True, exist_ok=True)
        sample = "1\n00:00:00,000 --> 00:00:01,500\n你好世界\n\n2\n00:00:01,500 --> 00:00:03,000\n第二行\n"
        srt_file = srt_dir / "p1_01_asr.srt"
        srt_file.write_text(sample, encoding="utf-8")
        try:
            return ui.render_project(base)
        finally:
            srt_file.unlink(missing_ok=True)
    return ui.render_project(base)


def _s6_chunk(html: str) -> str:
    s6_idx = html.find('data-stage="6"')
    s7_idx = html.find('data-stage="7"', s6_idx)
    assert s6_idx > 0, "Stage 6 面板没渲染"
    return html[s6_idx:s7_idx] if s7_idx > s6_idx else html[s6_idx:]


def test_extract_subtitle_button_label():
    """按钮文字必须是「提取短视频字幕」（不再有「重新 ASR」字样）"""
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
    assert "提取短视频字幕" in chunk, "按钮名应改为「提取短视频字幕」"
    # 旧名必须彻底消失（按钮上）
    # 注释里允许出现（作为 REQ 标记），但按钮文字里不能
    import re
    btn_match = re.search(r'<button[^>]*data-action="sv-stage5-asr"[^>]*>([^<]*)</button>', chunk)
    assert btn_match, "找不到 sv-stage5-asr 按钮"
    btn_text = btn_match.group(1)
    assert btn_text.strip() == "提取短视频字幕", f"按钮文字应是「提取短视频字幕」，实际「{btn_text}」"
    assert "重新 ASR" not in btn_text, f"按钮文字不应含「重新 ASR」：{btn_text}"


def test_extract_subtitle_button_keeps_data_action():
    """data-action="sv-stage5-asr" 保持不变（JS 路由不破）"""
    html = _stage6_html({
        "stage3_analyze": {"status": "done", "highlights": [{"index": 1}]},
        "stage5_coarse": {"status": "done", "highlights": [
            {"index": 1, "status": "done", "coarse_mp4": "tasks/t1/short_video/p1/stage4/c1.mp4"},
        ]},
    })
    chunk = _s6_chunk(html)
    assert 'data-action="sv-stage5-asr"' in chunk, "data-action 必须保留 sv-stage5-asr"


def test_inline_srt_falls_back_to_asr_when_no_refined():
    """核心修复：refined_srt 缺失时 fallback 读 asr_srt，老项目也能 inline 显示。

    场景：项目只设置了 asr_srt（老项目 / refined_srt 写入失败），点击按钮后
    用户必须立即看到 SRT 内容，而不是「点「提取短视频字幕」生成字幕」占位。
    """
    html = _stage6_html(
        pipeline_extra={
            "stage3_analyze": {"status": "done", "highlights": [{"index": 1, "title": "钩子"}]},
            "stage5_coarse": {"status": "done", "highlights": [
                {"index": 1, "status": "done", "title": "钩子",
                 "coarse_mp4": "tasks/t1/short_video/p1/stage4/c1.mp4"},
            ]},
            # 老项目：只有 asr_srt，refined_srt 字段不存在
            "stage6_refine": {"status": "done", "highlights": [
                {"index": 1, "status": "user_editing", "asr_srt": "stage5/p1_01_asr.srt",
                 "asr_chars": 25},
            ]},
        },
        with_files=True,  # 真实创建 SRT 文件
    )
    chunk = _s6_chunk(html)
    # 关键：refined 缺失但有 asr_srt + 真实文件 → 渲染 per-row 编辑器
    assert 'class="slirn-stage6-srt-row"' in chunk, (
        "refined 缺失时必须 fallback 读 asr_srt，否则老项目永远只看到占位"
    )
    # 不能显示空态
    assert "点「提取短视频字幕」生成字幕" not in chunk, (
        "有 asr_srt 时不应再显示空态"
    )
    # SRT 文字内容渲染了
    assert "你好世界" in chunk, "asr_srt 的文字内容应 inline 显示"
    assert "第二行" in chunk, "第二条 SRT 行也应显示"


def test_inline_srt_prefers_refined_over_asr():
    """refined_srt 存在时优先读 refined（用户编辑过的最权威）"""
    html = _stage6_html(
        pipeline_extra={
            "stage3_analyze": {"status": "done", "highlights": [{"index": 1, "title": "钩子"}]},
            "stage5_coarse": {"status": "done", "highlights": [
                {"index": 1, "status": "done", "coarse_mp4": "tasks/t1/short_video/p1/stage4/c1.mp4"},
            ]},
            # refined 和 asr 都有（refined 是用户编辑过的）
            "stage6_refine": {"status": "done", "highlights": [
                {"index": 1, "status": "user_editing",
                 "asr_srt": "stage5/p1_01_asr.srt",
                 "refined_srt": "stage5/p1_01_refined.srt",  # 优先用这个
                 "asr_chars": 25},
            ]},
        },
        with_files=True,
    )
    # 创建 refined 文件（asr 文件已经在 with_files 创建）
    srt_dir = (FUNCLIP_ROOT / "tasks" / "t1" / "short_video" / "p1" / "stage5")
    refined = srt_dir / "p1_01_refined.srt"
    refined_content = (
        "1\n00:00:00,000 --> 00:00:02,000\n这是 refined（用户编辑后）\n"
    )
    refined.write_text(refined_content, encoding="utf-8")
    try:
        from slirn_home import short_video_ui as ui
        base = {
            "id": "p1", "task_id": "t1", "name": "demo", "materials": [],
            "pipeline": {
                "stage1_source": {"status": "done"},
                "stage3_analyze": {"status": "done", "highlights": [{"index": 1, "title": "钩子"}]},
                "stage5_coarse": {"status": "done", "highlights": [
                    {"index": 1, "status": "done", "coarse_mp4": "tasks/t1/short_video/p1/stage4/c1.mp4"},
                ]},
                "stage6_refine": {"status": "done", "highlights": [
                    {"index": 1, "status": "user_editing",
                     "asr_srt": "stage5/p1_01_asr.srt",
                     "refined_srt": "stage5/p1_01_refined.srt", "asr_chars": 25},
                ]},
            },
        }
        html = ui.render_project(base)
    finally:
        refined.unlink(missing_ok=True)
    chunk = _s6_chunk(html)
    # 优先用 refined：包含「refined（用户编辑后）」
    assert "refined（用户编辑后）" in chunk, "refined 存在时优先用 refined 内容"
    # 不应回退到 asr 文字
    assert "你好世界" not in chunk, "refined 存在时不应显示 asr 原始内容"


def test_inline_srt_handles_missing_files_gracefully():
    """refined_name + asr_name 指向的文件都不存在时 → 优雅降级到空态"""
    html = _stage6_html({
        "stage3_analyze": {"status": "done", "highlights": [{"index": 1, "title": "钩子"}]},
        "stage5_coarse": {"status": "done", "highlights": [
            {"index": 1, "status": "done", "coarse_mp4": "tasks/t1/short_video/p1/stage4/c1.mp4"},
        ]},
        "stage6_refine": {"status": "done", "highlights": [
            {"index": 1, "status": "user_editing",
             "asr_srt": "stage5/p1_01_asr_NOT_EXIST.srt",  # 文件不存在
             "refined_srt": "stage5/p1_01_refined_NOT_EXIST.srt",
             "asr_chars": 25},
        ]},
    })
    chunk = _s6_chunk(html)
    # 文件不存在时显示空态文案（不报错）
    assert "点「提取短视频字幕」生成字幕" in chunk, "文件缺失时显示新按钮名占位"


def test_empty_placeholder_mentions_extract_subtitle():
    """占位文案必须含「提取短视频字幕」（不再有「重新 ASR」字样）"""
    html = _stage6_html({
        "stage3_analyze": {"status": "done", "highlights": [{"index": 1, "title": "钩子"}]},
        "stage5_coarse": {"status": "done", "highlights": [
            {"index": 1, "status": "done", "coarse_mp4": "tasks/t1/short_video/p1/stage4/c1.mp4"},
        ]},
        # 故意完全空，让 empty placeholder 出现
        "stage6_refine": {"status": "pending", "highlights": []},
    })
    chunk = _s6_chunk(html)
    # 占位文案必须含新按钮名
    assert "点「提取短视频字幕」生成字幕" in chunk, "占位文案应提到「提取短视频字幕」"
    # 不应再有「重新 ASR」字样（仅注释里允许）
    import re
    # 去掉注释（<-- ... -->）和 Python 源码式注释
    text_only = re.sub(r'<!--.*?-->', '', chunk, flags=re.DOTALL)
    # 占位 / 文案层面不应再出现「重新 ASR」
    # （按钮文字里也不应该有，前面 test 已锁）
    # 兜底：检查 .slirn-sv-muted 文本
    muted_matches = re.findall(r'<div class="slirn-sv-muted">([^<]*)</div>', chunk)
    for txt in muted_matches:
        assert "重新 ASR" not in txt, f"占位文案含旧名「重新 ASR」：{txt}"


def test_textarea_placeholder_mentions_extract_subtitle():
    """隐藏 textarea 的 placeholder 必须同步改名"""
    html = _stage6_html({
        "stage3_analyze": {"status": "done", "highlights": [{"index": 1, "title": "钩子"}]},
        "stage5_coarse": {"status": "done", "highlights": [
            {"index": 1, "status": "done", "coarse_mp4": "tasks/t1/short_video/p1/stage4/c1.mp4"},
        ]},
    })
    chunk = _s6_chunk(html)
    # textarea placeholder
    import re
    ta_match = re.search(
        r'<textarea[^>]*class="slirn-stage5-srt"[^>]*placeholder="([^"]*)"',
        chunk,
    )
    assert ta_match, "找不到 stage5-srt textarea"
    placeholder = ta_match.group(1)
    assert "提取短视频字幕" in placeholder, f"textarea placeholder 应提到新按钮名：{placeholder}"
    assert "重新 ASR" not in placeholder, f"textarea placeholder 不应再含「重新 ASR」：{placeholder}"


def test_js_toast_text_uses_extract_subtitle():
    """JS 端 stage5Asr 的 toast 文案必须用新按钮名"""
    js_path = (FUNCLIP_ROOT / "slirn_home" / "static" / "short_video.js").read_text(
        encoding="utf-8")
    # 新 toast 文案
    assert "提取短视频字幕 中…" in js_path, (
        "JS toast 必须用「提取短视频字幕 中…」（按钮改名）"
    )
    # 旧 toast 必须消失
    assert "toast('重新 ASR 中…')" not in js_path, (
        "JS toast 不能再有「重新 ASR 中…」"
    )


def test_js_save_toast_uses_extract_subtitle():
    """JS 端 stage5Save 的错误 toast 也必须同步改名"""
    js_path = (FUNCLIP_ROOT / "slirn_home" / "static" / "short_video.js").read_text(
        encoding="utf-8")
    # 「请先编辑或重新 ASR」已改为「请先编辑或提取字幕」
    assert "请先编辑或提取字幕" in js_path, (
        "JS save 错误 toast 应同步为「请先编辑或提取字幕」"
    )
    assert "请先编辑或重新 ASR" not in js_path, (
        "JS save 错误 toast 不应再有「重新 ASR」"
    )
