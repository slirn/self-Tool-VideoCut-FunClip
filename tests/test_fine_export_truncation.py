# -*- coding: utf-8 -*-
"""REQ-20260923-NNN：精剪导出「静默截断」修复的单元测试。

事故（20260916-004 反复复现）：69 分钟源导出只剩 18 分钟，ffmpeg exit 0、
moov 完整、双流可播 — 服务据 returncode==0 误标成功。根因是滤镜图
「无限视频流（封面/背景 -loop 1）+ 有限音频流」组合下 -shortest 在 I/O
拥塞时提前判音频 EOF。修复 = 显式 -t 上界 + 完成后 ffprobe 双流时长复核
+ 跨任务串行门 + stderr 尾部留痕。本文件覆盖四层中的可单测部分。
"""
import io
import json
import threading
import time
from pathlib import Path

import pytest


# ---------- 夹具（复刻 test_workbench._make_mgr 的最小形态） ----------

def _make_mgr(tmp_path: Path):
    from tasklib import TaskManager

    video = tmp_path / "lecture.mp4"
    video.write_bytes(b"fake-video")
    return TaskManager(tmp_path), video


def _save_min_fc(m, tid: str, video: Path):
    from slirn_home.app import _save_fine_compose

    _save_fine_compose(m, tid, {
        "materials": {"video": {"path": str(video.name), "type": "video",
                                "source": "upload"}},
        "layout": {"video": {"x": 0, "y": 0, "scale": 1.0, "enabled": True}},
    })


class _FakeResult:
    def __init__(self, stdout=""):
        self.returncode = 0
        self.stdout = stdout
        self.stderr = ""


def _make_fake_run(probe_format="0", streams_json=None, captured=None):
    """按命令特征分流的 subprocess.run 桩。

    - ffprobe format=duration → probe_format（源时长秒）
    - ffprobe stream=codec_type,duration → streams_json（产物双流时长）
    - ffmpeg 渲染 → returncode 0
    """
    def _fake(cmd, **kw):
        if captured is not None:
            if "-filter_complex" in cmd:
                captured["filter"] = cmd[cmd.index("-filter_complex") + 1]
                # 只记 ffmpeg 渲染命令（输入侧自带 -t duration，输出侧的
                # -t 上界在 -movflags 之前，用倒序找避免误取输入侧的）
                captured["out_t"] = None
                for _i in range(len(cmd) - 1, -1, -1):
                    if cmd[_i] == "-t":
                        captured["out_t"] = cmd[_i + 1]
                        break
            captured["cmd"] = list(cmd)
        joined = " ".join(str(c) for c in cmd)
        if "format=duration" in joined:
            return _FakeResult(probe_format)
        if "stream=codec_type,duration" in joined:
            return _FakeResult(json.dumps({"streams": streams_json or []}))
        return _FakeResult("")
    return _fake


def _assert_out_t(captured, value):
    """断言输出级 -t 的值（cmd 里最后一个 -t；输入侧预览还有个 -t duration）。"""
    assert captured.get("out_t") == value


# ---------- _verify_render_duration：截断校验 ----------

def test_verify_duration_skips_when_expected_unknown(tmp_path, monkeypatch):
    """expected 未知（None/0）→ 跳过校验（返回空串）。"""
    from slirn_home.app import _verify_render_duration

    f = tmp_path / "out.mp4"
    f.write_bytes(b"x")
    assert _verify_render_duration(f, None) == ""
    assert _verify_render_duration(f, 0) == ""


def test_verify_duration_skips_when_file_missing(tmp_path, monkeypatch):
    """产物不存在 → 告警跳过（不误伤 mock/网络盘延迟场景）。"""
    from slirn_home.app import _verify_render_duration

    assert _verify_render_duration(tmp_path / "nope.mp4", 100.0) == ""


def test_verify_duration_flags_short_video_stream(tmp_path, monkeypatch):
    """视频流时长 < 预期 98% → 返回截断描述。"""
    import subprocess
    from slirn_home.app import _verify_render_duration

    f = tmp_path / "out.mp4"
    f.write_bytes(b"x")
    monkeypatch.setattr(subprocess, "run", _make_fake_run(
        streams_json=[{"codec_type": "video", "duration": "5.0"},
                      {"codec_type": "audio", "duration": "9.9"}]))
    msg = _verify_render_duration(f, 10.0)
    assert msg != ""
    assert "视频流" in msg and "截断" in msg and "重试" in msg


