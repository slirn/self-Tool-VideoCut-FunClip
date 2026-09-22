"""优化字幕服务测试 — REQ-20260917-030。

纯函数口径：解析防御（before 摘抄/丢弃/重叠剔除）、词频与对应关系聚合、
替换应用（new_text）、SRT 生成、决策落盘（人工编辑值/全量口径）。
start_job 走 monkeypatch（ASR 与 LLM 均假实现，不触网不载模型）。
"""
from __future__ import annotations

import json
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

from slirn_home import optimize_service as osvc

SEGS = [
    {"i": 1, "start_ms": 0, "end_ms": 2000, "start": "00:00:00,000", "end": "00:00:02,000",
     "text": "今天讲一下神精网络"},
    {"i": 2, "start_ms": 3000, "end_ms": 5000, "start": "00:00:03,000", "end": "00:00:05,000",
     "text": "爱在很多行业都有应用"},
    {"i": 3, "start_ms": 6000, "end_ms": 8000, "start": "00:00:06,000", "end": "00:00:08,000",
     "text": "神经网洛这个词很形象"},
]
LINES = [{"id": str(s["i"]), "text": s["text"]} for s in SEGS]
HOTWORDS = ["AI", "神经网络"]

RAW = '''```json
[{"id": "1", "unclear": [{"before": "神精网络", "after": "神经网络", "reason": "术语误写"}]},
 {"id": "2", "unclear": [{"before": "爱", "after": "AI", "reason": "热词冲突"}]},
 {"id": "3", "unclear": [{"before": "神经网洛", "after": "神经网络", "reason": "同音误写"}]},
 {"id": "999", "unclear": [{"before": "x", "after": "y"}]},
 {"id": "2", "unclear": "不是数组"},
 {"id": "3", "unclear": [{"before": "不在这行里", "after": "神经网络"}]}]
```'''


# =============== _normalize_occ / parse_occurrences ===============

def test_normalize_ok():
    r = osvc._normalize_occ({"before": "神精网络", "after": "神经网络", "reason": "术语"}, SEGS[0]["text"])
    assert r == {"before": "神精网络", "after": "神经网络", "reason": "术语"}


def test_normalize_rejects():
    text = SEGS[0]["text"]
    assert osvc._normalize_occ({"before": "深度学习", "after": "神经网络"}, text) is None  # 摘抄幻觉
    assert osvc._normalize_occ({"before": "神精网络", "after": "神精网络"}, text) is None  # 无意义
    assert osvc._normalize_occ({"before": "", "after": "AI"}, text) is None  # 空
    assert osvc._normalize_occ(None, text) is None


def test_parse_fenced_and_filtering():
    """围栏剥离；未知 id / 非数组 unclear / before 不在文 → 丢弃；reason 可缺省。"""
    out = osvc.parse_occurrences(RAW, LINES)
    assert set(out) == {"1", "2", "3"}
    assert out["1"][0]["pos"] == 5  # 「今天讲一下」5 字之后（0 基）
    assert out["2"][0]["after"] == "AI" and out["2"][0]["reason"] == "热词冲突"


def test_parse_overlapping_dropped():
    raw = json.dumps([{"id": "1", "unclear": [
        {"before": "神精网络", "after": "神经网络"},
        {"before": "神精", "after": "神经"}]}], ensure_ascii=False)
    out = osvc.parse_occurrences(raw, LINES)
    assert len(out["1"]) == 1  # 重叠只留一个


def test_parse_empty_and_bad():
    assert osvc.parse_occurrences("[]", LINES) == {}
    assert osvc.parse_occurrences("说明 [] 收尾", LINES) == {}
    with pytest.raises(ValueError):
        osvc.parse_occurrences("完全不是 JSON", LINES)


# =============== prompt ===============

def test_user_prompt():
    p = osvc.build_user_prompt("测试任务", HOTWORDS, LINES)
    assert "神经网络" in p and "AI" in p and '"id": "2"' in p and "神精网络" in p
    p2 = osvc.build_user_prompt("t", [], LINES)
    assert "（空）" in p2


# =============== 聚合 / 应用 / SRT ===============

