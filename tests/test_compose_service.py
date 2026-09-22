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


# ---- format_srt / _ms_to_srt_time (REQ-20260916-019) ----

def test_ms_to_srt_time_basic():
    assert compose_service._ms_to_srt_time(0) == "00:00:00,000"
    assert compose_service._ms_to_srt_time(999) == "00:00:00,999"
    assert compose_service._ms_to_srt_time(1000) == "00:00:01,000"
    assert compose_service._ms_to_srt_time(950) == "00:00:00,950"


def test_ms_to_srt_time_hours():
    assert compose_service._ms_to_srt_time(3600000) == "01:00:00,000"
    assert compose_service._ms_to_srt_time(3600000 + 65000) == "01:01:05,000"


def test_format_srt_empty():
    assert compose_service.format_srt([]) == ""


def test_format_srt_basic_sorted():
    units = [
        {"id": "10", "start_ms": 0, "end_ms": 2000, "text": "你好"},
        {"id": "11", "start_ms": 3000, "end_ms": 5000, "text": "世界"},
    ]
    out = compose_service.format_srt(units)
    assert out == "1\n00:00:00,000 --> 00:00:02,000\n你好\n\n2\n00:00:03,000 --> 00:00:05,000\n世界\n", out


def test_format_srt_unsorted_input_sorted():
    """输入乱序也按 start_ms 升序输出。"""
    units = [
        {"id": "11", "start_ms": 3000, "end_ms": 5000, "text": "世界"},
        {"id": "10", "start_ms": 0, "end_ms": 2000, "text": "你好"},
    ]
    out = compose_service.format_srt(units)
    assert out.index("00:00:00,000") < out.index("00:00:03,000")


def test_format_srt_text_overrides():
    """热词替换：overrides[id] 优先于 unit.text。"""
    units = [{"id": "10", "start_ms": 0, "end_ms": 2000, "text": "神精网络"}]
    out = compose_service.format_srt(units, {"10": "神经网络"})
    assert "神经网络" in out and "神精网络" not in out


# ---- delete_rough_compose (REQ-20260916-019) ----

def test_delete_rough_compose_missing(tmp_path):
    res = compose_service.delete_rough_compose(tmp_path)
    assert res["deleted"] is False
    assert "不存在" in res["message"]


def test_delete_rough_compose_only_mp4(tmp_path):
    mp4 = compose_service.rough_compose_path(tmp_path)
    mp4.write_bytes(b"fake mp4")
    res = compose_service.delete_rough_compose(tmp_path)
    assert res["deleted"] is True
    assert mp4.name in res["removed"]
    assert not mp4.exists()


def test_delete_rough_compose_both(tmp_path):
    mp4 = compose_service.rough_compose_path(tmp_path)
    srt = mp4.with_suffix(".srt")
    mp4.write_bytes(b"fake")
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8")
    res = compose_service.delete_rough_compose(tmp_path)
    assert res["deleted"] is True
    assert {mp4.name, srt.name} == set(res["removed"])
    assert not mp4.exists() and not srt.exists()


# ---- parse_srt（REQ-20260916-020：按 SRT 时间段截取拼接） ----

def test_parse_srt_standard():
    srt = ("1\n00:00:00,950 --> 00:00:04,270\n有点回音好像\n\n"
           "2\n00:00:04,270 --> 00:00:06,000\n第二行文本")
    out = compose_service.parse_srt(srt)
    assert [(e["start_ms"], e["end_ms"]) for e in out] == [(950, 4270), (4270, 6000)]
    assert out[0]["text"] == "有点回音好像"


def test_parse_srt_dot_millis_crlf_bom_unsorted():
    """点号毫秒 / CRLF / BOM / 乱序块均容忍，输出按 start_ms 升序。"""
    srt = ("\ufeff2\r\n00:00:05.500 --> 00:00:07\r\n后块\r\n\r\n"
           "1\r\n00:00:01.200 --> 00:00:03.450\r\n前块")
    out = compose_service.parse_srt(srt)
    assert [(e["start_ms"], e["end_ms"]) for e in out] == [(1200, 3450), (5500, 7000)]
    assert out[0]["text"] == "前块"


