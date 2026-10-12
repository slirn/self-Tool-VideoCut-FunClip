"""REQ-20261012-099 修订五（Batch F）：Stage 3 调用上游「大模型智能剪辑」后台。

用户验收三件事：
1. 解析按钮 → 上游 funclip/launch.py llm_inference 同款链路（system =
   「srt字幕分析剪辑器」+ user = 「这是待裁剪的视频srt字幕：」+ raw SRT），
   LLM 返回 `1. [开始-结束时间] 文本` 编号字幕列表
2. 字幕列表自动编号（原始顺序号）；拖拽重排只改 splice_order，编号不变
3. 截取拼接：按 splice_order 从源视频逐段截取并拼接（moviepy 步骤 mock）
"""

import json
import subprocess
from pathlib import Path

from slirn_home import revision_service
from slirn_home import short_video_analyze as sva
from slirn_home import short_video_coarse as coarse
from slirn_home import short_video_service as svc

FUNCLIP_ROOT = Path(__file__).resolve().parent.parent

_SRT = (
    "1\n00:00:01,000 --> 00:00:02,000\n第一句\n\n"
    "2\n00:00:05,000 --> 00:00:06,000\n第二句\n\n"
    "3\n00:00:08,000 --> 00:00:09,000\n第三句\n\n"
    "4\n00:00:10,500 --> 00:00:11,000\n第四句\n\n"
)

# 上游「大模型智能剪辑」LLM 原文：序号乱序 + 全角句点（parser 必须自动重编号）
_LLM_CLIP_TEXT = (
    "3. [00:00:08,000-00:00:09,000] 第三句\n"
    "1、[00:00:01,000-00:00:06,000] 第一句到第二句\n"
    "2. [00:00:10,500-00:00:11,000] 第四句\n"
)


def _make_project(tmp_path: Path):
    """建任务 + split 项目（自动导入源视频）+ 4 行 raw.srt；返回 (tid, pid)。"""
    from tasklib import TaskManager

    video = tmp_path / "src.mp4"
    video.write_bytes(b"fake-mp4-bytes")
    mgr = TaskManager(tmp_path)
    task = mgr.create(name="t", original_video=video)
    tid = task.task_id
    proj = svc.create_project(tmp_path, tid, name="p", brief="")
    pid = proj["id"]
    rp = svc.stage_raw_srt_path(tmp_path, tid, pid)
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(_SRT, encoding="utf-8")
    return tid, pid


def _select_first_source(tmp_path: Path, tid: str, pid: str) -> None:
    proj = svc.load_project(tmp_path, tid, pid)
    mats = [m for m in proj.get("materials") or [] if m.get("kind") == "video"]
    assert mats, "create_project(split) 应自动导入任务源视频"
    proj["base_material_id"] = mats[0]["id"]
    svc.save_project(tmp_path, proj)


def _run_stage3_clips(tmp_path: Path, tid: str, pid: str, n_clips: int = 4) -> dict:
    orig = revision_service._call_llm
    revision_service._call_llm = lambda s, u, **kw: _LLM_CLIP_TEXT
    try:
        state = sva.run_stage3(tmp_path, tid, pid, n_clips=n_clips)
    finally:
        revision_service._call_llm = orig
    return state


# ---------- 上游提示词保真 ----------

def test_upstream_prompt_fidelity():
    """上游 launch.py 的 prompt_head 与我们的默认 system 同源（srt字幕分析剪辑器）。"""
    upstream = (FUNCLIP_ROOT / "funclip" / "launch.py").read_text(encoding="utf-8")
    assert "srt字幕分析剪辑器" in upstream
    assert "这是待裁剪的视频srt字幕：" in upstream
    assert "srt字幕分析剪辑器" in sva._UPSTREAM_CLIP_SYSTEM_PROMPT
    assert sva._UPSTREAM_CLIP_USER_PREFIX == "这是待裁剪的视频srt字幕："


# ---------- 解析器 ----------

