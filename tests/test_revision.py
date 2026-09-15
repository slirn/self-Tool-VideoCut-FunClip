"""测试字幕修订服务 — REQ-20260915-005。

单元层（LLM 全 mock，不打网络）：解析防御、决策合并、后台 job 落盘、
渲染三态、阶段状态推进。真实 LLM 链路走 E2E（work/REQ-20260915-005-subtitle-review/）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

FUNCLIP_ROOT = Path(__file__).resolve().parent.parent
SLIRN_STANDALONE = FUNCLIP_ROOT.parent / "slirn-standalone"

if str(FUNCLIP_ROOT) not in sys.path:
    sys.path.insert(0, str(FUNCLIP_ROOT))
if SLIRN_STANDALONE.exists() and str(SLIRN_STANDALONE) not in sys.path:
    sys.path.insert(0, str(SLIRN_STANDALONE))


def _make_mgr(tmp_path: Path):
    from tasklib import TaskManager

    video = tmp_path / "lecture.mp4"
    video.write_bytes(b"fake-video")
    return TaskManager(tmp_path), video


def _write_subtitle(mgr, tid: str, segs: list[dict]) -> Path:
    outputs = mgr.tasks_dir / tid / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    (outputs / "subtitle.json").write_text(
        json.dumps({"version": 1, "segments": segs}, ensure_ascii=False), encoding="utf-8"
    )
    return outputs


SEGS = [
    {"i": 1, "start_ms": 0, "end_ms": 1500, "start": "00:00:00.000", "end": "00:00:01.500", "text": "嗯嗯大家好"},
    {"i": 2, "start_ms": 1500, "end_ms": 4000, "start": "00:00:01.500", "end": "00:00:04.000", "text": "今天讲第一课"},
    {"i": 3, "start_ms": 4000, "end_ms": 7000, "start": "00:00:04.000", "end": "00:00:07.000", "text": "那个那个就是说我们开始吧"},
]


# ---------- parse_llm_suggestions 防御 ----------

def test_parse_basic_with_fence():
    from slirn_home.revision_service import parse_llm_suggestions

    raw = """```json
    [{"i": 1, "category": "split", "keep_text": "大家好", "note": "连续「嗯」为语气词"},
     {"i": 2, "category": "keep", "keep_text": null, "note": "正常内容"},
     {"i": 3, "category": "delete", "note": "口头禅「那个」「就是说」"}]
    ```"""
    out = parse_llm_suggestions(raw, SEGS)
    assert [o["i"] for o in out] == [1, 2, 3]
    assert out[0]["category"] == "split" and out[0]["keep_text"] == "大家好"
    assert out[1]["category"] == "keep" and out[1]["keep_text"] is None
    assert out[2]["category"] == "delete"


def test_parse_defensive_fills_and_normalizes():
    from slirn_home.revision_service import parse_llm_suggestions

    raw = json.dumps([
        {"i": 1, "category": "maybe_delete", "note": ""},   # 未知类别 → review；空说明 → 兜底
        {"i": 99, "category": "keep", "note": "未知段丢弃"},
        {"category": "keep", "note": "缺 i 丢弃"},
        {"i": 3, "category": "split", "keep_text": "  ", "note": "split 无有效保留文本"},
    ])
    out = parse_llm_suggestions(raw, SEGS)
    assert len(out) == 3  # 条目数 == 段数（漏答回填）
    assert out[0]["category"] == "review" and out[0]["note"] == "模型未给出说明"
    assert out[1]["category"] == "review" and out[1]["note"] == "模型未返回该段，请人工复核"
    assert out[2]["category"] == "split" and out[2]["keep_text"] is None


def test_parse_invalid_json_raises():
    from slirn_home.revision_service import parse_llm_suggestions

    try:
        parse_llm_suggestions("抱歉我不能输出 JSON", SEGS)
    except ValueError as e:
        assert "JSON" in str(e)
    else:
        raise AssertionError("应抛 ValueError")


def test_parse_tolerates_surrounding_text():
    from slirn_home.revision_service import parse_llm_suggestions

    raw = '分析结果如下：\n[{"i": 1, "category": "keep", "note": "x"}]\n以上。'
    out = parse_llm_suggestions(raw, SEGS)
    assert out[0]["category"] == "keep"


# ---------- 决策合并 ----------

def test_merge_decisions_apply_and_skip():
    from slirn_home.revision_service import merge_decisions

    rev = {"entries": [
        {"i": 1, "decision": "pending", "user_note": ""},
        {"i": 2, "decision": "pending", "user_note": ""},
    ]}
    rev2, applied = merge_decisions(rev, [
        {"i": 1, "decision": "accept", "user_note": "同意删语气词"},
        {"i": 2, "decision": "wrong", "user_note": "非法类别跳过"},
        {"i": 9, "decision": "keep", "user_note": "未知段跳过"},
        {"decision": "keep"},  # 缺 i 跳过
    ])
    assert applied == 1
    assert rev2["entries"][0]["decision"] == "accept"
    assert rev2["entries"][0]["user_note"] == "同意删语气词"
    assert rev2["entries"][1]["decision"] == "pending"


def test_all_decided():
    from slirn_home.revision_service import all_decided

    assert not all_decided({"entries": []})
    assert not all_decided({"entries": [{"decision": "pending"}, {"decision": "keep"}]})
    assert all_decided({"entries": [{"decision": "accept"}, {"decision": "delete"}]})


# ---------- 后台 job（mock LLM）----------

def test_start_job_writes_revision(tmp_path: Path, monkeypatch):
    from slirn_home import revision_service

    outputs = tmp_path / "outputs"
    monkeypatch.setattr(
        revision_service, "_call_llm",
        lambda sys_p, user, retries=1: json.dumps([
            {"i": 1, "category": "delete", "note": "语气词"},
            {"i": 2, "category": "keep", "note": "保留"},
            {"i": 3, "category": "split", "keep_text": "我们开始吧", "note": "去重复"},
        ]),
    )
    assert revision_service.start_job("t1", SEGS, "任务A", ["热词"], outputs)
    # 等 job 结束
    import time
    for _ in range(100):
        j = revision_service.job_status("t1")
        if j and j["state"] in ("done", "error"):
            break
        time.sleep(0.05)
    assert j["state"] == "done", j
    rev = revision_service.load_revision(outputs)
    assert rev is not None and rev["segments_count"] == 3
    entries = rev["entries"]
    # 每条齐备：建议 + 说明 + 用户决策字段（AC-2）
    for e, seg in zip(entries, SEGS):
        assert e["text"] == seg["text"] and e["category"] and e["note"]
        assert e["decision"] == "pending" and e["user_note"] == ""
    assert entries[2]["keep_text"] == "我们开始吧"


def test_start_job_llm_error(tmp_path: Path, monkeypatch):
    """LLM 失败（如未配置 Key）→ job error 态，不落盘。"""
    import time

    from slirn_home import revision_service

    def _boom(*a, **k):
        raise RuntimeError("未配置 DASHSCOPE_API_KEY（无法调用大模型）")

    outputs = tmp_path / "outputs"
    monkeypatch.setattr(revision_service, "_call_llm", _boom)
    assert revision_service.start_job("t2", SEGS, "任务B", [], outputs)
    for _ in range(100):
        j = revision_service.job_status("t2")
        if j and j["state"] in ("done", "error"):
            break
        time.sleep(0.05)
    assert j["state"] == "error" and "DASHSCOPE" in j["error"], j
    assert not (outputs / "revision.json").exists()


# ---------- 渲染三态 ----------

def test_render_revision_zone_states(tmp_path: Path):
    from slirn_home.app import _render_revision_zone

    m, video = _make_mgr(tmp_path)
    t = m.create(name="修订任务", original_video=video)
    tid = t.task_id

    # 状态 1：无字幕 → 引导
    h1 = _render_revision_zone(tid, m.get(tid), m)
    assert "请先完成上一阶段「字幕生成」" in h1
    assert 'data-action="wb-stage" data-pane="subtitle"' in h1

    # 状态 2：有字幕无建议 → 分析按钮 + 类别说明
    _write_subtitle(m, tid, SEGS)
    h2 = _render_revision_zone(tid, m.get(tid), m)
    assert "🤖 大模型分析字幕" in h2 and 'data-action="revise-subtitle"' in h2
    assert "整行删除" in h2 and "完整保留" in h2 and "切分修剪" in h2 and "人工复核" in h2

    # 状态 3：有建议 → 每行 原字幕+建议+决策（AC-3）
    outputs = m.tasks_dir / tid / "outputs"
    (outputs / "revision.json").write_text(json.dumps({
        "version": 1, "model": "qwen-plus", "created_at": "2026-09-15T10:00:00",
        "saved_at": None, "segments_count": 3,
        "entries": [
            {"i": 1, "start_ms": 0, "end_ms": 1500, "start": "00:00:00.000", "end": "00:00:01.500",
             "text": "嗯嗯大家好", "category": "split", "keep_text": "大家好",
             "note": "「嗯」为语气词", "decision": "pending", "user_note": ""},
            {"i": 2, "start_ms": 1500, "end_ms": 4000, "start": "00:00:01.500", "end": "00:00:04.000",
             "text": "今天讲第一课", "category": "keep", "keep_text": None,
             "note": "正常内容", "decision": "accept", "user_note": "同意"},
            {"i": 3, "start_ms": 4000, "end_ms": 7000, "start": "00:00:04.000", "end": "00:00:07.000",
             "text": "那个那个就是说我们开始吧", "category": "delete", "keep_text": None,
             "note": "口头禅", "decision": "pending", "user_note": ""},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    h3 = _render_revision_zone(tid, m.get(tid), m)
    assert 'id="slirn-rev-list"' in h3
    assert h3.count('class="slirn-rev-row"') == 3
    assert h3.count("slirn-rev-badge") >= 3
    assert 'slirn-rev-badge split">切分修剪' in h3
    assert "建议保留：「大家好」" in h3
    # 手动决策下拉 ×3 + 手动说明输入 ×3（预填已保存的决策）
    assert h3.count('class="slirn-rev-select" data-i=') == 3
    assert h3.count('class="slirn-rev-note-input" data-i=') == 3
    assert '<option value="accept" selected>采纳建议</option>' in h3
    assert 'value="同意"' in h3
    assert "已决策 <b>1/3</b>" in h3
    assert 'data-action="save-revision"' in h3
    assert 'data-action="revise-subtitle"' in h3 and 'data-has-revision="1"' in h3


def test_wb_stage_states_with_revision(tmp_path: Path):
    """revision.json 落盘 → 前三阶段 done，粗剪 current。"""
    from slirn_home.app import _wb_stage_states

    m, video = _make_mgr(tmp_path)
    t = m.create(name="t", original_video=video)
    tid = t.task_id
    outputs = m.tasks_dir / tid / "outputs"  # create 时已建
    (outputs / "subtitle.json").write_text(json.dumps({"version": 1, "segments": SEGS}), encoding="utf-8")
    (outputs / "revision.json").write_text(
        json.dumps({"version": 1, "entries": [{"i": 1, "decision": "accept"}]}), encoding="utf-8"
    )
    assert _wb_stage_states(m.get(tid)) == ["done", "done", "done", "current"] + ["pending"] * 4