def test_parse_srt_multiline_text_joined():
    srt = "1\n00:00:00,000 --> 00:00:02,000\n第一行\n第二行"
    out = compose_service.parse_srt(srt)
    assert out[0]["text"] == "第一行\n第二行"


def test_parse_srt_skip_invalid_blocks():
    """end<=start / 无时间轴块跳过；全非法 → []。"""
    bad = ("1\n00:00:05,000 --> 00:00:05,000\n零长度\n\n"
           "没有时间轴的垃圾块\n\n"
           "2\n00:00:01,000 --> 00:00:02,000\n有效")
    out = compose_service.parse_srt(bad)
    assert [(e["start_ms"], e["end_ms"]) for e in out] == [(1000, 2000)]
    assert compose_service.parse_srt("完全不是SRT") == []
    assert compose_service.parse_srt("") == []


def test_parse_srt_hours_and_short_millis():
    """小时位 + 毫秒不足 3 位右补零（',7' = 700ms）；毫秒可省略。升序输出。"""
    srt = ("1\n01:02:03,456 --> 01:02:04,7\n文本A\n\n"
           "2\n00:00:10 --> 00:00:11\n文本B")
    out = compose_service.parse_srt(srt)
    # 00:00:10（10s）在 01:02:03（3723s）之前 → 升序
    assert [(e["start_ms"], e["end_ms"]) for e in out] == [
        (10_000, 11_000),
        ((1 * 3600 + 2 * 60 + 3) * 1000 + 456, (1 * 3600 + 2 * 60 + 4) * 1000 + 700),
    ]
    assert out[1]["text"] == "文本A"


# ---- REQ-20260922-NNN 标记删除行 → 优化成片剪辑 ----

def test_complement_intervals_ms():
    """删除区间的补集 = 保留区间：clamp / 碎片丢弃 / 全删 / 头尾触界。"""
    # 删中段
    assert compose_service.complement_intervals_ms([[2000, 4000]], 8000) == [
        [0, 2000], [4000, 8000]]
    # 删头 / 删尾
    assert compose_service.complement_intervals_ms([[0, 1000]], 8000) == [[1000, 8000]]
    assert compose_service.complement_intervals_ms([[7000, 8000]], 8000) == [[0, 7000]]
    # 多段删除 + 乱序输入
    assert compose_service.complement_intervals_ms(
        [[6000, 7000], [2000, 3000]], 8000) == [[0, 2000], [3000, 6000], [7000, 8000]]
    # 越界 clamp + 全删光
    assert compose_service.complement_intervals_ms([[-500, 1000]], 8000) == [[1000, 8000]]
    assert compose_service.complement_intervals_ms([[0, 9000]], 8000) == []
    # 空/零长删除段 → 整片保留
    assert compose_service.complement_intervals_ms([], 8000) == [[0, 8000]]
    assert compose_service.complement_intervals_ms([[3000, 3000]], 8000) == [[0, 8000]]


def test_build_optimize_cut_cmd_audio():
    """有音轨：每段 v+a trim/setpts + concat a=1 + 重编码参数 + progress。"""
    cmd = compose_service.build_optimize_cut_cmd(
        Path("in.mp4"), Path("out.mp4"), [[0, 2000], [4000, 8000]], has_audio=True)
    joined = " ".join(cmd)
    assert cmd[:4] == ["ffmpeg", "-y", "-i", "in.mp4"]
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "[0:v]trim=start=0.000:end=2.000,setpts=PTS-STARTPTS[v0]" in fc
    assert "[0:a]atrim=start=4.000:end=8.000,asetpts=PTS-STARTPTS[a1]" in fc
    assert "concat=n=2:v=1:a=1[vout][aout]" in fc
    assert "[vout]" in cmd and "[aout]" in cmd and cmd.count("-map") == 2
    assert "-c:v" in cmd and "libx264" in cmd and "veryfast" in cmd
    assert "-progress pipe:1" in joined and "out.mp4" in cmd[-1]


def test_build_optimize_cut_cmd_no_audio():
    """无音轨：只有视频链 + concat a=0，无 -map [aout]/音频编码参数。"""
    cmd = compose_service.build_optimize_cut_cmd(
        Path("in.mp4"), Path("out.mp4"), [[1000, 3000]], has_audio=False)
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert fc == ("[0:v]trim=start=1.000:end=3.000,setpts=PTS-STARTPTS[v0];"
                  "[v0]concat=n=1:v=1:a=0[vout]")
    assert "[0:a]" not in fc
    assert "[aout]" not in cmd and "aac" not in cmd


