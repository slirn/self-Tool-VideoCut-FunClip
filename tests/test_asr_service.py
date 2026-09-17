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

    sent = {"text": "嗯我们今天", "timestamp": [[100, 300], [300, 800], [800, 1200], [1200, 1700], [1700, 2200]]}
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


# ---------- 说话人编号 + 统计（REQ-20260917-029） ----------


def test_get_model_lazy_and_reuse(monkeypatch):
    """SD 开 → 建带 spk 模型；SD 关优先复用已加载的 SD 单例（不建第二份
    paraformer）；SD 模型从未加载时 SD 关 → 建无 spk 模型（离线无 cam++ 可用）。"""
    from slirn_home import asr_service

    built = []
    monkeypatch.setattr(asr_service, "_MODEL", None)
    monkeypatch.setattr(asr_service, "_MODEL_SD", None)
    monkeypatch.setattr(
        asr_service, "_build_model", lambda with_spk: built.append(with_spk) or {"spk": with_spk}
    )

    m_sd = asr_service.get_model(sd=True)
    assert built == [True]
    assert asr_service.get_model(sd=True) is m_sd  # 复用单例
    assert asr_service.get_model(sd=False) is m_sd  # SD 单例在场 → 不建第二份
    assert built == [True]

    monkeypatch.setattr(asr_service, "_MODEL_SD", None)  # SD 模型从未加载
    m_plain = asr_service.get_model(sd=False)
    assert built == [True, False]
    assert m_plain is not m_sd
    assert asr_service.get_model(sd=False) is m_plain


def test_local_model_dir_cache_first(tmp_path, monkeypatch):
    """缓存命中 → 传本地路径（离线可加载，绕开 modelscope 联网版本核对）；未命中 → 原 id 回退下载。"""
    from slirn_home import asr_service

    ids = {
        "iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
        "damo/speech_fsmn_vad_zh-cn-16k-common-pytorch",
        "damo/speech_campplus_sv_zh-cn_16k-common",
    }
    cache = tmp_path / "hub" / "models"
    for mid in ids:
        d = cache / mid
        d.mkdir(parents=True)
        (d / "config.yaml").write_text("x", encoding="utf-8")

    monkeypatch.setattr(
        "slirn_home.asr_service.Path.home", lambda: tmp_path  # 兜底分支的基准
    )
    # modelscope 已装时走 get_modelscope_cache_dir()（实测 v1.x 返回值已含 hub 后缀）
    import modelscope.utils.file_utils as _mf

    monkeypatch.setattr(_mf, "get_modelscope_cache_dir", lambda: str(tmp_path / "hub"))

    for mid in ids:
        resolved = asr_service._local_model_dir(mid)
        assert resolved == str(cache / mid), resolved

    # 返回值不带 hub 后缀的旧形态同样兼容
    monkeypatch.setattr(_mf, "get_modelscope_cache_dir", lambda: str(tmp_path))
    assert asr_service._local_model_dir("damo/speech_fsmn_vad_zh-cn-16k-common-pytorch") == str(
        cache / "damo/speech_fsmn_vad_zh-cn-16k-common-pytorch"
    )

    # 缓存缺 config.yaml（不完整）→ 保持 id 交给 modelscope 正常下载
    missing = cache / "damo/not-cached"
    missing.mkdir(parents=True)
    assert asr_service._local_model_dir("damo/not-cached") == "damo/not-cached"
    assert asr_service._local_model_dir("iic/never-seen") == "iic/never-seen"


def test_segments_pass_spk_raw():
    """SD 分支的 sentence 带 spk（簇标签）→ 段列表透传为 spk_raw（int）。"""
    from slirn_home.asr_service import segments_from_sentences

    sents = [
        {"text": "大家好", "timestamp": [[0, 1200]], "spk": 5},
        {"text": "你好", "timestamp": [[2000, 3000]]},  # 无 spk 的段
    ]
    segs = segments_from_sentences(sents)
    assert segs[0]["spk_raw"] == 5
    assert "spk_raw" not in segs[1]


def test_assign_speaker_numbers_first_appearance():
    """簇标签无语义 → 按首次出现顺序归一为 1 起始人员编号，spk_raw 剔除。"""
    from slirn_home.asr_service import assign_speaker_numbers

    segs = [{"spk_raw": r} for r in (5, 2, 5, 7, 2)] + [{"text": "x"}]
    assign_speaker_numbers(segs)
    assert [s.get("spk") for s in segs] == [1, 2, 1, 3, 2, None]
    assert all("spk_raw" not in s for s in segs)


def test_assign_speaker_numbers_noop_without_labels():
    """无任何标签（SD 关闭）→ 段列表原样，不引入 spk 字段。"""
    from slirn_home.asr_service import assign_speaker_numbers

    segs = [{"text": "a"}, {"text": "b"}]
    assign_speaker_numbers(segs)
    assert segs == [{"text": "a"}, {"text": "b"}]


