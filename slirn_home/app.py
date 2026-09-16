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


def _render_rigor_picker() -> str:
    """分析严谨性级别单选卡（REQ-20260916-003，用户必选）— 高/中/低 + 自定义 四档。

    每档带定位标题、说明、例子（revision_service.RIGOR_LEVELS），
    让操作者不看文档也能体感三档差别；不预选（必须主动选择），
    上次选择由前端 localStorage 预填（applyRevRigorState）。
    自定义档（REQ-20260916-007）：默认底稿（高档完整提示词）可编辑；
    REQ-20260916-009：高/中/低三档完整提示词随 data-prompt-* 下发，
    编辑区提供三个底稿按钮 — 点击即载入/恢复该档（默认底稿仍为高档）；
    textarea 服务端置空，由 JS 预填 localStorage 草稿（slirnRevCustomPrompt）或高档底稿。
    """
    from slirn_home import revision_service
    from slirn_home.revision_service import RIGOR_LEVELS

    cards = ""
    for key in ("high", "medium", "low"):
        cfg = RIGOR_LEVELS[key]
        cards += (
            f'<label class="slirn-rigor-card" title="{_esc(cfg["desc"])}">'
            f'<input type="radio" name="slirn-rev-rigor" value="{key}" />'
            f'<span class="slirn-rigor-card-title">{_esc(cfg["badge"])} · {_esc(cfg["title"])}</span>'
            f'<span class="slirn-rigor-card-desc">{_esc(cfg["desc"])}</span>'
            f'<span class="slirn-rigor-card-example">例：{_esc(cfg["example"])}</span>'
            f'</label>'
        )
    cards += (
        '<label class="slirn-rigor-card" title="可在高/中/低任一底稿基础上修改修订标准，'
        '适合有自己一套规则的老手">'
        '<input type="radio" name="slirn-rev-rigor" value="custom" />'
        '<span class="slirn-rigor-card-title">自 · 自定义严谨性</span>'
        '<span class="slirn-rigor-card-desc">可在高/中/低任一底稿基础上修改修订标准，'
        '可附加自有规则</span>'
        '<span class="slirn-rigor-card-example">例：附加「专业术语保留英文原文」等自有规则</span>'
        '</label>'
    )
    # 自定义提示词编辑区：选「自定义」时由 JS 展开；高/中/低三档完整提示词随
    # data-prompt-* 下发（REQ-20260916-009）— 点底稿按钮即载入/恢复该档提示词
    # （与服务端 default_custom_prompt 回退逻辑同源，默认底稿 = 高档）
    preset_btns = "".join(
        f'<button type="button" class="slirn-btn slirn-rigor-preset"'
        f' data-action="rigor-prompt-preset" data-preset="{key}"'
        f' title="载入「{_esc(cfg["badge"])} · {_esc(cfg["title"])}」完整提示词，可在此基础上修改">'
        f'{_esc(cfg["badge"])} · {_esc(cfg["title"])}</button>'
        for key, cfg in RIGOR_LEVELS.items()
    )
    custom = (
        f'<div class="slirn-rigor-custom" id="slirn-rigor-custom" style="display:none;"'
        f' data-prompt-high="{_esc(revision_service.build_system_prompt("high"))}"'
        f' data-prompt-medium="{_esc(revision_service.build_system_prompt("medium"))}"'
        f' data-prompt-low="{_esc(revision_service.build_system_prompt("low"))}">'
        '<div class="slirn-rigor-custom-head">'
        '<span>📝 自定义提示词（可载入高/中/低底稿修改）</span>'
        '<span class="slirn-rigor-preset-group">'
        '<span class="slirn-rigor-preset-label">底稿：</span>'
        f'{preset_btns}'
        '</span>'
        '</div>'
        '<textarea class="slirn-textarea slirn-rigor-custom-text" rows="10"'
        ' placeholder="点右侧底稿载入参考后直接修改…"></textarea>'
        '<div class="slirn-rigor-custom-tip">点「底稿：高/中/低」载入对应级别的完整提示词'
        '（会覆盖当前编辑内容），给修改提供参考、也可一键恢复任一档；'
        '请保留「输出要求」中的 JSON 格式部分，否则模型输出无法解析；'
        '内容更正（fix）判定建议保留。草稿自动保存，仅本机浏览器。</div>'
        '</div>'
    )
    return f'<div class="slirn-rigor-cards">{cards}</div>{custom}'


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
        from slirn_home import llm_config

        cur_model = _esc(llm_config.get_current(mgr.tasks_dir.parent))
        return f'''<div class="slirn-card" style="margin-top:16px;">
        <div class="slirn-panel-header"><div class="slirn-panel-title">🎬 处理剪辑 · 第 2 步：字幕修订</div></div>
        <div class="slirn-form-hint">把上一阶段生成的 {len(sub_meta['segments'])} 段字幕交给大模型（<b>{cur_model}</b>，可在顶栏 ⚙️ 修改）逐段分析，识别：</div>
        <div class="slirn-rev-intro">
            <div>🟥 <strong>整行删除</strong> — 口癖、口头禅、语气词、无意义内容</div>
            <div>🟩 <strong>完整保留</strong> — 正常有效内容</div>
            <div>🟪 <strong>切分修剪</strong> — 行内重复只保留一次、剔除夹杂语气词（附建议保留文本）</div>
            <div>🟦 <strong>内容更正</strong> — 错字/别字等文字错误，直接给出更正后字幕（说明附原文对照）</div>
            <div>🟧 <strong>人工复核</strong> — 模型拿不准，交给你判断</div>
        </div>
        <div class="slirn-form-hint">每段建议都带具体分析说明；你在建议之上逐条决策（采纳/改判，切分行填写切分修剪后内容）。</div>
        <div class="slirn-form-hint" style="margin-top:14px;"><b>分析严谨性级别</b>（必选）— 决定大模型按多严格的标准处理字幕：</div>
        {_render_rigor_picker()}
        <div id="slirn-rev-status" class="slirn-status-msg" style="{status_display};"
             data-task-id="{_esc(task_id)}" data-state="{_esc(job_state)}">{running_html}</div>
        <div class="slirn-task-actions" style="margin-top:14px;">
            <button class="slirn-btn slirn-btn-primary" data-action="revise-subtitle" data-task-id="{_esc(task_id)}">🤖 大模型分析字幕</button>
        </div></div>'''

    # ---- 状态 3：建议列表（行式与字幕生成列表一致：序号|时间|文本+徽章|决策；
    #      模型分析/建议保留/手动说明收进每行可展开的详情块 — REQ-20260916-002）----
    rows = ""
    for e in entries:
        cat = e.get("category", "review")
        cat_label = dict(revision_service.LLM_CATEGORIES).get(cat, ("人工复核",))[0]
        decision = e.get("decision", "pending")
        # 需要人工关注或已有手动说明的行默认展开，其余收起保持列表紧凑
        open_detail = cat == "review" or bool(e.get("user_note"))
        # 未决策的行默认选「采纳建议」（REQ-20260916-003）：扫一遍改掉不同意的，
        # 直接保存即全量采纳；「未决策」选项保留，可手动改回
        sel_val = decision if decision != "pending" else "accept"
        opts = "".join(
            f'<option value="{k}"{" selected" if sel_val == k else ""}>{v}</option>'
            for k, v in revision_service.USER_DECISIONS.items()
        )
        # 建议保留（split）/ 更正后（fix）：模型给出的目标文本，收进详情块（REQ-20260916-006）
        extra_html = ""
        if e.get("keep_text"):
            if cat == "split":
                extra_html = (
                    f'<div class="slirn-rev-keeptext">✂️ 建议保留：「{_esc(e["keep_text"])}」</div>'
                )
            elif cat == "fix":
                extra_html = (
                    f'<div class="slirn-rev-keeptext fix">✏️ 更正后：「{_esc(e["keep_text"])}」</div>'
                )
        # split 行「切分修剪后内容」自动预填建议文本（REQ-20260916-010）：手动填写
        # 优先；未填 → keep_text 作起点（与服务端分析时自动填写、切分清单回退同口径，
        # 存量任务未重分析也能看到/微调将要生效的修剪后内容）
        note_val = str(e.get("user_note") or "").strip()
        if not note_val and cat == "split" and e.get("keep_text"):
            note_val = str(e["keep_text"])
        # 决策的实质类别（REQ-20260916-010 过滤口径，与 cutlist_service._final_kind
        # 同源）：手动改判优先；accept = 模型建议类别；pending = 空（未决策）
        if decision in ("keep", "delete", "split", "fix"):
            final_kind = decision
        elif decision == "accept" and cat in ("keep", "delete", "split", "fix"):
            final_kind = cat
        else:
            final_kind = ""
        rows += (
            f'<div class="slirn-rev-row{" open" if open_detail else ""}" data-task-id="{_esc(task_id)}"'
            f' data-start-ms="{int(e.get("start_ms", 0))}" data-end-ms="{int(e.get("end_ms", 0))}"'
            f' data-sugg="{_esc(cat)}" data-decision="{_esc(decision)}" data-final="{_esc(final_kind)}">'
            f'<div class="slirn-rev-line">'
            f'<span class="slirn-sub-idx">{int(e["i"])}</span>'
            f'<span class="slirn-sub-time">{_esc(e.get("start", ""))} → {_esc(e.get("end", ""))}</span>'
            f'<span class="slirn-sub-text"><span class="slirn-rev-badge {cat}">{cat_label}</span>'
            f'{_esc(e.get("text", ""))}</span>'
            f'<select class="slirn-rev-select" data-i="{int(e["i"])}" title="处理决策">{opts}</select>'
            f'<button class="slirn-rev-toggle" data-action="rev-detail" data-i="{int(e["i"])}"'
            f' title="展开/收起模型分析与切分修剪后内容">{"▴" if open_detail else "▾"}</button>'
            f'</div>'
            f'<div class="slirn-rev-detail">'
            f'<div class="slirn-rev-note">🤖 {_esc(e.get("note", ""))}</div>{extra_html}'
            f'<input class="slirn-rev-note-input" data-i="{int(e["i"])}"'
            f' placeholder="切分修剪后内容（可空）" value="{_esc(note_val)}" />'
            f'</div></div>'
        )

    n = len(entries)
    cat_counts = {k: 0 for k in revision_service.LLM_CATEGORIES}
    for e in entries:
        cat_counts[e.get("category", "review")] = cat_counts.get(e.get("category", "review"), 0) + 1
    decided = sum(1 for e in entries if e.get("decision") != "pending")
    created = _esc((rev or {}).get("created_at", ""))
    rigor_key = (rev or {}).get("rigor") or ""
    rigor_cfg = revision_service.RIGOR_LEVELS.get(rigor_key)  # 旧数据无 rigor → 不显示
    if rigor_cfg:
        rigor_stats = f" · 严谨性 {rigor_cfg['badge']}（{rigor_cfg['title']}）"
    elif rigor_key == revision_service.CUSTOM_RIGOR_KEY:
        rigor_stats = " · 严谨性 自定义"  # 实际提示词见 meta.custom_prompt（REQ-20260916-007）
    else:
        rigor_stats = ""
    stats = (
        f"📝 {n} 段 · 分析于 {created} · 模型 {model}{rigor_stats} · "
        f"保留 {cat_counts.get('keep', 0)} / 删除 {cat_counts.get('delete', 0)} / "
        f"切分 {cat_counts.get('split', 0)} / 更正 {cat_counts.get('fix', 0)} / 复核 {cat_counts.get('review', 0)}"
        f" · ✅ 已决策 <b>{decided}/{n}</b> · 点击行定位播放 · 决策列默认「采纳建议」"
        f" · ▾ 展开模型分析与切分修剪后内容"
    )

    return f'''<div class="slirn-card" style="margin-top:16px;">
        <div class="slirn-panel-header"><div class="slirn-panel-title">🎬 处理剪辑 · 第 2 步：字幕修订</div></div>
        <div class="slirn-sub-meta">{stats}</div>
        <div class="slirn-rev-kbhint">⌨ 快捷键：<kbd>↑</kbd><kbd>↓</kbd> 上一条 / 下一条 · <kbd>空格</kbd> 播放 / 暂停
 · <kbd>R</kbd> 重播本行 · <kbd>K</kbd> 保留 · <kbd>D</kbd> 删除（标记后自动下一条）
 · <kbd>S</kbd> 切分（展开详情聚焦内容） · <kbd>Esc</kbd> 退出输入框
 · <span class="slirn-revkeys-open" data-action="revkeys-open" role="button" tabindex="0">⚙ 自定义</span></div>
        <div class="slirn-rev-filter" id="slirn-rev-filter">
          <span class="slirn-rev-filter-label">🔎 筛选</span>
          <span class="slirn-rev-filter-dims">
            <button type="button" class="slirn-rev-filter-dim active" data-rev-filter-dim="sugg">建议状态</button>
            <button type="button" class="slirn-rev-filter-dim" data-rev-filter-dim="dec">决策状态</button>
          </span>
          <span class="slirn-rev-filter-chips" id="slirn-rev-filter-chips"></span>
          <span class="slirn-rev-filter-count" id="slirn-rev-filter-count"></span>
        </div>
        <div id="slirn-rev-player-wrap" class="slirn-video-wrap slirn-sub-player-wrap" style="display:none;">
            <video id="slirn-rev-player" controls preload="metadata"></video>
        </div>
        <div id="slirn-rev-status" class="slirn-status-msg" style="{status_display};"
             data-task-id="{_esc(task_id)}" data-state="{_esc(job_state)}">{running_html}</div>
        <div class="slirn-rev-list" id="slirn-rev-list">{rows}</div>
        <details class="slirn-rigor-box" id="slirn-rev-rigor-box">
            <summary>🔄 重新分析：点选分析严谨性级别（必选）</summary>
            {_render_rigor_picker()}
        </details>
        <div class="slirn-task-actions" style="margin-top:14px;">
            <button class="slirn-btn slirn-btn-primary" data-action="save-revision" data-task-id="{_esc(task_id)}">💾 保存修订决策</button>
            <button class="slirn-btn" data-action="play-rev-video" data-task-id="{_esc(task_id)}">▶️ 播放视频</button>
            <button class="slirn-btn" data-action="revise-subtitle" data-task-id="{_esc(task_id)}" data-has-revision="1">🔄 重新分析</button>
        </div></div>'''


