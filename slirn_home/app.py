"""Gradio Blocks 构造 — 全 HTML 自定义渲染，玻璃拟态统一风格。"""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any

import gradio as gr

# 确保 tasklib 可导入（slirn-standalone 在 sibling 或子模块挂载点）
# 必须在 from slirn_home.task_list import 之前调用，因为 task_list.py 顶层 import tasklib
from slirn_home.paths import ensure_tasklib_importable

ensure_tasklib_importable()

from tasklib import TaskManager, TaskStatus  # noqa: E402
from tasklib.hotword_lib import HotwordLibrary  # noqa: E402

from slirn_home.task_list import (
    confirm_delete,
    show_detail,
)

_DELETE_ARMED = "⚠️ 确认删除？"
_DELETE_IDLE = "🗑️ 删除"


# ============================================================
# 数据层：所有任务列表 / 热词库 / 创建任务的渲染 HTML
# ============================================================

def _esc(s: Any) -> str:
    return html.escape(str(s)) if s is not None else ""


def _time_ago(dt) -> str:
    """datetime / ISO 字符串 → 相对时间描述。"""
    from datetime import datetime
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            return "未知"
    if dt is None:
        return "未知"
    # 转 UTC→本地
    try:
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
    except Exception:
        pass
    delta = datetime.now() - dt
    if delta.days > 0:
        return f"{delta.days} 天前"
    if delta.seconds >= 3600:
        return f"{delta.seconds // 3600} 小时前"
    if delta.seconds >= 60:
        return f"{delta.seconds // 60} 分钟前"
    return "刚刚"


def _stats(mgr: TaskManager) -> dict:
    from datetime import datetime, timedelta
    summaries = mgr.list()
    total = len(summaries)
    week_ago = datetime.now() - timedelta(days=7)
    week_new = 0
    draft = 0
    done = 0
    for s in summaries:
        created = s.created_at
        if isinstance(created, str):
            try:
                created = datetime.fromisoformat(created.replace("Z", "+00:00")).replace(tzinfo=None)
            except Exception:
                created = None
        elif hasattr(created, "tzinfo") and created.tzinfo is not None:
            created = created.replace(tzinfo=None)
        if created and created >= week_ago:
            week_new += 1
        if s.status == TaskStatus.DRAFT:
            draft += 1
        elif s.status == TaskStatus.MUXED:
            done += 1
    return {"total": total, "week": week_new, "draft": draft, "done": done}


# =============== Dashboard ===============

def _render_dashboard(mgr: TaskManager, repo_root: Path) -> str:
    stats = _stats(mgr)
    recent = mgr.list()[:5]
    cards = "".join([
        f'''<div class="slirn-stat-card" data-action="goto-tasks">
            <div class="slirn-stat-icon">{icon}</div>
            <div class="slirn-stat-label">{label}</div>
            <div class="slirn-stat-value">{value}</div>
            <div class="slirn-stat-trend">{trend}</div>
        </div>'''
        for icon, label, value, trend in [
            ("📋", "总任务数", stats["total"], "所有状态汇总"),
            ("✨", "本周新建", stats["week"], "最近 7 天 ↑"),
            ("📝", "草稿数", stats["draft"], "待处理"),
            ("✅", "已完成", stats["done"], "视频字幕合成"),
        ]
    ])

    recent_html = ""
    for t in recent:
        status_label = t.status.value if hasattr(t.status, "value") else str(t.status)
        recent_html += f'''<div class="slirn-recent-item" data-action="view-task" data-task-id="{_esc(t.task_id)}">
            <div class="slirn-recent-avatar">🎬</div>
            <div class="slirn-recent-info">
                <div class="slirn-recent-name">{_esc(t.name)}</div>
                <div class="slirn-recent-meta">{_esc(status_label)} · {_esc(_time_ago(t.updated_at))}</div>
            </div>
        </div>'''
    if not recent_html:
        recent_html = '<div class="slirn-empty"><div class="slirn-empty-icon">📭</div><div class="slirn-empty-text">暂无任务 — 在「➕ 新建任务」中创建第一个</div></div>'

    return f'''<div id="slirn-tab-dashboard-inner" class="slirn-tab-inner">
    <div class="slirn-stats">{cards}</div>
    <div class="slirn-main-grid">
        <div class="slirn-card">
            <div class="slirn-panel-header">
                <div class="slirn-panel-title">📋 最近任务</div>
                <span class="slirn-panel-subtitle">最多显示 5 条</span>
            </div>
            <div class="slirn-recent-list">{recent_html}</div>
        </div>
        <div class="slirn-card">
            <div class="slirn-panel-header">
                <div class="slirn-panel-title">🕐 活动时间线</div>
            </div>
            <div class="slirn-recent-list">{recent_html}</div>
        </div>
    </div>
    </div>'''


# =============== 任务列表 ===============

def _status_dot_class(status) -> str:
    if status == TaskStatus.DRAFT:
        return "draft"
    if status == TaskStatus.MUXED:
        return "done"
    return "progress"


def _render_task_list(mgr: TaskManager) -> str:
    summaries = mgr.list()

    cards = ""
    for s in summaries:
        # status 可能是 enum 或 str — 兼容
        if hasattr(s.status, "value"):
            status_label = s.status.value
            dot_class = _status_dot_class(s.status)
        else:
            status_label = str(s.status)
            dot_class = "draft"
        cards += f'''<div class="slirn-task-card" data-action="view-task" data-task-id="{_esc(s.task_id)}">
            <div class="slirn-task-card-header">
                <span class="slirn-task-id">{_esc(s.task_id)}</span>
                <span class="slirn-status-badge"><span class="slirn-status-dot {dot_class}"></span>{_esc(status_label)}</span>
            </div>
            <div class="slirn-task-name">{_esc(s.name)}</div>
            <div class="slirn-task-video">🎬 {_esc(s.original_video or "—")}</div>
            <div class="slirn-task-meta">
                <span>创建 {_esc(_time_ago(s.created_at))}</span>
                <span>修改 {_esc(_time_ago(s.updated_at))}</span>
            </div>
            <div class="slirn-task-actions">
                <button class="slirn-btn slirn-btn-sm" data-action="view-task" data-task-id="{_esc(s.task_id)}">📄 详情</button>
                <button class="slirn-btn slirn-btn-sm" data-action="edit-task" data-task-id="{_esc(s.task_id)}">✏️ 编辑</button>
                <button class="slirn-btn slirn-btn-sm slirn-btn-primary" data-action="open-workbench" data-task-id="{_esc(s.task_id)}">✂️ 剪辑</button>
                <button class="slirn-btn slirn-btn-sm slirn-btn-danger" data-action="delete-task" data-task-id="{_esc(s.task_id)}">🗑️ 删除</button>
            </div>
        </div>'''

    if not cards:
        cards = '''<div class="slirn-empty" style="grid-column:1/-1;">
            <div class="slirn-empty-icon">📭</div>
            <div class="slirn-empty-text">暂无任务 — 点击「➕ 新建任务」开始</div>
        </div>'''

    return f'''<div id="slirn-tab-tasks-inner" class="slirn-tab-inner">
    <div class="slirn-toolbar">
        <div class="slirn-search-wrap">
            <input class="slirn-search-box" id="slirn-task-search" placeholder="搜索任务名 / ID / 视频..." />
        </div>
        <button class="slirn-btn" data-action="refresh-tasks">🔄 刷新</button>
        <button class="slirn-btn slirn-btn-primary" data-action="goto-create">➕ 新建任务</button>
    </div>
    <div class="slirn-task-grid">{cards}</div>
    <div class="slirn-status-msg" id="slirn-task-status" style="display:none;"></div>
    </div>'''


# =============== 详情 ===============

def _resolve_task_video(t) -> tuple[Path | None, str]:
    """任务要识别/预览的视频：截取段优先，否则原视频。返回 (path, label)。"""
    if t.segment and t.segment.path.exists():
        return t.segment.path, f"截取段（{t.segment.start} → {t.segment.end}）"
    if t.original_video_source.exists():
        return t.original_video_source, "完整原视频"
    return None, ""


def _ensure_segment_file(t) -> bool:
    """自愈：segment 文件缺失但原视频在 → 重新截取到 segment.path。

    历史原因 cut_preview 曾把截取文件移进 Gradio 缓存目录而 create_task 没找到，
    造成部分任务 metadata 有 segment 记录但 raw_input 无文件。幂等，成功返回 True。
    """
    if not t.segment:
        return False
    if t.segment.path.exists():
        return True
    src = t.original_video_source
    if not src.exists():
        return False
    try:
        from tasklib.video import cut_video
        t.segment.path.parent.mkdir(parents=True, exist_ok=True)
        cut_video(src, t.segment.path, t.segment.start, t.segment.end)
        return t.segment.path.exists()
    except Exception:  # noqa: BLE001 — 失败则回退原视频路径
        return False


def _render_subtitle_zone(task_id: str, t, mgr: TaskManager) -> str:
    """处理剪辑·第 1 步：字幕生成区（按钮 + 进度 + 播放器 + 字幕列表）— REQ-20260915-001。"""
    from slirn_home import asr_service

    outputs_dir = mgr.tasks_dir / task_id / "outputs"
    meta = asr_service.load_subtitle(outputs_dir)
    job = asr_service.job_status(task_id)

    video, video_label = _resolve_task_video(t)
    if video is not None:
        from tasklib.video import get_video_duration
        dur = get_video_duration(video)
        if dur:
            mm, ss = divmod(int(dur), 60)
            hh, mm = divmod(mm, 60)
            dur_str = f"{hh:02d}:{mm:02d}:{ss:02d}"
        else:
            dur_str = "时长未知"
        hint = f"识别对象：{video_label}（{dur_str}）"
        if dur and dur > 1800:
            hint += ' · <span style="color:#c2410c;">⚠️ 长视频识别耗时较长，建议先截取重点片段</span>'
    else:
        hint = "⚠️ 任务视频文件缺失（截取段和原视频都不在磁盘上）"

    hw_count = 0
    try:
        if t.hotwords_path.exists():
            hw_count = len([w for w in t.hotwords_path.read_text(encoding="utf-8").split() if w])
    except Exception:  # noqa: BLE001
        pass
    hint += f" · 任务热词 {hw_count} 个 · 模型 seaco-paraformer（热词优化）"

    job_state = job.get("state") if job else ("done" if meta and meta.get("segments") else "idle")

    # 字幕列表（已生成时）
    list_html = ""
    if meta and meta.get("segments"):
        rows = "".join(
            f'<div class="slirn-sub-row" data-task-id="{_esc(task_id)}"'
            f' data-start-ms="{int(s["start_ms"])}" data-end-ms="{int(s["end_ms"])}">'
            f'<span class="slirn-sub-idx">{int(s["i"])}</span>'
            f'<span class="slirn-sub-time">{_esc(s["start"])} → {_esc(s["end"])}</span>'
            f'<span class="slirn-sub-text">{_esc(s["text"])}</span>'
            f"</div>"
            for s in meta["segments"]
        )
        n = len(meta["segments"])
        created = _esc(meta.get("created_at", ""))
        src_label = "截取段时间轴" if meta.get("source") == "segment" else "原视频时间轴"
        list_html = f'''<div class="slirn-sub-meta">📝 {n} 段 · 识别于 {created} · {src_label} · 点击任一行定位播放，播放时当前行高亮</div>
        <div class="slirn-sub-list" id="slirn-sub-list">{rows}</div>'''

    gen_btn_label = "🔄 重新生成字幕" if (meta and meta.get("segments")) else "🎙 生成字幕"

    return f'''<div class="slirn-card" style="margin-top:16px;">
        <div class="slirn-panel-header">
            <div class="slirn-panel-title">🎬 处理剪辑 · 第 1 步：生成字幕</div>
        </div>
        <div class="slirn-form-hint">{hint}</div>
        <div id="slirn-sub-player-wrap" class="slirn-video-wrap slirn-sub-player-wrap" style="display:none;">
            <video id="slirn-sub-player" controls preload="metadata"></video>
        </div>
        <div id="slirn-asr-status" class="slirn-status-msg" style="display:none;"
             data-task-id="{_esc(task_id)}" data-state="{_esc(job_state or "idle")}"></div>
        {list_html}
        <div class="slirn-task-actions" style="margin-top:14px;">
            <button class="slirn-btn slirn-btn-primary" data-action="gen-subtitle" data-task-id="{_esc(task_id)}">{gen_btn_label}</button>
            <button class="slirn-btn" data-action="play-segment" data-task-id="{_esc(task_id)}">▶️ 播放视频</button>
        </div>
    </div>'''


def _render_revision_zone(task_id: str, t, mgr: TaskManager) -> str:
    """处理剪辑·第 2 步：字幕修订（大模型建议 + 手动决策）— REQ-20260915-005。"""
    from slirn_home import asr_service, revision_service

    outputs_dir = mgr.tasks_dir / task_id / "outputs"
    sub_meta = asr_service.load_subtitle(outputs_dir)
    rev = revision_service.load_revision(outputs_dir)
    job = revision_service.job_status(task_id)

    # ---- 状态 1：上一阶段未完成 → 引导 ----
    if not (sub_meta and sub_meta.get("segments")):
        return '''<div class="slirn-card" style="margin-top:16px;">
        <div class="slirn-panel-header"><div class="slirn-panel-title">🎬 处理剪辑 · 第 2 步：字幕修订</div></div>
        <div class="slirn-empty"><div class="slirn-empty-icon">🚧</div>
            <div class="slirn-empty-text">请先完成上一阶段「字幕生成」— 修订以生成的字幕列表为输入</div></div>
        <div class="slirn-task-actions" style="margin-top:14px;">
            <button class="slirn-btn slirn-btn-primary" data-action="wb-stage" data-pane="subtitle">🎙 去生成字幕</button>
        </div></div>'''

    entries = (rev or {}).get("entries") or []
    job_state = job.get("state") if job else ("done" if entries else "idle")
    model = _esc((rev or {}).get("model") or "")
    status_display = "" if job_state == "running" else "display:none;"
    running_html = ""
    if job_state == "running":
        import time as _t
        elapsed = int((job.get("finished_at") or _t.time()) - job.get("started_at", _t.time()))
        running_html = f"⏳ {_esc(job.get('stage') or '处理中')} · 已耗时 {elapsed}s"

    # ---- 状态 2：有字幕、无建议 → 说明 + 分析按钮 ----
    if not entries:
        return f'''<div class="slirn-card" style="margin-top:16px;">
        <div class="slirn-panel-header"><div class="slirn-panel-title">🎬 处理剪辑 · 第 2 步：字幕修订</div></div>
        <div class="slirn-form-hint">把上一阶段生成的 {len(sub_meta['segments'])} 段字幕交给大模型（qwen）逐段分析，识别：</div>
        <div class="slirn-rev-intro">
            <div>🟥 <strong>整行删除</strong> — 口癖、口头禅、语气词、无意义内容</div>
            <div>🟩 <strong>完整保留</strong> — 正常有效内容</div>
            <div>🟪 <strong>切分修剪</strong> — 行内重复只保留一次、剔除夹杂语气词（附建议保留文本）</div>
            <div>🟧 <strong>人工复核</strong> — 模型拿不准，交给你判断</div>
        </div>
        <div class="slirn-form-hint">每段建议都带具体分析说明；你在建议之上逐条决策（采纳/改判 + 手动说明）。</div>
        <div id="slirn-rev-status" class="slirn-status-msg" style="{status_display};"
             data-task-id="{_esc(task_id)}" data-state="{_esc(job_state)}">{running_html}</div>
        <div class="slirn-task-actions" style="margin-top:14px;">
            <button class="slirn-btn slirn-btn-primary" data-action="revise-subtitle" data-task-id="{_esc(task_id)}">🤖 大模型分析字幕</button>
        </div></div>'''

    # ---- 状态 3：建议列表（每行 = 原字幕 + 模型建议 + 手动决策）----
    rows = ""
    for e in entries:
        cat = e.get("category", "review")
        cat_label = dict(revision_service.LLM_CATEGORIES).get(cat, ("人工复核",))[0]
        keep_html = (
            f'<div class="slirn-rev-keeptext">✂️ 建议保留：「{_esc(e.get("keep_text") or "")}」</div>'
            if cat == "split" and e.get("keep_text") else ""
        )
        opts = "".join(
            f'<option value="{k}"{" selected" if e.get("decision", "pending") == k else ""}>{v}</option>'
            for k, v in revision_service.USER_DECISIONS.items()
        )
        rows += (
            f'<div class="slirn-rev-row" data-task-id="{_esc(task_id)}"'
            f' data-start-ms="{int(e.get("start_ms", 0))}" data-end-ms="{int(e.get("end_ms", 0))}">'
            f'<div class="slirn-rev-orig">'
            f'<span class="slirn-sub-idx">{int(e["i"])}</span>'
            f'<span class="slirn-sub-time">{_esc(e.get("start", ""))} → {_esc(e.get("end", ""))}</span>'
            f'<span class="slirn-sub-text">{_esc(e.get("text", ""))}</span></div>'
            f'<div class="slirn-rev-suggest"><span class="slirn-rev-badge {cat}">{cat_label}</span>'
            f'<span class="slirn-rev-note">{_esc(e.get("note", ""))}</span>{keep_html}</div>'
            f'<div class="slirn-rev-decide">'
            f'<select class="slirn-rev-select" data-i="{int(e["i"])}">{opts}</select>'
            f'<input class="slirn-rev-note-input" data-i="{int(e["i"])}"'
            f' placeholder="手动处理说明（可空）" value="{_esc(e.get("user_note") or "")}" /></div>'
            f"</div>"
        )

    n = len(entries)
    cat_counts = {k: 0 for k in revision_service.LLM_CATEGORIES}
    for e in entries:
        cat_counts[e.get("category", "review")] = cat_counts.get(e.get("category", "review"), 0) + 1
    decided = sum(1 for e in entries if e.get("decision") != "pending")
    created = _esc((rev or {}).get("created_at", ""))
    stats = (
        f"📝 {n} 段 · 分析于 {created} · 模型 {model} · "
        f"保留 {cat_counts.get('keep', 0)} / 删除 {cat_counts.get('delete', 0)} / "
        f"切分 {cat_counts.get('split', 0)} / 复核 {cat_counts.get('review', 0)}"
        f" · ✅ 已决策 <b>{decided}/{n}</b> · 点击行定位播放"
    )

    return f'''<div class="slirn-card" style="margin-top:16px;">
        <div class="slirn-panel-header"><div class="slirn-panel-title">🎬 处理剪辑 · 第 2 步：字幕修订</div></div>
        <div class="slirn-sub-meta">{stats}</div>
        <div id="slirn-rev-player-wrap" class="slirn-video-wrap slirn-sub-player-wrap" style="display:none;">
            <video id="slirn-rev-player" controls preload="metadata"></video>
        </div>
        <div id="slirn-rev-status" class="slirn-status-msg" style="{status_display};"
             data-task-id="{_esc(task_id)}" data-state="{_esc(job_state)}">{running_html}</div>
        <div class="slirn-rev-list" id="slirn-rev-list">{rows}</div>
        <div class="slirn-task-actions" style="margin-top:14px;">
            <button class="slirn-btn slirn-btn-primary" data-action="save-revision" data-task-id="{_esc(task_id)}">💾 保存修订决策</button>
            <button class="slirn-btn" data-action="play-rev-video" data-task-id="{_esc(task_id)}">▶️ 播放视频</button>
            <button class="slirn-btn" data-action="revise-subtitle" data-task-id="{_esc(task_id)}" data-has-revision="1">🔄 重新分析</button>
        </div></div>'''


