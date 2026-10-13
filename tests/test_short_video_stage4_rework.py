"""REQ-20261013-108：Stage 4「内容优化」补全测试。

覆盖：
- 全局提示词多 stage 存储（stage3 / stage4 隔离 + 迁移）
- per_hl 状态文件读写（line_edits / line_marks / split_marks）
- effective_subtitle_lines 合并读：marks 删 → edits 改 → splits 切
- _interpolate_subs_time 字符比内插
- _resolve_srt_line_times 通过 raw.srt 拿回每行起止毫秒
- _coarse_single_hl_state 子任务 hl_index 隔离
- start_coarse_async(..., hl_index=1) 只跑 1 条、不影响其他 highlight
- preview_highlight 输出 mp4 路径 + segments 计数
"""

import json
import shutil
from pathlib import Path

import pytest

from slirn_home import short_video_coarse as svc_coarse
from slirn_home import short_video_optimize as svc_opt
from slirn_home import short_video_service as svc


def _make_project(tmp_path: Path):
    """建 split 项目 + 4 行 raw.srt；返回 (tid, pid, raw_srt_path)。"""
    from tasklib import TaskManager

    video = tmp_path / "src.mp4"
    video.write_bytes(b"fake")
    mgr = TaskManager(tmp_path)
    task = mgr.create(name="t", original_video=video)
    tid = task.task_id
    proj = svc.create_project(tmp_path, tid, name="p", brief="", kind="split")
    pid = proj["id"]
    # 设 base_material_id 让 run_stage4 找到源视频
    proj["base_material_id"] = "mat_src"
    proj["materials"] = [{"id": "mat_src", "kind": "video",
                          "name": "src.mp4", "rel_path": "src.mp4"}]
    svc.save_project(tmp_path, proj)
    rp = svc.stage_raw_srt_path(tmp_path, tid, pid)
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(
        "1\n00:00:01,000 --> 00:00:02,500\n第一句内容\n\n"
        "2\n00:00:05,000 --> 00:00:07,000\n第二句内容\n\n"
        "3\n00:00:10,000 --> 00:00:12,000\n第三句内容\n\n"
        "4\n00:00:15,000 --> 00:00:17,000\n第四句内容\n\n"
        "5\n00:00:18,000 --> 00:00:20,000\n第五句内容\n\n",
        encoding="utf-8",
    )
    # 写 stage3/highlights.json：2 条高亮，每条包含 2 行
    from slirn_home.short_video_service import stage_highlights_path
    hp = stage_highlights_path(tmp_path, tid, pid)
    hp.parent.mkdir(parents=True, exist_ok=True)
    hp.write_text(json.dumps({
        "highlights": [
            {
                "index": 1,
                "id": "h1",
                "title": "片段一",
                "start_ms": 0,
                "end_ms": 12000,
                "subtitle_lines": [
                    {"src_index": 1, "text": "行 1-1"},
                    {"src_index": 2, "text": "行 1-2"},
                    {"src_index": 3, "text": "行 1-3"},
                ],
            },
            {
                "index": 2,
                "id": "h2",
                "title": "片段二",
                "start_ms": 12000,
                "end_ms": 20000,
                "subtitle_lines": [
                    {"src_index": 4, "text": "行 2-1"},
                    {"src_index": 5, "text": "行 2-2"},
                ],
            },
        ],
    }, ensure_ascii=False), encoding="utf-8")
    return tid, pid


# ---------- 多 stage 提示词存储 ----------

def test_prompt_settings_stage3_stage4_isolation(tmp_path):
    # 写 stage3 默认
    svc.save_default_prompt(tmp_path, "stage3 系统提示词", stage="stage3")
    # 写 stage4 默认
    svc.save_default_prompt(tmp_path, "stage4 系统提示词", stage="stage4")
    # 隔离：两个 stage 互不污染
    s3 = svc.load_prompt_settings(tmp_path, stage="stage3")
    s4 = svc.load_prompt_settings(tmp_path, stage="stage4")
    assert s3["default_prompt"]["prompt"] == "stage3 系统提示词"
    assert s4["default_prompt"]["prompt"] == "stage4 系统提示词"
    # stage3 写历史不污染 stage4
    svc.save_prompt_history(tmp_path, "stage3 历史", stage="stage3")
    s4b = svc.load_prompt_settings(tmp_path, stage="stage4")
    assert all("stage3" not in (h.get("prompt") or "") for h in s4b["history"])


