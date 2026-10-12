"""REQ-20261012-099 修订六（Batch G）：Stage 3 分组短视频解析（上游同款链路）。

用户验收四件事：
1. N 组 = N 条独立短视频（每条围绕一个标题、字幕合计约 70s、组内行序可≠srt 原序）
2. 截图先 OCR 提取文字，作为「参考方向」拼进提示词（主调用纯文本）
3. 行唯一编号 no：拖拽重排随行移动不变；「还原顺序」= 按 no 升序
4. 截取拼接：每组按当前行序逐行切片拼接成独立 clip_<N>.mp4 + 重定位 clip_<N>.srt
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

# 修订六分组格式：组号乱序（parser 按出现顺序重编）+ 组内行序 ≠ srt 原序
# （主题B 组先讲 8s 的「第三句」再回到 1s 的「第一句」——叙事需要）
_LLM_GROUPS_TEXT = (
    "# 2 | 主题B\n"
    "1. [00:00:08,000-00:00:09,000] 第三句\n"
    "2. [00:00:01,000-00:00:02,000] 第一句\n"
    "# 1 | 主题A\n"
    "1. [00:00:05,000-00:00:06,000] 第二句\n"
    "2、[00:00:10,500-00:00:11,000] 第四句\n"
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


def _run_stage3_groups(
    tmp_path: Path, tid: str, pid: str, n_clips: int = 4,
    llm_text: str = _LLM_GROUPS_TEXT,
) -> dict:
    orig = revision_service._call_llm
    revision_service._call_llm = lambda s, u, **kw: llm_text
    try:
        state = sva.run_stage3(tmp_path, tid, pid, n_clips=n_clips)
    finally:
        revision_service._call_llm = orig
    return state


def _swap_group1_lines(tmp_path: Path, tid: str, pid: str) -> None:
    """模拟用户拖拽：把组 1 的字幕行倒序（no 随行移动不变）。"""
    hp = svc.stage_highlights_path(tmp_path, tid, pid)
    doc = json.loads(hp.read_text(encoding="utf-8"))
    for h in doc["highlights"]:
        if int(h.get("index") or 0) == 1:
            h["subtitle_lines"] = list(reversed(h["subtitle_lines"]))
            break
    hp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


# ---------- 上游提示词保真 ----------

def test_upstream_prompt_fidelity():
    """上游 launch.py 的 prompt_head 与我们的默认 system 同源（srt字幕分析剪辑器）。"""
    upstream = (FUNCLIP_ROOT / "funclip" / "launch.py").read_text(encoding="utf-8")
    assert "srt字幕分析剪辑器" in upstream
    assert "这是待裁剪的视频srt字幕：" in upstream
    assert "srt字幕分析剪辑器" in sva._UPSTREAM_CLIP_SYSTEM_PROMPT
    assert sva._UPSTREAM_CLIP_USER_PREFIX == "这是待裁剪的视频srt字幕："


# ---------- 解析器：分组短视频列表 ----------

def test_parse_llm_time_variants():
    assert sva._parse_llm_time("00:00:01,500") == 1500
    assert sva._parse_llm_time("01:02:03,004") == 3723004
    assert sva._parse_llm_time("1:05,500") == 65500  # MM:SS,mmm 短格式
    assert sva._parse_llm_time("坏的时间") == -1


def test_match_group_title_variants():
    f = sva._match_group_title
    assert f("# 1 | 短视频标题") == "短视频标题"
    assert f("## 第 2 组 | 标题二") == "标题二"
    assert f("**组 3：标题三**") == "标题三"
    assert f("片段 4：标题四") == "标题四"
    assert f("随便一句话") is None          # 非标题行
    assert f("1. [00:00:01,000-00:00:02,000] x") is None  # 字幕行不是标题


def test_parse_groups_renumber_and_line_no():
    """组号按出现顺序重编（LLM 组号不可信）；组内行 no 从 1 连续编号，
    行序按叙事需要（可≠srt 原序）；时间/文本以匹配上的源行为准。"""
    lines = sva._parse_srt_lines(_SRT)
    groups, warnings = sva._parse_llm_clip_groups(_LLM_GROUPS_TEXT, lines, n_groups=4)

    assert [g["index"] for g in groups] == [1, 2]
    assert [g["id"] for g in groups] == ["h1", "h2"]
    # LLM 写「# 2 | 主题B」在前 → 重编为组 1
    assert groups[0]["title"] == "主题B"
    assert groups[1]["title"] == "主题A"
    # 组 1 行序 = LLM 叙事顺序：先 src3（8s）后 src1（1s）——与 srt 原序相反
    g1 = groups[0]["subtitle_lines"]
    assert [sl["no"] for sl in g1] == [1, 2]
    assert [sl["src_index"] for sl in g1] == [3, 1]
    assert g1[0]["start_ms"] == 8000 and g1[0]["end_ms"] == 9000
    assert g1[0]["text"] == "第三句"
    # 组窗口 = 组内行时间的 min/max
    assert groups[0]["start_ms"] == 1000 and groups[0]["end_ms"] == 9000
    # 测试 SRT 很短 → 两组都触发「合计不足 70s」提醒
    assert any("目标约 70s" in w for w in warnings)


def test_parse_groups_flat_fallback():
    """无组标题行的扁平编号列表（修订五格式兼容）→ 每行各自成组。"""
    lines = sva._parse_srt_lines(_SRT)
    raw = (
        "1. [00:00:01,000-00:00:02,000] 第一句\n"
        "2. [00:00:05,000-00:00:06,000] 第二句\n"
    )
    groups, _warnings = sva._parse_llm_clip_groups(raw, lines, n_groups=4)
    assert len(groups) == 2
    assert groups[0]["title"] == "第一句"
    assert [sl["src_index"] for sl in groups[0]["subtitle_lines"]] == [1]


def test_parse_groups_empty_header_replaced():
    lines = sva._parse_srt_lines(_SRT)
    raw = (
        "# 1 |\n"                       # 空标题组 → 被下一个有内容的组替换
        "# 2 | 有标题\n"
        "1. [00:00:01,000-00:00:02,000] 第一句\n"
    )
    groups, _warnings = sva._parse_llm_clip_groups(raw, lines, n_groups=4)
    assert len(groups) == 1
    assert groups[0]["title"] == "有标题"


def test_parse_groups_cap_and_invalid():
    lines = sva._parse_srt_lines(_SRT)
    raw = (
        "# 1 | 段一\n1. [00:00:01,000-00:00:06,000] 段一文本\n"
        "# 2 | 段二\n2. [00:00:08,000-00:00:04,000] 结束早于开始（应跳过该行）\n"
        "1. [00:00:08,000-00:00:09,000] 段三文本\n"
        "# 3 | 段三\n1. [00:00:05,000-00:00:06,000] 第二句\n"
    )
    groups, warnings = sva._parse_llm_clip_groups(raw, lines, n_groups=2)
    # cap=2 → 截断到 2 组（告警）；无效行进 warnings
    assert len(groups) == 2
    assert any("超出 2 组上限" in w for w in warnings)
    assert any("结束时间" in w for w in warnings)

    # 完全不可解析 → 报错并带回显开头
    try:
        sva._parse_llm_clip_groups("我找不到片段", lines)
        raise AssertionError("无可识别行应抛错")
    except svc.ShortVideoError as e:
        assert "我找不到片段" in str(e)


def test_parse_groups_no_overlap_warns_and_keeps_llm_time():
    lines = sva._parse_srt_lines(_SRT)
    # 落在两行字幕之间的空隙（2.5s-4.5s 无字幕）→ 告警但保留 LLM 时间戳
    groups, warnings = sva._parse_llm_clip_groups(
        "# 1 | 空隙\n1. [00:00:02,500-00:00:04,500] 空隙句\n", lines,
    )
    assert len(groups) == 1
    sl = groups[0]["subtitle_lines"][0]
    assert sl["src_index"] == 0
    assert sl["start_ms"] == 2500 and sl["end_ms"] == 4500
    assert any("没有可匹配的源字幕行" in w for w in warnings)


def test_parse_groups_duration_over_warns():
    long_srt = (
        "1\n00:00:00,000 --> 00:01:00,000\n长句一\n\n"
        "2\n00:02:00,000 --> 00:03:10,000\n长句二\n\n"
    )
    lines = sva._parse_srt_lines(long_srt)
    raw = (
        "# 1 | 长主题\n"
        "1. [00:00:00,000-00:01:00,000] 长句一\n"
        "2. [00:02:00,000-00:03:10,000] 长句二\n"
    )
    groups, warnings = sva._parse_llm_clip_groups(raw, lines, n_groups=4)
    assert groups[0]["total_ms"] == 130_000
    assert any("明显超过 70s" in w for w in warnings)


# ---------- run_stage3 分组链路 ----------

def test_run_stage3_groups_flow(tmp_path):
    tid, pid = _make_project(tmp_path)
    captured = {"sys": "", "user": ""}
    orig = revision_service._call_llm

    def fake(sys_p, user_p, **kw):
        captured["sys"] = sys_p
        captured["user"] = user_p
        return _LLM_GROUPS_TEXT

    revision_service._call_llm = fake
    try:
        state = sva.run_stage3(tmp_path, tid, pid)
    finally:
        revision_service._call_llm = orig

    assert state["status"] == "done"
    assert state["flow"] == "llm_groups"
    assert state["count"] == 2
    assert "splice_order" not in state  # 修订五拼接顺序随语义移除
    # system = 上游默认（srt字幕分析剪辑器，n 按 4 模板化）
    assert "srt字幕分析剪辑器" in captured["sys"]
    assert "整理出4条" in captured["sys"]
    # user = 上游前缀 + 原始 SRT + 分组格式约束
    assert captured["user"].startswith("这是待裁剪的视频srt字幕：")
    assert "00:00:08,000 --> 00:00:09,000" in captured["user"]
    assert "只输出 4 组" in captured["user"]
    assert "约 70 秒" in captured["user"]

    hp = svc.stage_highlights_path(tmp_path, tid, pid)
    doc = json.loads(hp.read_text(encoding="utf-8"))
    assert doc["flow"] == "llm_groups"
    assert "splice_order" not in doc
    hls = doc["highlights"]
    assert [h["index"] for h in hls] == [1, 2]
    assert [sl["no"] for sl in hls[0]["subtitle_lines"]] == [1, 2]
    # LLM 原文落档
    assert svc.stage_llm_raw_path(tmp_path, tid, pid).read_text(encoding="utf-8") == _LLM_GROUPS_TEXT


def test_run_stage3_json_fallback_annotates(tmp_path):
    """模型按旧 JSON 输出（无编号行）→ 兜底旧解析 + 补 no/时间戳，不整跑失败。"""
    tid, pid = _make_project(tmp_path)
    old_json = json.dumps({"highlights": [{
        "id": "h1", "title": "旧格式", "start_ms": 1000, "end_ms": 6000,
        "subtitle_lines": [{"src_index": 1, "text": "第一句"},
                           {"src_index": 2, "text": "第二句"}],
    }]}, ensure_ascii=False)
    state = _run_stage3_groups(tmp_path, tid, pid, llm_text=old_json)
    assert state["status"] == "done"
    assert state["count"] == 1
    doc = json.loads(
        svc.stage_highlights_path(tmp_path, tid, pid).read_text(encoding="utf-8")
    )
    sls = doc["highlights"][0]["subtitle_lines"]
    assert [sl["no"] for sl in sls] == [1, 2]      # _annotate_subtitle_lines 补 no
    assert sls[0]["start_ms"] == 1000              # 补源行时间戳
    assert sls[0]["end_ms"] == 2000


def test_run_stage3_resets_splice_state(tmp_path):
    """重跑解析 → splice 状态重置（修订五 splice_order 已不存在）。"""
    tid, pid = _make_project(tmp_path)
    assert _run_stage3_groups(tmp_path, tid, pid)["status"] == "done"
    proj = svc.load_project(tmp_path, tid, pid)
    sp = svc.get_stage_state(proj, "stage3_splice")
    sp["status"] = "done"
    svc.set_stage_state(proj, "stage3_splice", sp)
    svc.save_project(tmp_path, proj)

    state = _run_stage3_groups(tmp_path, tid, pid)
    assert state["status"] == "done"
    proj = svc.load_project(tmp_path, tid, pid)
    assert svc.get_stage_state(proj, "stage3_splice").get("status") == "pending"


# ---------- OCR：截图先提文字再拼参考方向 ----------

_VISION_ENTRY = {"id": "qwen-vl-max", "provider": "dashscope", "protocol": "openai"}


def test_run_stage3_ocr_reference_flow(tmp_path):
    """带截图 → 先 OCR（vision 调用）→ 文字作「参考方向」拼 user 头部；主调用纯文本。"""
    tid, pid = _make_project(tmp_path)
    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG-fake")
    svc.save_stage3_image(tmp_path, tid, pid, img)

    calls = {"vision": 0, "llm": 0}
    captured = {"user": ""}
    orig_llm = revision_service._call_llm
    orig_vis = revision_service._call_llm_vision

    def fake_vision(sys_p, user_text, image_paths, entry=None, **kw):
        calls["vision"] += 1
        assert len(image_paths) == 1
        assert image_paths[0].name == "input_image.png"
        return "重点一：如何选课\n重点二：如何剪辑"

    def fake_llm(sys_p, user_p, **kw):
        calls["llm"] += 1
        captured["user"] = user_p
        return _LLM_GROUPS_TEXT

    revision_service._call_llm_vision = fake_vision
    revision_service._call_llm = fake_llm
    try:
        state = sva.run_stage3(
            tmp_path, tid, pid, use_image=True, entry=_VISION_ENTRY,
        )
        assert state["status"] == "done"
        assert calls == {"vision": 1, "llm": 1}
        # OCR 文字作为参考方向拼在 user payload 头部（带明确「仅供参考」声明）
        assert "【课程重点参考" in captured["user"]
        assert "重点一：如何选课" in captured["user"]
        assert "仅作为选题与叙事方向的参考" in captured["user"]
        assert "这是待裁剪的视频srt字幕：" in captured["user"]
        # ocr_text.txt 落档 + state.ocr 元数据
        ocr_file = svc.stage3_dir(tmp_path, tid, pid) / "ocr_text.txt"
        assert ocr_file.read_text(encoding="utf-8") == "重点一：如何选课\n重点二：如何剪辑"
        assert state["ocr"]["source_image"] == "input_image.png"
        assert state["ocr"]["cached"] is False

        # 同图再跑一次 → OCR 缓存复用（vision 不再调用）
        state2 = sva.run_stage3(
            tmp_path, tid, pid, use_image=True, entry=_VISION_ENTRY,
        )
        assert state2["status"] == "done"
        assert calls["vision"] == 1
        assert state2["ocr"]["cached"] is True
    finally:
        revision_service._call_llm = orig_llm
        revision_service._call_llm_vision = orig_vis


def test_run_stage3_ocr_empty_text_warns(tmp_path):
    """OCR 提不出文字 → 不失败：告警 + payload 无参考段。"""
    tid, pid = _make_project(tmp_path)
    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG-fake")
    svc.save_stage3_image(tmp_path, tid, pid, img)

    captured = {"user": ""}
    orig_llm = revision_service._call_llm
    orig_vis = revision_service._call_llm_vision
    revision_service._call_llm_vision = lambda *a, **kw: "   "
    revision_service._call_llm = lambda s, u, **kw: (captured.__setitem__("user", u) or _LLM_GROUPS_TEXT)
    try:
        state = sva.run_stage3(
            tmp_path, tid, pid, use_image=True, entry=_VISION_ENTRY,
        )
        assert state["status"] == "done"
        assert any("未提取到文字" in w for w in state.get("warnings") or [])
        assert "【课程重点参考" not in captured["user"]
        assert "ocr" not in state
    finally:
        revision_service._call_llm = orig_llm
        revision_service._call_llm_vision = orig_vis


def test_run_stage3_ocr_failure_fails(tmp_path):
    """OCR 调用失败 → stage3 failed（错误信息含「截图文字提取失败」）。"""
    tid, pid = _make_project(tmp_path)
    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG-fake")
    svc.save_stage3_image(tmp_path, tid, pid, img)

    orig_vis = revision_service._call_llm_vision

    def boom(*a, **kw):
        raise RuntimeError("vision 挂了")

    revision_service._call_llm_vision = boom
    try:
        state = sva.run_stage3(
            tmp_path, tid, pid, use_image=True, entry=_VISION_ENTRY,
        )
        assert state["status"] == "failed"
        assert "截图文字提取失败" in str(state.get("error"))
    finally:
        revision_service._call_llm_vision = orig_vis


# ---------- 还原顺序（按初始编号升序） ----------

def test_restore_hl_order(tmp_path):
    tid, pid = _make_project(tmp_path)
    assert _run_stage3_groups(tmp_path, tid, pid)["status"] == "done"
    _swap_group1_lines(tmp_path, tid, pid)

    # 还原前 splice 标 done，验证还原会作废下游
    proj = svc.load_project(tmp_path, tid, pid)
    sp = svc.get_stage_state(proj, "stage3_splice")
    sp["status"] = "done"
    svc.set_stage_state(proj, "stage3_splice", sp)
    svc.save_project(tmp_path, proj)

    hl = sva.restore_hl_order(tmp_path, tid, pid, 1)
    nos = [sl["no"] for sl in hl["subtitle_lines"]]
    assert nos == [1, 2]
    doc = json.loads(
        svc.stage_highlights_path(tmp_path, tid, pid).read_text(encoding="utf-8")
    )
    g1 = next(h for h in doc["highlights"] if h["index"] == 1)
    assert [sl["no"] for sl in g1["subtitle_lines"]] == [1, 2]
    assert [sl["src_index"] for sl in g1["subtitle_lines"]] == [3, 1]  # 内容对得上
    # 下游/拼接作废
    proj = svc.load_project(tmp_path, tid, pid)
    assert svc.get_stage_state(proj, "stage3_splice").get("status") == "pending"

    # 不存在的组 → 报错
    try:
        sva.restore_hl_order(tmp_path, tid, pid, 9)
        raise AssertionError("不存在的组应抛错")
    except svc.ShortVideoError:
        pass


# ---------- 截取拼接（逐组独立 mp4 + 重定位 SRT） ----------

def test_run_stage3_splice_per_group(tmp_path, monkeypatch):
    tid, pid = _make_project(tmp_path)
    _select_first_source(tmp_path, tid, pid)
    assert _run_stage3_groups(tmp_path, tid, pid)["status"] == "done"
    # 用户拖拽：组 1 行序倒过来（no 随行移动）
    _swap_group1_lines(tmp_path, tid, pid)
    # 旧产物残留（修订五 splice.* + 上一轮不同组数的 clip_3）
    stage_dir = svc.stage3_dir(tmp_path, tid, pid)
    stage_dir.mkdir(parents=True, exist_ok=True)
    (stage_dir / "splice.mp4").write_bytes(b"old")
    (stage_dir / "clip_3.mp4").write_bytes(b"old")

    captured: dict[str, list] = {"calls": []}

    def fake_render(source_video, seg_list, out_mp4):
        captured["calls"].append({
            "src": Path(source_video).name,
            "segs": [(round(s, 3), round(e, 3)) for s, e in seg_list],
            "out": Path(out_mp4).name,
        })
        out_mp4.write_bytes(b"fake-clip-mp4" * 200)

    monkeypatch.setattr(coarse, "_render_splice_video", fake_render)
    state = coarse.run_stage3_splice(tmp_path, tid, pid)

    assert state["status"] == "done", state
    assert state["count"] == 2
    # 每组独立 mp4：切割窗按该组**当前行序**（组 1 已被拖拽倒序）
    by_out = {c["out"]: c for c in captured["calls"]}
    assert set(by_out) == {"clip_1.mp4", "clip_2.mp4"}
    assert all(c["src"] == "src.mp4" for c in captured["calls"])
    # 组 1：no2(src1, 1-2s) 在前 + no1(src3, 8-9s) 在后；end_ost 默认 +100ms
    assert by_out["clip_1.mp4"]["segs"] == [(1.0, 2.1), (8.0, 9.1)]
    # 组 2：行序未动（5-6s、10.5-11s）
    assert by_out["clip_2.mp4"]["segs"] == [(5.0, 6.1), (10.5, 11.1)]
    # 旧产物清理
    assert not (stage_dir / "splice.mp4").exists()
    assert not (stage_dir / "clip_3.mp4").exists()
    # clips 元数据（标题 + 时长 + 文件名）
    clips = {c["index"]: c for c in state["clips"]}
    assert clips[1]["title"] == "主题B"
    assert clips[2]["title"] == "主题A"
    assert clips[1]["mp4"] == "clip_1.mp4" and clips[1]["srt"] == "clip_1.srt"
    assert state["total_sec"] == round(sum(c["total_sec"] for c in state["clips"]), 1)

    # 组 1 SRT：重编号 + 时间轴重定位到拼接后连续时间轴
    srt1 = (stage_dir / "clip_1.srt").read_text(encoding="utf-8")
    blocks = [b for b in srt1.split("\n\n") if b.strip()]
    assert len(blocks) == 2
    b1, b2 = blocks[0].splitlines(), blocks[1].splitlines()
    assert b1[0] == "1" and b1[1].startswith("00:00:00,000 -->")
    assert b1[2] == "第一句"            # 拖拽后 src1 在前
    assert b2[0] == "2"
    assert b2[1] == "00:00:01,100 --> 00:00:02,200"  # 1.1s + 1.1s 累加
    assert b2[2] == "第三句"


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
    assert _run_stage3_groups(tmp_path, tid, pid)["status"] == "done"

    def boom(source_video, seg_list, out_mp4):
        raise RuntimeError("moviepy 崩了")

    monkeypatch.setattr(coarse, "_render_splice_video", boom)
    state = coarse.run_stage3_splice(tmp_path, tid, pid)
    assert state["status"] == "failed"
    assert "moviepy 崩了" in str(state.get("error"))


# ---------- API 端点 ----------

def test_api_restore_and_splice_endpoints(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from slirn_home.app import build_app

    tid, pid = _make_project(tmp_path)
    _select_first_source(tmp_path, tid, pid)
    monkeypatch.setattr(
        revision_service, "_call_llm", lambda s, u, **kw: _LLM_GROUPS_TEXT,
    )
    monkeypatch.setattr(
        coarse, "_render_splice_video",
        lambda sv, segs, out: out.write_bytes(b"x" * 2048),
    )
    client = TestClient(build_app(tmp_path).app)

    # 解析
    r = client.post("/slirn/api/short_video_stage3_run", json={
        "task_id": tid, "project_id": pid, "template": "hook_first", "n_clips": 4,
    }).json()
    assert r["ok"] is True, r

    # 还原顺序（先人为打乱）
    _swap_group1_lines(tmp_path, tid, pid)
    r = client.post("/slirn/api/short_video_stage3_restore_order", json={
        "task_id": tid, "project_id": pid, "hl_index": 1,
    }).json()
    assert r["ok"] is True, r
    assert "已还原" in r["toast"]
    assert [sl["no"] for sl in r["highlight"]["subtitle_lines"]] == [1, 2]

    # 修订五拖拽端点已移除
    resp = client.post("/slirn/api/short_video_stage3_reorder_highlights", json={
        "task_id": tid, "project_id": pid, "order": [2, 1],
    })
    assert resp.status_code == 404

    # 拼接（renderer 已 mock）→ 每组一条
    r = client.post("/slirn/api/short_video_stage3_splice", json={
        "task_id": tid, "project_id": pid,
    }).json()
    assert r["ok"] is True, r
    assert r["stage3_splice"]["count"] == 2
    assert "2 条短视频" in r["toast"]


# ---------- UI 渲染（走 short_video_get —— highlights 由端点层注入 state） ----------

def test_stage3_ui_line_no_timestamps_and_restore(tmp_path):
    from fastapi.testclient import TestClient

    from slirn_home.app import build_app

    tid, pid = _make_project(tmp_path)
    _select_first_source(tmp_path, tid, pid)
    assert _run_stage3_groups(tmp_path, tid, pid)["status"] == "done"
    _swap_group1_lines(tmp_path, tid, pid)

    client = TestClient(build_app(tmp_path).app)
    html = client.post("/slirn/api/short_video_get", json={
        "task_id": tid, "project_id": pid,
    }).json()["html"]

    # 行显示：唯一编号 + 完整时间戳（修订七：hh:mm:ss,ms 带方括号）
    assert html.count("slirn-stage3-hl-no") >= 4          # 2 组 × 2 行
    assert "[00:00:08,000 → 00:00:09,000]" in html        # src3 的时间区间
    assert "[00:00:01,000 → 00:00:02,000]" in html
    # 组元信息：字幕合计 + 行数
    assert "字幕合计" in html and "行" in html
    # 还原顺序按钮：组 1（已打乱）可点；组 2（原序）禁用
    assert 'data-action="sv-stage3-restore-order" data-hl-index="1" ' in html
    assert 'data-hl-index="2" disabled' in html
    # 修订五行拖拽把柄 / 拼接顺序展示已移除
    assert "slirn-stage3-hl-dragseg" not in html
    assert "拼接顺序：" not in html
    # 拼接卡（已选源 + 已解析 → 可点）
    assert "短视频截取拼接" in html
    assert 'data-action="sv-stage3-splice"' in html
    assert 'data-action="sv-stage3-splice" disabled' not in html


def test_stage3_ui_ocr_note_and_clip_list(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from slirn_home.app import build_app

    tid, pid = _make_project(tmp_path)
    _select_first_source(tmp_path, tid, pid)
    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG-fake")
    svc.save_stage3_image(tmp_path, tid, pid, img)
    monkeypatch.setattr(
        coarse, "_render_splice_video",
        lambda sv, segs, out: out.write_bytes(b"x" * 2048),
    )
    # 直接跑 run_stage3（显式 vision entry；API 端点用 get_current_entry，
    # 本机真实配置可能是非 vision 模型，端点路径的 vision 校验另有单测）
    orig_llm = revision_service._call_llm
    orig_vis = revision_service._call_llm_vision
    revision_service._call_llm = lambda s, u, **kw: _LLM_GROUPS_TEXT
    revision_service._call_llm_vision = lambda *a, **kw: "课程重点参考文字"
    client = TestClient(build_app(tmp_path).app)
    try:
        state = sva.run_stage3(
            tmp_path, tid, pid, use_image=True, entry=_VISION_ENTRY,
        )
        assert state["status"] == "done"

        html = client.post("/slirn/api/short_video_get", json={
            "task_id": tid, "project_id": pid,
        }).json()["html"]
        # OCR 提示 + 查看提取文字入口
        assert "截图文字已提取" in html
        assert "ocr_text.txt" in html

        # 拼接 → clips 列表
        r = client.post("/slirn/api/short_video_stage3_splice", json={
            "task_id": tid, "project_id": pid,
        }).json()
        assert r["ok"] is True, r
        html = client.post("/slirn/api/short_video_get", json={
            "task_id": tid, "project_id": pid,
        }).json()["html"]
        assert html.count("slirn-stage3-clip-item") == 2
        assert "clip_1.mp4" in html and "clip_1.srt" in html
        assert "主题B" in html and "主题A" in html
    finally:
        revision_service._call_llm = orig_llm
        revision_service._call_llm_vision = orig_vis


def test_stage3_ui_splice_card_before_parse(tmp_path):
    """未解析 → 拼接按钮禁用 + 空列表（不报错）。"""
    from fastapi.testclient import TestClient

    from slirn_home.app import build_app

    tid, pid = _make_project(tmp_path)
    client = TestClient(build_app(tmp_path).app)
    html = client.post("/slirn/api/short_video_get", json={
        "task_id": tid, "project_id": pid,
    }).json()["html"]
    assert "短视频截取拼接" in html
    assert 'data-action="sv-stage3-splice" disabled' in html


# ---------- 修订七：只显示一组 bug（state 镜像 ≠ doc） ----------

def test_stale_state_mirror_heals_on_get(tmp_path):
    """真实 bug（2026-10-12 用户报「只看到 3 号短视频」）：

    rerun_one 在镜像为空时留下单组镜像 → 之后整跑重写 doc 为全新 N 组，
    run_stage3 不清镜像 + GET 注入 `if not highlights` 跳过 → 页面只显示
    那一组过期数据。GET 注入必须按 doc（source of truth）整表覆盖。
    """
    from fastapi.testclient import TestClient

    from slirn_home.app import build_app

    tid, pid = _make_project(tmp_path)
    assert _run_stage3_groups(tmp_path, tid, pid)["status"] == "done"

    # 人为复刻磁盘上的过期单组镜像（等价于 rerun 留下 + 整跑后的现场）
    proj = svc.load_project(tmp_path, tid, pid)
    doc = json.loads(
        svc.stage_highlights_path(tmp_path, tid, pid).read_text(encoding="utf-8")
    )
    stale = [dict(doc["highlights"][1])]  # 只留组 2（对应真实现场「只剩 #3」）
    proj["pipeline"]["stage3_analyze"]["highlights"] = stale
    svc.save_project(tmp_path, proj)

    client = TestClient(build_app(tmp_path).app)
    html = client.post("/slirn/api/short_video_get", json={
        "task_id": tid, "project_id": pid,
    }).json()["html"]
    # doc 有 2 组 → 页面必须显示 2 组（旧代码这里只剩 1 行）
    assert html.count('class="slirn-stage3-hl-row"') == 2
    assert "主题A" in html and "主题B" in html


def test_run_stage3_clears_stale_mirror(tmp_path):
    """整跑产出全新 doc → 旧镜像（highlights / srt_lines）必须清掉。"""
    tid, pid = _make_project(tmp_path)
    proj = svc.load_project(tmp_path, tid, pid)
    s3 = proj["pipeline"]["stage3_analyze"]
    s3["highlights"] = [{"index": 99, "title": "过期单组"}]
    s3["srt_lines"] = [{"index": 1, "start_ms": 0, "end_ms": 1, "text": "过期行"}]
    svc.save_project(tmp_path, proj)

    assert _run_stage3_groups(tmp_path, tid, pid)["status"] == "done"
    proj = svc.load_project(tmp_path, tid, pid)
    s3 = proj["pipeline"]["stage3_analyze"]
    assert "highlights" not in s3, "整跑应清掉过期 highlights 镜像（由 GET 重注）"
    assert "srt_lines" not in s3, "整跑应清掉过期 srt_lines 镜像"


def test_rerun_and_restore_mirror_full_doc(tmp_path, monkeypatch):
    """rerun_one / restore 后，磁盘 state 镜像必须整表等于 doc（多入口对称）。"""
    from fastapi.testclient import TestClient

    from slirn_home.app import build_app

    tid, pid = _make_project(tmp_path)
    assert _run_stage3_groups(tmp_path, tid, pid)["status"] == "done"
    _swap_group1_lines(tmp_path, tid, pid)

    monkeypatch.setattr(
        revision_service, "_call_llm",
        lambda s, u, **kw: "# 1 | 重解析主题\n1. [00:00:05,000-00:00:06,000] 第二句\n",
    )
    client = TestClient(build_app(tmp_path).app)

    # rerun 组 1 → 镜像 = doc 全表（2 组），不是只有重解析的那组
    r = client.post("/slirn/api/short_video_stage3_rerun_one", json={
        "task_id": tid, "project_id": pid, "hl_index": 1,
    }).json()
    assert r["ok"] is True, r
    doc = json.loads(
        svc.stage_highlights_path(tmp_path, tid, pid).read_text(encoding="utf-8")
    )
    proj = svc.load_project(tmp_path, tid, pid)
    mirror = proj["pipeline"]["stage3_analyze"]["highlights"]
    assert len(mirror) == len(doc["highlights"]) == 2, "镜像必须整表同步 doc"

    # restore 组 1 → 同样整表同步（组 1 被 rerun 成单行，取组 2 验证内容随行）
    r = client.post("/slirn/api/short_video_stage3_restore_order", json={
        "task_id": tid, "project_id": pid, "hl_index": 1,
    }).json()
    assert r["ok"] is True, r
    proj = svc.load_project(tmp_path, tid, pid)
    mirror = proj["pipeline"]["stage3_analyze"]["highlights"]
    assert len(mirror) == 2
    m1 = next(h for h in mirror if h["index"] == 1)
    assert [sl["no"] for sl in m1["subtitle_lines"]] == [1]  # rerun 后只剩 1 行
    m2 = next(h for h in mirror if h["index"] == 2)
    assert [sl["no"] for sl in m2["subtitle_lines"]] == [1, 2]


# ---------- 修订七：字幕文字颜色 + 预览窗口拖动/调宽 ----------

def test_stage3_hl_text_explicit_color():
    """Gradio 用 `.prose *` 给 gr.HTML 所有后代强制 color（直接声明压继承）——
    无显式颜色的 .slirn-stage3-hl-text 在深色预览底上被强制成深字（看不到
    文字）；.slirn-srt-pre 固定浅底也同理（暗色主题变白底白字）。必须有显式色。"""
    css = (FUNCLIP_ROOT / "slirn_home" / "static" / "home.css").read_text(encoding="utf-8")
    seg = css.split(".slirn-stage3-hl-preview .slirn-stage3-hl-text {", 1)[1]
    seg = seg[: seg.index("}")]
    assert "color:" in seg, "hl-text 需显式 color（双类特异度压 .prose *）"
    srt_seg = css.split(".slirn-srt-pre {", 1)[1][:400]
    assert "color: #0f172a" in srt_seg, "srt-pre 固定浅底 → 固定深字"


def test_preview_windows_drag_and_resize_wiring():
    """预览 modal 与悬浮播放器都要能拖动 + 调整大小（修订七验收）。"""
    js = (FUNCLIP_ROOT / "slirn_home" / "static" / "short_video.js").read_text(encoding="utf-8")
    css = (FUNCLIP_ROOT / "slirn_home" / "static" / "home.css").read_text(encoding="utf-8")
    # 共享拖动/调宽助手 + modal 接线（modal 原本居中固定，不能拖也不能调）
    assert "function _enableWinDrag(" in js
    assert "function _enableWinResize(" in js
    assert "_enableWinDrag(card, ov.querySelector('.slirn-sv-preview-head'))" in js
    assert "slirn-sv-preview-resize" in js
    # 悬浮播放器：原本只可拖，补调宽 + 尺寸记忆
    assert "slirn-sv-fp-resize" in js
    assert "slirn.sv.fp.size" in js
    # CSS：把柄样式 + 标题栏拖动光标
    assert ".slirn-sv-preview-resize,\n.slirn-sv-fp-resize {" in css
    assert "cursor: nwse-resize" in css
    assert "cursor: move" in css.split(".slirn-sv-preview-head {", 1)[1][:200]


# ---------- 前端接线守卫 ----------

def test_llm_groups_js_wiring():
    js = (FUNCLIP_ROOT / "slirn_home" / "static" / "short_video.js").read_text(encoding="utf-8")
    # 还原顺序动作 + 端点 + 分发
    assert "function stage3RestoreOrder(btn)" in js
    assert "short_video_stage3_restore_order" in js
    assert "action === 'sv-stage3-restore-order'" in js
    # 拼接动作 + 分发
    assert "function stage3Splice()" in js
    assert "action === 'sv-stage3-splice'" in js
    # 修订五行拖拽已移除
    assert ".slirn-stage3-hl-dragseg" not in js
    assert "short_video_stage3_reorder_highlights" not in js
    assert "window._slirnSegDragRow" not in js

    r = subprocess.run(
        ["node", "--check", str(FUNCLIP_ROOT / "slirn_home" / "static" / "short_video.js")],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, f"short_video.js 语法错误\nstderr={r.stderr[:1000]}"


# ---------- 修订八：预览逐段播放 / 删除确认 / 重解析提示词重组 ----------

def test_filter_ocr_for_title():
    """OCR 参考按标题相关性过滤：相关行保留、无关行丢弃、无命中给空串。"""
    f = sva._filter_ocr_for_title
    ocr = "如何选课的三个步骤\n老师讲了剪辑软件\n选课要注意时间冲突\n下一章：导出视频"
    out = f(ocr, "选课")
    assert "如何选课的三个步骤" in out
    assert "选课要注意时间冲突" in out
    assert "剪辑软件" not in out and "导出视频" not in out
    assert f(ocr, "") == ""                    # 无标题 → 不给参考
    assert f("", "选课") == ""                 # 无 OCR → 空
    assert f("完全无关的内容", "选课") == ""   # 无命中 → 宁缺毋滥
    long_ocr = "\n".join(f"选课要点{i}" for i in range(20))
    assert len(f(long_ocr, "选课").splitlines()) == 12   # 12 行上限


def test_rerun_prompt_theme_and_filtered_ocr(tmp_path):
    """修订八：重解析提示词按标题重组 —— 主题声明 + OCR 只给相关行 +
    有条理组织要求（system 尾部定向指令同步升级）。"""
    tid, pid = _make_project(tmp_path)
    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG-fake")
    svc.save_stage3_image(tmp_path, tid, pid, img)
    # 整跑出 2 组（重编后：组1=主题B，组2=主题A）
    assert _run_stage3_groups(tmp_path, tid, pid)["status"] == "done"

    captured = {"sys": "", "user": ""}
    orig_llm = revision_service._call_llm
    orig_vis = revision_service._call_llm_vision

    def fake_vision(*a, **kw):
        return "如何做好主题A的第一步\n无关行：明天天气\n主题A 的第二步要点"

    def fake_llm(sys_p, user_p, **kw):
        captured["sys"] = sys_p
        captured["user"] = user_p
        return "# 1 | 主题A\n1. [00:00:05,000-00:00:06,000] 第二句\n"

    revision_service._call_llm = fake_llm
    revision_service._call_llm_vision = fake_vision
    try:
        state = sva.rerun_stage3_one(
            tmp_path, tid, pid, 2, use_image=True, entry=_VISION_ENTRY,
        )
        assert state["status"] == "done"
        # OCR 只给与「主题A」相关的行（节选模板，非整跑全量模板）
        assert "主题A的第一步" in captured["user"]
        assert "主题A 的第二步要点" in captured["user"]
        assert "无关行" not in captured["user"]
        assert "与主题「主题A」相关的参考方向" in captured["user"]
        assert "课程重点参考（来自" not in captured["user"]
        # 主题声明 + 有条理组织（user 约束 + system 定向指令）
        assert "只解析主题「主题A」这 1 条短视频" in captured["user"]
        assert "有条理地组织" in captured["user"]
        assert "只围绕主题「主题A」" in captured["sys"]
        assert "有条理地组织" in captured["sys"]
    finally:
        revision_service._call_llm = orig_llm
        revision_service._call_llm_vision = orig_vis


def test_remove_hl_flow(tmp_path):
    """删除一条：doc/state 镜像同步、其余 index 不重编、下游作废、
    删光后页面回未解析态；不存在 → 报错。"""
    from fastapi.testclient import TestClient

    from slirn_home.app import build_app

    tid, pid = _make_project(tmp_path)
    _select_first_source(tmp_path, tid, pid)
    assert _run_stage3_groups(tmp_path, tid, pid)["status"] == "done"
    proj = svc.load_project(tmp_path, tid, pid)
    sp = svc.get_stage_state(proj, "stage3_splice")
    sp["status"] = "done"
    svc.set_stage_state(proj, "stage3_splice", sp)
    svc.save_project(tmp_path, proj)

    client = TestClient(build_app(tmp_path).app)
    r = client.post("/slirn/api/short_video_stage3_remove_hl", json={
        "task_id": tid, "project_id": pid, "hl_index": 2,
    }).json()
    assert r["ok"] is True, r
    assert "剩余 1 条" in r["toast"]

    doc = json.loads(
        svc.stage_highlights_path(tmp_path, tid, pid).read_text(encoding="utf-8")
    )
    assert [h["index"] for h in doc["highlights"]] == [1]   # index 不重编
    proj = svc.load_project(tmp_path, tid, pid)
    s3 = proj["pipeline"]["stage3_analyze"]
    assert s3["count"] == 1 and len(s3["highlights"]) == 1  # 镜像整表同步
    assert svc.get_stage_state(proj, "stage3_splice").get("status") == "pending"

    r = client.post("/slirn/api/short_video_stage3_remove_hl", json={
        "task_id": tid, "project_id": pid, "hl_index": 9,
    }).json()
    assert r["ok"] is False and "不存在" in r["error"]

    r = client.post("/slirn/api/short_video_stage3_remove_hl", json={
        "task_id": tid, "project_id": pid, "hl_index": 1,
    }).json()
    assert r["ok"] is True and "已无剩余" in r["toast"]
    html = client.post("/slirn/api/short_video_get", json={
        "task_id": tid, "project_id": pid,
    }).json()["html"]
    assert html.count('class="slirn-stage3-hl-row"') == 0
    assert 'data-action="sv-stage3-splice" disabled' in html


def test_stage3_ui_segments_and_remove_button(tmp_path):
    """预览按钮带逐段切割窗（当前行序 + 100ms 词尾，与拼接一致）；
    删除按钮文案「删除」而非单字「删」。"""
    from fastapi.testclient import TestClient

    from slirn_home.app import build_app

    tid, pid = _make_project(tmp_path)
    _select_first_source(tmp_path, tid, pid)
    assert _run_stage3_groups(tmp_path, tid, pid)["status"] == "done"
    _swap_group1_lines(tmp_path, tid, pid)

    client = TestClient(build_app(tmp_path).app)
    html = client.post("/slirn/api/short_video_get", json={
        "task_id": tid, "project_id": pid,
    }).json()["html"]
    # 组 1 已拖拽倒序：no2(src1, 1-2s) 在前 + no1(src3, 8-9s)，end+100ms
    assert 'data-segments="[[1000, 2100], [8000, 9100]]"' in html
    # 组 2 原序：5-6s、10.5-11s
    assert 'data-segments="[[5000, 6100], [10500, 11100]]"' in html
    # 删除按钮（修订八：完整文案 + 需确认提示；Stage 1 素材列表的「删」不在此范围）
    assert ('data-action="sv-stage3-remove-hl" data-hl-index="1" '
            'title="删除这条短视频（需确认）">删除</button>') in html
    assert ('data-action="sv-stage3-remove-hl" data-hl-index="1" '
            'title="删除这条短视频（需确认）">删</button>') not in html


def test_preview_segments_and_remove_wiring():
    js = (FUNCLIP_ROOT / "slirn_home" / "static" / "short_video.js").read_text(encoding="utf-8")
    # 逐段播放引擎：解析 data-segments、段末跳下一段、手动拖进度条退出自动推进
    assert "getAttribute('data-segments')" in js
    assert "JSON.parse(raw)" in js
    assert "v.currentTime = segs[segIdx][0]" in js
    assert "addEventListener('seeking', onSeeking)" in js
    # 删除：confirm 先行 + 端点 + 分发
    assert "function stage3RemoveHl(btn)" in js
    assert "window.confirm(msg)" in js
    assert "short_video_stage3_remove_hl" in js
    assert "action === 'sv-stage3-remove-hl'" in js