def _occs(**over):
    base = [
        {"occ_id": 0, "seg": 1, "pos": 6, "before": "神精网络", "after": "神经网络", "reason": "", "applied": True},
        {"occ_id": 1, "seg": 2, "pos": 0, "before": "爱", "after": "AI", "reason": "", "applied": True},
        {"occ_id": 2, "seg": 3, "pos": 0, "before": "神经网洛", "after": "神经网络", "reason": "", "applied": True},
    ]
    for k, v in over.items():
        base[int(k)]["applied"] = v
    return base


def test_aggregate_words_groups_by_after():
    """同一目标词的两种错误写法 → 同词计 2 次（次数降序）。"""
    words = osvc.aggregate_words(_occs())
    assert words == [{"word": "神经网络", "count": 2}, {"word": "AI", "count": 1}]
    assert osvc.aggregate_words(_occs(**{"0": False, "2": False})) == [{"word": "AI", "count": 1}]


def test_build_mapping_variants():
    m = osvc.build_mapping(_occs())
    assert m[0] == {"after": "神经网络", "count": 2,  # variants 按出现顺序（occ_id 序）
                    "variants": [{"before": "神精网络", "count": 1}, {"before": "神经网洛", "count": 1}]}
    assert m[1]["after"] == "AI"


def test_apply_to_segments():
    segs = osvc.apply_to_segments(SEGS, _occs())
    assert segs[0]["new_text"] == "今天讲一下神经网络"
    assert segs[1]["new_text"] == "AI在很多行业都有应用"
    assert segs[2]["new_text"] == "神经网络这个词很形象"
    # 不采纳 → 无 new_text（保留原文）
    segs2 = osvc.apply_to_segments(SEGS, _occs(**{"1": False}))
    assert "new_text" not in segs2[1]


# =============== REQ-20260922-NNN 整句替换 ===============

def test_apply_to_segments_line_edit_overrides():
    """整句行：new_text 直接用整句文本，跳过该行局部替换；其他行照常。"""
    segs = osvc.apply_to_segments(SEGS, _occs(), [
        {"seg": 1, "text": "今天我们来讲一下神经网络的基本概念"},
    ])
    assert segs[0]["new_text"] == "今天我们来讲一下神经网络的基本概念"  # 整句优先
    assert segs[1]["new_text"] == "AI在很多行业都有应用"  # 其他行局部替换不受影响
    assert segs[2]["new_text"] == "神经网络这个词很形象"


def test_apply_to_segments_line_edit_defensive():
    """非法 seg / 空白文本 / 与原文相同 → 忽略该条（走原局部替换逻辑）。"""
    segs = osvc.apply_to_segments(SEGS, _occs(), [
        {"seg": 99, "text": "未知行"},                      # 未知 seg
        {"seg": "abc", "text": "非法 seg"},                 # 非法 seg
        {"seg": 1, "text": "   "},                          # 空白
        {"seg": 1, "text": "今天讲一下神精网络"},            # == 原文
        {"text": "缺 seg"},                                 # 缺 seg
        "不是 dict",
    ])
    assert segs[0]["new_text"] == "今天讲一下神经网络"  # 回退局部替换
    assert "new_text" not in segs[1] or segs[1]["new_text"]  # 不抛异常即可


def test_apply_to_segments_no_edits_keeps_legacy():
    """不传 line_edits（None）→ 行为与旧版完全一致。"""
    segs = osvc.apply_to_segments(SEGS, _occs())
    segs2 = osvc.apply_to_segments(SEGS, _occs(), None)
    assert segs == segs2


def test_build_srt_prefers_new_text():
    segs = osvc.apply_to_segments(SEGS, _occs(**{"2": False}))
    srt = osvc.build_srt(segs)
    assert "今天讲一下神经网络" in srt  # 生效替换进 SRT
    assert "神经网洛这个词很形象" in srt  # 未采纳保留原文
    assert "今天讲一下神精网络" not in srt
    assert srt.count("\n\n") >= 2  # 标准空行分隔


# =============== effective_stats ===============

def test_effective_stats():
    data = {"segments": SEGS, "occurrences": _occs(**{"1": False})}
    est = osvc.effective_stats(data)
    assert est == {"lines": 3, "occurrences": 3, "applied": 2, "skipped": 1,
                   "words": 1, "line_edits": 0}


def test_effective_stats_with_line_edits():
    """REQ-20260922-NNN：整句替换行数单列（与局部替换口径正交）。"""
    data = {"segments": SEGS, "occurrences": _occs(),
            "line_edits": [{"seg": 1, "text": "整句一"}, {"seg": 2, "text": "整句二"}]}
    est = osvc.effective_stats(data)
    assert est["line_edits"] == 2 and est["applied"] == 3  # occ 统计不受整句影响


