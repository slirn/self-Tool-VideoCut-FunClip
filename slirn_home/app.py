"""Gradio Blocks 构造 — 全 HTML 自定义渲染，玻璃拟态统一风格。"""

from __future__ import annotations

import html
import logging
from pathlib import Path
from typing import Any

import gradio as gr

log = logging.getLogger(__name__)

# 确保 tasklib 可导入（slirn-standalone 在 sibling 或子模块挂载点）
# 必须在 from slirn_home.task_list import 之前调用，因为 task_list.py 顶层 import tasklib
from slirn_home.paths import ensure_tasklib_importable

ensure_tasklib_importable()

from tasklib import TaskManager, TaskNotFoundError, TaskStatus  # noqa: E402
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


def _to_local_naive(dt):
    """datetime / ISO 字符串 → 本地时区的 naive datetime。

    tasklib 的 created_at/updated_at 是带时区的 UTC（datetime.now(timezone.utc)）；
    显示/比较前必须 astimezone() 转本地。直接剥 tzinfo 会把 UTC 当本地用，
    UTC+8 下新建任务即显示「8 小时前」（2026-09-17 修复）。naive 输入视为已是本地。
    """
    from datetime import datetime
    if dt is None:
        return None
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except Exception:
            return None
    if getattr(dt, "tzinfo", None) is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def _fmt_local(dt) -> str:
    """datetime / ISO 字符串 → 本地时间 YYYY-MM-DD HH:MM:SS。"""
    d = _to_local_naive(dt)
    return d.strftime("%Y-%m-%d %H:%M:%S") if d else "未知"