def test_speaker_stats_counts_and_order():
    from slirn_home.asr_service import speaker_stats

    segs = [{"spk": 2}, {"spk": 1}, {"spk": 2}, {"spk": 1}, {"spk": 1}, {"text": "无标签"}]
    assert speaker_stats(segs) == [
        {"spk": 1, "sentences": 3},
        {"spk": 2, "sentences": 2},
    ]
    assert speaker_stats([{"text": "x"}]) == []


def test_segments_to_srt_spk_label_and_blank_lines():
    """有 spk → 序号行带 spk{N}；无 spk → 纯序号；块间空行分隔、单尾换行。"""
    from slirn_home.asr_service import segments_to_srt

    segs = [
        {"i": 1, "start": "00:00:00,000", "end": "00:00:01,200", "text": "第一句", "spk": 1},
        {"i": 2, "start": "00:00:02,000", "end": "00:00:03,500", "text": "第二句"},
    ]
    srt = segments_to_srt(segs)
    assert srt == ("1  spk1\n00:00:00,000 --> 00:00:01,200\n第一句\n\n2\n00:00:02,000 --> 00:00:03,500\n第二句\n")


def test_start_job_sd_writes_speakers_meta(fake_env):
    """SD 开：json 每段带归一 spk + speakers 统计，srt 序号行带 spk 标签。"""
    asr_service, video, outputs = fake_env

    def fake_run(video_path, hotword_str, sd=True):
        assert sd is True
        return {
            "sentences": [
                {"text": "大家好", "timestamp": [[0, 1200]], "spk": 5},
                {"text": "你好呀", "timestamp": [[2000, 3500]], "spk": 2},
                {"text": ["讲", "解"], "timestamp": [[4000, 6000], [6000, 8000]], "spk": 5},
            ]
        }

    asr_service._run_recognition = fake_run
    assert asr_service.start_job("T-SD1", video, [], outputs, sd=True) is True
    j = _wait_job(asr_service, "T-SD1")
    assert j["state"] == "done"
    assert j["segments_count"] == 3

    meta = json.loads((outputs / "subtitle.json").read_text(encoding="utf-8"))
    assert meta["sd"] is True
    assert [s["spk"] for s in meta["segments"]] == [1, 2, 1]  # 首次出现顺序
    assert meta["speakers"] == {
        "count": 2,
        "stats": [{"spk": 1, "sentences": 2}, {"spk": 2, "sentences": 1}],
    }
    srt = (outputs / "subtitle.srt").read_text(encoding="utf-8-sig")
    assert "1  spk1\n" in srt and "2  spk2\n" in srt and "3  spk1\n" in srt


def test_start_job_sd_off_matches_legacy(fake_env):
    """SD 关：无 spk 字段、无 speakers meta、srt 纯序号（与旧行为一致）。"""
    asr_service, video, outputs = fake_env

    def fake_run(video_path, hotword_str, sd=True):
        assert sd is False
        return {
            "sentences": [
                {"text": "大家好", "timestamp": [[0, 1200]]},
                {"text": "你好呀", "timestamp": [[2000, 3500]]},
            ]
        }

    asr_service._run_recognition = fake_run
    assert asr_service.start_job("T-SD0", video, [], outputs, sd=False) is True
    _wait_job(asr_service, "T-SD0")

    meta = json.loads((outputs / "subtitle.json").read_text(encoding="utf-8"))
    assert meta["sd"] is False
    assert all("spk" not in s for s in meta["segments"])
    assert "speakers" not in meta
    srt = (outputs / "subtitle.srt").read_text(encoding="utf-8-sig")
    assert "spk" not in srt
    assert srt.startswith("1\n00:00:00,000 --> 00:00:01,200\n大家好\n\n2\n")


# ---------- job 状态机（monkeypatch 假识别器） ----------


@pytest.fixture
def fake_env(tmp_path: Path, monkeypatch):
    """假视频 + 假识别 + 临时 job 表隔离。"""
    from slirn_home import asr_service

    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake-video")
    outputs = tmp_path / "outputs"

    def fake_run(video_path: str, hotword_str: str, sd: bool = True):
        assert Path(video_path).exists()
        state = {
            "sentences": [
                {"text": "第一句", "timestamp": [[0, 1200]]},
                {"text": ["第二", "句"], "timestamp": [[2000, 3500]]},
            ]
        }
        return state

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
        "T-1",
        video,
        ["热词A", "热词B"],
        outputs,
        source="segment",
        base_offset_ms=65_000,
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

    def slow_run(video_path, hotword_str, sd=True):
        started.set()
        time.sleep(0.6)
        return {"sentences": []}

    asr_service._run_recognition = slow_run
    assert asr_service.start_job("T-2", video, [], outputs) is True
    started.wait(5)
    assert asr_service.start_job("T-2", video, [], outputs) is False
    _wait_job(asr_service, "T-2")