# =============== save_decisions ===============

def _write_opt(tmp_path: Path, occs=None, edits=None) -> Path:
    occs = _occs() if occs is None else occs
    data = {
        "version": 1, "video": "rough_compose.mp4", "model": "m", "provider": "p",
        "protocol": "openai", "created_at": "2026-09-17T10:00:00", "saved_at": None,
        "hotwords": HOTWORDS, "segments": SEGS, "occurrences": occs,
        "words": osvc.aggregate_words(occs), "mapping": osvc.build_mapping(occs),
        "stats": {"lines": 3, "occurrences": 3},
    }
    if edits is not None:
        data["line_edits"] = edits
    p = tmp_path / osvc.OPTIMIZE_JSON
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def test_save_decisions_roundtrip(tmp_path):
    _write_opt(tmp_path)
    # 全量口径：采纳 0/2、不采纳 1（人工改替换值），且 occ 2 未列出 → 不采纳
    data, applied = osvc.save_decisions(tmp_path, [
        {"occ_id": 0, "applied": True, "after": "神经网络"},
        {"occ_id": 1, "applied": False, "after": "AI"},
        {"occ_id": 2, "applied": False, "after": "神经网络"},
    ])
    assert applied == 1 and data["saved_at"]
    assert data["occurrences"][1]["applied"] is False
    assert data["occurrences"][2]["applied"] is False  # 未列出 = 不采纳
    assert data["words"] == [{"word": "神经网络", "count": 1}]
    assert data["segments"][0]["new_text"] == "今天讲一下神经网络"
    assert "new_text" not in data["segments"][1]
    assert data["stats"]["applied"] == 1


def test_save_decisions_edited_after(tmp_path):
    """人工编辑替换值生效；改回原文 = 无效替换 → 服务端兜底不采纳。"""
    _write_opt(tmp_path)
    data, applied = osvc.save_decisions(tmp_path, [
        {"occ_id": 0, "applied": True, "after": "神经网路-改"},   # 人工编辑
        {"occ_id": 1, "applied": True, "after": "爱"},            # = before → 不采纳
        {"occ_id": 2, "applied": True, "after": ""},              # 空 → 不采纳
    ])
    assert applied == 1
    assert data["occurrences"][0]["after"] == "神经网路-改"
    assert data["segments"][0]["new_text"] == "今天讲一下神经网路-改"
    assert data["occurrences"][1]["applied"] is False
    assert data["occurrences"][2]["applied"] is False


def test_save_decisions_missing_file(tmp_path):
    with pytest.raises(RuntimeError):
        osvc.save_decisions(tmp_path, [])


def test_save_decisions_line_edits_roundtrip(tmp_path):
    """REQ-20260922-NNN：整句替换往返落盘 — 校验过滤 + 按 seg 升序 + 整句行跳过局部替换。"""
    _write_opt(tmp_path)
    data, applied = osvc.save_decisions(tmp_path, [
        {"occ_id": 0, "applied": True, "after": "神经网络"},
        {"occ_id": 1, "applied": True, "after": "AI"},
        {"occ_id": 2, "applied": True, "after": "神经网络"},
    ], line_edits=[
        {"seg": 3, "text": "神经网络这个词听起来很形象"},
        {"seg": 1, "text": "今天我们来讲一下神经网络"},   # 乱序 → 落盘按 seg 升序
        {"seg": 2, "text": "   "},                        # 空白 → 丢弃
        {"seg": 99, "text": "未知行"},                    # 未知 seg → 丢弃
        {"seg": 2, "text": "爱在很多行业都有应用"},         # == 原文 → 丢弃
        "不是 dict",
    ])
    assert applied == 3  # occ 生效统计不受整句影响
    assert data["line_edits"] == [
        {"seg": 1, "text": "今天我们来讲一下神经网络"},
        {"seg": 3, "text": "神经网络这个词听起来很形象"},
    ]
    assert data["segments"][0]["new_text"] == "今天我们来讲一下神经网络"  # 整句覆盖局部
    assert data["segments"][1]["new_text"] == "AI在很多行业都有应用"      # 无整句走局部
    assert data["segments"][2]["new_text"] == "神经网络这个词听起来很形象"
    assert data["stats"]["line_edits"] == 2