def _render_cutlist_zone(task_id: str, t, mgr: TaskManager) -> str:
    """处理剪辑·第 3 步：切分修剪（REQ-20260916-008）。

    把字幕修订的最终决策翻译成切分修剪清单：保留/更正整段带入（编号继承
    不变）、删除剔除、切分对齐字级时间戳切子段（父编号.子序号 10.1/10.2/10.3）。
    面板打开即服务端现算预览（不落盘）；「生成切分清单」才落盘 +
    推进 ROUGH_CUT_DONE。
    """
    from slirn_home import asr_service, cutlist_service, revision_service

    outputs_dir = mgr.tasks_dir / task_id / "outputs"

    # ---- 状态 1：上一阶段未完成 → 引导 ----
    def _guide(msg: str, btn: str) -> str:
        return f'''<div class="slirn-card" style="margin-top:16px;">
        <div class="slirn-panel-header"><div class="slirn-panel-title">✂️ 处理剪辑 · 第 3 步：切分修剪</div></div>
        <div class="slirn-empty"><div class="slirn-empty-icon">🚧</div>
            <div class="slirn-empty-text">{_esc(msg)}</div></div>
        <div class="slirn-task-actions" style="margin-top:14px;">{btn}</div></div>'''

    rev = revision_service.load_revision(outputs_dir)
    entries = (rev or {}).get("entries") or []
    if not entries:
        return _guide(
            "请先完成上一阶段「字幕修订」— 切分修剪以修订决策（保留/删除/切分/更正）为输入",
            '<button class="slirn-btn slirn-btn-primary" data-action="wb-stage" '
            'data-pane="subtitle_review">📝 去字幕修订</button>',
        )
    if not revision_service.all_decided(rev):
        pending = sum(1 for e in entries if e.get("decision") == "pending")
        return _guide(
            f"字幕修订还有 {pending}/{len(entries)} 条未决策 — 全部决策并保存后，本阶段才可生成切分清单",
            '<button class="slirn-btn slirn-btn-primary" data-action="wb-stage" '
            'data-pane="subtitle_review">📝 去完成决策</button>',
        )
    sub_meta = asr_service.load_subtitle(outputs_dir)
    if not (sub_meta and sub_meta.get("segments")):
        return _guide(
            "缺少字幕生成产物（subtitle.json）— 请先在「字幕生成」阶段生成字幕",
            '<button class="slirn-btn slirn-btn-primary" data-action="wb-stage" '
            'data-pane="subtitle">🎙 去生成字幕</button>',
        )

    # ---- 状态 2：决策齐备 → 预览清单（服务端现算；已保存的手工决策并入显示）----
    saved = cutlist_service.load_cutlist(outputs_dir)
    cutlist = cutlist_service.build_cutlist(
        sub_meta, rev,
        manual_marks=(saved or {}).get("manual_marks") if saved else None,
        actions=(saved or {}).get("actions") if saved else None,
    )
    stats = cutlist["stats"]
    dur_ms = stats["keep_duration_ms"]
    mm_, ss_ = divmod(dur_ms // 1000, 60)
    hh_, mm_ = divmod(mm_, 60)
    dur_str = f"{hh_}:{mm_:02d}:{ss_:02d}" if hh_ else f"{mm_:02d}:{ss_:02d}"
    saved_note = ""
    if saved and saved.get("items"):
        saved_at = _esc(saved.get("saved_at") or "")
        saved_note = f" · ✅ 清单已生成{f'（{saved_at}）' if saved_at else ''}"

    # 按父段分组渲染（REQ-20260916-011 第二步）：keep/fix 整段 = 单行组；
    # split = 组头 + 完整子段表（keep 块 + delete 洞，时间序编号 父.N）
    ACT_BADGES = {"keep": "✅ 已改判保留", "delete": "❌ 已改判删除", "split": "✂️ 已改判切分"}
    actions_map: dict[int, str] = {}
    for k, v in (cutlist.get("actions") or {}).items():
        try:
            actions_map[int(k)] = str(v)
        except (TypeError, ValueError):
            continue
    groups: list[tuple[int, list[dict]]] = []
    for it in cutlist["items"]:
        si = int(it["source_i"])
        if not groups or groups[-1][0] != si:
            groups.append((si, []))
        groups[-1][1].append(it)
    # 改判切分的整段组预填用：修订条目的切分后内容（user_note 优先 → 模型建议）
    note_by_i: dict[int, str] = {}
    for e in rev["entries"]:
        try:
            note_by_i[int(e["i"])] = cutlist_service._split_target(e)
        except (KeyError, TypeError, ValueError):
            continue

    rows = ""
    for si, its in groups:
        first = its[0]
        is_split_group = any(x.get("sub") is not None for x in its)
        act = actions_map.get(si)
        act_badge = ACT_BADGES.get(act, "维持原状")
        act_attr = f' data-act="{_esc(act)}"' if act else ""
        act_cls = " changed" if act else ""
        if is_split_group:
            # 切分组：组头（父编号+原段→切分后文字+决策徽章+重切/试听钮）+ 完整子段表
            rows += (
                f'<div class="slirn-cut-group split" data-source-i="{si}"'
                f' data-task-id="{_esc(task_id)}"{act_attr}'
                f' data-target="{_esc(first.get("target_text") or "")}">'
                f'<div class="slirn-cut-ghead">'
                f'<span class="slirn-sub-idx">{si}</span>'
                f'<span class="slirn-rev-badge split" data-kind="split">切分修剪</span>'
                f'<span class="slirn-cut-gtext">原段：「{_esc(first.get("orig_text", ""))}」'
                f'<span class="slirn-cut-ptarget">→ 切分后：「{_esc(first.get("target_text") or "")}」</span></span>'
                f'<span class="slirn-cut-abadge{act_cls}" data-abadge>{act_badge}</span>'
                f'<button class="slirn-btn slirn-btn-xs" data-cut-act="resplit"'
                f' title="修改切分后内容并按新内容重新划分本段">✂️ 重新切分</button>'
                f'<button class="slirn-btn slirn-btn-xs" data-cut-act="play-keep"'
                f' title="连续跳播本段全部保留子段（成片效果）">▶ 试听</button></div>'
            )
            for it in its:
                mk = str(it.get("mark") or "keep")
                mk_manual = " ✏️" if it.get("mark_manual") else ""
                mk_label = ("✅ 保留" if mk == "keep" else "❌ 删除") + mk_manual
                fb_title = ' title="无字级时间戳（旧字幕数据）或切分后文字无法对齐 — 已整段带入，可重新生成字幕后重试"' if it.get("fallback") else ""
                fb_mark = " ⚠️" if it.get("fallback") else ""
                rows += (
                    f'<div class="slirn-cut-row sub mark-{mk}" data-task-id="{_esc(task_id)}"'
                    f' data-id="{_esc(it["id"])}" data-mark="{mk}" data-mark-init="{mk}" data-source-i="{si}"'
                    f' data-start-ms="{int(it["start_ms"])}" data-end-ms="{int(it["end_ms"])}"{fb_title}>'
                    f'<span class="slirn-sub-idx">{_esc(it["id"])}</span>'
                    f'<span class="slirn-sub-time">{_esc(it["start"])} → {_esc(it["end"])}</span>'
                    f'<span class="slirn-cut-mark" title="点击翻转 保留/删除">{mk_label}</span>'
                    f'<span class="slirn-sub-text">{_esc(it["text"])}{fb_mark}</span>'
                    f'</div>'
                )
            rows += "</div>"
        else:
            # 整段组（keep/fix）：单行即组（可预播/键盘选中/改判，无需组头）。
            # 改判切分（act=split）时附 ✂️ 入口 + data-target（修订 user_note 预填）
            kind = str(first.get("kind", "keep"))
            kind_label = dict(cutlist_service.CUT_KINDS).get(kind, ("完整保留",))[0]
            if kind == "fix":
                text_html = (f'{_esc(first.get("text", ""))}'
                             f'<span class="slirn-cut-ptarget">（原文「{_esc(first.get("orig_text", ""))}」）</span>')
            else:
                text_html = _esc(first.get("text", ""))
            orig_text = str(first.get("text") or first.get("orig_text") or "")
            resplit_btn = ('<button class="slirn-btn slirn-btn-xs" data-cut-act="resplit"'
                           ' title="填写切分后内容，把本段按新内容重新切分">✂️ 重新切分</button>'
                           ) if act == "split" else ""
            rows += (
                f'<div class="slirn-cut-group" data-source-i="{si}"'
                f' data-task-id="{_esc(task_id)}"{act_attr}'
                f' data-orig-text="{_esc(orig_text)}"'
                f' data-target="{_esc(note_by_i.get(si, ""))}">'
                f'<div class="slirn-cut-row whole" data-task-id="{_esc(task_id)}"'
                f' data-id="{_esc(first["id"])}" data-source-i="{si}"'
                f' data-start-ms="{int(first["start_ms"])}" data-end-ms="{int(first["end_ms"])}">'
                f'<span class="slirn-sub-idx">{si}</span>'
                f'<span class="slirn-sub-time">{_esc(first["start"])} → {_esc(first["end"])}</span>'
                f'<span class="slirn-rev-badge {kind}" data-kind="{kind}">{kind_label}</span>'
                f'<span class="slirn-sub-text">{text_html}</span>'
                f'<span class="slirn-cut-abadge{act_cls}" data-abadge>{act_badge}</span>'
                f'{resplit_btn}'
                f'</div></div>'
            )
    # fallback 提示（有降级子段时在统计行下提醒）
    n_fb = stats["fallback"]
    fb_hint = (
        f'<div class="slirn-form-hint">⚠️ {n_fb} 条切分段缺少字级时间戳（旧格式字幕），已整段带入；'
        f"在「字幕生成」阶段重新生成字幕后重算即可精确切分。</div>" if n_fb else ""
    )

    # 修订比已保存清单新 → 过期黄条（REQ-20260916-011）
    stale_note = ""
    if saved and saved.get("items"):
        rev_at = str((rev or {}).get("saved_at") or "")
        cut_at = str(saved.get("saved_at") or "")
        if rev_at and cut_at and rev_at > cut_at:
            stale_note = ('<div class="slirn-form-hint slirn-cut-stale">'
                          "📝 修订决策已更新，已保存的切分清单可能过期 — 可「🔄 重新执行切分修剪」按最新决策重算</div>")

    total_in = len(entries)
    stats_line = (
        f"✂️ 切分修剪预览 · 修订输入 {total_in} 条 · 带入 <b>{stats['brought']}</b> 段"
        f"（保留 {stats['kept']} · 更正 {stats['fixed']} · 切分子段 {stats['split_subs']}"
        f"（含删除洞 {stats['split_subs_delete']}））"
        f" · 剔除 {stats['dropped']} 条 · 预计保留时长 {dur_str}{saved_note}"
        f" · 点击子段预播（播到段尾自动停）· 编号继承修订阶段（父编号.子序号）"
    )
    # 已生成过清单 → 主按钮变「重新执行」（按最新决策重算并清除全部手工决策）
    if saved and saved.get("items"):
        main_btn = (f'<button class="slirn-btn slirn-btn-primary" data-action="rebuild-cutlist"'
                    f' data-task-id="{_esc(task_id)}">🔄 重新执行切分修剪</button>')
    else:
        main_btn = (f'<button class="slirn-btn slirn-btn-primary" data-action="build-cutlist"'
                    f' data-task-id="{_esc(task_id)}">✅ 生成切分清单并完成本阶段</button>')

    return f'''<div class="slirn-card" style="margin-top:16px;">
        <div class="slirn-panel-header"><div class="slirn-panel-title">✂️ 处理剪辑 · 第 3 步：切分修剪</div></div>
        <div class="slirn-sub-meta">{stats_line}</div>
        <div class="slirn-form-hint">本阶段把上一阶段确定的字幕决策落到时间段：完整保留与内容更正的整段带入（编号不变）；
        删除的不带入；切分修剪的按「原段内容 vs 切分后文字」对齐，把整段时间轴**完整**切成交替子段
        （保留块 ✅ / 删除洞 ❌，父编号保留，子段依次编号 10.1、10.2、10.3）——点行从该处连续播放
        （播完一条接下一条，播放行高亮跟随，**只播保留内容：删除洞与改判删除的整条自动跳过 = 成片效果**），
        点标记徽章翻转去留；组头「▶ 试听」同样只播本组保留部分；快捷键与字幕修订阶段一致：
        ↑↓ 切换 · 空格 播/停 · R 重播 · K/D/S 整条改判（保留/删除/切分）。</div>
        {fb_hint}{stale_note}
        <div id="slirn-cut-player-wrap" class="slirn-video-wrap slirn-sub-player-wrap" style="display:none;">
            <video id="slirn-cut-player" controls preload="metadata"></video>
        </div>
        <div class="slirn-cut-list" id="slirn-cut-list">{rows}</div>
        <div class="slirn-task-actions" style="margin-top:14px;">
            {main_btn}
            <button class="slirn-btn" id="slirn-cut-save" data-action="save-cut-decisions"
                    data-task-id="{_esc(task_id)}">💾 保存切分决策</button>
            <button class="slirn-btn" data-action="play-cut-video" data-task-id="{_esc(task_id)}">▶️ 播放视频</button>
        </div></div>'''


def _render_rough_compose_zone(task_id: str, t, mgr: TaskManager) -> str:
    """可选步骤 · 粗剪合成（REQ-20260916-016 → REQ-20260916-018 改用上游合成方法）。

    根据切分修剪的执行口径（保留区间）用上游 VideoClipper 方法合成一版粗剪
    视频，快速预览切分后的整体效果。可选：不推进任务状态、不合成不影响后续
    阶段；产物 outputs/rough_compose.mp4 存在即视为本阶段完成（附随片字幕
    rough_compose.srt）。口径与切分修剪面板完全一致（修订决策实时 + 已保存
    的手工翻转/改判），行文本并入热词替换已确认的修正。
    """
    from slirn_home import asr_service, compose_service, cutlist_service, fine_service, revision_service

    outputs_dir = mgr.tasks_dir / task_id / "outputs"

    def _fmt_dur(ms: int) -> str:
        s = ms // 1000
        mm_, ss_ = divmod(s, 60)
        hh_, mm_ = divmod(mm_, 60)
        return f"{hh_}:{mm_:02d}:{ss_:02d}" if hh_ else f"{mm_}:{ss_:02d}"

    def _guide(msg: str, btn: str) -> str:
        return f'''<div class="slirn-card" style="margin-top:16px;">
        <div class="slirn-panel-header"><div class="slirn-panel-title">🎥 粗剪合成 <span class="slirn-wb-stage-optional">可选</span></div></div>
        <div class="slirn-empty"><div class="slirn-empty-icon">🚧</div>
            <div class="slirn-empty-text">{_esc(msg)}</div></div>
        <div class="slirn-task-actions" style="margin-top:14px;">{btn}</div></div>'''

    rev = revision_service.load_revision(outputs_dir)
    entries = (rev or {}).get("entries") or []
    if not entries:
        return _guide(
            "需要先完成「字幕修订」— 粗剪合成按切分修剪的保留区间拼接视频",
            '<button class="slirn-btn slirn-btn-primary" data-action="wb-stage" '
            'data-pane="subtitle_review">📝 去字幕修订</button>',
        )
    if not revision_service.all_decided(rev):
        return _guide(
            "字幕修订还有未决策条目 — 切分修剪完成后才能确定保留区间",
            '<button class="slirn-btn slirn-btn-primary" data-action="wb-stage" '
            'data-pane="subtitle_review">📝 去完成决策</button>',
        )
    sub_meta = asr_service.load_subtitle(outputs_dir)
    if not (sub_meta and sub_meta.get("segments")):
        return _guide(
            "缺少字幕生成产物 — 请先在「字幕生成」阶段生成字幕",
            '<button class="slirn-btn slirn-btn-primary" data-action="wb-stage" '
            'data-pane="subtitle">🎙 去生成字幕</button>',
        )

    # 与切分修剪面板同口径：修订实时重建 + 已保存手工翻转/改判 → 执行口径保留区间
    saved = cutlist_service.load_cutlist(outputs_dir)
    cutlist = cutlist_service.build_cutlist(
        sub_meta, rev,
        manual_marks=(saved or {}).get("manual_marks") if saved else None,
        actions=(saved or {}).get("actions") if saved else None,
    )
    units = cutlist_service.effective_keep_units(cutlist)
    intervals_ms = [(int(u["start_ms"]), int(u["end_ms"])) for u in units]
    n_merged = len(compose_service.merge_intervals_ms(intervals_ms))
    keep_ms = sum(int(u["end_ms"]) - int(u["start_ms"]) for u in units)

    artifact = compose_service.rough_compose_path(outputs_dir)
    preview_html = ""
    stale_note = ""
    if artifact.exists():
        stat = artifact.stat()
        dur = compose_service._probe_duration(artifact)  # noqa: SLF001 — 同模块族内使用
        size_mb = round(stat.st_size / 1024 / 1024, 1)
        dur_str = _fmt_dur(int((dur or 0) * 1000)) if dur else "时长未知"
        import time as _time

        mtime = _time.strftime("%Y-%m-%d %H:%M", _time.localtime(stat.st_mtime))
        saved_at = str((saved or {}).get("saved_at") or "")
        rev_at = str((rev or {}).get("saved_at") or "")
        fine_at = str((fine_service.load_fine(outputs_dir) or {}).get("saved_at") or "")
        newer = max(saved_at, rev_at, fine_at)
        if newer and newer > _time.strftime("%Y-%m-%dT%H:%M:%S", _time.localtime(stat.st_mtime)):
            stale_note = ('<div class="slirn-cut-stale">⚠️ 切分决策在合成之后有更新 — '
                          "建议重新合成以预览最新效果</div>")
        preview_html = f'''{stale_note}
        <div class="slirn-video-wrap" style="margin-top:12px;">
            <video id="slirn-rc-player" controls preload="metadata"
                   src="/slirn/api/video/{_esc(task_id)}?src=rough_compose"></video>
        </div>
        <div class="slirn-sub-meta" style="margin-top:8px;">🎞️ 粗剪成片 · {size_mb} MB · {dur_str} · 合成于 {mtime}</div>'''

    stats_line = (
        f"保留单元 {len(units)} 个（合并连续段后 {n_merged} 个切点） · "
        f"预计成片时长 {_fmt_dur(keep_ms)}"
    )
    return f'''<div class="slirn-card" style="margin-top:16px;">
        <div class="slirn-panel-header"><div class="slirn-panel-title">🎥 粗剪合成 <span class="slirn-wb-stage-optional">可选</span></div></div>
        <div class="slirn-sub-meta">{stats_line}</div>
        <div class="slirn-form-hint">用「处理之后的字幕 + 原视频」走上游 FunClip 的合成方法（VideoClipper）
        把保留区间拼接成一版粗剪成片，快速预览切分后的整体效果，并附随片字幕 rough_compose.srt。
        本步骤为<b>可选</b> — 不合成也不影响后续阶段；口径与切分修剪面板一致（修订决策实时并入，
        手工翻转/整条改判以「💾 保存切分决策」之后的为准），行文本含热词替换已确认的修正。
        合成需重新编码：44 分钟源实测约 11 分钟，请在后台合成期间继续其它操作。</div>
        <div class="slirn-task-actions" style="margin-top:14px;">
            <button class="slirn-btn slirn-btn-primary" id="slirn-rc-compose" data-action="compose-rough"
                    data-task-id="{_esc(task_id)}">🎬 {'重新合成粗剪视频' if artifact.exists() else '合成粗剪视频'}</button>
        </div>
        <div id="slirn-rc-status" class="slirn-status-msg" style="display:none;"></div>
        {preview_html}
    </div>'''


def _render_fine_review_zone(task_id: str, t, mgr: TaskManager) -> str:
    """处理剪辑 · 精剪修订：热词替换（REQ-20260916-017）。

    切分修剪执行口径的保留行交给大模型，按任务热词找出误识别文字并替换。
    面板展示：任务热词、替换统计（词 → 频次）、行内高亮（<mark> 标替换处）、
    过滤出有替换的行、逐行撤销误替换；「确认并保存」后阶段完成
    （saved_at + 推进 FINE_SUBTITLE_REVIEWED）。
    """
    from slirn_home import asr_service, fine_service, revision_service

    outputs_dir = mgr.tasks_dir / task_id / "outputs"

    def _guide(msg: str, btn: str) -> str:
        return f'''<div class="slirn-card" style="margin-top:16px;">
        <div class="slir-panel-header"><div class="slirn-panel-title">🔎 精剪修订 · 热词替换</div></div>
        <div class="slirn-empty"><div class="slirn-empty-icon">🚧</div>
            <div class="slirn-empty-text">{_esc(msg)}</div></div>
        <div class="slirn-task-actions" style="margin-top:14px;">{btn}</div></div>'''

    rev = revision_service.load_revision(outputs_dir)
    if not ((rev or {}).get("entries")):
        return _guide(
            "需要先完成「字幕修订」— 热词替换处理切分修剪之后的字幕文本",
            '<button class="slirn-btn slirn-btn-primary" data-action="wb-stage" '
            'data-pane="subtitle_review">📝 去字幕修订</button>',
        )
    if not revision_service.all_decided(rev):
        return _guide(
            "字幕修订还有未决策条目 — 完成决策后才能确定切分后的字幕行",
            '<button class="slirn-btn slirn-btn-primary" data-action="wb-stage" '
            'data-pane="subtitle_review">📝 去完成决策</button>',
        )
    sub_meta = asr_service.load_subtitle(outputs_dir)
    if not (sub_meta and sub_meta.get("segments")):
        return _guide(
            "缺少字幕生成产物 — 请先在「字幕生成」阶段生成字幕",
            '<button class="slirn-btn slirn-btn-primary" data-action="wb-stage" '
            'data-pane="subtitle">🎙 去生成字幕</button>',
        )
    hotwords: list[str] = []
    try:
        if t.hotwords_path.exists():
            hotwords = [w for w in t.hotwords_path.read_text(encoding="utf-8").split() if w]
    except Exception:  # noqa: BLE001
        pass
    if not hotwords:
        return _guide(
            "任务没有热词 — 热词替换需要先在任务里添加热词（专有名词/人名/术语的正确写法）",
            '<button class="slirn-btn slirn-btn-primary" data-action="wb-stage" '
            'data-pane="assets">📦 去添加热词</button>',
        )

    # 行集口径与切分面板/粗剪合成一致（切分之后的字幕 = 执行口径保留行）
    from slirn_home import cutlist_service
    saved = cutlist_service.load_cutlist(outputs_dir)
    cutlist = cutlist_service.build_cutlist(
        sub_meta, rev,
        manual_marks=(saved or {}).get("manual_marks") if saved else None,
        actions=(saved or {}).get("actions") if saved else None,
    )
    units = cutlist_service.effective_keep_units(cutlist)

    fine = fine_service.load_fine(outputs_dir)
    job = fine_service.job_status(task_id)
    job_state = job.get("state") if job else ("done" if fine else "idle")
    hw_chips = "".join(f'<span class="slirn-hw-chip">{_esc(w)}</span>' for w in hotwords)

    # ---- 状态 A：未分析 → 说明 + 热词 + 分析按钮 ----
    if not fine:
        from slirn_home import llm_config
        cur_model = _esc(llm_config.get_current(mgr.tasks_dir.parent))
        return f'''<div class="slirn-card" style="margin-top:16px;">
        <div class="slirn-panel-header"><div class="slirn-panel-title">🔎 精剪修订 · 热词替换</div></div>
        <div class="slirn-form-hint">把切分修剪之后保留的 <b>{len(units)}</b> 行字幕交给大模型（<b>{cur_model}</b>，可在顶栏 ⚙️ 修改），
        按下面的任务热词找出<b>误识别文字</b>并替换（如「神精网络」→「神经网络」）。替换只动错误部分，
        不改写整行；拿不准的一律不动（宁缺勿错）。</div>
        <div class="slirn-form-hint" style="margin-top:10px;"><b>任务热词</b>（{len(hotwords)} 个）：</div>
        <div class="slirn-hw-chips">{hw_chips}</div>
        <div class="slirn-form-hint">分析完成后可查看替换统计、逐处确认（撤销误替换），确认保存后本阶段完成。</div>
        <div class="slirn-task-actions" style="margin-top:14px;">
            <button class="slirn-btn slirn-btn-primary" data-action="fine-revise"
                    data-task-id="{_esc(task_id)}">🔎 大模型热词替换分析</button>
        </div>
        <div id="slirn-fine-status" class="slirn-status-msg" style="display:none;"
             data-task-id="{_esc(task_id)}" data-state="{_esc(job_state)}"></div>
    </div>'''

    # ---- 状态 B：替换结果 → 统计 + 高亮行 + 确认 ----
    import time as _time
    est = fine_service.effective_stats(fine)
    wstats = fine_service.word_stats(fine)
    by_id = {str(e.get("id")): e for e in (fine.get("entries") or [])}
    confirmed = bool(fine.get("saved_at"))
    running_html = ""
    if job_state == "running":
        elapsed = int((job.get("finished_at") or _time.time()) - job.get("started_at", _time.time()))
        running_html = (f'⏳ {_esc(job.get("stage") or "处理中")} · '
                        f"{job.get('progress') or 0:.0f}% · 已耗时 {elapsed}s")

    stale_note = ""
    created = str(fine.get("created_at") or "")
    newer = max(str((saved or {}).get("saved_at") or ""), str((rev or {}).get("saved_at") or ""))
    if newer and created and newer > created:
        stale_note = ('<div class="slirn-cut-stale">⚠️ 切分/修订决策在热词替换之后有更新 — '
                      "建议重新分析以覆盖最新字幕文本</div>")

    wstats_html = " · ".join(
        f'<span class="slirn-fw-word">{_esc(w)}<b>×{n}</b></span>' for w, n in wstats
    ) or '<span class="slirn-sub-meta">没有生效的替换</span>'

    def _line_html(u: dict) -> str:
        rid = str(u.get("id"))
        e = by_id.get(rid)
        start = str(u.get("start") or "")
        text = _esc(str(u.get("text", "")))
        if not e:
            return (f'<div class="slirn-fw-row plain" data-id="{_esc(rid)}">'
                    f'<span class="slirn-sub-idx">{_esc(rid)}</span>'
                    f'<span class="slirn-fw-time">{_esc(start)}</span>'
                    f'<div class="slirn-fw-text">{text}</div></div>')
        reps = sorted(e.get("replacements") or [], key=lambda r: int(r.get("pos", 0)))
        # 生效文本：高亮替换处（title 悬浮看原文）
        segs = []
        pos = 0
        raw = str(e.get("text") or "")
        for r in reps:
            p = int(r.get("pos", 0))
            segs.append(_esc(raw[pos:p]))
            segs.append(f'<mark class="slirn-fw-mark" title="原文：{_esc(str(r.get("before")))}">'
                        f"{_esc(str(r.get('after')))}</mark>")
            pos = p + len(str(r.get("before")))
        segs.append(_esc(raw[pos:]))
        live_html = "".join(segs)
        reverted = bool(e.get("reverted"))
        return (f'<div class="slirn-fw-row{" reverted" if reverted else ""}" data-id="{_esc(rid)}"'
                f' data-reverted="{1 if reverted else 0}" data-start-ms="{int(u.get("start_ms", 0))}">'
                f'<span class="slirn-sub-idx">{_esc(rid)}</span>'
                f'<span class="slirn-fw-time">{_esc(start)}</span>'
                f'<div class="slirn-fw-text">'
                f'<div class="slirn-fw-live">{live_html}</div>'
                f'<div class="slirn-fw-orig">{text}</div></div>'
                f'<button class="slirn-btn slirn-btn-xs" data-action="fine-toggle" '
                f'data-id="{_esc(rid)}">{"↩️ 已撤销 · 恢复" if reverted else "↩️ 撤销替换"}</button></div>')

    rows = "".join(_line_html(u) for u in units)
    confirm_label = "✅ 确认替换结果" if not confirmed else "✅ 已确认 · 再次保存"
    model_disp = _esc(fine.get("model") or "")
    return f'''<div class="slirn-card" style="margin-top:16px;">
        <div class="slirn-panel-header"><div class="slirn-panel-title">🔎 精剪修订 · 热词替换</div></div>
        <div class="slirn-sub-meta">保留行 {len(units)} · 替换 <b>{est["replacements"]}</b> 处 · 涉及 {est["replaced_lines"]} 行
        · 已撤销 {est["reverted"]} 行 · 模型 {model_disp}{" · ✅ 已确认" if confirmed else ""}</div>
        {stale_note}
        <div class="slirn-form-hint" style="margin-top:10px;"><b>替换热词频次</b>（撤销的行不计）：</div>
        <div class="slirn-fw-stats">{wstats_html}</div>
        <div class="slirn-form-hint">行内 <mark class="slirn-fw-mark">高亮</mark> = 大模型替换处（悬浮可见原文）；
        点行定位播放，逐处检查替换是否正确 — 错误的点「撤销替换」，确认无误后保存。</div>
        <div class="slirn-task-actions" style="margin-top:10px;">
            <button class="slirn-btn" data-action="fine-filter" data-shown="1"
                    data-all-text="🔍 只看有替换的行（{len(by_id)}/{len(units)}）">🔍 只看有替换的行（{len(by_id)}/{len(units)}）</button>
        </div>
        <div id="slirn-fine-player-wrap" class="slirn-video-wrap slirn-sub-player-wrap" style="display:none;">
            <video id="slirn-fine-player" controls preload="metadata"></video>
        </div>
        <div class="slirn-fw-list" id="slirn-fw-list">{rows}</div>
        <div class="slirn-task-actions" style="margin-top:14px;">
            <button class="slirn-btn slirn-btn-primary" data-action="save-fine-revision"
                    data-task-id="{_esc(task_id)}">{confirm_label}</button>
            <button class="slirn-btn" data-action="fine-revise" data-task-id="{_esc(task_id)}"
                    data-has-fine="1">🔄 重新分析</button>
        </div>
        <div id="slirn-fine-status" class="slirn-status-msg" style="{'display:none;' if job_state != 'running' else ''};"
             data-task-id="{_esc(task_id)}" data-state="{_esc(job_state)}">{running_html}</div>
    </div>'''


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
    ("rough_cut",       "ROUGH_CUT_DONE",       "切分修剪", "✂️", "按修订决策带入保留/更正段，切分段父编号+子编号"),
    ("rough_compose",  "FINE_SUBTITLE_DONE",   "粗剪合成", "🎥", "按切分保留内容用上游 VideoClipper 合成粗剪视频（含随片字幕）"),
    ("fine_review",     "FINE_SUBTITLE_REVIEWED", "精剪修订", "🔎", "按任务热词替换字幕误识别文字，统计频次并逐处确认"),
    ("fine_cut",        "FINE_CUT_DONE",        "精剪视频", "🎬", "按精剪段生成成品视频"),
    ("mux",             "MUXED",                "字幕合成", "🎞️", "字幕烧录进画面 / 封装输出成品"),
]


