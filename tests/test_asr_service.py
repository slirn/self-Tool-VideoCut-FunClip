"""测试 slirn_home.asr_service + 详情页字幕区渲染 — REQ-20260915-001。

识别链路本身（funasr/moviepy）不进单测 — `_run_recognition` 被 monkeypatch 成
假识别器，测 job 状态机、落盘格式和渲染。
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

import pytest

FUNCLIP_ROOT = Path(__file__).resolve().parent.parent
SLIRN_STANDALONE = FUNCLIP_ROOT.parent / "slirn-standalone"

if str(FUNCLIP_ROOT) not in sys.path:
    sys.path.insert(0, str(FUNCLIP_ROOT))
if SLIRN_STANDALONE.exists() and str(SLIRN_STANDALONE) not in sys.path:
    sys.path.insert(0, str(SLIRN_STANDALONE))


# ---------- segments_from_sentences（纯函数） ----------

def test_segments_from_str_text():
    """未拆分句：text 是字符串 → 一段，起止取 timestamp 首尾。"""
    from slirn_home.asr_service import segments_from_sentences

    sent = {"text": "大家好", "timestamp": [[1000, 1500], [1500, 2200]]}
    segs = segments_from_sentences([sent])
    assert len(segs) == 1
    assert segs[0]["start_ms"] == 1000
    assert segs[0]["end_ms"] == 2200
    assert segs[0]["i"] == 1
    assert segs[0]["text"] == "大家好"


def test_segments_from_token_list_text():
    """拆分句（_split_long_sentence 产物）：text 是 token 列表 → 中文拼接无空格。"""
    from slirn_home.asr_service import segments_from_sentences

    sent = {"text": ["神经", "网络"], "timestamp": [[5000, 6000], [6000, 7000]]}
    segs = segments_from_sentences([sent])
    assert segs[0]["text"] == "神经网络"
    assert segs[0]["start"] == "00:00:05,000"
    assert segs[0]["end"] == "00:00:07,000"


def test_segments_english_words_space_joined():
    from slirn_home.asr_service import segments_from_sentences

    sent = {"text": ["hello", "world"], "timestamp": [[0, 500], [500, 900]]}
    segs = segments_from_sentences([sent])
    assert segs[0]["text"] == "hello world"


def test_segments_skip_missing_timestamp():
    """无 timestamp 的条目跳过（与上游 generate_srt 行为一致）。"""
    from slirn_home.asr_service import segments_from_sentences

    segs = segments_from_sentences([{"text": "x"}, {"text": "y", "timestamp": [[0, 100]]}])
    assert len(segs) == 1
    assert segs[0]["i"] == 1  # 序号连续，不留洞


def test_segments_indexing_across_rows():
    from slirn_home.asr_service import segments_from_sentences

    sents = [
        {"text": "a", "timestamp": [[0, 100]]},
        {"text": "b", "timestamp": [[200, 300]]},
    ]
    segs = segments_from_sentences(sents)
    assert [s["i"] for s in segs] == [1, 2]


# ---------- 字级时间戳（tokens + token_ts，REQ-20260916-008） ----------

def test_segments_keep_tokens_when_aligned():
    """timestamp 与 tokenize(text) 等长 → 保存 tokens/token_ts（切分修剪对齐用）。"""
    from slirn_home.asr_service import segments_from_sentences

    sent = {"text": "嗯我们今天",
            "timestamp": [[100, 300], [300, 800], [800, 1200], [1200, 1700], [1700, 2200]]}
    segs = segments_from_sentences([sent])
    assert segs[0]["tokens"] == ["嗯", "我", "们", "今", "天"]
    assert segs[0]["token_ts"] == [[100, 300], [300, 800], [800, 1200], [1200, 1700], [1700, 2200]]

    # token 列表文本（拆分句产物）同样保存
    sent2 = {"text": ["神经", "网络"], "timestamp": [[0, 500], [500, 900]]}
    segs2 = segments_from_sentences([sent2])
    assert segs2[0]["tokens"] == ["神经", "网络"]


def test_segments_drop_tokens_when_mismatch():
    """timestamp 与 token 数不等长 → 视为不可信，不保存（该段切分时降级整段）。"""
    from slirn_home.asr_service import segments_from_sentences

    sent = {"text": "大家好呀", "timestamp": [[0, 100], [100, 300]]}  # 4 字 vs 2 对
    segs = segments_from_sentences([sent])
    assert "tokens" not in segs[0] and "token_ts" not in segs[0]

    # 中间对畸形（缺 end）：Text2SRT 只碰首尾不受影响，token_ts 归一化失败 → 不保存
    sent2 = {"text": "好的呀", "timestamp": [[0, 500], [900], [1000, 1200]]}
    segs2 = segments_from_sentences([sent2])
    assert segs2[0]["text"] == "好的呀"
    assert "token_ts" not in segs2[0]


# ---------- job 状态机（monkeypatch 假识别器） ----------

@pytest.fixture
def fake_env(tmp_path: Path, monkeypatch):
    """假视频 + 假识别 + 临时 job 表隔离。"""
    from slirn_home import asr_service

    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake-video")
    outputs = tmp_path / "outputs"

    def fake_run(video_path: str, hotword_str: str):
        assert Path(video_path).exists()
        state = {"sentences": [
            {"text": "第一句", "timestamp": [[0, 1200]]},
            {"text": ["第二", "句"], "timestamp": [[2000, 3500]]},
        ]}
        return "1\n00:00:00,000 --> 00:00:01,200\n第一句\n", state

    monkeypatch.setattr(asr_service, "_run_recognition", fake_run)
    monkeypatch.setattr(asr_service, "_JOBS", {})
    return asr_service, video, outputs


def _wait_job(asr_service, tid: str, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        j = asr_service.job_status(tid)
        if j and j["state"] in ("done", "error"):
            return j
        time.sleep(0.05)
    pytest.fail("job 未在超时内完成")


def test_start_job_writes_outputs_and_calls_on_success(fake_env):
    asr_service, video, outputs = fake_env
    got: list[list[dict]] = []
    ok = asr_service.start_job(
        "T-1", video, ["热词A", "热词B"], outputs,
        source="segment", base_offset_ms=65_000,
        on_success=lambda segs: got.append(segs),
    )
    assert ok is True
    j = _wait_job(asr_service, "T-1")
    assert j["state"] == "done"
    assert j["segments_count"] == 2

    # srt 落盘（utf-8-sig）
    srt = (outputs / "subtitle.srt").read_text(encoding="utf-8-sig")
    assert "第一句" in srt
    # json 落盘 + 元数据
    meta = json.loads((outputs / "subtitle.json").read_text(encoding="utf-8"))
    assert meta["source"] == "segment"
    assert meta["base_offset_ms"] == 65_000
    assert meta["video_url"] == "/slirn/api/video/T-1"
    assert len(meta["segments"]) == 2
    assert meta["segments"][0]["text"] == "第一句"
    # on_success 收到段列表
    assert got and len(got[0]) == 2


def test_start_job_single_flight(fake_env):
    """running 中重复 start → False，不起第二个线程。"""
    asr_service, video, outputs = fake_env
    import threading

    started = threading.Event()

    def slow_run(video_path, hotword_str):
        started.set()
        time.sleep(0.6)
        return "", {"sentences": []}

    asr_service._run_recognition = slow_run
    assert asr_service.start_job("T-2", video, [], outputs) is True
    started.wait(5)
    assert asr_service.start_job("T-2", video, [], outputs) is False
    _wait_job(asr_service, "T-2")


def test_start_job_error_state(fake_env):
    asr_service, video, outputs = fake_env

    def boom(video_path, hotword_str):
        raise RuntimeError("模型加载失败")

    asr_service._run_recognition = boom
    assert asr_service.start_job("T-3", video, [], outputs) is True
    j = _wait_job(asr_service, "T-3")
    assert j["state"] == "error"
    assert "模型加载失败" in j["error"]


def test_load_subtitle_missing_and_corrupt(tmp_path: Path):
    from slirn_home.asr_service import load_subtitle

    assert load_subtitle(tmp_path) is None
    (tmp_path / "subtitle.json").write_text("{broken", encoding="utf-8")
    assert load_subtitle(tmp_path) is None


# ---------- 详情页渲染 ----------

@pytest.fixture
def detail_mgr(tmp_path: Path):
    from tasklib import TaskManager

    video = tmp_path / "lecture.mp4"
    video.write_bytes(b"fake")
    m = TaskManager(tmp_path)
    t = m.create(name="带字幕任务", original_video=video, hotwords=["FunASR", "热词"])
    # 预置一份已生成的 subtitle.json
    outputs = tmp_path / "tasks" / t.task_id / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    (outputs / "subtitle.json").write_text(json.dumps({
        "version": 1, "source": "segment", "created_at": "2026-09-15T12:00:00",
        "segments": [
            {"i": 1, "start_ms": 0, "end_ms": 3200,
             "start": "00:00:00,000", "end": "00:00:03,200", "text": "第一句台词"},
            {"i": 2, "start_ms": 3500, "end_ms": 6000,
             "start": "00:00:03,500", "end": "00:00:06,000", "text": "<b>第二句含HTML</b>"},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    return m, t.task_id


def test_render_detail_with_subtitle_list(detail_mgr):
    from slirn_home.app import _render_task_detail

    m, tid = detail_mgr
    html = _render_task_detail(tid, m)
    assert "slirn-sub-list" in html
    assert "slirn-sub-player" in html
    assert 'data-start-ms="3500"' in html
    assert "data-action=\"gen-subtitle\"" in html
    # HTML 转义
    assert "<b>第二句含HTML</b>" not in html
    assert "&lt;b&gt;第二句含HTML&lt;/b&gt;" in html


def test_render_detail_without_subtitle_shows_start_button(mgr_tmp=None):
    import tempfile

    from tasklib import TaskManager
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        video = tmp / "v.mp4"
        video.write_bytes(b"x")
        m = TaskManager(tmp)
        t = m.create(name="无字幕", original_video=video)
        from slirn_home.app import _render_task_detail
        html = _render_task_detail(t.task_id, m)
        assert "🎙 生成字幕" in html
        assert "slirn-sub-list" not in html  # 无列表


def test_render_detail_running_job_state(detail_mgr, monkeypatch):
    from slirn_home import asr_service
    from slirn_home.app import _render_task_detail

    m, tid = detail_mgr
    monkeypatch.setattr(
        asr_service, "job_status",
        lambda tid_: {"state": "running", "stage": "识别中", "error": None,
                      "started_at": time.time(), "finished_at": None, "segments_count": 0},
    )
    html = _render_task_detail(tid, m)
    assert 'data-state="running"' in html


def test_resolve_task_video_prefers_segment(tmp_path: Path):
    from tasklib import TaskManager

    from slirn_home.app import _resolve_task_video

    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    m = TaskManager(tmp_path)
    t = m.create(name="seg", original_video=video,
                 segment=None)
    # 直接构造带 segment 的任务（不走 create 的文件搬运）
    from tasklib.models import TaskStatus, TimeSegment
    seg_path = tmp_path / "seg.mp4"
    seg_path.write_bytes(b"s")
    t.segment = TimeSegment(start="00:00:10", end="00:01:00", path=seg_path)
    p, label = _resolve_task_video(t)
    assert p == seg_path
    assert "截取段" in label
    # segment 文件不存在 → 回退原视频
    seg_path.unlink()
    p2, label2 = _resolve_task_video(t)
    assert p2 == video
    assert "原视频" in label2


# ---------- has_audio_track（依赖 ffprobe，环境没有就跳过） ----------

@pytest.mark.skipif(not shutil.which("ffprobe"), reason="需要 ffprobe")
def test_has_audio_track_real(tmp_path: Path):
    import subprocess

    from slirn_home.asr_service import has_audio_track

    vid = tmp_path / "a.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.5",
         "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.5",
         "-shortest", "-y", str(vid)],
        check=True, capture_output=True,
    )
    assert has_audio_track(vid) is True
    silent = tmp_path / "b.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.5",
         "-y", str(silent)],
        check=True, capture_output=True,
    )
    assert has_audio_track(silent) is False


# ---------- _extract_mono_wav_16k（REQ-20260917-023：长视频识别内存修复） ----------

@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="需要 ffmpeg")
def test_extract_mono_wav_16k_real(tmp_path: Path):
    """抽轨产物必须是 16k 单声道 PCM16，时长≈源（绕开上游立体声大块分配）。"""
    import subprocess
    import wave

    from slirn_home.asr_service import _extract_mono_wav_16k

    vid = tmp_path / "a.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.5",
         "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.5",
         "-shortest", "-y", str(vid)],
        check=True, capture_output=True,
    )
    wav = tmp_path / "a.wav"
    _extract_mono_wav_16k(str(vid), wav)
    with wave.open(str(wav), "rb") as w:
        assert w.getframerate() == 16000
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert abs(w.getnframes() / 16000 - 0.5) < 0.05


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="需要 ffmpeg")
def test_extract_mono_wav_16k_bad_input(tmp_path: Path):
    """非视频输入 → 清晰报错（不静默产出空 wav）。"""
    from slirn_home.asr_service import _extract_mono_wav_16k

    bad = tmp_path / "notavideo.mp4"
    bad.write_bytes(b"garbage bytes")
    with pytest.raises(RuntimeError, match="音频提取失败"):
        _extract_mono_wav_16k(str(bad), tmp_path / "x.wav")


def test_ensure_segment_file_recuts_when_missing(tmp_path: Path, monkeypatch):
    """自愈：segment 文件缺失但原视频在 → 重新截取；已存在 → 不重复截。"""
    from tasklib import TaskManager
    from tasklib import video as tvideo
    from tasklib.models import TimeSegment

    from slirn_home.app import _ensure_segment_file

    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    m = TaskManager(tmp_path)
    t = m.create(name="selfheal", original_video=video)
    seg_path = tmp_path / "tasks" / t.task_id / "raw_input" / "seg.mp4"
    t.segment = TimeSegment(start="00:00:05", end="00:00:20", path=seg_path)

    calls: list[tuple] = []

    def fake_cut(src, dst, s, e):
        calls.append((s, e))
        Path(dst).write_bytes(b"cut")

    monkeypatch.setattr(tvideo, "cut_video", fake_cut)

    assert _ensure_segment_file(t) is True
    assert calls == [("00:00:05", "00:00:20")]  # 重截一次
    assert seg_path.exists()

    calls.clear()
    assert _ensure_segment_file(t) is True  # 幂等：存在不再截
    assert not calls


def test_ensure_segment_file_no_segment_or_no_source(tmp_path: Path):
    """无 segment / 原视频也丢了 → False。"""
    from tasklib import TaskManager

    from slirn_home.app import _ensure_segment_file

    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    m = TaskManager(tmp_path)
    t = m.create(name="plain", original_video=video)
    assert _ensure_segment_file(t) is False  # 无 segment

    from tasklib.models import TimeSegment
    t.segment = TimeSegment(start="00:00:05", end="00:00:20", path=tmp_path / "no" / "seg.mp4")
    t.original_video_source = tmp_path / "gone.mp4"  # 原视频也没了
    assert _ensure_segment_file(t) is False
