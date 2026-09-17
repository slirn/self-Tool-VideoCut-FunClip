"""测试切分修剪阶段关联人员ID — REQ-20260917-031。

单元层（纯函数，无网络/无 ASR）：时间重叠对齐（整段/子段/无重叠/并列取早）、
按人员统计（执行口径保留数）、无 spk 引导、面板按钮渲染、API 端点（有/无 spk）。
完整浏览器链路走 E2E（真实页面点击）。
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


def _seg(i, start_ms, end_ms, text, spk=None, tokens=None, token_ts=None):
    from slirn_home.cutlist_service import ms2srt

    seg = {"i": i, "start_ms": start_ms, "end_ms": end_ms,
           "start": ms2srt(start_ms), "end": ms2srt(end_ms), "text": text}
    if spk is not None:
        seg["spk"] = spk
    if tokens is not None:
        seg["tokens"] = tokens
        seg["token_ts"] = token_ts
    return seg


def _rev(entries: list[dict]) -> dict:
    return {"version": 1, "created_at": "2026-09-17T10:00:00",
            "saved_at": "2026-09-17T10:05:00",
            "segments_count": len(entries), "entries": entries}


# ---------- 纯函数：时间重叠 ----------

def test_overlap_ms():
    from slirn_home.cut_speaker import overlap_ms

    assert overlap_ms(0, 3000, 2000, 5000) == 1000, "交叉 1s"
    assert overlap_ms(0, 3000, 3000, 5000) == 0, "边界相接不算重叠"
    assert overlap_ms(0, 1000, 2000, 3000) == 0, "无重叠"
    assert overlap_ms(0, 5000, 1000, 2000) == 1000, "包含关系取内窗"


def test_link_speakers_whole_and_sub():
    """整段行/切分子段都按重叠对齐；统计按执行口径（含改判/子段标记）。"""
    from slirn_home.cut_speaker import link_speakers
    from slirn_home.cutlist_service import build_cutlist

    # 段1 spk=1 切分（10.1 删除洞 + 10.2 保留块）；段2 spk=2 整段保留
    tokens = list("嗯嗯那个我们开始吧")  # 9 token：嗯嗯那个(0-3) 剔除、我们开始吧(4-8) 保留
    segs = [
        _seg(10, 0, 2680, "嗯嗯那个我们开始吧", spk=1,
             tokens=tokens, token_ts=[[300 * k, 300 * k + 280] for k in range(len(tokens))]),
        _seg(20, 3000, 5000, "同学提问环节", spk=2),
    ]
    rev = _rev([
        {"i": 10, "category": "split", "keep_text": "我们开始吧",
         "decision": "accept", "user_note": ""},
        {"i": 20, "category": "keep", "keep_text": None, "decision": "accept", "user_note": ""},
    ])
    cut = build_cutlist({"segments": segs}, rev)
    link = link_speakers({"segments": segs}, cut)
    assert link["available"] is True
    assert link["rows"] == {"10.1": 1, "10.2": 1, "20": 2}, "子段与整段都标注"
    assert link["stats"] == [
        {"spk": 1, "total": 2, "kept": 1, "deleted": 1},  # 10.1 删除洞
        {"spk": 2, "total": 1, "kept": 1, "deleted": 0},
    ]

    # 改判：段20 组级 delete + 10.2 手工翻转 delete → spk2 全删、spk1 全删
    cut2 = build_cutlist({"segments": segs}, rev,
                         manual_marks={"10.2": "delete"}, actions={"20": "delete"})
    link2 = link_speakers({"segments": segs}, cut2)
    assert link2["stats"] == [
        {"spk": 1, "total": 2, "kept": 0, "deleted": 2},
        {"spk": 2, "total": 1, "kept": 0, "deleted": 1},
    ], "执行口径：组级 delete / 子段 mark=delete 均剔除"


def test_link_speakers_no_overlap_and_tie():
    """无重叠不标注；重叠并列取时间更早的段。"""
    from slirn_home.cut_speaker import link_speakers

    # 行 [4000,5000] 与两段各重叠 200ms（早段 spk=1 起点更早）→ 归 spk1
    segs = [
        {"i": 1, "start_ms": 3600, "end_ms": 4200, "spk": 1},
        {"i": 2, "start_ms": 4800, "end_ms": 5400, "spk": 2},
        {"i": 3, "start_ms": 9000, "end_ms": 9500, "spk": 3},  # 与行无重叠
    ]
    cutlist = {"items": [
        {"id": "7", "source_i": 7, "kind": "keep", "start_ms": 4000, "end_ms": 5000},
        {"id": "8", "source_i": 8, "kind": "keep", "start_ms": 6000, "end_ms": 7000},
    ]}
    link = link_speakers({"segments": segs}, cutlist)
    assert link["rows"] == {"7": 1}, "并列取更早段；无重叠（行8）不标注"
    assert link["stats"] == [{"spk": 1, "total": 1, "kept": 1, "deleted": 0}]

    # 重叠不等 → 大者胜：行 [4000, 5400] 与段2 重叠 600ms > 段1 200ms → spk2
    cutlist2 = {"items": [
        {"id": "9", "source_i": 9, "kind": "keep", "start_ms": 4000, "end_ms": 5400},
    ]}
    assert link_speakers({"segments": segs}, cutlist2)["rows"] == {"9": 2}


def test_link_speakers_unavailable_without_spk():
    """无 spk 段（旧任务/SD 关闭）→ available=False 空结果（端点据此引导）。"""
    from slirn_home.cut_speaker import link_speakers

    segs = [{"i": 1, "start_ms": 0, "end_ms": 1000}]  # 无 spk 字段
    segs_spk0 = [{"i": 1, "start_ms": 0, "end_ms": 1000, "spk": 0}]  # spk=0 无效
    cutlist = {"items": [{"id": "1", "source_i": 1, "kind": "keep",
                          "start_ms": 0, "end_ms": 1000}]}
    for meta in ({"segments": segs}, {"segments": segs_spk0}, None, {}):
        link = link_speakers(meta, cutlist)
        assert link == {"available": False, "rows": {}, "stats": []}


# ---------- 面板渲染 ----------

def test_render_cutlist_zone_has_spk_button(tmp_path: Path):
    """操作行有「关联人员ID」按钮 + 列表上方预置隐藏统计条容器。"""
    from slirn_home.app import _render_cutlist_zone

    m, video = _make_mgr(tmp_path)
    t = m.create(name="关联人员", original_video=video)
    tid = t.task_id
    outputs = m.tasks_dir / tid / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    (outputs / "subtitle.json").write_text(json.dumps(
        {"version": 1, "segments": [_seg(1, 0, 900, "正常一句", spk=1)]}, ensure_ascii=False),
        encoding="utf-8")
    (outputs / "revision.json").write_text(json.dumps(_rev(
        [{"i": 1, "category": "keep", "keep_text": None, "decision": "accept", "user_note": ""}]),
        ensure_ascii=False), encoding="utf-8")

    h = _render_cutlist_zone(tid, m.get(tid), m)
    assert 'data-action="cut-spk-link"' in h and "关联人员ID" in h
    assert 'id="slirn-cut-spk-bar"' in h and "display:none" in h, "统计条预置且隐藏（关联后由 JS 填充）"
    # 按钮位置：操作行（保存/播放同一容器内），在列表下方
    bar_pos = h.find('id="slirn-cut-spk-bar"')
    btn_pos = h.find('data-action="cut-spk-link"')
    list_pos = h.find('id="slirn-cut-list"')
    assert 0 < bar_pos < list_pos < btn_pos, "统计条在列表上方、按钮在操作行（列表下方）"


# ---------- API 端点 ----------

def _seed_task(m, segs: list[dict], entries: list[dict]):
    video = Path(m.repo_root) / "v.mp4"
    video.write_bytes(b"v")
    t = m.create(name="端点关联", original_video=video)
    outputs = m.tasks_dir / t.task_id / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    (outputs / "subtitle.json").write_text(
        json.dumps({"version": 1, "segments": segs}, ensure_ascii=False), encoding="utf-8")
    (outputs / "revision.json").write_text(
        json.dumps(_rev(entries), ensure_ascii=False), encoding="utf-8")
    return t.task_id, outputs


def test_cut_speaker_link_endpoint(tmp_path: Path):
    """有 spk → ok + rows/stats；无 spk → error 引导重新生成；前置缺失 → 各自提示。"""
    import warnings

    warnings.filterwarnings("ignore")
    from fastapi.testclient import TestClient

    from slirn_home import build_app

    m, _ = _make_mgr(tmp_path)
    app = build_app(repo_root=m.repo_root)
    client = TestClient(app.app)

    keep_entry = [{"i": 1, "category": "keep", "keep_text": None,
                   "decision": "accept", "user_note": ""}]

    # 1) 有 spk：行标注 + 统计 + rows 数
    tid1, _ = _seed_task(m, [_seg(1, 0, 900, "讲师开场", spk=1),
                             _seg(2, 900, 1800, "同学提问", spk=2)],
                         keep_entry + [{"i": 2, "category": "keep", "keep_text": None,
                                        "decision": "accept", "user_note": ""}])
    r = client.post("/slirn/api/cut_speaker_link", json={"task_id": tid1})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] and d["rows"] == 2
    assert d["link"]["available"] is True
    assert d["link"]["rows"] == {"1": 1, "2": 2}
    assert d["link"]["stats"][0] == {"spk": 1, "total": 1, "kept": 1, "deleted": 0}

    # 2) 无 spk：引导重新生成（不 500）
    tid2, _ = _seed_task(m, [_seg(1, 0, 900, "旧任务无说话人")], keep_entry)
    r2 = client.post("/slirn/api/cut_speaker_link", json={"task_id": tid2})
    d2 = r2.json()
    assert d2["ok"] is False and "说话人" in d2["error"] and "重新生成" in d2["error"]

    # 3) 缺 task_id / 任务不存在 / 无修订
    assert "缺少 task_id" in client.post(
        "/slirn/api/cut_speaker_link", json={}).json()["error"]
    assert "任务不存在" in client.post(
        "/slirn/api/cut_speaker_link", json={"task_id": "nope"}).json()["error"]
    tid3, outputs3 = _seed_task(m, [_seg(1, 0, 900, "x", spk=1)], [])
    (outputs3 / "revision.json").unlink()
    assert "修订" in client.post(
        "/slirn/api/cut_speaker_link", json={"task_id": tid3}).json()["error"]
