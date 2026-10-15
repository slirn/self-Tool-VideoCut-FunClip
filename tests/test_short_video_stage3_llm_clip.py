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
    # 测试 SRT 很短 → 两组都触发「未达 60s 下限」告警
    assert any("未达 60s 下限" in w for w in warnings)


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
    assert any("超过 90s 上限" in w for w in warnings)


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
    # REQ-20261015-119：system 带 60-90 硬约束 + 标注求和口径
    assert "60–90" in captured["sys"]
    # user = 上游前缀 + 秒数标注版 SRT + 分组格式约束
    assert captured["user"].startswith("这是待裁剪的视频srt字幕：")
    assert "00:00:08,000 --> 00:00:09,000" in captured["user"]
    assert "只输出 4 组" in captured["user"]
    assert "60–90" in captured["user"]
    assert "第一句（1.0s）" in captured["user"], "SRT 每行末尾须带真实秒数标注"

    hp = svc.stage_highlights_path(tmp_path, tid, pid)
    doc = json.loads(hp.read_text(encoding="utf-8"))
    assert doc["flow"] == "llm_groups"
    assert "splice_order" not in doc
    hls = doc["highlights"]
    assert [h["index"] for h in hls] == [1, 2]
    assert [sl["no"] for sl in hls[0]["subtitle_lines"]] == [1, 2]
    # 测试 SRT 极短（每组 2s/1.5s）→ 必触发时长反馈重试；两轮原文都落档
    assert state["duration_retry"] is True
    raw_saved = svc.stage_llm_raw_path(tmp_path, tid, pid).read_text(encoding="utf-8")
    assert raw_saved.startswith(_LLM_GROUPS_TEXT)
    assert "===== 时长反馈重试 =====" in raw_saved
    # 终态逐组时长如实告警（重试后仍不达标）
    assert any("未达 60s 下限" in w for w in state["warnings"])


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


# REQ-20261013-112：截取拼接功能已移除，splice 状态及对应测试一并删除


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
        # REQ-20261015-119：测试 SRT 极短（组 2s/1.5s）→ 主调用后必有
        # 一轮时长反馈重试（mock 返回同文，重试后仍不达标但流程完整）
        assert calls == {"vision": 1, "llm": 2}
        assert state["duration_retry"] is True
        # OCR 文字作为参考方向拼在 user payload 头部（带明确「仅供参考」声明）
        assert "【课程重点参考" in captured["user"]
        assert "重点一：如何选课" in captured["user"]
        assert "仅作为选题与叙事方向的参考" in captured["user"]
        assert "这是待裁剪的视频srt字幕：" in captured["user"]
        assert "重点一：如何选课（" not in captured["user"].split("这是待裁剪的视频srt字幕：")[1][:50], \
            "OCR 参考段不应混进 SRT 正文"
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

    # REQ-20261013-112：截取拼接已移除；下游 stage4/5/6/7 仍按 reset_downstream_stages 作废

    hl = sva.restore_hl_order(tmp_path, tid, pid, 1)
    nos = [sl["no"] for sl in hl["subtitle_lines"]]
    assert nos == [1, 2]
    doc = json.loads(
        svc.stage_highlights_path(tmp_path, tid, pid).read_text(encoding="utf-8")
    )
    g1 = next(h for h in doc["highlights"] if h["index"] == 1)
    assert [sl["no"] for sl in g1["subtitle_lines"]] == [1, 2]
    assert [sl["src_index"] for sl in g1["subtitle_lines"]] == [3, 1]  # 内容对得上
    # 下游（拼接已移除，验证 stage4 起被重置为 pending）
    proj = svc.load_project(tmp_path, tid, pid)
    assert svc.get_stage_state(proj, "stage4_optimize").get("status") == "pending"

    # 不存在的组 → 报错
    try:
        sva.restore_hl_order(tmp_path, tid, pid, 9)
        raise AssertionError("不存在的组应抛错")
    except svc.ShortVideoError:
        pass



# ---------- API 端点 ----------

