"""REQ-20260916-018 — compose_service（上游 VideoClipper 方法）单元测试。

仅覆盖纯函数与签名（避免触动上游 funclip/ + moviepy）。端到端合成走
work/REQ-20260916-018-upstream-compose/_service_test.py。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from slirn_home import compose_service

# ---- merge_intervals_ms（毫秒整数版，传给上游的合并语义） ----

def test_merge_intervals_ms_consecutive():
    """相邻（同 end / start）合并；空隙 <1ms 也合并。"""
    assert compose_service.merge_intervals_ms([]) == []
    assert compose_service.merge_intervals_ms([(0, 1000)]) == [[0, 1000]]
    assert compose_service.merge_intervals_ms([(0, 1000), (1000, 2000)]) == [[0, 2000]]
    assert compose_service.merge_intervals_ms([(0, 1000), (500, 2000)]) == [[0, 2000]]
    # 1ms 缝隙不合并（边界外）：保留两段
    assert compose_service.merge_intervals_ms([(0, 1000), (1001, 2000)]) == [
        [0, 1000], [1001, 2000],
    ]


def test_merge_intervals_ms_unsorted():
    """输入乱序也按起始升序合并。"""
    out = compose_service.merge_intervals_ms([(5000, 6000), (0, 1000), (1000, 2000)])
    assert out == [[0, 2000], [5000, 6000]], out


# ---- build_sentences（保留行 → 上游 sentences 格式） ----

def test_build_sentences_whole_line_single_token():
    """整行单 token（行即区间）— 上游按 token 切字幕正确解析的最小单元。"""
    lines = [{"id": "10", "start_ms": 950, "end_ms": 4270, "text": "神精网络"}]
    out = compose_service.build_sentences(lines)
    assert out == [{"text": "神精网络", "timestamp": [[950, 4270]]}], out


def test_build_sentences_hotword_new_text_preserved():
    """热词替换后 new_text 透传：上游拿到的是已修订文本。"""
    lines = [{"id": "11", "start_ms": 5000, "end_ms": 8000, "text": "神经网络"}]
    out = compose_service.build_sentences(lines)
    assert out[0]["text"] == "神经网络"


def test_build_sentences_empty():
    assert compose_service.build_sentences([]) == []


# ---- merge_intervals（秒浮点版，UI 渲染仍需） ----

def test_merge_intervals_seconds():
    assert compose_service.merge_intervals([]) == []
    assert compose_service.merge_intervals([(0.0, 1.0), (1.0005, 2.0)]) == [[0.0, 2.0]]
    # 明显缝隙不合并：保留两段
    out = compose_service.merge_intervals([(0.0, 1.0), (1.5, 2.0)])
    assert out == [[0.0, 1.0], [1.5, 2.0]], out


# ---- rough_compose_path（产物固定名） ----

def test_rough_compose_path_fixed_name():
    p = compose_service.rough_compose_path(Path("tasks/001/outputs"))
    assert p.name == "rough_compose.mp4"
    # Windows 上 Path("tasks/001/outputs") 的 str 是反斜杠；仅断言文件名 + 父目录
    assert p.parent.name == "outputs"


# ---- compose_rough_cut 守卫（无上游依赖，仅校验前置条件） ----

def test_compose_rough_cut_missing_src(tmp_path):
    dst = tmp_path / "out.mp4"
    with pytest.raises(RuntimeError, match="源视频不存在"):
        compose_service.compose_rough_cut(tmp_path / "missing.mp4", [(0, 1000)], dst, [])


def test_compose_rough_cut_empty_intervals(tmp_path):
    src = tmp_path / "src.mp4"
    src.write_bytes(b"")
    with pytest.raises(RuntimeError, match="没有保留区间"):
        compose_service.compose_rough_cut(src, [], tmp_path / "out.mp4", [])


def test_compose_rough_cut_missing_lines(tmp_path):
    src = tmp_path / "src.mp4"
    src.write_bytes(b"")
    with pytest.raises(RuntimeError, match="缺少保留行"):
        compose_service.compose_rough_cut(src, [(0, 1000)], tmp_path / "out.mp4", [])


# ---- start_compose 重入防护（无 src/无 lines 直接抛错；job 入表后被 _run 标记 error） ----

def test_start_compose_rejects_running_task(tmp_path):
    """第二次启动同一 task_id 应返回 False（in-memory job 防重入）。"""
    src = tmp_path / "src.mp4"
    src.write_bytes(b"")  # 让 _run 立即抛错
    lines = [{"id": "1", "start_ms": 0, "end_ms": 1000, "text": "x"}]
    dst = tmp_path / "out.mp4"
    # 第一次启动（会运行后报 missing mp4 error）
    compose_service.start_compose("t1", src, [(0, 1000)], dst, lines)
    # 第二次同 tid 应立即 False
    assert compose_service.start_compose("t1", src, [(0, 1000)], dst, lines) is False


# ---- job_status 缺如/存在 ----

def test_job_status_missing():
    assert compose_service.job_status("never-existed") is None