def _wb_stage_states(t) -> list[str]:
    """各阶段状态：done / current / pending。

    素材准备看磁盘资产（原视频在即完成，DRAFT 状态也算）；
    字幕生成看产物 subtitle.json、字幕修订看 revision.json、切分修剪看
    cutlist.json（服务器重启后内存 job 不在，以磁盘为准）；
    其余按 TaskStatus 管线序比较。
    """
    from tasklib.models import TaskStatus

    from slirn_home import asr_service as _asr_mod
    from slirn_home import cutlist_service as _cut_mod
    from slirn_home import revision_service as _rev_mod

    rank = {s.name: i for i, s in enumerate(TaskStatus)}
    cur_rank = rank.get(getattr(t.status, "name", str(t.status)), 0)
    states: list[str] = []
    assets_done = t.original_video_source.exists() or t.original_video_symlink.exists()
    outputs_dir = Path(str(t.hotwords_path)).parent / "outputs"
    sub_meta = None
    rev_meta = None
    cut_meta = None
    try:
        sub_meta = _asr_mod.load_subtitle(outputs_dir)
    except Exception:  # noqa: BLE001
        pass
    try:
        rev_meta = _rev_mod.load_revision(outputs_dir)
    except Exception:  # noqa: BLE001
        pass
    try:
        cut_meta = _cut_mod.load_cutlist(outputs_dir)
    except Exception:  # noqa: BLE001
        pass
    subtitle_done = bool(sub_meta and sub_meta.get("segments"))
    review_done = bool(rev_meta and rev_meta.get("entries"))
    cut_done = bool(cut_meta and cut_meta.get("items"))
    for key, status_name, *_rest in _WB_STAGES:
        if key == "assets":
            states.append("done" if assets_done else "pending")
        elif key == "subtitle":
            states.append("done" if (subtitle_done or cur_rank >= rank["SUBTITLE_GENERATED"]) else "pending")
        elif key == "subtitle_review":
            states.append(
                "done" if (review_done or cur_rank >= rank["SUBTITLE_REVIEWED"]) else "pending"
            )
        elif key == "rough_cut":
            states.append("done" if (cut_done or cur_rank >= rank[status_name]) else "pending")
        elif key == "rough_compose":
            # 可选步骤（REQ-20260916-016）：不推进任务状态，产物存在即完成；
            # 未合成也不阻塞后续阶段（current 停留于此仅是建议）
            from slirn_home import compose_service as _comp_mod

            composed = _comp_mod.rough_compose_path(outputs_dir).exists()
            states.append("done" if (composed or cur_rank >= rank[status_name]) else "pending")
        elif key == "fine_review":
            # 热词替换（REQ-20260916-017）：替换结果确认保存（saved_at）即完成；
            # 已分析未确认 → 仍 pending（current 停在这提示待确认）
            from slirn_home import fine_service as _fine_mod

            fine = _fine_mod.load_fine(outputs_dir)
            fine_done = bool(fine and fine.get("saved_at"))
            states.append("done" if (fine_done or cur_rank >= rank[status_name]) else "pending")
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
        opt_chip = '<span class="slirn-wb-stage-optional">可选</span>' if key == "rough_compose" else ""
        stage_items += (
            f'<div class="slirn-wb-stage {state}{" active" if i == focus else ""}" '
            f'data-action="wb-stage" data-pane="{key}">'
            f'<span class="slirn-wb-stage-mark">{mark}</span>'
            f'<div class="slirn-wb-stage-body"><div class="slirn-wb-stage-title">{icon} {title}{opt_chip}</div>'
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
        "rough_cut": _render_cutlist_zone(task_id, t, mgr),
        "rough_compose": _render_rough_compose_zone(task_id, t, mgr),
        "fine_review": _render_fine_review_zone(task_id, t, mgr),
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
                <button class="slirn-btn slirn-btn-sm slirn-wb-stages-expand" data-action="wb-toggle-stages"
                        title="展开左侧阶段列表">🧭 展开阶段</button>
                <button class="slirn-btn slirn-btn-sm" data-action="edit-task" data-task-id="{_esc(task_id)}">✏️ 编辑任务</button>
                <button class="slirn-btn slirn-btn-sm" data-action="goto-tasks">📋 返回列表</button>
            </div>
        </div>
        {top_rows}
    </div>
    <div class="slirn-wb-main">
        <div class="slirn-wb-stages-rail" data-action="wb-toggle-stages"
             title="展开左侧阶段列表"><span>🧭</span><span>阶</span><span>段</span><span>»</span></div>
        <div class="slirn-card slirn-wb-stages">
            <div class="slirn-wb-stages-head"><span class="slirn-wb-stages-title">🧭 阶段</span>
                <button class="slirn-btn-mini" data-action="wb-toggle-stages"
                        title="收起阶段列表，加宽右侧工作区">« 收起</button></div>
            {stage_items}
        </div>
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

  // ===== 大模型设置弹窗（REQ-20260915-008：多厂商模型动态注册；REQ-20260916-001：协议 + 编辑）=====
  // 已注册模型列表（当前 ⭐ 高亮 + 设为当前/测试/编辑/删除）+ 表单（模型名/厂商/
  // 协议/Base URL/Key 环境变量名，添加与编辑共用）。Key 本身始终从系统环境变量
  // 读取，界面只填「环境变量名」。
  var LLM_EDIT_ID = null;   // 非空 = 表单处于编辑该模型状态
  var LLM_MODELS = [];      // 最近一次列表数据（编辑时回填表单用）
  function llmFormReset() {
    LLM_EDIT_ID = null;
    ['slirn-llm-in-id', 'slirn-llm-in-provider', 'slirn-llm-in-url', 'slirn-llm-in-env']
      .forEach(function(i) { var el = document.getElementById(i); if (el) el.value = ''; });
    var proto = document.getElementById('slirn-llm-in-proto');
    if (proto) proto.value = 'openai';
    var t = document.getElementById('slirn-llm-form-title');
    if (t) t.textContent = '添加模型';
    var btn = document.getElementById('slirn-llm-add-btn');
    if (btn) btn.textContent = '➕ 添加';
    var cancel = document.getElementById('slirn-llm-cancel-btn');
    if (cancel) cancel.style.display = 'none';
  }
  function openLLMSettings() {
    var existing = document.getElementById('slirn-llm-modal');
    if (existing) existing.remove();
    var overlay = document.createElement('div');
    overlay.id = 'slirn-llm-modal';
    overlay.className = 'slirn-modal-overlay';
    overlay.innerHTML =
      '<div class="slirn-modal-card slirn-llm-card">' +
        '<div class="slirn-modal-title">⚙️ 大模型设置</div>' +
        '<div class="slirn-llm-subtitle">当前模型：<b id="slirn-llm-current">…</b></div>' +
        '<div class="slirn-llm-list" id="slirn-llm-list"></div>' +
        '<div class="slirn-llm-section" id="slirn-llm-form-title">添加模型</div>' +
        '<div class="slirn-llm-form" id="slirn-llm-form">' +
          '<div class="slirn-llm-row"><span class="slirn-llm-label">模型名</span>' +
            '<input id="slirn-llm-in-id" class="slirn-llm-input" placeholder="如 deepseek-chat" /></div>' +
          '<div class="slirn-llm-row"><span class="slirn-llm-label">厂商</span>' +
            '<input id="slirn-llm-in-provider" class="slirn-llm-input" placeholder="如 DeepSeek / 阿里云百炼" /></div>' +
          '<div class="slirn-llm-row"><span class="slirn-llm-label">协议</span>' +
            '<select id="slirn-llm-in-proto" class="slirn-llm-input">' +
              '<option value="openai">OpenAI 兼容（{Base URL}/chat/completions）</option>' +
              '<option value="anthropic">Anthropic（{Base URL}/v1/messages）</option>' +
            '</select></div>' +
          '<div class="slirn-llm-row"><span class="slirn-llm-label">Base URL</span>' +
            '<input id="slirn-llm-in-url" class="slirn-llm-input" placeholder="https://api.deepseek.com/v1" /></div>' +
          '<div class="slirn-llm-row"><span class="slirn-llm-label">Key 环境变量</span>' +
            '<input id="slirn-llm-in-env" class="slirn-llm-input" placeholder="如 DEEPSEEK_API_KEY" /></div>' +
        '</div>' +
        '<div class="slirn-llm-tip">按所选协议调用（OpenAI 兼容 <code>{Base URL}/chat/completions</code> / Anthropic <code>{Base URL}/v1/messages</code>）；API Key 从上面填写的系统环境变量读取，界面不存储 Key。</div>' +
        '<div id="slirn-llm-test-result" class="slirn-llm-test-result"></div>' +
        '<div class="slirn-llm-actions">' +
          '<button class="slirn-btn slirn-btn-primary" data-action="llm-add" id="slirn-llm-add-btn">➕ 添加</button>' +
          '<button class="slirn-btn" data-action="llm-cancel-edit" id="slirn-llm-cancel-btn" style="display:none">取消编辑</button>' +
          '<button class="slirn-btn" data-action="llm-close">关闭</button>' +
        '</div>' +
      '</div>';
    overlay.addEventListener('click', function(e) {
      if (e.target === overlay) overlay.remove();  // 点遮罩关闭
    });
    document.body.appendChild(overlay);
    llmFormReset();
    fetch(SLIRN_API + '/llm_config').then(function(r) { return r.json(); })
      .then(function(r) {
        if (r && r.ok) renderLLMList(r.models || [], r.current || '');
      })
      .catch(function() {});
  }
  function renderLLMList(models, current) {
    LLM_MODELS = models;
    var cur = document.getElementById('slirn-llm-current');
    if (cur) cur.textContent = current || '（无）';
    var list = document.getElementById('slirn-llm-list');
    if (!list) return;
    if (!models.length) {
      list.innerHTML = '<div class="slirn-llm-empty">尚未注册模型，请在下方添加</div>';
      return;
    }
    list.innerHTML = models.map(function(m) {
      var isCur = m.id === current;
      var proto = m.protocol === 'anthropic' ? 'Anthropic' : 'OpenAI 兼容';
      return '<div class="slirn-llm-item' + (isCur ? ' current' : '') + '">' +
        '<div class="slirn-llm-item-head">' +
          '<span class="slirn-llm-item-name">' + (isCur ? '⭐ ' : '') + escapeHtml(m.id) +
            '<span class="slirn-llm-item-prov">' + escapeHtml(m.provider || '') + '</span>' +
            '<span class="slirn-llm-item-proto">' + proto + '</span></span>' +
          '<span class="slirn-llm-item-key ' + (m.key_present ? 'ok' : 'miss') + '">' +
            (m.key_present ? '✅ Key 已配置' : '❌ 未配置 ' + escapeHtml(m.api_key_env)) + '</span>' +
        '</div>' +
        '<div class="slirn-llm-item-url">' + escapeHtml(m.base_url) +
          ' <code>' + escapeHtml(m.api_key_env) + '</code></div>' +
        '<div class="slirn-llm-item-ops">' +
          (isCur
            ? '<span class="slirn-llm-item-cur">✅ 当前使用中</span>'
            : '<button class="slirn-btn slirn-btn-sm slirn-btn-primary" data-action="llm-use" data-id="' +
              escapeHtml(m.id) + '">⭐ 设为当前</button>') +
          '<button class="slirn-btn slirn-btn-sm" data-action="llm-test" data-id="' + escapeHtml(m.id) + '">测试</button>' +
          '<button class="slirn-btn slirn-btn-sm" data-action="llm-edit" data-id="' + escapeHtml(m.id) + '">编辑</button>' +
          '<button class="slirn-btn slirn-btn-sm" data-action="llm-remove" data-id="' + escapeHtml(m.id) + '">删除</button>' +
        '</div>' +
      '</div>';
    }).join('');
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
    if (card && v) { v.src = '/gradio_api/file=' + encodeURI(info.path); v.load(); card.style.display = ''; bindSpeedControl(v); }  // 倍速控件（REQ-20260916-014）
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
    if (wrap && v) { v.src = '/gradio_api/file=' + encodeURI(info.path); v.load(); wrap.style.display = ''; bindSpeedControl(v); }  // 倍速控件（REQ-20260916-014）
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
      bindSpeedControl(v);  // 倍速控件（REQ-20260916-014）
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
        // 重渲染后旧行引用全部失效：试听状态清零（防跳播序列指向已移除的行）
        cutKeepSeq = null; cutKeepIdx = 0; cutKeepMode = null;
        bindCutPlayer();  // 切分修剪播放器（timeupdate 高亮/自动停/跳播 — REQ-20260916-011）
        var rcPv = document.getElementById('slirn-rc-player');
        if (rcPv) bindSpeedControl(rcPv);  // 粗剪成片预览倍速（REQ-20260916-014 同款）
        var finePv = revVis('slirn-fine-player');
        if (finePv) bindSpeedControl(finePv);  // 热词替换行播放倍速（REQ-20260916-017）
        bindFineRows(tid);
        var fineSt = revVis('slirn-fine-status');  // 分析 job 还在跑 → 恢复轮询
        if (fineSt && fineSt.dataset.taskId && fineSt.dataset.state === 'running')
          startFinePolling(fineSt.dataset.taskId);
        applyWbStagesState();  // 恢复上次收起/展开（跨刷新保持 — REQ-20260916-002）
        applyRevRigorState();  // 上次选过的严谨性级别预填（REQ-20260916-003）
      } else if (r && r.error) {
        toast('❌ ' + r.error, 'error');
      }
    });
  }

  // ===== 阶段列表收起/展开（localStorage 记忆 — REQ-20260916-002）=====
  function applyWbStagesState() {
    var host = document.getElementById('slirn-tab-workbench-inner');
    if (!host) return;
    var collapsed = '';
    try { collapsed = localStorage.getItem('slirnWbStagesCollapsed') || ''; } catch (err) {}
    if (collapsed !== '0' && collapsed !== '1') {  // 脏值自愈：当作未收起（REQ-20260916-011）
      collapsed = '';
      try { localStorage.removeItem('slirnWbStagesCollapsed'); } catch (err) {}
    }
    host.classList.toggle('wb-stages-collapsed', collapsed === '1');
  }

  // ===== 严谨性级别：上次选择预填（不发起新分析也可见 — REQ-20260916-003）=====
  function applyRevRigorState() {
    if (!document.querySelector('input[name="slirn-rev-rigor"]:checked')) {
      var saved = '';
      try { saved = localStorage.getItem('slirnRevRigor') || ''; } catch (err) {}
      if (['high', 'medium', 'low', 'custom'].indexOf(saved) >= 0) {
        var el = document.querySelector('input[name="slirn-rev-rigor"][value="' + saved + '"]');
        if (el) el.checked = true;
      }
    }
    syncRigorCustomUI();  // 自定义档编辑区跟随（REQ-20260916-007）
  }

  // ===== 自定义严谨性（REQ-20260916-007/009）：编辑区展开 + 底稿/草稿预填 =====
  // 选中「自定义」才展开；textarea 首次展开预填 localStorage 草稿，无草稿用高档底稿
  // （data-prompt-high 与服务端 default_custom_prompt 回退逻辑同源），此后不再覆盖用户编辑
  function syncRigorCustomUI() {
    var wrap = document.getElementById('slirn-rigor-custom');
    if (!wrap) return;
    var sel = document.querySelector('input[name="slirn-rev-rigor"]:checked');
    var isCustom = !!(sel && sel.value === 'custom');
    wrap.style.display = isCustom ? '' : 'none';
    var ta = wrap.querySelector('.slirn-rigor-custom-text');
    if (isCustom && ta && !ta.dataset.slirnFilled) {
      var draft = '';
      try { draft = localStorage.getItem('slirnRevCustomPrompt') || ''; } catch (err) {}
      ta.value = draft || wrap.getAttribute('data-prompt-high') || '';
      ta.dataset.slirnFilled = '1';
    }
  }
  document.addEventListener('change', function(e) {
    if (e.target && e.target.name === 'slirn-rev-rigor') syncRigorCustomUI();
    // 决策下拉变化 → 行 data-decision/data-final 跟随（实质口径），过滤
    // 计数/可见性实时刷新（REQ-20260916-010；未保存前纯前端，刷新即还原）
    if (e.target && e.target.classList && e.target.classList.contains('slirn-rev-select')) {
      var rowF = e.target.closest('.slirn-rev-row');
      if (rowF) {
        var dv = e.target.value || 'pending';
        var suggF = rowF.getAttribute('data-sugg') || '';
        var finF = '';
        if (dv === 'keep' || dv === 'delete' || dv === 'split' || dv === 'fix') finF = dv;
        else if (dv === 'accept' && (suggF === 'keep' || suggF === 'delete'
            || suggF === 'split' || suggF === 'fix')) finF = suggF;
        rowF.setAttribute('data-decision', dv);
        rowF.setAttribute('data-final', finF);
        revFilterSync();
      }
    }
  });
  // 草稿实时保存（仅本机浏览器）— 不依赖点「分析」提交
  document.addEventListener('input', function(e) {
    if (e.target && e.target.classList &&
        e.target.classList.contains('slirn-rigor-custom-text')) {
      try { localStorage.setItem('slirnRevCustomPrompt', e.target.value); } catch (err) {}
    }
  });

  function switchWbPane(paneKey) {
    document.querySelectorAll('.slirn-wb-stage').forEach(function(s) {
      s.classList.toggle('active', s.getAttribute('data-pane') === paneKey);
    });
    document.querySelectorAll('.slirn-wb-pane').forEach(function(p) {
      p.style.display = (p.id === 'slirn-wb-pane-' + paneKey) ? '' : 'none';
    });
    // 面板显隐切换后过滤计数才可见：重算 chips/可见性（工作台初始打开时
    // 修订面板可能隐藏，revRows 取不到行 → 计数 0；REQ-20260916-010）
    revFilterSync();
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

  // ===== 倍速显示与控制（REQ-20260916-014）=====
  // 背景：全部代码无任何 playbackRate 写入（grep 证实），神秘变速来自 Chrome 原生视频
  // 菜单（右键/⋮「播放速度」）等外部途径——页面上原本无任何倍速显示，所以"不知道为
  // 什么变快变慢"。这里把真实速率显示出来并可调大/调小/一键回 1x；ratechange 监听
  // 捕获任何来源的变速——速率一变必可见，不再是黑盒。
  var SLIRN_RATE_STEPS = [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2, 2.5, 3];
  var slirnRate = 1;  // 会话级倍速：播放器元素随面板重渲染重建（重建后浏览器重置 1x），重绑时重新应用
  function slirnRateLabel(r) { return parseFloat((+r || 1).toFixed(2)) + 'x'; }
  function bindSpeedControl(v) {
    if (!v) return;
    var wrap = (v.closest ? v.closest('.slirn-video-wrap') : null) || v.parentNode;
    if (!wrap || !wrap.querySelector) return;
    var ctl = wrap.querySelector('.slirn-speed');
    if (!ctl) {
      ctl = document.createElement('div');
      ctl.className = 'slirn-speed';
      ctl.innerHTML = '<span class="slirn-speed-cap">倍速</span>'
        + '<button type="button" class="slirn-btn slirn-btn-xs" data-rate-act="down" title="调慢（最低 0.5x）">−</button>'
        + '<button type="button" class="slirn-btn slirn-btn-xs slirn-speed-cur" data-rate-act="reset" title="点击恢复 1x">1x</button>'
        + '<button type="button" class="slirn-btn slirn-btn-xs" data-rate-act="up" title="调快（最高 3x）">＋</button>';
      wrap.appendChild(ctl);
      ctl.addEventListener('click', function(e) {
        var b = e.target.closest ? e.target.closest('[data-rate-act]') : null;
        if (!b) return;
        e.preventDefault();
        var r = +v.playbackRate || 1;
        var act = b.getAttribute('data-rate-act');
        if (act === 'reset') {
          r = 1;  // 调回原来的值 = 正常速度
        } else {
          // 阶梯调大/调小：先找当前速率最近的档位，再上下移动一格
          var near = 0;
          for (var i = 1; i < SLIRN_RATE_STEPS.length; i++) {
            if (Math.abs(SLIRN_RATE_STEPS[i] - r) < Math.abs(SLIRN_RATE_STEPS[near] - r)) near = i;
          }
          var nxt = Math.max(0, Math.min(SLIRN_RATE_STEPS.length - 1, near + (act === 'up' ? 1 : -1)));
          r = SLIRN_RATE_STEPS[nxt];
        }
        v.playbackRate = r;  // 触发 ratechange → 下方监听统一同步显示
      });
    }
    if (!ctl.dataset.ratebound) {
      ctl.dataset.ratebound = '1';
      // 任何来源的变速（本控件 / Chrome 原生菜单 / 扩展）→ 显示同步为真实速率
      v.addEventListener('ratechange', function() {
        slirnRate = +v.playbackRate || 1;
        var cur = ctl.querySelector('[data-rate-act="reset"]');
        if (cur) cur.textContent = slirnRateLabel(slirnRate);
      });
    }
    // 元素重渲染后浏览器重置为 1x → 重绑时把会话倍速应用回去（同值赋值不触发 ratechange，手动刷一次显示）
    try { v.playbackRate = slirnRate; } catch (err) {}
    var cur0 = ctl.querySelector('[data-rate-act="reset"]');
    if (cur0) cur0.textContent = slirnRateLabel(v.playbackRate);
  }

  function bindSubPlayer() {
    var v = document.getElementById('slirn-sub-player');
    var list = document.getElementById('slirn-sub-list');
    bindSpeedControl(v);  // 倍速显示与控制（REQ-20260916-014）
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

  // 详情页与工作台可能同时持有修订区 DOM（隐藏 tab 不清空 innerHTML）→ 一律取
  // 「可见的」那个元素，避免 id 撞车时操作到隐藏播放器/列表（REQ-20260916-004 顺带修复）
  function revVis(id) {
    var els = document.querySelectorAll('#' + id);
    for (var i = 0; i < els.length; i++) { if (els[i].offsetParent) return els[i]; }
    return els[0] || null;
  }

  function playRevAt(tid, startMs) {
    var wrap = revVis('slirn-rev-player-wrap');
    var v = revVis('slirn-rev-player');
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

  // 切分修剪行定位播放（REQ-20260916-008）— 与修订行同模式，独立播放器防 id 撞车
  function playCutAt(tid, startMs) {
    var wrap = revVis('slirn-cut-player-wrap');
    var v = revVis('slirn-cut-player');
    if (!v) { toast('❌ 播放器未就绪', 'error'); return; }
    if (wrap) wrap.style.display = '';
    if (!v.src) { v.src = SLIRN_API + '/video/' + encodeURIComponent(tid); v.load(); }
    var goCut = function() {
      try { v.currentTime = (startMs || 0) / 1000; } catch (err) {}
      var p = v.play();
      if (p && p.catch) p.catch(function() {});
    };
    if (v.readyState >= 1) goCut();
    else v.addEventListener('loadedmetadata', goCut, {once: true});
  }

  // ===== 切分修剪预览与决策（REQ-20260916-011 → 013 连续播放）=====
  // 行点击/↑↓ = 从该行起点连续播放（REQ-20260916-013：播完一条自动接下一条，
  // 不再段尾自停），播放中 active 高亮跟随（与字幕/修订阶段同款）；
  // 组头 ▶ 试听 = 本组 keep 子段成片口径跳播。
  // 快捷键与字幕修订阶段同键位（可自定义）：↑↓ 切换 · 空格 播/停 · R 重播 ·
  // K/D/S 对选中行所属字幕整条改判（保留/删除/切分，再按同键取消）。
  // 翻转/改判未保存纯前端，「💾 保存切分决策」落盘。
  var cutKeepSeq = null; // 试听跳播序列 [{s,e,row},…]（null = 不跳播）
  var cutKeepIdx = 0;    // 试听当前段下标（索引跟踪：只前进不回扫 — REQ-20260916-012）
  var cutKeepMode = null; // 跳播来源：'group' 组头试听（显示成片试听条）| 'row' 行级连续播放（REQ-20260916-015）
  // 执行口径的行保留判定（与 cutlist_service.effective_keep_units 对齐 — REQ-20260916-015）：
  // 组级改判 delete → 整条剔除；切分组子段按 mark（组改判 keep → 子段划分作废、整段保留）；
  // 整段组默认保留（改判 split 未重切前维持整段）。试听/连续播放只播保留内容 = 成片效果。
  function cutRowKept(row) {
    var g = row.closest('.slirn-cut-group');
    var act = g ? (g.getAttribute('data-act') || '') : '';
    if (act === 'delete') return false;
    if (row.classList.contains('sub')) {
      if (act === 'keep') return true;
      return (row.getAttribute('data-mark') || 'keep') === 'keep';
    }
    return true;
  }
  // 从 startRow 起到列表末尾的全部保留区间（时间序）— 行级连续播放的跳播序列：
  // 删除洞（子段 mark=delete）与改判删除的整条直接跳过不播
  function cutKeepAllFrom(startRow) {
    var seq = [], started = false;
    cutRows().forEach(function(r) {
      if (r === startRow) started = true;
      if (!started || !cutRowKept(r)) return;
      var s = parseInt(r.getAttribute('data-start-ms'), 10) || 0;
      var e = parseInt(r.getAttribute('data-end-ms'), 10) || 0;
      if (e > s) seq.push({ s: s, e: e, row: r });
    });
    return seq;
  }
  function cutRows() {
    var list = revVis('slirn-cut-list');
    if (!list || !list.offsetParent) return [];  // 面板不可见 → 快捷键整体不生效
    return Array.prototype.slice.call(list.querySelectorAll('.slirn-cut-row'));
  }
  function cutSelIndex(rows) {
    for (var i = 0; i < rows.length; i++) { if (rows[i].classList.contains('kbsel')) return i; }
    return -1;
  }
  function cutMarkSel(row) {
    cutRows().forEach(function(r) { r.classList.toggle('kbsel', r === row); });
    if (row && row.scrollIntoView) row.scrollIntoView({block: 'nearest'});
  }
  function cutSelectRow(idx, seek) {
    var rows = cutRows();
    if (!rows.length) return null;
    var cur = cutSelIndex(rows);
    if (cur < 0) idx = (idx < 0) ? rows.length - 1 : 0;  // 无选中：↓ 第一行，↑ 最后一行
    idx = Math.max(0, Math.min(rows.length - 1, idx));
    var row = rows[idx];
    cutMarkSel(row);
    if (seek) cutPreview(row);
    return row;
  }
  // 行级连续播放（REQ-20260916-013 起，REQ-20260916-015 改为保留内容跳播）：从本行起
  // 按执行口径连续播保留区间 — 播完一条 seek 下一条，删除洞/改判删除整条直接跳过不播
  // （= 成片效果），active 高亮随 timeupdate 跟随切换。点击的行若本身是删除内容，
  // 从其后的下一个保留区间播起。
  function cutPreview(row) {
    if (!row) return;
    cutAuditionBar(null);
    var tid = row.getAttribute('data-task-id') || '';
    var seq = cutKeepAllFrom(row);
    if (!seq.length) {
      // 本行起再无保留内容：退化为定位普通播放（至少让用户听到点过的位置）
      cutKeepSeq = null; cutKeepMode = null;
      playCutAt(tid, parseInt(row.getAttribute('data-start-ms'), 10) || 0);
      return;
    }
    cutKeepSeq = seq;
    cutKeepIdx = 0;
    cutKeepMode = 'row';
    cutMarkSel(row);  // 选中停在点到的行（播放起点可能是其后的保留区间）— 键盘改判目标可预期
    playCutAt(tid, seq[0].s);
  }
  function cutReplayRow() {
    var rows = cutRows();
    var row = rows[cutSelIndex(rows)];
    if (!row) {
      var kmR = revKeysLoad();
      toast('⌨ 先用 ' + revKeyLabel(kmR.prev) + ' / ' + revKeyLabel(kmR.next) + ' 选择一个子段', 'error');
      return;
    }
    cutPreview(row);
  }
  function cutTogglePlay() {
    var v = revVis('slirn-cut-player');
    if (!v) return;
    if (!v.src) {  // 从未播放过：从选中行（或第一行）起点开播
      var rows = cutRows();
      cutPreview(rows[cutSelIndex(rows)] || rows[0]);
      return;
    }
    if (v.paused) { var p = v.play(); if (p && p.catch) p.catch(function() {}); }
    else {
      v.pause();
      if (cutKeepMode === 'group') { cutKeepSeq = null; cutAuditionBar(null); }  // 组试听：手动暂停即退出（REQ-20260916-012）
      // 行级连续播放：暂停保留跳播序列 — 恢复播放后继续跳过删除内容（REQ-20260916-015）
    }
  }
  // 组头 ▶ 试听：本组「执行口径」保留内容连续跳播 — 播完一段自动 seek 下一段
  // （跳变即真实剪辑效果：删掉洞后 keep 段首尾紧贴）。REQ-20260916-015 对齐
  // effective_keep_units：改判删除 → 成片没有这段；改判保留 → 整段一段（子段作废）；
  // 其余按子段 mark=keep。
  function cutPlayGroupKeep(g) {
    if (!g) return;
    var tid = g.getAttribute('data-task-id') || '';
    var act = g.getAttribute('data-act') || '';
    if (act === 'delete') {
      toast('本组已改判删除 — 成片中没有这段内容', 'error');
      return;
    }
    var seq = [];
    var subs = Array.prototype.slice.call(g.querySelectorAll('.slirn-cut-row.sub'));
    if (subs.length && act !== 'keep') {
      subs.forEach(function(r) {
        if ((r.getAttribute('data-mark') || 'keep') === 'keep') {
          var s = parseInt(r.getAttribute('data-start-ms'), 10) || 0;
          var e = parseInt(r.getAttribute('data-end-ms'), 10) || 0;
          if (e > s) seq.push({ s: s, e: e, row: r });
        }
      });
    } else {
      // 整段保留：改判 keep 的切分组（子段划分作废）或整段组 — 首行起点到末行终点一段
      var rowsG = Array.prototype.slice.call(g.querySelectorAll('.slirn-cut-row'));
      if (rowsG.length) {
        var s0 = parseInt(rowsG[0].getAttribute('data-start-ms'), 10) || 0;
        var e0 = parseInt(rowsG[rowsG.length - 1].getAttribute('data-end-ms'), 10) || 0;
        if (e0 > s0) seq.push({ s: s0, e: e0, row: rowsG[0] });
      }
    }
    if (!seq.length) { toast('本组没有保留子段（全部为删除洞）', 'error'); return; }
    cutKeepSeq = seq;
    cutKeepIdx = 0;  // 从第一段开播，一次到底（REQ-20260916-012）
    cutKeepMode = 'group';
    playCutAt(tid, seq[0].s);
    cutMarkSel(seq[0].row);
    var v = revVis('slirn-cut-player');
    if (v) {
      var p = v.play(); if (p && p.catch) p.catch(function() {});
      cutAuditionBar(v);  // 试听条：成片口径连续时间戳
    }
  }
  // 试听条（REQ-20260916-012）：成片时间戳 = 当前段之前全部 keep 段累计时长 + 段内偏移。
  // 跳播时视频原始时间戳会跳变；这条时间轴连续不回退，与当前试听进度严格一致。
  function cutFmtMS(ms) {
    var s = Math.floor(ms / 1000);
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), ss = s % 60;
    var two = function(n) { return (n < 10 ? '0' : '') + n; };
    return (h ? h + ':' + two(m) : m) + ':' + two(ss);
  }
  function cutAuditionBar(v) {
    var wrap = document.getElementById('slirn-cut-player-wrap');
    if (!wrap) return;
    var bar = wrap.querySelector('.slirn-cut-audition');
    if (!cutKeepSeq || !v) { if (bar) bar.hidden = true; return; }
    if (!bar) {
      bar = document.createElement('div');
      bar.className = 'slirn-cut-audition';
      wrap.appendChild(bar);
    }
    var total = 0, before = 0, i;
    for (i = 0; i < cutKeepSeq.length; i++) total += Math.max(0, cutKeepSeq[i].e - cutKeepSeq[i].s);
    for (i = 0; i < cutKeepIdx; i++) before += Math.max(0, cutKeepSeq[i].e - cutKeepSeq[i].s);
    var seg = cutKeepSeq[cutKeepIdx];
    var cur = before + Math.max(0, Math.min(v.currentTime * 1000, seg.e) - seg.s);
    bar.hidden = false;
    bar.textContent = '🎧 试听（成片口径）' + cutFmtMS(cur) + ' / ' + cutFmtMS(total)
      + ' · 保留段 ' + (cutKeepIdx + 1) + '/' + cutKeepSeq.length
      + ' · 对应视频 ' + cutFmtMS(v.currentTime * 1000);
  }
  // 翻转子段标记（未保存纯前端；data-mark 与 data-mark-init 供「有手工修改」判断）
  function cutFlipMark(row) {
    var nw = (row.getAttribute('data-mark') || 'keep') === 'keep' ? 'delete' : 'keep';
    row.setAttribute('data-mark', nw);
    row.classList.toggle('mark-delete', nw === 'delete');
    var el = row.querySelector('.slirn-cut-mark');
    if (el) el.textContent = nw === 'keep' ? '✅ 保留' : '❌ 删除';
    toast(nw === 'keep' ? '已翻转为 ✅ 保留（未保存）' : '已翻转为 ❌ 删除（未保存）');
  }
  // ===== 字幕级改判 K/D/S（REQ-20260916-011 M3）：作用于选中行所属的字幕（组）=====
  // 空=维持原状 · delete=整条删 · keep=原切分不切了整段保留 · split=改为切分（展开编辑区）
  var CUT_ACT_BADGES = { keep: '✅ 已改判保留', delete: '❌ 已改判删除', split: '✂️ 已改判切分' };
  function cutActBadge(g, val) {
    var b = g.querySelector('[data-abadge]');
    if (b) b.textContent = val ? CUT_ACT_BADGES[val] : '维持原状';
  }
  function cutApplyDecision(val) {
    var rows = cutRows();
    var row = rows[cutSelIndex(rows)];
    if (!row) {
      var kmA = revKeysLoad();
      toast('⌨ 先用 ' + revKeyLabel(kmA.prev) + ' / ' + revKeyLabel(kmA.next) + ' 选择一条字幕，再按 '
        + revKeyLabel(kmA.keep) + ' / ' + revKeyLabel(kmA.del) + ' / ' + revKeyLabel(kmA.split) + ' 改判', 'error');
      return;
    }
    var g = row.closest('.slirn-cut-group');
    if (!g) return;
    cutCloseResplit();  // 任何改判动作先收起重切编辑区（改判 split 时再重开 — 防取消后残留）
    if ((g.getAttribute('data-act') || '') === val) {  // 再按同键 → 取消，回到维持原状
      g.removeAttribute('data-act');
      cutActBadge(g, '');
      toast('已取消改判（维持原状）— 未保存');
      return;
    }
    g.setAttribute('data-act', val);
    cutActBadge(g, val);
    if (val === 'split') {
      cutOpenResplit(g);  // 切分需要内容：展开编辑区（预填原文/当前切分后文字）
      toast('✂️ 已改判切分 — 填写切分后内容后点「重新切分」（未保存）');
    } else {
      toast((val === 'keep' ? '✅ 已改判整条保留' : '❌ 已改判整条删除')
        + '（子段标记不再参与执行）— 未保存');
    }
  }
  // ===== 单段重新切分（REQ-20260916-011 M3）：组头下行内展开编辑区 =====
  function cutOpenResplit(g) {
    if (!g) return;
    cutCloseResplit();
    var target = g.getAttribute('data-target') || g.getAttribute('data-orig-text') || '';
    var si = g.getAttribute('data-source-i') || '?';
    var box = document.createElement('div');
    box.className = 'slirn-cut-resplit';
    box.innerHTML =
      '<div class="slirn-cut-resplit-label">✂️ 重新切分第 ' + si + ' 条 — 修改「切分后内容」'
      + '（多写少写都行，对不上的字自动落进删除洞）：</div>'
      + '<input class="slirn-cut-resplit-input" type="text">'
      + '<button class="slirn-btn slirn-btn-xs" data-cut-act="resplit-go">✂️ 按新内容重新切分</button>'
      + '<button class="slirn-btn slirn-btn-xs" data-cut-act="resplit-cancel">取消</button>'
      + '<span class="slirn-cut-resplit-hint">重切会重置本段的手工翻转标记；切分后内容将回写修订决策（本条 → 切分）</span>';
    var head = g.querySelector('.slirn-cut-ghead') || g.querySelector('.slirn-cut-row');
    if (!head) return;
    head.parentNode.insertBefore(box, head.nextSibling);
    var inp = box.querySelector('.slirn-cut-resplit-input');
    inp.value = target.trim();
    inp.focus();
    try { inp.setSelectionRange(inp.value.length, inp.value.length); } catch (err) {}
  }
  function cutCloseResplit() {
    var b = document.querySelector('.slirn-cut-resplit');
    if (b && b.parentNode) b.parentNode.removeChild(b);
  }
  function cutDoResplit(g) {
    var inp = document.querySelector('.slirn-cut-resplit-input');
    if (!g || !inp) return;
    var val = inp.value.trim();
    if (!val) { toast('❌ 请填写切分后内容', 'error'); inp.focus(); return; }
    var tid = g.getAttribute('data-task-id') || '';
    postJSON(SLIRN_API + '/resplit_segment', {
      task_id: tid,
      source_i: parseInt(g.getAttribute('data-source-i'), 10),
      target_text: val,
    }).then(function(r) {
      if (r && r.ok) { toast(r.toast || '已按新内容重新切分'); openWorkbench(tid); }
      else if (r && r.error) toast('❌ ' + r.error, 'error');
    });
  }
  function cutHasManual() {  // 有手工修改？（翻转过 / 有字幕级改判）→ 重新执行前 confirm
    var flipped = cutRows().some(function(r) {
      return (r.getAttribute('data-mark') || '') !== (r.getAttribute('data-mark-init') || '');
    });
    var acted = !!document.querySelector('#slirn-cut-list .slirn-cut-group[data-act]');
    return flipped || acted;
  }
  // 粗剪合成（REQ-20260916-016，可选步骤）：启动后台拼接 → 轮询进度 → 完成注入预览
  function rcFmtDur(sec) {
    var s = Math.floor(sec || 0), m = Math.floor(s / 60), h = Math.floor(m / 60);
    var two = function(n) { return (n < 10 ? '0' : '') + n; };
    return h ? h + ':' + two(m % 60) + ':' + two(s % 60) : m + ':' + two(s % 60);
  }
  function rcInjectPreview(tid, result) {
    var pane = document.getElementById('slirn-wb-pane-rough_compose');
    if (!pane) return;
    var old = pane.querySelector('#slirn-rc-player');
    if (old && old.closest('.slirn-video-wrap')) {  // 已有预览 → 换源刷新即可
      old.src = '/slirn/api/video/' + encodeURIComponent(tid) + '?src=rough_compose&t=' + Date.now();
      old.load();
      var meta = pane.querySelector('.slirn-sub-meta:last-of-type');
      if (meta && result) meta.textContent = '🎞️ 粗剪成片 · ' + result.size_mb + ' MB · ' + rcFmtDur(result.duration);
      return;
    }
    var card = pane.querySelector('.slirn-card');
    if (!card) return;
    var wrap = document.createElement('div');
    wrap.className = 'slirn-video-wrap';
    wrap.style.marginTop = '12px';
    wrap.innerHTML = '<video id="slirn-rc-player" controls preload="metadata" src="'
      + '/slirn/api/video/' + encodeURIComponent(tid) + '?src=rough_compose"></video>';
    card.appendChild(wrap);
    var meta = document.createElement('div');
    meta.className = 'slirn-sub-meta';
    meta.style.marginTop = '8px';
    meta.textContent = result ? ('🎞️ 粗剪成片 · ' + result.size_mb + ' MB · ' + rcFmtDur(result.duration)) : '🎞️ 粗剪成片已生成';
    card.appendChild(meta);
    bindSpeedControl(wrap.querySelector('video'));  // 倍速控件（REQ-20260916-014）
  }
  function rcCompose(btn) {
    var tid = btn.getAttribute('data-task-id') || '';
    var status = document.getElementById('slirn-rc-status');
    var setBtn = function(txt, dis) { btn.textContent = txt; btn.disabled = !!dis; };
    var show = function(msg) { if (status) { status.style.display = ''; status.textContent = msg; } };
    setBtn('🎬 合成中…', true);
    show('⏳ 正在启动合成…');
    postJSON(SLIRN_API + '/compose_rough', {task_id: tid}).then(function(r) {
      if (!r || !r.ok) {
        setBtn('🎬 合成粗剪视频', false);
        show('');
        if (status) status.style.display = 'none';
        toast('❌ ' + (r && r.error ? r.error : '启动失败'), 'error');
        return;
      }
      toast(r.toast || '🎬 合成已启动');
      var timer = setInterval(function() {
        postJSON(SLIRN_API + '/compose_rough_status', {task_id: tid}).then(function(s) {
          if (!s || !s.ok || !s.job) return;
          var j = s.job;
          if (j.state === 'running') {
            var pct = Math.max(1, Math.min(99, Math.round(j.progress || 0)));
            setBtn('🎬 合成中… ' + pct + '%', true);
            show('⏳ 合成进行中 ' + pct + '% — 可继续其它操作，完成后此处自动显示预览');
            return;
          }
          clearInterval(timer);
          if (j.state === 'done') {
            setBtn('🎬 重新合成粗剪视频', false);
            show('✅ 粗剪成片已生成 — 下方预览整体效果');
            toast('✅ 粗剪成片已生成');
            rcInjectPreview(tid, j.result);
          } else {
            setBtn('🎬 重新合成粗剪视频', false);
            show('❌ ' + (j.error || '合成失败'));
            toast('❌ ' + (j.error || '合成失败'), 'error');
          }
        });
      }, 2000);
    });
  }
  // ===== 精剪修订 · 热词替换（REQ-20260916-017）：轮询 + 行撤销/过滤 + 确认保存 =====
  var finePollTimer = null;
  function startFinePolling(tid) {
    if (finePollTimer) { clearInterval(finePollTimer); finePollTimer = null; }
    var update = function() {
      postJSON(SLIRN_API + '/fine_revise_status', {task_id: tid}).then(function(r) {
        if (!r || !r.ok) return;
        var j = r.job || {};
        var el = revVis('slirn-fine-status');
        if (j.state === 'running') {
          if (el) {
            el.style.display = '';
            el.dataset.state = 'running';
            el.innerHTML = '⏳ ' + escapeHtml(j.stage || '分析中')
              + (j.progress ? ' · ' + Math.round(j.progress) + '%' : '')
              + ' · 已耗时 ' + fmtElapsed(j.elapsed_s || 0);
          }
        } else {
          if (finePollTimer) { clearInterval(finePollTimer); finePollTimer = null; }
          if (j.state === 'done') {
            toast('✅ 热词替换分析完成：' + (j.replaced_lines || 0) + ' 行 / '
              + (j.replacements || 0) + ' 处替换');
            openWorkbench(tid);  // 刷新面板（统计 + 高亮列表 + 阶段态）
          } else if (j.state === 'error') {
            if (el) {
              el.style.display = '';
              el.dataset.state = 'error';
              el.innerHTML = '❌ ' + escapeHtml(j.error || '分析失败');
            }
            toast('❌ 热词替换分析失败', 'error');
          }
        }
      });
    };
    update();
    finePollTimer = setInterval(update, 2000);
  }
  function fineAnalyze(btn) {
    var tid = btn.getAttribute('data-task-id') || '';
    var payload = {task_id: tid};
    if (btn.getAttribute('data-has-fine') === '1') {
      if (!window.confirm('重新分析将覆盖现有替换结果，并重置全部撤销决定。确定继续？')) return;
      payload.force = true;
    }
    postJSON(SLIRN_API + '/fine_revise', payload).then(function(r) {
      if (r && r.ok) {
        toast(r.toast || '已开始分析');
        var el = revVis('slirn-fine-status');
        if (el) { el.dataset.state = 'running'; el.style.display = ''; el.innerHTML = '⏳ 已提交…'; }
        startFinePolling(tid);
      } else if (r && r.error) {
        toast('❌ ' + r.error, 'error');
      }
    });
  }
  function fineRow(btn) {
    var id = btn.getAttribute('data-id') || '';
    var list = revVis('slirn-fw-list');
    if (!list) return null;
    var rows = list.querySelectorAll('.slirn-fw-row[data-id]');
    for (var i = 0; i < rows.length; i++) {
      if (rows[i].getAttribute('data-id') === id) return rows[i];
    }
    return null;
  }
  function fineToggle(btn) {  // 行撤销/恢复（纯前端，保存时统一提交）
    var row = fineRow(btn);
    if (!row) return;
    var now = row.getAttribute('data-reverted') === '1' ? 0 : 1;
    row.setAttribute('data-reverted', String(now));
    row.classList.toggle('reverted', now === 1);
    btn.textContent = now === 1 ? '↩️ 已撤销 · 恢复' : '↩️ 撤销替换';
  }
  function fineFilter(btn) {  // 只看有替换的行 / 全部行
    var list = revVis('slirn-fw-list');
    if (!list) return;
    var only = list.classList.toggle('slirn-fw-only-replaced');
    btn.setAttribute('data-shown', only ? '0' : '1');
    btn.textContent = only ? '📋 显示全部保留行'
      : (btn.getAttribute('data-all-text') || '🔍 只看有替换的行');
  }
  function fineSave(btn) {
    var tid = btn.getAttribute('data-task-id') || '';
    var list = revVis('slirn-fw-list');
    if (!list) { toast('❌ 无替换结果列表', 'error'); return; }
    var reverted = [];
    list.querySelectorAll('.slirn-fw-row[data-reverted="1"]').forEach(function(row) {
      reverted.push(row.getAttribute('data-id') || '');
    });
    postJSON(SLIRN_API + '/save_fine_revision', {task_id: tid, reverted_ids: reverted})
      .then(function(r) {
        if (r && r.ok) {
          toast(r.toast || '已确认保存');
          openWorkbench(tid);  // 刷新阶段态（done）+ 统计
        } else if (r && r.error) {
          toast('❌ ' + r.error, 'error');
        }
      });
  }
  function playFineAt(tid, startMs) {  // 行定位播放（与修订/切分同模式，独立播放器防 id 撞车）
    var wrap = revVis('slirn-fine-player-wrap');
    var v = revVis('slirn-fine-player');
    if (!v) { toast('❌ 播放器未就绪', 'error'); return; }
    if (wrap) wrap.style.display = '';
    if (!v.src) { v.src = SLIRN_API + '/video/' + encodeURIComponent(tid); v.load(); }
    var goF = function() {
      try { v.currentTime = (startMs || 0) / 1000; } catch (err) {}
      var p = v.play();
      if (p && p.catch) p.catch(function() {});
    };
    if (v.readyState >= 1) goF();
    else v.addEventListener('loadedmetadata', goF, {once: true});
  }
  function bindFineRows(tid) {  // 行点击定位播放（撤销按钮自身不触发）
    var list = revVis('slirn-fw-list');
    if (!list) return;
    list.querySelectorAll('.slirn-fw-row[data-start-ms]').forEach(function(row) {
      row.addEventListener('click', function(ev) {
        if (ev.target && ev.target.closest('button')) return;
        playFineAt(tid, parseInt(row.getAttribute('data-start-ms'), 10) || 0);
      });
    });
  }
  // 保存切分决策：全量收集子段 mark + 组级 action（改判 split 附切分后内容）→ 落盘
  function cutSave(btn) {
    var tid = btn.getAttribute('data-task-id') || '';
    var marks = {};
    cutRows().forEach(function(r) {
      if (r.classList.contains('sub') && r.getAttribute('data-id'))
        marks[r.getAttribute('data-id')] = r.getAttribute('data-mark') || 'keep';
    });
    var acts = {}, targets = {}, badSplit = null;
    document.querySelectorAll('#slirn-cut-list .slirn-cut-group[data-act]').forEach(function(g) {
      acts[g.getAttribute('data-source-i') || ''] = g.getAttribute('data-act');
    });
    // 改判切分必须有切分后内容：组 data-target（重切/恢复）或当前编辑区输入值
    document.querySelectorAll('#slirn-cut-list .slirn-cut-group[data-act="split"]').forEach(function(g) {
      var tv = (g.getAttribute('data-target') || '').trim();
      if (!tv) {
        var inp = document.querySelector('.slirn-cut-resplit-input');
        if (inp && inp.closest('.slirn-cut-group') === g) tv = inp.value.trim();
      }
      if (!tv) badSplit = g.getAttribute('data-source-i');
      targets[g.getAttribute('data-source-i') || ''] = tv;
    });
    if (badSplit !== null) {
      toast('❌ 第 ' + badSplit + ' 条已改判切分，但未填写切分后内容 — 按 S 或点 ✂️ 填写后重切', 'error');
      return;
    }
    postJSON(SLIRN_API + '/save_cut_decisions',
             {task_id: tid, manual_marks: marks, actions: acts, split_targets: targets})
      .then(function(r) {
        if (r && r.ok) { toast(r.toast || '切分决策已保存'); openWorkbench(tid); }
        else if (r && r.error) toast('❌ ' + r.error, 'error');
      });
  }
  // 播放器绑定：timeupdate 三合一 — 试听跳播 / 预播段尾自动停 / 高亮跟随
  function bindCutPlayer() {
    var v = revVis('slirn-cut-player');
    var list = revVis('slirn-cut-list');
    bindSpeedControl(v);  // 倍速显示与控制（REQ-20260916-014）
    if (v && list && !v.dataset.bound) {
      v.dataset.bound = '1';
      var rows = Array.prototype.slice.call(list.querySelectorAll('.slirn-cut-row'));
      var lastHit = -1;
      v.addEventListener('timeupdate', function() {
        var tms = v.currentTime * 1000;
        // ① 试听跳播（REQ-20260916-012 索引跟踪）：只看当前段 — 播过当前 keep
        // 段尾 → seek 下一段起点；播完最后一段 → 停。绝不从 0 重扫：旧行为里
        // 跳到下一段后 tms ≥ 前段尾恒成立，会立刻再跳、末组折返，段间无限
        // 乒乓反复播放且永不结束。
        // 序列来源（REQ-20260916-015）：组头试听（cutKeepMode='group'，显示成片
        // 试听条）或行级连续播放（'row'，只播保留内容、不显示试听条）。
        if (cutKeepSeq) {
          var seg = cutKeepSeq[cutKeepIdx];
          if (tms < seg.s - 500) {  // 用户回拖：重定位到 tms 所在段
            while (cutKeepIdx > 0 && tms < cutKeepSeq[cutKeepIdx].s) cutKeepIdx--;
            seg = cutKeepSeq[cutKeepIdx];
          }
          if (tms >= seg.e - 30) {
            if (cutKeepIdx + 1 < cutKeepSeq.length) {
              cutKeepIdx++;
              var nxt = cutKeepSeq[cutKeepIdx];
              try { v.currentTime = nxt.s / 1000; } catch (err) {}
              cutMarkSel(nxt.row);
              if (cutKeepMode === 'group') cutAuditionBar(v);
              return;  // 跳转后的首个 timeupdate 再走高亮，防旧位置误亮
            }
            v.pause(); cutKeepSeq = null; cutKeepMode = null;
            cutAuditionBar(null);  // 播放完毕：试听结束收起
          } else {
            if (cutKeepMode === 'group') cutAuditionBar(v);  // 试听时间戳与当前段保持一致
          }
        }
        // ② 高亮跟随（与字幕/修订阶段同款 active：段间缝隙保持前一段亮）
        var hit = -1;
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
        for (var j = 0; j < rows.length; j++) rows[j].classList.toggle('active', j === hit);
        if (hit >= 0 && rows[hit] && rows[hit].scrollIntoView) rows[hit].scrollIntoView({block: 'nearest'});
      });
    }
  }

  function bindRevPlayer() {
    var v = revVis('slirn-rev-player');
    var list = revVis('slirn-rev-list');
    bindSpeedControl(v);  // 倍速显示与控制（REQ-20260916-014）
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
    applyRevKeysState();  // 提示条跟随自定义键位（localStorage — REQ-20260916-005）
    revFilterSync();  // 过滤条：渲染 chips 计数 + 应用上次筛选（localStorage — REQ-20260916-010）
  }

  // ===== 字幕修订快捷键（REQ-20260916-004）：听 → 判 → 标记 → 下一条 =====
  // ↑↓ 选行（跳到行起点，播放态跟随）· 空格 播放/暂停 · R 重播本行
  // K 保留 / D 删除（标记后自动下一条）· S 切分（展开详情+聚焦内容输入，不跳行）
  // Esc 从切分修剪后内容输入框退回列表；输入框/下拉聚焦时不劫持按键
  function revRows() {
    var list = revVis('slirn-rev-list');
    if (!list || !list.offsetParent) return [];  // 列表不可见 → 快捷键整体不生效
    return Array.prototype.slice.call(list.querySelectorAll('.slirn-rev-row'));
  }
  function revSelIndex(rows) {
    for (var i = 0; i < rows.length; i++) { if (rows[i].classList.contains('kbsel')) return i; }
    return -1;
  }
  function revMarkSel(row) {
    revRows().forEach(function(r) { r.classList.toggle('kbsel', r === row); });
    if (row && row.scrollIntoView) row.scrollIntoView({block: 'nearest'});
  }
  function revSelectRow(idx, seek) {
    var rows = revNavRows();  // 过滤后可见行（REQ-20260916-010）：↑↓ 跳过被筛掉的行
    if (!rows.length) return null;
    var cur = revSelIndex(rows);
    if (cur < 0) idx = (idx < 0) ? rows.length - 1 : 0;  // 无选中：↓ 取第一行，↑ 取最后一行
    idx = Math.max(0, Math.min(rows.length - 1, idx));
    var row = rows[idx];
    revMarkSel(row);
    if (seek) {
      var v = revVis('slirn-rev-player');
      if (v && v.src) {  // 播放器加载过才跳（暂停时不强制播放，按空格续听）
        try { v.currentTime = (parseInt(row.getAttribute('data-start-ms'), 10) || 0) / 1000; } catch (err) {}
      }
    }
    return row;
  }
  function revTogglePlay() {
    var v = revVis('slirn-rev-player');
    if (!v) return;
    if (!v.src) {  // 从未播放过：从选中行（或第一行）起点开播
      var rows = revNavRows();
      var row = rows[revSelIndex(rows)] || rows[0];
      if (row) playRevAt(row.getAttribute('data-task-id') || '',
        parseInt(row.getAttribute('data-start-ms'), 10) || 0);
      return;
    }
    if (v.paused) { var p = v.play(); if (p && p.catch) p.catch(function() {}); }
    else { v.pause(); }
  }
  function revReplayRow() {
    var rows = revNavRows();
    var row = rows[revSelIndex(rows)];
    if (!row) {
      var kmR = revKeysLoad();
      toast('⌨ 先用 ' + revKeyLabel(kmR.prev) + ' / ' + revKeyLabel(kmR.next) + ' 选择一条字幕', 'error');
      return;
    }
    playRevAt(row.getAttribute('data-task-id') || '', parseInt(row.getAttribute('data-start-ms'), 10) || 0);
  }
  function revApplyDecision(val) {
    var rows = revNavRows();
    var idx = revSelIndex(rows);
    if (idx < 0) {
      var kmA = revKeysLoad();
      toast('⌨ 先用 ' + revKeyLabel(kmA.prev) + ' / ' + revKeyLabel(kmA.next) + ' 选择一条字幕，再按 '
        + revKeyLabel(kmA.keep) + ' / ' + revKeyLabel(kmA.del) + ' / ' + revKeyLabel(kmA.split) + ' 标记', 'error');
      return;
    }
    var row = rows[idx];
    var sel = row.querySelector('.slirn-rev-select');
    if (!sel) return;
    sel.value = val;
    sel.dispatchEvent(new Event('change', {bubbles: true}));
    if (val === 'split') {
      // 切分需要人工给出修剪后文本：展开详情块、光标移到输入框，不自动跳行（写完按 Esc 返回）
      row.classList.add('open');
      var tg = row.querySelector('.slirn-rev-toggle');
      if (tg) tg.textContent = '▴';
      var note = row.querySelector('.slirn-rev-note-input');
      if (note) {
        note.focus();
        try { note.setSelectionRange(note.value.length, note.value.length); } catch (err) {}
      }
      toast('✂️ 已标记切分 — 填写切分修剪后内容后按 ' + revKeyLabel(revKeysLoad().esc) + ' 返回列表');
    } else {
      revSelectRow(idx + 1, true);  // 保留/删除：标记即过，自动下一条
    }
  }

  // ===== 修订列表状态过滤（REQ-20260916-010）=====
  // 两个维度：建议状态（模型判定）/ 决策状态（用户选择）。决策维度按「实质
  // 类别」匹配 — accept 的实质 = 模型建议类别（与切分清单 _final_kind 同源）：
  // 筛「切分」时「采纳+建议切分」的行也命中。筛选只藏行不删行，
  // 「保存修订决策」仍收集全部行（revRows 不受过滤影响）。
  var REV_FILTER_SUGG = [
    ['keep', '保留'], ['delete', '删除'], ['split', '切分'],
    ['fix', '更正'], ['review', '复核'],
  ];
  var REV_FILTER_DEC = [
    ['pending', '未决策'], ['accept', '采纳建议'],
    ['keep', '保留'], ['delete', '删除'], ['split', '切分'], ['fix', '内容更正'],
  ];
  function revFilterLoad() {
    try { return JSON.parse(localStorage.getItem('slirnRevFilter') || 'null'); }
    catch (err) { return null; }
  }
  var revFilter = revFilterLoad() || { dim: 'sugg', val: 'all' };
  if (revFilter.dim !== 'sugg' && revFilter.dim !== 'dec') revFilter = { dim: 'sugg', val: 'all' };
  function revFilterSave() {
    try { localStorage.setItem('slirnRevFilter', JSON.stringify(revFilter)); } catch (err) {}
  }
  function revNavRows() {  // 过滤后仍可见的行（导航/标记走这里；保存收集仍用 revRows 全量）
    return revRows().filter(function(r) { return r.style.display !== 'none'; });
  }
  function revFilterMatch(row) {
    if (revFilter.val === 'all') return true;
    if (revFilter.dim === 'sugg') return (row.getAttribute('data-sugg') || '') === revFilter.val;
    if (revFilter.val === 'pending' || revFilter.val === 'accept')
      return (row.getAttribute('data-decision') || '') === revFilter.val;
    return (row.getAttribute('data-final') || '') === revFilter.val;  // 实质口径
  }
  function revFilterCountOf(rows, dim, val) {
    return rows.filter(function(r) {
      if (val === 'all') return true;
      if (dim === 'sugg') return (r.getAttribute('data-sugg') || '') === val;
      if (val === 'pending' || val === 'accept')
        return (r.getAttribute('data-decision') || '') === val;
      return (r.getAttribute('data-final') || '') === val;
    }).length;
  }
  function revFilterRender() {
    var chips = revVis('slirn-rev-filter-chips');
    if (!chips) return;
    var rows = revRows();
    var defs = revFilter.dim === 'sugg' ? REV_FILTER_SUGG : REV_FILTER_DEC;
    var html = '<button type="button" class="slirn-rev-chip' + (revFilter.val === 'all' ? ' active' : '')
      + '" data-rev-filter-val="all">全部 ' + rows.length + '</button>';
    defs.forEach(function(d) {
      var n = revFilterCountOf(rows, revFilter.dim, d[0]);
      var solid = revFilter.dim === 'dec' && d[0] !== 'pending' && d[0] !== 'accept';
      html += '<button type="button" class="slirn-rev-chip' + (revFilter.val === d[0] ? ' active' : '')
        + '" data-rev-filter-val="' + d[0] + '"'
        + (solid ? ' title="实质口径：含「采纳建议」且建议为此类的行"' : '')
        + '>' + d[1] + ' ' + n + '</button>';
    });
    chips.innerHTML = html;
    document.querySelectorAll('[data-rev-filter-dim]').forEach(function(b) {
      b.classList.toggle('active', b.getAttribute('data-rev-filter-dim') === revFilter.dim);
    });
  }
  function revFilterApply() {
    if (!revVis('slirn-rev-filter')) return;
    var rows = revRows(), shown = 0;
    rows.forEach(function(r) {
      var ok = revFilterMatch(r);
      r.style.display = ok ? '' : 'none';
      if (ok) shown++;
    });
    var cnt = revVis('slirn-rev-filter-count');
    if (cnt) cnt.textContent = '显示 ' + shown + ' / ' + rows.length + ' 条';
  }
  function revFilterSync() { revFilterRender(); revFilterApply(); }

  // ===== 快捷键自定义（REQ-20260916-005）：localStorage 键 slirnRevKeys =====
  // 每个人习惯不同：点击提示条「⚙ 自定义」→ 点键帽 → 按新键。键位图持久化，
  // 提示条/引导 toast 跟随当前键位渲染；冲突拒绝、可恢复默认。
  var REV_KEY_ACTIONS = [
    { id: 'prev',   name: '上一条',                   def: 'arrowup' },
    { id: 'next',   name: '下一条',                   def: 'arrowdown' },
    { id: 'play',   name: '播放 / 暂停',               def: ' ' },
    { id: 'replay', name: '重播本行',                  def: 'r' },
    { id: 'keep',   name: '保留（标记后自动下一条）',   def: 'k' },
    { id: 'del',    name: '删除（标记后自动下一条）',   def: 'd' },
    { id: 'split',  name: '切分（展开详情并聚焦内容）', def: 's' },
    { id: 'esc',    name: '退出说明输入框',             def: 'escape' }
  ];
  var REV_KEY_ALLOWED = ['arrowup', 'arrowdown', 'arrowleft', 'arrowright', 'escape',
    'enter', 'backspace', 'delete', 'home', 'end', 'pageup', 'pagedown'];  // 单字符键另判
  function revKeysLoad() {
    var km = {};
    REV_KEY_ACTIONS.forEach(function(a) { km[a.id] = a.def; });
    try {
      var raw = JSON.parse(localStorage.getItem('slirnRevKeys') || 'null');
      if (raw && typeof raw === 'object') {
        REV_KEY_ACTIONS.forEach(function(a) {
          var v = raw[a.id];
          // 脏数据（非字符串/重复键）→ 该动作回退默认，先到先得
          if (typeof v === 'string' && v) {
            var used = REV_KEY_ACTIONS.some(function(b) { return km[b.id] === v; });
            if (!used) km[a.id] = v;
          }
        });
      }
    } catch (err) {}
    return km;
  }
  function revKeyLabel(k) {
    var map = { ' ': '空格', 'arrowup': '↑', 'arrowdown': '↓', 'arrowleft': '←',
      'arrowright': '→', 'escape': 'Esc', 'enter': 'Enter', 'backspace': '⌫',
      'delete': 'Del', 'home': 'Home', 'end': 'End', 'pageup': 'PgUp', 'pagedown': 'PgDn' };
    var s = map[k] || ((k && k.length === 1) ? k.toUpperCase() : k);
    return String(s).replace(/[&<>"']/g, function(c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  // 提示条跟随当前键位重渲染（详情页/工作台两份一并更新）
  function applyRevKeysState() {
    var km = revKeysLoad();
    document.querySelectorAll('.slirn-rev-kbhint').forEach(function(el) {
      el.innerHTML = '⌨ 快捷键：<kbd>' + revKeyLabel(km.prev) + '</kbd><kbd>'
        + revKeyLabel(km.next) + '</kbd> 上一条 / 下一条 · <kbd>' + revKeyLabel(km.play)
        + '</kbd> 播放 / 暂停 · <kbd>' + revKeyLabel(km.replay) + '</kbd> 重播本行 · <kbd>'
        + revKeyLabel(km.keep) + '</kbd> 保留 · <kbd>' + revKeyLabel(km.del)
        + '</kbd> 删除（标记后自动下一条）· <kbd>' + revKeyLabel(km.split)
        + '</kbd> 切分（展开详情聚焦内容） · <kbd>' + revKeyLabel(km.esc)
        + '</kbd> 退出输入框'
        + ' · <span class="slirn-revkeys-open" data-action="revkeys-open" role="button" tabindex="0">⚙ 自定义</span>';
    });
  }
  var revKeysRec = null;  // 正在录制换绑的动作 id（null = 未在录制）
  function revKeysModalOpen() { return !!document.getElementById('slirn-revkeys-modal'); }
  function revKeysOpenModal() {
    if (revKeysModalOpen()) return;
    var bd = document.createElement('div');
    bd.id = 'slirn-revkeys-modal';
    bd.className = 'slirn-revkeys-backdrop';
    bd.innerHTML = '<div class="slirn-revkeys-modal" role="dialog" aria-label="快捷键自定义">'
      + '<div class="slirn-revkeys-title">⌨ 快捷键自定义'
      + '<span class="slirn-revkeys-sub">点击键帽 → 按新键更换</span></div>'
      + '<div class="slirn-revkeys-rows"></div>'
      + '<div class="slirn-revkeys-foot">'
      + '<span class="slirn-revkeys-tip">Esc 取消录制 · 不支持组合键</span>'
      + '<button class="slirn-btn" data-action="revkeys-reset">↩️ 恢复默认</button>'
      + '<button class="slirn-btn slirn-btn-primary" data-action="revkeys-close">✅ 完成</button>'
      + '</div></div>';
    bd.addEventListener('click', function(ev) { if (ev.target === bd) revKeysCloseModal(); });
    document.body.appendChild(bd);
    revKeysRec = null;
    revKeysRenderRows();
  }
  function revKeysCloseModal() {
    var m = document.getElementById('slirn-revkeys-modal');
    if (m) m.remove();
    revKeysRec = null;
  }
  function revKeysRenderRows(flashConflict) {
    var box = document.querySelector('#slirn-revkeys-modal .slirn-revkeys-rows');
    if (!box) return;
    var km = revKeysLoad();
    var html = '';
    REV_KEY_ACTIONS.forEach(function(a) {
      var cls = 'slirn-revkeys-key';
      if (revKeysRec === a.id) cls += ' rec';
      if (flashConflict === a.id) cls += ' conflict';
      html += '<div class="slirn-revkeys-row"><span class="slirn-revkeys-name">' + a.name
        + '</span><span class="' + cls + '" data-revkey="' + a.id + '">'
        + (revKeysRec === a.id ? '按新键…' : revKeyLabel(km[a.id])) + '</span></div>';
    });
    box.innerHTML = html;
  }
  // 录制监听（capture）：弹窗打开时接管全部按键，列表快捷键让位
  document.addEventListener('keydown', function(e) {
    if (!revKeysModalOpen()) return;
    e.preventDefault();
    e.stopPropagation();
    if (e.ctrlKey || e.metaKey || e.altKey) {
      toast('❌ 不支持组合键（避免与浏览器/系统冲突）', 'error');
      return;
    }
    if (e.key === 'Shift' || e.key === 'Control' || e.key === 'Alt'
      || e.key === 'Meta' || e.key === 'Dead') return;  // 等待实际按键
    if (e.repeat) return;
    if (!revKeysRec) { if (e.key === 'Escape') revKeysCloseModal(); return; }
    // Esc 取消录制；但 esc 动作本身允许绑 Esc（否则永远绑不回去）
    if (e.key === 'Escape' && revKeysRec !== 'esc') {
      revKeysRec = null;
      revKeysRenderRows();
      return;
    }
    var k = (e.key || '').toLowerCase();
    var okChar = (k.length === 1 && /[\x20-\x7e]/.test(k));  // 可打印 ASCII 单键
    if (!okChar && REV_KEY_ALLOWED.indexOf(k) < 0) {
      toast('❌ 该键不可用作快捷键', 'error');
      return;
    }
    var km = revKeysLoad();
    for (var i = 0; i < REV_KEY_ACTIONS.length; i++) {
      var a = REV_KEY_ACTIONS[i];
      if (a.id !== revKeysRec && km[a.id] === k) {
        revKeysRenderRows(a.id);  // 冲突行闪红
        toast('❌ 「' + revKeyLabel(k) + '」已用于「' + a.name + '」，请换一个键', 'error');
        return;
      }
    }
    km[revKeysRec] = k;
    try { localStorage.setItem('slirnRevKeys', JSON.stringify(km)); } catch (err) {}
    revKeysRec = null;
    revKeysRenderRows();
    applyRevKeysState();  // 提示条立即跟随新键位
  }, true);

  document.addEventListener('keydown', function(e) {
    if (revKeysModalOpen()) return;  // 自定义弹窗打开 → 录制监听器接管
    // 带修饰键的组合留给浏览器/系统
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    var k = (e.key || '').toLowerCase();
    var km = revKeysLoad();
    // 退回键：手动说明输入框 → 退回列表快捷键状态（焦点在输入框内也生效，键可改绑）
    if (k === km.esc) {
      var ae = document.activeElement;
      if (ae && ae.classList && ae.classList.contains('slirn-rev-note-input')) {
        ae.blur();
        e.preventDefault();
      }
      return;
    }
    // 文本输入中不劫持（下拉的方向键保留原生行为）
    var t = e.target;
    if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return;
    var rows = revNavRows();  // 快捷键只作用于过滤后可见的行（REQ-20260916-010）
    if (!rows.length) return;  // 修订列表不可见/无行 → 快捷键不生效
    // 反查当前键位对应的动作（按动作表顺序，先定义者优先）
    var act = null;
    for (var i = 0; i < REV_KEY_ACTIONS.length; i++) {
      if (km[REV_KEY_ACTIONS[i].id] === k) { act = REV_KEY_ACTIONS[i].id; break; }
    }
    if (!act) return;
    // 长按只放行导航（快速滚动）；其余动作防连环触发
    if (e.repeat && act !== 'prev' && act !== 'next') return;
    e.preventDefault();
    if (act === 'prev') { revSelectRow(revSelIndex(rows) - 1, true); }
    else if (act === 'next') { revSelectRow(revSelIndex(rows) + 1, true); }
    else if (act === 'play') { revTogglePlay(); }
    else if (act === 'replay') { revReplayRow(); }
    else if (act === 'keep') { revApplyDecision('keep'); }
    else if (act === 'del') { revApplyDecision('delete'); }
    else if (act === 'split') { revApplyDecision('split'); }
  });

  // ===== 切分修剪快捷键（REQ-20260916-011）：与修订区共用键位/自定义，按可见面板分发 =====
  // ↑↓ 选行（跳段起点播放）· 空格 播放/暂停 · R 重播本段（播到段尾自动停）
  // K/D/S 字幕级改判（作用于选中行所属字幕：保留/删除/切分，再按同键取消）
  // 修订列表可见时本 handler 自然让位（cutRows 为空），两区互不抢键。
  document.addEventListener('keydown', function(e) {
    if (revKeysModalOpen()) return;  // 键位自定义弹窗打开 → 录制监听器接管
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    var k = (e.key || '').toLowerCase();
    var km = revKeysLoad();
    // Esc：退出重切编辑区（焦点在输入框内也生效，键可改绑）
    if (k === km.esc) {
      var aeC = document.activeElement;
      if (aeC && aeC.classList && aeC.classList.contains('slirn-cut-resplit-input')) {
        cutCloseResplit();
        e.preventDefault();
      }
      return;
    }
    var t = e.target;
    if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return;
    var rows = cutRows();
    if (!rows.length) return;  // 切分面板不可见 → 快捷键不生效
    var act = null;
    for (var i = 0; i < REV_KEY_ACTIONS.length; i++) {
      if (km[REV_KEY_ACTIONS[i].id] === k) { act = REV_KEY_ACTIONS[i].id; break; }
    }
    if (!act || act === 'esc') return;
    if (e.repeat && act !== 'prev' && act !== 'next') return;  // 长按只放行导航
    e.preventDefault();
    if (act === 'prev') { cutSelectRow(cutSelIndex(rows) - 1, true); }
    else if (act === 'next') { cutSelectRow(cutSelIndex(rows) + 1, true); }
    else if (act === 'play') { cutTogglePlay(); }
    else if (act === 'replay') { cutReplayRow(); }
    else if (act === 'keep') { cutApplyDecision('keep'); }
    else if (act === 'del') { cutApplyDecision('delete'); }
    else if (act === 'split') { cutApplyDecision('split'); }
  });

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

    // 修订行点击定位播放（select/input/button 与详情块上的点击不触发）
    var revRow = e.target.closest('.slirn-rev-row');
    if (revRow && !e.target.closest('select, input, button, a, .slirn-rev-detail')) {
      e.preventDefault();
      revMarkSel(revRow);  // 鼠标与键盘共享「当前行」（REQ-20260916-004）
      var tidR = revRow.getAttribute('data-task-id') || '';
      var startMsR = parseInt(revRow.getAttribute('data-start-ms'), 10) || 0;
      playRevAt(tidR, startMsR);
      return;
    }

    // 切分修剪（REQ-20260916-011）：mark 徽章点击=翻转 · 组头试听钮=keep 连续跳播 ·
    // 行点击=段级预播（播到段尾自动停）；旧「点行续播」由预播替代
    var cutMark = e.target.closest('.slirn-cut-mark');
    if (cutMark) {
      e.preventDefault();
      var rowM = cutMark.closest('.slirn-cut-row');
      if (rowM) { cutMarkSel(rowM); cutFlipMark(rowM); }
      return;
    }
    var cutListen = e.target.closest('[data-cut-act="play-keep"]');
    if (cutListen) {
      e.preventDefault();
      cutPlayGroupKeep(cutListen.closest('.slirn-cut-group'));
      return;
    }
    // 单段重新切分（REQ-20260916-011）：入口展开 / 执行 / 取消
    var cutRsg = e.target.closest('[data-cut-act="resplit"]');
    if (cutRsg) {
      e.preventDefault();
      cutOpenResplit(cutRsg.closest('.slirn-cut-group'));
      return;
    }
    var cutRsgGo = e.target.closest('[data-cut-act="resplit-go"]');
    if (cutRsgGo) {
      e.preventDefault();
      cutDoResplit(cutRsgGo.closest('.slirn-cut-group'));
      return;
    }
    var cutRsgNo = e.target.closest('[data-cut-act="resplit-cancel"]');
    if (cutRsgNo) {
      e.preventDefault();
      cutCloseResplit();
      return;
    }
    var cutRow = e.target.closest('.slirn-cut-row');
    if (cutRow && !e.target.closest('button, a')) {
      e.preventDefault();
      cutMarkSel(cutRow);  // 鼠标与键盘共享「当前行」
      cutPreview(cutRow);
      return;
    }

    // 快捷键自定义：键帽点击进入录制（REQ-20260916-005；键帽无 data-action，先于其判断）
    var rk = e.target.closest('[data-revkey]');
    if (rk) {
      e.preventDefault();
      revKeysRec = rk.getAttribute('data-revkey');
      revKeysRenderRows();
      return;
    }

    // 状态过滤：维度切换 / 筛选项点选（REQ-20260916-010；chips 无 data-action，先于其判断）
    var fdim = e.target.closest('[data-rev-filter-dim]');
    if (fdim) {
      e.preventDefault();
      revFilter.dim = fdim.getAttribute('data-rev-filter-dim');
      revFilter.val = 'all';
      revFilterSave();
      revFilterSync();
      return;
    }
    var fval = e.target.closest('[data-rev-filter-val]');
    if (fval) {
      e.preventDefault();
      revFilter.val = fval.getAttribute('data-rev-filter-val');
      revFilterSave();
      revFilterSync();
      return;
    }

    var target = e.target.closest('[data-action]');
    if (!target) return;
    var action = target.getAttribute('data-action');
    e.preventDefault();

    if (action === 'rigor-prompt-preset') {
      // 自定义严谨性：载入/恢复 高/中/低 任一档底稿（REQ-20260916-009）；
      // 已有编辑内容且不同 → 确认后再覆盖；草稿同步覆盖
      var wrapR = document.getElementById('slirn-rigor-custom');
      var taR = wrapR ? wrapR.querySelector('.slirn-rigor-custom-text') : null;
      if (taR) {
        var keyR = target.getAttribute('data-preset') || 'high';
        var textR = wrapR.getAttribute('data-prompt-' + keyR) || '';
        var nameR = target.textContent.trim();
        if (taR.value && taR.value !== textR &&
            !window.confirm('载入底稿「' + nameR + '」会覆盖当前编辑内容。确定继续？')) return;
        taR.value = textR;
        try { localStorage.setItem('slirnRevCustomPrompt', textR); } catch (err) {}
        taR.focus();
        toast('已载入底稿「' + nameR + '」，可在此基础上修改');
      }
      return;
    }
    if (action === 'revkeys-open') {
      revKeysOpenModal();
      return;
    }
    if (action === 'revkeys-close') {
      revKeysCloseModal();
      return;
    }
    if (action === 'revkeys-reset') {
      try { localStorage.removeItem('slirnRevKeys'); } catch (err) {}
      revKeysRec = null;
      revKeysRenderRows();
      applyRevKeysState();
      toast('↩️ 快捷键已恢复默认键位');
      return;
    }

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
    else if (action === 'play-cut-video') {
      playCutAt(target.getAttribute('data-task-id') || '', 0);
    }
    else if (action === 'build-cutlist') {
      // 生成切分清单并完成本阶段（REQ-20260916-008）：落盘 + 推进 ROUGH_CUT_DONE，
      // openWorkbench 全刷新（阶段条 rough_cut → done、精剪字幕 → current）
      var tidC = target.getAttribute('data-task-id') || '';
      postJSON(SLIRN_API + '/build_cutlist', {task_id: tidC})
        .then(function(r) {
          if (r && r.ok) {
            toast(r.toast || '切分清单已生成');
            openWorkbench(tidC);
          } else if (r && r.error) {
            toast('❌ ' + r.error, 'error');
          }
        });
    }
    else if (action === 'save-cut-decisions') {
      // 保存切分决策（REQ-20260916-011）：翻转 manual_marks + 字幕级改判 actions
      cutSave(target);
    }
    else if (action === 'compose-rough') {
      // 粗剪合成（REQ-20260916-016，可选）：后台拼接保留区间成片
      rcCompose(target);
    }
    else if (action === 'fine-revise') {
      // 精剪修订热词替换（REQ-20260916-017）：后台分析 → 轮询 → 刷新
      fineAnalyze(target);
    }
    else if (action === 'fine-toggle') {
      fineToggle(target);
    }
    else if (action === 'fine-filter') {
      fineFilter(target);
    }
    else if (action === 'save-fine-revision') {
      fineSave(target);
    }
    else if (action === 'rebuild-cutlist') {
      // 重新执行切分修剪（REQ-20260916-011）：按最新修订决策整单重算落盘，
      // 清除全部手工决策（与「重新分析=重置」同一哲学）；有手工修改先 confirm
      var tidRb = target.getAttribute('data-task-id') || '';
      if (cutHasManual() &&
          !window.confirm('重新执行将按最新修订决策整单重算，并清除全部手工决策（子段翻转与字幕级改判）。确定继续？')) return;
      postJSON(SLIRN_API + '/build_cutlist', {task_id: tidRb})
        .then(function(r) {
          if (r && r.ok) {
            toast(r.toast || '已按最新决策重新执行切分修剪');
            openWorkbench(tidRb);
          } else if (r && r.error) {
            toast('❌ ' + r.error, 'error');
          }
        });
    }
    else if (action === 'revise-subtitle') {
      var tidV = target.getAttribute('data-task-id') || '';
      // 严谨性级别必选（REQ-20260916-003）：先于覆盖确认 — 没选等级就不发请求
      var rigorEl = document.querySelector('input[name="slirn-rev-rigor"]:checked');
      if (!rigorEl) {
        toast('❌ 请先选择分析严谨性级别（高 / 中 / 低）', 'error');
        var det = document.getElementById('slirn-rev-rigor-box');
        if (det) det.open = true;
        var cards = document.querySelector('.slirn-rigor-cards');
        if (cards) cards.scrollIntoView({behavior: 'smooth', block: 'center'});
        return;
      }
      var payloadV = {task_id: tidV, rigor: rigorEl.value};
      if (rigorEl.value === 'custom') {  // 自定义档带上用户提示词（空白 → 服务端回退底稿）
        var wrapC = document.getElementById('slirn-rigor-custom');
        var taC = wrapC ? wrapC.querySelector('.slirn-rigor-custom-text') : null;
        payloadV.custom_prompt = taC ? taC.value : '';
      }
      try { localStorage.setItem('slirnRevRigor', rigorEl.value); } catch (err) {}
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
      revRows().forEach(function(row) {  // 只收集可见列表（详情页+工作台同屏时防串数据）
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
    else if (action === 'wb-toggle-stages') {
      var hostW = document.getElementById('slirn-tab-workbench-inner');
      if (hostW) {
        var nowCollapsed = !hostW.classList.contains('wb-stages-collapsed');
        hostW.classList.toggle('wb-stages-collapsed', nowCollapsed);
        try { localStorage.setItem('slirnWbStagesCollapsed', nowCollapsed ? '1' : '0'); } catch (err) {}
      }
    }
    else if (action === 'rev-detail') {
      var rowD = target.closest('.slirn-rev-row');
      if (rowD) {
        var opened = rowD.classList.toggle('open');
        target.textContent = opened ? '▴' : '▾';
      }
    }
    else if (action === 'open-llm-settings') {
      openLLMSettings();
    }
    else if (action === 'llm-close') {
      var llmModal = document.getElementById('slirn-llm-modal');
      if (llmModal) llmModal.remove();
    }
    else if (action === 'llm-add') {
      var gv = function(elId) { return ((document.getElementById(elId) || {}).value || '').trim(); };
      var protoSel = document.getElementById('slirn-llm-in-proto');
      var editing = LLM_EDIT_ID;  // 非空 = 当前是编辑模式 → 提交修改
      var payload = {
        id: gv('slirn-llm-in-id'), provider: gv('slirn-llm-in-provider'),
        base_url: gv('slirn-llm-in-url'), api_key_env: gv('slirn-llm-in-env'),
        protocol: protoSel ? protoSel.value : 'openai',
      };
      postJSON(SLIRN_API + '/llm_config/' + (editing ? 'update' : 'add'), editing
        ? {id: editing, new_id: payload.id, provider: payload.provider,
           base_url: payload.base_url, api_key_env: payload.api_key_env,
           protocol: payload.protocol}
        : payload).then(function(r) {
        if (r && r.ok) {
          toast(r.toast || (editing ? '已更新' : '已添加'));
          renderLLMList(r.models || [], r.current || '');
          llmFormReset();
        } else {
          toast('❌ ' + ((r && r.error) || '操作失败'), 'error');
        }
      });
    }
    else if (action === 'llm-edit') {
      var editId = target.getAttribute('data-id') || '';
      var m = LLM_MODELS.filter(function(x) { return x.id === editId; })[0];
      if (!m) return;
      LLM_EDIT_ID = editId;
      document.getElementById('slirn-llm-in-id').value = m.id;
      document.getElementById('slirn-llm-in-provider').value = m.provider || '';
      document.getElementById('slirn-llm-in-url').value = m.base_url || '';
      document.getElementById('slirn-llm-in-env').value = m.api_key_env || '';
      var protoSel2 = document.getElementById('slirn-llm-in-proto');
      if (protoSel2) protoSel2.value = (m.protocol === 'anthropic') ? 'anthropic' : 'openai';
      var ft = document.getElementById('slirn-llm-form-title');
      if (ft) ft.textContent = '修改模型（' + editId + '）';
      var ab = document.getElementById('slirn-llm-add-btn');
      if (ab) ab.textContent = '💾 保存修改';
      var cb = document.getElementById('slirn-llm-cancel-btn');
      if (cb) cb.style.display = '';
      var formEl = document.getElementById('slirn-llm-form');
      if (formEl && formEl.scrollIntoView) formEl.scrollIntoView({block: 'nearest', behavior: 'smooth'});
    }
    else if (action === 'llm-cancel-edit') {
      llmFormReset();
    }
    else if (action === 'llm-use' || action === 'llm-remove') {
      var llmId = target.getAttribute('data-id') || '';
      postJSON(SLIRN_API + '/llm_config/' + action.slice(4), {id: llmId}).then(function(r) {
        if (r && r.ok) {
          toast(r.toast || '已更新');
          renderLLMList(r.models || [], r.current || '');
        } else {
          toast('❌ ' + ((r && r.error) || '操作失败'), 'error');
        }
      });
    }
    else if (action === 'llm-test') {
      var testId = target.getAttribute('data-id') || '';
      var testBox = document.getElementById('slirn-llm-test-result');
      if (testBox) { testBox.className = 'slirn-llm-test-result'; testBox.textContent = '⏳ 测试中（' + testId + ' 真实调用一次）…'; }
      postJSON(SLIRN_API + '/llm_test', {id: testId}).then(function(r) {
        if (!testBox) return;
        if (r && r.ok) {
          testBox.className = 'slirn-llm-test-result ok';
          testBox.textContent = '✅ 模型可用（' + r.model + ' · 耗时 '
            + (r.elapsed_s || 0).toFixed(1) + 's · 回复「' + (r.reply || '') + '」）';
        } else {
          testBox.className = 'slirn-llm-test-result err';
          testBox.textContent = '❌ ' + ((r && r.error) || '测试失败');
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
            <button class="slirn-btn-icon" data-action="open-llm-settings" aria-label="大模型设置" title="大模型设置">⚙️</button>
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
        # ?src=original → 完整原视频（编辑页滑全片定位用，REQ-20260915-003）
        # ?src=rough_compose → 粗剪成片（可选阶段产物，REQ-20260916-016）
        src_q = _parse_qs(scope.get("query_string", b"").decode("latin-1")).get("src", [""])[0]
        video: Path | None = None
        try:
            t = mgr.get(tid)
            if src_q == "original" and t.original_video_source.exists():
                video = t.original_video_source
            elif src_q == "rough_compose":
                from slirn_home import compose_service as _comp_mod

                rc = _comp_mod.rough_compose_path(mgr.tasks_dir / tid / "outputs")
                video = rc if rc.exists() else None
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

    @app.app.get("/slirn/api/llm_config")
    async def llm_config_get():
        """模型注册表 + 当前模型 + 各条目 Key 状态（REQ-20260915-008 弹窗初始化）。"""
        from slirn_home import llm_config

        return _ok(
            "",
            models=llm_config.list_models(repo_root),
            current=llm_config.get_current(repo_root),
        )

    @app.app.post("/slirn/api/llm_config/add")
    async def llm_config_add(body: dict = Body(default_factory=dict)):
        """添加模型注册项：{id, provider, base_url, api_key_env, protocol?}。"""
        from slirn_home import llm_config

        try:
            llm_config.add_model(
                repo_root,
                body.get("id", ""), body.get("provider", ""),
                body.get("base_url", ""), body.get("api_key_env", ""),
                body.get("protocol", "openai"),
            )
        except llm_config.LLMConfigError as e:
            return _err(str(e))
        return _ok("", models=llm_config.list_models(repo_root),
                   current=llm_config.get_current(repo_root),
                   toast=f"✅ 已添加模型 {body.get('id', '')}")

    @app.app.post("/slirn/api/llm_config/update")
    async def llm_config_update(body: dict = Body(default_factory=dict)):
        """修改模型注册项（REQ-20260916-001）：{id, new_id, provider, base_url, api_key_env, protocol?}。"""
        from slirn_home import llm_config

        try:
            llm_config.update_model(
                repo_root,
                str(body.get("id") or ""), body.get("new_id", ""),
                body.get("provider", ""), body.get("base_url", ""),
                body.get("api_key_env", ""), body.get("protocol", "openai"),
            )
        except llm_config.LLMConfigError as e:
            return _err(str(e))
        new_id = str(body.get("new_id") or "").strip()
        return _ok("", models=llm_config.list_models(repo_root),
                   current=llm_config.get_current(repo_root),
                   toast=f"✏️ 已更新模型 {new_id}")

    @app.app.post("/slirn/api/llm_config/remove")
    async def llm_config_remove(body: dict = Body(default_factory=dict)):
        """删除模型注册项；删的是当前模型 → 自动切到剩余第一个。"""
        from slirn_home import llm_config

        try:
            llm_config.remove_model(repo_root, str(body.get("id") or ""))
        except llm_config.LLMConfigError as e:
            return _err(str(e))
        return _ok("", models=llm_config.list_models(repo_root),
                   current=llm_config.get_current(repo_root),
                   toast=f"🗑 已删除模型 {body.get('id', '')}")

    @app.app.post("/slirn/api/llm_config/use")
    async def llm_config_use(body: dict = Body(default_factory=dict)):
        """设置当前使用的模型。"""
        from slirn_home import llm_config

        try:
            cur = llm_config.use_model(repo_root, str(body.get("id") or ""))
        except llm_config.LLMConfigError as e:
            return _err(str(e))
        return _ok("", models=llm_config.list_models(repo_root),
                   current=cur, toast=f"⚙️ 当前模型 → {cur}")

    @app.app.post("/slirn/api/llm_test")
    async def llm_test(body: dict = Body(default_factory=dict)):
        """连通性测试：对已注册模型真实调用一次，返回可用性/耗时/可读错误。"""
        import time as _time

        from slirn_home import llm_config, revision_service

        mid = str(body.get("id") or "").strip()
        entry = next((m for m in llm_config.list_models(repo_root)
                      if m.get("id") == mid and "key_present" in m), None)
        if entry is None:
            return _err(f"模型 {mid} 未注册")
        t0 = _time.time()
        try:
            reply = revision_service._call_llm(
                "你是连通性测试助手。", "请只回复两个字：正常", entry=entry, retries=0,
            )
        except Exception as e:  # noqa: BLE001 — 错误信息已是 REQ-007 可读格式
            return _err(str(e))
        return _ok("", model=entry["id"], elapsed_s=round(_time.time() - t0, 2),
                   reply=(reply or "").strip()[:50])

    @app.app.post("/slirn/api/revise_subtitle")
    async def revise_subtitle(body: dict = Body(default_factory=dict)):
        """启动大模型字幕分析（REQ-20260915-005）。已有建议时须 force（前端二次确认）。

        rigor 必填（REQ-20260916-003）：high|medium|low|custom（007 自定义档），
        注入提示词并留痕；custom 时 custom_prompt 为用户修改后的提示词
        （空白回退默认底稿），实际使用全文写入 revision.json meta。
        """
        from slirn_home import llm_config, revision_service

        tid = (body.get("task_id") or "").strip()
        if not tid:
            return _err("缺少 task_id")
        rigor = str(body.get("rigor") or "").strip().lower()
        custom_prompt = str(body.get("custom_prompt") or "")
        if (rigor != revision_service.CUSTOM_RIGOR_KEY
                and rigor not in revision_service.RIGOR_LEVELS):
            return _err("请先选择分析严谨性级别（高 / 中 / 低 / 自定义）")
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

        entry = llm_config.get_current_entry(repo_root)
        if entry is None:
            return _err("未注册任何大模型 — 请先点顶栏 ⚙️ 添加模型")
        started = revision_service.start_job(
            tid, sub_meta["segments"], t.name, hotwords, outputs_dir,
            entry=entry, rigor=rigor, custom_prompt=custom_prompt,
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
            toast += " · 字幕修订完成，可进入切分修剪"
        elif remaining:
            toast += f" · 还剩 {remaining} 条未决策"
        return _ok("", toast=toast, decided=decided, total=total, finished=finished)

    @app.app.post("/slirn/api/build_cutlist")
    async def build_cutlist(body: dict = Body(default_factory=dict)):
        """生成切分修剪清单（REQ-20260916-008）：决策落盘 cutlist.json + 推进 ROUGH_CUT_DONE。"""
        import time as _time

        from slirn_home import asr_service, cutlist_service, revision_service

        tid = (body.get("task_id") or "").strip()
        if not tid:
            return _err("缺少 task_id")
        try:
            mgr.get(tid)
        except Exception as e:  # noqa: BLE001
            return _err(f"任务不存在: {e}")
        outputs_dir = mgr.tasks_dir / tid / "outputs"
        rev = revision_service.load_revision(outputs_dir)
        if not (rev and rev.get("entries")):
            return _err("尚无修订建议，请先在「字幕修订」阶段完成大模型分析")
        if not revision_service.all_decided(rev):
            pending = sum(1 for e in rev["entries"] if e.get("decision") == "pending")
            return _err(f"字幕修订还有 {pending} 条未决策，请先保存全部决策")
        sub_meta = asr_service.load_subtitle(outputs_dir)
        if not (sub_meta and sub_meta.get("segments")):
            return _err("缺少字幕生成产物（subtitle.json），请先在「字幕生成」阶段生成字幕")
        try:
            cutlist = cutlist_service.build_cutlist(sub_meta, rev)
            cutlist_service.save_cutlist(outputs_dir, cutlist)
        except Exception as e:  # noqa: BLE001
            return _err(f"生成切分清单失败: {e}")
        stats = cutlist.get("stats", {})
        try:
            mgr.update_status(tid, TaskStatus.ROUGH_CUT_DONE)
        except Exception as e:  # noqa: BLE001
            revision_service.log.warning("更新任务 %s 状态失败: %s", tid, e)
        saved_at = _time.strftime("%Y-%m-%dT%H:%M:%S")
        return _ok("", toast=(
            f"✅ 切分清单已生成（带入 {stats.get('brought', 0)} 段 · 剔除 {stats.get('dropped', 0)} 条"
            f" · 切分子段 {stats.get('split_subs', 0)}）· 切分修剪完成，可进入精剪字幕"
        ), saved_at=saved_at, stats=stats)

    @app.app.post("/slirn/api/save_cut_decisions")
    async def save_cut_decisions(body: dict = Body(default_factory=dict)):
        """保存切分决策（REQ-20260916-011）：子段翻转 manual_marks + 字幕级改判 actions。

        重算并入显示后落盘 cutlist.json（阶段随磁盘判定/幂等推进不变）。
        改判 split 携带的切分后内容（split_targets）回写修订 user_note（S→填内容
        →直接保存的路径闭环）；校验改判 split 必须有切分后内容，残缺决策不落盘。
        """
        import json
        import time as _time

        from slirn_home import asr_service, cutlist_service, revision_service

        tid = (body.get("task_id") or "").strip()
        if not tid:
            return _err("缺少 task_id")
        try:
            mgr.get(tid)
        except Exception as e:  # noqa: BLE001
            return _err(f"任务不存在: {e}")
        outputs_dir = mgr.tasks_dir / tid / "outputs"
        rev = revision_service.load_revision(outputs_dir)
        if not (rev and rev.get("entries")):
            return _err("尚无修订建议，请先在「字幕修订」阶段完成大模型分析")
        if not revision_service.all_decided(rev):
            pending = sum(1 for e in rev["entries"] if e.get("decision") == "pending")
            return _err(f"字幕修订还有 {pending} 条未决策，请先保存全部决策")
        sub_meta = asr_service.load_subtitle(outputs_dir)
        if not (sub_meta and sub_meta.get("segments")):
            return _err("缺少字幕生成产物（subtitle.json），请先在「字幕生成」阶段生成字幕")

        manual_marks = body.get("manual_marks") or {}
        actions = body.get("actions") or {}
        split_targets = body.get("split_targets") or {}
        if (not isinstance(manual_marks, dict) or not isinstance(actions, dict)
                or not isinstance(split_targets, dict)):
            return _err("manual_marks / actions / split_targets 格式不正确")
        # 改判 split 附带的切分后内容 → 回写修订（decision=split + user_note，
        # 单一事实源；与「✂️ 重新切分」同一落点）
        rev_changed = False
        for k, tv in split_targets.items():
            if str(actions.get(k)) != "split":
                continue
            tv = str(tv or "").strip()
            if not tv:
                continue
            entry = next((e for e in rev["entries"] if str(e.get("i")) == str(k)), None)
            if entry is not None and cutlist_service._split_target(entry) != tv:
                entry["decision"] = "split"
                entry["user_note"] = tv[:500]
                rev_changed = True
        # 改判 split 必须有切分后内容（本次携带 / 修订 user_note / 模型建议），
        # 否则执行口径残缺
        for k, v in actions.items():
            if str(v) not in cutlist_service.ACTIONS:
                return _err(f"未知的决策状态「{v}」（第 {k} 条）")
            if str(v) == "split":
                entry = next((e for e in rev["entries"] if str(e.get("i")) == str(k)), None)
                if entry is None or not cutlist_service._split_target(entry):
                    return _err(f"第 {k} 条已改判切分，但未填写切分后内容 — 请填写后再保存")
        try:
            cutlist = cutlist_service.build_cutlist(sub_meta, rev,
                                                    manual_marks=manual_marks, actions=actions)
        except Exception as e:  # noqa: BLE001
            return _err(f"保存切分决策失败: {e}")
        if rev_changed:  # 修订先落盘、清单后落盘（saved_at 单调，不误报过期黄条）
            rev["saved_at"] = _time.strftime("%Y-%m-%dT%H:%M:%S")
            (outputs_dir / revision_service.REVISION_JSON).write_text(
                json.dumps(rev, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        cutlist_service.save_cutlist(outputs_dir, cutlist)
        try:
            mgr.update_status(tid, TaskStatus.ROUGH_CUT_DONE)  # 幂等：清单在盘即完成
        except Exception as e:  # noqa: BLE001
            revision_service.log.warning("更新任务 %s 状态失败: %s", tid, e)
        stats = cutlist.get("stats", {})
        parts = ["✅ 切分决策已保存"]
        if stats.get("mark_flipped"):
            parts.append(f"手工翻转 {stats['mark_flipped']} 段")
        if stats.get("action_changed"):
            parts.append(f"字幕级改判 {stats['action_changed']} 条")
        return _ok("", toast=" · ".join(parts), stats=stats)

    @app.app.post("/slirn/api/compose_rough")
    async def compose_rough(body: dict = Body(default_factory=dict)):
        """启动粗剪合成（REQ-20260916-018，可选步骤）：上游 VideoClipper 方法合成。

        口径与切分修剪面板一致（修订实时 + 已保存手工翻转/改判）；行文本取
        最新确认版（热词替换未撤销的行用 new_text，REQ-20260916-017）—
        「处理之后的字幕 + 原视频」交给上游合成方法（video_clip）。
        不推进任务状态。已在跑 → 返回 running 供前端接续轮询。
        """
        from slirn_home import asr_service, compose_service, cutlist_service, fine_service, revision_service

        tid = (body.get("task_id") or "").strip()
        if not tid:
            return _err("缺少 task_id")
        try:
            mgr.get(tid)
        except Exception as e:  # noqa: BLE001
            return _err(f"任务不存在: {e}")
        outputs_dir = mgr.tasks_dir / tid / "outputs"
        rev = revision_service.load_revision(outputs_dir)
        if not (rev and rev.get("entries")):
            return _err("请先完成「字幕修订」")
        if not revision_service.all_decided(rev):
            return _err("字幕修订还有未决策条目，无法确定保留区间")
        sub_meta = asr_service.load_subtitle(outputs_dir)
        if not (sub_meta and sub_meta.get("segments")):
            return _err("缺少字幕生成产物，请先生成字幕")
        saved = cutlist_service.load_cutlist(outputs_dir)
        try:
            cutlist = cutlist_service.build_cutlist(
                sub_meta, rev,
                manual_marks=(saved or {}).get("manual_marks") if saved else None,
                actions=(saved or {}).get("actions") if saved else None,
            )
        except Exception as e:  # noqa: BLE001
            return _err(f"计算保留区间失败: {e}")
        units = cutlist_service.effective_keep_units(cutlist)
        if not units:
            return _err("没有保留内容 — 全部被删除时无需合成")
        intervals_ms = [(int(u["start_ms"]), int(u["end_ms"])) for u in units]
        # 行文本取最新确认版：热词替换（REQ-20260916-017）未撤销的行 → new_text
        fine = fine_service.load_fine(outputs_dir)
        fmap = {str(e["id"]): e["new_text"] for e in (fine or {}).get("entries") or []
                if not e.get("reverted")}
        lines = [{"id": str(u["id"]), "start_ms": int(u["start_ms"]), "end_ms": int(u["end_ms"]),
                  "text": fmap.get(str(u["id"]), str(u["text"]))} for u in units]
        video, _label = _resolve_task_video(mgr.get(tid))
        if video is None:
            return _err("任务视频文件缺失，无法合成")
        keep_ms = sum(int(u["end_ms"]) - int(u["start_ms"]) for u in units)
        n_merged = len(compose_service.merge_intervals_ms(intervals_ms))
        started = compose_service.start_compose(
            tid, video, intervals_ms, compose_service.rough_compose_path(outputs_dir), lines)
        job = compose_service.job_status(tid) or {}
        return _ok("", running=bool(started), job=job,
                   toast="🎬 合成已启动" if started else "🎬 合成已在进行中",
                   keep_ms=keep_ms, segments=n_merged, raw_segments=len(intervals_ms))

    @app.app.post("/slirn/api/compose_rough_status")
    async def compose_rough_status(body: dict = Body(default_factory=dict)):
        """粗剪合成进度轮询（REQ-20260916-016）。"""
        from slirn_home import compose_service

        tid = (body.get("task_id") or "").strip()
        if not tid:
            return _err("缺少 task_id")
        job = compose_service.job_status(tid)
        if job is None:  # 服务重启后 job 丢失 — 产物在即视为完成
            artifact = compose_service.rough_compose_path(mgr.tasks_dir / tid / "outputs")
            if artifact.exists():
                return _ok("", job={"state": "done", "progress": 100.0})
            return _err("没有进行中的合成任务")
        return _ok("", job=job)

    @app.app.post("/slirn/api/fine_revise")
    async def fine_revise(body: dict = Body(default_factory=dict)):
        """启动大模型热词替换分析（REQ-20260916-017）。已有结果时须 force（前端二次确认）。

        行集 = 切分执行口径的保留行（与切分面板/粗剪合同同口径）；
        只处理任务热词相关的误识别，替换结果待用户逐处确认。
        """
        import time as _time

        from slirn_home import asr_service, cutlist_service, fine_service, llm_config, revision_service

        tid = (body.get("task_id") or "").strip()
        if not tid:
            return _err("缺少 task_id")
        try:
            t = mgr.get(tid)
        except Exception as e:  # noqa: BLE001
            return _err(f"任务不存在: {e}")
        outputs_dir = mgr.tasks_dir / tid / "outputs"
        rev = revision_service.load_revision(outputs_dir)
        if not (rev and rev.get("entries")):
            return _err("请先完成「字幕修订」")
        if not revision_service.all_decided(rev):
            return _err("字幕修订还有未决策条目，无法确定切分后的字幕行")
        sub_meta = asr_service.load_subtitle(outputs_dir)
        if not (sub_meta and sub_meta.get("segments")):
            return _err("缺少字幕生成产物，请先生成字幕")
        hotwords: list[str] = []
        try:
            if t.hotwords_path.exists():
                hotwords = [w for w in t.hotwords_path.read_text(encoding="utf-8").split() if w]
        except Exception:  # noqa: BLE001
            pass
        if not hotwords:
            return _err("任务没有热词 — 请先在「素材准备」阶段为任务添加热词")
        if fine_service.load_fine(outputs_dir) and not body.get("force"):
            return _err("已存在热词替换结果 — 重新分析将覆盖并重置全部撤销决定，请确认后重试")

        saved = cutlist_service.load_cutlist(outputs_dir)
        cutlist = cutlist_service.build_cutlist(
            sub_meta, rev,
            manual_marks=(saved or {}).get("manual_marks") if saved else None,
            actions=(saved or {}).get("actions") if saved else None,
        )
        units = cutlist_service.effective_keep_units(cutlist)
        if not units:
            return _err("切分执行口径下没有保留行，无需热词替换")
        lines = [
            {"id": str(u["id"]), "start_ms": int(u["start_ms"]), "end_ms": int(u["end_ms"]),
             "text": str(u.get("text", ""))}
            for u in units
        ]
        entry = llm_config.get_current_entry(repo_root)
        if entry is None:
            return _err("未注册任何大模型 — 请先点顶栏 ⚙️ 添加模型")
        started = fine_service.start_job(
            tid, lines, t.name, hotwords, outputs_dir,
            entry=entry,
            cutlist_saved_at=str((saved or {}).get("saved_at")
                                 or (rev or {}).get("saved_at")
                                 or _time.strftime("%Y-%m-%dT%H:%M:%S")),
        )
        if not started:
            return _ok("", toast="⏳ 该任务已在分析中，请等待完成")
        return _ok("", toast="🔎 热词替换分析已开始（后台运行，可离开本页）",
                   job={"state": "running", "stage": "准备提示词"})

    @app.app.post("/slirn/api/fine_revise_status")
    async def fine_revise_status(body: dict = Body(default_factory=dict)):
        """热词替换分析进度轮询（REQ-20260916-017）。"""
        from slirn_home import fine_service

        tid = (body.get("task_id") or "").strip()
        if not tid:
            return _err("缺少 task_id")
        j = fine_service.job_status(tid)
        if j is None:  # 服务重启后 job 丢失 — 产物在即视为完成
            fine = fine_service.load_fine(mgr.tasks_dir / tid / "outputs")
            if fine:
                return _ok("", job={
                    "state": "done",
                    "replaced_lines": (fine.get("stats") or {}).get("replaced_lines", 0),
                    "replacements": (fine.get("stats") or {}).get("replacements", 0),
                })
            return _ok("", job={"state": "idle"})
        import time as _time
        j["elapsed_s"] = int((j.get("finished_at") or _time.time()) - j.get("started_at", _time.time()))
        return _ok("", job=j)

    @app.app.post("/slirn/api/save_fine_revision")
    async def save_fine_revision(body: dict = Body(default_factory=dict)):
        """保存热词替换确认（REQ-20260916-017）：撤销决定落盘 + 阶段完成。

        reverted_ids：用户点「撤销替换」的行 id 列表（全量口径 — 未列出的行
        一律恢复生效）；幂等推进 FINE_SUBTITLE_REVIEWED。
        """
        from tasklib.models import TaskStatus

        from slirn_home import fine_service

        tid = (body.get("task_id") or "").strip()
        if not tid:
            return _err("缺少 task_id")
        try:
            mgr.get(tid)
        except Exception as e:  # noqa: BLE001
            return _err(f"任务不存在: {e}")
        outputs_dir = mgr.tasks_dir / tid / "outputs"
        if fine_service.load_fine(outputs_dir) is None:
            return _err("还没有热词替换结果 — 请先执行「大模型热词替换分析」")
        reverted = body.get("reverted_ids")
        if not isinstance(reverted, list):
            return _err("reverted_ids 必须是数组")
        try:
            fine, changed = fine_service.save_decisions(outputs_dir, [str(x) for x in reverted])
        except Exception as e:  # noqa: BLE001
            return _err(f"保存失败: {e}")
        est = fine_service.effective_stats(fine)
        mgr.update_status(tid, TaskStatus.FINE_SUBTITLE_REVIEWED)  # 幂等：结果在盘即完成
        return _ok("", toast=(f"✅ 已确认：生效替换 {est['replacements']} 处"
                              f"（撤销 {est['reverted']} 行）"), stats=est)

    @app.app.post("/slirn/api/resplit_segment")
    async def resplit_segment_api(body: dict = Body(default_factory=dict)):
        """单段重新切分（REQ-20260916-011）：改切分后内容 → 回写修订 + 重算清单落盘。

        纯逻辑见 cutlist_service.resplit_segment（命中 0 / 无字级时间戳 →
        宁可不切不可错切，返回错误不落盘）。
        """
        import json
        import time as _time

        from slirn_home import asr_service, cutlist_service, revision_service

        tid = (body.get("task_id") or "").strip()
        if not tid:
            return _err("缺少 task_id")
        try:
            si = int(body.get("source_i"))
        except (TypeError, ValueError):
            return _err("缺少 source_i")
        target_text = str(body.get("target_text") or "")
        try:
            mgr.get(tid)
        except Exception as e:  # noqa: BLE001
            return _err(f"任务不存在: {e}")
        outputs_dir = mgr.tasks_dir / tid / "outputs"
        rev = revision_service.load_revision(outputs_dir)
        if not (rev and rev.get("entries")):
            return _err("尚无修订建议，请先在「字幕修订」阶段完成大模型分析")
        sub_meta = asr_service.load_subtitle(outputs_dir)
        if not (sub_meta and sub_meta.get("segments")):
            return _err("缺少字幕生成产物（subtitle.json），请先在「字幕生成」阶段生成字幕")
        saved = cutlist_service.load_cutlist(outputs_dir)
        cutlist, rev2, err = cutlist_service.resplit_segment(
            sub_meta, rev, si, target_text, saved)
        if err:
            return _err(err)
        # 修订先落盘、清单后落盘（saved_at 单调，不误报过期黄条）
        rev2["saved_at"] = _time.strftime("%Y-%m-%dT%H:%M:%S")
        (outputs_dir / revision_service.REVISION_JSON).write_text(
            json.dumps(rev2, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        cutlist_service.save_cutlist(outputs_dir, cutlist)
        try:
            mgr.update_status(tid, TaskStatus.ROUGH_CUT_DONE)  # 幂等：清单在盘即完成
        except Exception as e:  # noqa: BLE001
            revision_service.log.warning("更新任务 %s 状态失败: %s", tid, e)
        subs = [it for it in cutlist.get("items", [])
                if int(it.get("source_i", -1)) == si and it.get("kind") == "split"]
        n_keep = sum(1 for s in subs if str(s.get("mark")) == "keep")
        n_del = len(subs) - n_keep
        return _ok("", toast=(
            f"✅ 第 {si} 条已重新切分：{len(subs)} 个子段"
            f"（保留 {n_keep} · 删除洞 {n_del}）— 可预播确认"
        ))

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