def _time_ago(dt) -> str:
    """datetime / ISO 字符串 → 相对时间描述（先转本地时区，见 _to_local_naive）。"""
    from datetime import datetime
    dt = _to_local_naive(dt)
    if dt is None:
        return "未知"
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
        created = _to_local_naive(s.created_at)
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
                <button class="slirn-btn slirn-btn-sm slirn-btn-danger" data-action="delete-task" data-task-id="{_esc(s.task_id)}" data-task-name="{_esc(s.name)}">🗑️ 删除</button>
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
        # 项目定位就是长视频剪辑：≤5 小时不提示（REQ-20260917-022），仅超支持上限时提醒
        if dur and dur > 18000:
            hint += ' · <span style="color:#c2410c;">⚠️ 视频超过 5 小时（支持上限），识别耗时很长，建议先截取重点片段</span>'
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
        rows = ""
        for s in meta["segments"]:
            spk = s.get("spk")
            badge = (
                f'<span class="slirn-sub-spk spk-c{(int(spk) - 1) % 6 + 1}">人员{int(spk)}</span>'
                if spk else ""
            )
            rows += (
                f'<div class="slirn-sub-row{" has-spk" if spk else ""}"'
                f' data-task-id="{_esc(task_id)}"'
                f' data-start-ms="{int(s["start_ms"])}" data-end-ms="{int(s["end_ms"])}">'
                f'<span class="slirn-sub-idx">{int(s["i"])}</span>'
                f'{badge}'
                f'<span class="slirn-sub-time">{_esc(s["start"])} → {_esc(s["end"])}</span>'
                f'<span class="slirn-sub-text">{_esc(s["text"])}</span>'
                f"</div>"
            )
        n = len(meta["segments"])
        created = _esc(meta.get("created_at", ""))
        src_label = "截取段时间轴" if meta.get("source") == "segment" else "原视频时间轴"
        # 说话人统计行（REQ-20260917-029）：仅 SD 生成的字幕有 speakers
        speakers = meta.get("speakers") or {}
        stats_html = ""
        if speakers.get("stats"):
            parts = "、".join(f"人员{st['spk']} {st['sentences']} 句" for st in speakers["stats"])
            stats_html = (f' · 🎙 {speakers.get("count", len(speakers["stats"]))}'
                          f" 位说话人：{parts}")
        list_html = f'''<div class="slirn-sub-meta">📝 {n} 段 · 识别于 {created} · {src_label}{stats_html} · 点击任一行定位播放，播放时当前行高亮</div>
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
            <label class="slirn-sd-toggle" title="开启后用 FunASR cam++ 分辨每句话的说话人：字幕带人员编号并统计每人句数；单人视频误分成多人时可关闭重生成">
                <input type="checkbox" id="slirn-sd-switch" /> 区分说话人
            </label>
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
        <div class="slirn-form-hint">每段建议都带具体分析说明；你在建议之上逐条决策（采纳/改判；切分行填写切分修剪后内容，更正行填写更正后内容）。</div>
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
        # split/fix 行输入框自动预填建议文本（REQ-20260916-010 / 20260917-026）：
        # 手动填写优先；未填 → keep_text 作起点（与服务端分析时自动填写、切分清单
        # 回退同口径，存量任务未重分析也能看到/微调将要生效的修剪后/更正后内容）
        note_val = str(e.get("user_note") or "").strip()
        if not note_val and cat in ("split", "fix") and e.get("keep_text"):
            note_val = str(e["keep_text"])
        # 输入框语义按建议类别区分（决策下拉改为 fix/split 时 JS 动态跟随，REQ-20260917-026）
        note_ph = "更正后内容（可微调）" if cat == "fix" else "切分修剪后内容（可空）"
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
            f' data-sugg="{_esc(cat)}" data-decision="{_esc(decision)}" data-final="{_esc(final_kind)}"'
            f' data-text="{_esc(e.get("text", ""))}">'
            f'<div class="slirn-rev-line">'
            f'<span class="slirn-sub-idx">{int(e["i"])}</span>'
            f'<span class="slirn-sub-time">{_esc(e.get("start", ""))} → {_esc(e.get("end", ""))}</span>'
            f'<span class="slirn-sub-text"><span class="slirn-rev-badge {cat}">{cat_label}</span>'
            f'{_esc(e.get("text", ""))}</span>'
            f'<select class="slirn-rev-select" data-i="{int(e["i"])}" title="处理决策">{opts}</select>'
            f'<button class="slirn-rev-toggle" data-action="rev-detail" data-i="{int(e["i"])}"'
            f' title="展开/收起模型分析与修剪/更正内容">{"▴" if open_detail else "▾"}</button>'
            f'</div>'
            f'<div class="slirn-rev-detail">'
            f'<div class="slirn-rev-note">🤖 {_esc(e.get("note", ""))}</div>{extra_html}'
            f'<input class="slirn-rev-note-input" data-i="{int(e["i"])}"'
            f' placeholder="{_esc(note_ph)}" value="{_esc(note_val)}" />'
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
        f" · ✅ 已决策 <b>{decided}/{n}</b> · 点击行连续跳播（本条播完跳下一条，段间空白不播） · 决策列默认「采纳建议」"
        f" · ▾ 展开模型分析与修剪/更正内容"
    )
    # REQ-20260918-039：批量改判目标 = 决策下拉全部状态（与单条决策同口径）
    rev_batch_opts = "".join(
        f'<option value="{k}"{" selected" if k == "keep" else ""}>{v}</option>'
        for k, v in revision_service.USER_DECISIONS.items()
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
          <select class="slirn-rev-jump-sel" id="slirn-rev-jump-sel"
                  title="跳转目标状态：跳转时整个列表保持显示，只定位到该状态的上/下一条记录（上下文可见）"></select>
          <span class="slirn-rev-jump">
            <button type="button" class="slirn-rev-jump-btn" data-rev-jump="prev" title="跳到上一条所选状态的记录">⬆ 上一条</button>
            <button type="button" class="slirn-rev-jump-btn" data-rev-jump="next" title="跳到下一条所选状态的记录">下一条 ⬇</button>
          </span>
          <span class="slirn-rev-filter-count" id="slirn-rev-filter-count"></span>
        </div>
        <div class="slirn-batch-bar" id="slirn-rev-batch">
          <span class="slirn-batch-label">🧮 批量</span>
          序号 <input class="slirn-batch-num" id="slirn-rev-batch-start" type="number" min="1" step="1">
          ～ <input class="slirn-batch-num" id="slirn-rev-batch-end" type="number" min="1" step="1">
          改为 <select class="slirn-batch-sel" id="slirn-rev-batch-sel">{rev_batch_opts}</select>
          <button type="button" class="slirn-btn slirn-btn-xs" data-action="rev-batch-apply"
                  title="把区间内字幕的决策批量改为所选状态（确认后应用，保存前可继续调整）">🧮 批量应用</button>
          <span class="slirn-batch-hint">区间内所有行一次改判 — 应用后记得「💾 保存修订决策」</span>
        </div>
        <div class="slirn-batch-bar" id="slirn-rev-search">
          <span class="slirn-batch-label">🔎 搜索</span>
          <input class="slirn-search-q" id="slirn-rev-search-q" type="text"
                 placeholder="输入字幕内容片段定位（回车下一条，Shift+回车上一条）">
          <span class="slirn-search-count" id="slirn-rev-search-count"></span>
          <button type="button" class="slirn-btn slirn-btn-xs" data-action="rev-search-prev">⬆ 上一条</button>
          <button type="button" class="slirn-btn slirn-btn-xs" data-action="rev-search-next">下一条 ⬇</button>
          <span class="slirn-batch-hint">按内容定位字幕（不区分大小写）；筛选生效时只在可见行中搜</span>
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
    from slirn_home import asr_service, cut_speaker, cutlist_service, revision_service

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
    actions_map: dict[int, str] = {}
    for k, v in (cutlist.get("actions") or {}).items():
        try:
            actions_map[int(k)] = str(v)
        except (TypeError, ValueError):
            continue
    # 组级决策下拉（REQ-20260917-027）：点徽章位置即弹出可选状态——鼠标改判
    # 与快捷键 K/D/S 同效（原来仅键盘可达；删除后跳播会抢走选中，鼠标无从恢复）
    ACT_OPTS = [("", "维持原状"), ("keep", "✅ 改判保留"),
                ("delete", "❌ 改判删除"), ("split", "✂️ 改判切分")]

    def _act_sel(act: str | None) -> str:
        a = act or ""
        opts = "".join(
            f'<option value="{v}"{" selected" if a == v else ""}>{label}</option>'
            for v, label in ACT_OPTS
        )
        return ('<select class="slirn-cut-abadge slirn-cut-actsel" data-actsel'
                ' title="选择本条字幕的决策（与快捷键 K/D/S 同效）">' + opts + "</select>")
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

    # 人员关联（REQ-033）：已落盘 enabled → 现算对齐（与端点同口径；快照备查），
    # 行上直接渲染 👤 徽章 + data-spk；subtitle 已无 spk（重新生成过）→ 静默回普通态
    spk_link = None
    if cut_speaker.load_link(outputs_dir):
        spk_link = cut_speaker.link_speakers(sub_meta, cutlist)
        if not spk_link.get("available"):
            spk_link = None
    spk_rows: dict[str, int] = (spk_link or {}).get("rows") or {}

    def _spk_attr(rid) -> str:
        spk = spk_rows.get(str(rid))
        return f' data-spk="{int(spk)}"' if spk else ""

    def _spk_badge(rid) -> str:
        spk = spk_rows.get(str(rid))
        if not spk:
            return ""
        # REQ-20260917-036：紧跟序号渲染（第2轨），与其他信息同行不折行
        return (f'<span class="slirn-cut-spk"'
                f' title="人员 {int(spk)}（时间段重叠最大的字幕段说话人）">👤{int(spk)}</span>')

    rows = ""
    for si, its in groups:
        first = its[0]
        is_split_group = any(x.get("sub") is not None for x in its)
        act = actions_map.get(si)
        act_attr = f' data-act="{_esc(act)}"' if act else ""
        if is_split_group:
            # 切分组：组头（父编号+原段→切分后文字+决策下拉+重切/试听钮）+ 完整子段表
            rows += (
                f'<div class="slirn-cut-group split" data-source-i="{si}"'
                f' data-task-id="{_esc(task_id)}"{act_attr}'
                f' data-target="{_esc(first.get("target_text") or "")}">'
                f'<div class="slirn-cut-ghead">'
                f'<span class="slirn-sub-idx">{si}</span>'
                f'<span class="slirn-rev-badge split" data-kind="split">切分修剪</span>'
                f'<span class="slirn-cut-gtext">原段：「{_esc(first.get("orig_text", ""))}」'
                f'<span class="slirn-cut-ptarget">→ 切分后：「{_esc(first.get("target_text") or "")}」</span></span>'
                f'{_act_sel(act)}'
                f'<button class="slirn-btn slirn-btn-xs" data-cut-act="resplit"'
                f' title="修改切分后内容并按新内容重新划分本段">✂️ 重新切分</button>'
                f'<button class="slirn-btn slirn-btn-xs" data-cut-act="play-keep"'
                f' title="连续跳播本段全部保留子段（成片效果）">▶ 试听</button></div>'
            )
            for it in its:
                mk = str(it.get("mark") or "keep")
                sub_orig_text = str(first.get("orig_text") or "")  # 子段共用父段原文（搜原文命中用）
                mk_manual = " ✏️" if it.get("mark_manual") else ""
                mk_label = ("✅ 保留" if mk == "keep" else "❌ 删除") + mk_manual
                fb_title = ' title="无字级时间戳（旧字幕数据）或切分后文字无法对齐 — 已整段带入，可重新生成字幕后重试"' if it.get("fallback") else ""
                fb_mark = " ⚠️" if it.get("fallback") else ""
                rows += (
                    f'<div class="slirn-cut-row sub mark-{mk}" data-task-id="{_esc(task_id)}"'
                    f' data-id="{_esc(it["id"])}" data-mark="{mk}" data-mark-init="{mk}" data-source-i="{si}"'
                    f' data-start-ms="{int(it["start_ms"])}" data-end-ms="{int(it["end_ms"])}"{_spk_attr(it["id"])}{fb_title}'
                    f' data-text="{_esc(it["text"])}" data-orig="{_esc(sub_orig_text)}">'
                    f'<span class="slirn-sub-idx">{_esc(it["id"])}</span>'
                    f'{_spk_badge(it["id"])}'
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
                f' data-start-ms="{int(first["start_ms"])}" data-end-ms="{int(first["end_ms"])}"{_spk_attr(first["id"])}'
                f' data-text="{_esc(first.get("text", ""))}" data-orig="{_esc(orig_text)}">'
                f'<span class="slirn-sub-idx">{si}</span>'
                f'{_spk_badge(first["id"])}'
                f'<span class="slirn-sub-time">{_esc(first["start"])} → {_esc(first["end"])}</span>'
                f'<span class="slirn-rev-badge {kind}" data-kind="{kind}">{kind_label}</span>'
                f'<span class="slirn-sub-text">{text_html}</span>'
                f'{_act_sel(act)}'
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

    # 人员统计条（REQ-033）：已关联 → 服务端直接渲染（结构与 router.cutSpkBarRender
    # 一致），chips 走事件委托（data-action="cut-spk-chip"），容器 data-linked=1
    # 让「重新统计」守卫直接放行。统计口径：仅计未删除记录。
    if spk_link:
        chips = "".join(
            f'<span class="slirn-cut-spk-chip" data-action="cut-spk-chip" data-spk="{s["spk"]}"'
            f' title="点击填入查找框（统计仅计未删除记录）">👤{s["spk"]} · <b>{s["count"]}</b> 条</span>'
            for s in spk_link["stats"]
        )
        spk_bar = (
            '<div id="slirn-cut-spk-bar" class="slirn-cut-spk-bar" data-linked="1">'
            '<div class="slirn-cut-spk-title">👥 人员统计（仅计未删除记录 · 关联已保存，重进任务自动显示）</div>'
            f'<div class="slirn-cut-spk-chips">{chips}</div>'
            '<div class="slirn-cut-spk-find">按人员ID查找：'
            '<input id="slirn-cut-spk-q" type="number" min="1" step="1" placeholder="如 2">'
            '<label class="slirn-cut-spk-skiplbl" title="勾选后「上一条/下一条」只在未删除的记录间跳转">'
            '<input id="slirn-cut-spk-skipdel" type="checkbox" checked>跳过已删除</label>'
            '<button class="slirn-btn slirn-btn-xs" data-action="cut-spk-prev">⬆️ 上一条</button>'
            '<button class="slirn-btn slirn-btn-xs" data-action="cut-spk-next">⬇️ 下一条</button>'
            '<button class="slirn-btn slirn-btn-xs" data-action="cut-spk-delete">❌ 删除该人员全部记录</button>'
            '<button class="slirn-btn slirn-btn-xs" data-action="cut-spk-recount">🧮 重新统计</button>'
            '<span class="slirn-cut-spk-hint">统计只计未删除记录 — 删除非主讲人员后重算即可确认清零；'
            '删除改判需「💾 保存切分决策」落盘</span></div></div>'
        )
        spk_btn_label = "🔄 重新关联人员ID"
    else:
        spk_bar = '<div id="slirn-cut-spk-bar" class="slirn-cut-spk-bar" style="display:none;"></div>'
        spk_btn_label = "👤 关联人员ID"

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
        {spk_bar}
        <div class="slirn-batch-bar" id="slirn-cut-batch">
          <span class="slirn-batch-label">🧮 批量</span>
          行号 <input class="slirn-batch-num" id="slirn-cut-batch-start" type="number" min="1" step="any"
                 title="支持子段行号，如 10.1">
          ～ <input class="slirn-batch-num" id="slirn-cut-batch-end" type="number" min="1" step="any"
                 title="支持子段行号，如 10.2；区间覆盖父段号时含其全部子段">
          改为 <select class="slirn-batch-sel" id="slirn-cut-batch-sel">
            <option value="keep">✅ 保留</option>
            <option value="delete">❌ 删除</option>
            <option value="">↩️ 维持原状</option>
          </select>
          <button type="button" class="slirn-btn slirn-btn-xs" data-action="cut-batch-apply"
                  title="把区间内行的状态批量改为所选目标（整段行=组级决策，子段行=去留标记；确认后应用，保存前可继续调整）">🧮 批量应用</button>
          <span class="slirn-batch-hint">整段行改组级决策、子段行改去留 — 应用后记得「💾 保存切分决策」</span>
        </div>
        <div class="slirn-batch-bar" id="slirn-cut-search">
          <span class="slirn-batch-label">🔎 搜索</span>
          <input class="slirn-search-q" id="slirn-cut-search-q" type="text"
                 placeholder="输入字幕内容片段定位（回车下一条，Shift+回车上一条）">
          <span class="slirn-search-count" id="slirn-cut-search-count"></span>
          <button type="button" class="slirn-btn slirn-btn-xs" data-action="cut-search-prev">⬆ 上一条</button>
          <button type="button" class="slirn-btn slirn-btn-xs" data-action="cut-search-next">下一条 ⬇</button>
          <span class="slirn-batch-hint">整段行按显示文本与原文匹配，子段行按子段文本匹配（不区分大小写）</span>
        </div>
        <div class="slirn-cut-list" id="slirn-cut-list">{rows}</div>
        <div class="slirn-task-actions" style="margin-top:14px;">
            {main_btn}
            <button class="slirn-btn" id="slirn-cut-save" data-action="save-cut-decisions"
                    data-task-id="{_esc(task_id)}">💾 保存切分决策</button>
            <button class="slirn-btn" data-action="cut-spk-link" data-task-id="{_esc(task_id)}">{spk_btn_label}</button>
            <button class="slirn-btn" data-action="play-cut-video" data-task-id="{_esc(task_id)}">▶️ 播放视频</button>
        </div></div>'''