def test_optimize_cut_ready_matrix(tmp_path):
    """就绪判据：三件产物 + sidecar.marks 一致 + mp4 不旧于 rough。"""
    import json
    import os

    def _mk(marks=(1, 2), mp4_newer=True):
        compose_service._delete_cut_artifacts(tmp_path)
        rough = compose_service.rough_compose_path(tmp_path)
        rough.write_bytes(b"r")
        mp4 = compose_service.optimize_compose_path(tmp_path)
        mp4.write_bytes(b"v")
        (tmp_path / "optimize_compose.srt").write_text("1\nx", encoding="utf-8")
        (tmp_path / compose_service.OPTIMIZE_CUT_JSON).write_text(json.dumps(
            {"marks": list(marks), "deleted_ms": 1, "kept_sec": 1.0}), encoding="utf-8")
        t0 = rough.stat().st_mtime
        os.utime(rough, (t0, t0))
        os.utime(mp4, (t0, t0 + (10 if mp4_newer else -10)))
        return t0

    _mk(marks=(1, 2), mp4_newer=True)
    assert compose_service.optimize_cut_ready(tmp_path, [2, 1]) is not None  # 顺序无关
    # marks 不一致 → 失效
    assert compose_service.optimize_cut_ready(tmp_path, [1]) is None
    # rough 重合成（更新）→ 失效
    _mk(marks=(1, 2), mp4_newer=False)
    assert compose_service.optimize_cut_ready(tmp_path, [1, 2]) is None
    # 产物缺失 → 失效
    _mk(marks=(1, 2), mp4_newer=True)
    compose_service.optimize_compose_path(tmp_path).unlink()
    assert compose_service.optimize_cut_ready(tmp_path, [1, 2]) is None
    # sidecar 损坏 → 失效
    _mk(marks=(1, 2), mp4_newer=True)
    (tmp_path / compose_service.OPTIMIZE_CUT_JSON).write_text("{broken", encoding="utf-8")
    assert compose_service.optimize_cut_ready(tmp_path, [1, 2]) is None


def test_opt_cut_status_disk_fallback(tmp_path):
    """服务重启兜底：无内存 job → done（sidecar 新鲜）/ error（tmp 半成品）/ idle。"""
    st = compose_service.opt_cut_status("no-job", tmp_path, [])
    assert st["state"] == "idle"
    (tmp_path / compose_service.OPTIMIZE_COMPOSE_TMP_NAME).write_bytes(b"half")
    st2 = compose_service.opt_cut_status("no-job", tmp_path, [])
    assert st2["state"] == "error" and "重新保存" in st2["error"]


def test_start_optimize_cut_marks_empty_cleans(tmp_path, monkeypatch):
    """marks 为空 → 线程自清产物直接 done（result.cleared）。"""
    import json

    from slirn_home import optimize_service as osvc

    (tmp_path / osvc.OPTIMIZE_JSON).write_text(json.dumps(
        {"segments": [], "occurrences": [], "line_marks": []}), encoding="utf-8")
    for name in ("optimize_compose.mp4", "optimize_compose.srt",
                 compose_service.OPTIMIZE_CUT_JSON):
        (tmp_path / name).write_text("stale", encoding="utf-8")
    assert compose_service.start_optimize_cut("tc-1", tmp_path, tmp_path / "rough.mp4")
    import time as _t
    for _ in range(100):
        j = compose_service.opt_cut_job_status("tc-1")
        if j and j["state"] in ("done", "error"):
            break
        _t.sleep(0.05)
    assert j["state"] == "done" and j["result"]["cleared"] is True
    assert not (tmp_path / "optimize_compose.mp4").exists()


def test_start_optimize_cut_no_double_start():
    """job 在跑 → start 返回 False（保存端点 kick 幂等）。"""
    compose_service._OPT_CUT_JOBS["tc-busy"] = {
        "state": "running", "progress": 1.0, "stage": "剪辑中", "error": None,
        "started_at": 0.0, "finished_at": None, "result": None}
    assert compose_service.start_optimize_cut(
        "tc-busy", Path("."), Path("rough.mp4")) is False