def _task_echo_fragments(t) -> tuple[str, str, str]:
    """任务回显片段（REQ-20260915-002）— 详情页 / 工作台共用。

    Returns:
        (video_disp, seg_disp, hotwords_disp) 三个 HTML 片段。
    """
    # ---- 原视频：格式对齐新建步骤1 的文件信息条「📁 name（size MB · duration）」 ----
    src = t.original_video_source
    if src.exists():
        from tasklib.video import get_video_duration
        dur = get_video_duration(src)
        if dur:
            mm_, ss_ = divmod(int(dur), 60)
            hh_, mm_ = divmod(mm_, 60)
            dur_str = f"{hh_:02d}:{mm_:02d}:{ss_:02d}"
        else:
            dur_str = "时长未知"
        size_mb = round(src.stat().st_size / 1024 / 1024, 1)
        video_disp = f"📁 {_esc(src.name)}（{size_mb} MB · {dur_str}）"
    else:
        video_disp = f"📁 {_esc(src.name)}（⚠️ 文件缺失）"

    # ---- 截取段：原样起止时间 + 时长 + 大小（对齐新建「✅ 待剪辑视频已生成 N MB」）----
    if t.segment:
        seg_dur = "时长未知"
        try:
            from tasklib.time_utils import parse_time
            _ds = (parse_time(t.segment.end) - parse_time(t.segment.start)).total_seconds()
            if _ds > 0:
                seg_dur = f"{int(_ds // 3600):02d}:{int(_ds % 3600 // 60):02d}:{int(_ds % 60):02d}"
        except ValueError:
            pass
        if t.segment.path.exists():
            seg_mb = round(t.segment.path.stat().st_size / 1024 / 1024, 1)
            seg_disp = f"⏱ {_esc(t.segment.start)} → {_esc(t.segment.end)}（{seg_dur} · {seg_mb} MB）"
        else:
            seg_disp = f"⏱ {_esc(t.segment.start)} → {_esc(t.segment.end)}（{seg_dur} · ⚠️ 截取文件缺失）"
    else:
        seg_disp = "未截取 · 使用完整原视频"

    # ---- 热词：chips 全量回显（带来源标签，与新建步骤3 显示方式一致；只读无 ✕）----
    hw_words: list[str] = []
    try:
        if t.hotwords_path.exists():
            hw_words = [w for w in t.hotwords_path.read_text(encoding="utf-8").split() if w]
    except Exception:  # noqa: BLE001
        pass
    sources = t.hotword_sources or {}
    src_labels = {"inherit": "继承", "pick": "已选", "manual": "手动"}
    if hw_words:
        chips = ""
        for w in hw_words:
            src_tag = sources.get(w)
            label = f'<span class="slirn-hw-chip-label">{src_labels.get(src_tag, src_tag)}</span>' if src_tag else ""
            cls = f"slirn-hw-chip src-{src_tag}" if src_tag else "slirn-hw-chip"
            chips += f'<span class="{cls}" data-word="{_esc(w)}">{label}<span class="slirn-hw-chip-text">{_esc(w)}</span></span>'
        summary = f"共 {len(hw_words)} 个词" + (" · 来自继承全部公共库" if t.inherit_public else "")
        hotwords_disp = f'<div class="slirn-hw-chips">{chips}</div><div class="slirn-hw-summary">{_esc(summary)}</div>'
    else:
        hotwords_disp = "（无）"

    return video_disp, seg_disp, hotwords_disp


def _render_task_detail(task_id: str, mgr: TaskManager) -> str:
    try:
        t = mgr.get(task_id)
    except Exception as e:
        return f'<div class="slirn-empty"><div class="slirn-empty-icon">⚠️</div><div class="slirn-empty-text">{_esc(e)}</div></div>'

    from tasklib.models import TASK_STATUS_LABEL
    status_label = TASK_STATUS_LABEL.get(t.status, str(getattr(t.status, "value", t.status)))
    video_disp, seg_disp, hotwords_disp = _task_echo_fragments(t)
    src = t.original_video_source

    rows = [
        ("任务 ID", _esc(t.task_id)),
        ("任务名", _esc(t.name)),
        ("状态", _esc(status_label)),
        ("原始视频", f"{video_disp}<div class='slirn-detail-sub'>{_esc(str(src))}</div>"),
        ("截取段", seg_disp),
        ("热词", hotwords_disp),
        ("创建时间", _esc(t.created_at.isoformat() if hasattr(t.created_at, "isoformat") else str(t.created_at))),
        ("修改时间", _esc(t.updated_at.isoformat() if hasattr(t.updated_at, "isoformat") else str(t.updated_at))),
    ]
    if t.segment:
        rows.append(("截取文件", f"<div class='slirn-detail-sub'>{_esc(str(t.segment.path))}</div>"))

    detail_rows = "".join([
        f'<div class="slirn-detail-row"><div class="slirn-detail-key">{k}</div><div class="slirn-detail-val">{v}</div></div>'
        for k, v in rows
    ])

    return f'''<div class="slirn-card">
        <div class="slirn-panel-header">
            <div class="slirn-panel-title">📄 任务详情</div>
            <button class="slirn-btn slirn-btn-sm" data-action="close-detail">✕ 关闭</button>
        </div>
        {detail_rows}
        <div class="slirn-task-actions" style="margin-top:20px;">
            <button class="slirn-btn slirn-btn-danger" data-action="delete-task" data-task-id="{_esc(task_id)}">🗑️ 删除任务</button>
        </div>
    </div>
    {_render_subtitle_zone(task_id, t, mgr)}'''


# =============== 剪辑工作台（REQ-20260915-003） ===============

# 阶段定义：(key, 对应 TaskStatus, 标题, 图标, 说明) — 与 TaskStatus 管线一一对应
_WB_STAGES = [
    ("assets",          "ASSETS_READY",         "素材准备", "📦", "上传视频 · 时间截取 · 任务热词"),
    ("subtitle",        "SUBTITLE_GENERATED",   "字幕生成", "🎙", "FunASR seaco-paraformer + 热词识别"),
    ("subtitle_review", "SUBTITLE_REVIEWED",    "字幕修订", "📝", "对照视频逐段校对、修改字幕文本与时间"),
    ("rough_cut",       "ROUGH_CUT_DONE",       "粗剪",     "✂️", "按字幕段落选择保留片段，粗剪拼接"),
    ("fine_subtitle",   "FINE_SUBTITLE_DONE",   "精剪字幕", "🔧", "对粗剪结果重新生成精确字幕"),
    ("fine_review",     "FINE_SUBTITLE_REVIEWED", "精剪修订", "🔎", "精剪字幕二次校对"),
    ("fine_cut",        "FINE_CUT_DONE",        "精剪视频", "🎬", "按精剪段生成成品视频"),
    ("mux",             "MUXED",                "字幕合成", "🎞️", "字幕烧录进画面 / 封装输出成品"),
]


def _wb_stage_states(t) -> list[str]:
    """各阶段状态：done / current / pending。

    素材准备看磁盘资产（原视频在即完成，DRAFT 状态也算）；
    字幕生成看产物 subtitle.json、字幕修订看 revision.json（服务器重启后内存 job 不在，以磁盘为准）；
    其余按 TaskStatus 管线序比较。
    """
    from tasklib.models import TaskStatus

    from slirn_home import asr_service as _asr_mod
    from slirn_home import revision_service as _rev_mod

    rank = {s.name: i for i, s in enumerate(TaskStatus)}
    cur_rank = rank.get(getattr(t.status, "name", str(t.status)), 0)
    states: list[str] = []
    assets_done = t.original_video_source.exists() or t.original_video_symlink.exists()
    outputs_dir = Path(str(t.hotwords_path)).parent / "outputs"
    sub_meta = None
    rev_meta = None
    try:
        sub_meta = _asr_mod.load_subtitle(outputs_dir)
    except Exception:  # noqa: BLE001
        pass
    try:
        rev_meta = _rev_mod.load_revision(outputs_dir)
    except Exception:  # noqa: BLE001
        pass
    subtitle_done = bool(sub_meta and sub_meta.get("segments"))
    review_done = bool(rev_meta and rev_meta.get("entries"))
    for key, status_name, *_rest in _WB_STAGES:
        if key == "assets":
            states.append("done" if assets_done else "pending")
        elif key == "subtitle":
            states.append("done" if (subtitle_done or cur_rank >= rank["SUBTITLE_GENERATED"]) else "pending")
        elif key == "subtitle_review":
            states.append(
                "done" if (review_done or cur_rank >= rank["SUBTITLE_REVIEWED"]) else "pending"
            )
        else:
            states.append("done" if cur_rank >= rank[status_name] else "pending")
    # current = 第一个 pending
    for i, s in enumerate(states):
        if s == "pending":
            states[i] = "current"
            break
    return states


def _render_workbench(task_id: str, mgr: TaskManager) -> str:
    """剪辑工作台：顶部任务信息 + 左侧阶段步骤条 + 右侧各阶段执行面板。"""
    try:
        t = mgr.get(task_id)
    except Exception as e:
        return f'<div class="slirn-empty"><div class="slirn-empty-icon">⚠️</div><div class="slirn-empty-text">{_esc(e)}</div></div>'

    from tasklib.models import TASK_STATUS_LABEL
    status_label = TASK_STATUS_LABEL.get(t.status, str(getattr(t.status, "value", t.status)))
    video_disp, seg_disp, hotwords_disp = _task_echo_fragments(t)

    # ---- 顶部：任务信息 ----
    top_rows = "".join([
        f'<div class="slirn-detail-row"><div class="slirn-detail-key">{k}</div><div class="slirn-detail-val">{v}</div></div>'
        for k, v in [
            ("任务", f"{_esc(t.name)}<span class='slirn-wb-tid'>（{_esc(task_id)}）</span>"),
            ("状态", _esc(status_label)),
            ("原始视频", video_disp),
            ("截取段", seg_disp),
            ("热词", hotwords_disp),
        ]
    ])

    # ---- 左：阶段步骤条 ----
    states = _wb_stage_states(t)
    # 默认聚焦：最后一个 done 的下一阶段（= current）；全完成 → 最后一阶段
    focus = next((i for i, s in enumerate(states) if s == "current"), len(_WB_STAGES) - 1)
    stage_items = ""
    for i, (key, _st, title, icon, desc) in enumerate(_WB_STAGES):
        state = states[i]
        mark = "✓" if state == "done" else ("▶" if state == "current" else str(i + 1))
        stage_items += (
            f'<div class="slirn-wb-stage {state}{" active" if i == focus else ""}" '
            f'data-action="wb-stage" data-pane="{key}">'
            f'<span class="slirn-wb-stage-mark">{mark}</span>'
            f'<div class="slirn-wb-stage-body"><div class="slirn-wb-stage-title">{icon} {title}</div>'
            f'<div class="slirn-wb-stage-desc">{desc}</div></div></div>'
        )

    # ---- 右：各阶段面板 ----
    def _pane_planned(i: int, desc: str) -> str:
        return (
            '<div class="slirn-empty"><div class="slirn-empty-icon">🚧</div>'
            '<div class="slirn-empty-text">规划中 — 该阶段将在后续版本提供</div>'
            f'<div class="slirn-form-hint">{_esc(desc)}</div></div>'
        )

    assets_pane = "".join([
        f'<div class="slirn-detail-row"><div class="slirn-detail-key">{k}</div><div class="slirn-detail-val">{v}</div></div>'
        for k, v in [
            ("原始视频", f"{video_disp}<div class='slirn-detail-sub'>{_esc(str(t.original_video_source))}</div>"),
            ("截取段", seg_disp),
            ("热词", hotwords_disp),
        ]
    ])

    panes = {
        "assets": f'<div class="slirn-wb-pane-card"><div class="slirn-wb-pane-title">📦 资产清单</div>{assets_pane}</div>',
        "subtitle": _render_subtitle_zone(task_id, t, mgr),
        "subtitle_review": _render_revision_zone(task_id, t, mgr),
    }
    for i, (key, _st, _t2, _ic, desc) in enumerate(_WB_STAGES):
        if key in panes:
            continue
        panes[key] = _pane_planned(i, desc)

    hidden_attr = ' style="display:none;"'
    pane_html = "".join(
        f'<div class="slirn-wb-pane" id="slirn-wb-pane-{key}"{hidden_attr if i != focus else ""}>{panes[key]}</div>'
        for i, (key, *_r) in enumerate(_WB_STAGES)
    )

    return f'''<div id="slirn-tab-workbench-inner" class="slirn-tab-inner" data-task-id="{_esc(task_id)}">
    <div class="slirn-card slirn-wb-top">
        <div class="slirn-panel-header">
            <div class="slirn-panel-title">✂️ 剪辑工作台 · {_esc(t.name)}</div>
            <div>
                <button class="slirn-btn slirn-btn-sm" data-action="edit-task" data-task-id="{_esc(task_id)}">✏️ 编辑任务</button>
                <button class="slirn-btn slirn-btn-sm" data-action="goto-tasks">📋 返回列表</button>
            </div>
        </div>
        {top_rows}
    </div>
    <div class="slirn-wb-main">
        <div class="slirn-card slirn-wb-stages">{stage_items}</div>
        <div class="slirn-wb-panes">{pane_html}</div>
    </div>
    </div>'''


# =============== 热词库 ===============

def _render_hotword_picker(repo_root: Path) -> str:
    """渲染公共库的「选择器」网格（用于「新建任务」页：点击切换选中状态，无 X / 无批量按钮）。"""
    hwlib = HotwordLibrary(repo_root)
    grouped = hwlib.list_grouped()

    sections = ""
    for cat, words in grouped.items():
        cells = ""
        for w in words:
            cells += (
                f'<div class="slirn-hotword-cell slirn-pick-cell" data-word="{_esc(w)}">'
                f'  <span class="slirn-cell-text">{_esc(w)}</span>'
                f'</div>'
            )
        for _ in range(len(words) % 5):
            cells += '<div class="slirn-hotword-cell empty"></div>'
        cat_id = _esc(cat)
        sections += f'''<div class="slirn-category-section slirn-pick-section" data-category="{cat_id}">
            <div class="slirn-category-title">
                <span class="slirn-cat-name">{cat_id} <span class="slirn-category-count">{len(words)}</span></span>
            </div>
            <div class="slirn-hotword-grid">{cells}</div>
        </div>'''
    if not sections:
        sections = '<div class="slirn-empty"><div class="slirn-empty-text">公共热词库为空 — 先去「📚 热词库」添加</div></div>'
    return sections