def test_verify_duration_flags_short_audio_stream(tmp_path, monkeypatch):
    """音频流先短（-shortest 事故的真实形态）→ 截断描述。"""
    import subprocess
    from slirn_home.app import _verify_render_duration

    f = tmp_path / "out.mp4"
    f.write_bytes(b"x")
    monkeypatch.setattr(subprocess, "run", _make_fake_run(
        streams_json=[{"codec_type": "video", "duration": "4136.7"},
                      {"codec_type": "audio", "duration": "1088.0"}]))
    msg = _verify_render_duration(f, 4136.8)
    assert "音频流" in msg and "截断" in msg


def test_verify_duration_passes_full_streams(tmp_path, monkeypatch):
    """双流 ≥ 98% → 通过（返回空串）。"""
    import subprocess
    from slirn_home.app import _verify_render_duration

    f = tmp_path / "out.mp4"
    f.write_bytes(b"x")
    monkeypatch.setattr(subprocess, "run", _make_fake_run(
        streams_json=[{"codec_type": "video", "duration": "4136.7"},
                      {"codec_type": "audio", "duration": "4136.6"}]))
    assert _verify_render_duration(f, 4136.8) == ""


def test_verify_duration_tolerates_probe_failure(tmp_path, monkeypatch):
    """ffprobe 抛异常/输出异常 → 跳过不误报。"""
    import subprocess
    from slirn_home.app import _verify_render_duration

    f = tmp_path / "out.mp4"
    f.write_bytes(b"x")

    def _boom(cmd, **kw):
        raise RuntimeError("probe down")

    monkeypatch.setattr(subprocess, "run", _boom)
    assert _verify_render_duration(f, 10.0) == ""


# ---------- cmd 构建：-t 上界替代 -shortest ----------

def test_cmd_uses_t_cap_when_duration_given(tmp_path, monkeypatch):
    """duration=10 且 probe 不可得 → -t 10.050（duration+50ms），无 -shortest。"""
    import subprocess
    from slirn_home.app import _run_fine_render

    m, video = _make_mgr(tmp_path)
    t = m.create(name="t-t-cap", original_video=video)
    _save_min_fc(m, t.task_id, video)

    captured = {}
    monkeypatch.setattr(subprocess, "run", _make_fake_run(
        probe_format="0", captured=captured))
    out = tmp_path / "tasks" / t.task_id / "outputs" / "x.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)

    res = _run_fine_render(t.task_id, m, out, 10.0)
    assert res["ok"] is True
    assert "-shortest" not in captured["cmd"]
    _assert_out_t(captured, "10.050")


def test_cmd_t_clamped_to_source_tail(tmp_path, monkeypatch):
    """duration=10 但源只有 5s → 夹紧到源尾：-t 5.050（防校验误报）。"""
    import subprocess
    from slirn_home.app import _run_fine_render

    m, video = _make_mgr(tmp_path)
    t = m.create(name="t-t-clamp", original_video=video)
    _save_min_fc(m, t.task_id, video)

    captured = {}
    monkeypatch.setattr(subprocess, "run", _make_fake_run(
        probe_format="5.0", captured=captured))
    out = tmp_path / "tasks" / t.task_id / "outputs" / "x.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)

    res = _run_fine_render(t.task_id, m, out, 10.0)
    assert res["ok"] is True
    _assert_out_t(captured, "5.050")


def test_cmd_full_export_probe_minus_start(tmp_path, monkeypatch):
    """完整导出（duration=None）+ preview_start=2 → -t (probe-start)=3.050。"""
    import subprocess
    from slirn_home.app import _run_fine_render

    m, video = _make_mgr(tmp_path)
    t = m.create(name="t-t-full", original_video=video)
    _save_min_fc(m, t.task_id, video)

    captured = {}
    monkeypatch.setattr(subprocess, "run", _make_fake_run(
        probe_format="5.0", captured=captured))
    out = tmp_path / "tasks" / t.task_id / "outputs" / "x.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)

    res = _run_fine_render(t.task_id, m, out, None, preview_start=2.0)
    assert res["ok"] is True
    _assert_out_t(captured, "3.050")