def test_parse_llm_time_variants():
    assert sva._parse_llm_time("00:00:01,500") == 1500
    assert sva._parse_llm_time("01:02:03,004") == 3723004
    assert sva._parse_llm_time("1:05,500") == 65500  # MM:SS,mmm 短格式
    assert sva._parse_llm_time("坏的时间") == -1


def test_parse_llm_clip_segments_renumber_and_map():
    lines = sva._parse_srt_lines(_SRT)
    segments, warnings = sva._parse_llm_clip_segments(_LLM_CLIP_TEXT, lines, n_clips=4)

    # 自动编号：按出现顺序重编（LLM 写的 3、1、2 不可信）
    assert [h["index"] for h in segments] == [1, 2, 3]
    assert [h["id"] for h in segments] == ["h1", "h2", "h3"]
    # 第 1 条（时间上先出现的是「第三句」段）时间与文本对上
    assert segments[0]["start_ms"] == 8000 and segments[0]["end_ms"] == 9000
    assert segments[0]["title"] == "第三句"
    # 时间重叠映射：[1s,6s] 段含源字幕行 1、2
    assert [sl["src_index"] for sl in segments[1]["subtitle_lines"]] == [1, 2]


def test_parse_llm_clip_segments_cap_and_invalid():
    lines = sva._parse_srt_lines(_SRT)
    raw = (
        "1. [00:00:01,000-00:00:06,000] 段一\n"
        "2. [00:00:08,000-00:00:04,000] 结束早于开始（应跳过）\n"
        "3. [00:00:08,000-00:00:09,000] 段三\n"
    )
    segments, warnings = sva._parse_llm_clip_segments(raw, lines, n_clips=2)
    # cap=2 → 截断到 2 条；无效行进 warnings
    assert len(segments) == 2
    assert any("结束时间" in w for w in warnings)

    # 完全不可解析 → 报错并带回显开头
    try:
        sva._parse_llm_clip_segments("我找不到片段", lines)
        raise AssertionError("无片段行应抛错")
    except svc.ShortVideoError as e:
        assert "我找不到片段" in str(e)


def test_parse_llm_clip_segments_no_overlap_warns():
    lines = sva._parse_srt_lines(_SRT)
    # 落在两行字幕之间的空隙（2.5s-4.5s 无字幕）→ 告警但保留该段
    segments, warnings = sva._parse_llm_clip_segments(
        "1. [00:00:02,500-00:00:04,500] 空隙段\n", lines,
    )
    assert len(segments) == 1
    assert any("没有源字幕行" in w for w in warnings)


# ---------- run_stage3 上游链路 ----------

def test_run_stage3_upstream_flow(tmp_path):
    tid, pid = _make_project(tmp_path)
    captured = {"sys": "", "user": ""}
    orig = revision_service._call_llm

    def fake(sys_p, user_p, **kw):
        captured["sys"] = sys_p
        captured["user"] = user_p
        return _LLM_CLIP_TEXT

    revision_service._call_llm = fake
    try:
        state = sva.run_stage3(tmp_path, tid, pid)
    finally:
        revision_service._call_llm = orig

    assert state["status"] == "done"
    assert state["flow"] == "llm_clip"
    assert state["count"] == 3
    assert state["splice_order"] == [1, 2, 3]
    # system = 上游默认（srt字幕分析剪辑器，n 按 4 模板化）
    assert "srt字幕分析剪辑器" in captured["sys"]
    assert "输出4条以内" in captured["sys"]
    # user = 上游前缀 + 原始 SRT + 编号列表格式约束
    assert captured["user"].startswith("这是待裁剪的视频srt字幕：")
    assert "00:00:08,000 --> 00:00:09,000" in captured["user"]
    assert "[开始时间-结束时间]" in captured["user"]

    hp = svc.stage_highlights_path(tmp_path, tid, pid)
    doc = json.loads(hp.read_text(encoding="utf-8"))
    assert doc["flow"] == "llm_clip"
    assert doc["splice_order"] == [1, 2, 3]
    hls = doc["highlights"]
    assert [h["index"] for h in hls] == [1, 2, 3]
    assert hls[0]["subtitle_lines"] == [{"src_index": 3, "text": "第三句"}]
    # LLM 原文落档
    assert svc.stage_llm_raw_path(tmp_path, tid, pid).read_text(encoding="utf-8") == _LLM_CLIP_TEXT