def _render_rough_compose_zone(task_id: str, t, mgr: TaskManager) -> str:
    """必做阶段 · 粗剪合成（REQ-20260916-016 → REQ-20260918-045 由可选改为必做）。

    根据切分修剪的执行口径（保留区间）用上游 VideoClipper 方法合成一版粗剪
    视频，快速预览切分后的整体效果。不推进任务状态，产物 outputs/rough_compose.mp4
    存在即视为本阶段完成（附随片字幕 rough_compose.srt）。口径与切分修剪面板
    完全一致（修订决策实时 + 已保存的手工翻转/改判），行文本并入热词替换已确认的修正。
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
        <div class="slirn-panel-header"><div class="slirn-panel-title">🎥 粗剪合成</div></div>
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
    # ---- REQ-20260916-019：有效字幕 SRT 内嵌（与精剪修订/字幕合成同源） ----
    fine_for_subs = fine_service.load_fine(outputs_dir)
    fmap = {str(e["id"]): e["new_text"]
            for e in (fine_for_subs or {}).get("entries") or []
            if not e.get("reverted")}
    subs_srt = compose_service.format_srt(units, fmap)
    subs_block = ""
    if subs_srt:
        # <pre> 内 textContent 由前端 escapeHtml 处理；这里直接拼字符串
        # （format_srt 输出安全字符：换行 + HH:MM:SS,mmm + 行文本 — 字幕文本
        # 是字幕修订/热词替换产物，前端落入 <pre> 时已用 escapeHtml 转义）
        subs_escaped = subs_srt.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        subs_block = f'''<details class="slirn-rc-subs" open style="margin-top:14px;">
        <summary>📝 切分之后的有效字幕（{len(units)} 行 · {_fmt_dur(keep_ms)}）</summary>
        <pre class="slirn-rc-subs-body">{subs_escaped}</pre>
        <div class="slirn-task-actions" style="margin-top:10px;">
            <button class="slirn-btn slirn-btn-sm" data-action="compose-rough-subs-copy"
                    data-task-id="{_esc(task_id)}">📋 复制 SRT</button>
            <button class="slirn-btn slirn-btn-sm" data-action="compose-rough-subs-download"
                    data-task-id="{_esc(task_id)}">⬇️ 下载 SRT</button>
        </div>
    </details>'''
    else:
        subs_block = '<div class="slirn-sub-meta" style="margin-top:14px;">📝 没有保留内容 — 字幕清单为空</div>'

    # ---- REQ-20260916-019：删除按钮（仅产物存在时） ----
    delete_btn = ""
    if artifact.exists():
        delete_btn = (
            f'<button class="slirn-btn slirn-btn-danger" id="slirn-rc-delete" '
            f'data-action="compose-rough-delete" data-task-id="{_esc(task_id)}" '
            f'style="margin-left:8px;">🗑️ 删除粗剪成片</button>'
        )

    return f'''<div class="slirn-card" style="margin-top:16px;">
        <div class="slirn-panel-header"><div class="slirn-panel-title">🎥 粗剪合成</div></div>
        <div class="slirn-sub-meta">{stats_line}</div>
        <div class="slirn-form-hint">用「处理之后的字幕 + 原视频」走上游 FunClip 的合成方法（VideoClipper）
        把保留区间拼接成一版粗剪成片，快速预览切分后的整体效果，并附随片字幕 rough_compose.srt。
        本步骤为<b>必做</b>阶段 — 成片交付前需在此完成合成；口径与切分修剪面板一致（修订决策实时并入，
        手工翻转/整条改判以「💾 保存切分决策」之后的为准），行文本含热词替换已确认的修正。
        合成需重新编码：44 分钟源实测约 11 分钟，请在后台合成期间继续其它操作。</div>
        <div class="slirn-task-actions" style="margin-top:14px;">
            <button class="slirn-btn slirn-btn-primary" id="slirn-rc-compose" data-action="compose-rough"
                    data-task-id="{_esc(task_id)}">🎬 {'重新合成粗剪视频' if artifact.exists() else '合成粗剪视频'}</button>
            {delete_btn}
        </div>
        <div id="slirn-rc-status" class="slirn-status-msg" style="display:none;"></div>
        {subs_block}
        {preview_html}
    </div>'''


def _render_optimize_zone(task_id: str, t, mgr: TaskManager) -> str:
    """处理剪辑 · 优化字幕（REQ-20260917-030）。

    粗剪成片 + 任务热词 → ASR 重识别（只识别字幕，不区分说话人）→ 大模型
    提取不明确字/词（附替换建议与理由）→ 人工逐处分辨（采纳/不采纳/改替换值）
    → 保存替换对应关系 + 优化后字幕（SRT 可导出）。
    """
    from slirn_home import compose_service, optimize_service

    outputs_dir = mgr.tasks_dir / task_id / "outputs"
    artifact = compose_service.rough_compose_path(outputs_dir)

    def _guide(msg: str, btn: str) -> str:
        return f'''<div class="slirn-card" style="margin-top:16px;">
        <div class="slir-panel-header"><div class="slirn-panel-title">✨ 优化字幕 · 不明确字词</div></div>
        <div class="slirn-empty"><div class="slirn-empty-icon">🚧</div>
            <div class="slirn-empty-text">{_esc(msg)}</div></div>
        <div class="slirn-task-actions" style="margin-top:14px;">{btn}</div></div>'''

    if not artifact.exists():
        return _guide(
            "需要先合成粗剪成片 — 优化字幕以成片为准重新识别字幕",
            '<button class="slirn-btn slirn-btn-primary" data-action="wb-stage" '
            'data-pane="rough_compose">🎥 去粗剪合成</button>',
        )

    hotwords: list[str] = []
    try:
        if t.hotwords_path.exists():
            hotwords = [w for w in t.hotwords_path.read_text(encoding="utf-8").split() if w]
    except Exception:  # noqa: BLE001
        pass

    data = optimize_service.load_optimize(outputs_dir)
    job = optimize_service.job_status(task_id)
    job_state = job.get("state") if job else ("done" if data else "idle")
    hw_chips = "".join(f'<span class="slirn-hw-chip">{_esc(w)}</span>' for w in hotwords)

    # ---- 状态 A：未优化 → 说明 + 热词 + 开始按钮 ----
    if not data:
        from slirn_home import llm_config
        if llm_config.get_current_entry(mgr.tasks_dir.parent) is None:
            return _guide("未注册任何大模型 — 请先点顶栏 ⚙️ 添加模型", "")
        try:
            size_mb = round(artifact.stat().st_size / 1024 / 1024, 1)
            art_disp = f"（{size_mb} MB）"
        except OSError:
            art_disp = ""
        hw_block = (f'<div class="slirn-form-hint" style="margin-top:10px;">'
                    f"<b>任务热词</b>（{len(hotwords)} 个，识别偏置 + 分析参照）：</div>"
                    f'<div class="slirn-hw-chips">{hw_chips}</div>' if hotwords
                    else '<div class="slirn-form-hint" style="margin-top:10px;">'
                         "⚠️ 任务没有热词 — 仍可分析（建议先在「素材准备」补充，"
                         '专有名词/术语的识别和判定会更准）</div>')
        return f'''<div class="slirn-card" style="margin-top:16px;">
        <div class="slirn-panel-header"><div class="slirn-panel-title">✨ 优化字幕 · 不明确字词</div></div>
        <div class="slirn-form-hint">把粗剪成片 <b>{_esc(artifact.name)}</b>{_esc(art_disp)} + 任务热词交给 ASR
        重新识别字幕（只识别文字，不区分说话人），再用大模型做语义分析：提取<b>不明确的字/词</b>
        （疑似误识别、语义不通、与热词矛盾），统计出现次数并定位位置。替换值可逐处编辑，
        人工分辨确认后保存替换对应关系，并可导出优化后的成片字幕 SRT。</div>
        {hw_block}
        <div class="slirn-task-actions" style="margin-top:14px;">
            <button class="slirn-btn slirn-btn-primary" data-action="optimize-start"
                    data-task-id="{_esc(task_id)}">✨ 开始优化字幕</button>
        </div>
        <div id="slirn-opt-status" class="slirn-status-msg" style="display:none;"
             data-task-id="{_esc(task_id)}" data-state="{_esc(job_state)}"></div>
    </div>'''

    # ---- 状态 B：优化结果 → 词频 + 行内出现项（可编辑/采纳）+ 确认保存 ----
    import time as _time
    est = optimize_service.effective_stats(data)
    occs_by_seg: dict[str, list[dict]] = {}
    for o in data.get("occurrences") or []:
        occs_by_seg.setdefault(str(o.get("seg")), []).append(o)
    confirmed = bool(data.get("saved_at"))
    running_html = ""
    if job_state == "running":
        elapsed = int((job.get("finished_at") or _time.time()) - job.get("started_at", _time.time()))
        running_html = (f'⏳ {_esc(job.get("stage") or "处理中")} · '
                        f"{job.get('progress') or 0:.0f}% · 已耗时 {elapsed}s")

    stale_note = ""
    created = str(data.get("created_at") or "")
    try:
        mtime = _time.strftime("%Y-%m-%dT%H:%M:%S", _time.localtime(artifact.stat().st_mtime))
    except OSError:
        mtime = ""
    if mtime and created and mtime > created:
        stale_note = ('<div class="slirn-cut-stale">⚠️ 粗剪成片在优化之后重新合成 — '
                      "建议重新优化以覆盖最新成片</div>")

    # REQ-038：不明确字词频次列表 — 按替换目标词分组（全部出现项，含未采纳），
    # 每词带「处理完成」标识（该词全部出现处都已明确处理 = 采纳/不采纳/编辑过），
    # 可按标识过滤。词身份取分析时的建议替换值（occ.after 落盘值）。
    word_agg: dict[str, dict] = {}
    for o in data.get("occurrences") or []:
        w = str(o.get("after") or "")
        if not w:
            continue
        r = word_agg.setdefault(w, {"total": 0, "done": 0})
        r["total"] += 1
        if o.get("reviewed"):
            r["done"] += 1
    words_html = "".join(
        f'<div class="slirn-opt-word" data-word="{_esc(w)}" data-done="{1 if r["done"] >= r["total"] else 0}">'
        f'<button class="slirn-opt-chip" data-action="opt-word" data-word="{_esc(w)}" '
        f'title="点按筛选该词的出现行，再点取消">{_esc(w)}<b>×{r["total"]}</b></button>'
        f'<span class="slirn-opt-word-prog">{r["done"]}/{r["total"]}</span>'
        f'<span class="slirn-opt-word-badge{" done" if r["done"] >= r["total"] else ""}"'
        f' title="{"该词全部出现处都已明确处理（采纳或不采纳）" if r["done"] >= r["total"] else "还有 " + str(r["total"] - r["done"]) + " 处未处理 — 逐处切换 ✓/✕ 或编辑替换值即计为已处理"}">'
        f'{"✅ 已完成" if r["done"] >= r["total"] else "⬜ 未完成"}</span></div>'
        for w, r in sorted(word_agg.items(), key=lambda kv: (-kv[1]["total"], kv[0]))
    ) or '<span class="slirn-sub-meta">没有生效的替换（可重新优化或直接保存）</span>'
    word_filter_html = (
        '<div class="slirn-opt-word-filters">'
        '<button class="slirn-btn slirn-btn-xs active" data-action="opt-word-filter" data-mode="all">全部</button>'
        '<button class="slirn-btn slirn-btn-xs" data-action="opt-word-filter" data-mode="todo">⬜ 未完成</button>'
        '<button class="slirn-btn slirn-btn-xs" data-action="opt-word-filter" data-mode="done">✅ 已完成</button>'
        '</div>'
    ) if word_agg else ""

    def _line_html(seg: dict) -> str:
        rid = str(seg.get("i"))
        occs = sorted(occs_by_seg.get(rid, []), key=lambda o: int(o.get("pos", 0)))
        # REQ-032 修订：替换栏放右侧独立列后，正文流不再插入 chip（避免重复显示），
        # 整段原文连续渲染，删除线 + 箭头 + 输入框全部在替换栏。
        raw = str(seg.get("text", ""))
        parts = [_esc(raw)]
        occ_chips = []
        for o in occs:
            applied = 1 if o.get("applied", True) else 0
            occ_id = int(o["occ_id"])
            before_txt = _esc(str(o["before"]))
            after_txt = _esc(str(o["after"]))
            reason_txt = _esc(str(o.get("reason") or "不明确片段"))
            toggle_title = _esc("已采纳（保存时替换）" if applied else "已不采纳（保留原文）")
            toggle_mark = "✓" if applied else "✕"
            occ_chips.append(
                f'<span class="slirn-opt-occ" data-occ="{occ_id}" data-applied="{applied}"'
                f' data-word="{after_txt}" data-reviewed="{1 if o.get("reviewed") else 0}">'
                f'<s class="slirn-opt-before" title="{reason_txt}">{before_txt}</s>'
                f'<span class="slirn-opt-arrow">→</span>'
                f'<input class="slirn-opt-after" value="{after_txt}">'
                f'<button class="slirn-btn slirn-btn-xs slirn-opt-toggle" data-action="opt-occ-toggle" '
                f'data-occ="{occ_id}" title="{toggle_title}">{toggle_mark}</button></span>'
            )
        row_words = sorted({str(o["after"]) for o in occs if o.get("applied", True)})
        has_occ = " has-occ" if occs else ""
        occ_col = f'<div class="slirn-opt-col">{"".join(occ_chips)}</div>' if occ_chips else ''
        return (f'<div class="slirn-opt-row{has_occ}" data-id="{_esc(rid)}" data-start-ms="{int(seg.get("start_ms", 0))}"'
                f' data-words="{_esc(chr(10).join(row_words))}">'
                f'<span class="slirn-sub-idx">{_esc(rid)}</span>'
                f'<span class="slirn-fw-time">{_esc(str(seg.get("start") or ""))}</span>'
                f'<div class="slirn-fw-text">{"".join(parts)}</div>'
                f'{occ_col}</div>')

    rows = "".join(_line_html(s) for s in (data.get("segments") or []))
    save_label = "💾 确认替换并保存" if not confirmed else "✅ 已确认 · 再次保存"
    n_occ_rows = len(occs_by_seg)
    n_lines = est["lines"]
    model_disp = _esc(data.get("model") or "")
    return f'''<div class="slirn-card" style="margin-top:16px;">
        <div class="slirn-panel-header"><div class="slirn-panel-title">✨ 优化字幕 · 不明确字词</div></div>
        <div class="slirn-sub-meta">识别 {n_lines} 行 · 不明确 {est["occurrences"]} 处（{est["words"]} 个词）
        · 生效替换 <b>{est["applied"]}</b> 处 · 未采纳 {est["skipped"]} 处 · 模型 {model_disp}
        {" · ✅ 已确认" if confirmed else ""}</div>
        {stale_note}
        <div class="slirn-form-hint" style="margin-top:10px;"><b>不明确字词频次</b>（按替换目标词分组；
        出现处切换过 ✓/✕ 或编辑过替换值即计「已处理」，全部处理完 → ✅）：</div>
        {word_filter_html}
        <div class="slirn-fw-stats" id="slirn-opt-words">{words_html}</div>
        <div class="slirn-form-hint">行内 <s class="slirn-opt-before">删除线</s> = 疑似误识别原文，旁边输入框 = 替换值（可直接编辑）；
        <b>✓</b> 采纳 / <b>✕</b> 不采纳（逐处切换）。点词筛选出现行，点行按成片时间跳播核对。</div>
        <div class="slirn-task-actions" style="margin-top:10px;">
            <button class="slirn-btn" data-action="opt-filter" data-shown="1"
                    data-all-text="🔍 只看有不明确字词的行（{n_occ_rows}/{n_lines}）">🔍 只看有不明确字词的行（{n_occ_rows}/{n_lines}）</button>
        </div>
        <div id="slirn-opt-player-wrap" class="slirn-video-wrap slirn-sub-player-wrap" style="display:none;">
            <video id="slirn-opt-player" controls preload="metadata"></video>
        </div>
        <div class="slirn-fw-list slirn-opt-list" id="slirn-opt-list">{rows}</div>
        <div class="slirn-task-actions" style="margin-top:14px;">
            <button class="slirn-btn slirn-btn-primary" data-action="save-optimize"
                    data-task-id="{_esc(task_id)}">{save_label}</button>
            <button class="slirn-btn" data-action="opt-srt-download" data-task-id="{_esc(task_id)}"
                    {"" if confirmed else 'disabled title="确认保存后可下载"'}>⬇️ 下载优化字幕 SRT</button>
            <button class="slirn-btn" data-action="optimize-start" data-task-id="{_esc(task_id)}"
                    data-has="1">🔄 重新优化</button>
        </div>
        <div id="slirn-opt-status" class="slirn-status-msg" style="{'display:none;' if job_state != 'running' else ''};"
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
        ("创建时间", _esc(_fmt_local(t.created_at))),
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
            <button class="slirn-btn slirn-btn-danger" data-action="delete-task" data-task-id="{_esc(task_id)}" data-task-name="{_esc(t.name)}">🗑️ 删除任务</button>
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
    ("fine_review",     "FINE_SUBTITLE_REVIEWED", "优化字幕", "✨", "重识别粗剪成片字幕，大模型提取不明确字词，人工替换并保存对应关系"),
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
            # 必做阶段（REQ-20260916-016 可选 → REQ-20260918-045 改必做）：
            # 不推进任务状态，产物存在即完成；current 停留于此提示尽快合成
            from slirn_home import compose_service as _comp_mod

            composed = _comp_mod.rough_compose_path(outputs_dir).exists()
            states.append("done" if (composed or cur_rank >= rank[status_name]) else "pending")
        elif key == "fine_review":
            # 优化字幕（REQ-20260917-030）：替换确认保存（saved_at）即完成；
            # 兼容旧任务：fine_revision.json 已确认（旧精剪修订流程）同样视为完成
            from slirn_home import fine_service as _fine_mod
            from slirn_home import optimize_service as _opt_mod

            opt = _opt_mod.load_optimize(outputs_dir)
            fine = _fine_mod.load_fine(outputs_dir)
            done = bool((opt and opt.get("saved_at")) or (fine and fine.get("saved_at")))
            states.append("done" if (done or cur_rank >= rank[status_name]) else "pending")
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
        opt_chip = ""  # REQ-20260918-045：粗剪合成由可选改为必做，不再显示「可选」徽章
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
        "fine_review": _render_optimize_zone(task_id, t, mgr),
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
        {_render_exec_history_card(task_id, mgr)}
    </div>
    <div class="slirn-wb-main">
        <div class="slirn-wb-stages-rail" data-action="wb-toggle-stages"
             title="展开左侧阶段列表"><span>🧭</span><span>阶</span><span>段</span><span>»</span></div>
        <div class="slirn-card slirn-wb-stages">
            <div class="slirn-wb-stages-head"><span class="slirn-wb-stages-title">🧭 阶段</span>
                <button class="slirn-btn-mini" data-action="wb-toggle-stages"
                        title="收起阶段列表，加宽右侧工作区">« 收起</button></div>
            <label class="slirn-wb-autonext" title="开启后：当前阶段的工作完成（识别/保存/合成完成）时，自动切换到下一阶段页面">
                <input type="checkbox" id="slirn-wb-autonext"> 完成后自动进下一阶段
            </label>
            {stage_items}
        </div>
        <div class="slirn-wb-panes">{pane_html}</div>
    </div>
    </div>'''


# =============== 热词库 ===============

def _render_exec_history_card(task_id: str, mgr) -> str:
    """REQ-20260918-048 — 工作台顶部的执行历史折叠卡片（字幕生成 + 粗剪合成）。

    读 outputs/execution_history.json 倒序最多 10 条；落盘数据，无需实时刷新。
    复用 REQ-20260918-044 的 .slirn-col 折叠壳：JS 自动 wrap，零新逻辑。
    """
    from slirn_home import execution_history

    outputs_dir = mgr.tasks_dir / task_id / "outputs"
    items = execution_history.load_history(outputs_dir)
    # 倒序：最新在前；空文件 → 友好空态
    items.sort(key=lambda x: (float(x.get("started_at") or 0), str(x.get("id") or "")), reverse=True)
    total = len(items)
    shown = items[:10]

    if not shown:
        body = ('<div class="slirn-form-hint slirn-exec-empty">'
                '尚无执行记录。点击「字幕生成 / 粗剪合成」开始第一次执行后，这里会出现历史。'
                '</div>')
    else:
        KIND_LABEL = {
            execution_history.KIND_SUBTITLE_GENERATION: ("🎙 字幕生成", "字幕生成"),
            execution_history.KIND_ROUGH_COMPOSE: ("🎥 粗剪合成", "粗剪合成"),
        }
        rows = []
        # 倒序：shown[0] 是最近一次；真实序号 = total - i（最大 = 最新）
        for i_row, it in enumerate(shown):
            real_n = total - i_row
            kind_icon, _kind_text = KIND_LABEL.get(it.get("kind") or "", ("⚙ 操作", "操作"))
            status = it.get("status") or "running"
            if status == "success":
                badge = '<span class="slirn-exec-ok">✅ 成功</span>'
            elif status == "failed":
                badge = '<span class="slirn-exec-fail">❌ 失败</span>'
            else:
                badge = '<span class="slirn-exec-running">⏳ 运行中</span>'
            start_iso = (it.get("started_at_iso") or "").replace("T", " ")[:19]
            end_iso = (it.get("finished_at_iso") or "").replace("T", " ")[:19] or "—"
            duration = execution_history.format_duration(it.get("duration_ms"))
            # extra 摘要：subtitle → segments/speakers；compose → segments/output
            extra = it.get("extra") or {}
            extra_bits = []
            if "segments" in extra:
                extra_bits.append(f"{extra['segments']} 段")
            if extra.get("speakers"):
                extra_bits.append(f"{extra['speakers']} 位说话人")
            if extra.get("intervals"):
                extra_bits.append(f"{extra['intervals']} 区间")
            extra_str = (" · " + " / ".join(extra_bits)) if extra_bits else ""
            err_html = ""
            if it.get("error"):
                err_html = (f'<div class="slirn-exec-err">{_esc(str(it["error"])[:300])}</div>')
            rows.append(
                f'<div class="slirn-exec-row" data-status="{_esc(status)}">'
                f'  <span class="slirn-exec-idx">#{real_n}</span>'
                f'  <span class="slirn-exec-kind">{_esc(kind_icon)} · {_esc(start_iso)} → {_esc(end_iso)}</span>'
                f'  <span class="slirn-exec-dur">{_esc(duration)}</span>'
                f'  {badge}{extra_str}'
                f'  {err_html}'
                f'</div>'
            )

        more = (f'<div class="slirn-form-hint">共 {total} 次记录，仅展示最近 {len(shown)} 次</div>'
                if total > len(shown) else '')
        body = "".join(rows) + more

    # 折叠壳：与 REQ-20260918-044 的 .slirn-col 自动 wrap 兼容；key 用 exec:history
    return (
        f'<div class="slirn-col slirn-exec-card" data-col-key="exec:history">'
        f'  <div class="slirn-col-head"><button type="button" class="slirn-col-btn" data-action="col-toggle">'
        f'    <span class="slirn-col-chev">▾</span>📜 执行历史'
        f'    <span class="slirn-exec-count">{total} 次</span>'
        f'  </button></div>'
        f'  <div class="slirn-exec-body">{body}</div>'
        f'</div>'
    )


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

ROUTER_JS = '<script defer src="/slirn/static/router.js"></script>'
# 说明：ROUTER_JS 原本是 ~115KB 的内联 JS 字符串，通过 gr.HTML(head=...) 注入。
# Gradio 6.17.3 的 head 传输链路会把脚本文本里的反斜杠转义解码
# （\n -> 换行、\x20 -> 空格、\\ -> \、孤立的 \ 被删除），内联 JS 因此产生
# SyntaxError 而整体失效（页面所有 data-action 按钮无响应，2026-09-17 排查）。
# 现在改为：JS 源码放 static/router.js，由 _register_slirn_api 注册的
# GET /slirn/static/router.js 提供，这里只注入 <script src>（无反斜杠，安全）。


# ============================================================
# CSS 注入
# ============================================================

def _read_css() -> str:
    """读取 home.css — 用 launch(css=...) 注入，因为 Gradio 6.x 会用 _deprecated_css 覆盖 app.css。"""
    css_path = Path(__file__).parent / "static" / "home.css"
    return css_path.read_text(encoding="utf-8")


def _build_head() -> str:
    """构造 head 注入：主题切换 JS（内联，无反斜杠，安全）+ 事件路由 JS（静态文件引用）。

    注意：head= 传输链路会解码反斜杠转义，任何含 \\ 的内联 JS 都会被破坏 —— 见 ROUTER_JS 注释。
    """
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
    JS 注入走 gr.HTML 组件的 head= 参数（见 build_app）：theme_js 内联（无反斜杠），
    事件路由 JS 用 <script src="/slirn/static/router.js"> 静态文件（head= 传输会破坏反斜杠）。
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
        # 所以 JS 必须挂在 gr.HTML 组件的 head= 参数上 — 前端运行时会用 DOMParser
        # 解析后把 <script> 重新 createElement/appendChild 到 document.head（src 脚本按 src 去重）。
        # 但注意：head= 传输链路会解码反斜杠转义，内联 JS 含 \ 就会被破坏 —
        # 所以这里只放 theme_js（无反斜杠）+ <script src> 引用，路由 JS 走静态文件。
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

    # 事件路由 JS — 静态文件提供。head= 内联注入会被反斜杠转义解码破坏（见 ROUTER_JS 注释），
    # 所以 _build_head 只注入 <script src>，实际内容从这里出。no-cache 方便改版后刷新即生效。
    from fastapi.responses import FileResponse

    router_js_path = Path(__file__).parent / "static" / "router.js"

    @app.app.get("/slirn/static/router.js")
    async def serve_router_js():
        return FileResponse(
            router_js_path,
            media_type="text/javascript; charset=utf-8",
            headers={"Cache-Control": "no-cache"},
        )

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
        """删除任务（REQ-20260917-034）— 真删：整个任务目录从磁盘移除，不可恢复。

        失败必须如实报错（旧版吞异常假报「已删除」，任务刷新后复活）；
        Windows 下文件被占用（如视频预览握着句柄）是常见失败原因。
        """
        tid = (body.get("task_id") or "").strip()
        if not tid:
            return _err("缺少 task_id")
        try:
            mgr.delete(tid)
        except TaskNotFoundError as e:
            return _err(f"任务不存在或已删除: {e}")
        except OSError as e:
            log.exception("删除任务 %s 失败（真删未完成）", tid)
            return _err(f"删除失败: {e} — 可能有文件正被预览/占用，关闭预览后重试")
        return _ok(_render_task_list(mgr), toast="✅ 已删除（任务文件已从磁盘移除）")

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

        # 说话人分离开关（REQ-20260917-029）：默认开；旧客户端不带该字段 → 开
        sd_val = body.get("sd")
        sd_on = True if sd_val is None else bool(sd_val)
        started = _asr.start_job(
            tid, video, hotwords, outputs_dir,
            source=source, base_offset_ms=base_off_ms, sd=sd_on, on_success=_on_success,
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

    @app.app.post("/slirn/api/cut_speaker_link")
    async def cut_speaker_link(body: dict = Body(default_factory=dict)):
        """切分修剪阶段关联人员ID（REQ-20260917-031）：按时间段重叠对齐 subtitle 段级 spk。

        服务端实时重建切分清单预览（与面板同口径：修订实时 + 已保存手工决策并入），
        返回每行人员编号 + 按人员统计（总数/执行口径保留数）。不落盘 — 再次点击
        即按最新时间窗重算；删除改判的落盘走既有 save_cut_decisions。
        """
        from slirn_home import asr_service, cut_speaker, cutlist_service, revision_service

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
        saved = cutlist_service.load_cutlist(outputs_dir)
        cutlist = cutlist_service.build_cutlist(
            sub_meta, rev,
            manual_marks=(saved or {}).get("manual_marks") if saved else None,
            actions=(saved or {}).get("actions") if saved else None,
        )
        link = cut_speaker.link_speakers(sub_meta, cutlist)
        if not link.get("available"):
            return _err(
                "字幕无人员编号 — 该任务生成字幕时未开启说话人分离（或为旧任务）。"
                "请到「字幕生成」阶段开启「区分说话人」重新生成后再关联"
            )
        # REQ-033：关联状态落盘 — 重进面板时徽章 + 统计条直接渲染（渲染端现算，快照备查）
        saved_link = cut_speaker.save_link(outputs_dir, link)
        return _ok("", link=link, rows=len(cutlist.get("items") or []),
                   linked_at=saved_link.get("linked_at"))

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
        """启动粗剪合成（REQ-20260916-018，必做阶段）：上游 VideoClipper 方法合成。

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

    @app.app.post("/slirn/api/compose_rough_delete")
    async def compose_rough_delete(body: dict = Body(default_factory=dict)):
        """删除粗剪成片（REQ-20260916-019）：用户主动清理产物以便重合成。"""
        from slirn_home import compose_service

        tid = (body.get("task_id") or "").strip()
        if not tid:
            return _err("缺少 task_id")
        try:
            mgr.get(tid)
        except Exception as e:  # noqa: BLE001
            return _err(f"任务不存在: {e}")
        outputs_dir = mgr.tasks_dir / tid / "outputs"
        res = compose_service.delete_rough_compose(outputs_dir)
        return _ok("", deleted=res["deleted"],
                   toast="🗑️ " + res["message"] if res["deleted"] else None,
                   removed=res["removed"], remaining=res["remaining"],
                   message=res["message"])

    @app.app.post("/slirn/api/compose_rough_preview_subs")
    async def compose_rough_preview_subs(body: dict = Body(default_factory=dict)):
        """预览「切分之后的有效字幕」（REQ-20260916-019）：SRT 格式字符串。

        行集口径与 compose_rough 一致（修订实时 + 已保存手工翻转/改判），
        行文本并入热词替换未撤销行的 new_text；按 start_ms 升序输出 SRT。
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
            return _err("字幕修订还有未决策条目")
        sub_meta = asr_service.load_subtitle(outputs_dir)
        if not (sub_meta and sub_meta.get("segments")):
            return _err("缺少字幕生成产物")
        saved = cutlist_service.load_cutlist(outputs_dir)
        try:
            cutlist = cutlist_service.build_cutlist(
                sub_meta, rev,
                manual_marks=(saved or {}).get("manual_marks") if saved else None,
                actions=(saved or {}).get("actions") if saved else None,
            )
        except Exception as e:  # noqa: BLE001
            return _err(f"计算保留行失败: {e}")
        units = cutlist_service.effective_keep_units(cutlist)
        if not units:
            return _err("没有保留内容")
        fine = fine_service.load_fine(outputs_dir)
        fmap = {str(e["id"]): e["new_text"]
                for e in (fine or {}).get("entries") or [] if not e.get("reverted")}
        srt = compose_service.format_srt(units, fmap)
        keep_ms = sum(int(u["end_ms"]) - int(u["start_ms"]) for u in units)
        return _ok("", srt=srt, lines=len(units), keep_ms=keep_ms)

    @app.app.post("/slirn/api/execution_history")
    async def execution_history_endpoint(body: dict = Body(default_factory=dict)):
        """REQ-20260918-048 — 执行历史记录（字幕生成 + 粗剪合成）。

        返回该任务 outputs/execution_history.json 的全部记录（按 started_at 倒序）。
        工作台面板初始化时调用渲染折叠区，无需实时刷新（落盘数据，下次进入自然新）。
        """
        from slirn_home import execution_history

        tid = (body.get("task_id") or "").strip()
        if not tid:
            return _err("缺少 task_id")
        try:
            mgr.get(tid)
        except Exception as e:  # noqa: BLE001
            return _err(f"任务不存在: {e}")
        outputs_dir = mgr.tasks_dir / tid / "outputs"
        items = execution_history.load_history(outputs_dir)
        # 倒序（最新在前）；同秒多条按 id 倒序兜底
        items.sort(key=lambda x: (float(x.get("started_at") or 0), str(x.get("id") or "")), reverse=True)
        return _ok("", items=items)

    @app.app.post("/slirn/api/optimize_subtitle")
    async def optimize_subtitle(body: dict = Body(default_factory=dict)):
        """启动优化字幕（REQ-20260917-030）：ASR 重识别粗剪成片 → 大模型提取不明确字词。

        已有优化结果时须 force（前端二次确认）。热词为空不阻塞（提示级）。
        """
        from slirn_home import compose_service, llm_config, optimize_service

        tid = (body.get("task_id") or "").strip()
        if not tid:
            return _err("缺少 task_id")
        try:
            t = mgr.get(tid)
        except Exception as e:  # noqa: BLE001
            return _err(f"任务不存在: {e}")
        outputs_dir = mgr.tasks_dir / tid / "outputs"
        artifact = compose_service.rough_compose_path(outputs_dir)
        if not artifact.exists():
            return _err("缺少粗剪成片 — 请先在「粗剪合成」阶段合成")
        if optimize_service.load_optimize(outputs_dir) and not body.get("force"):
            return _err("已存在优化结果 — 重新优化将覆盖并重置全部采纳决定，请确认后重试")
        hotwords: list[str] = []
        try:
            if t.hotwords_path.exists():
                hotwords = [w for w in t.hotwords_path.read_text(encoding="utf-8").split() if w]
        except Exception:  # noqa: BLE001
            pass
        entry = llm_config.get_current_entry(repo_root)
        if entry is None:
            return _err("未注册任何大模型 — 请先点顶栏 ⚙️ 添加模型")
        started = optimize_service.start_job(
            tid, artifact, hotwords, t.name, outputs_dir, entry=entry,
        )
        if not started:
            return _ok("", toast="⏳ 该任务已在优化中，请等待完成")
        return _ok("", toast="✨ 优化字幕已开始（先识别成片，再大模型分析，可离开本页）",
                   job={"state": "running", "stage": "加载模型"})

    @app.app.post("/slirn/api/optimize_subtitle_status")
    async def optimize_subtitle_status(body: dict = Body(default_factory=dict)):
        """优化字幕进度轮询（REQ-20260917-030）。"""
        from slirn_home import optimize_service

        tid = (body.get("task_id") or "").strip()
        if not tid:
            return _err("缺少 task_id")
        j = optimize_service.job_status(tid)
        if j is None:  # 服务重启后 job 丢失 — 产物在即视为完成
            data = optimize_service.load_optimize(mgr.tasks_dir / tid / "outputs")
            if data:
                st = (data.get("stats") or {})
                return _ok("", job={
                    "state": "done",
                    "lines": st.get("lines", 0),
                    "occurrences": st.get("occurrences", 0),
                })
            return _ok("", job={"state": "idle"})
        import time as _time
        j["elapsed_s"] = int((j.get("finished_at") or _time.time()) - j.get("started_at", _time.time()))
        return _ok("", job=j)

    @app.app.post("/slirn/api/save_optimize_subtitle")
    async def save_optimize_subtitle(body: dict = Body(default_factory=dict)):
        """保存优化字幕人工替换决定（REQ-20260917-030）：决定合并落盘 + 阶段完成。

        decisions：[{occ_id, applied, after}] 全量口径 — 未列出的出现项一律不
        采纳；after 可为人工编辑值（生效要求非空且 ≠ 原文）。幂等推进
        FINE_SUBTITLE_REVIEWED。
        """
        from tasklib.models import TaskStatus

        from slirn_home import optimize_service

        tid = (body.get("task_id") or "").strip()
        if not tid:
            return _err("缺少 task_id")
        try:
            mgr.get(tid)
        except Exception as e:  # noqa: BLE001
            return _err(f"任务不存在: {e}")
        decisions = body.get("decisions")
        if not isinstance(decisions, list):
            return _err("decisions 必须是数组")
        outputs_dir = mgr.tasks_dir / tid / "outputs"
        try:
            data, applied_n = optimize_service.save_decisions(outputs_dir, decisions)
        except Exception as e:  # noqa: BLE001
            return _err(f"保存失败: {e}")
        est = optimize_service.effective_stats(data)
        mgr.update_status(tid, TaskStatus.FINE_SUBTITLE_REVIEWED)  # 幂等：结果在盘即完成
        return _ok("", toast=(f"✅ 已保存替换对应关系：生效 {applied_n} 处"
                              f"（未采纳 {est['skipped']} 处）"), stats=est)

    @app.app.post("/slirn/api/optimized_srt")
    async def optimized_srt(body: dict = Body(default_factory=dict)):
        """下载优化后成片字幕 SRT（REQ-20260917-030，时间基 = 粗剪成片）。"""
        from slirn_home import optimize_service

        tid = (body.get("task_id") or "").strip()
        if not tid:
            return _err("缺少 task_id")
        data = optimize_service.load_optimize(mgr.tasks_dir / tid / "outputs")
        if data is None:
            return _err("还没有优化结果 — 请先执行「开始优化字幕」")
        if not data.get("saved_at"):
            return _err("尚未确认保存 — 请先「确认替换并保存」")
        segments = data.get("segments") or []
        return _ok("", srt=optimize_service.build_srt(segments), lines=len(segments))

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
        segment_temp = None  # 无截取参数时不进 if，先定义避免下方 UnboundLocalError
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