def test_cmd_falls_back_to_shortest_without_probe(tmp_path, monkeypatch):
    """probe 失败 + 完整导出 → expected 未知 → 退回 -shortest 旧行为。"""
    import subprocess
    from slirn_home.app import _run_fine_render

    m, video = _make_mgr(tmp_path)
    t = m.create(name="t-t-fallback", original_video=video)
    _save_min_fc(m, t.task_id, video)

    captured = {}
    monkeypatch.setattr(subprocess, "run", _make_fake_run(
        probe_format="0", captured=captured))
    out = tmp_path / "tasks" / t.task_id / "outputs" / "x.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)

    res = _run_fine_render(t.task_id, m, out, None)
    assert res["ok"] is True
    assert "-shortest" in captured["cmd"]
    assert "-t" not in captured["cmd"]


def test_sync_render_flags_truncated_output(tmp_path, monkeypatch):
    """exit 0 但产物视频流只有 5s（预期 10s）→ ok=False + 截断错误。"""
    import subprocess
    from slirn_home.app import _run_fine_render

    m, video = _make_mgr(tmp_path)
    t = m.create(name="t-sync-trunc", original_video=video)
    _save_min_fc(m, t.task_id, video)

    out = tmp_path / "tasks" / t.task_id / "outputs" / "x.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"fake-mp4")  # 校验要求产物存在

    monkeypatch.setattr(subprocess, "run", _make_fake_run(
        probe_format="20.0",
        streams_json=[{"codec_type": "video", "duration": "5.0"},
                      {"codec_type": "audio", "duration": "5.0"}]))
    res = _run_fine_render(t.task_id, m, out, 10.0)
    assert res["ok"] is False
    assert "截断" in res["error"]


# ---------- 跨任务串行门 ----------

def test_gate_serializes_cross_task_exports(monkeypatch, tmp_path):
    """_run_fine_render_async 在 _FINE_RENDER_GATE 被占时排队，释放后才执行。"""
    import slirn_home.app as appmod

    called = threading.Event()
    seen = {}

    def _stub(job, *a, **k):
        seen["state_at_run"] = job.state
        called.set()

    monkeypatch.setattr(appmod, "_run_fine_render_locked", _stub)

    job = appmod._RenderJob(job_id="job_gate_test", task_id="t-gate")
    appmod._FINE_RENDER_GATE.acquire()
    try:
        th = threading.Thread(
            target=appmod._run_fine_render_async,
            args=(job, "t-gate", None, tmp_path / "x.mp4"),
            daemon=True,
        )
        th.start()
        time.sleep(0.3)
        assert not called.is_set(), "门被占时不应进入渲染"
        assert job.state == "queued", "排队期间 state 应保持 queued"
    finally:
        appmod._FINE_RENDER_GATE.release()
    th.join(timeout=2)
    assert called.is_set()
    assert seen["state_at_run"] == "queued"


# ---------- 异步渲染终态接线：截断 → failed ----------

class _FakeLiveProc:
    """假 ffmpeg 进程：吐几行 progress 后 EOF、returncode 0。"""

    returncode = 0

    def __init__(self):
        self.stdout = io.BytesIO(
            b"out_time_ms=1000000\nspeed=1.0x\nprogress=continue\nprogress=end\n")

    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0

    def terminate(self):
        pass

    def kill(self):
        pass


def _run_locked_with_fake_ffmpeg(tmp_path, monkeypatch, verify_ret):
    import subprocess
    import slirn_home.app as appmod

    m, video = _make_mgr(tmp_path)
    t = m.create(name="t-async-trunc", original_video=video)
    _save_min_fc(m, t.task_id, video)

    out = tmp_path / "tasks" / t.task_id / "outputs" / "fine_export.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: _FakeLiveProc())
    monkeypatch.setattr(appmod, "_verify_render_duration",
                        lambda p, e: verify_ret)

    job = appmod._RenderJob(job_id="job_trunc_test", task_id=t.task_id)
    appmod._run_fine_render_locked(
        job, t.task_id, m, out, "", out.parent, 0.0, 10.0)
    return job, out


def test_async_render_marks_truncated_as_failed(tmp_path, monkeypatch):
    """exit 0 + 校验发现截断 → job failed（无 output_url），非 done。"""
    job, out = _run_locked_with_fake_ffmpeg(
        tmp_path, monkeypatch, verify_ret="MOCK-TRUNC")
    assert job.state == "failed"
    assert "MOCK-TRUNC" in job.error
    assert job.output_url == ""


def test_async_render_full_output_marks_done(tmp_path, monkeypatch):
    """exit 0 + 校验通过 → done + output_url 带实际文件名。"""
    job, out = _run_locked_with_fake_ffmpeg(
        tmp_path, monkeypatch, verify_ret="")
    assert job.state == "done"
    assert "fine_export.mp4" in job.output_url
    assert job.progress_pct == 100.0