def test_run_stage3_json_fallback(tmp_path):
    """模型按旧 JSON 输出（无编号行）→ 兜底旧解析，不整跑失败。"""
    tid, pid = _make_project(tmp_path)
    old_json = json.dumps({"highlights": [{
        "id": "h1", "title": "旧格式", "start_ms": 1000, "end_ms": 6000,
        "subtitle_lines": [{"src_index": 1, "text": "第一句"},
                           {"src_index": 2, "text": "第二句"}],
    }]}, ensure_ascii=False)
    orig = revision_service._call_llm
    revision_service._call_llm = lambda s, u, **kw: old_json
    try:
        state = sva.run_stage3(tmp_path, tid, pid)
    finally:
        revision_service._call_llm = orig
    assert state["status"] == "done"
    assert state["count"] == 1


def test_run_stage3_resets_splice_state(tmp_path):
    """重跑解析 → 新片段集 + splice_order 重置自然序 + splice 状态重置。"""
    tid, pid = _make_project(tmp_path)
    assert _run_stage3_clips(tmp_path, tid, pid)["status"] == "done"
    # 手工把 splice 标成 done（模拟已拼接），再重跑
    proj = svc.load_project(tmp_path, tid, pid)
    sp = svc.get_stage_state(proj, "stage3_splice")
    sp["status"] = "done"
    svc.set_stage_state(proj, "stage3_splice", sp)
    svc.save_project(tmp_path, proj)
    sva.reorder_splice_order(tmp_path, tid, pid, [3, 1, 2])

    state = _run_stage3_clips(tmp_path, tid, pid)
    assert state["status"] == "done"
    assert state["splice_order"] == [1, 2, 3]
    proj = svc.load_project(tmp_path, tid, pid)
    assert svc.get_stage_state(proj, "stage3_splice").get("status") == "pending"


# ---------- 拖拽重排（编号不变） ----------

def test_reorder_splice_order(tmp_path):
    tid, pid = _make_project(tmp_path)
    assert _run_stage3_clips(tmp_path, tid, pid)["status"] == "done"

    state = sva.reorder_splice_order(tmp_path, tid, pid, [3, 1, 2])
    assert state["splice_order"] == [3, 1, 2]
    doc = json.loads(
        svc.stage_highlights_path(tmp_path, tid, pid).read_text(encoding="utf-8")
    )
    assert doc["splice_order"] == [3, 1, 2]
    # 片段本体编号不变（重排永不重编）
    assert [h["index"] for h in doc["highlights"]] == [1, 2, 3]

    # 非排列（漏 2）→ 拒绝
    try:
        sva.reorder_splice_order(tmp_path, tid, pid, [1, 3])
        raise AssertionError("非完整排列应抛错")
    except svc.ShortVideoError:
        pass

    # 重排后 splice 状态作废提示（先标 done 再重排）
    proj = svc.load_project(tmp_path, tid, pid)
    sp = svc.get_stage_state(proj, "stage3_splice")
    sp["status"] = "done"
    svc.set_stage_state(proj, "stage3_splice", sp)
    svc.save_project(tmp_path, proj)
    sva.reorder_splice_order(tmp_path, tid, pid, [2, 1, 3])
    proj = svc.load_project(tmp_path, tid, pid)
    sp = svc.get_stage_state(proj, "stage3_splice")
    assert sp.get("status") == "pending"
    assert "拼接顺序已调整" in str(sp.get("stale_reason"))


def test_build_splice_plan():
    hls = [{"index": 1}, {"index": 2}, {"index": 3}]
    ordered, seq = coarse.build_splice_plan(hls, [3, 1])
    assert seq == [3, 1, 2]          # 漏掉的按自然序补尾
    ordered, seq = coarse.build_splice_plan(hls, [9, 2, 2])  # 悬空/重复 → 过滤
    assert seq == [2, 1, 3]
    ordered, seq = coarse.build_splice_plan(hls, None)
    assert seq == [1, 2, 3]          # 无 order → 自然序
    assert ordered == hls


