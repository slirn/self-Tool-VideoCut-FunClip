"""REQ-20260918-039 — 批量修改字幕状态（渲染层）。

字幕修改 / 切分修剪两阶段的批量条：起止序号输入 + 目标状态下拉 + 应用按钮。
批量逻辑在 router.js（纯前端状态变更），单测覆盖服务端渲染的结构与可选项；
行为（区间匹配/确认/取消/非法路径）由真实浏览器 E2E 覆盖。
"""
import json
from pathlib import Path


def _make_mgr(tmp_path: Path):
    from tasklib import TaskManager

    video = tmp_path / "lecture.mp4"
    video.write_bytes(b"fake-video")
    return TaskManager(tmp_path), video


SEGS = [
    {"i": 1, "start_ms": 0, "end_ms": 1500, "start": "00:00:00.000", "end": "00:00:01.500", "text": "嗯嗯大家好"},
    {"i": 2, "start_ms": 1500, "end_ms": 4000, "start": "00:00:01.500", "end": "00:00:04.000", "text": "今天讲第一课"},
    {"i": 3, "start_ms": 4000, "end_ms": 7000, "start": "00:00:04.000", "end": "00:00:07.000", "text": "那个那个就是说我们开始吧"},
]

ENTRIES = [
    {"i": s["i"], "start_ms": s["start_ms"], "end_ms": s["end_ms"],
     "start": s["start"], "end": s["end"], "text": s["text"],
     "category": cat, "keep_text": kt, "note": "n", "decision": dec, "user_note": ""}
    for s, cat, kt, dec in [
        (SEGS[0], "delete", None, "accept"),
        (SEGS[1], "keep", None, "pending"),
        (SEGS[2], "split", "我们开始吧", "accept"),
    ]
]


def _write_subtitle(mgr, tid: str) -> Path:
    outputs = mgr.tasks_dir / tid / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    (outputs / "subtitle.json").write_text(
        json.dumps({"version": 1, "segments": SEGS}, ensure_ascii=False), encoding="utf-8")
    return outputs


def test_render_rev_zone_batch_bar(tmp_path: Path):
    """字幕修改：批量条渲染 — 起止输入 + 全部决策状态（默认保留）+ 应用按钮。"""
    from slirn_home import revision_service
    from slirn_home.app import _render_revision_zone

    m, video = _make_mgr(tmp_path)
    t = m.create(name="批量修订", original_video=video)
    tid = t.task_id
    outputs = _write_subtitle(m, tid)
    (outputs / "revision.json").write_text(json.dumps(
        {"version": 1, "model": "qwen-plus", "created_at": "2026-09-18T10:00:00",
         "saved_at": None, "segments_count": len(ENTRIES), "entries": ENTRIES},
        ensure_ascii=False), encoding="utf-8")

    h = _render_revision_zone(tid, m.get(tid), m)
    assert 'id="slirn-rev-batch"' in h
    assert 'id="slirn-rev-batch-start"' in h and 'id="slirn-rev-batch-end"' in h
    assert 'data-action="rev-batch-apply"' in h
    # 目标状态 = 决策下拉全部状态（与单条决策同口径），默认选中「保留」
    assert 'id="slirn-rev-batch-sel"' in h
    for k, v in revision_service.USER_DECISIONS.items():
        assert f'<option value="{k}"' in h, f"缺目标状态 {k}"
        assert f">{v}</option>" in h
    assert '<option value="keep" selected>保留</option>' in h


def test_render_cut_zone_batch_bar(tmp_path: Path):
    """切分修剪：批量条渲染 — 起止输入（支持子段行号）+ 保留/删除/维持原状 + 应用按钮。"""
    from slirn_home.app import _render_cutlist_zone

    m, video = _make_mgr(tmp_path)
    t = m.create(name="批量切分", original_video=video)
    tid = t.task_id
    outputs = _write_subtitle(m, tid)
    # 切分阶段门槛：修订全部决策（批量条只在清单态渲染）
    decided = [dict(e, decision="accept") for e in ENTRIES]
    (outputs / "revision.json").write_text(json.dumps(
        {"version": 1, "model": "qwen-plus", "created_at": "2026-09-18T10:00:00",
         "saved_at": "2026-09-18T10:05:00", "segments_count": len(decided),
         "entries": decided},
        ensure_ascii=False), encoding="utf-8")

    h = _render_cutlist_zone(tid, m.get(tid), m)
    assert 'id="slirn-cut-batch"' in h
    assert 'id="slirn-cut-batch-start"' in h and 'id="slirn-cut-batch-end"' in h
    assert 'step="any"' in h, "行号支持小数（子段 10.1）"
    assert 'data-action="cut-batch-apply"' in h
    assert '<option value="keep">✅ 保留</option>' in h
    assert '<option value="delete">❌ 删除</option>' in h
    assert '<option value="">↩️ 维持原状</option>' in h
    # 批量条在清单前（先见批量条再见行）
    assert h.index('id="slirn-cut-batch"') < h.index('id="slirn-cut-list"')