def test_save_decisions_line_edits_absent_clears(tmp_path):
    """全量口径：不传 line_edits = 清空已有整句替换（DOM = 磁盘镜像）。"""
    _write_opt(tmp_path, edits=[{"seg": 1, "text": "整句旧版"}])
    data, _ = osvc.save_decisions(tmp_path, [
        {"occ_id": 0, "applied": True, "after": "神经网络"},
        {"occ_id": 1, "applied": True, "after": "AI"},
        {"occ_id": 2, "applied": True, "after": "神经网络"},
    ])  # 不传 line_edits
    assert data["line_edits"] == []
    assert data["segments"][0]["new_text"] == "今天讲一下神经网络"  # 恢复局部替换
    assert data["stats"]["line_edits"] == 0


# =============== start_job（monkeypatch：ASR + LLM 假实现） ===============

def test_start_job_writes_artifact(tmp_path, monkeypatch):
    from slirn_home import asr_service, revision_service

    monkeypatch.setattr(asr_service, "_run_recognition",
                        lambda vp, hw, sd: {"sentences": "fake"})
    monkeypatch.setattr(asr_service, "segments_from_sentences", lambda sents: SEGS)
    monkeypatch.setattr(revision_service, "_call_llm", lambda sys, user, entry: RAW)

    started = osvc.start_job("t-1", tmp_path / "rough_compose.mp4", HOTWORDS, "任务",
                             tmp_path, entry={"id": "m", "provider": "p", "protocol": "openai"})
    assert started is True
    assert osvc.start_job("t-1", tmp_path, [], "任务", tmp_path) is False  # 已在跑 → False
    for _ in range(100):
        j = osvc.job_status("t-1")
        if j and j["state"] in ("done", "error"):
            break
        time.sleep(0.05)
    assert j["state"] == "done", j
    assert j["lines"] == 3 and j["occurrences"] == 3
    data = osvc.load_optimize(tmp_path)
    assert data is not None
    assert data["video"] == "rough_compose.mp4" and data["saved_at"] is None
    assert [o["occ_id"] for o in data["occurrences"]] == [0, 1, 2]  # occ_id 连续稳定
    assert data["words"] == [{"word": "神经网络", "count": 2}, {"word": "AI", "count": 1}]
    assert len(data["mapping"]) == 2


def test_start_job_llm_all_fail_still_writes(tmp_path, monkeypatch):
    """LLM 全部失败 → 识别结果 + 空出现项仍落盘（提议失败不阻塞流程）。

    行数 3 < 5 → 不触发零发现复检，行为与旧版一致。
    """
    from slirn_home import asr_service, revision_service

    monkeypatch.setattr(asr_service, "_run_recognition",
                        lambda vp, hw, sd: {"sentences": "fake"})
    monkeypatch.setattr(asr_service, "segments_from_sentences", lambda sents: SEGS)
    monkeypatch.setattr(revision_service, "_call_llm", lambda sys, user, entry: "不是JSON")

    assert osvc.start_job("t-2", tmp_path / "v.mp4", [], "任务", tmp_path)
    for _ in range(100):
        j = osvc.job_status("t-2")
        if j and j["state"] in ("done", "error"):
            break
        time.sleep(0.05)
    assert j["state"] == "done"
    data = osvc.load_optimize(tmp_path)
    assert data["occurrences"] == [] and data["words"] == []


def test_start_job_zero_found_strict_retry(tmp_path, monkeypatch):
    """首轮零发现（≥5 行）→ 严格复检一轮；复检有发现则落盘（REQ-030 复检逻辑）。"""
    from slirn_home import asr_service, revision_service

    segs5 = SEGS + [
        {"i": 4, "start_ms": 9000, "end_ms": 11000, "start": "00:00:09,000",
         "end": "00:00:11,000", "text": "视频视频积分的福利"},
        {"i": 5, "start_ms": 12000, "end_ms": 14000, "start": "00:00:12,000",
         "end": "00:00:14,000", "text": "先把八四这个都开出去"},
    ]
    calls = {"n": 0}

    def fake_call(sys_prompt, user, entry):
        calls["n"] += 1
        if "复检要求" in sys_prompt:
            return ('[{"id": "4", "unclear": [{"before": "视频视频", "after": "视频", '
                    '"reason": "叠字重复"}]}]')
        return "[]"  # 首轮一无所获

    monkeypatch.setattr(asr_service, "_run_recognition",
                        lambda vp, hw, sd: {"sentences": "fake"})
    monkeypatch.setattr(asr_service, "segments_from_sentences", lambda sents: segs5)
    monkeypatch.setattr(revision_service, "_call_llm", fake_call)

    assert osvc.start_job("t-3", tmp_path / "v.mp4", [], "任务", tmp_path)
    for _ in range(100):
        j = osvc.job_status("t-3")
        if j and j["state"] in ("done", "error"):
            break
        time.sleep(0.05)
    assert j["state"] == "done"
    assert calls["n"] == 2  # 首轮 1 批 + 复检 1 批（BATCH_SIZE=40，5 行一批）
    assert j["occurrences"] == 1
    data = osvc.load_optimize(tmp_path)
    assert data["occurrences"][0]["before"] == "视频视频"
    assert data["words"] == [{"word": "视频", "count": 1}]