def _render_hotword_lib(repo_root: Path) -> str:
    hwlib = HotwordLibrary(repo_root)
    grouped = hwlib.list_grouped()

    categories_html = ""
    for cat, words in grouped.items():
        cells = ""
        for w in words:
            cells += (
                f'<div class="slirn-hotword-cell" data-word="{_esc(w)}">'
                f'  <span class="slirn-cell-text">{_esc(w)}</span>'
                f'  <button class="slirn-cell-delete" data-action="hw-delete-one" data-word="{_esc(w)}" title="删除此词">✕</button>'
                f'</div>'
            )
        # 补齐空白（让网格保持 5 列对齐）
        for _ in range(len(words) % 5):
            cells += '<div class="slirn-hotword-cell empty"></div>'
        cat_id = _esc(cat)
        categories_html += f'''<div class="slirn-category-section" data-category="{cat_id}">
            <div class="slirn-category-title">
                <span class="slirn-cat-name">{cat_id} <span class="slirn-category-count">{len(words)}</span></span>
                <div class="slirn-category-actions">
                    <button class="slirn-btn-mini" data-action="hw-cat-select-all">☑ 全选</button>
                    <button class="slirn-btn-mini" data-action="hw-cat-invert">⇄ 反选</button>
                    <button class="slirn-btn-mini slirn-btn-mini-danger" data-action="hw-cat-delete">🗑 删除选中</button>
                </div>
            </div>
            <div class="slirn-hotword-grid">{cells}</div>
        </div>'''

    if not categories_html:
        categories_html = '<div class="slirn-empty"><div class="slirn-empty-icon">📚</div><div class="slirn-empty-text">公共热词库为空 — 在下方添加第一个词</div></div>'

    return f'''<div class="slirn-card">
        <div class="slirn-panel-header">
            <div class="slirn-panel-title">📚 现有词（按分类）</div>
            <span class="slirn-panel-subtitle">{_esc(sum(len(v) for v in grouped.values()))} 个词 · {_esc(len(grouped))} 个分类 · 鼠标移上去显示 ✕ 可单删</span>
        </div>
        {categories_html}
    </div>
    <div class="slirn-card">
        <div class="slirn-panel-header">
            <div class="slirn-panel-title">➕ 添加新词</div>
        </div>
        <div class="slirn-form-grid">
            <div class="slirn-form-row">
                <label class="slirn-form-label">词</label>
                <textarea class="slirn-input" id="slirn-hw-word" rows="3" placeholder="例如：张老师（多个用空格 / 逗号 / 换行分隔）"></textarea>
            </div>
            <div class="slirn-form-row">
                <label class="slirn-form-label">分类</label>
                <input class="slirn-input" id="slirn-hw-category" placeholder="例如：讲师（留空 = 默认）" />
            </div>
        </div>
        <div class="slirn-form-hint">💡 支持一次添加多个词（空格 / 逗号 / 换行分隔），已存在的词会自动跳过</div>
        <button class="slirn-btn slirn-btn-primary" data-action="hw-add" style="margin-top:12px;">➕ 添加</button>
        <div class="slirn-status-msg" id="slirn-hw-status" style="display:none;"></div>
    </div>'''


# =============== 新建任务 ===============

def _render_create_task(repo_root: Path, edit=None) -> str:
    """新建任务页；edit 传入 tasklib.models.Task 时渲染为「编辑任务」模式（REQ-20260915-003）：
    预填原视频信息（不可换视频）、截取时间、任务名、热词三来源状态。"""
    picker_html = _render_hotword_picker(repo_root)

    if edit is not None:
        # ---- 编辑模式预填 ----
        from tasklib.video import get_video_duration
        src = edit.original_video_source
        dur = get_video_duration(src) if src.exists() else None
        dur_str = ""
        if dur:
            _mm, _ss = divmod(int(dur), 60)
            _hh, _mm = divmod(_mm, 60)
            dur_str = f"{_hh:02d}:{_mm:02d}:{_ss:02d}"
        size_mb = round(src.stat().st_size / 1024 / 1024, 1) if src.exists() else 0
        # 公共库选中态：来源为 pick 的词在网格里预亮（直接加 .selected，JS 端照常收集）
        sources = edit.hotword_sources or {}
        if sources:
            missing_picked: list[str] = []
            for w in sorted(sources):
                if sources[w] != "pick":
                    continue
                marker = f'data-word="{_esc(w)}"'
                fresh = (
                    f'<div class="slirn-hotword-cell slirn-pick-cell" {marker}>',
                    f'<div class="slirn-hotword-cell slirn-pick-cell selected" {marker}>',
                )
                if fresh[0] in picker_html:
                    picker_html = picker_html.replace(fresh[0], fresh[1])
                else:
                    missing_picked.append(w)  # 词已不在公共库（被删）→ 不能丢，追加专属分区
            if missing_picked:
                cells = "".join(
                    f'<div class="slirn-hotword-cell slirn-pick-cell selected" data-word="{_esc(w)}">'
                    f'  <span class="slirn-cell-text">{_esc(w)}</span></div>'
                    for w in missing_picked
                )
                cat = "任务已选（不在公共库）"
                picker_html += (
                    f'<div class="slirn-category-section slirn-pick-section" data-category="{cat}">'
                    f'<div class="slirn-category-title"><span class="slirn-cat-name">{cat} '
                    f'<span class="slirn-category-count">{len(missing_picked)}</span></span></div>'
                    f'<div class="slirn-hotword-grid">{cells}</div></div>'
                )
        manual_words = [w for w in (edit.hotwords_path.read_text(encoding="utf-8").split()
                                    if edit.hotwords_path.exists() else []) if sources.get(w) == "manual"]
        return f'''<div id="slirn-tab-create-inner" class="slirn-tab-inner">
    <div id="slirn-edit-state"
         data-task-id="{_esc(edit.task_id)}"
         data-video-path="{_esc(str(src).replace(chr(92), '/'))}"
         data-video-url="/slirn/api/video/{_esc(edit.task_id)}?src=original"
         data-duration-seconds="{float(dur) if dur else 0}"></div>
    <div class="slirn-card">
        <div class="slirn-panel-header">
            <div class="slirn-panel-title">📁 步骤 1 · 原视频（编辑模式，不可更换）</div>
        </div>
        <div class="slirn-status-msg">📁 {_esc(src.name)}（{size_mb} MB · {dur_str or "时长未知"}）</div>
        <div class="slirn-form-hint" style="margin-top:6px;">如需更换视频请新建任务</div>
    </div>

    <div class="slirn-card" id="slirn-player-card" style="display:none;">
        <div class="slirn-panel-header">
            <div class="slirn-panel-title">🎥 步骤 2 · 视频预览 + 时间设置</div>
            <span class="slirn-panel-subtitle">拖动滑块定位 · 点击按钮设置开始/结束</span>
        </div>
        <div class="slirn-video-wrap">
            <video id="slirn-player" controls preload="metadata"></video>
        </div>
        <div class="slirn-slider-wrap">
            <input type="range" class="slirn-slider" id="slirn-seek" min="0" max="600" step="0.1" value="0" />
            <div class="slirn-time-display" id="slirn-time-display">00:00:00.000 / 00:00:00.000</div>
        </div>
        <div style="display:flex; gap:8px; margin-top:12px;">
            <button class="slirn-btn slirn-btn-primary" data-action="set-start">⏱ 设为开始</button>
            <button class="slirn-btn slirn-btn-primary" data-action="set-end">⏱ 设为结束</button>
        </div>
        <div class="slirn-form-grid" style="margin-top:16px;">
            <div class="slirn-form-row">
                <label class="slirn-form-label">开始时间 (HH:MM:SS.mmm)</label>
                <input class="slirn-input" id="slirn-start-box" placeholder="00:00:00.000" value="{_esc(edit.segment.start if edit.segment else '')}" />
            </div>
            <div class="slirn-form-row">
                <label class="slirn-form-label">结束时间 (HH:MM:SS.mmm)</label>
                <input class="slirn-input" id="slirn-end-box" placeholder="00:00:00.000" value="{_esc(edit.segment.end if edit.segment else '')}" />
            </div>
        </div>
        <button class="slirn-btn slirn-btn-primary" data-action="cut-preview" style="margin-top:8px;">🎬 待剪辑视频预览</button>
        <div id="slirn-cut-msg" style="margin-top:12px;"></div>
        <div id="slirn-cut-preview-wrap" style="display:none; margin-top:12px;">
            <div class="slirn-video-wrap">
                <video id="slirn-cut-preview" controls preload="metadata"></video>
            </div>
        </div>
    </div>

    <div class="slirn-card">
        <div class="slirn-panel-header">
            <div class="slirn-panel-title">📝 步骤 3 · 任务信息</div>
        </div>
        <div class="slirn-form-row">
            <label class="slirn-form-label">任务名（留空 = 文件名）</label>
            <input class="slirn-input" id="slirn-task-name" placeholder="例如：讲师介绍视频" value="{_esc(edit.name)}" />
        </div>
        <div class="slirn-form-row">
            <label class="slirn-form-label">🔥 任务级热词（3 种来源可叠加）</label>

            <label class="slirn-hw-inherit-row" for="slirn-hw-inherit-all">
                <input type="checkbox" id="slirn-hw-inherit-all" {'checked' if edit.inherit_public else ''} />
                <span>① <strong>继承公共库所有热词</strong>（无需逐个勾选 · 公共库新增自动生效）</span>
            </label>

            <div class="slirn-form-hint" style="margin-top:10px;">
                ② <strong>从公共库选择</strong>（仅在未勾选「继承」时显示 · 点击切换）
            </div>
            <div id="slirn-hw-picker-wrap">
                <div id="slirn-hw-picker">
                    {picker_html}
                </div>
            </div>

            <div class="slirn-form-hint" style="margin-top:14px;">③ <strong>手动输入</strong>（空格 / 换行分隔 · 与上面两个叠加）</div>
            <textarea class="slirn-textarea" id="slirn-hotwords-manual" rows="3" placeholder="手动输入热词...">{_esc(' '.join(manual_words))}</textarea>

            <div class="slirn-form-hint" style="margin-top:14px;">✅ <strong>最终生效的热词</strong>（点击 ✕ 可移除）</div>
            <div class="slirn-hw-chips" id="slirn-hw-chips"></div>
            <div class="slirn-hw-summary" id="slirn-hw-summary"></div>
        </div>
    </div>

    <div style="display:flex; gap:12px;">
        <button class="slirn-btn slirn-btn-primary" data-action="update-task" data-task-id="{_esc(edit.task_id)}" style="flex:1;">💾 保存修改</button>
        <button class="slirn-btn slirn-btn-danger" data-action="cancel-create">❌ 取消</button>
    </div>
    <div id="slirn-create-msg" style="margin-top:12px;"></div>
    </div>'''

    return f'''<div id="slirn-tab-create-inner" class="slirn-tab-inner">
    <div class="slirn-card">
        <div class="slirn-panel-header">
            <div class="slirn-panel-title">📁 步骤 1 · 选择视频</div>
        </div>
        <div class="slirn-dropzone" data-action="trigger-file">
            <div class="slirn-dropzone-icon">📂</div>
            <div>点击选择视频文件 · 或把视频拖到这里</div>
            <div class="slirn-form-hint">支持 mp4 / avi / mkv / mov / webm / ts / mpeg（最大 10 GB）</div>
        </div>
        <div id="slirn-file-info-display" style="margin-top:12px;"></div>
    </div>

    <div class="slirn-card" id="slirn-player-card" style="display:none;">
        <div class="slirn-panel-header">
            <div class="slirn-panel-title">🎥 步骤 2 · 视频预览 + 时间设置</div>
            <span class="slirn-panel-subtitle">拖动滑块定位 · 点击按钮设置开始/结束</span>
        </div>
        <div class="slirn-video-wrap">
            <video id="slirn-player" controls preload="metadata"></video>
        </div>
        <div class="slirn-slider-wrap">
            <input type="range" class="slirn-slider" id="slirn-seek" min="0" max="600" step="0.1" value="0" />
            <div class="slirn-time-display" id="slirn-time-display">00:00:00.000 / 00:00:00.000</div>
        </div>
        <div style="display:flex; gap:8px; margin-top:12px;">
            <button class="slirn-btn slirn-btn-primary" data-action="set-start">⏱ 设为开始</button>
            <button class="slirn-btn slirn-btn-primary" data-action="set-end">⏱ 设为结束</button>
        </div>
        <div class="slirn-form-grid" style="margin-top:16px;">
            <div class="slirn-form-row">
                <label class="slirn-form-label">开始时间 (HH:MM:SS.mmm)</label>
                <input class="slirn-input" id="slirn-start-box" placeholder="00:00:00.000" />
            </div>
            <div class="slirn-form-row">
                <label class="slirn-form-label">结束时间 (HH:MM:SS.mmm)</label>
                <input class="slirn-input" id="slirn-end-box" placeholder="00:00:00.000" />
            </div>
        </div>
        <button class="slirn-btn slirn-btn-primary" data-action="cut-preview" style="margin-top:8px;">🎬 待剪辑视频预览</button>
        <div id="slirn-cut-msg" style="margin-top:12px;"></div>
        <div id="slirn-cut-preview-wrap" style="display:none; margin-top:12px;">
            <div class="slirn-video-wrap">
                <video id="slirn-cut-preview" controls preload="metadata"></video>
            </div>
        </div>
    </div>

    <div class="slirn-card">
        <div class="slirn-panel-header">
            <div class="slirn-panel-title">📝 步骤 3 · 任务信息</div>
        </div>
        <div class="slirn-form-row">
            <label class="slirn-form-label">任务名（留空 = 文件名）</label>
            <input class="slirn-input" id="slirn-task-name" placeholder="例如：讲师介绍视频" />
        </div>
        <div class="slirn-form-row">
            <label class="slirn-form-label">🔥 任务级热词（3 种来源可叠加）</label>

            <label class="slirn-hw-inherit-row" for="slirn-hw-inherit-all">
                <input type="checkbox" id="slirn-hw-inherit-all" />
                <span>① <strong>继承公共库所有热词</strong>（无需逐个勾选 · 公共库新增自动生效）</span>
            </label>

            <div class="slirn-form-hint" style="margin-top:10px;">
                ② <strong>从公共库选择</strong>（仅在未勾选「继承」时显示 · 点击切换）
            </div>
            <div id="slirn-hw-picker-wrap">
                <div id="slirn-hw-picker">
                    {picker_html}
                </div>
            </div>

            <div class="slirn-form-hint" style="margin-top:14px;">③ <strong>手动输入</strong>（空格 / 换行分隔 · 与上面两个叠加）</div>
            <textarea class="slirn-textarea" id="slirn-hotwords-manual" rows="3" placeholder="手动输入热词..."></textarea>

            <div class="slirn-form-hint" style="margin-top:14px;">✅ <strong>最终生效的热词</strong>（点击 ✕ 可移除）</div>
            <div class="slirn-hw-chips" id="slirn-hw-chips"></div>
            <div class="slirn-hw-summary" id="slirn-hw-summary"></div>
        </div>
    </div>

    <div style="display:flex; gap:12px;">
        <button class="slirn-btn slirn-btn-primary" data-action="create-task" style="flex:1;">✅ 创建任务</button>
        <button class="slirn-btn slirn-btn-danger" data-action="cancel-create">❌ 取消</button>
    </div>
    <div id="slirn-create-msg" style="margin-top:12px;"></div>
    </div>'''


# ============================================================
# JS 路由：捕获自定义 HTML 元素的点击 → 转发到 Gradio hidden button
# ============================================================

