"""优化字幕行渲染测试 — REQ-20260918-054。

验证 _line_html 渲染的 .slirn-opt-row 包含 data-start-ms 和 data-end-ms，
供前端 timeupdate 按 [start, end) 命中行。
"""
from __future__ import annotations

import sys
from pathlib import Path

FUNCLIP_ROOT = Path(__file__).resolve().parent.parent

if str(FUNCLIP_ROOT) not in sys.path:
    sys.path.insert(0, str(FUNCLIP_ROOT))


def _make_mgr(tmp_path: Path):
    from tasklib import TaskManager

    video = tmp_path / "lecture.mp4"
    video.write_bytes(b"fake-video")
    return TaskManager(tmp_path), video


def _call_line_html(seg: dict, occs_by_seg: dict[str, list[dict]] | None = None):
    """直接调 _line_html，传入 seg + 字典化的 occs_by_seg。

    _line_html 是 _render_optimize_zone 的内嵌函数，外部拿不到。改用：
    通过 _render_optimize_zone 渲染整块 HTML，再正则抠出单个 row。
    """
    import re
    from slirn_home import compose_service, optimize_service
    from slirn_home.app import _render_optimize_zone

    # 用一个临时任务跑 _render_optimize_zone
    return None, _render_optimize_zone, re


def test_line_html_emits_data_end_ms(tmp_path: Path):
    """_line_html 渲染 .slirn-opt-row 时必须输出 data-end-ms（REQ-20260918-054）。"""
    import json
    import re
    from slirn_home import optimize_service as osvc

    mgr, video = _make_mgr(tmp_path)
    t = mgr.create(name="t", original_video=video)

    # 写一个 optimize_subtitle.json（让 _render_optimize_zone 走状态 B）
    outputs_dir = mgr.tasks_dir / t.task_id / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "version": 1,
        "model": "test-model",
        "saved_at": "2026-09-18T10:00:00",
        "segments": [
            {"i": 1, "start_ms": 0, "end_ms": 2000, "start": "00:00:00,000",
             "end": "00:00:02,000", "text": "今天讲一下神精网络"},
            {"i": 2, "start_ms": 3000, "end_ms": 5000, "start": "00:00:03,000",
             "end": "00:00:05,000", "text": "爱在很多行业都有应用"},
            {"i": 3, "start_ms": 6000, "end_ms": 8500, "start": "00:00:06,000",
             "end": "00:00:08,500", "text": "神经网洛这个词很形象"},
        ],
        "occurrences": [
            {"occ_id": 101, "seg": 1, "pos": 100, "before": "神精网络",
             "after": "神经网络", "reason": "术语误写", "applied": True},
        ],
        "words": [
            {"before": "神精网络", "after": "神经网络", "count": 1, "done": 0},
        ],
    }
    (outputs_dir / "optimize_subtitle.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8")

    # 写粗剪成片（_render_optimize_zone 第一道 gate）
    (outputs_dir / "rough_compose.mp4").write_bytes(b"fake-mp4")

    # 调用 zone 渲染
    from slirn_home.app import _render_optimize_zone
    html = _render_optimize_zone(t.task_id, t, mgr)

    # 抠出所有 .slirn-opt-row 行
    rows = re.findall(r'<div[^>]*class="slirn-opt-row[^"]*"[^>]*>', html)
    assert len(rows) >= 3, f"应该有 ≥3 行，实际 {len(rows)}"

    for idx, row in enumerate(rows):
        assert 'data-start-ms=' in row, f"行 {idx} 缺 data-start-ms: {row}"
        assert 'data-end-ms=' in row, (
            f"行 {idx} 缺 data-end-ms（REQ-20260918-054 验证）: {row}")

    # 端点值核对：每行的 data-end-ms 必须等于对应 seg.end_ms
    expected_ends = {0: 2000, 1: 5000, 2: 8500}
    for idx, row in enumerate(rows):
        m = re.search(r'data-end-ms="(\d+)"', row)
        assert m, f"行 {idx} 缺 data-end-ms attr"
        actual = int(m.group(1))
        assert actual == expected_ends[idx], (
            f"行 {idx} data-end-ms={actual} ≠ 期望 {expected_ends[idx]}")


def test_line_html_includes_data_words(tmp_path: Path):
    """回归：data-words 不被误改（REQ-20260918-052 等用）。"""
    import json
    import re
    from slirn_home.app import _render_optimize_zone

    mgr, video = _make_mgr(tmp_path)
    t = mgr.create(name="t", original_video=video)
    outputs_dir = mgr.tasks_dir / t.task_id / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "version": 1, "model": "x", "saved_at": "2026-09-18T10:00:00",
        "segments": [
            {"i": 1, "start_ms": 0, "end_ms": 2000, "start": "00:00:00,000",
             "end": "00:00:02,000", "text": "今天讲一下神精网络"},
        ],
        "occurrences": [
            {"occ_id": 101, "seg": 1, "pos": 100, "before": "神精网络",
             "after": "神经网络", "reason": "术语误写", "applied": True},
        ],
        "words": [
            {"before": "神精网络", "after": "神经网络", "count": 1, "done": 0},
        ],
    }
    (outputs_dir / "optimize_subtitle.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8")
    (outputs_dir / "rough_compose.mp4").write_bytes(b"fake-mp4")

    html = _render_optimize_zone(t.task_id, t, mgr)
    rows = re.findall(r'<div[^>]*class="slirn-opt-row[^"]*"[^>]*>', html)
    assert len(rows) >= 1
    # data-words 仍存在 + 包含该行的词
    assert 'data-words=' in rows[0]
    assert '神经网络' in rows[0]