# ---------- 截取拼接 ----------

def test_run_stage3_splice_with_fake_renderer(tmp_path, monkeypatch):
    tid, pid = _make_project(tmp_path)
    _select_first_source(tmp_path, tid, pid)
    assert _run_stage3_clips(tmp_path, tid, pid)["status"] == "done"
    sva.reorder_splice_order(tmp_path, tid, pid, [2, 3, 1])

    captured = {}

    def fake_render(source_video, seg_list, out_mp4):
        captured["src"] = Path(source_video).name
        captured["segs"] = seg_list
        out_mp4.write_bytes(b"fake-splice-mp4" * 200)

    monkeypatch.setattr(coarse, "_render_splice_video", fake_render)
    state = coarse.run_stage3_splice(tmp_path, tid, pid)

    assert state["status"] == "done", state
    assert state["order"] == [2, 3, 1]
    assert state["count"] == 3
    assert captured["src"] == "src.mp4"
    # 切割窗按 splice_order：#2 [1s,6.1s]、#3 [10.5s,11.1s]、#1 [8s,9.1s]
    # （end_ost 默认 +100ms 防切词尾）
    assert [(round(s, 3), round(e, 3)) for s, e in captured["segs"]] == [
        (1.0, 6.1), (10.5, 11.1), (8.0, 9.1),
    ]
    mp4 = svc.stage3_dir(tmp_path, tid, pid) / "splice.mp4"
    assert mp4.is_file() and mp4.stat().st_size > 1024
    # 拼接 SRT：按新顺序重编 + 时间轴重定位（首块从 0 起，跨段连续累加）
    srt = (svc.stage3_dir(tmp_path, tid, pid) / "splice.srt").read_text(encoding="utf-8")
    blocks = [b for b in srt.split("\n\n") if b.strip()]
    assert len(blocks) == 4  # #2 带两行字幕，#3/#1 各一行
    first = blocks[0].splitlines()
    assert first[0] == "1" and first[1].startswith("00:00:00,000 -->")
    texts = [b.splitlines()[2] for b in blocks]
    assert texts == ["第一句", "第二句", "第四句", "第三句"]  # 顺序 #2 → #3 → #1


def test_run_stage3_splice_errors(tmp_path):
    tid, pid = _make_project(tmp_path)
    # 未选源视频
    try:
        coarse.run_stage3_splice(tmp_path, tid, pid)
        raise AssertionError("未选源应抛错")
    except svc.ShortVideoError:
        pass
    # 无 highlights
    _select_first_source(tmp_path, tid, pid)
    try:
        coarse.run_stage3_splice(tmp_path, tid, pid)
        raise AssertionError("未解析应抛错")
    except svc.ShortVideoError:
        pass


def test_run_stage3_splice_renderer_failure(tmp_path, monkeypatch):
    tid, pid = _make_project(tmp_path)
    _select_first_source(tmp_path, tid, pid)
    assert _run_stage3_clips(tmp_path, tid, pid)["status"] == "done"

    def boom(source_video, seg_list, out_mp4):
        raise RuntimeError("moviepy 崩了")

    monkeypatch.setattr(coarse, "_render_splice_video", boom)
    state = coarse.run_stage3_splice(tmp_path, tid, pid)
    assert state["status"] == "failed"
    assert "moviepy 崩了" in str(state.get("error"))


# ---------- API 端点 ----------