ROUTER_JS = """
<script>
// Slirn 事件路由 — 走自定义 /slirn/api/* 同步路由，绕开 Gradio 队列
(function() {
  var SLIRN_API = '/slirn/api';

  function postJSON(url, payload) {
    return fetch(url, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload || {})
    }).then(function(r) { return r.json(); }).catch(function(e) { return {ok: false, error: String(e)}; });
  }

  function postForm(url, formData) {
    return fetch(url, {
      method: 'POST',
      body: formData
    }).then(function(r) { return r.json(); }).catch(function(e) { return {ok: false, error: String(e)}; });
  }

  function getInput(id) {
    var el = document.getElementById(id);
    return el ? el.value : '';
  }

  function toast(msg, type) {
    type = type || 'success';
    var existing = document.querySelector('.slirn-toast');
    if (existing) existing.remove();
    var t = document.createElement('div');
    t.className = 'slirn-toast ' + type;
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(function() { t.style.opacity = '0'; t.style.transition = 'opacity 0.3s'; }, 2400);
    setTimeout(function() { t.remove(); }, 2800);
  }
  window.slirnToast = toast;

  // 更新分类标题里"删除选中"按钮上的计数
  function updateDeleteCount(section) {
    if (!section) return;
    var btn = section.querySelector('[data-action="hw-cat-delete"]');
    if (!btn) return;
    var n = section.querySelectorAll('.slirn-hotword-cell.selected').length;
    btn.textContent = n > 0 ? ('🗑 删除选中 (' + n + ')') : '🗑 删除选中';
    btn.classList.toggle('has-selection', n > 0);
  }
  window.slirnUpdateDeleteCount = updateDeleteCount;

  // ========== 任务级热词「选择器」逻辑（新建任务页） ==========

  // 收集当前「选中」状态的公共库词（按 DOM 出现顺序去重）
  function collectPickedHotwords() {
    var seen = {};
    var out = [];
    var cells = document.querySelectorAll('#slirn-hw-picker .slirn-pick-cell.selected');
    cells.forEach(function(c) {
      var w = c.getAttribute('data-word');
      if (w && !seen[w]) { seen[w] = 1; out.push(w); }
    });
    return out;
  }

  // 收集「继承公共库所有热词」时按显示顺序的词列表（用于预览 chips）
  function collectAllPublicHotwords() {
    var seen = {};
    var out = [];
    document.querySelectorAll('#slirn-hw-picker .slirn-pick-cell').forEach(function(c) {
      var w = c.getAttribute('data-word');
      if (w && !seen[w]) { seen[w] = 1; out.push(w); }
    });
    return out;
  }

  // 解析手动输入的词
  function parseManual(text) {
    if (!text) return [];
    return (text.replace(/\\n/g, ' ').split(/[\\s,，;；、]+/)).filter(Boolean);
  }

  // 渲染「最终生效热词」的 chips（继承 + 选中 + 手动，去重）
  function renderHwChips() {
    var box = document.getElementById('slirn-hw-chips');
    var sum = document.getElementById('slirn-hw-summary');
    if (!box) return;
    var inheritEl = document.getElementById('slirn-hw-inherit-all');
    var inherit = inheritEl && inheritEl.checked;
    var baseWords = inherit ? collectAllPublicHotwords() : collectPickedHotwords();
    var manualRaw = document.getElementById('slirn-hotwords-manual');
    var manual = parseManual(manualRaw ? manualRaw.value : '');

    var seen = {};
    var ordered = [];
    baseWords.forEach(function(w) {
      var k = w.trim();
      if (k && !seen[k]) { seen[k] = 1; ordered.push({w: k, src: inherit ? 'inherit' : 'pick'}); }
    });
    manual.forEach(function(w) {
      var k = w.trim();
      if (k && !seen[k]) { seen[k] = 1; ordered.push({w: k, src: 'manual'}); }
    });

    box.innerHTML = '';
    if (!ordered.length) {
      box.innerHTML = '<div class="slirn-hw-chip-empty">（还没有热词 — 勾选继承 / 从公共库选 / 或在下方输入）</div>';
    } else {
      ordered.forEach(function(o) {
        var chip = document.createElement('span');
        var cls = 'slirn-hw-chip src-' + o.src;
        chip.className = cls;
        chip.setAttribute('data-word', o.w);
        var label = o.src === 'inherit' ? '继承' : (o.src === 'pick' ? '已选' : '手动');
        var xBtn = (o.src === 'inherit')
          ? ''  // 继承来的词不可单独移除（要排除就关掉「继承」）
          : '<button class="slirn-hw-chip-x" data-action="hw-chip-remove" data-word="' + escapeAttr(o.w) + '" title="移除">✕</button>';
        chip.innerHTML = '<span class="slirn-hw-chip-label">' + label + '</span>' +
                         '<span class="slirn-hw-chip-text">' + escapeHtml(o.w) + '</span>' +
                         xBtn;
        box.appendChild(chip);
      });
    }
    if (sum) {
      sum.textContent = '共 ' + ordered.length + ' 个词' +
        (inherit ? ' · 来自继承全部公共库' : '');
    }
  }
  window.slirnRenderHwChips = renderHwChips;

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function(c) {
      return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c];
    });
  }
  function escapeAttr(s) {
    return escapeHtml(s);
  }

  // 切换继承勾选 → 控制选择器显示/隐藏，并刷新 chips
  function syncInheritVisibility() {
    var inheritEl = document.getElementById('slirn-hw-inherit-all');
    var wrap = document.getElementById('slirn-hw-picker-wrap');
    if (!inheritEl || !wrap) return;
    if (inheritEl.checked) {
      wrap.classList.add('slirn-inherit-mode');
    } else {
      wrap.classList.remove('slirn-inherit-mode');
    }
    renderHwChips();
  }
  window.slirnSyncInherit = syncInheritVisibility;

  // 初始化新建任务页的热词选择器（每次切换到该 tab 时调用一次）
  function initHotwordPicker() {
    var picker = document.getElementById('slirn-hw-picker');
    if (!picker) return;
    var inheritEl = document.getElementById('slirn-hw-inherit-all');
    var manualEl = document.getElementById('slirn-hotwords-manual');
    if (inheritEl && !inheritEl.dataset.slirnBound) {
      inheritEl.dataset.slirnBound = '1';
      inheritEl.addEventListener('change', syncInheritVisibility);
    }
    if (manualEl && !manualEl.dataset.slirnBound) {
      manualEl.dataset.slirnBound = '1';
      manualEl.addEventListener('input', renderHwChips);
    }
    // 点击 picker 单元切换选中（事件代理）
    if (!picker.dataset.slirnClickBound) {
      picker.dataset.slirnClickBound = '1';
      picker.addEventListener('click', function(ev) {
        var cell = ev.target.closest('.slirn-pick-cell');
        if (!cell) return;
        cell.classList.toggle('selected');
        renderHwChips();
      });
    }
    // chips 容器上的 ✕ 移除（事件代理）
    var chipsBox = document.getElementById('slirn-hw-chips');
    if (chipsBox && !chipsBox.dataset.slirnClickBound) {
      chipsBox.dataset.slirnClickBound = '1';
      chipsBox.addEventListener('click', function(ev) {
        var x = ev.target.closest('[data-action="hw-chip-remove"]');
        if (!x) return;
        ev.stopPropagation();
        var w = x.getAttribute('data-word') || '';
        // 找到对应 picker cell，去掉 .selected（picked 来源）
        var cell = picker.querySelector('.slirn-pick-cell[data-word="' + cssEscape(w) + '"]');
        if (cell) cell.classList.remove('selected');
        // 如果是手动来源，从 textarea 里去掉这个词
        var manualEl2 = document.getElementById('slirn-hotwords-manual');
        if (manualEl2 && manualEl2.value) {
          var tokens = manualEl2.value.split(/(\\s+)/);
          manualEl2.value = tokens.filter(function(t) { return t.trim() !== w; }).join('');
        }
        renderHwChips();
      });
    }
    syncInheritVisibility();
  }

  function cssEscape(s) {
    if (window.CSS && CSS.escape) return CSS.escape(s);
    return String(s).replace(/(["\\\\.#:>+~*\\[\\]()'])/g, '\\$1');
  }
  window.slirnInitHotwordPicker = initHotwordPicker;

  function secondsToHMS(s) {
    if (s == null || isNaN(s) || s < 0) return '';
    var ms = Math.round(s * 1000);
    var hh = Math.floor(ms / 3600000); ms = ms % 3600000;
    var mm = Math.floor(ms / 60000); ms = ms % 60000;
    var ss = Math.floor(ms / 1000); var mss = ms % 1000;
    return (hh<10?'0':'')+hh+':'+(mm<10?'0':'')+mm+':'+(ss<10?'0':'')+ss+'.'+(mss<10?'00':mss<100?'0':'')+mss;
  }
  window.slirnSecToHMS = secondsToHMS;

  // HH:MM:SS.mmm / HH:MM:SS → 秒数（用于客户端校验 start<end）
  // 返回 null 表示格式无效
  function hmsToSeconds(str) {
    if (!str) return null;
    var m = String(str).match(/^(\\d+):(\\d{1,2}):(\\d{1,2})(?:\\.(\\d{1,3}))?$/);
    if (!m) return null;
    var h = parseInt(m[1], 10);
    var mi = parseInt(m[2], 10);
    var se = parseInt(m[3], 10);
    var ms = m[4] ? parseInt(m[4].padEnd(3, '0'), 10) : 0;
    if (mi >= 60 || se >= 60) return null;
    return h * 3600 + mi * 60 + se + ms / 1000;
  }

  // ===== 当前选中的视频路径（来自文件选择）=====
  window.slirnSelectedFile = '';

  // ===== Tab 切换 — 纯客户端 CSS toggle，HTML 已在初始 DOM 中 =====
  var TAB_BUTTONS = {
    'goto-dashboard': 'slirn-tab-dashboard',
    'goto-tasks':     'slirn-tab-tasks',
    'goto-create':    'slirn-tab-create',
    'goto-hotwords':  'slirn-tab-hotwords',
  };
  var ALL_TABS = ['slirn-tab-dashboard','slirn-tab-tasks','slirn-tab-create','slirn-tab-hotwords','slirn-tab-workbench'];

  function showTab(targetCell) {
    ALL_TABS.forEach(function(id) {
      var el = document.getElementById(id);
      if (el) el.style.display = (id === targetCell) ? '' : 'none';
    });
    var detail = document.getElementById('slirn-tab-detail');
    if (detail) detail.style.display = 'none';
    // 切到「新建任务」时初始化热词选择器（刷新 chip 显示）；
    // 若上次是「编辑任务」占用了本页 → 重新拉取全新建页
    if (targetCell === 'slirn-tab-create') {
      try { window.slirnInitHotwordPicker && window.slirnInitHotwordPicker(); } catch (e) {}
      if (window.slirnEditTaskId) {
        window.slirnEditTaskId = null;
        postJSON(SLIRN_API + '/create_page', {}).then(function(r) {
          if (r && r.ok && r.html) {
            var c = document.getElementById('slirn-tab-create');
            if (c) c.innerHTML = r.html;
            initTaskEdit();  // 无 edit-state 时仅清状态
          }
        });
      }
    }
    window.scrollTo({top: 0, behavior: 'smooth'});
  }

  function refreshCell(cellId, html) {
    var cell = document.getElementById(cellId);
    if (!cell) return;
    if (html) cell.innerHTML = html;
  }

  // ===== 上传进度弹窗 =====
  function openUploadDialog(file) {
    var existing = document.getElementById('slirn-upload-modal');
    if (existing) existing.remove();
    var overlay = document.createElement('div');
    overlay.id = 'slirn-upload-modal';
    overlay.className = 'slirn-modal-overlay';
    var sizeMb = (file.size / 1024 / 1024).toFixed(2);
    overlay.innerHTML =
      '<div class="slirn-modal-card">' +
        '<div class="slirn-modal-title">⏳ 上传视频中…</div>' +
        '<div class="slirn-modal-filename">' + escapeHtml(file.name) + '（' + sizeMb + ' MB）</div>' +
        '<div class="slirn-progress-track"><div id="slirn-progress-bar" class="slirn-progress-bar"></div></div>' +
        '<div id="slirn-progress-text" class="slirn-progress-text">准备中…</div>' +
      '</div>';
    document.body.appendChild(overlay);
    return overlay;
  }
  function setUploadProgress(loaded, total) {
    var bar = document.getElementById('slirn-progress-bar');
    var text = document.getElementById('slirn-progress-text');
    if (!bar || !text) return;
    var pct = total > 0 ? Math.min(100, Math.round(loaded / total * 100)) : 0;
    bar.style.width = pct + '%';
    var sentMb = (loaded / 1024 / 1024).toFixed(2);
    var totalMb = (total / 1024 / 1024).toFixed(2);
    text.textContent = pct + '%  ·  ' + sentMb + ' / ' + totalMb + ' MB';
  }
  function closeUploadDialog() {
    var m = document.getElementById('slirn-upload-modal');
    if (m) m.remove();
  }
  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function(c) {
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
    });
  }

  // 上传文件到我们自己的端点 — 用 XHR 拿进度事件，弹窗一直显示
  // 不用 Gradio 内置 /gradio_api/upload：那个端点用 python_multipart，并发会截断大文件
  function uploadFile(file) {
    openUploadDialog(file);
    return new Promise(function(resolve, reject) {
      var fd = new FormData();
      fd.append('files', file);
      var xhr = new XMLHttpRequest();
      xhr.open('POST', SLIRN_API + '/upload_video');
      xhr.upload.onprogress = function(e) {
        if (e.lengthComputable) setUploadProgress(e.loaded, e.total);
        else setUploadProgress(0, file.size);
      };
      xhr.onload = function() {
        try {
          var j = JSON.parse(xhr.responseText);
          if (j && j.ok && j.path) {
            resolve(j.path);
          } else {
            reject(new Error((j && j.error) || ('Upload failed: ' + xhr.status)));
          }
        } catch (parseErr) {
          reject(new Error('Upload parse error: ' + parseErr));
        }
      };
      xhr.onerror = function() { reject(new Error('网络错误，上传失败')); };
      xhr.onabort = function() { reject(new Error('上传已取消')); };
      xhr.send(fd);
    });
  }

  // 业务回调：把 server 返回的 file_info / cut_done 渲染到 DOM
  function applyFileInfo(info) {
    if (!info) return;
    var el = document.getElementById('slirn-file-info-display');
    if (el) el.innerHTML = '<div class="slirn-status-msg">📁 ' + info.name + '（' + info.size_mb + ' MB · ' + info.duration + '）</div>';
    var card = document.getElementById('slirn-player-card');
    var v = document.getElementById('slirn-player');
    // Gradio 6 文件 API：/gradio_api/file=<urlencoded-path>
    if (card && v) { v.src = '/gradio_api/file=' + encodeURI(info.path); v.load(); card.style.display = ''; }
    // 把 seek 滑块的最大值设为视频时长（默认 HTML 写死 600，需要根据实际视频更新）
    var seek = document.getElementById('slirn-seek');
    if (seek && info.duration_seconds) {
      seek.max = info.duration_seconds;
      seek.value = 0;
    }
    // 更新时间显示 HH:MM:SS.mmm / HH:MM:SS.mmm
    var timeDisp = document.getElementById('slirn-time-display');
    if (timeDisp && info.duration) {
      timeDisp.textContent = '00:00:00.000 / ' + info.duration + '.000';
    }
  }
  function applyCutDone(info) {
    if (!info) return;
    var msg = document.getElementById('slirn-cut-msg');
    if (msg) msg.innerHTML = '<div class="slirn-status-msg">✅ 待剪辑视频已生成 ' + info.size_mb + ' MB</div>';
    var wrap = document.getElementById('slirn-cut-preview-wrap');
    var v = document.getElementById('slirn-cut-preview');
    if (wrap && v) { v.src = '/gradio_api/file=' + encodeURI(info.path); v.load(); wrap.style.display = ''; }
  }

  // ===== 字幕生成：后台轮询 + 详情刷新（REQ-20260915-001）=====
  function fmtElapsed(sec) {
    var m = Math.floor(sec / 60), s = sec % 60;
    return m > 0 ? (m + ' 分 ' + s + ' 秒') : (s + ' 秒');
  }

  function refreshDetail(tid) {
    postJSON(SLIRN_API + '/view_task', {task_id: tid}).then(function(r) {
      if (r && r.ok && r.html) {
        var d = document.getElementById('slirn-tab-detail');
        if (d) { d.innerHTML = r.html; d.style.display = ''; }
        bindSubPlayer();
        bindRevPlayer();
      }
    });
  }

  // ===== 编辑任务 / 剪辑工作台（REQ-20260915-003）=====
  function initTaskEdit() {
    var st = document.getElementById('slirn-edit-state');
    if (!st) { window.slirnEditTaskId = null; return; }
    window.slirnEditTaskId = st.getAttribute('data-task-id');
    window.slirnSelectedFile = st.getAttribute('data-video-path') || '';
    // 播放器：完整原视频 + 滑块全长
    var card = document.getElementById('slirn-player-card');
    var v = document.getElementById('slirn-player');
    var url = st.getAttribute('data-video-url');
    if (card && v && url) {
      v.src = url; v.load();
      card.style.display = '';
    }
    var durSec = parseFloat(st.getAttribute('data-duration-seconds')) || 0;
    var seek = document.getElementById('slirn-seek');
    if (seek && durSec > 0) { seek.max = durSec; seek.value = 0; }
    var td = document.getElementById('slirn-time-display');
    if (td && durSec > 0) {
      var ds = secondsToHMS(durSec);
      td.textContent = '00:00:00.000 / ' + ds + '.000';
    }
    try { window.slirnRenderHwChips && window.slirnRenderHwChips(); } catch (e) {}
  }

  function openWorkbench(tid) {
    postJSON(SLIRN_API + '/workbench', {task_id: tid}).then(function(r) {
      if (r && r.ok && r.html) {
        var w = document.getElementById('slirn-tab-workbench');
        if (!w) return;
        w.innerHTML = r.html;
        ALL_TABS.forEach(function(id) { var el = document.getElementById(id); if (el) el.style.display = 'none'; });
        var d = document.getElementById('slirn-tab-detail');
        if (d) d.style.display = 'none';
        w.style.display = '';
        window.scrollTo({top: 0, behavior: 'smooth'});
        bindSubPlayer();
        bindRevPlayer();
      } else if (r && r.error) {
        toast('❌ ' + r.error, 'error');
      }
    });
  }

  function switchWbPane(paneKey) {
    document.querySelectorAll('.slirn-wb-stage').forEach(function(s) {
      s.classList.toggle('active', s.getAttribute('data-pane') === paneKey);
    });
    document.querySelectorAll('.slirn-wb-pane').forEach(function(p) {
      p.style.display = (p.id === 'slirn-wb-pane-' + paneKey) ? '' : 'none';
    });
  }

  var subPollTimer = null;
  function startSubPolling(tid) {
    if (subPollTimer) { clearInterval(subPollTimer); subPollTimer = null; }
    var update = function() {
      postJSON(SLIRN_API + '/subtitle_status', {task_id: tid}).then(function(r) {
        if (!r || !r.ok) return;
        var j = r.job || {};
        var el = document.getElementById('slirn-asr-status');
        if (j.state === 'running') {
          if (el) {
            el.style.display = '';
            el.dataset.state = 'running';
            el.innerHTML = '⏳ ' + escapeHtml(j.stage || '处理中') + ' · 已耗时 ' + fmtElapsed(j.elapsed_s || 0);
          }
        } else {
          if (subPollTimer) { clearInterval(subPollTimer); subPollTimer = null; }
          if (j.state === 'done') {
            toast('✅ 字幕生成完成：' + (j.segments_count || 0) + ' 段');
            // 字幕完成时用户可能在工作台或详情页 — 刷新所在视图
            if (el && el.closest && el.closest('#slirn-tab-workbench')) { openWorkbench(tid); }
            else { refreshDetail(tid); }
          } else if (j.state === 'error') {
            if (el) {
              el.style.display = '';
              el.dataset.state = 'error';
              el.innerHTML = '❌ ' + escapeHtml(j.error || '生成失败');
            }
            toast('❌ 字幕生成失败', 'error');
          }
        }
      });
    };
    update();
    subPollTimer = setInterval(update, 2000);
  }

  // ===== 字幕播放器：定位播放 + 播放高亮跟随 =====
  function playSubAt(tid, startMs) {
    var wrap = document.getElementById('slirn-sub-player-wrap');
    var v = document.getElementById('slirn-sub-player');
    if (!v) { toast('❌ 播放器未就绪', 'error'); return; }
    if (wrap) wrap.style.display = '';
    if (!v.src) { v.src = SLIRN_API + '/video/' + encodeURIComponent(tid); v.load(); }
    var go = function() {
      try { v.currentTime = (startMs || 0) / 1000; } catch (err) {}
      var p = v.play();
      if (p && p.catch) p.catch(function() {});
    };
    if (v.readyState >= 1) go();
    else v.addEventListener('loadedmetadata', go, {once: true});
  }

  function bindSubPlayer() {
    var v = document.getElementById('slirn-sub-player');
    var list = document.getElementById('slirn-sub-list');
    if (v && list && !v.dataset.bound) {
      v.dataset.bound = '1';
      var rows = Array.prototype.slice.call(list.querySelectorAll('.slirn-sub-row'));
      var lastHit = -1;
      var setActive = function(idx) {
        for (var i = 0; i < rows.length; i++) rows[i].classList.toggle('active', i === idx);
        if (idx >= 0 && rows[idx] && rows[idx].scrollIntoView) rows[idx].scrollIntoView({block: 'nearest'});
      };
      v.addEventListener('timeupdate', function() {
        var tms = v.currentTime * 1000, hit = -1;
        for (var i = 0; i < rows.length; i++) {
          var s0 = parseInt(rows[i].getAttribute('data-start-ms'), 10) || 0;
          var e0 = parseInt(rows[i].getAttribute('data-end-ms'), 10) || 0;
          if (tms >= s0 && tms < e0) { hit = i; break; }
          if (s0 > tms) break;  // 行按时间有序，后面不可能命中
        }
        if (hit === -1 && lastHit >= 0) {
          // 两个字幕段之间的静音间隙：保持上一行高亮（校对视角更连续）
          var eh = parseInt(rows[lastHit].getAttribute('data-end-ms'), 10) || 0;
          var nh = (lastHit + 1 < rows.length)
            ? (parseInt(rows[lastHit + 1].getAttribute('data-start-ms'), 10) || 0)
            : Infinity;
          if (tms >= eh && tms < nh) hit = lastHit;
        }
        lastHit = hit;
        setActive(hit);
      });
    }
    // 详情（重新）打开时，若 job 还在跑 → 恢复轮询
    var st = document.getElementById('slirn-asr-status');
    if (st && st.dataset.taskId && st.dataset.state === 'running') startSubPolling(st.dataset.taskId);
  }

  // ===== 字幕修订：轮询 + 播放器（REQ-20260915-005，与字幕区同模式、独立 id）=====
  var revPollTimer = null;
  function startRevPolling(tid) {
    if (revPollTimer) { clearInterval(revPollTimer); revPollTimer = null; }
    var update = function() {
      postJSON(SLIRN_API + '/revise_status', {task_id: tid}).then(function(r) {
        if (!r || !r.ok) return;
        var j = r.job || {};
        var el = document.getElementById('slirn-rev-status');
        if (j.state === 'running') {
          if (el) {
            el.style.display = '';
            el.dataset.state = 'running';
            el.innerHTML = '⏳ ' + escapeHtml(j.stage || '分析中') + ' · 已耗时 ' + fmtElapsed(j.elapsed_s || 0);
          }
        } else {
          if (revPollTimer) { clearInterval(revPollTimer); revPollTimer = null; }
          if (j.state === 'done') {
            toast('✅ 大模型分析完成：' + (j.entries_count || 0) + ' 条建议');
            openWorkbench(tid);  // 刷新面板（建议列表 + 阶段态）
          } else if (j.state === 'error') {
            if (el) {
              el.style.display = '';
              el.dataset.state = 'error';
              el.innerHTML = '❌ ' + escapeHtml(j.error || '分析失败');
            }
            toast('❌ 大模型分析失败', 'error');
          }
        }
      });
    };
    update();
    revPollTimer = setInterval(update, 2000);
  }

  function playRevAt(tid, startMs) {
    var wrap = document.getElementById('slirn-rev-player-wrap');
    var v = document.getElementById('slirn-rev-player');
    if (!v) { toast('❌ 播放器未就绪', 'error'); return; }
    if (wrap) wrap.style.display = '';
    if (!v.src) { v.src = SLIRN_API + '/video/' + encodeURIComponent(tid); v.load(); }
    var go = function() {
      try { v.currentTime = (startMs || 0) / 1000; } catch (err) {}
      var p = v.play();
      if (p && p.catch) p.catch(function() {});
    };
    if (v.readyState >= 1) go();
    else v.addEventListener('loadedmetadata', go, {once: true});
  }

  function bindRevPlayer() {
    var v = document.getElementById('slirn-rev-player');
    var list = document.getElementById('slirn-rev-list');
    if (v && list && !v.dataset.bound) {
      v.dataset.bound = '1';
      var rows = Array.prototype.slice.call(list.querySelectorAll('.slirn-rev-row'));
      var lastHit = -1;
      var setActive = function(idx) {
        for (var i = 0; i < rows.length; i++) rows[i].classList.toggle('active', i === idx);
        if (idx >= 0 && rows[idx] && rows[idx].scrollIntoView) rows[idx].scrollIntoView({block: 'nearest'});
      };
      v.addEventListener('timeupdate', function() {
        var tms = v.currentTime * 1000, hit = -1;
        for (var i = 0; i < rows.length; i++) {
          var s0 = parseInt(rows[i].getAttribute('data-start-ms'), 10) || 0;
          var e0 = parseInt(rows[i].getAttribute('data-end-ms'), 10) || 0;
          if (tms >= s0 && tms < e0) { hit = i; break; }
          if (s0 > tms) break;
        }
        if (hit === -1 && lastHit >= 0) {
          var eh = parseInt(rows[lastHit].getAttribute('data-end-ms'), 10) || 0;
          var nh = (lastHit + 1 < rows.length)
            ? (parseInt(rows[lastHit + 1].getAttribute('data-start-ms'), 10) || 0)
            : Infinity;
          if (tms >= eh && tms < nh) hit = lastHit;
        }
        lastHit = hit;
        setActive(hit);
      });
    }
    // 工作台（重新）打开时，若 job 还在跑 → 恢复轮询
    var st = document.getElementById('slirn-rev-status');
    if (st && st.dataset.taskId && st.dataset.state === 'running') startRevPolling(st.dataset.taskId);
  }

  function handleResp(resp, refreshCellId) {
    if (!resp) { toast('❌ 无响应', 'error'); return; }
    if (!resp.ok) { toast('❌ ' + (resp.error || '操作失败'), 'error'); return; }
    if (resp.html) refreshCell(refreshCellId, resp.html);
    if (resp.file_info) applyFileInfo(resp.file_info);
    if (resp.cut_done) applyCutDone(resp.cut_done);
    if (resp.toast) toast(resp.toast);
  }

  // ===== 全局事件委托 =====
  document.addEventListener('click', function(e) {
    // 字幕行点击（行自身无 data-action，先于 data-action 委托处理）
    var subRow = e.target.closest('.slirn-sub-row');
    if (subRow) {
      e.preventDefault();
      var tidS = subRow.getAttribute('data-task-id') || '';
      var startMs = parseInt(subRow.getAttribute('data-start-ms'), 10) || 0;
      playSubAt(tidS, startMs);
      return;
    }

    // 修订行点击定位播放（select/input/button 上的点击不触发）
    var revRow = e.target.closest('.slirn-rev-row');
    if (revRow && !e.target.closest('select, input, button, a')) {
      e.preventDefault();
      var tidR = revRow.getAttribute('data-task-id') || '';
      var startMsR = parseInt(revRow.getAttribute('data-start-ms'), 10) || 0;
      playRevAt(tidR, startMsR);
      return;
    }

    var target = e.target.closest('[data-action]');
    if (!target) return;
    var action = target.getAttribute('data-action');
    e.preventDefault();

    // Tab 切换
    if (TAB_BUTTONS[action]) {
      showTab(TAB_BUTTONS[action]);
      return;
    }

    if (action === 'refresh-tasks') {
      postJSON(SLIRN_API + '/refresh_tasks', {}).then(function(r) { handleResp(r, 'slirn-tab-tasks'); });
    }
    else if (action === 'view-task') {
      var tid = target.getAttribute('data-task-id') || '';
      postJSON(SLIRN_API + '/view_task', {task_id: tid}).then(function(r) {
        if (r && r.html) {
          var d = document.getElementById('slirn-tab-detail');
          if (d) { d.innerHTML = r.html; d.style.display = ''; }
          bindSubPlayer();
          bindRevPlayer();
        }
      });
    }
    else if (action === 'gen-subtitle') {
      var tidG = target.getAttribute('data-task-id') || '';
      postJSON(SLIRN_API + '/gen_subtitle', {task_id: tidG}).then(function(r) {
        if (r && r.ok) {
          toast(r.toast || '已开始生成');
          var el = document.getElementById('slirn-asr-status');
          if (el) { el.dataset.state = 'running'; el.style.display = ''; el.innerHTML = '⏳ 已提交…'; }
          startSubPolling(tidG);
        } else if (r && r.error) {
          toast('❌ ' + r.error, 'error');
        }
      });
    }
    else if (action === 'play-segment') {
      var tidP = target.getAttribute('data-task-id') || '';
      playSubAt(tidP, 0);
    }
    else if (action === 'play-rev-video') {
      playRevAt(target.getAttribute('data-task-id') || '', 0);
    }
    else if (action === 'revise-subtitle') {
      var tidV = target.getAttribute('data-task-id') || '';
      var payloadV = {task_id: tidV};
      if (target.getAttribute('data-has-revision') === '1') {
        if (!window.confirm('重新分析将覆盖现有建议，并重置全部手动决策。确定继续？')) return;
        payloadV.force = true;
      }
      postJSON(SLIRN_API + '/revise_subtitle', payloadV).then(function(r) {
        if (r && r.ok) {
          toast(r.toast || '已开始分析');
          var el = document.getElementById('slirn-rev-status');
          if (el) { el.dataset.state = 'running'; el.style.display = ''; el.innerHTML = '⏳ 已提交…'; }
          startRevPolling(tidV);
        } else if (r && r.error) {
          toast('❌ ' + r.error, 'error');
        }
      });
    }
    else if (action === 'save-revision') {
      var tidW = target.getAttribute('data-task-id') || '';
      var decisions = [];
      document.querySelectorAll('#slirn-rev-list .slirn-rev-row').forEach(function(row) {
        var sel = row.querySelector('.slirn-rev-select');
        var note = row.querySelector('.slirn-rev-note-input');
        if (sel) {
          decisions.push({
            i: parseInt(sel.getAttribute('data-i'), 10),
            decision: sel.value,
            user_note: note ? note.value : '',
          });
        }
      });
      if (!decisions.length) { toast('❌ 无可保存的决策行', 'error'); return; }
      postJSON(SLIRN_API + '/save_revision', {task_id: tidW, decisions: decisions})
        .then(function(r) {
          if (r && r.ok) {
            toast(r.toast || '已保存');
            openWorkbench(tidW);  // 刷新统计/阶段态
          } else if (r && r.error) {
            toast('❌ ' + r.error, 'error');
          }
        });
    }
    else if (action === 'close-detail') {
      var d = document.getElementById('slirn-tab-detail');
      if (d) d.style.display = 'none';
    }
    else if (action === 'delete-task') {
      var tid2 = target.getAttribute('data-task-id') || '';
      postJSON(SLIRN_API + '/delete_task', {task_id: tid2}).then(function(r) {
        handleResp(r, 'slirn-tab-tasks');
        var d = document.getElementById('slirn-tab-detail');
        if (d) d.style.display = 'none';
      });
    }
    else if (action === 'edit-task') {
      var tidE = target.getAttribute('data-task-id') || '';
      postJSON(SLIRN_API + '/edit_task', {task_id: tidE}).then(function(r) {
        if (r && r.ok && r.html) {
          var c = document.getElementById('slirn-tab-create');
          if (c) { c.innerHTML = r.html; c.style.display = ''; }
          ALL_TABS.forEach(function(id) {
            if (id !== 'slirn-tab-create') { var el = document.getElementById(id); if (el) el.style.display = 'none'; }
          });
          var dE = document.getElementById('slirn-tab-detail');
          if (dE) dE.style.display = 'none';
          initTaskEdit();
        } else if (r && r.error) {
          toast('❌ ' + r.error, 'error');
        }
      });
    }
    else if (action === 'open-workbench') {
      openWorkbench(target.getAttribute('data-task-id') || '');
    }
    else if (action === 'wb-stage') {
      switchWbPane(target.getAttribute('data-pane') || '');
    }
    else if (action === 'update-task') {
      var tidU = target.getAttribute('data-task-id') || window.slirnEditTaskId || '';
      var sU = getInput('slirn-start-box'), eU = getInput('slirn-end-box');
      var nU = getInput('slirn-task-name');
      var manualU = getInput('slirn-hotwords-manual');
      var inheritElU = document.getElementById('slirn-hw-inherit-all');
      var inheritU = inheritElU ? !!inheritElU.checked : false;
      var pickedU = collectPickedHotwords();
      postJSON(SLIRN_API + '/update_task', {
        task_id: tidU, start: sU, end: eU, name: nU,
        inherit_public: inheritU, picked_words: pickedU, manual_words: manualU,
      }).then(function(r) {
        if (r && r.ok) {
          toast(r.toast || '✅ 已保存');
          handleResp(r, 'slirn-tab-tasks');
          showTab('slirn-tab-tasks');
        } else if (r && r.error) {
          toast('❌ ' + r.error, 'error');
        }
      });
    }
    else if (action === 'set-start') {
      var seek = document.getElementById('slirn-seek');
      if (seek) document.getElementById('slirn-start-box').value = secondsToHMS(parseFloat(seek.value));
    }
    else if (action === 'set-end') {
      var seek2 = document.getElementById('slirn-seek');
      if (seek2) document.getElementById('slirn-end-box').value = secondsToHMS(parseFloat(seek2.value));
    }
    else if (action === 'cut-preview') {
      var s = getInput('slirn-start-box'), e2 = getInput('slirn-end-box');
      // 客户端校验：先看时间格式 + 开始<结束 再发请求
      var sSec = hmsToSeconds(s);
      var eSec = hmsToSeconds(e2);
      if (sSec == null) { toast('❌ 开始时间格式错误（应为 HH:MM:SS.mmm）', 'error'); return; }
      if (eSec == null) { toast('❌ 结束时间格式错误（应为 HH:MM:SS.mmm）', 'error'); return; }
      if (sSec >= eSec) { toast('❌ 开始时间必须小于结束时间（' + s + ' ≥ ' + e2 + '）', 'error'); return; }
      postJSON(SLIRN_API + '/cut_preview', {path: window.slirnSelectedFile, start: s, end: e2})
        .then(function(r) { handleResp(r, 'slirn-tab-create'); });
    }
    else if (action === 'create-task') {
      var s1 = getInput('slirn-start-box'), e1 = getInput('slirn-end-box');
      var n = getInput('slirn-task-name');
      var manual = getInput('slirn-hotwords-manual');
      var inheritEl = document.getElementById('slirn-hw-inherit-all');
      var inherit = inheritEl ? !!inheritEl.checked : false;
      var picked = collectPickedHotwords();
      postJSON(SLIRN_API + '/create_task', {
        path: window.slirnSelectedFile, start: s1, end: e1, name: n,
        inherit_public: inherit,
        picked_words: picked,
        manual_words: manual,
      }).then(function(r) {
        if (r && r.ok) {
          handleResp(r, 'slirn-tab-tasks');
          showTab('slirn-tab-tasks');
        }
      });
    }
    else if (action === 'cancel-create') {
      postJSON(SLIRN_API + '/cancel_create', {}).then(function(r) {
        handleResp(r, 'slirn-tab-tasks');
        showTab('slirn-tab-tasks');
      });
    }
    else if (action === 'hw-add') {
      var w = getInput('slirn-hw-word'), cat = getInput('slirn-hw-category');
      postJSON(SLIRN_API + '/hw_add', {word: w, category: cat})
        .then(function(r) {
          if (r && r.ok) {
            // 清空词输入框（保留分类方便连续添加同一分类的词）
            var ta = document.getElementById('slirn-hw-word');
            if (ta) ta.value = '';
          }
          handleResp(r, 'slirn-tab-hotwords');
        });
    }
    else if (action === 'hw-delete-one') {
      // 阻止冒泡到 cell 上的潜在 click 监听
      if (e) { e.stopPropagation(); }
      var wd = target.getAttribute('data-word') || '';
      if (!wd) return;
      if (!confirm('删除热词「' + wd + '」？')) return;
      postJSON(SLIRN_API + '/hw_remove', {word: wd})
        .then(function(r) { handleResp(r, 'slirn-tab-hotwords'); });
    }
    else if (action === 'hw-cat-select-all' || action === 'hw-cat-invert' || action === 'hw-cat-delete') {
      // 找当前按钮所在的分类 section
      var section = target.closest('.slirn-category-section');
      if (!section) return;
      var cells = section.querySelectorAll('.slirn-hotword-cell:not(.empty)');
      if (action === 'hw-cat-select-all') {
        cells.forEach(function(c) { c.classList.add('selected'); });
        updateDeleteCount(section);
      } else if (action === 'hw-cat-invert') {
        cells.forEach(function(c) { c.classList.toggle('selected'); });
        updateDeleteCount(section);
      } else if (action === 'hw-cat-delete') {
        var selected = section.querySelectorAll('.slirn-hotword-cell.selected');
        var words = [];
        selected.forEach(function(c) {
          var ww = c.getAttribute('data-word');
          if (ww) words.push(ww);
        });
        if (!words.length) {
          toast('请先用 全选 / 反选 勾选要删除的词', 'error');
          return;
        }
        if (!confirm('确认删除 ' + words.length + ' 个词？')) return;
        postJSON(SLIRN_API + '/hw_remove', {words: words})
          .then(function(r) { handleResp(r, 'slirn-tab-hotwords'); });
      }
    }
    else if (action === 'trigger-file') {
      // 动态创建一个临时 file input — 每次新创建才能避开 value 缓存（重选同一文件也能触发 change）
      var tmp = document.createElement('input');
      tmp.type = 'file';
      tmp.accept = '.mp4,.avi,.mkv,.mov,.webm,.ts,.mpeg,.m4v,.flv,.wmv,video/*';
      tmp.style.display = 'none';
      tmp.addEventListener('change', function() {
        var files = tmp.files;
        document.body.removeChild(tmp);
        if (!files || !files.length) return;
        var file = files[0];
        uploadFile(file).then(function(path) {
          closeUploadDialog();
          window.slirnSelectedFile = path;
          toast('✅ 上传完成，正在解析视频…');
          return postJSON(SLIRN_API + '/file_selected', {path: path});
        }).then(function(r) {
          closeUploadDialog();
          handleResp(r, 'slirn-tab-create');
        }).catch(function(err) {
          closeUploadDialog();
          toast('❌ 上传失败: ' + err, 'error');
        });
      });
      document.body.appendChild(tmp);
      tmp.click();
    }
  });

  // ===== 拖拽上传 — dropzone 区域支持把视频文件拖进来 =====
  function slirnDragUpload(file) {
    uploadFile(file).then(function(path) {
      closeUploadDialog();
      window.slirnSelectedFile = path;
      toast('✅ 上传完成，正在解析视频…');
      return postJSON(SLIRN_API + '/file_selected', {path: path});
    }).then(function(r) {
      closeUploadDialog();
      handleResp(r, 'slirn-tab-create');
    }).catch(function(err) {
      closeUploadDialog();
      toast('❌ 上传失败: ' + err, 'error');
    });
  }
  document.addEventListener('dragover', function(e) {
    var dz = e.target && e.target.closest && e.target.closest('.slirn-dropzone');
    if (!dz) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
    dz.classList.add('drag-over');
  });
  document.addEventListener('dragleave', function(e) {
    var dz = e.target && e.target.closest && e.target.closest('.slirn-dropzone');
    if (dz) {
      dz.classList.remove('drag-over');
    }
  });
  document.addEventListener('drop', function(e) {
    var dz = e.target && e.target.closest && e.target.closest('.slirn-dropzone');
    if (!dz) return;
    e.preventDefault();
    dz.classList.remove('drag-over');
    var files = e.dataTransfer && e.dataTransfer.files;
    if (!files || !files.length) {
      toast('⚠️ 未识别到文件，请拖入视频文件', 'warning');
      return;
    }
    var file = files[0];
    if (!file.type.startsWith('video/') && !/\\.(mp4|avi|mkv|mov|webm|ts|mpeg|m4v|flv|wmv)$/i.test(file.name)) {
      toast('⚠️ 请拖入视频文件（mp4/avi/mkv/...）', 'warning');
      return;
    }
    slirnDragUpload(file);
  });

  // ===== 文件选择 — 改由 trigger-file 处理器动态创建 input 并就地监听 change =====
  // 不再用全局 change 监听，避免拖拽上传时双触发

  // ===== Slider / Video 同步 =====
  document.addEventListener('input', function(e) {
    if (e.target && e.target.id === 'slirn-seek') {
      var v = document.getElementById('slirn-player');
      if (v) v.currentTime = parseFloat(e.target.value);
    }
  });
  document.addEventListener('timeupdate', function(e) {
    if (e.target && e.target.id === 'slirn-player') {
      var v = e.target;
      var s = document.getElementById('slirn-seek');
      var d = document.getElementById('slirn-time-display');
      if (s) s.value = v.currentTime;
      if (d) d.textContent = secondsToHMS(v.currentTime) + ' / ' + secondsToHMS(v.duration);
      if (s && v.duration) s.max = v.duration;
    }
  });

  console.log('[slirn] router initialized (custom /slirn/api mode)');
})();
</script>
"""