def test_start_job_error_state(fake_env):
    asr_service, video, outputs = fake_env

    def boom(video_path, hotword_str, sd=True):
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
    (outputs / "subtitle.json").write_text(
        json.dumps(
            {
                "version": 1,
                "source": "segment",
                "created_at": "2026-09-15T12:00:00",
                "segments": [
                    {
                        "i": 1,
                        "start_ms": 0,
                        "end_ms": 3200,
                        "start": "00:00:00,000",
                        "end": "00:00:03,200",
                        "text": "第一句台词",
                    },
                    {
                        "i": 2,
                        "start_ms": 3500,
                        "end_ms": 6000,
                        "start": "00:00:03,500",
                        "end": "00:00:06,000",
                        "text": "<b>第二句含HTML</b>",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return m, t.task_id


def test_render_detail_with_subtitle_list(detail_mgr):
    from slirn_home.app import _render_task_detail

    m, tid = detail_mgr
    html = _render_task_detail(tid, m)
    assert "slirn-sub-list" in html
    assert "slirn-sub-player" in html
    assert 'data-start-ms="3500"' in html
    assert 'data-action="gen-subtitle"' in html
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
        asr_service,
        "job_status",
        lambda tid_: {
            "state": "running",
            "stage": "识别中",
            "error": None,
            "started_at": time.time(),
            "finished_at": None,
            "segments_count": 0,
        },
    )
    html = _render_task_detail(tid, m)
    assert 'data-state="running"' in html


def test_render_detail_with_speaker_badges_and_stats(tmp_path: Path):
    """SD 字幕（带 spk + speakers）→ 行徽标「人员N」+ 统计行（REQ-20260917-029）。"""
    from tasklib import TaskManager

    video = tmp_path / "talk.mp4"
    video.write_bytes(b"fake")
    m = TaskManager(tmp_path)
    t = m.create(name="双人对话", original_video=video)
    outputs = tmp_path / "tasks" / t.task_id / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    (outputs / "subtitle.json").write_text(
        json.dumps(
            {
                "version": 2,
                "source": "original",
                "created_at": "2026-09-17T12:00:00",
                "speakers": {"count": 2, "stats": [{"spk": 1, "sentences": 2}, {"spk": 2, "sentences": 1}]},
                "segments": [
                    {
                        "i": 1,
                        "start_ms": 0,
                        "end_ms": 3200,
                        "start": "00:00:00,000",
                        "end": "00:00:03,200",
                        "text": "讲师开场",
                        "spk": 1,
                    },
                    {
                        "i": 2,
                        "start_ms": 3500,
                        "end_ms": 6000,
                        "start": "00:00:03,500",
                        "end": "00:00:06,000",
                        "text": "学员提问",
                        "spk": 2,
                    },
                    {
                        "i": 3,
                        "start_ms": 6100,
                        "end_ms": 9000,
                        "start": "00:00:06,100",
                        "end": "00:00:09,000",
                        "text": "讲师回答",
                        "spk": 1,
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    from slirn_home.app import _render_task_detail

    html = _render_task_detail(t.task_id, m)
    # 徽标：类名（6 色循环）+ 文本
    assert 'class="slirn-sub-spk spk-c1">人员1<' in html
    assert 'class="slirn-sub-spk spk-c2">人员2<' in html
    assert "has-spk" in html
    # 统计行：人数 + 每人句数
    assert "2 位说话人" in html
    assert "人员1 2 句" in html and "人员2 1 句" in html
    # 开关存在且默认开
    assert 'id="slirn-sd-switch" checked' in html


def test_render_detail_legacy_subtitle_without_spk(detail_mgr):
    """旧 subtitle.json（无 spk/speakers）→ 无徽标无统计，不回归。"""
    from slirn_home.app import _render_task_detail

    m, tid = detail_mgr
    html = _render_task_detail(tid, m)
    assert "slirn-sub-spk" not in html
    assert "位说话人" not in html
    assert "has-spk" not in html


def test_resolve_task_video_prefers_segment(tmp_path: Path):
    from tasklib import TaskManager

    from slirn_home.app import _resolve_task_video

    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    m = TaskManager(tmp_path)
    t = m.create(name="seg", original_video=video, segment=None)
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
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.5",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=64x64:d=0.5",
            "-shortest",
            "-y",
            str(vid),
        ],
        check=True,
        capture_output=True,
    )
    assert has_audio_track(vid) is True
    silent = tmp_path / "b.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.5", "-y", str(silent)],
        check=True,
        capture_output=True,
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
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.5",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=64x64:d=0.5",
            "-shortest",
            "-y",
            str(vid),
        ],
        check=True,
        capture_output=True,
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