# =============== 面板渲染 ===============

def _make_mgr(tmp_path: Path):
    from tasklib import TaskManager

    video = tmp_path / "lecture.mp4"
    video.write_bytes(b"fake-video")
    mgr = TaskManager(tmp_path)
    t = mgr.create(name="优化", original_video=video, hotwords=HOTWORDS)
    return mgr, t


def test_render_zone_guide_without_artifact(tmp_path):
    """无粗剪成片 → 引导先去粗剪合成。"""
    from slirn_home.app import _render_optimize_zone

    m, t = _make_mgr(tmp_path)
    html = _render_optimize_zone(t.task_id, m.get(t.task_id), m)
    assert "需要先合成粗剪成片" in html
    assert 'data-pane="rough_compose"' in html


def test_render_zone_start_state(tmp_path, monkeypatch):
    """有成片 + 已注册模型 + 无结果 → 开始态（热词 chips + 开始按钮）。"""
    from slirn_home import llm_config
    from slirn_home.app import _render_optimize_zone

    m, t = _make_mgr(tmp_path)
    outputs = tmp_path / "tasks" / t.task_id / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    (outputs / "rough_compose.mp4").write_bytes(b"fake-mp4")
    monkeypatch.setattr(llm_config, "get_current_entry",
                        lambda root: {"id": "qwen-plus", "provider": "dashscope", "protocol": "openai"})
    html = _render_optimize_zone(t.task_id, m.get(t.task_id), m)
    assert 'data-action="optimize-start"' in html
    assert "神经网络" in html  # 热词回显
    assert "只识别文字，不区分说话人" in html


def test_render_zone_result_state(tmp_path, monkeypatch):
    """有优化结果 → 词频 chips + 行内出现项（输入框/采纳开关）+ 保存/SRT 按钮。"""
    from slirn_home import llm_config
    from slirn_home.app import _render_optimize_zone

    m, t = _make_mgr(tmp_path)
    outputs = tmp_path / "tasks" / t.task_id / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    (outputs / "rough_compose.mp4").write_bytes(b"fake-mp4")
    _write_opt(outputs, occs=_occs(**{"1": False}))
    monkeypatch.setattr(llm_config, "get_current_entry",
                        lambda root: {"id": "qwen-plus", "provider": "dashscope", "protocol": "openai"})
    html = _render_optimize_zone(t.task_id, m.get(t.task_id), m)
    # 词频 chips（可点按筛选）
    assert 'data-action="opt-word" data-word="神经网络"' in html
    assert "神经网络<b>×2</b>" in html
    # 行内出现项：删除线原文 + 替换输入框 + 采纳开关（不采纳的渲染 ✕）
    assert '<s class="slirn-opt-before"' in html and "神精网络</s>" in html
    assert 'class="slirn-opt-after" value="神经网络"' in html
    assert 'data-applied="0"' in html and ">✕</button>" in html
    assert 'data-applied="1"' in html and ">✓</button>" in html
    # 行携带定位播放与词命中数据
    assert 'data-start-ms="0"' in html and "data-words=" in html
    # 保存 / SRT（未确认 → disabled）/ 重新优化
    assert 'data-action="save-optimize"' in html
    assert 'data-action="opt-srt-download"' in html and "disabled" in html
    assert 'data-action="optimize-start"' in html and 'data-has="1"' in html
    # 统计行
    assert "识别 3 行" in html and "不明确 3 处" in html