# ============================================================
# CSS 注入
# ============================================================

def _read_css() -> str:
    """读取 home.css — 用 launch(css=...) 注入，因为 Gradio 6.x 会用 _deprecated_css 覆盖 app.css。"""
    css_path = Path(__file__).parent / "static" / "home.css"
    return css_path.read_text(encoding="utf-8")


def _build_head() -> str:
    """构造 head 注入：主题切换 JS + 自定义事件路由 JS。"""
    theme_js = """
    <script>
    (function() {
      function applyTheme(t) {
        document.documentElement.setAttribute('data-theme', t);
        try { localStorage.setItem('slirn-theme', t); } catch(e) {}
      }
      window.slirnToggleTheme = function() {
        var cur = document.documentElement.getAttribute('data-theme');
        applyTheme(cur === 'dark' ? 'light' : 'dark');
      };
      var saved = null;
      try { saved = localStorage.getItem('slirn-theme'); } catch(e) {}
      if (saved) applyTheme(saved);
    })();
    </script>
    """
    return theme_js + ROUTER_JS


def _inject_css_and_js(app: gr.Blocks) -> None:
    """老接口兼容 — 仍设置 app.css 但 launch() 会用 _deprecated_css 覆盖，外部需读 slirn_home_static_css。

    不再设置 app.head — Gradio 6.17.3 的 Blocks.head 不会渲染到页面 <head>。
    JS 注入已改到 gr.HTML 组件的 head= 参数（见 build_app 里的 gr.HTML(full_html, head=...) 调用）。
    """
    css_path = Path(__file__).parent / "static" / "home.css"
    app.css = css_path.read_text(encoding="utf-8")