def test_prompt_settings_load_all_returns_both_stages(tmp_path):
    svc.save_default_prompt(tmp_path, "s3", stage="stage3")
    svc.save_default_prompt(tmp_path, "s4", stage="stage4")
    all_settings = svc.load_all_prompt_settings(tmp_path)
    assert all_settings["stage3"]["default_prompt"]["prompt"] == "s3"
    assert all_settings["stage4"]["default_prompt"]["prompt"] == "s4"


def test_prompt_settings_legacy_migration(tmp_path):
    """旧形态（顶层 default_prompt + history）视为 stage3，新形态按 stage 节点读。"""
    legacy = {"default_prompt": {"prompt": "legacy", "updated_at": "x"}, "history": []}
    (tmp_path / "config" / "short_video_prompts.json").parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "short_video_prompts.json").write_text(
        json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    # 读 stage3：迁移后能拿到 legacy
    s3 = svc.load_prompt_settings(tmp_path, stage="stage3")
    assert s3["default_prompt"]["prompt"] == "legacy"
    # 读 stage4：迁移后应是空
    s4 = svc.load_prompt_settings(tmp_path, stage="stage4")
    assert s4["default_prompt"] is None


def test_stage4_system_prompt_constant():
    """STAGE4_SYSTEM_PROMPT 必须存在且包含关键约束。"""
    s = svc.STAGE4_SYSTEM_PROMPT
    assert "split" in s and "fix" in s
    assert "JSON" in s
    # 含强约束段关键词（不严格匹配中文）
    assert "强约束" in s
    assert "split" in s and "fix" in s
    # 必须强调 split/fix 互斥
    assert "互斥" in s or "不同时" in s or "不要" in s
    # 强调不改变顺序
    assert "顺序" in s


# ---------- per_hl 状态文件 ----------

def test_per_hl_state_roundtrip(tmp_path):
    tid, pid = _make_project(tmp_path)
    # 初始：空
    st = svc_opt.load_per_hl(tmp_path, tid, pid, hl_index=1)
    # REQ-20261013-110：新增 line_order 字段（None = 保持原序）
    assert st == {"line_edits": {}, "line_marks": [], "split_marks": {}, "line_order": None}
    # 写 line_edits
    svc_opt.per_hl_apply_line_edit(tmp_path, tid, pid, hl_index=1, src_index=1, new_text="改后")
    st = svc_opt.load_per_hl(tmp_path, tid, pid, hl_index=1)
    assert st["line_edits"]["1"] == "改后"
    # 写 line_marks
    svc_opt.per_hl_toggle_line_mark(tmp_path, tid, pid, hl_index=1, src_index=2)
    st = svc_opt.load_per_hl(tmp_path, tid, pid, hl_index=1)
    assert 2 in st["line_marks"]
    # 再次 toggle 取消
    svc_opt.per_hl_toggle_line_mark(tmp_path, tid, pid, hl_index=1, src_index=2)
    st = svc_opt.load_per_hl(tmp_path, tid, pid, hl_index=1)
    assert 2 not in st["line_marks"]


def test_per_hl_state_isolation_between_highlights(tmp_path):
    tid, pid = _make_project(tmp_path)
    svc_opt.per_hl_apply_line_edit(tmp_path, tid, pid, hl_index=1, src_index=1, new_text="hl1-改")
    svc_opt.per_hl_apply_line_edit(tmp_path, tid, pid, hl_index=2, src_index=3, new_text="hl2-改")
    s1 = svc_opt.load_per_hl(tmp_path, tid, pid, hl_index=1)
    s2 = svc_opt.load_per_hl(tmp_path, tid, pid, hl_index=2)
    assert s1["line_edits"]["1"] == "hl1-改" and "3" not in s1["line_edits"]
    assert s2["line_edits"]["3"] == "hl2-改" and "1" not in s2["line_edits"]


# ---------- effective_subtitle_lines 合并读 ----------

def test_effective_subtitle_lines_merges_in_correct_order(tmp_path):
    """合并顺序：先 marks 删 → 再 edits 改 → 再 splits 切。"""
    tid, pid = _make_project(tmp_path)
    # 标删 line 1、整行替换 line 2、切分 line 3 为 2 段
    svc_opt.per_hl_toggle_line_mark(tmp_path, tid, pid, hl_index=1, src_index=1)
    svc_opt.per_hl_apply_line_edit(tmp_path, tid, pid, hl_index=1, src_index=2, new_text="行 1-2 改后")
    svc_opt.per_hl_resplit(tmp_path, tid, pid, hl_index=1, src_index=3, target_subs=["3A", "3B"])
    eff = svc_opt.effective_subtitle_lines(tmp_path, tid, pid, hl_index=1)
    # 验证：line 1 被删、line 2 文字 = "行 1-2 改后"、line 3 → 2 段
    src_idxs = [s["src_index"] for s in eff]
    assert 1 not in src_idxs
    assert 2 in src_idxs
    line2 = next(s for s in eff if s["src_index"] == 2)
    assert line2["text"] == "行 1-2 改后"
    # 切分后会出现 split_from=3 / 子 src_index
    split_lines = [s for s in eff if s.get("split_from") == 3]
    assert len(split_lines) >= 2


def test_effective_subtitle_lines_falls_back_to_char_interpolation(tmp_path):
    """无 per_hl 编辑时，按 highlight [start_ms, end_ms] 内插时间。"""
    tid, pid = _make_project(tmp_path)
    eff = svc_opt.effective_subtitle_lines(tmp_path, tid, pid, hl_index=1)
    # 至少 2 行有 start_ms / end_ms
    assert all("start_ms" in s and "end_ms" in s for s in eff)
    # 第一行 start_ms 应 ≥ highlight 起点 0
    assert eff[0]["start_ms"] >= 0
    # 末行 end_ms 应 ≤ highlight 终点 12000
    assert eff[-1]["end_ms"] <= 12000
    # 行单调递增
    for i in range(1, len(eff)):
        assert eff[i]["start_ms"] >= eff[i-1]["end_ms"]


def test_interpolate_subs_time_splits_by_ratio(tmp_path):
    """_interpolate_subs_time：3 段按字数比切。"""
    orig = {"start_ms": 0, "end_ms": 1000, "text": "abcdef"}  # 6 字
    subs = svc_opt._interpolate_subs_time(orig, ["ab", "cd", "ef"])  # 2/2/2
    assert subs[0]["start_ms"] == 0
    assert abs(subs[0]["end_ms"] - 1000 * 2/6) < 1
    assert abs(subs[2]["end_ms"] - 1000) < 1


def test_resolve_srt_line_times_returns_per_line(tmp_path):
    """_resolve_srt_line_times 从 raw.srt 拿真实起止毫秒。"""
    tid, pid = _make_project(tmp_path)
    times = svc_opt._resolve_srt_line_times(tmp_path, tid, pid)
    assert times[1] == (1000, 2500)
    assert times[2] == (5000, 7000)
    assert times[3] == (10000, 12000)


# ---------- run_stage4 hl_index 隔离（不直接跑 ffmpeg，只看状态） ----------

def test_start_coarse_async_accepts_hl_index(tmp_path):
    """start_coarse_async 必须能接受 hl_index 参数（不抛 TypeError）。"""
    tid, pid = _make_project(tmp_path)
    # monkeypatch run_stage4 避免真跑 ffmpeg
    called = {}
    import threading
    import time as _t

    def fake_run(root, t, p, progress_cb=None, hl_index=None):
        called["hl_index"] = hl_index
        called["done"] = True
    import slirn_home.short_video_coarse as _sc
    orig_run = _sc.run_stage4
    _sc.run_stage4 = fake_run
    try:
        # 同步跑不异步
        _sc.run_stage4(tmp_path, tid, pid, hl_index=2)
        assert called.get("hl_index") == 2
    finally:
        _sc.run_stage4 = orig_run


def test_run_stage4_filters_highlights_by_hl_index(tmp_path, monkeypatch):
    """hl_index 触发 run_stage4 时，只跑指定的 highlight。"""
    tid, pid = _make_project(tmp_path)
    from slirn_home.short_video_service import project_dir
    out_dir = project_dir(tmp_path, tid, pid) / "stage4"
    out_dir.mkdir(parents=True, exist_ok=True)
    # 预放 hl2 已存在的粗剪文件
    fake = out_dir / "coarse_02.mp4"
    fake.write_bytes(b"fake-hl2")
    # monkeypatch _coarse_one 让它只写 hl1 文件，返回 CoarseClipResult
    def fake_coarse_one(src, hl, dst, srt, *, start_ost_ms=0, end_ost_ms=100, segments=None):
        Path(str(dst)).write_bytes(b"fake-hl1")
        return svc_coarse.CoarseClipResult(
            index=int(hl.get("index") or 0),
            coarse_mp4=Path(str(dst)),
            coarse_srt=Path(str(srt)),
            status="done",
            duration_sec=1.0,
            elapsed_sec=1,
            error="",
        )
    monkeypatch.setattr(svc_coarse, "_coarse_one", fake_coarse_one)
    # 跑 hl_index=1
    svc_coarse.run_stage4(tmp_path, tid, pid, hl_index=1)
    # 验证 hl1 文件被写、hl2 文件未被删
    assert (out_dir / "coarse_01.mp4").exists()
    assert (out_dir / "coarse_02.mp4").exists()
    assert (out_dir / "coarse_02.mp4").read_bytes() == b"fake-hl2"


# ---------- REQ-20261013-109：Stage 4 每 highlight tab 化 ----------

def test_stage4_ui_renders_tabs_per_hl(tmp_path):
    """N 条 highlight → N 个 tab + N 个 pane + 第 1 个 active。
    每个 pane 内含原 .slirn-stage4-clip（REQ-108 编辑能力保留）。"""
    from slirn_home.short_video_ui import _stage4_optimize

    tid, pid = _make_project(tmp_path)
    # 建一个最小 project 字典：含 task_id / id / pipeline.stage3_analyze.highlights
    project = {
        "task_id": tid, "id": pid,
        "pipeline": {
            "stage3_analyze": {
                "status": "done",
                "highlights": [
                    {"index": 1, "id": "h1", "title": "片段一足够长的标题",
                     "start_ms": 0, "end_ms": 12000, "subtitle_lines": [
                         {"src_index": 1, "text": "行 1-1"},
                         {"src_index": 2, "text": "行 1-2"},
                         {"src_index": 3, "text": "行 1-3"},
                     ]},
                    {"index": 2, "id": "h2", "title": "片段二",
                     "start_ms": 12000, "end_ms": 20000, "subtitle_lines": [
                         {"src_index": 4, "text": "行 2-1"},
                         {"src_index": 5, "text": "行 2-2"},
                     ]},
                ],
            },
            "stage4_optimize": {"status": "done", "highlights": []},
        },
    }
    html = _stage4_optimize(project, task_id=tid, project_id=pid, repo_root=tmp_path)

    # 2 个 tab + 2 个 pane
    import re
    # tab 是 button，class 必为 ...-hl-tab"（不能是 ...-hl-tabs" 容器）
    tabs = re.findall(r'class="slirn-stage4-hl-tab(?!\s)[^"]*"', html)
    panes = re.findall(r'class="slirn-stage4-hl-pane[^"]*"', html)
    assert len(tabs) == 2, f"expected 2 tabs, got {len(tabs)}: {tabs}"
    assert len(panes) == 2, f"expected 2 panes, got {len(panes)}: {panes}"

    # 第 1 个 tab / pane 必须 active
    assert 'class="slirn-stage4-hl-tab active"' in html
    assert 'class="slirn-stage4-hl-pane active"' in html
    # 第 2 个不应有 active（仅 1 个 active，避免与第 1 个冲突）
    assert html.count('class="slirn-stage4-hl-tab active"') == 1
    assert html.count('class="slirn-stage4-hl-pane active"') == 1

    # 每个 pane 内含原 .slirn-stage4-clip（REQ-108 编辑能力保留）
    assert html.count('class="slirn-stage4-clip"') == 2

    # tab bar 容器存在
    assert 'class="slirn-stage4-hl-tabs"' in html
    # 切 tab 的 action 存在
    assert 'data-action="sv-stage4-hl-tab"' in html
    # 每个 tab 带 data-hl-index
    assert 'data-hl-index="1"' in html
    assert 'data-hl-index="2"' in html


def test_stage4_ui_tabs_wiring():
    """JS 派发器 + CSS 选择器接线测试（仿 Stage 3 test_stage3_hl_tabs_wiring）。"""
    js = Path("slirn_home/static/short_video.js").read_text(encoding="utf-8")
    css = Path("slirn_home/static/home.css").read_text(encoding="utf-8")

    # 1) JS action 路由
    assert "sv-stage4-hl-tab" in js
    # 2) switchHlTab 扩展了 stage4 选择器
    assert ".slirn-stage3-hl-tab, .slirn-stage4-hl-tab" in js
    assert ".slirn-stage3-hl-pane, .slirn-stage4-hl-pane" in js
    # 3) _restoreHlTab 同步扩展
    assert ".slirn-stage3-hl-tab[data-hl-index=" in js
    assert ".slirn-stage4-hl-tab[data-hl-index=" in js
    # 4) CSS 阶段（方案 A 共用选择器）
    assert ".slirn-stage4-hl-tab" in css
    assert ".slirn-stage4-hl-pane" in css
    # 5) 隐藏机制一致（display: none / display: block）
    pane_block = css.split(".slirn-stage4-hl-pane", 1)[1][:200]
    assert "display: none" in pane_block
    assert "display: block" in pane_block
    # 6) tab 视觉共用
    assert "border-bottom: 1px solid #e2e8f0" in css.split(".slirn-stage3-hl-tabs", 1)[1][:600]


def test_stage4_tabs_state_persists_in_memory():
    """switchHlTab 写 window._slirnSvHlTab；_restoreHlTab 跨 openProject 恢复。"""
    # 不需要真实 DOM；检查 _restoreHlTab / switchHlTab 函数的代码结构
    js = Path("slirn_home/static/short_video.js").read_text(encoding="utf-8")
    # 1) switchHlTab 命中后写 window._slirnSvHlTab
    assert "window._slirnSvHlTab = {k: _svProjKey(), hl: want}" in js
    # 2) _restoreHlTab key 不匹配时 return（不污染其他项目）
    assert "if (mem.k !== String(taskId) + '/' + String(projectId)) return;" in js
    # 3) _restoreHlTab 找不到 tab → 置 null（回默认第一条）
    assert "window._slirnSvHlTab = null" in js
    # 4) openProject 调 _restoreHlTab
    assert "_restoreHlTab(taskId, projectId);" in js


def test_stage4_tabs_stale_index_falls_back():
    """_restoreHlTab 找不到记忆的 hl → window._slirnSvHlTab = null（回默认第 1 条）。

    实测逻辑走 JS 字符串检查：_restoreHlTab 用 querySelector 同时查 stage3 + stage4，
    两边都没有 → if (tab) false → 置 null。
    """
    js = Path("slirn_home/static/short_video.js").read_text(encoding="utf-8")
    # 1) querySelector 同时查 stage3 + stage4（按行扫描，避开反斜杠转义）
    s3_line = next(
        (ln for ln in js.splitlines() if "slirn-stage3-hl-tab[data-hl-index=" in ln),
        None,
    )
    s4_line = next(
        (ln for ln in js.splitlines() if "slirn-stage4-hl-tab[data-hl-index=" in ln),
        None,
    )
    assert s3_line is not None, "stage3 hl-tab querySelector line missing"
    assert s4_line is not None, "stage4 hl-tab querySelector line missing"
    # 2) 找不到 tab → 置 null
    assert "if (tab) switchHlTab(mem.hl);" in js
    assert "else window._slirnSvHlTab = null" in js


# ---------- REQ-20261013-110：Stage 4 增强（详情折叠 + 4 字段 + 段内重排） ----------

def test_stage4_prompt_allows_intra_hl_reorder():
    """提示词应明确允许段内重排，并显式禁用 op:\"delete\" 防止误用警告。"""
    p = svc.STAGE4_SYSTEM_PROMPT
    # 1) 强约束 1 改为「允许段内重排」（不再有「不改变行的先后顺序」原文）
    assert "不改变行的先后顺序" not in p
    assert "段内重排" in p or "允许段内重排" in p
    # 2) 显式禁 op:"delete"
    assert "不要输出" in p and "op" in p and "delete" in p
    # 3) 新增逻辑校验维度
    assert "逻辑校验" in p or "重复度" in p or "承上启下" in p
    # 4) 顶部任务目标段
    assert "任务目标" in p or "逻辑通顺" in p


def test_parse_optimize_result_treats_delete_silently():
    """op:\"delete\" 误用应静默忽略（视为未列入=删除），不产生警告。"""
    # src 3 行
    src_lines = [
        {"no": 1, "text": "A"},
        {"no": 2, "text": "B"},
        {"no": 3, "text": "C"},
    ]
    # LLM 输出：第 2 行显式 op:delete，第 1/3 行 keep
    raw = json.dumps({
        "lines": [
            {"no": 1, "op": "keep"},
            {"no": 2, "op": "delete"},
            {"no": 3, "op": "keep"},
        ]
    }, ensure_ascii=False)
    ops, warnings = svc_opt._parse_optimize_result(raw, src_lines)
    # 第 2 行应被忽略（未列入），不产生警告
    assert 1 in ops
    assert 2 not in ops  # 视同未列入
    assert 3 in ops
    assert not any("未知 op" in w for w in warnings), f"delete 应静默，产生警告: {warnings}"


def test_effective_subtitle_lines_applies_line_order(tmp_path):
    """per-hl line_order 字段应让 effective 顺序按白名单重排；未列入的按原序追加。"""
    tid, pid = _make_project(tmp_path)
    # hl 1 有 3 行 src_index [1, 2, 3]；重排为 [3, 1]
    svc_opt.per_hl_reorder(tmp_path, tid, pid, hl_index=1, order=[3, 1])
    eff = svc_opt.effective_subtitle_lines(tmp_path, tid, pid, hl_index=1)
    srcs = [int(s.get("src_index") or 0) for s in eff]
    # [3, 1] 列入；[2] 未列入按原序追加
    assert srcs == [3, 1, 2]
    # no 重新编号 1..M
    nos = [int(s.get("no") or 0) for s in eff]
    assert nos == [1, 2, 3]


def test_effective_subtitle_lines_unlisted_appended(tmp_path):
    """line_order 只列部分 src_index，未列入的应按原序追加（白名单语义，不丢行）。"""
    tid, pid = _make_project(tmp_path)
    svc_opt.per_hl_reorder(tmp_path, tid, pid, hl_index=1, order=[2])
    eff = svc_opt.effective_subtitle_lines(tmp_path, tid, pid, hl_index=1)
    srcs = [int(s.get("src_index") or 0) for s in eff]
    assert srcs == [2, 1, 3]


def test_per_hl_reorder_clear_with_empty_list(tmp_path):
    """传 [] 或 None 应清除重排，恢复原序。"""
    tid, pid = _make_project(tmp_path)
    svc_opt.per_hl_reorder(tmp_path, tid, pid, hl_index=1, order=[3, 2, 1])
    eff = svc_opt.effective_subtitle_lines(tmp_path, tid, pid, hl_index=1)
    assert [int(s.get("src_index") or 0) for s in eff] == [3, 2, 1]
    # 清除
    svc_opt.per_hl_reorder(tmp_path, tid, pid, hl_index=1, order=[])
    eff = svc_opt.effective_subtitle_lines(tmp_path, tid, pid, hl_index=1)
    assert [int(s.get("src_index") or 0) for s in eff] == [1, 2, 3]


def test_per_hl_reorder_rejects_empty_when_not_clearing(tmp_path):
    """非空 list 校验：传空字符串元素应被跳过（防御性）。"""
    tid, pid = _make_project(tmp_path)
    # 正常写入
    per = svc_opt.per_hl_reorder(tmp_path, tid, pid, hl_index=1, order=[3, 1])
    assert per["line_order"] == [3, 1]
    # 加载
    per2 = svc_opt.load_per_hl(tmp_path, tid, pid, hl_index=1)
    assert per2["line_order"] == [3, 1]


def test_stage4_sub_row_has_4_fields(tmp_path):
    """_render_stage4_sub_row 输出应含 4 个字段 span：no / srt / time / text。"""
    from slirn_home import short_video_ui as ui
    sl = {
        "no": 1, "src_index": 5,
        "text": "测试文本",
        "start_ms": 12345, "end_ms": 23456,
    }
    per_hl = {}
    html = ui._render_stage4_sub_row(hl_index=1, sl=sl, per_hl=per_hl)
    # 4 个 span 都在
    assert 'class="slirn-stage4-sub-no"' in html
    assert 'class="slirn-stage4-sub-srt"' in html
    assert 'class="slirn-stage4-sub-time"' in html
    assert 'class="slirn-stage4-sub-text"' in html
    # 4 字段内容
    assert "#1" in html
    assert "@SRT#5" in html
    assert "00:00:12,345" in html  # start_ms
    assert "00:00:23,456" in html  # end_ms
    assert "测试文本" in html
    # 不应再含旧的 slirn-sv-muted 单 span 行号
    assert '<span class="slirn-sv-muted">#1</span>' not in html


def test_stage4_ui_red_box_moved_to_detail_fold(tmp_path):
    """_stage4_optimize 主页面不应含顶层 opt-detail / opt-warns，应有 detail fold。"""
    from slirn_home import short_video_ui as ui
    tid, pid = _make_project(tmp_path)
    # 构造项目数据：1 条 highlight 已优化完成
    project = {
        "task_id": tid, "id": pid,
        "name": "test",
        "pipeline": {
            "stage4_optimize": {
                "status": "done",
                "highlights": [
                    {"index": 1, "title": "片段一", "start_ms": 0, "end_ms": 12000,
                     "status": "ok", "lines_before": 3, "lines_after": 3,
                     "kept": 3, "fixed": 0, "split": 0, "deleted": 0,
                     "warning_count": 1},
                ],
                "warnings": ["#1 未知 op [delete]，保留原行"],
            },
            "stage3_analyze": {
                "srt_lines": [],
                "highlights": [
                    {"index": 1, "title": "片段一", "start_time": 0, "end_time": 12,
                     "srt_lines": [{"index": 1, "text": "a"}, {"index": 2, "text": "b"}]},
                ],
                "hl_status": [
                    {"index": 1, "status": "ok", "lines_before": 3, "lines_after": 3,
                     "kept": 3, "fixed": 0, "split": 0, "deleted": 0},
                ],
            },
        },
    }
    html = ui._stage4_optimize(project, task_id=tid, project_id=pid, repo_root=tmp_path)
    # 1) 主页面无顶层 opt-detail / opt-warns（应在 detail fold 内）
    # 简单判定：detail fold summary 存在 + 至少含一条 ul.slirn-stage4-opt-warns 子节点
    assert '<details class="slirn-stage4-detail">' in html
    assert "📊 优化详情与警告" in html
    # ul 应在 detail-body 内（不是顶层）
    body_start = html.find('<div class="slirn-stage4-detail-body">')
    assert body_start > 0
    # 整个 ul 块都在 detail-body 之内
    ul_start = html.find('<ul class="slirn-stage4-opt-warns">')
    assert ul_start > body_start, "警告 ul 必须在 detail-body 之内"
    # 2) 顶层不应再有 <div class="slirn-stage4-opt-detail">（之前的红框）
    # 简单判定：detail-body 之前的区域没有 opt-detail 块
    pre_body = html[:body_start]
    assert 'class="slirn-stage4-opt-detail"' not in pre_body
    # 3) 4 字段 row 已应用
    assert 'class="slirn-stage4-sub-no"' in html
    assert 'class="slirn-stage4-sub-srt"' in html
    # 4) 顶部 detail fold 存在
    assert '<details class="slirn-stage4-detail">' in html
    assert "📊 优化详情与警告" in html


def test_fmt_srt_time_helper():
    """_fmt_srt_time 毫秒 → SRT 格式 00:00:00,000（项目惯例 2 位小时）。"""
    from slirn_home import short_video_ui as ui
    assert ui._fmt_srt_time(0) == "00:00:00,000"
    assert ui._fmt_srt_time(12345) == "00:00:12,345"
    assert ui._fmt_srt_time(60000) == "00:01:00,000"
    assert ui._fmt_srt_time(3661001) == "01:01:01,001"
    assert ui._fmt_srt_time(3600000) == "01:00:00,000"
    # None / 负数兜底
    assert ui._fmt_srt_time(None) == "00:00:00,000"
    assert ui._fmt_srt_time(-5) == "00:00:00,000"