def test_save_decisions_persists_reviewed(tmp_path):
    """REQ-038：reviewed（处理进度）随保存落盘；未列出/未带 = 未处理。"""
    _write_opt(tmp_path)
    data, _ = osvc.save_decisions(tmp_path, [
        {"occ_id": 0, "applied": True, "after": "神经网络", "reviewed": True},
        {"occ_id": 1, "applied": False, "after": "AI", "reviewed": True},
        {"occ_id": 2, "applied": True, "after": "神经网络"},
    ])
    assert data["occurrences"][0]["reviewed"] is True
    assert data["occurrences"][1]["reviewed"] is True
    assert data["occurrences"][2]["reviewed"] is False


def test_render_zone_word_list_done_flags(tmp_path, monkeypatch):
    """REQ-038：词频列表 + 处理完成标识 + 过滤按钮；occ 带词归属与已处理态。"""
    from slirn_home import llm_config
    from slirn_home.app import _render_optimize_zone

    m, t = _make_mgr(tmp_path)
    outputs = tmp_path / "tasks" / t.task_id / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    (outputs / "rough_compose.mp4").write_bytes(b"fake-mp4")
    occs = _occs()
    occs[0]["reviewed"] = True   # 神经网络 组 1/2 → 未完成
    occs[1]["reviewed"] = True   # AI 组 1/1 → 已完成
    _write_opt(outputs, occs=occs)
    monkeypatch.setattr(llm_config, "get_current_entry",
                        lambda root: {"id": "qwen-plus", "provider": "dashscope", "protocol": "openai"})
    html = _render_optimize_zone(t.task_id, m.get(t.task_id), m)
    # 词行：进度 x/y + 完成标识 + data-done
    assert 'class="slirn-opt-word" data-word="神经网络" data-done="0"' in html
    assert 'class="slirn-opt-word" data-word="AI" data-done="1"' in html
    assert "1/2" in html and "1/1" in html  # 神经网络 1/2 · AI 1/1
    assert "⬜ 未完成" in html and "✅ 已完成" in html
    # 过滤按钮（全部/未完成/已完成）
    for mode in ("all", "todo", "done"):
        assert f'data-action="opt-word-filter" data-mode="{mode}"' in html
    # occ 带词归属 + 已处理态（0 已处理 / 2 未处理）
    assert 'data-word="神经网络" data-reviewed="1"' in html
    assert 'data-word="神经网络" data-reviewed="0"' in html


def test_render_zone_line_edit_full_edit_row(tmp_path, monkeypatch):
    """REQ-20260922-NNN：整句替换 — full-edit 行徽章/覆盖标注 + 行首按钮 + 快捷键提示条。"""
    from slirn_home import llm_config
    from slirn_home.app import _render_optimize_zone

    m, t = _make_mgr(tmp_path)
    outputs = tmp_path / "tasks" / t.task_id / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    (outputs / "rough_compose.mp4").write_bytes(b"fake-mp4")
    _write_opt(outputs, edits=[{"seg": 1, "text": "今天我们来讲一下神经网络"}])
    monkeypatch.setattr(llm_config, "get_current_entry",
                        lambda root: {"id": "qwen-plus", "provider": "dashscope", "protocol": "openai"})
    html = _render_optimize_zone(t.task_id, m.get(t.task_id), m)
    # full-edit 行：类 + 徽章 + 新文本 + 删除线原文
    assert 'class="slirn-opt-row has-occ full-edit" data-id="1"' in html
    assert '<span class="slirn-opt-line-badge">✏️ 整句替换</span>' in html
    assert '<span class="slirn-opt-line-new">今天我们来讲一下神经网络</span>' in html
    assert '<span class="slirn-opt-line-orig" title="原文">今天讲一下神精网络</span>' in html
    # 整句行 occ chip 覆盖标注 + 提示；其他行正常 ✏️
    assert 'class="slirn-opt-occ overridden"' in html
    assert "整句替换已覆盖本行局部替换" in html
    assert 'data-action="opt-line-edit"' in html  # 无整句行是 ✏️，整句行是 ↩️
    assert ">✏️</button>" in html and ">↩️</button>" in html
    # 行 data 属性（前端编辑态/收集用）
    assert 'data-orig-text="今天讲一下神精网络"' in html
    assert 'data-full-edit="1"' in html and 'data-full-edit="0"' in html
    assert 'data-full-text="今天我们来讲一下神经网络"' in html
    # 头部统计
    assert "整句替换 <b>1</b> 行" in html