# 暴露给 launcher 直接读
slirn_home_static_css = lambda: _read_css()  # noqa: E731
slirn_home_static_head = lambda: _build_head()  # noqa: E731


# ============================================================
# 主构造
# ============================================================

def build_app(repo_root: Path | None = None) -> gr.Blocks:
    if repo_root is None:
        from slirn_home.paths import find_slirn_standalone_root
        repo_root = find_slirn_standalone_root()

    mgr = TaskManager(repo_root)

    app = gr.Blocks(title="Slirn — 自定义首页")
    _inject_css_and_js(app)
    # 放宽 Gradio 默认上传大小限制到 10 GB（原默认很小，会截断大视频）
    app.max_file_size = 10 * 1024 * 1024 * 1024  # bytes

    initial_stats = _stats(mgr)

    # 单个 gr.HTML 包裹全部 UI — JS 通过 ID 控制各 tab 的可见性
    full_html = f'''
    <div class="slirn-topbar">
        <div class="slirn-logo">
            <div class="slirn-logo-icon">🎬</div>
            <span>Slirn Studio</span>
        </div>
        <div class="slirn-topbar-actions">
            <button class="slirn-btn-icon" onclick="window.slirnToggleTheme && window.slirnToggleTheme()" aria-label="切换主题" title="切换主题">🌓</button>
            <a class="slirn-btn" href="http://127.0.0.1:7860/" target="_blank">🚀 上游首页</a>
        </div>
    </div>

    <div class="slirn-hero">
        <div class="slirn-hero-text">
            <h1>欢迎回来 👋</h1>
            <p>今天有 <strong>{initial_stats["draft"]}</strong> 个草稿任务待处理 · 仓库 <code>{_esc(repo_root)}</code></p>
        </div>
        <div class="slirn-hero-actions">
            <button class="slirn-btn" data-action="goto-tasks">📋 任务列表</button>
            <button class="slirn-btn" data-action="goto-hotwords">📚 热词库</button>
            <button class="slirn-btn slirn-btn-primary" data-action="goto-create">➕ 新建任务</button>
        </div>
    </div>

    <div id="slirn-tab-dashboard" class="slirn-tab-content">{_render_dashboard(mgr, repo_root)}</div>
    <div id="slirn-tab-tasks" class="slirn-tab-content" style="display:none;">{_render_task_list(mgr)}</div>
    <div id="slirn-tab-create" class="slirn-tab-content" style="display:none;">{_render_create_task(repo_root)}</div>
    <div id="slirn-tab-hotwords" class="slirn-tab-content" style="display:none;">{_render_hotword_lib(repo_root)}</div>
    <div id="slirn-tab-detail" class="slirn-tab-content" style="display:none;"></div>
    <div id="slirn-tab-workbench" class="slirn-tab-content" style="display:none;"></div>

    <div class="slirn-footer">Slirn v0.2 · 仓库 <code>{_esc(repo_root)}</code></div>
    '''

    with app:
        # Gradio 6.17.3 的 Blocks.head / app.head 不会渲染到页面 <head>（template 静态），
        # 所以 JS 必须挂在 gr.HTML 组件的 head= 参数上 — 这个会被前端运行时
        # 用 DOMParser 解析并把 <script> 真实 appendChild 到 document.head。
        # ROUTER_JS + theme_js 合并成一个 head 字符串
        gr.HTML(full_html, head=_build_head())

        # 必要的隐藏 inputs（Gradio 6 不会把这些渲染成组件，需要用作 API 参数传递）
        hidden_start = gr.Textbox(value="", elem_id="hidden-start-input", elem_classes=["slirn-hidden-trigger"])
        hidden_end = gr.Textbox(value="", elem_id="hidden-end-input", elem_classes=["slirn-hidden-trigger"])
        hidden_name = gr.Textbox(value="", elem_id="hidden-name-input", elem_classes=["slirn-hidden-trigger"])
        hidden_hotwords = gr.Textbox(value="", elem_id="hidden-hotwords-input", elem_classes=["slirn-hidden-trigger"])
        hidden_hw_word = gr.Textbox(value="", elem_id="hidden-hw-word", elem_classes=["slirn-hidden-trigger"])
        hidden_hw_cat = gr.Textbox(value="", elem_id="hidden-hw-category", elem_classes=["slirn-hidden-trigger"])
        hidden_file = gr.File(elem_id="slirn-file-input-real", elem_classes=["slirn-hidden-trigger"])
        hidden_task_id = gr.Textbox(value="", elem_id="hidden-task-id-input", elem_classes=["slirn-hidden-trigger"])

        # ====================================================
        # 每个按钮只返回一个字符串 — JS fetch 拿到的 data[0] = HTML
        # 闭包保留 mgr / repo_root / hidden_file.value 等状态
        # ====================================================

        def go_to_dashboard() -> str: return _render_dashboard(mgr, repo_root)
        def go_to_tasks() -> str: return _render_task_list(mgr)
        def go_to_create() -> str: return _render_create_task(repo_root)
        def go_to_hotwords() -> str: return _render_hotword_lib(repo_root)

        btn_goto_dashboard = gr.Button("dashboard", elem_classes=["slirn-hidden-trigger"])
        btn_goto_tasks = gr.Button("tasks", elem_classes=["slirn-hidden-trigger"])
        btn_goto_create = gr.Button("create", elem_classes=["slirn-hidden-trigger"])
        btn_goto_hotwords = gr.Button("hotwords", elem_classes=["slirn-hidden-trigger"])
        btn_goto_dashboard.click(fn=go_to_dashboard, inputs=[], outputs=[])
        btn_goto_tasks.click(fn=go_to_tasks, inputs=[], outputs=[])
        btn_goto_create.click(fn=go_to_create, inputs=[], outputs=[])
        btn_goto_hotwords.click(fn=go_to_hotwords, inputs=[])

        def on_refresh_tasks() -> str: return _render_task_list(mgr)
        btn_refresh_tasks = gr.Button("refresh-tasks", elem_classes=["slirn-hidden-trigger"])
        btn_refresh_tasks.click(fn=on_refresh_tasks, inputs=[], outputs=[])

        def on_view_task(task_id: str) -> str:
            if not task_id.strip():
                return '<div class="slirn-empty"><div class="slirn-empty-icon">🔍</div><div class="slirn-empty-text">请指定任务 ID</div></div>'
            return _render_task_detail(task_id.strip(), mgr)
        btn_view_task = gr.Button("view", elem_classes=["slirn-hidden-trigger"])
        btn_view_task.click(fn=on_view_task, inputs=[hidden_task_id], outputs=[])

        def on_delete_task(task_id: str) -> str:
            if task_id.strip():
                try:
                    mgr.delete(task_id.strip())
                except Exception:
                    pass
            return _render_task_list(mgr)
        btn_delete_task = gr.Button("delete", elem_classes=["slirn-hidden-trigger"])
        btn_delete_task.click(fn=on_delete_task, inputs=[hidden_task_id], outputs=[])

        def on_file_selected() -> str:
            fp = hidden_file.value
            if not fp:
                return _render_create_task(repo_root)
            from tasklib.video import get_video_duration
            p = Path(fp)
            if not p.exists():
                return _render_create_task(repo_root)
            dur = get_video_duration(p)
            if dur:
                mm, ss = divmod(int(dur), 60)
                hh, mm = divmod(mm, 60)
                dur_str = f"{hh:02d}:{mm:02d}:{ss:02d}"
            else:
                dur_str = "未知"
            size_mb = p.stat().st_size / 1024 / 1024
            js_callback = (
                f'<script>window.slirnFileLoaded && window.slirnFileLoaded('
                f'"{_esc(p.name)}", "{dur_str}", {size_mb:.1f}, '
                f'"{_esc(str(p).replace(chr(92), "/"))}");</script>'
            )
            return js_callback + _render_create_task(repo_root)
        btn_on_file_selected = gr.Button("file-selected", elem_classes=["slirn-hidden-trigger"])
        btn_on_file_selected.click(fn=on_file_selected, inputs=[], outputs=[])

        def on_cut_preview(start: str, end: str) -> str:
            fp = hidden_file.value
            if not fp or not start.strip() or not end.strip():
                return _render_create_task(repo_root)
            import uuid

            from tasklib.exceptions import VideoProcessingError
            from tasklib.video import cut_video
            src = Path(fp)
            temp_dir = repo_root / ".temp"
            temp_dir.mkdir(exist_ok=True)
            dst = temp_dir / f"cut_{uuid.uuid4().hex[:8]}_{src.stem}{src.suffix}"
            try:
                cut_video(src, dst, start.strip(), end.strip())
            except VideoProcessingError:
                return _render_create_task(repo_root)
            size_mb = dst.stat().st_size / 1024 / 1024 if dst.exists() else 0
            js_callback = (
                f'<script>window.slirnCutDone && window.slirnCutDone('
                f'"{_esc(str(dst).replace(chr(92), "/"))}", {size_mb:.1f});</script>'
            )
            return js_callback + _render_create_task(repo_root)
        btn_cut_preview = gr.Button("cut", elem_classes=["slirn-hidden-trigger"])
        btn_cut_preview.click(fn=on_cut_preview, inputs=[hidden_start, hidden_end], outputs=[])

        def on_create_task(start: str, end: str, name: str, hotwords_text: str) -> str:
            fp = hidden_file.value
            if not fp:
                return _render_create_task(repo_root)
            src = Path(fp)
            final_name = name.strip() or src.stem
            hotwords = [w.strip() for w in hotwords_text.replace("\n", " ").split() if w.strip()]
            segment = None
            segment_temp = None
            seg_filename = None
            if start.strip() and end.strip():
                temp_dir = repo_root / ".temp"
                if temp_dir.exists():
                    cuts = sorted(
                        temp_dir.glob(f"cut_*_{src.stem}{src.suffix}"),
                        key=lambda p: p.stat().st_mtime, reverse=True,
                    )
                    if cuts:
                        segment_temp = cuts[0]
                        from tasklib.time_utils import segment_filename
                        ext = src.suffix.lstrip(".") or "mp4"
                        seg_filename = segment_filename(src.stem, start.strip(), end.strip(), ext)
                        from tasklib.models import TimeSegment
                        segment = TimeSegment(start=start.strip(), end=end.strip(), path=Path("placeholder"))
            try:
                task = mgr.create(name=final_name, original_video=src, segment=segment, hotwords=hotwords)
            except Exception:
                return _render_create_task(repo_root)
            if segment_temp and segment_temp.exists():
                import shutil
                final_path = repo_root / "tasks" / task.task_id / "raw_input" / seg_filename
                try:
                    final_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(segment_temp), str(final_path))
                except Exception:
                    pass
            return _render_task_list(mgr)
        btn_create_task = gr.Button("create-task", elem_classes=["slirn-hidden-trigger"])
        btn_create_task.click(fn=on_create_task, inputs=[hidden_start, hidden_end, hidden_name, hidden_hotwords], outputs=[])

        def on_cancel_create() -> str: return _render_task_list(mgr)
        btn_cancel_create = gr.Button("cancel-create", elem_classes=["slirn-hidden-trigger"])
        btn_cancel_create.click(fn=on_cancel_create, inputs=[], outputs=[])

        def on_hw_add(word: str, category: str) -> str:
            hwlib = HotwordLibrary(repo_root)
            try:
                hwlib.add(word.strip(), category=category.strip() or "默认")
            except Exception:
                pass
            return _render_hotword_lib(repo_root)
        btn_hw_add = gr.Button("hw-add", elem_classes=["slirn-hidden-trigger"])
        btn_hw_add.click(fn=on_hw_add, inputs=[hidden_hw_word, hidden_hw_cat], outputs=[])

    # 注册自定义 FastAPI 路由 — 绕过 Gradio 队列（POST 同步返回 JSON）
    _register_slirn_api(app, mgr, repo_root)

    # 注册自定义 FastAPI 路由 — 绕过 Gradio 队列（POST 同步返回 JSON）
    _register_slirn_api(app, mgr, repo_root)

    return app