def test_api_restore_endpoint(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from slirn_home.app import build_app

    tid, pid = _make_project(tmp_path)
    _select_first_source(tmp_path, tid, pid)
    monkeypatch.setattr(
        revision_service, "_call_llm", lambda s, u, **kw: _LLM_GROUPS_TEXT,
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

    # REQ-20261013-112：截取拼接端点已移除
    resp = client.post("/slirn/api/short_video_stage3_splice", json={
        "task_id": tid, "project_id": pid,
    })
    assert resp.status_code == 404


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
    # REQ-20261013-112：截取拼接卡已移除
    assert "短视频截取拼接" not in html
    assert 'data-action="sv-stage3-splice"' not in html


def test_stage3_ui_ocr_note(tmp_path, monkeypatch):
    """OCR 提示 + 查看提取文字入口（截取拼接功能已移除，clip_list 断言一并删除）。"""
    from fastapi.testclient import TestClient

    from slirn_home.app import build_app

    tid, pid = _make_project(tmp_path)
    _select_first_source(tmp_path, tid, pid)
    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG-fake")
    svc.save_stage3_image(tmp_path, tid, pid, img)
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

        # REQ-20261013-112：截取拼接已移除 → 无 clip_*.mp4 / clip_*.srt 入口
        assert "slirn-stage3-clip-item" not in html
        assert "clip_1.mp4" not in html
        # 解析产物（标题）仍正常展示
        assert "主题B" in html and "主题A" in html
    finally:
        revision_service._call_llm = orig_llm
        revision_service._call_llm_vision = orig_vis



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
    # REQ-20261013-112：截取拼接动作 + 分发已移除
    assert "function stage3Splice()" not in js
    assert "action === 'sv-stage3-splice'" not in js
    # 修订五行拖拽已移除
    assert ".slirn-stage3-hl-dragseg" not in js
    assert "short_video_stage3_reorder_highlights" not in js
    assert "window._slirnSegDragRow" not in js

    r = subprocess.run(
        ["node", "--check", str(FUNCLIP_ROOT / "slirn_home" / "static" / "short_video.js")],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, f"short_video.js 语法错误\nstderr={r.stderr[:1000]}"


# ---------- REQ-20261013-112：截取拼接全栈移除回归 ----------

def test_splice_feature_fully_removed():
    """JS / CSS / Python 三层 splice 痕迹全清。"""
    js = (FUNCLIP_ROOT / "slirn_home" / "static" / "short_video.js").read_text(encoding="utf-8")
    css = (FUNCLIP_ROOT / "slirn_home" / "static" / "home.css").read_text(encoding="utf-8")

    # JS：handler + 派发 + 端点全删
    assert "function stage3Splice" not in js
    assert "stage3Splice()" not in js
    assert "sv-stage3-splice" not in js
    assert "short_video_stage3_splice" not in js
    # CSS：splice 卡片样式全删
    assert ".slirn-stage3-splice-card" not in css
    assert ".slirn-stage3-splice-head" not in css
    assert ".slirn-stage3-splice-actions" not in css
    assert ".slirn-stage3-clip-list" not in css
    assert ".slirn-stage3-clip-item" not in css
    assert ".slirn-stage3-clip-title" not in css


def test_stage3_analyze_no_splice_card_in_html(tmp_path):
    """_stage3_analyze 渲染不再含 splice 卡 / splice 按钮。"""
    from fastapi.testclient import TestClient

    from slirn_home.app import build_app

    tid, pid = _make_project(tmp_path)
    _select_first_source(tmp_path, tid, pid)
    assert _run_stage3_groups(tmp_path, tid, pid)["status"] == "done"
    client = TestClient(build_app(tmp_path).app)
    html = client.post("/slirn/api/short_video_get", json={
        "task_id": tid, "project_id": pid,
    }).json()["html"]
    assert "短视频截取拼接" not in html
    assert "data-action=\"sv-stage3-splice\"" not in html


def test_stage3_splice_endpoint_returns_404(tmp_path):
    """移除的 splice 端点必须 404（防止误调残留代码）。"""
    from fastapi.testclient import TestClient

    from slirn_home.app import build_app

    tid, pid = _make_project(tmp_path)
    client = TestClient(build_app(tmp_path).app)
    r = client.post("/slirn/api/short_video_stage3_splice", json={
        "task_id": tid, "project_id": pid,
    })
    assert r.status_code == 404


# ---------- REQ-20261013-113：预览片段逐段播放 —— 引擎修复 ----------

def test_preview_segment_engine_skips_seeking_heuristic():
    """JS 引擎用 expectedSeek 标志区分自动跳转 vs 用户拖动。"""
    js = (FUNCLIP_ROOT / "slirn_home" / "static" / "short_video.js").read_text(encoding="utf-8")
    # expectedSeek 标志在跳段前 + 初始 seek 前置 true
    assert "var expectedSeek" in js
    assert "expectedSeek = true" in js
    # onSeeking 不再用「越界 = 手动」启发式（已替换为 expectedSeek 消费）
    # 旧启发式残留检测：onSeeking 内不再有「t > cur[1] + 0.05」分支
    seg_idx = js.find("function onSeeking")
    if seg_idx >= 0:
        onSeeking_body = js[seg_idx:js.find("function onTime", seg_idx)]
        # onSeeking 只检查 expectedSeek + manual，越界启发式已删
        assert "expectedSeek = false" in onSeeking_body
        assert "cur[1] + 0.05" not in onSeeking_body or "expectedSeek" in onSeeking_body


# ---------- REQ-20261013-115：拖拽后「↩️ 还原顺序」按钮就地刷新 ----------

def test_drag_reorder_refreshes_restore_btn_inline():
    """拖拽完成后，JS 必须就地调用 _refreshRestoreBtn(hlIndex) 同步按钮的
    disabled 态。否则 server 旧渲染的 disabled HTML 一直锁住按钮，用户看到
    行序已乱但按钮仍 disabled，等同「按钮失灵」。
    """
    js = (FUNCLIP_ROOT / "slirn_home" / "static" / "short_video.js").read_text(encoding="utf-8")
    # 1. _refreshRestoreBtn 函数存在
    assert "function _refreshRestoreBtn" in js
    # 2. 拖拽成功的 then 分支里有 _refreshRestoreBtn 调用
    drop_idx = js.find("'sv-stage3-restore-order'")
    # 找到 bindSubtitleSort 内部的成功回调
    success_idx = js.find("字幕顺序已更新（下游 Stage 4-7 已重置）")
    assert success_idx > 0
    success_body = js[success_idx: success_idx + 800]
    assert "_refreshRestoreBtn" in success_body
    # 3. 解析 row 编号的口径 = 读 .slirn-stage3-hl-no 节点文本
    fn_idx = js.find("function _refreshRestoreBtn")
    assert fn_idx > 0
    fn_body = js[fn_idx: fn_idx + 1200]
    assert "slirn-stage3-hl-no" in fn_body
    # 4. 按升序判「已是原序」（与 server is_original = nos == sorted(nos) 一致）
    assert "n >= arr[i - 1]" in fn_body
    # 5. 失败回滚分支不能调 _refreshRestoreBtn（行序未变，按钮态也不该变）
    # 抽取 success 与 fail 各自的函数体，按出现的 } else { 分界
    fail_marker = "字幕重排失败：' + (data.body"
    fail_idx = js.find(fail_marker)
    assert fail_idx > 0
    fail_body = js[fail_idx: fail_idx + 400]
    # fail_body 末尾可能在 success_body 之后；只看 fail_body 自身前 200 字符
    # 应只含 showToast（用于提示失败），不应含 _refreshRestoreBtn
    fail_short = fail_body[:200]
    assert "_refreshRestoreBtn" not in fail_short, (
        "失败回滚分支不应调 _refreshRestoreBtn（行序未变，按钮态也不该变）"
    )


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
    # REQ-20261013-112：截取拼接已移除，删除 hl 时不再需要预先标 splice=done

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
    # REQ-20261013-112：截取拼接已移除
    assert 'data-action="sv-stage3-splice"' not in html
    # 修订九：删光后页签栏也不渲染（回「解析后将列出」空态）
    assert "slirn-stage3-hl-tabs" not in html


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


# ---------- 修订九：N 条短视频逐条页签（tab bar + 面板） ----------

def test_stage3_ui_tabs_per_hl(tmp_path):
    """每条短视频一个页签：tab 数 = 条数、首签（#1）默认激活、
    面板一一对应且只有一个显示；行本身的 DOM 结构不变（旧断言不受影响）。"""
    from fastapi.testclient import TestClient

    from slirn_home.app import build_app

    tid, pid = _make_project(tmp_path)
    _select_first_source(tmp_path, tid, pid)
    assert _run_stage3_groups(tmp_path, tid, pid)["status"] == "done"

    client = TestClient(build_app(tmp_path).app)
    html = client.post("/slirn/api/short_video_get", json={
        "task_id": tid, "project_id": pid,
    }).json()["html"]

    # tab bar：2 组 → 2 个页签（#1 主题B 重编后是第一组）
    assert 'class="slirn-stage3-hl-tabs"' in html
    assert html.count('data-action="sv-stage3-hl-tab"') == 2
    # 首签默认激活（组 1 = 主题B）；非 active 的 tab 无标记
    assert 'class="slirn-stage3-hl-tab active" data-action="sv-stage3-hl-tab" data-hl-index="1"' in html
    assert html.count('slirn-stage3-hl-tab active"') == 1
    assert "#1 主题B" in html and "#2 主题A" in html
    # 面板：每条一个 pane，仅激活页显示；行 class 保持原样（既有断言不破）
    assert html.count('class="slirn-stage3-hl-pane"') == 1
    assert html.count('class="slirn-stage3-hl-pane active"') == 1
    assert html.count('class="slirn-stage3-hl-row"') == 2


def test_stage3_ui_tab_label_truncation(tmp_path, monkeypatch):
    """页签标签超长截断（≤10 字 + …）；完整标题仍在 tooltip / 标题输入框里。"""
    from fastapi.testclient import TestClient

    from slirn_home.app import build_app

    tid, pid = _make_project(tmp_path)
    assert _run_stage3_groups(tmp_path, tid, pid)["status"] == "done"
    monkeypatch.setattr(
        revision_service, "_call_llm",
        lambda s, u, **kw: (
            "# 1 | 一个特别特别特别长的短视频标题一定会被截断\n"
            "1. [00:00:01,000-00:00:02,000] 第一句\n"
        ),
    )
    assert sva.rerun_stage3_one(tmp_path, tid, pid, 1)["status"] == "done"

    client = TestClient(build_app(tmp_path).app)
    html = client.post("/slirn/api/short_video_get", json={
        "task_id": tid, "project_id": pid,
    }).json()["html"]
    assert "#1 一个特别特别特别长的…" in html            # 10 字截断 + …
    assert "一个特别特别特别长的短视频标题一定会被截断" in html  # tooltip/输入框仍完整


def test_stage3_hl_tabs_wiring():
    """页签切换 JS（switchHlTab + 分发 + 重渲染后恢复记忆页签）与 CSS 显示规则。"""
    js = (FUNCLIP_ROOT / "slirn_home" / "static" / "short_video.js").read_text(encoding="utf-8")
    css = (FUNCLIP_ROOT / "slirn_home" / "static" / "home.css").read_text(encoding="utf-8")
    # 切换函数 + 点击分发
    assert "function switchHlTab(" in js
    assert "action === 'sv-stage3-hl-tab'" in js
    # 重渲染（restore/rerun/删除/拼接都走 openProject）后恢复用户选中的页签；
    # 记忆带项目 key（换项目不串页签），已删除的条目回默认第一条
    assert "function _restoreHlTab(" in js
    assert "_restoreHlTab(taskId, projectId);" in js
    assert "window._slirnSvHlTab" in js
    # CSS：面板默认隐藏、激活显示；tab 单行横排可横滚
    # REQ-20261013-109：stage3 与 stage4 共用选择器（逗号并列）
    assert ".slirn-stage3-hl-tab.active," in css
    assert ".slirn-stage4-hl-tab.active" in css
    assert ".slirn-stage3-hl-pane," in css
    assert ".slirn-stage4-hl-pane" in css
    assert "display: none" in css.split(".slirn-stage3-hl-pane,", 1)[1][:200]
    assert "display: block" in css.split(".slirn-stage3-hl-pane.active,", 1)[1][:200]
    assert "overflow-x: auto" in css.split(".slirn-stage3-hl-tabs,", 1)[1][:200]


# ---------- 修订十：系统级提示词 + 「🧭 引用系统提示词」按钮 ----------

def test_stage3_system_prompt_constant():
    """系统级提示词内置在服务层：独立于默认/历史两级存储，内容为用户
    提供的完整版（选题类型 / 选行规则 / 切片筛选 / 内容质量门槛 / 输出前自检）。"""
    s = svc.STAGE3_SYSTEM_PROMPT
    assert s.strip().startswith("你是")
    for key in (
        "【干货】三步搭建AI脚本工作流",   # 组头示例（与解析器 # N | 标题 格式一致）
        "选行规则",
        "内容质量门槛",
        "输出前自检",
        "宁缺毋滥",
    ):
        assert key in s, f"系统提示词缺少关键段: {key}"
    # 只管选题与选行标准；输出格式约束由 run_stage3 拼 user 尾部，这里不复述
    assert "只输出" not in s


def test_stage3_ui_use_system_prompt_button(tmp_path):
    """「🧭 引用系统提示词」按钮位于「💾 设为默认」之前，data-prompt
    携带系统提示词全文（HTML 转义）；未跑解析的项目也要渲染（提示词 UI
    不依赖 highlights）。"""
    from fastapi.testclient import TestClient

    from slirn_home.app import build_app
    from slirn_home.short_video_ui import esc

    tid, pid = _make_project(tmp_path)
    client = TestClient(build_app(tmp_path).app)
    html = client.post("/slirn/api/short_video_get", json={
        "task_id": tid, "project_id": pid,
    }).json()["html"]

    assert "引用系统提示词" in html
    i_sys = html.index('data-action="sv-stage3-use-system-prompt"')
    i_def = html.index('data-action="sv-stage3-save-default"')
    assert i_sys < i_def, "引用系统提示词按钮必须在「设为默认」前面"
    assert 'data-prompt="' in html[i_sys:i_def], "按钮须携带 data-prompt 全文"
    assert esc(svc.STAGE3_SYSTEM_PROMPT) in html, "系统提示词全文需嵌入页面"


def test_stage3_system_prompt_enforces_60_90_duration():
    """REQ-20261013-114：60-90 秒时长硬约束必须显式落在系统提示词里。
    提示词要：① 明确「必须落在 60-90 秒区间」② 给出时长计算口径 ③ 给出
    凑时长 / 压时长的具体手段 ④ 自检环节把时长检查放第一位。"""
    s = svc.STAGE3_SYSTEM_PROMPT
    # 1) 60-90 秒硬约束（service 提示词用 ASCII 连字符表述）
    assert "60-90" in s, "系统提示词缺 60-90 秒硬约束"
    # 2) 时长计算口径（以行末标注真实秒数为准，禁止字符估算）
    assert ("（X.Xs）" in s) and ("标注" in s), \
        "系统提示词缺时长计算口径（行末秒数标注求和）"
    assert "(本行字符数 + 1) / 4" not in s, \
        "字符数公式对快语速课程高估约一倍，必须移除"
    # 3) 凑时长 / 压时长手段
    for key in ("时长 < 60", "时长 > 90", "补行", "砍行"):
        assert key in s, f"系统提示词缺凑/压时长手段: {key}"
    # 4) 时长自检放第一位
    dur_check_idx = s.find("时长硬约束自检")
    other_check_idx = s.find("其它标准自检") if "其它标准自检" in s else s.find("信息密度")
    assert dur_check_idx > 0, "系统提示词缺「时长硬约束自检」段"
    assert other_check_idx > 0
    assert dur_check_idx < other_check_idx, "时长自检必须在其它标准之前"


def test_stage3_use_system_prompt_wiring():
    """前端接线：引用函数（覆盖确认 + 独立 toast）+ 点击分发 + JS 语法。"""
    js = (FUNCLIP_ROOT / "slirn_home" / "static" / "short_video.js").read_text(encoding="utf-8")
    assert "function stage3UseSystemPrompt(" in js
    assert "action === 'sv-stage3-use-system-prompt'" in js
    assert "已引用系统提示词" in js
    assert "用系统提示词覆盖它？" in js

    r = subprocess.run(
        ["node", "--check", str(FUNCLIP_ROOT / "slirn_home" / "static" / "short_video.js")],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, f"short_video.js 语法错误\nstderr={r.stderr[:1000]}"


# ---------- REQ-20261015-119：60-90 秒硬约束（秒数标注口径 + 反馈重试） ----------
# 真实故障（20260924-001，行均 2.07s）：旧口径「约 70 秒」无计算方法，
# LLM 自由心证；字符公式又高估一倍 → 实际拼出 32-41s。

def _fmt_ts(ms: int) -> str:
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms2 = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms2:03d}"


def _dur_srt(n_lines: int = 40) -> str:
    """n 行、每行恰 2s（行间隔 1s）的 SRT——行时长可直接心算。"""
    blocks = []
    for i in range(1, n_lines + 1):
        start = (i - 1) * 3000
        blocks.append(f"{i}\n{_fmt_ts(start)} --> {_fmt_ts(start + 2000)}\n内容第{i}句\n")
    return "\n".join(blocks) + "\n"


def _group_text(title: str, line_ids) -> str:
    """按 _dur_srt 的行号集合构造分组输出（行号从 1 连续编）。"""
    rows = []
    for no, i in enumerate(line_ids, start=1):
        start = (i - 1) * 3000
        rows.append(f"{no}. [{_fmt_ts(start)}-{_fmt_ts(start + 2000)}] 内容第{i}句")
    return f"# 1 | {title}\n" + "\n".join(rows) + "\n"


def _make_project_dur(tmp_path: Path):
    tid, pid = _make_project(tmp_path)
    svc.stage_raw_srt_path(tmp_path, tid, pid).write_text(
        _dur_srt(40), encoding="utf-8",
    )
    return tid, pid


def test_annotate_srt_seconds_and_strip():
    """每行文本末尾追加真实秒数；多行文本标在最后一行；误抄可清洗。"""
    srt = (
        "1\n00:00:01,000 --> 00:00:02,500\n第一句\n\n"
        "2\n00:00:05,000 --> 00:00:06,000\n第二句上半\n第二句下半\n\n"
    )
    out = sva._annotate_srt_seconds(srt)
    assert "第一句（1.5s）" in out
    assert "第二句上半\n第二句下半（1.0s）" in out
    # 标注后仍可被正常解析（时间轴未被破坏）
    lines = sva._parse_srt_lines(out)
    assert [ln.index for ln in lines] == [1, 2]
    assert [ln.text for ln in lines] == ["第一句（1.5s）", "第二句上半 第二句下半（1.0s）"]
    # strip_duration_tag 只清行尾标注
    assert sva.strip_duration_tag("内容第3句（2.0s）") == "内容第3句"
    assert sva.strip_duration_tag("内容第3句(2.5 s)") == "内容第3句"
    assert sva.strip_duration_tag("正常（括号）文本") == "正常（括号）文本"


def test_upstream_prompt_and_constraint_hard_duration():
    """默认链 system + 输出约束都换 60–90 硬口径，「约70秒」不得残留。"""
    sys_p = sva._UPSTREAM_CLIP_SYSTEM_PROMPT.format(
        n=4, **sva._duration_prompt_ctx(60.0, 90.0))
    assert "60–90" in sys_p
    assert "（X.Xs）" in sys_p, "必须声明以行末秒数标注为求和口径"
    assert "30–45 行" in sys_p, "行数指引 = 区间半值（60/90 → 30–45 行）"
    assert "约70秒" not in sys_p and "约 70 秒" not in sys_p

    c = sva._GROUPS_OUTPUT_CONSTRAINT.format(
        n=4, **sva._duration_prompt_ctx(60.0, 90.0))
    assert "60–90" in c
    assert "不要复制（X.Xs）秒数标注" in c


def test_duration_feedback_and_retry_warnings():
    """反馈生成：短组写缺口 + 增选行数；超长组写删行；达标组不出现。"""
    short = {"index": 1, "title": "短", "subtitle_lines": [{"start_ms": 0, "end_ms": 10_000}]}
    ok = {"index": 2, "title": "中", "subtitle_lines": [{"start_ms": 0, "end_ms": 65_000}]}
    over = {"index": 3, "title": "长", "subtitle_lines": [{"start_ms": 0, "end_ms": 100_000}]}

    fb = sva.duration_feedback([short, ok, over])
    assert "低于 60s" in fb and "还差 50.0s" in fb
    assert "约需再选 26 行" in fb
    assert "超过 90s 上限 10.0s" in fb
    assert "第 2 组" not in fb, "达标组不产生反馈行"
    assert sva.duration_feedback([ok]) == ""

    ws = sva.duration_retry_warnings([short, ok, over])
    assert any("未达 60s 下限" in w for w in ws)
    assert any("超过 90s 上限" in w for w in ws)
    assert sva.duration_retry_warnings([ok]) == []


def test_run_stage3_duration_retry_fixes_short_group(tmp_path):
    """第一轮 10s 不达标 → 带反馈重试；第二轮 64s → 终态用第二轮结果。"""
    tid, pid = _make_project_dur(tmp_path)
    calls = {"n": 0, "payloads": []}
    short_raw = _group_text("短组", range(1, 6))       # 5 行 × 2s = 10s
    fixed_raw = _group_text("达标组", range(1, 33))    # 32 行 × 2s = 64s

    def fake(sys_p, user_p, **kw):
        calls["n"] += 1
        calls["payloads"].append(user_p)
        return short_raw if calls["n"] == 1 else fixed_raw

    orig = revision_service._call_llm
    revision_service._call_llm = fake
    try:
        state = sva.run_stage3(tmp_path, tid, pid, n_clips=4)
    finally:
        revision_service._call_llm = orig

    assert state["status"] == "done"
    assert calls["n"] == 2, "不达标必须恰好触发一轮重试"
    assert state["duration_retry"] is True
    # 重试 payload = 原载荷 + 反馈
    assert "时长核查反馈" in calls["payloads"][1]
    assert "低于 60s" in calls["payloads"][1]
    assert "还差 50.0s" in calls["payloads"][1]
    # 终态 = 第二轮（32 行、64s、无未达告警）
    doc = json.loads(
        svc.stage_highlights_path(tmp_path, tid, pid).read_text(encoding="utf-8")
    )
    assert len(doc["highlights"][0]["subtitle_lines"]) == 32
    assert state["hl_duration_sec"] == {"1": 64.0}
    assert not any("未达 60s" in w for w in state["warnings"])
    # 两轮原文都落档
    saved = svc.stage_llm_raw_path(tmp_path, tid, pid).read_text(encoding="utf-8")
    assert saved.startswith(short_raw)
    assert "===== 时长反馈重试 =====" in saved
    assert fixed_raw.strip() in saved


def test_run_stage3_no_retry_when_compliant(tmp_path):
    """第一轮就达标 → 只调一次 LLM，duration_retry=False。"""
    tid, pid = _make_project_dur(tmp_path)
    calls = {"n": 0}

    def fake(sys_p, user_p, **kw):
        calls["n"] += 1
        return _group_text("达标组", range(1, 33))     # 64s

    orig = revision_service._call_llm
    revision_service._call_llm = fake
    try:
        state = sva.run_stage3(tmp_path, tid, pid, n_clips=4)
    finally:
        revision_service._call_llm = orig

    assert state["status"] == "done"
    assert calls["n"] == 1
    assert state["duration_retry"] is False
    assert state["hl_duration_sec"] == {"1": 64.0}
    assert not any("未达 60s" in w or "超过 90s" in w for w in state["warnings"])


def test_rerun_one_payload_uses_annotated_srt_and_hard_duration(tmp_path):
    """单条重解析同口径：标注版 SRT + 60–90 + 反馈重试。"""
    tid, pid = _make_project_dur(tmp_path)
    # 先整跑一次得到 highlights.json（直接达标，1 次调用）
    short_then_ok = {"n": 0, "payloads": []}

    def fake(sys_p, user_p, **kw):
        short_then_ok["n"] += 1
        short_then_ok["payloads"].append((sys_p, user_p))
        return _group_text("达标组", range(1, 33))

    orig = revision_service._call_llm
    revision_service._call_llm = fake
    try:
        sva.run_stage3(tmp_path, tid, pid, n_clips=4)
        short_then_ok["n"] = 0
        short_then_ok["payloads"].clear()
        state = sva.rerun_stage3_one(tmp_path, tid, pid, 1)
    finally:
        revision_service._call_llm = orig

    assert state["status"] == "done"
    sys_p, user_p = short_then_ok["payloads"][0]
    assert "60–90" in sys_p, "定向指令须带硬口径"
    assert "内容第1句（2.0s）" in user_p, "重解析 payload 用秒数标注版 SRT"
    assert "60–90" in user_p
    assert "约 70 秒" not in user_p
    assert state["hl_duration_sec"]["1"] == 64.0


# ---------- REQ-20261015-120：每条短视频时长区间设置（蓝色框区） ----------
# 用户要求：UI 可设区间 → 后端直接取值（不再自行推算）→ 拼进提示词 →
# 输出按该区间核查（反馈重试 + 终态告警同口径）。

def test_norm_duration_range_clamps():
    f = sva._norm_duration_range
    assert f(None, None) == (60.0, 90.0)
    assert f(0, 0) == (60.0, 90.0), "0 视为未填 → 默认区间"
    assert f(40, 80) == (40.0, 80.0)
    assert f(5, 1000) == (10.0, 900.0), "离谱值钳到 [10,600]/[+5,900]"
    assert f(80, 40) == (80.0, 85.0), "倒挂时上限抬到下限+5（JS 侧已互换）"
    assert f(float("nan"), 90) == (60.0, 90.0)


def test_run_stage3_custom_duration_range_flows_to_prompt(tmp_path):
    """用户设 40-80 → system/user 提示词、反馈核查、state/doc 全按 40-80。"""
    tid, pid = _make_project_dur(tmp_path)
    calls = {"n": 0, "sys": "", "user": ""}
    ok_raw = _group_text("达标组", range(1, 26))   # 25 行 × 2s = 50s ∈ [40, 80]

    def fake(sys_p, user_p, **kw):
        calls["n"] += 1
        calls["sys"] = sys_p
        calls["user"] = user_p
        return ok_raw

    orig = revision_service._call_llm
    revision_service._call_llm = fake
    try:
        state = sva.run_stage3(
            tmp_path, tid, pid, n_clips=4,
            duration_min_sec=40, duration_max_sec=80,
        )
    finally:
        revision_service._call_llm = orig

    assert state["status"] == "done"
    assert calls["n"] == 1, "50s 在 40-80 内 → 不触发重试"
    assert "40–80 秒" in calls["sys"] and "40–80 秒" in calls["user"]
    assert "60–90" not in calls["sys"] and "60–90" not in calls["user"]
    assert "20–40 行" in calls["sys"], "行数指引按新区间折算"
    assert state["duration_range"] == [40.0, 80.0]
    assert state["hl_duration_sec"] == {"1": 50.0}
    doc = json.loads(
        svc.stage_highlights_path(tmp_path, tid, pid).read_text(encoding="utf-8")
    )
    assert doc["duration_range"] == [40.0, 80.0]


def test_run_stage3_custom_range_feedback_thresholds(tmp_path):
    """同一 10s 结果：默认区间要重试，区间 5-15 直接达标（阈值跟区间走）。"""
    tid, pid = _make_project_dur(tmp_path)
    short_raw = _group_text("短组", range(1, 6))    # 10s

    # ① 区间 [5, 15]：10s 达标 → 一次调用
    calls = {"n": 0}
    orig = revision_service._call_llm
    revision_service._call_llm = lambda s, u, **kw: (calls.__setitem__("n", calls["n"] + 1) or short_raw)
    try:
        st = sva.run_stage3(tmp_path, tid, pid, n_clips=4,
                            duration_min_sec=5, duration_max_sec=15)
    finally:
        revision_service._call_llm = orig
    assert st["duration_retry"] is False
    assert calls["n"] == 1
    assert st["duration_range"] == [10.0, 15.0], "下限 5 被钳到最小 10"

    # ② 默认 60-90：10s 不达标 → 重试一轮
    calls["n"] = 0
    revision_service._call_llm = lambda s, u, **kw: (calls.__setitem__("n", calls["n"] + 1) or short_raw)
    try:
        st2 = sva.run_stage3(tmp_path, tid, pid, n_clips=4)
    finally:
        revision_service._call_llm = orig
    assert st2["duration_retry"] is True
    assert calls["n"] == 2
    assert st2["duration_range"] == [60.0, 90.0]


def test_rerun_one_falls_back_to_doc_duration_range(tmp_path):
    """单条重解析不带区间 → 回退 doc 里上次整跑记录的区间。"""
    tid, pid = _make_project_dur(tmp_path)
    captured = {"user": ""}

    def fake(sys_p, user_p, **kw):
        captured["user"] = user_p
        return _group_text("达标组", range(1, 26))  # 50s

    orig = revision_service._call_llm
    revision_service._call_llm = fake
    try:
        sva.run_stage3(tmp_path, tid, pid, n_clips=4,
                       duration_min_sec=40, duration_max_sec=80)
        sva.rerun_stage3_one(tmp_path, tid, pid, 1)   # 不带区间参数
    finally:
        revision_service._call_llm = orig

    assert "40–80 秒" in captured["user"], "rerun 应回退 doc 记录的 40-80"


def test_stage3_duration_inputs_rendered():
    """设置区渲染两个区间输入（默认 60/90；随上次运行区间回填）+ 越界 ⚠。"""
    from slirn_home import short_video_ui as ui

    def _proj(range_, hl_secs):
        lines = [
            {"no": i, "src_index": i, "text": f"行{i}",
             "start_ms": (i - 1) * 3000, "end_ms": (i - 1) * 3000 + hl_secs * 1000}
            for i in range(1, 3)
        ]
        return {
            "id": "p1", "task_id": "t1", "name": "d", "materials": [],
            "pipeline": {
                "stage1_source": {"status": "done"},
                "stage3_analyze": {
                    "status": "done",
                    "duration_range": range_,
                    "highlights": [{
                        "index": 1, "id": "h1", "title": "t",
                        "start_ms": 0, "end_ms": 60000,
                        "subtitle_lines": lines,
                    }],
                },
            },
        }

    # ① 无记录 → 默认 60/90
    html = ui.render_project(_proj(None, 30))
    assert 'id="slirn-stage3-dmin" value="60"' in html
    assert 'id="slirn-stage3-dmax" value="90"' in html
    # ② 上次 40/75 → 回填；两行各 30s = 合计 60s ∈ [40,75] → 无 ⚠
    html = ui.render_project(_proj([40, 75], 30))
    assert 'id="slirn-stage3-dmin" value="40"' in html
    assert 'id="slirn-stage3-dmax" value="75"' in html
    assert "字幕合计 60s" in html and "⚠️" not in html
    # ③ 合计 60s < 下限 70 → ⚠ 未达
    html = ui.render_project(_proj([70, 90], 30))
    assert "字幕合计 60s ⚠️ 未达 70s" in html


def test_stage3_duration_range_wiring():
    """端点透传 + JS 读取/发送接线（source-grep 守卫）。"""
    app_src = (FUNCLIP_ROOT / "slirn_home" / "app.py").read_text(encoding="utf-8")
    assert "duration_min_sec=duration_min_sec" in app_src
    assert "duration_max_sec=duration_max_sec" in app_src
    assert app_src.count("body.get(\"duration_min_sec\")") == 2, (
        "run + rerun_one 两个端点都要透传"
    )

    js = (FUNCLIP_ROOT / "slirn_home" / "static" / "short_video.js").read_text(encoding="utf-8")
    assert "function _stage3DurationRange" in js
    assert "duration_min_sec: dur[0], duration_max_sec: dur[1]" in js
    assert js.count("duration_min_sec: dur[0]") == 2, (
        "stage3Run + stage3RerunOne 都要带区间"
    )