def test_api_reorder_and_splice_endpoints(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from slirn_home.app import build_app

    tid, pid = _make_project(tmp_path)
    _select_first_source(tmp_path, tid, pid)
    orig = revision_service._call_llm
    revision_service._call_llm = lambda s, u, **kw: _LLM_CLIP_TEXT
    monkeypatch.setattr(
        coarse, "_render_splice_video",
        lambda sv, segs, out: out.write_bytes(b"x" * 2048),
    )
    try:
        client = TestClient(build_app(tmp_path).app)

        # 解析
        r = client.post("/slirn/api/short_video_stage3_run", json={
            "task_id": tid, "project_id": pid, "template": "hook_first", "n_clips": 4,
        }).json()
        assert r["ok"] is True, r

        # 拖拽重排（编号不变）
        r = client.post("/slirn/api/short_video_stage3_reorder_highlights", json={
            "task_id": tid, "project_id": pid, "order": [2, 1, 3],
        }).json()
        assert r["ok"] is True, r
        assert r["splice_order"] == [2, 1, 3]

        # 非法 order → 拒绝
        r = client.post("/slirn/api/short_video_stage3_reorder_highlights", json={
            "task_id": tid, "project_id": pid, "order": [1, 1, 3],
        }).json()
        assert r["ok"] is False

        # 拼接（renderer 已 mock）
        r = client.post("/slirn/api/short_video_stage3_splice", json={
            "task_id": tid, "project_id": pid,
        }).json()
        assert r["ok"] is True, r
        assert r["stage3_splice"]["order"] == [2, 1, 3]
        assert "拼接完成" in r["toast"]
    finally:
        revision_service._call_llm = orig


# ---------- UI 渲染（走 short_video_get —— highlights 由端点层注入 state） ----------

def test_stage3_ui_splice_card_and_drag_handles(tmp_path):
    from fastapi.testclient import TestClient
    from slirn_home.app import build_app

    tid, pid = _make_project(tmp_path)
    _select_first_source(tmp_path, tid, pid)
    assert _run_stage3_clips(tmp_path, tid, pid)["status"] == "done"
    sva.reorder_splice_order(tmp_path, tid, pid, [2, 3, 1])

    client = TestClient(build_app(tmp_path).app)
    html = client.post("/slirn/api/short_video_get", json={
        "task_id": tid, "project_id": pid,
    }).json()["html"]
    # 每行带 ⇕ 拖拽把柄 + 固定原始编号
    assert html.count("slirn-stage3-hl-dragseg") == 3
    assert html.count("slirn-sv-row-no") >= 3
    # 行渲染顺序 = splice_order（#2 行在 #1 行前），编号仍是原始号
    assert html.index('<div class="slirn-stage3-hl-row" data-hl-index="2"') < \
        html.index('<div class="slirn-stage3-hl-row" data-hl-index="1"')
    assert "#2 → #3 → #1" in html
    # 拼接卡 + 按钮（已选源 + 已解析 → 可点）
    assert "片段截取拼接" in html
    assert 'data-action="sv-stage3-splice"' in html
    assert 'data-action="sv-stage3-splice" disabled' not in html


def test_stage3_ui_splice_card_before_parse(tmp_path):
    """未解析 → 拼接按钮禁用 + 占位顺序说明（不报错）。"""
    from fastapi.testclient import TestClient
    from slirn_home.app import build_app

    tid, pid = _make_project(tmp_path)
    client = TestClient(build_app(tmp_path).app)
    html = client.post("/slirn/api/short_video_get", json={
        "task_id": tid, "project_id": pid,
    }).json()["html"]
    assert "片段截取拼接" in html
    assert 'data-action="sv-stage3-splice" disabled' in html
    assert "解析后按编号顺序" in html


# ---------- 前端接线守卫 ----------

def test_llm_clip_js_wiring():
    js = (FUNCLIP_ROOT / "slirn_home" / "static" / "short_video.js").read_text(encoding="utf-8")
    # 行级拖拽（把柄按下才开 draggable）+ 顺序持久化
    assert ".slirn-stage3-hl-dragseg" in js
    assert "short_video_stage3_reorder_highlights" in js
    assert "window._slirnSegDragRow" in js
    # 拼接动作 + 分发
    assert "function stage3Splice()" in js
    assert "action === 'sv-stage3-splice'" in js

    r = subprocess.run(
        ["node", "--check", str(FUNCLIP_ROOT / "slirn_home" / "static" / "short_video.js")],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, f"short_video.js 语法错误\nstderr={r.stderr[:1000]}"