def _register_slirn_api(app: gr.Blocks, mgr: TaskManager, repo_root: Path) -> None:
    """为 Gradio app 注册自定义 /slirn/api/* 路由 — 同步返回 JSON，绕过 Gradio 队列。

    路由返回格式：{"ok": true, "html": "...", "toast": "...", "file_info": {...}, "cut_done": {...}}
    JS 端只需 fetch + .then(r => r.json()) 即可拿到结果。

    注意：不能直接用 `req: fastapi.Request` 作为参数 — Gradio 6 的 App.create_app 重建后的
    FastAPI 实例似乎对 Request 类型识别有 bug，会把 Request 当作 query 参数。
    改用 Pydantic 模型 body 解析，避开 Request 类型注入。
    """
    import hashlib as _hashlib
    import os as _os
    import re as _re
    import tempfile as _tempfile
    import uuid as _uuid
    from typing import Optional

    from fastapi import Body
    from fastapi.responses import JSONResponse

    # 直接从 starlette.requests 导入 Request，避免 Gradio 6 rebuild 后
    # FastAPI 把 fastapi.Request 类型误识别为 query 参数的 bug
    from starlette.requests import Request as _StarletteRequest

    def _ok(html: str = "", **extra) -> JSONResponse:
        return JSONResponse({"ok": True, "html": html, **extra})

    def _err(msg: str) -> JSONResponse:
        return JSONResponse({"ok": False, "error": msg})

    # 视频上传端点 — 自己解析 multipart + 写盘
    # 不用 Gradio /gradio_api/upload：那个端点用 python_multipart.MultipartParser，
    # 并发场景下会截断大文件（实测 5 个并发 415MB 上传有 3 个被截断到 0–52%）。
    # 不用 FastAPI UploadFile + python_multipart：同一个库的同一个 bug。
    # 也不用 Request 参数：Gradio 6 rebuild 后 FastAPI 把 Request 误判为 query param。
    # 解法：直接挂载一个原始 ASGI handler（不经过 FastAPI 路由的依赖注入），
    # 自己读 body、解析 multipart、写盘、返回 JSON。这样无论并发与否都稳定。
    import json as _json

    from starlette.responses import Response as _StarletteResponse

    async def _upload_video_asgi(scope, receive, send):
        if scope["type"] != "http":
            return
        # 提取 boundary
        ctype_full = ""
        for k, v in scope.get("headers", []):
            if k == b"content-type":
                ctype_full = v.decode("latin-1", errors="replace")
                break
        m = _re.search(r'boundary="?([^";]+)"?', ctype_full)
        if not m:
            resp = _err("缺少 boundary")
            await resp(scope, receive, send)
            return
        # 收集所有 body chunks 到一个 bytes — 简单且可靠（415MB 在现代机器上无压力）
        # 不依赖流式 chunk 边界，避免复杂的 tail 窗口管理 bug
        body_chunks = []
        bytes_received = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"") or b""
            if chunk:
                body_chunks.append(chunk)
                bytes_received += len(chunk)
            if not message.get("more_body", False):
                break
        if bytes_received == 0:
            resp = _err("收到空 body")
            await resp(scope, receive, send)
            return
        body_bytes = b"".join(body_chunks)

        # 解析 multipart — 找 opening boundary 行 + headers + data + closing boundary
        # 格式: --boundary\r\nheaders\r\n\r\ndata\r\n--boundary--\r\n
        boundary = b"--" + m.group(1).encode("ascii")
        # 找第一个 \r\n\r\n（headers 结束标记）
        hdr_end = body_bytes.find(b"\r\n\r\n")
        if hdr_end < 0:
            resp = _err("multipart 头不完整")
            await resp(scope, receive, send)
            return
        # 解析 filename
        header_block = body_bytes[:hdr_end]
        cdm = _re.search(
            rb'Content-Disposition:[^\r\n]*filename\*?=(?:UTF-8\'\'|")?([^\r\n";]+)',
            header_block,
            _re.IGNORECASE,
        )
        raw_name = ""
        if cdm:
            fn_raw = cdm.group(1).strip().strip(b'"').decode("utf-8", errors="replace")
            if "%" in fn_raw:
                from urllib.parse import unquote
                fn_raw = unquote(fn_raw)
            raw_name = Path(fn_raw).name
        if not raw_name:
            raw_name = f"upload_{_uuid.uuid4().hex[:8]}.bin"
        # data 起点
        data_start = hdr_end + 4
        # 找结束 boundary —— 必须从 data_start 之后开始找
        # 优先 \r\n--boundary--（最终终止）；兜底 \r\n--boundary（中间 part 终止）
        end_marker_full = b"\r\n" + boundary + b"--"
        end_marker_part = b"\r\n" + boundary
        end_idx = body_bytes.find(end_marker_full, data_start)
        if end_idx < 0:
            end_idx = body_bytes.find(end_marker_part, data_start)
            if end_idx < 0:
                resp = _err("找不到结束 boundary")
                await resp(scope, receive, send)
                return
        data_bytes = body_bytes[data_start:end_idx]
        if not data_bytes:
            resp = _err("data 为空")
            await resp(scope, receive, send)
            return

        # 计算 SHA + 写到 Gradio 缓存目录
        sha = _hashlib.sha256()
        sha.update(data_bytes)
        sha_hex = sha.hexdigest()
        safe_name = _re.sub(r'[\\/:\0]', "_", raw_name)
        gradio_cache = Path(_tempfile.gettempdir()) / "gradio" / sha_hex
        gradio_cache.mkdir(parents=True, exist_ok=True)
        final_path = gradio_cache / safe_name
        try:
            with open(final_path, "wb") as f:
                f.write(data_bytes)
        except Exception as e:
            resp = _err(f"写文件失败: {e}")
            await resp(scope, receive, send)
            return
        body_json = _json.dumps(
            {"ok": True, "path": str(final_path).replace("\\", "/"), "size_mb": round(len(data_bytes) / 1024 / 1024, 2)},
            ensure_ascii=False,
        )
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": body_json.encode("utf-8")})

    # 把 ASGI handler 直接挂到 FastAPI 路由（不依赖 Request 参数）
    # 注意：starlette.routing.Route 把函数视为 request_response 风格（只接受 request 一个参数），
    # 把类视为 ASGI 风格（接受 scope/receive/send）。我们的 _upload_video_asgi 是 raw ASGI，
    # 所以包成一个类来强制 Starlette 用 ASGI 模式调用。
    class _UploadVideoASGIApp:
        async def __call__(self, scope, receive, send):
            await _upload_video_asgi(scope, receive, send)

    # 把 ASGI 类挂上去，Starlette 看到是类就当 ASGI app 处理（传 scope, receive, send）
    app.app.router.add_route(
        "/slirn/api/upload_video",
        _UploadVideoASGIApp(),
        methods=["POST"],
    )

    @app.app.post("/slirn/api/hw_add")
    async def hw_add(body: dict = Body(default_factory=dict)):
        # 支持三种入参形式：
        #   1) {"word": "人工智能", "category": "科技"}            单个
        #   2) {"word": "人工智能 机器学习\nNLP", "category": ...} 一个字段里多个，分隔符任意
        #   3) {"words": ["人工智能", "机器学习"], "category": ...} 显式数组
        cat = (body.get("category") or "").strip() or "默认"
        words_raw = body.get("words")
        if isinstance(words_raw, list):
            tokens = [str(w).strip() for w in words_raw]
        else:
            raw = body.get("word") or ""
            # 切分：换行 / 制表 / 逗号 / 分号 / 全角逗号 / 空白
            tokens = _re.split(r"[\s,，;；、]+", str(raw))
            tokens = [t.strip() for t in tokens]
        tokens = [t for t in tokens if t]
        if not tokens:
            return _err("词不能为空")
        try:
            hwlib = HotwordLibrary(repo_root)
            added, dup = [], []
            for w in tokens:
                try:
                    ok = hwlib.add(w, category=cat)
                    if ok:
                        added.append(w)
                    else:
                        dup.append(w)  # 已存在（add 返回 False）
                except Exception:
                    dup.append(w)  # 抛异常（如空字符串）
        except Exception as e:
            return _err(f"添加失败: {e}")
        if added and not dup:
            msg = f"✅ 已添加 {len(added)} 个词"
        elif added and dup:
            msg = f"✅ 新增 {len(added)} 个，跳过重复 {len(dup)} 个"
        else:
            return _err(f"全部为重复词：{', '.join(dup[:5])}")
        return _ok(_render_hotword_lib(repo_root), toast=msg)

    @app.app.post("/slirn/api/hw_remove")
    async def hw_remove(body: dict = Body(default_factory=dict)):
        # 支持单删 ({word}) 与批量删 ({words:[...]})
        words_raw = body.get("words")
        if isinstance(words_raw, list):
            targets = [str(w).strip() for w in words_raw if str(w or "").strip()]
        else:
            word = (body.get("word") or "").strip()
            targets = [word] if word else []
        if not targets:
            return _err("请提供要删除的词")
        try:
            hwlib = HotwordLibrary(repo_root)
            removed = []
            missing = []
            for w in targets:
                try:
                    if hwlib.remove(w):
                        removed.append(w)
                    else:
                        missing.append(w)
                except Exception:
                    missing.append(w)
        except Exception as e:
            return _err(f"删除失败: {e}")
        n = len(removed)
        if n == 0:
            return _err("没有词被删除（全部不存在）")
        if n == 1:
            toast = f"✅ 已删除：{removed[0]}"
        else:
            toast = f"✅ 已删除 {n} 个词"
        if missing:
            toast += f" · 跳过 {len(missing)} 个不存在的词"
        return _ok(_render_hotword_lib(repo_root), toast=toast)

    @app.app.post("/slirn/api/refresh_tasks")
    async def refresh_tasks():
        return _ok(_render_task_list(mgr))

    @app.app.post("/slirn/api/view_task")
    async def view_task(body: dict = Body(default_factory=dict)):
        tid = (body.get("task_id") or "").strip()
        if not tid:
            return _ok('<div class="slirn-empty"><div class="slirn-empty-icon">🔍</div><div class="slirn-empty-text">请指定任务 ID</div></div>')
        return _ok(_render_task_detail(tid, mgr))

    @app.app.post("/slirn/api/create_page")
    async def create_page():
        """全新建任务页 HTML（编辑模式退出后还原用）。"""
        return _ok(_render_create_task(repo_root))

    @app.app.post("/slirn/api/edit_task")
    async def edit_task(body: dict = Body(default_factory=dict)):
        """编辑任务 — 返回新建页（编辑模式预填）HTML（REQ-20260915-003）。"""
        tid = (body.get("task_id") or "").strip()
        if not tid:
            return _err("缺少 task_id")
        try:
            t = mgr.get(tid)
        except Exception as e:  # noqa: BLE001
            return _err(f"任务不存在: {e}")
        return _ok(_render_create_task(repo_root, edit=t))

    @app.app.post("/slirn/api/update_task")
    async def update_task(body: dict = Body(default_factory=dict)):
        """保存编辑：任务名 / 截取时间（变更自动重截取）/ 热词三来源（REQ-20260915-003）。"""
        tid = (body.get("task_id") or "").strip()
        name = (body.get("name") or "").strip()
        start = (body.get("start") or "").strip()
        end = (body.get("end") or "").strip()
        inherit_public = bool(body.get("inherit_public"))
        picked_raw = body.get("picked_words")
        manual_text = (body.get("manual_words") or "").strip()
        if not tid:
            return _err("缺少 task_id")
        try:
            t = mgr.get(tid)
        except Exception as e:  # noqa: BLE001
            return _err(f"任务不存在: {e}")

        # ---- 热词三来源重合并（与 create_task 同规则） ----
        seen: set[str] = set()
        ordered: list[str] = []
        word_sources: dict[str, str] = {}
        if inherit_public:
            try:
                hwlib = HotwordLibrary(repo_root)
                for _cat, ws in hwlib.list_grouped().items():
                    for w in ws:
                        w = w.strip()
                        if w and w not in seen:
                            seen.add(w)
                            ordered.append(w)
                            word_sources[w] = "inherit"
            except Exception:  # noqa: BLE001
                pass
        if isinstance(picked_raw, list):
            for w in picked_raw:
                w = str(w or "").strip()
                if w and w not in seen:
                    seen.add(w)
                    ordered.append(w)
                    word_sources[w] = "pick"
        for w in manual_text.replace("\n", " ").split():
            w = w.strip()
            if w and w not in seen:
                seen.add(w)
                ordered.append(w)
                word_sources[w] = "manual"

        # ---- 截取段时间：变更 → 从原视频重截取 ----
        from tasklib.exceptions import InvalidTimeRangeError
        from tasklib.models import TimeSegment
        from tasklib.time_utils import parse_time, segment_filename

        old_seg = t.segment
        seg_changed = False
        new_segment: TimeSegment | None = None
        toast = "✅ 任务已更新"
        if start and end:
            try:
                if parse_time(start).total_seconds() >= parse_time(end).total_seconds():
                    return _err(f"开始时间 ({start}) 必须小于结束时间 ({end})")
            except ValueError as e:
                return _err(f"时间格式错误: {e}")
            if old_seg is None or old_seg.start != start or old_seg.end != end:
                src = t.original_video_source
                if not src.exists():
                    return _err("原视频文件缺失，无法重截取")
                ext = src.suffix.lstrip(".") or "mp4"
                final_path = mgr.tasks_dir / tid / "raw_input" / segment_filename(src.stem, start, end, ext)
                try:
                    from tasklib.video import cut_video
                    cut_video(src, final_path, start, end)
                except Exception as e:  # noqa: BLE001
                    return _err(f"重截取失败: {e}")
                new_segment = TimeSegment(start=start, end=end, path=final_path)
                seg_changed = True
                toast = "✅ 任务已更新（截取段已重截）"
        elif old_seg is not None:
            # 时间清空 → 解除截取段（走完整原视频）
            seg_changed = True
            new_segment = None

        # 已生成字幕 & 截取范围变化 → 字幕已过期，提示重新生成
        had_subtitle = (mgr.tasks_dir / tid / "outputs" / _asr.SUBTITLE_JSON).exists()

        try:
            mgr.update_task(
                tid, name=name or None,
                segment=new_segment, segment_changed=seg_changed,
                hotwords=ordered,
                inherit_public=inherit_public,
                hotword_sources=word_sources if word_sources else None,
            )
        except Exception as e:  # noqa: BLE001
            return _err(f"更新失败: {e}")

        # 清理被替换的旧截取文件
        if seg_changed and old_seg is not None and old_seg.path != (new_segment.path if new_segment else None):
            try:
                if old_seg.path.exists():
                    old_seg.path.unlink()
            except OSError:
                pass

        if seg_changed and had_subtitle:
            toast += " · 截取范围已变，建议重新生成字幕"
        return _ok(_render_task_list(mgr), toast=toast, task_id=tid)

    @app.app.post("/slirn/api/workbench")
    async def workbench(body: dict = Body(default_factory=dict)):
        """剪辑工作台 HTML（REQ-20260915-003）。"""
        tid = (body.get("task_id") or "").strip()
        if not tid:
            return _err("缺少 task_id")
        return _ok(_render_workbench(tid, mgr))

    @app.app.post("/slirn/api/delete_task")
    async def delete_task(body: dict = Body(default_factory=dict)):
        tid = (body.get("task_id") or "").strip()
        if tid:
            try:
                mgr.delete(tid)
            except Exception:
                pass
        return _ok(_render_task_list(mgr), toast="✅ 已删除")

    # ---------- 字幕生成（REQ-20260915-001）----------

    from slirn_home import asr_service as _asr

    # 任务视频流式播放：支持 Range（拖动进度条必需），不经 Gradio 文件白名单
    # （任务视频在 slirn-standalone/tasks/ 下，/gradio_api/file= 只服务 %TEMP%/gradio/）
    # 同样挂原始 ASGI handler，绕开 Gradio 6 rebuild 后的 FastAPI 参数注入 bug
    _VIDEO_MIME = {
        ".mp4": "video/mp4", ".webm": "video/webm", ".mkv": "video/x-matroska",
        ".mov": "video/quicktime", ".avi": "video/x-msvideo", ".ts": "video/mp2t",
        ".mpeg": "video/mpeg",
    }

    async def _task_video_asgi(scope, receive, send):
        if scope["type"] != "http":
            return
        from urllib.parse import parse_qs as _parse_qs
        from urllib.parse import unquote as _unquote

        tid = _unquote(scope["path"].rsplit("/", 1)[-1])
        # ?src=original → 强制服务完整原视频（编辑页滑全片定位用，REQ-20260915-003）
        force_original = _parse_qs(scope.get("query_string", b"").decode("latin-1")).get("src", [""])[0] == "original"
        video: Path | None = None
        try:
            t = mgr.get(tid)
            if force_original and t.original_video_source.exists():
                video = t.original_video_source
            else:
                video, _label = _resolve_task_video(t)
        except Exception:  # noqa: BLE001
            video = None
        if video is None:
            await send({"type": "http.response.start", "status": 404,
                        "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body",
                        "body": b'{"ok": false, "error": "task video missing"}'})
            return

        size = video.stat().st_size
        mime = _VIDEO_MIME.get(video.suffix.lower(), "application/octet-stream")
        range_hdr = ""
        for k, v in scope.get("headers", []):
            if k == b"range":
                range_hdr = v.decode("latin-1", errors="replace")
                break

        start, end, status = 0, size - 1, 200
        headers = [
            (b"content-type", mime.encode()),
            (b"accept-ranges", b"bytes"),
            (b"cache-control", b"no-cache"),
        ]
        if range_hdr:
            m2 = _re.search(r"bytes=(\d*)-(\d*)", range_hdr)
            if m2:
                s0, e0 = m2.group(1), m2.group(2)
                start = int(s0) if s0 else 0
                end = min(int(e0), size - 1) if e0 else size - 1
                if start > end or start >= size:
                    await send({"type": "http.response.start", "status": 416,
                                "headers": [(b"content-range", f"bytes */{size}".encode())]})
                    await send({"type": "http.response.body", "body": b""})
                    return
                status = 206
                headers.append((b"content-range", f"bytes {start}-{end}/{size}".encode()))
        headers.append((b"content-length", str(end - start + 1).encode()))
        await send({"type": "http.response.start", "status": status, "headers": headers})

        # 本地文件按块直读（每块 ≤1MB，SSD 上亚毫秒级，不引入线程池复杂度）
        remaining = end - start + 1
        with open(video, "rb") as f:
            f.seek(start)
            while remaining > 0:
                chunk = f.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                await send({"type": "http.response.body", "body": chunk,
                            "more_body": remaining > 0})
        if remaining > 0:  # 文件被并发缩短等异常 — 提前收尾
            await send({"type": "http.response.body", "body": b"", "more_body": False})

    class _TaskVideoASGIApp:
        async def __call__(self, scope, receive, send):
            await _task_video_asgi(scope, receive, send)

    app.app.router.add_route(
        "/slirn/api/video/{task_id:path}", _TaskVideoASGIApp(), methods=["GET"],
    )

    @app.app.post("/slirn/api/gen_subtitle")
    async def gen_subtitle(body: dict = Body(default_factory=dict)):
        tid = (body.get("task_id") or "").strip()
        if not tid:
            return _err("缺少 task_id")
        try:
            t = mgr.get(tid)
        except Exception as e:  # noqa: BLE001
            return _err(f"任务不存在: {e}")
        _ensure_segment_file(t)  # 自愈：截取文件丢失时从原视频重截
        video, _label = _resolve_task_video(t)
        if video is None:
            return _err("任务视频文件缺失（截取段和原视频都不在磁盘上）")
        if not _asr.has_audio_track(video):
            return _err("视频没有音频轨，无法生成字幕")

        hotwords: list[str] = []
        try:
            if t.hotwords_path.exists():
                hotwords = [w for w in t.hotwords_path.read_text(encoding="utf-8").split() if w]
        except Exception:  # noqa: BLE001
            pass

        source, base_off_ms = "original", 0
        if t.segment and t.segment.path.exists():
            source = "segment"
            from tasklib.time_utils import parse_time
            base_off_ms = int(parse_time(t.segment.start).total_seconds() * 1000)

        outputs_dir = mgr.tasks_dir / tid / "outputs"

        def _on_success(segs: list[dict]) -> None:
            mgr.update_status(tid, TaskStatus.SUBTITLE_GENERATED)

        started = _asr.start_job(
            tid, video, hotwords, outputs_dir,
            source=source, base_offset_ms=base_off_ms, on_success=_on_success,
        )
        if not started:
            return _ok("", toast="⏳ 该任务已在生成中，请等待完成")
        return _ok("", toast="🎙 字幕生成已开始（后台运行，可离开本页）",
                   job={"state": "running", "stage": "加载模型"})

    @app.app.post("/slirn/api/subtitle_status")
    async def subtitle_status(body: dict = Body(default_factory=dict)):
        import time as _time

        tid = (body.get("task_id") or "").strip()
        if not tid:
            return _err("缺少 task_id")
        j = _asr.job_status(tid)
        if j is None:
            # 无内存 job：要么从未生成，要么服务器重启前已完成（结果在磁盘上）
            meta = _asr.load_subtitle(mgr.tasks_dir / tid / "outputs")
            if meta and meta.get("segments"):
                return _ok("", job={"state": "done", "segments_count": len(meta["segments"])})
            return _ok("", job={"state": "idle"})
        j = dict(j)
        j["elapsed_s"] = int((j.get("finished_at") or _time.time()) - j["started_at"])
        return _ok("", job=j)

    @app.app.post("/slirn/api/revise_subtitle")
    async def revise_subtitle(body: dict = Body(default_factory=dict)):
        """启动大模型字幕分析（REQ-20260915-005）。已有建议时须 force（前端二次确认）。"""
        from slirn_home import revision_service

        tid = (body.get("task_id") or "").strip()
        if not tid:
            return _err("缺少 task_id")
        try:
            t = mgr.get(tid)
        except Exception as e:  # noqa: BLE001
            return _err(f"任务不存在: {e}")
        outputs_dir = mgr.tasks_dir / tid / "outputs"
        sub_meta = _asr.load_subtitle(outputs_dir)
        if not (sub_meta and sub_meta.get("segments")):
            return _err("请先生成字幕（上一阶段）再进行修订分析")
        if revision_service.load_revision(outputs_dir) and not body.get("force"):
            return _err("已存在修订建议 — 重新分析将覆盖建议并重置全部决策，请确认后重试")

        hotwords: list[str] = []
        try:
            if t.hotwords_path.exists():
                hotwords = [w for w in t.hotwords_path.read_text(encoding="utf-8").split() if w]
        except Exception:  # noqa: BLE001
            pass

        started = revision_service.start_job(
            tid, sub_meta["segments"], t.name, hotwords, outputs_dir,
        )
        if not started:
            return _ok("", toast="⏳ 该任务已在分析中，请等待完成")
        return _ok("", toast="🤖 大模型分析已开始（后台运行，可离开本页）",
                   job={"state": "running", "stage": "准备提示词"})

    @app.app.post("/slirn/api/revise_status")
    async def revise_status(body: dict = Body(default_factory=dict)):
        import time as _time

        from slirn_home import revision_service

        tid = (body.get("task_id") or "").strip()
        if not tid:
            return _err("缺少 task_id")
        j = revision_service.job_status(tid)
        if j is None:
            rev = revision_service.load_revision(mgr.tasks_dir / tid / "outputs")
            if rev and rev.get("entries"):
                return _ok("", job={"state": "done", "entries_count": len(rev["entries"])})
            return _ok("", job={"state": "idle"})
        j = dict(j)
        j["elapsed_s"] = int((j.get("finished_at") or _time.time()) - j["started_at"])
        return _ok("", job=j)

    @app.app.post("/slirn/api/save_revision")
    async def save_revision(body: dict = Body(default_factory=dict)):
        """保存用户逐条决策；全部决策完成 → 状态推进 SUBTITLE_REVIEWED。"""
        import json
        import time as _time

        from slirn_home import revision_service

        tid = (body.get("task_id") or "").strip()
        decisions = body.get("decisions")
        if not tid:
            return _err("缺少 task_id")
        if not isinstance(decisions, list):
            return _err("缺少 decisions 列表")
        try:
            mgr.get(tid)
        except Exception as e:  # noqa: BLE001
            return _err(f"任务不存在: {e}")
        outputs_dir = mgr.tasks_dir / tid / "outputs"
        rev = revision_service.load_revision(outputs_dir)
        if not (rev and rev.get("entries")):
            return _err("尚无修订建议，请先运行大模型分析")
        rev, applied = revision_service.merge_decisions(rev, decisions)
        rev["saved_at"] = _time.strftime("%Y-%m-%dT%H:%M:%S")
        (outputs_dir / revision_service.REVISION_JSON).write_text(
            json.dumps(rev, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        total = len(rev["entries"])
        decided = sum(1 for e in rev["entries"] if e.get("decision") != "pending")
        finished = revision_service.all_decided(rev)
        if finished:
            try:
                mgr.update_status(tid, TaskStatus.SUBTITLE_REVIEWED)
            except Exception as e:  # noqa: BLE001
                revision_service.log.warning("更新任务 %s 状态失败: %s", tid, e)
        remaining = total - decided
        toast = f"✅ 已保存（生效 {applied} 条 · 已决策 {decided}/{total}）"
        if finished:
            toast += " · 字幕修订完成，可进入粗剪"
        elif remaining:
            toast += f" · 还剩 {remaining} 条未决策"
        return _ok("", toast=toast, decided=decided, total=total, finished=finished)

    @app.app.post("/slirn/api/file_selected")
    async def file_selected(body: dict = Body(default_factory=dict)):
        fp = (body.get("path") or "").strip()
        if not fp:
            return _ok(_render_create_task(repo_root))
        from tasklib.video import get_video_duration
        p = Path(fp)
        if not p.exists():
            return _err("文件不存在")
        dur = get_video_duration(p)
        if dur:
            mm, ss = divmod(int(dur), 60)
            hh, mm = divmod(mm, 60)
            dur_str = f"{hh:02d}:{mm:02d}:{ss:02d}"
        else:
            dur_str = "未知"
        size_mb = p.stat().st_size / 1024 / 1024
        return _ok(
            _render_create_task(repo_root),
            file_info={
                "name": p.name,
                "duration": dur_str,
                "duration_seconds": float(dur) if dur else 0.0,
                "size_mb": round(size_mb, 1),
                "path": str(p).replace("\\", "/"),
            },
        )

    @app.app.post("/slirn/api/cut_preview")
    async def cut_preview(body: dict = Body(default_factory=dict)):
        fp = (body.get("path") or "").strip()
        start = (body.get("start") or "").strip()
        end = (body.get("end") or "").strip()
        if not fp or not start or not end:
            return _err("需要路径 + 开始时间 + 结束时间")
        # 服务端兜底校验：开始 < 结束（解析失败也拒）
        from tasklib.exceptions import InvalidTimeRangeError
        from tasklib.time_utils import parse_time
        try:
            s_sec = parse_time(start).total_seconds()
            e_sec = parse_time(end).total_seconds()
        except ValueError as e:
            return _err(f"时间格式错误: {e}")
        if s_sec >= e_sec:
            return _err(f"开始时间 ({start}) 必须小于结束时间 ({end})")
        # 用 slirn-standalone tasklib.video.cut_video（流式 ffmpeg 实现）
        from tasklib.exceptions import VideoProcessingError
        from tasklib.video import cut_video
        src = Path(fp)
        if not src.exists():
            return _err("视频文件不存在")
        # 截取写到 Gradio 缓存目录结构（%TEMP%/gradio/<sha256>/<name>）
        # 这样 /gradio_api/file=<encoded-path> 才能服务（Gradio 白名单仅缓存目录内的文件，
        # 其它路径返回 403 — 这就是「上传的视频没有声音」的根因）
        import hashlib as _hashlib_mod
        import tempfile as _tempfile_mod
        temp_dir = repo_root / ".temp"
        temp_dir.mkdir(exist_ok=True)
        dst = temp_dir / f"cut_{_uuid.uuid4().hex[:8]}_{src.stem}{src.suffix}"
        try:
            cut_video(src, dst, start, end)
        except VideoProcessingError as e:
            return _err(f"截取失败: {e}")
        if not dst.exists():
            return _err("截取后未生成文件")
        # 把文件移到 Gradio 缓存目录（保留原文件名），并按文件内容 hash 分桶
        with open(dst, "rb") as _f:
            _data_bytes = _f.read()
        sha_hex = _hashlib_mod.sha256(_data_bytes).hexdigest()
        gradio_cache = Path(_tempfile_mod.gettempdir()) / "gradio" / sha_hex
        gradio_cache.mkdir(parents=True, exist_ok=True)
        final_dst = gradio_cache / dst.name
        try:
            final_dst.write_bytes(_data_bytes)
            dst.unlink()  # 清理 .temp/ 里的副本
        except Exception:
            # 写缓存失败 — 退回到 .temp/ 路径（前端会 403，但至少服务端不出错）
            final_dst = dst
        size_mb = final_dst.stat().st_size / 1024 / 1024
        # 不返回 html — 不要替换整个 tab（保留原文件预览 + 滑块 + 时间输入）
        return _ok(
            "",
            toast=f"✅ 待剪辑视频已生成 {round(size_mb, 1)} MB",
            cut_done={
                "path": str(final_dst).replace("\\", "/"),
                "size_mb": round(size_mb, 1),
            },
        )

    @app.app.post("/slirn/api/create_task")
    async def create_task(body: dict = Body(default_factory=dict)):
        fp = (body.get("path") or "").strip()
        start = (body.get("start") or "").strip()
        end = (body.get("end") or "").strip()
        name = (body.get("name") or "").strip()
        # 三种来源：① 全部继承（true/false）② 选中（数组）③ 手动（文本）
        inherit_public = bool(body.get("inherit_public"))
        picked_raw = body.get("picked_words")
        manual_text = (body.get("manual_words") or body.get("hotwords") or "").strip()

        if not fp:
            return _err("未选择视频")
        src = Path(fp)
        if not src.exists():
            return _err("视频文件不存在")
        final_name = name or src.stem

        # 组合最终热词（按：全部继承 → 选中 → 手动 去重保序）
        # REQ-20260915-002：同时记录每个词的来源（详情页回显 chips 标签用）
        seen = set()
        ordered = []
        word_sources: dict[str, str] = {}
        if inherit_public:
            try:
                hwlib = HotwordLibrary(repo_root)
                for _cat, ws in hwlib.list_grouped().items():
                    for w in ws:
                        w = w.strip()
                        if w and w not in seen:
                            seen.add(w)
                            ordered.append(w)
                            word_sources[w] = "inherit"
            except Exception:
                pass
        if isinstance(picked_raw, list):
            for w in picked_raw:
                w = str(w or "").strip()
                if w and w not in seen:
                    seen.add(w)
                    ordered.append(w)
                    word_sources[w] = "pick"
        for w in manual_text.replace("\n", " ").split():
            w = w.strip()
            if w and w not in seen:
                seen.add(w)
                ordered.append(w)
                word_sources[w] = "manual"
        hotwords = ordered
        segment = None
        seg_filename = None
        if start and end:
            # 找最近的截取文件：.temp/（旧路径）+ %TEMP%/gradio/<sha>/（cut_preview 现把
            # 文件放这里过 /gradio_api/file= 白名单）。两处都找，取最新。
            import tempfile as _tempfile_mod

            candidates: list[Path] = []
            temp_dir = repo_root / ".temp"
            if temp_dir.exists():
                candidates.extend(temp_dir.glob(f"cut_*_{src.stem}{src.suffix}"))
            gradio_root = Path(_tempfile_mod.gettempdir()) / "gradio"
            if gradio_root.exists():
                candidates.extend(gradio_root.glob(f"*/cut_*_{src.stem}{src.suffix}"))
            candidates = [c for c in candidates if c.is_file()]
            if candidates:
                segment_temp = max(candidates, key=lambda p: p.stat().st_mtime)
                from tasklib.time_utils import segment_filename
                ext = src.suffix.lstrip(".") or "mp4"
                seg_filename = segment_filename(src.stem, start, end, ext)
                from tasklib.models import TimeSegment
                segment = TimeSegment(start=start, end=end, path=Path("placeholder"))
            else:
                segment_temp = None
        try:
            task = mgr.create(
                name=final_name, original_video=src, segment=segment, hotwords=hotwords,
                inherit_public=inherit_public,
                hotword_sources=word_sources if word_sources else None,
            )
        except Exception as e:
            return _err(f"创建失败: {e}")
        if segment_temp is not None and segment_temp.exists() and seg_filename:
            import shutil
            final_path = repo_root / "tasks" / task.task_id / "raw_input" / seg_filename
            try:
                final_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(segment_temp), str(final_path))
            except Exception:
                pass
        return _ok(_render_task_list(mgr), toast="✅ 任务已创建", task_id=task.task_id)

    @app.app.post("/slirn/api/cancel_create")
    async def cancel_create():
        return _ok(_render_task_list(mgr))