# ---------- REQ-20260918-040 字幕内容搜索定位 ----------

def test_render_rev_zone_search_bar(tmp_path: Path):
    """REQ-040：字幕修订 — 搜索条（输入框 + 计数 + 上/下一条 + 提示）渲染于批量条之后、清单之前。
    行 data-text 暴露当前 text（小写归一化在 JS 完成），便于 JS 在筛选后匹配可见行。"""
    from slirn_home.app import _render_revision_zone

    m, video = _make_mgr(tmp_path)
    t = m.create(name="搜索修订", original_video=video)
    tid = t.task_id
    outputs = _write_subtitle(m, tid)
    (outputs / "revision.json").write_text(json.dumps(
        {"version": 1, "model": "qwen-plus", "created_at": "2026-09-18T10:00:00",
         "saved_at": None, "segments_count": len(ENTRIES), "entries": ENTRIES},
        ensure_ascii=False), encoding="utf-8")

    h = _render_revision_zone(tid, m.get(tid), m)
    # 搜索条
    assert 'id="slirn-rev-search"' in h
    assert 'id="slirn-rev-search-q"' in h and 'placeholder=' in h
    assert 'id="slirn-rev-search-count"' in h
    assert 'data-action="rev-search-prev"' in h
    assert 'data-action="rev-search-next"' in h
    # 顺序：批量条 < 搜索条 < 清单
    assert h.index('id="slirn-rev-batch"') < h.index('id="slirn-rev-search"') < h.index('id="slirn-rev-list"')
    # 行 data-text（HTML 属性 escape 在 _esc 完成；保留中文字符）
    assert 'data-text="嗯嗯大家好"' in h
    assert 'data-text="今天讲第一课"' in h


def test_render_cut_zone_search_bar_with_orig(tmp_path: Path):
    """REQ-040：切分修剪 — 搜索条带「整段按原文、子段按子段」提示。
    整段行 data-text 是 keep_text/原 text；切分子段行 data-text 是子段文本、data-orig 是原整段文本。"""
    from slirn_home.app import _render_cutlist_zone

    m, video = _make_mgr(tmp_path)
    t = m.create(name="搜索切分", original_video=video)
    tid = t.task_id
    outputs = _write_subtitle(m, tid)
    decided = [dict(e, decision="accept") for e in ENTRIES]
    (outputs / "revision.json").write_text(json.dumps(
        {"version": 1, "model": "qwen-plus", "created_at": "2026-09-18T10:00:00",
         "saved_at": "2026-09-18T10:05:00", "segments_count": len(decided),
         "entries": decided},
        ensure_ascii=False), encoding="utf-8")

    h = _render_cutlist_zone(tid, m.get(tid), m)
    assert 'id="slirn-cut-search"' in h
    assert 'id="slirn-cut-search-q"' in h
    assert 'id="slirn-cut-search-count"' in h
    assert 'data-action="cut-search-prev"' in h
    assert 'data-action="cut-search-next"' in h
    # 顺序：批量条 < 搜索条 < 清单
    assert h.index('id="slirn-cut-batch"') < h.index('id="slirn-cut-search"') < h.index('id="slirn-cut-list"')
    # 切分修剪 — 段3 split，行应同时暴露子段文本与原文
    assert 'data-text="我们开始吧"' in h, "子段行 data-text 暴露子段文本"
    # 段1 整段保留 + 段2 整段保留 → data-text 是原 text
    assert 'data-text="嗯嗯大家好"' in h or 'data-text="今天讲第一课"' in h, "整段行 data-text 暴露显示文本"
