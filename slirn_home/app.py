"""Gradio Blocks 构造 — 全 HTML 自定义渲染，玻璃拟态统一风格。"""

from __future__ import annotations

import html
import json
import os
import re
import logging
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import gradio as gr
from fastapi import Body, File, Form as _Form, Request, UploadFile  # REQ-061：精剪视频上传用；REQ-075：Request 读 X-Slirn-Auto

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
        # REQ-20260919-075：终态判断同时认 MUXED（旧任务）+ FINE_CUT_DONE（新任务）
        elif s.status in (TaskStatus.MUXED, TaskStatus.FINE_CUT_DONE):
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
            ("✅", "已完成", stats["done"], "精剪视频导出完成"),
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
    # REQ-20260919-075：终态同时认 MUXED + FINE_CUT_DONE
    if status in (TaskStatus.MUXED, TaskStatus.FINE_CUT_DONE):
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
                <button class="slirn-btn slirn-btn-sm slirn-btn-primary" data-action="open-workbench" data-task-id="{_esc(s.task_id)}" data-task-label="{_esc(s.name)}">✂️ 剪辑</button>
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

    # REQ-20260919-068：字幕修订阶段关联人员 ID（与切分修剪同口径）。
    # 关联状态落盘后，重进面板时现算（按当前 revision entries + subtitle spk），
    # 保证统计口径永远最新；快照仅供人工检查。
    rev_spk_link = None
    rev_spk_rows: dict[str, int] = {}
    if entries and sub_meta:
        from slirn_home import rev_speaker
        saved_link = rev_speaker.load_link(outputs_dir)
        if saved_link is not None:
            # 现算（不读快照的 rows/stats，但若字幕无 spk → 用 saved_link 提示）
            fresh = rev_speaker.link_speakers(sub_meta, rev)
            if fresh.get("available"):
                rev_spk_link = fresh
                rev_spk_rows = fresh.get("rows") or {}
            else:
                rev_spk_link = None  # 当前 subtitle 无 spk，不渲染统计条
        # saved_link is None → 不渲染 spk_bar（按钮显示「👤 关联人员ID」）

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
        # REQ-20260919-068：行级人员编号（仅在已关联时显示）
        _i_str = str(int(e["i"]))
        _spk = rev_spk_rows.get(_i_str)
        _spk_attr = f' data-spk="{int(_spk)}"' if _spk else ""
        _spk_badge_html = f'👤{int(_spk)}' if _spk else ""
        rows += (
            f'<div class="slirn-rev-row{" open" if open_detail else ""}" data-task-id="{_esc(task_id)}"'
            f' data-start-ms="{int(e.get("start_ms", 0))}" data-end-ms="{int(e.get("end_ms", 0))}"'
            f' data-sugg="{_esc(cat)}" data-decision="{_esc(decision)}" data-final="{_esc(final_kind)}"'
            f' data-i="{int(e["i"])}"{_spk_attr}'
            f' data-text="{_esc(e.get("text", ""))}">'
            f'<div class="slirn-rev-line">'
            f'<span class="slirn-sub-idx">{int(e["i"])}</span>'
            f'<span class="slirn-rev-spk">{_spk_badge_html}</span>'
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

    # REQ-20260919-068：字幕修订阶段关联人员 ID 后的统计条 + 按钮文案。
    # 模式与切分修剪 cut_spk_bar 完全对齐（含 chips、查找、上/下一条、删除、重算）。
    if rev_spk_link and rev_spk_link.get("available"):
        chips = "".join(
            f'<span class="slirn-rev-spk-chip" data-action="rev-spk-chip" data-spk="{s["spk"]}"'
            f' title="点击填入查找框（统计仅计未删除决策行）">👤{s["spk"]} · <b>{s["count"]}</b> 条</span>'
            for s in rev_spk_link["stats"]
        )
        rev_spk_bar = (
            '<div id="slirn-rev-spk-bar" class="slirn-rev-spk-bar" data-linked="1">'
            '<div class="slirn-rev-spk-title">👥 人员统计（仅计未删除决策行 · 关联已保存，重进任务自动显示）</div>'
            f'<div class="slirn-rev-spk-chips">{chips}</div>'
            '<div class="slirn-rev-spk-find">按人员ID查找：'
            '<input id="slirn-rev-spk-q" type="number" min="1" step="1" placeholder="如 2">'
            '<label class="slirn-rev-spk-skiplbl" title="勾选后「上一条/下一条」只在未删除决策行间跳转">'
            '<input id="slirn-rev-spk-skipdel" type="checkbox" checked>跳过已删除</label>'
            '<button class="slirn-btn slirn-btn-xs" data-action="rev-spk-prev">⬆️ 上一条</button>'
            '<button class="slirn-btn slirn-btn-xs" data-action="rev-spk-next">⬇️ 下一条</button>'
            '<button class="slirn-btn slirn-btn-xs" data-action="rev-spk-delete">❌ 删除该人员全部记录</button>'
            '<button class="slirn-btn slirn-btn-xs" data-action="rev-spk-recount">🧮 重新统计</button>'
            '<span class="slirn-rev-spk-hint">统计只计未删除决策行 — 删除非主讲人员后重算即可确认清零；'
            '删除改判需「💾 保存修订决策」落盘</span></div></div>'
        )
        rev_spk_btn_label = "🔄 重新关联人员ID"
    else:
        rev_spk_bar = '<div id="slirn-rev-spk-bar" class="slirn-rev-spk-bar" style="display:none;"></div>'
        rev_spk_btn_label = "👤 关联人员ID"

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
        {rev_spk_bar}
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
            <button class="slirn-btn" data-action="rev-spk-link" data-task-id="{_esc(task_id)}" title="按时间段重叠把 subtitle 段级 spk 对齐到修订每行">{rev_spk_btn_label}</button>
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
        # REQ-20260918-057B：文字输入过滤（与状态过滤 AND 组合）
        '<span class="slirn-opt-word-text-filter">'
        '<input type="text" id="slirn-opt-word-text" placeholder="🔍 输入词文本过滤" autocomplete="off">'
        '<button type="button" class="slirn-opt-word-text-clear" data-action="opt-word-text-clear" '
        'title="清空过滤" style="display:none;">✕</button>'
        '</span>'
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
        # REQ-20260918-054：补 data-end-ms，前端 timeupdate 按 [start,end) 命中行
        return (f'<div class="slirn-opt-row{has_occ}" data-id="{_esc(rid)}" '
                f'data-start-ms="{int(seg.get("start_ms", 0))}" '
                f'data-end-ms="{int(seg.get("end_ms", 0))}"'
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
# REQ-20260919-075：去掉第八阶段「字幕合成/MUXED」（已被精剪视频的最终导出覆盖）；
# 任务终态改为 FINE_CUT_DONE。TaskStatus.MUXED 枚举保留（向后兼容），但不再有 UI 阶段引导到它。
_WB_STAGES = [
    ("assets",          "ASSETS_READY",         "素材准备", "📦", "上传视频 · 时间截取 · 任务热词"),
    ("subtitle",        "SUBTITLE_GENERATED",   "字幕生成", "🎙", "FunASR seaco-paraformer + 热词识别"),
    ("subtitle_review", "SUBTITLE_REVIEWED",    "字幕修订", "📝", "对照视频逐段校对、修改字幕文本与时间"),
    ("rough_cut",       "ROUGH_CUT_DONE",       "切分修剪", "✂️", "按修订决策带入保留/更正段，切分段父编号+子编号"),
    ("rough_compose",  "FINE_SUBTITLE_DONE",   "粗剪合成", "🎥", "按切分保留内容用上游 VideoClipper 合成粗剪视频（含随片字幕）"),
    ("fine_review",     "FINE_SUBTITLE_REVIEWED", "优化字幕", "✨", "重识别粗剪成片字幕，大模型提取不明确字词，人工替换并保存对应关系"),
    ("fine_cut",        "FINE_CUT_DONE",        "精剪视频", "🎬", "按精剪段生成成品视频"),
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


# REQ-20260919-061：精剪视频·四素材合成器 — 数据结构辅助函数
_FINE_MATERIAL_KINDS = ("video", "subtitle", "cover", "bg", "reference", "audio")
_FINE_MATERIAL_LABELS = {
    "video":     ("🎬", "粗剪视频", "mp4/mov"),
    "subtitle":  ("📝", "字幕文件", "srt"),
    "cover":     ("🖼", "封面图片", "png/jpg"),
    "bg":        ("🎨", "背景图片", "png/jpg"),
    "reference": ("🤖", "参考位置关系图", "png/jpg（AI 解析用）"),
    "audio":     ("🎵", "背景音乐", "mp3/wav/m4a"),
}
# 用户补充 · x/y 改为像素坐标（基于 1920×1080 设计空间）：
# - 0.35 * 1920 ≈ 672；0.9 * 1080 = 972；0.7 * 1920 ≈ 1344；0.85 * 1080 = 918
# - 设计空间保证：720p 输出时 ffmpeg 在末尾 scale，WYSIWYG 不变
_FINE_DESIGN_W = 1920
_FINE_DESIGN_H = 1080
_FINE_LAYOUT_SCHEMA = 2  # _schema=2：x/y 是像素；旧 0-1 数据自动迁移
# REQ-20260919-062 v19：颜色 picker 只接受完整 #RRGGBB（拒绝 #fff 简写和非 hex 字符串）
_HEX_COLOR_OK = re.compile(r"^#[0-9A-Fa-f]{6}$")
# REQ-20260919-061 扩展：cover 不再是「角标小图」，改为「片头全屏海报」；
# 旧字段 x/y/scale 在迁移时被保留（数据不丢），新增 duration 字段控制展示秒数。
_FINE_LAYOUT_DEFAULTS = {
    # REQ-20260919-061 用户补充：视频不铺满画布，只占左侧 70%（右侧让背景图人员区透过）
    # crop_* 是从源视频里再截一个矩形（归一化 0-1），与画布 x/y/scale 正交。
    # 默认全幅（不裁剪）；点击「16:9 居中」预设切换到 16:9 居中矩形。
    "video":    {"x": 0,    "y": 0,    "scale": 0.7,
                 # crop_* 是从源视频里截一个矩形（设计空间 1920×1080 像素），
                 # 渲染时按源视频实际尺寸等比换算到 iw/ih
                 "crop_x": 0, "crop_y": 0, "crop_w": 1920, "crop_h": 1080,
                 "enabled": True,
                 # REQ-20260919-062 v5 用户反馈：把视频展示的区域限定在所检测区域之内。
                 # viewport 是背景图白色区域检测的结果（设计空间像素），
                 # 设置后 x/y/scale 在 save_fine_layout 时被自动夹紧到 viewport 内。
                 # None / 缺失 = 不限定（视频可超出画布任意位置）。
                 "viewport": None,
                 # REQ-20260919-062 v18 用户反馈：「视频源裁剪里的锁定 16:9 比例」也要保存。
                 # True = crop_w/crop_h 拖动时按 16:9 联动（防变形）；
                 # False = 任意调整（1:1 等预设才能任意设 w=h）。
                 # 默认 True，与前端默认勾选保持一致。
                 "crop_aspect_lock": True},
    "subtitle": {"x": 672,  "y": 972,  "scale": 1.0, "enabled": True},
    "cover":    {"enabled": False, "duration": 2.0},
    "bg":       {"x": 0,    "y": 0,    "scale": 1.0, "enabled": False},
}
_FINE_FONT_DEFAULTS = {
    "size":         36,
    "color":        "#FFFFFF",  # REQ-20260919-062：字幕文字本身颜色（libass PrimaryColour）
    "stroke_width": 2,
    "stroke_color": "#000000",
    "bg_enabled":   False,
    "bg_color":     "#000000",
    "bg_opacity":   0.6,
    "bg_radius":    4,
    "bold":         True,
    "align":        "center",
    "family":       "STHeitiMedium",
}
# REQ-20260919-061 扩展：背景音乐 4 项（启用 + 音量 + 淡入/淡出）
# volume 默认 0.4 — 不压过说话人语音
_FINE_AUDIO_DEFAULTS = {
    "enabled":  False,
    "volume":   0.4,
    "fade_in":  0.0,
    "fade_out": 0.0,
}
_FINE_OUTPUT_DEFAULTS = {
    "resolution":  "1080p",  # 720p / 1080p / source
    "codec":       "h264",
    "audio_codec": "aac",
}


# REQ-20260920-078：系统默认 BGM 备选列表（绝对路径硬编码，不动态扫描）。
# 来源目录：D:\tmp\tttttt\（用户给的固定路径，不进 git）。
# 启动时校验 available；缺失则 UI 灰显，不报错。
_DEFAULT_BGMS_DIR = Path(r"D:\tmp\tttttt")
_DEFAULT_BGMS = [
    {"id": "lofi_beat_1",    "name": "Pretty John — Lo-Fi Beat",
     "filename": "prettyjohn1-lo-fi-beat-580021.mp3"},
    {"id": "lofi_love_loop", "name": "Sonican — Sentimental Jazzy Love",
     "filename": "sonican-lo-fi-music-loop-sentimental-jazzy-love-473154.mp3"},
    {"id": "the_mountain",   "name": "The Mountain — Lo-Fi Beat",
     "filename": "the_mountain-lo-fi-beat-567432.mp3"},
    {"id": "zephira_lofi",   "name": "Zephira Music — Lo-Fi",
     "filename": "zephiramusic-lo-fi-581502.mp3"},
    {"id": "zephira_relax",  "name": "Zephira Music — Relaxing Lo-Fi",
     "filename": "zephiramusic-relaxing-lo-fi-587547.mp3"},
]


def _get_default_bgms() -> list[dict]:
    """返回系统默认 BGM 列表（启动时校验存在性，available 字段标识）。"""
    out = []
    for bgm in _DEFAULT_BGMS:
        p = _DEFAULT_BGMS_DIR / bgm["filename"]
        size = 0
        if p.exists():
            try:
                size = p.stat().st_size
            except OSError:
                size = 0
        out.append({**bgm, "available": p.exists(), "size_bytes": size})
    return out


# REQ-20260919-061 用户补充：video/subtitle 既可手动上传，也可默认从上游产物获取
_FINE_AUTO_KINDS = frozenset({"video", "subtitle"})


def _fine_upstream_path(task_id: str, kind: str, mgr) -> Path | None:
    """返回上游产物绝对路径（None = 无上游或不支持 auto）。

    - video → tasks/{tid}/outputs/rough_compose.mp4（粗剪合成阶段产物）
    - subtitle → optimize_subtitle.json → 落盘到 tmp/optimized_subs.srt
    """
    if kind == "video":
        from slirn_home.compose_service import rough_compose_path
        p = rough_compose_path(mgr.tasks_dir / task_id / "outputs")
        return p if p.exists() else None
    if kind == "subtitle":
        from slirn_home import optimize_service
        outputs_dir = mgr.tasks_dir / task_id / "outputs"
        data = optimize_service.load_optimize(outputs_dir)
        if not data or not data.get("saved_at"):
            return None
        segments = data.get("segments") or []
        srt_text = optimize_service.build_srt(segments)
        tmp_dir = mgr.tasks_dir / task_id / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        out = tmp_dir / "optimized_subs.srt"
        out.write_text(srt_text, encoding="utf-8")
        return out
    return None


def _fine_upstream_label(task_id: str, kind: str, mgr) -> str:
    """上游产物的可读文件名（用于 UI 显示）。"""
    p = _fine_upstream_path(task_id, kind, mgr)
    return p.name if p else ""


def _resolve_mat_abs(mgr, task_id: str, materials: dict, kind: str) -> Path | None:
    """把 materials[kind].path（相对路径）解析为绝对路径（用于 ffmpeg input）。

    路径约定有两种历史来源：
    - 自动获取（compose_service.rough_compose_path / optimize_service.build_srt）：
      写的是 `mgr.tasks_dir.relative_to(mgr.repo_root)` → 不含 `tasks/` 前缀
      例：`20260918-022\\outputs\\rough_compose.mp4`
    - 上传（upload_fine_material_form）：
      写的是 `save_path.relative_to(repo_root)` → 含 `tasks/` 前缀
      例：`tasks\\20260918-022\\upload\\cover.png`

    这里两种都试一下：先 tasks_dir + pp，回退 repo_root + pp。
    """
    mat = materials.get(kind) or {}
    p = mat.get("path")
    if not p:
        return None
    pp = Path(p)
    if pp.is_absolute():
        return pp
    # 候选 1：tasks_dir + pp（自动获取约定）
    cand1 = (mgr.tasks_dir / pp).resolve()
    if cand1.exists():
        return cand1
    # 候选 2：repo_root + pp（上传约定，路径含 `tasks/` 前缀）
    cand2 = (mgr.repo_root / pp).resolve()
    if cand2.exists():
        return cand2
    # REQ-20260920-083：候选 3 — tasks_dir + task_id + pp（兼容 select_default_bgm 旧数据，
    # 旧版写 "materials/audio/<id>.mp3" 不含前缀；文件实际在 tasks/<tid>/materials/audio/）。
    # 用 exists() 检查兜底 — 不会误命中其他 kind 的同名文件。
    cand3 = (mgr.tasks_dir / task_id / pp).resolve()
    if cand3.exists():
        return cand3
    # 都找不到：返回最近似的（让上层报错信息有真实路径）
    return cand1


def _ffmpeg_filter_path(p: Path) -> str:
    """REQ-20260919-061 Phase B：把 Path 转成 ffmpeg filter 安全的字符串。

    Windows 路径含 `:`（盘符）+ `\`（转义符），与 ffmpeg filter 选项语法冲突。
    解决：全部转 `/`，并把 `:` 转义为 `\:`。
    """
    s = str(p).replace("\\", "/")
    s = s.replace(":", r"\:")
    return s


def _ass_force_style(font: dict) -> str:
    """REQ-20260919-061 Phase B：把 font 设置转成 ASS force_style（libass 字幕滤镜用）。"""
    family_map = {
        "STHeitiMedium": "STHeiti Medium",
        "Noto Sans CJK SC": "Noto Sans CJK SC",
    }
    family = family_map.get(font["family"], font["family"])
    parts = [f"FontName={family}", f"FontSize={int(font['size'])}"]
    # REQ-20260919-062：字幕文字本身颜色 = libass PrimaryColour
    # #RRGGBB → &H00BBGGRR（ASS 用 BGR，alpha 在前 &H00 = 不透明）
    # None/非字符串（如 0/False）走默认 #FFFFFF；非 #RRGGBB 格式（如 #fff/#GGGGGG）静默忽略。
    tc_raw = font.get("color", "#FFFFFF")
    if isinstance(tc_raw, str):
        tc = tc_raw.lstrip("#")
        if len(tc) == 6 and all(c in "0123456789abcdefABCDEF" for c in tc):
            tc_bgr = tc[4:6] + tc[2:4] + tc[0:2]
            parts.append(f"PrimaryColour=&H00{tc_bgr.upper()}")
    if font.get("bold"):
        parts.append("Bold=1")
    align_map = {"left": 1, "center": 2, "right": 3}
    parts.append(f"Alignment={align_map.get(font['align'], 2)}")
    sw = int(font.get("stroke_width") or 0)
    if sw > 0:
        # ASS stroke_color #RRGGBB → &H00BBGGRR
        sc = font.get("stroke_color", "#000000").lstrip("#")
        if len(sc) == 6:
            sc = sc[4:6] + sc[2:4] + sc[0:2]
            parts.append(f"Outline={sw}")
            parts.append(f"OutlineColour=&H00{sc.upper()}")
    if font.get("bg_enabled"):
        bc = font.get("bg_color", "#000000").lstrip("#")
        if len(bc) == 6:
            bc = bc[4:6] + bc[2:4] + bc[0:2]
        op = float(font.get("bg_opacity", 0.6))
        alpha_hex = format(int((1.0 - op) * 255), "02X")
        parts.append("BorderStyle=4")  # 背景框
        parts.append(f"BackColour=&H{alpha_hex}{bc.upper()}")
    return ",".join(parts)


# REQ-20260920-079：bg/cover/reference 超大图自动缩放（防 ffmpeg OOM 卡死）
# 触发：源图长边 > _PRESCALE_THRESHOLD_PX 才缩；缩到 fit target_w×target_h 后 pad。
# 不动用户原图（写 .<label>_req079_<stem>_<W>x<H><.ext> 临时文件）。
_PIL_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp"})
_PRESCALE_THRESHOLD_PX = 4096  # 4K 横向分辨率；超过即视为「可能 OOM」


def _maybe_prescale_image(path: Path, target_w: int, target_h: int, label: str,
                          threshold: int = _PRESCALE_THRESHOLD_PX) -> Path:
    """如果图片长边 > threshold，按 LANCZOS 缩放至 fit target_w × target_h，
    黑边/透明 pad 到精确尺寸，写到源同目录的 .<label>_req079_<stem>_<W>x<H><.ext>。

    返回 ffmpeg 应该使用的路径：
    - 不需要缩放 → 返回原 path（无任何操作）
    - 缩放成功   → 返回临时文件 path
    - 缩放失败   → log.warning + 返回原 path（兜底不阻塞）

    RGBA 透明 PNG 保留 mode=RGBA + 透明 canvas，让 ffmpeg 继续按 alpha 合成
    （与 REQ-20260919-063 `_build_bg_layer_chain` 的黑底 overlay 链行为一致）。
    """
    if path.suffix.lower() not in _PIL_IMAGE_EXTS:
        return path
    try:
        from PIL import Image as _PILImage
        with _PILImage.open(path) as im:
            iw, ih = im.size
            if max(iw, ih) <= threshold:
                return path
            tmp = path.with_name(
                f".{label}_req079_{path.stem}_{target_w}x{target_h}{path.suffix}"
            )
            # RGBA/LA 保留 alpha；其他转 RGB
            mode = "RGBA" if im.mode in ("RGBA", "LA") else "RGB"
            im2 = im.convert(mode)
            # thumbnail 保比例 fit（max(target_w, target_h) 上限）
            im2.thumbnail((target_w, target_h), _PILImage.LANCZOS)
            bg = (0, 0, 0, 0) if mode == "RGBA" else (0, 0, 0)
            canvas = _PILImage.new(mode, (target_w, target_h), bg)
            x = (target_w - im2.size[0]) // 2
            y = (target_h - im2.size[1]) // 2
            canvas.paste(im2, (x, y))
            canvas.save(tmp, optimize=True)
            log.warning(
                "[REQ-079] %s: %dx%d → %dx%d (%s) → tmp=%s",
                label, iw, ih, target_w, target_h, path.name, tmp.name,
            )
            return tmp
    except Exception as e:
        log.warning("[REQ-079] %s pre-scale failed, using original: %s", label, e)
        return path


def _build_bg_layer_chain(bg_idx: int, W: int, H: int) -> list[str]:
    """REQ-20260919-063：bg 图透明区黑底 alpha 合成链。

    - bg_idx >= 0：bg 图是 RGBA 且透明像素 RGB=白色 → 必须用「黑底 + bg 图 overlay」
      才能让透明像素显示黑色（否则 ffmpeg 直接把透明像素当白色渲染 → 用户看到「白框」）。
      注：ffmpeg 的 format filter 不支持 'auto' 值；PNG 解码默认保留 alpha，
      overlay 会自动按 alpha 合成。
    - bg_idx < 0：纯黑底。
    返回：filter_complex chain 片段。
    """
    if bg_idx >= 0:
        return [
            f"color=size={W}x{H}:color=black:rate=30[bg_b]",
            f"[{bg_idx}:v]scale={W}:{H},setsar=1[bg_img]",
            "[bg_b][bg_img]overlay=eof_action=pass[bg]",
        ]
    return [f"color=size={W}x{H}:color=black:rate=30[bg]"]


def _build_fine_filter(fc: dict, W: int, H: int) -> tuple[list[str], list[str], str]:
    """构建 ffmpeg inputs + filter_complex（REQ-20260919-061 Phase B）。

    返回：(input_args, chain, final_label)
    - input_args: ["-i", path, ...]
    - chain: filter_complex 的分号串行
    - final_label: 最终视频流的标签（用于 -map）
    """
    layout = fc["layout"]
    materials = fc["materials"]
    font = fc["font"]

    inputs: list[str] = []
    chain: list[str] = []

    # Input 0: 视频（必填；render_fine_preview 先校验存在）
    # Input 1 (opt): 背景
    # Input 2 (opt): 封面
    bg_input_idx = -1
    cover_input_idx = -1

    if layout["bg"]["enabled"] and (materials.get("bg") or {}).get("path"):
        bg_input_idx = 1  # 假设视频=0, bg=1
    if layout["cover"]["enabled"] and (materials.get("cover") or {}).get("path"):
        cover_input_idx = 2 if bg_input_idx >= 0 else 1

    # 1. 背景层 — REQ-20260919-063：详见 _build_bg_layer_chain
    chain.extend(_build_bg_layer_chain(bg_input_idx, W, H))
    cur = "[bg]"

    # 2. 视频层：crop + scale + overlay
    vc = layout["video"]
    if vc["enabled"]:
        # ASS 表达式中 crop_w/h 是 0-1，归一化 iw/ih
        crop_expr = (
            f"crop=iw*{vc['crop_w']}:ih*{vc['crop_h']}:"
            f"iw*{vc['crop_x']}:ih*{vc['crop_y']}"
        )
        # 缩放后尺寸（向上取整防止 0）
        sw = max(1, int(round(W * vc["scale"])))
        sh = max(1, int(round(H * vc["scale"])))
        chain.append(
            f"[0:v]{crop_expr},scale={sw}:{sh}:flags=lanczos,setsar=1[v]"
        )
        vx = int(round(vc["x"] * W))
        vy = int(round(vc["y"] * H))
        chain.append(f"{cur}[v]overlay=x={vx}:y={vy}[v1]")
        cur = "[v1]"

    # 3. 封面层
    if cover_input_idx >= 0:
        cc = layout["cover"]
        # 封面基础尺寸：画布宽度的 30% × scale
        cw = max(1, int(round(W * 0.3 * cc["scale"])))
        ch = max(1, int(round(H * 0.3 * cc["scale"])))
        chain.append(f"[{cover_input_idx}:v]scale={cw}:{ch}[cv]")
        cx = int(round(cc["x"] * W))
        cy = int(round(cc["y"] * H))
        chain.append(f"{cur}[cv]overlay=x={cx}:y={cy}[v2]")
        cur = "[v2]"

    # 4. 字幕 burn-in（force_style 用 font 设置）
    sub_mat = materials.get("subtitle") or {}
    if layout["subtitle"]["enabled"] and sub_mat.get("path"):
        fs = _ass_force_style(font)
        # force_style 含逗号，filter graph 用逗号分隔参数，所以 force_style 内不能用逗号
        # 我们已经把逗号作为参数分隔，所以单字符串内不能含逗号；上面已用 , 作分隔
        # 但 force_style 子串里有逗号时会被解析错 — 用 \\, 转义不靠谱，改用半角 ;?
        # 实际 ASS 风格里我们没用逗号，用空格/; 都不行。规范做法：用 ',' 作分隔符
        # 时 force_style 内容不能含 ','
        # 简化：把 ',' 在 font family 里换掉（这里 family 已知不含逗号）；stroke_color/...
        # 转 16 进制无逗号
        # 所以最终 force_style 不含逗号，安全
        chain.append(
            f"{cur}subtitles='{sub_mat['path']}':force_style='{fs}':si=0[vout]"
        )
        cur = "[vout]"
    else:
        chain.append(f"{cur}copy[vout]")
        cur = "[vout]"

    return inputs, chain, cur


def _assemble_fine_filter(
    task_id: str,
    mgr,
    duration: float | None,
    preview_start: float = 0.0,
) -> dict:
    """REQ-20260919-074：从 fc 组装 ffmpeg 输入参数 + filter_complex（同步/异步共用）。

    返回 dict（成功）：
        {
          "ok": True,
          "input_args": list[str],          # 喂给 ffmpeg 的 -ss/-t/-loop/-i 等
          "filter_complex": str,            # -filter_complex 完整字符串
          "final_map": str,                 # 末位输出标签（[vfinal] / [vout] / [vsub]）
          "sub_input_tmp": Path | None,     # 字幕偏移临时文件（调用方负责 finally 清理）
          "out_w": int, "out_h": int,        # 输出分辨率
        }
    返回 dict（失败）：
        {"ok": False, "error": "..."}
    """
    import tempfile as _tf
    from slirn_home.compose_service import parse_srt, format_srt

    fc = _get_fine_compose(mgr, task_id)
    layout = fc["layout"]
    materials = fc["materials"]
    output_cfg = fc["output"]

    # 1. 校验视频素材存在
    video_path = _resolve_mat_abs(mgr, task_id, materials, "video")
    if not video_path or not video_path.exists():
        return {"ok": False, "error": "缺少视频素材，请上传或自动获取粗剪视频"}

    # 2. 输出分辨率
    res = output_cfg.get("resolution", "1080p")
    if res == "720p":
        out_w, out_h = 1280, 720
    else:
        out_w, out_h = _FINE_DESIGN_W, _FINE_DESIGN_H
    W, H = _FINE_DESIGN_W, _FINE_DESIGN_H

    # 3. 收集 inputs
    input_args: list[str] = []
    input_args += ["-ss", str(max(0.0, float(preview_start)))]
    if duration is not None:
        input_args += ["-t", str(duration)]
    input_args += ["-i", str(video_path)]

    if layout["bg"]["enabled"] and (materials.get("bg") or {}).get("path"):
        bg_path = _resolve_mat_abs(mgr, task_id, materials, "bg")
        if bg_path and bg_path.exists():
            input_args += ["-loop", "1", "-i", str(bg_path)]
        else:
            layout = {**layout, "bg": {**layout["bg"], "enabled": False}}
            fc["layout"] = layout

    cover_input_enabled = (
        layout["cover"]["enabled"]
        and float(layout["cover"].get("duration", 0)) > 0
        and (materials.get("cover") or {}).get("path")
    )
    if cover_input_enabled:
        cover_path = _resolve_mat_abs(mgr, task_id, materials, "cover")
        if cover_path and cover_path.exists():
            cover_dur = float(layout["cover"].get("duration", 2.0))
            input_args += ["-loop", "1", "-framerate", "30", "-t", f"{cover_dur:.2f}", "-i", str(cover_path)]
        else:
            layout = {**layout, "cover": {**layout["cover"], "enabled": False}}
            fc["layout"] = layout
            cover_input_enabled = False

    audio_cfg = fc.get("audio") or _FINE_AUDIO_DEFAULTS
    audio_input_enabled = (
        audio_cfg.get("enabled")
        and (materials.get("audio") or {}).get("path")
    )
    if audio_input_enabled:
        audio_path = _resolve_mat_abs(mgr, task_id, materials, "audio")
        if audio_path and audio_path.exists():
            input_args += ["-i", str(audio_path)]
        else:
            audio_cfg = dict(audio_cfg)
            audio_cfg["enabled"] = False
            fc["audio"] = audio_cfg
            audio_input_enabled = False

    # REQ-20260920-079：超大图片预缩放（跟随 fc.output.resolution；不动原图）
    # 720p→1280×720；1080p→1920×1080；source→1920×1080（最长边 ≤ 1920 防 OOM）
    image_tmp_paths: list[Path] = []
    if res == "720p":
        _ps_target_w, _ps_target_h = 1280, 720
    else:
        _ps_target_w, _ps_target_h = 1920, 1080

    # 当前 input_args 里有几个 -i（用于判断 bg_idx / cover_idx）
    _ps_inputs_count = sum(1 for i_, _ in enumerate(input_args) if input_args[i_] == "-i")

    def _replace_input_path(old: Path, new: Path) -> None:
        """把 input_args 中 old 路径替换成 new（按 -i 之后的下个 token 匹配）。"""
        for i_, tok in enumerate(input_args):
            if tok == "-i" and i_ + 1 < len(input_args) and input_args[i_ + 1] == str(old):
                input_args[i_ + 1] = str(new)
                return

    if layout["bg"]["enabled"] and _ps_inputs_count >= 2:
        bg_abs = _resolve_mat_abs(mgr, task_id, materials, "bg")
        if bg_abs and bg_abs.exists():
            new_bg = _maybe_prescale_image(bg_abs, _ps_target_w, _ps_target_h, "bg")
            if new_bg != bg_abs:
                _replace_input_path(bg_abs, new_bg)
                image_tmp_paths.append(new_bg)
    if cover_input_enabled:
        cover_abs = _resolve_mat_abs(mgr, task_id, materials, "cover")
        if cover_abs and cover_abs.exists():
            new_cover = _maybe_prescale_image(cover_abs, _ps_target_w, _ps_target_h, "cover")
            if new_cover != cover_abs:
                _replace_input_path(cover_abs, new_cover)
                image_tmp_paths.append(new_cover)
    ref_mat_pre = materials.get("reference") or {}
    if ref_mat_pre.get("path"):
        ref_abs = _resolve_mat_abs(mgr, task_id, materials, "reference")
        if ref_abs and ref_abs.exists():
            new_ref = _maybe_prescale_image(ref_abs, _ps_target_w, _ps_target_h, "ref")
            if new_ref != ref_abs:
                _replace_input_path(ref_abs, new_ref)
                image_tmp_paths.append(new_ref)

    # 4. filter_complex
    chain: list[str] = []
    inputs_count = sum(1 for i, _ in enumerate(input_args) if input_args[i] == "-i")

    bg_idx = -1
    if layout["bg"]["enabled"]:
        bg_idx = 1 if inputs_count >= 2 else -1
    chain.extend(_build_bg_layer_chain(bg_idx, W, H))
    cur = "[bg]"

    vc = layout["video"]
    if vc["enabled"]:
        crop_expr = (
            f"crop=iw*{vc['crop_w']}/{_FINE_DESIGN_W}:ih*{vc['crop_h']}/{_FINE_DESIGN_H}:"
            f"iw*{vc['crop_x']}/{_FINE_DESIGN_W}:ih*{vc['crop_y']}/{_FINE_DESIGN_H}"
        )
        sw = max(1, int(round(W * vc["scale"])))
        sh = max(1, int(round(H * vc["scale"])))
        chain.append(
            f"[0:v]{crop_expr},scale={sw}:{sh}:flags=lanczos,setsar=1[v]"
        )
        vx = max(0, min(W, int(vc["x"])))
        vy = max(0, min(H, int(vc["y"])))
        chain.append(f"{cur}[v]overlay=x={vx}:y={vy}[v1]")
        cur = "[v1]"

    sub_mat = materials.get("subtitle") or {}
    preview_offset_ms = int(round(max(0.0, float(preview_start)) * 1000))
    sub_input_tmp: Path | None = None
    if layout["subtitle"]["enabled"] and sub_mat.get("path"):
        sub_path = _resolve_mat_abs(mgr, task_id, materials, "subtitle")
        if sub_path and sub_path.exists():
            fs = _ass_force_style(fc["font"])
            sub_filter_path = sub_path
            if preview_offset_ms > 0:
                try:
                    src_text = sub_path.read_text(encoding="utf-8-sig")
                    src_entries = parse_srt(src_text)
                    shifted: list[dict] = []
                    for ent in src_entries:
                        s = max(0, int(ent["start_ms"]) - preview_offset_ms)
                        e = max(0, int(ent["end_ms"]) - preview_offset_ms)
                        if e <= s:
                            continue
                        shifted.append({"id": len(shifted) + 1, "start_ms": s,
                                        "end_ms": e, "text": ent.get("text", "")})
                    if shifted:
                        _tfh = _tf.NamedTemporaryFile(
                            mode="w", suffix=".srt", encoding="utf-8",
                            delete=False, prefix="slirn_fine_srt_")
                        _tfh.write(format_srt(shifted))
                        _tfh.close()
                        sub_filter_path = Path(_tfh.name)
                        sub_input_tmp = sub_filter_path
                except Exception as e:  # noqa: BLE001
                    log.warning("preview_start 字幕偏移失败，回退原 SRT: %s", e)
                    sub_filter_path = sub_path
            sub_safe = _ffmpeg_filter_path(sub_filter_path)
            chain.append(
                f"{cur}subtitles='{sub_safe}':force_style='{fs}':si=0[vsub]"
            )
            cur = "[vsub]"
        else:
            chain.append(f"{cur}copy[vsub]")
            cur = "[vsub]"
    else:
        chain.append(f"{cur}copy[vsub]")
        cur = "[vsub]"

    cover_idx = -1
    if cover_input_enabled:
        if bg_idx >= 0:
            cover_idx = 2 if inputs_count >= 3 else -1
        else:
            cover_idx = 1 if inputs_count >= 2 else -1
    if cover_idx >= 0:
        chain.append(
            f"[{cover_idx}:v]scale={W}:{H}:force_original_aspect_ratio=decrease,"
            f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"setpts=PTS-STARTPTS,fps=30,setsar=1[intro]"
        )
        chain.append(f"[intro]{cur}concat=n=2:v=1:a=0[vout]")
        cur = "[vout]"

    if (out_w, out_h) != (W, H):
        chain.append(f"[vout]scale={out_w}:{out_h}:flags=lanczos,setsar=1[vfinal]")
        cur = "[vfinal]"

    audio_idx = inputs_count - 1 if audio_input_enabled else -1
    if cover_input_enabled:
        cover_delay_ms = int(round(float(layout["cover"].get("duration", 0)) * 1000))
        chain.append(
            f"[0:a]adelay={cover_delay_ms}|{cover_delay_ms}:all=1,volume=1.0[voice]"
        )
    else:
        chain.append("[0:a]volume=1.0[voice]")
    if audio_input_enabled and audio_idx >= 0:
        vol = float(audio_cfg.get("volume", 0.4))
        fade_in = float(audio_cfg.get("fade_in", 0.0))
        fade_out = float(audio_cfg.get("fade_out", 0.0))
        # REQ-20260920-080 修复：label [bgm] 必须紧接过滤器链尾部，不能 ",[bgm]"
        # （之前用 list + ",".join 会把 label 当成 filter name → No such filter: ''）
        bgm_chain = f"[{audio_idx}:a]aloop=loop=-1:size=2e9,volume={vol:.2f}"
        if fade_in > 0:
            bgm_chain += f",afade=t=in:st=0:d={fade_in:.2f}"
        if fade_out > 0:
            bgm_chain += f",afade=t=out:st=0:d={fade_out:.2f}"
        bgm_chain += "[bgm]"
        chain.append(bgm_chain)
        chain.append(
            "[voice][bgm]amix=inputs=2:duration=first:normalize=0[aout]"
        )
    else:
        chain.append("[voice]anull[aout]")

    filter_complex = ";\n".join(chain)
    return {
        "ok": True,
        "input_args": input_args,
        "filter_complex": filter_complex,
        "final_map": cur,
        "sub_input_tmp": sub_input_tmp,
        "image_tmp_paths": image_tmp_paths,  # REQ-20260920-079：预缩临时文件
        "out_w": out_w,
        "out_h": out_h,
    }


def _run_fine_render(
    task_id: str,
    mgr,
    output_path: Path,
    duration: float | None,
    preview_start: float = 0.0,
) -> dict:
    """REQ-20260919-061 Phase B：调 ffmpeg 渲染精剪视频（同步版本，预览/短任务用）。

    REQ-20260919-074：filter_complex 组装抽到 `_assemble_fine_filter`，本函数
    只负责 ffmpeg subprocess.run + 错误处理。timeout=120（预览 ≤30 秒足够）。
    1-3 小时的导出任务请走 `export_fine_video` → 后台线程 + 进度轮询。
    """
    import subprocess

    asm = _assemble_fine_filter(task_id, mgr, duration, preview_start)
    if not asm.get("ok"):
        return asm
    sub_input_tmp = asm["sub_input_tmp"]
    image_tmp_paths = asm.get("image_tmp_paths") or []  # REQ-20260920-079

    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        *asm["input_args"],
        "-filter_complex", asm["filter_complex"],
        "-map", asm["final_map"],
        "-map", "[aout]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest",
        "-movflags", "+faststart",
        str(output_path),
    ]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120, encoding="utf-8"
        )
    except FileNotFoundError:
        return {"ok": False, "error": "系统未安装 ffmpeg，请先安装并加入 PATH"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "ffmpeg 渲染超时（>120s），请缩短视频或简化滤镜"}
    finally:
        if sub_input_tmp is not None:
            try:
                sub_input_tmp.unlink(missing_ok=True)
            except Exception:
                pass
        # REQ-20260920-079：同步渲染也清理预缩临时文件
        for _p in image_tmp_paths:
            try:
                _p.unlink(missing_ok=True)
            except Exception:
                pass
    if result.returncode != 0:
        log.error("ffmpeg failed: %s", result.stderr[-2000:])
        return {"ok": False, "error": f"ffmpeg 渲染失败: {(result.stderr or '')[-300:]}"}

    return {"ok": True, "path": str(output_path.relative_to(mgr.tasks_dir.parent))
            if output_path.is_absolute() else str(output_path)}


# =====================================================================
# REQ-20260919-074：精剪·导出最终视频 → 异步后台任务 + 进度展示
# =====================================================================

@dataclass
class _RenderJob:
    """单次精剪渲染任务的状态容器（in-process 内存表，不持久化）。

    字段：
        job_id: 全局唯一 ID（job_<ts_ms>_<pid>）
        task_id: 来源任务 ID
        state: queued / running / done / failed / cancelled
        started_at / finished_at: monotonic 时间戳（用于算 elapsed）
        wall_started_at / wall_finished_at: time.time() 时间戳（用于显示）
        elapsed_sec: 已用秒数（每 0.5 秒刷新）
        progress_pct: 0-100（来自 ffmpeg out_time_ms / ffprobe total）
        progress_time_ms: 当前已编码毫秒
        total_duration_ms: 源视频总毫秒
        speed_x: ffmpeg speed=2.5x
        eta_sec: 预计剩余秒数（-1 表示未知）
        error: 失败时 stderr 末尾 500 字符
        output_url: 成功时的下载链接
        proc: subprocess.Popen，用于 cancel
    """
    job_id: str
    task_id: str
    state: str = "queued"
    started_at: float = 0.0
    finished_at: float = 0.0
    wall_started_at: float = 0.0
    wall_finished_at: float = 0.0
    elapsed_sec: float = 0.0
    progress_pct: float = 0.0
    progress_time_ms: int = 0
    total_duration_ms: int = 0
    speed_x: float = 0.0
    eta_sec: float = -1.0
    error: str = ""
    output_url: str = ""
    proc: Any = None


_JOB_REGISTRY: dict[str, _RenderJob] = {}
_JOB_LOCK = threading.Lock()
_JOB_TTL_SEC = 300  # 完成后保留 5 分钟，便于前端最后一次查询拿到结果


def _cleanup_stale_jobs(mgr) -> int:
    """清理已完成且超过 TTL 的 job。返回清理数量。线程安全。

    REQ-20260920-084：清理内存时同步删 .export_job.json。
    """
    now = time.time()
    removed = 0
    with _JOB_LOCK:
        for jid in list(_JOB_REGISTRY.keys()):
            j = _JOB_REGISTRY[jid]
            if j.finished_at and (now - j.wall_finished_at) > _JOB_TTL_SEC:
                task_id_to_clean = j.task_id
                del _JOB_REGISTRY[jid]
                removed += 1
                # REQ-20260920-084：内存清理时同步删 .export_job.json
                try:
                    _delete_active_export_job(mgr, task_id_to_clean)
                except Exception:
                    pass
    return removed


# REQ-20260920-084：task 级 active export job 落盘文件（页面刷新后回显用）
def _active_export_job_path(mgr, tid: str) -> Path:
    """REQ-20260920-084：task 级 export job 状态文件路径。"""
    return mgr.tasks_dir / tid / "outputs" / ".export_job.json"


def _write_active_export_job(mgr, tid: str, job_id: str, state: str) -> None:
    """REQ-20260920-084：写 job_id 到 task 级文件，供页面刷新后回查。"""
    p = _active_export_job_path(mgr, tid)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        import json as _json
        p.write_text(
            _json.dumps(
                {"job_id": job_id, "state": state, "started_at": time.time()},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except Exception as e:  # noqa: BLE001
        log.warning("[REQ-084] 写 .export_job.json 失败: %s", e)


def _read_active_export_job(mgr, tid: str) -> dict | None:
    """REQ-20260920-084：读 task 级 .export_job.json；解析失败返回 None。"""
    p = _active_export_job_path(mgr, tid)
    if not p.exists():
        return None
    try:
        import json as _json
        d = _json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def _delete_active_export_job(mgr, tid: str) -> None:
    """REQ-20260920-084：删 task 级文件（job 终态 5 分钟后 / 用户取消 / 失败时）。"""
    p = _active_export_job_path(mgr, tid)
    try:
        p.unlink(missing_ok=True)
    except Exception:
        pass


def _kill_proc_with_grace(proc, grace_sec: float = 5.0) -> None:
    """SIGTERM → 等 grace_sec → SIGKILL 兜底。"""
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
    except Exception:
        return
    try:
        proc.wait(timeout=grace_sec)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
            proc.wait(timeout=2.0)
        except Exception:
            pass


def _probe_video_duration_ms(mgr, tid: str) -> int:
    """ffprobe 拿源视频时长（毫秒）。失败返回 0。"""
    try:
        fc = _get_fine_compose(mgr, tid)
        vp = _resolve_mat_abs(mgr, tid, fc["materials"], "video")
        if not vp or not Path(vp).exists():
            return 0
        pr = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(vp)],
            capture_output=True, text=True, timeout=10, encoding="utf-8",
        )
        sec = float((pr.stdout or "0").strip() or "0")
        return int(sec * 1000)
    except Exception:
        return 0


def _run_fine_render_async(job: _RenderJob, tid: str, mgr, out_path: Path,
                          exec_id: str = "", outputs_dir: Path | None = None) -> None:
    """REQ-20260919-074：后台 daemon 线程跑 ffmpeg（1-3 小时不再超时）。

    写入 job.state/progress_pct/elapsed_sec/speed_x/eta_sec/progress_time_ms/
    total_duration_ms/error/output_url 等字段；前端 GET /render_status 读取。

    REQ-20260920-081：exec_id 与 outputs_dir 由 export_fine_video endpoint 传入，
    用于在多出口（assemble 失败 / FileNotFound / cancelled / returncode 非 0 /
    returncode 0）都补 record_finish。失败也写（status="failed"）。
    """
    job.state = "running"
    job.started_at = time.monotonic()
    job.wall_started_at = time.time()

    asm = _assemble_fine_filter(tid, mgr, duration=None, preview_start=0.0)
    if not asm.get("ok"):
        job.state = "failed"
        job.error = asm.get("error", "filter 组装失败")
        job.finished_at = time.monotonic()
        job.wall_finished_at = time.time()
        # REQ-20260920-081：assemble 失败也写历史
        if exec_id and outputs_dir is not None:
            try:
                execution_history.record_finish(
                    outputs_dir, exec_id, success=False,
                    error=str(asm.get("error") or "filter 组装失败")[:500],
                )
            except Exception:
                pass
        # REQ-20260920-084：assemble 失败删 .export_job.json
        try: _delete_active_export_job(mgr, tid)
        except Exception: pass
        return

    sub_input_tmp = asm["sub_input_tmp"]
    image_tmp_paths = asm.get("image_tmp_paths") or []  # REQ-20260920-079

    # 拿总时长（百分比 + ETA 计算依赖）
    job.total_duration_ms = _probe_video_duration_ms(mgr, tid)

    # ffmpeg 命令（关键差异：-progress pipe:1 -nostats）
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        *asm["input_args"],
        "-filter_complex", asm["filter_complex"],
        "-map", asm["final_map"],
        "-map", "[aout]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest",
        "-movflags", "+faststart",
        "-progress", "pipe:1",
        "-nostats",
        str(out_path),
    ]

    # 启动 ffmpeg
    # REQ-20260920-077：解 Windows TextIOWrapper 8KB 缓冲卡死。
    # 原写法 `text=True, bufsize=1` 在 Windows 上无效 — `text=True` 会用
    # `io.TextIOWrapper` 包装 stdout，默认 8KB 缓冲；ffmpeg 每 ~0.4s 写一行
    # `out_time_ms=...`（~20 字节），要攒够 8KB 才喂给 `readline()`，进度条
    # 看似卡住 5-15 秒。改用 `text=False, bufsize=0`（unbuffered 给底层
    # BufferedReader），再手动重包为 `TextIOWrapper(line_buffering=True)`
    # 保证每行立即 flush。
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            bufsize=0,
        )
    except FileNotFoundError:
        job.state = "failed"
        job.error = "系统未安装 ffmpeg，请先安装并加入 PATH"
        job.finished_at = time.monotonic()
        job.wall_finished_at = time.time()
        if sub_input_tmp:
            try: sub_input_tmp.unlink(missing_ok=True)
            except Exception: pass
        # REQ-20260920-079：ffmpeg 启动失败也要清理预缩临时文件
        for _p in image_tmp_paths:
            try: _p.unlink(missing_ok=True)
            except Exception: pass
        # REQ-20260920-081：ffmpeg 不存在也写历史
        if exec_id and outputs_dir is not None:
            try:
                execution_history.record_finish(
                    outputs_dir, exec_id, success=False,
                    error="系统未安装 ffmpeg，请先安装并加入 PATH",
                )
            except Exception:
                pass
        # REQ-20260920-084：ffmpeg 不存在删 .export_job.json
        try: _delete_active_export_job(mgr, tid)
        except Exception: pass
        return

    job.proc = proc

    # REQ-20260920-077：重包 stdout/stderr 为 line-buffered TextIOWrapper。
    # 原 `text=True` 在 Windows 上的 8KB 默认缓冲会让 `out_time_ms` 行被积压，
    # 导致进度看似卡死。改为手动重包并显式 `line_buffering=True`，每行立即 flush。
    import io as _io
    proc.stdout = _io.TextIOWrapper(
        proc.stdout, encoding="utf-8", newline="\n",
        line_buffering=True,
    )
    if proc.stderr:
        proc.stderr = _io.TextIOWrapper(
            proc.stderr, encoding="utf-8", newline="\n",
            line_buffering=True,
        )

    # 主循环：读 stdout（progress key=value），算 elapsed + ETA
    last_update = 0.0
    try:
        while True:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                # ffmpeg 已退出但 stdout 关闭
                continue
            line = line.strip()
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            if key == "out_time_ms":
                try:
                    job.progress_time_ms = int(val)
                    if job.total_duration_ms > 0:
                        job.progress_pct = min(
                            100.0,
                            job.progress_time_ms / job.total_duration_ms * 100,
                        )
                except ValueError:
                    pass
            elif key == "speed":
                try:
                    job.speed_x = float(val.rstrip("x"))
                except ValueError:
                    pass
            elif key == "out_time_us":
                # 兼容部分 ffmpeg 版本用 out_time_us
                try:
                    job.progress_time_ms = int(val) // 1000
                    if job.total_duration_ms > 0:
                        job.progress_pct = min(
                            100.0,
                            job.progress_time_ms / job.total_duration_ms * 100,
                        )
                except ValueError:
                    pass
            # 每 0.5 秒刷一次 elapsed/ETA（避免 dict 写太频繁）
            now = time.monotonic()
            if now - last_update > 0.5:
                last_update = now
                job.elapsed_sec = now - job.started_at
                if job.speed_x > 0 and job.total_duration_ms > 0:
                    remaining_ms = max(0, job.total_duration_ms - job.progress_time_ms)
                    # speed_x = 源时长 / 墙钟时长 → 剩余墙钟 = 剩余源时长 / speed
                    job.eta_sec = remaining_ms / 1000.0 / job.speed_x
    finally:
        # 兜底：用户中途取消时确保 proc 死掉
        if proc.poll() is None:
            _kill_proc_with_grace(proc)

    proc.wait()
    job.finished_at = time.monotonic()
    job.wall_finished_at = time.time()
    job.elapsed_sec = job.finished_at - job.started_at

    # 清理临时 SRT
    if sub_input_tmp:
        try: sub_input_tmp.unlink(missing_ok=True)
        except Exception: pass

    # REQ-20260920-079：清理预缩临时文件（bg/cover/reference）
    for _p in image_tmp_paths:
        try: _p.unlink(missing_ok=True)
        except Exception: pass

    if job.state == "cancelled":
        # REQ-20260920-081：用户取消也写历史（status 标为 failed，error 带 cancelled）
        if exec_id and outputs_dir is not None:
            try:
                execution_history.record_finish(
                    outputs_dir, exec_id, success=False,
                    error="用户取消渲染",
                )
            except Exception:
                pass
        # REQ-20260920-084：用户取消删 .export_job.json
        try: _delete_active_export_job(mgr, tid)
        except Exception: pass
        return  # 取消路径不判断 returncode
    if proc.returncode == 0:
        job.state = "done"
        job.progress_pct = 100.0
        job.output_url = (
            f"/slirn/api/video/{tid}?src=fine_export&t={int(time.time())}"
        )
        # REQ-20260920-081：成功落盘历史
        if exec_id and outputs_dir is not None:
            try:
                execution_history.patch_extra(
                    outputs_dir, exec_id,
                    {"output_path": str(out_path),
                     "duration_sec": round((job.finished_at - job.started_at), 1),
                     "resolution": (asm.get("output_resolution") or "1080p")},
                )
                execution_history.record_finish(
                    outputs_dir, exec_id, success=True, error="",
                )
            except Exception:
                pass
    else:
        job.state = "failed"
        try:
            stderr_tail = proc.stderr.read() if proc.stderr else ""
        except Exception:
            stderr_tail = ""
        job.error = (stderr_tail or "未知错误")[-500:]
        # REQ-20260920-081：渲染失败落盘历史
        if exec_id and outputs_dir is not None:
            try:
                execution_history.record_finish(
                    outputs_dir, exec_id, success=False, error=str(job.error)[:500],
                )
            except Exception:
                pass
        # REQ-20260920-084：渲染失败删 .export_job.json
        try: _delete_active_export_job(mgr, tid)
        except Exception: pass


def _get_fine_compose(mgr, task_id: str) -> dict:
    """返回 task 的 fine_compose 数据（独立 JSON 文件，不污染 Task dataclass）。

    存储位置：tasks/{tid}/fine_compose.json
    返回：含默认值的 dict（materials/layout/font/output）。

    自动迁移：磁盘数据若未带 `_schema: 2`（旧 0-1 归一化 x/y），读时把每个 layout
    的 x/y 乘以 (设计空间宽, 设计空间高) 转成像素值。_schema 字段在内存里维护，
    落盘时由 _save_fine_compose 写入。
    """
    fc_path = mgr.tasks_dir / task_id / "fine_compose.json"
    fc = {}
    if fc_path.exists():
        try:
            fc = json.loads(fc_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            fc = {}
    fc.setdefault("materials", {})
    fc.setdefault("layout", {})
    fc.setdefault("output", dict(_FINE_OUTPUT_DEFAULTS))
    fc.setdefault("font", dict(_FINE_FONT_DEFAULTS))
    # REQ-20260919-061 扩展：背景音乐默认设置（独立于 layout）
    fc.setdefault("audio", dict(_FINE_AUDIO_DEFAULTS))
    # REQ-20260919-065：检测区域正式参数（旧任务缺该字段也兼容）
    fc.setdefault("detected_region", None)
    # layout 默认值与已存值合并（保留用户已设置的）
    for k, defaults in _FINE_LAYOUT_DEFAULTS.items():
        fc["layout"].setdefault(k, dict(defaults))

    # REQ-20260919-061 扩展：cover 旧字段（x/y/scale）迁移 —
    # 旧版封面是「右侧角标小图」，新版是「片头全屏海报」（仅需 duration 字段）。
    # 旧字段保留（数据不丢），但补一个 duration 默认值。
    cover_layout = fc["layout"].get("cover") or {}
    if "duration" not in cover_layout:
        cover_layout["duration"] = 2.0
        fc["layout"]["cover"] = cover_layout

    # REQ-20260919-062 v18：旧任务没有 crop_aspect_lock 字段 → 补默认值 True
    # （与前端默认勾选一致；不引入隐性行为变更，老数据按原渲染）
    video_layout = fc["layout"].get("video") or {}
    if "crop_aspect_lock" not in video_layout:
        video_layout["crop_aspect_lock"] = True
    fc["layout"]["video"] = video_layout

    # REQ-20260919-062 v19：旧任务 fc.font 缺 color 字段 → 补 #FFFFFF
    # （与 libass 默认 PrimaryColour 一致；不改写用户已设的值）
    _f_color = fc.get("font") or {}
    if "color" not in _f_color:
        _f_color["color"] = "#FFFFFF"
        fc["font"] = _f_color

    # 数字字段类型规整：JSON 不区分 int/float/str，f-string 用 .2f 时必须是数字
    # 否则报 "Unknown format code 'f' for object of type 'str'"。
    # 先做类型规整（字符串 → 数字），再做迁移（旧 0-1 → 像素）。
    _f = fc["font"]
    for k in ("size", "stroke_width", "bg_opacity", "bg_radius"):
        v = _f.get(k)
        if isinstance(v, str):
            try:
                _f[k] = float(v)
            except (TypeError, ValueError):
                _f[k] = dict(_FINE_FONT_DEFAULTS)[k]
    # layout 数字字段规整：x/y/crop_* 字符串 → 数字（最终转 int 像素），scale/duration 保留 float
    for layout in fc["layout"].values():
        for axis in ("x", "y", "crop_x", "crop_y", "crop_w", "crop_h"):
            v = layout.get(axis)
            if isinstance(v, str):
                try:
                    layout[axis] = float(v)
                except (TypeError, ValueError):
                    layout[axis] = 0  # 字符串解析失败 → 安全默认
        for axis in ("scale", "duration"):
            v = layout.get(axis)
            if isinstance(v, str):
                try:
                    layout[axis] = float(v)
                except (TypeError, ValueError):
                    pass
    # REQ-20260919-061 扩展：audio 字段（volume/fade_in/fade_out）字符串 → 数字
    _a = fc.get("audio") or {}
    for ak in ("volume", "fade_in", "fade_out"):
        v = _a.get(ak)
        if isinstance(v, str):
            try:
                _a[ak] = float(v)
            except (TypeError, ValueError):
                _a[ak] = 0.0

    # 数据迁移：旧 _schema (None/1) → 2（x/y 与 crop_* 都由 0-1 转像素）
    # 此时所有相关字段已是数字（float 或 int），可直接判定。
    schema = int(fc.get("_schema") or 0)
    if schema < _FINE_LAYOUT_SCHEMA:
        for layout in fc["layout"].values():
            for axis, design_size in (
                ("x", _FINE_DESIGN_W), ("y", _FINE_DESIGN_H),
                ("crop_x", _FINE_DESIGN_W), ("crop_y", _FINE_DESIGN_H),
                ("crop_w", _FINE_DESIGN_W), ("crop_h", _FINE_DESIGN_H),
            ):
                v = layout.get(axis)
                if isinstance(v, (int, float)) and 0.0 <= v <= 1.0:
                    # 旧归一化坐标 → 像素（整数）
                    layout[axis] = int(round(v * design_size))
        fc["_schema"] = _FINE_LAYOUT_SCHEMA
        # 落盘（atomic write，迁移一次即可）
        try:
            _save_fine_compose(mgr, task_id, fc)
        except Exception:  # noqa: BLE001 — 迁移失败不应阻断功能
            log.warning("fine_compose 迁移落盘失败: %s", task_id)

    # 迁移后类型再规整：x/y/crop_* 强制 int（像素，纯 float 也转 int）
    for layout in fc["layout"].values():
        for axis in ("x", "y", "crop_x", "crop_y", "crop_w", "crop_h"):
            v = layout.get(axis)
            if isinstance(v, float):
                v = int(round(v))
            elif not isinstance(v, int):
                v = 0  # 兜底
            layout[axis] = v
    return fc


def _clamp_video_to_viewport(vc: dict) -> None:
    """REQ-20260919-062 v5 用户反馈：把视频展示区域限定在所检测区域之内。

    若 vc 含 viewport（设计空间像素 {x,y,width,height}），则把 x/y/scale
    夹紧到 viewport 内，使得 video 的显示矩形（x, y, x+crop_w*scale, y+crop_h*scale）
    完全落在 viewport 内。直接修改入参 dict。

    - viewport 缺失/None/非法 → 不做任何修改
    - crop_w/h 或 viewport.width/height ≤ 0 → 不做任何修改（避免除零/反向夹紧）
    - scale 上限 = min(viewport.w / crop_w, viewport.h / crop_h)，再和 _fine_scale_max
      取小，保证不会因为 viewport 很小就把视频压成 0
    """
    vp = vc.get("viewport") if isinstance(vc, dict) else None
    if not isinstance(vp, dict):
        return
    if not all(k in vp for k in ("x", "y", "width", "height")):
        return
    try:
        rx, ry = int(vp["x"]), int(vp["y"])
        rw, rh = int(vp["width"]), int(vp["height"])
        crop_w = int(vc.get("crop_w", _FINE_DESIGN_W))
        crop_h = int(vc.get("crop_h", _FINE_DESIGN_H))
        if crop_w <= 0 or crop_h <= 0 or rw <= 0 or rh <= 0:
            return
        scale = float(vc.get("scale", 1.0))
    except (TypeError, ValueError):
        return

    # scale 上限：display 完全放进 viewport；同时不超过滑块本身的 max=2.0
    max_scale = min(rw / crop_w, rh / crop_h, 2.0)
    if scale > max_scale:
        scale = max_scale
    vc["scale"] = scale

    # 夹紧 x/y：display 矩形 (x, y) → (x + crop_w*scale, y + crop_h*scale) 必须 ⊂ viewport
    disp_w = crop_w * scale
    disp_h = crop_h * scale
    max_x = rx + max(0, rw - disp_w)
    max_y = ry + max(0, rh - disp_h)
    try:
        cur_x = int(vc.get("x", rx))
        cur_y = int(vc.get("y", ry))
    except (TypeError, ValueError):
        cur_x, cur_y = rx, ry
    vc["x"] = max(rx, min(max_x, cur_x))
    vc["y"] = max(ry, min(max_y, cur_y))


def _fine_param(
    label: str,
    slider_id: str,
    data_key: str,
    value: float,
    min_v: float,
    max_v: float,
    step: float,
    value_format: str = "{:.2f}",
    data_attr: str = "data-key",
    raw_format: str | None = None,
) -> str:
    """REQ-20260919-061a v7 用户反馈：单个参数设置块 = label + slider + num。

    结构：
    ```
    ┌─ slirn-fine-param ────────────────────────────────┐
    │  [label]  [────── slider ──────]  [┌ num ┐▴▾]     │
    └──────────────────────────────────────────────────┘
    ```

    - range slider：拖动快速调（同步写 num 框）
    - number input：精调（直接输入数字，浏览器自带原生 stepper 满足 +1/-1）
    - `data-for=slider_id` 让 bindFineSteppers() 双向同步 slider ↔ num
    - v7：移除之前在输入框外面额外加的自定义 ▲▼ 按钮 — 浏览器原生 stepper 已提供
      同样功能，自定义按钮是重复的。

    `data_attr`：默认 `data-key`，给 audio 滑块传 `data-audio-key` 以保持它们走独立的
    `save_fine_audio` 端点而不是 `save_fine_layout`。
    `raw_format`：slider/number input 的 `value=` 字面值格式，默认跟随 `value_format`
    —— 这样 `0.4` 会渲染成 `"0.40"` 兼容旧测试断言。
    """
    if raw_format is None:
        raw_format = value_format
    raw = raw_format.format(value)
    return (
        f'<div class="slirn-fine-param">'
        f'<span class="slirn-fine-param-label">{label}</span>'
        f'<input type="range" class="slirn-fine-slider" id="{slider_id}" '
        f'{data_attr}="{data_key}" min="{min_v}" max="{max_v}" step="{step}" value="{raw}">'
        f'<input type="number" class="slirn-fine-num" id="{slider_id}_num" '
        f'aria-label="数值输入" min="{min_v}" max="{max_v}" step="{step}" value="{raw}" '
        f'data-for="{slider_id}">'
        f'</div>'
    )





def _save_bg_detect_cache(mgr, task_id: str, result: dict) -> None:
    """REQ-20260919-062 v8 用户反馈：检测后把结果存到 fine_compose.bg_detect_cache，
    下次打开精剪面板直接渲染缓存值，无需重新扫描。

    REQ-20260919-065 升级：双写到 bg_detect_cache（向后兼容） + detected_region（正式参数）。

    缓存字段（与 _result_payload 输出对齐 + 算法/时间戳）：
      - x, y, width, height, center_x, center_y, corners
      - pixel_count, image_native_w/h
      - algorithm, threshold, detected_color, color_tolerance
      - detected_at (ISO 时间戳)
    """
    try:
        fc = _get_fine_compose(mgr, task_id)
        cache = {
            "x": result.get("x"),
            "y": result.get("y"),
            "width": result.get("width"),
            "height": result.get("height"),
            "center_x": result.get("center_x"),
            "center_y": result.get("center_y"),
            "corners": result.get("corners"),
            "pixel_count": result.get("pixel_count"),
            "image_native_w": result.get("image_native_w"),
            "image_native_h": result.get("image_native_h"),
            "algorithm": result.get("algorithm"),
            "threshold": result.get("threshold"),
            "detected_at": datetime.now().isoformat(timespec="seconds"),
        }
        # 颜色字段：仅在算法返回时存（pixel 算法没颜色）
        if "detected_color" in result:
            cache["detected_color"] = list(result["detected_color"])
        if "color_tolerance" in result:
            cache["color_tolerance"] = result["color_tolerance"]
        fc["bg_detect_cache"] = cache
        # REQ-20260919-065：同一份内容也写到 detected_region（正式参数位置）
        fc["detected_region"] = cache
        _save_fine_compose(mgr, task_id, fc)
    except Exception:
        # 缓存失败不影响主流程（接口已返回成功结果）
        pass


def _save_fine_compose(mgr, task_id: str, fc: dict) -> None:
    """写回 fine_compose.json。"""
    fc_path = mgr.tasks_dir / task_id / "fine_compose.json"
    fc_path.parent.mkdir(parents=True, exist_ok=True)
    fc_path.write_text(
        json.dumps(fc, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _render_fine_cut_zone(task_id: str, t, mgr: TaskManager) -> str:
    """REQ-20260919-061：精剪视频 pane — 5 素材上传 + 位置/缩放 + 字体 5 项 + 输出。"""
    fc = _get_fine_compose(mgr, task_id)
    materials = fc.get("materials") or {}
    layout = fc["layout"]
    font = fc["font"]
    output = fc["output"]

    # 1. 5 个素材上传卡
    upload_cards = []
    for kind in _FINE_MATERIAL_KINDS:
        icon, label, accept = _FINE_MATERIAL_LABELS[kind]
        mat = materials.get(kind) or {}
        path = mat.get("path") or ""
        filename = Path(path).name if path else ""
        source = mat.get("source") or ("upload" if path else "")
        # REQ-20260919-061 用户补充：video/subtitle 可从上游 auto 获取
        upstream_name = _fine_upstream_label(task_id, kind, mgr) if kind in _FINE_AUTO_KINDS else ""
        # source=auto 但上游产物已不存在 → 降级回空态（清掉 path）
        if source == "auto" and not upstream_name:
            source = ""
            path = ""
            filename = ""
        has = " has-file" if path else ""
        # REQ-20260919-061 用户反馈：状态显示当前来源 + 文件名；上传/自动获取两个按钮都常驻可用，
        # 点哪个就用哪个（最后一次操作决定 source 字段），不再禁用对方按钮。
        if source == "auto":
            source_badge = '<span class="slirn-fine-source-badge auto">📥 自动获取</span>'
            status_text = f"✅ 已从上游获取：{_esc(upstream_name)}"
        elif source == "upload" and filename:
            source_badge = '<span class="slirn-fine-source-badge upload">📤 手动上传</span>'
            status_text = f"✅ {_esc(filename)}"
        else:
            source_badge = ""
            status_text = "未上传"
        # 自动获取按钮：仅当上游存在时显示，且只在未自动获取时高亮（避免反复点）
        auto_btn_html = ""
        if kind in _FINE_AUTO_KINDS and upstream_name:
            auto_btn_html = (
                f'<button class="slirn-btn slirn-btn-xs" data-action="fine-source-auto" '
                f'data-kind="{kind}" title="从上游阶段产物自动获取：{_esc(upstream_name)}">'
                f'📥 自动获取（{_esc(upstream_name)}）</button>'
            )
        # REQ-20260920-082：把「系统默认 BGM」下拉嵌进 audio 上传卡（从参数区迁移）。
        # 只在 audio 卡片里追加；其他 kind 不显示。
        # class 名复用 REQ-20260920-078 的 .slirn-fine-default-bgm-row，
        # 避免 router.js / CSS 改动。data-task-id 用于 router.js 的 change 委托拿 tid。
        default_bgm_html = ""
        if kind == "audio":
            default_bgm_html = (
                f'<div class="slirn-fine-default-bgm-row" data-task-id="{_esc(task_id)}">'
                f'<span class="slirn-fine-actions-label">📦 系统默认 BGM</span>'
                f'<select id="slirn-fine-default-bgm" class="slirn-fine-default-bgm-select">'
                f'<option value="">— 不选（清空选择）—</option>'
                f'</select>'
                f'</div>'
            )
        upload_cards.append(
            f'<div class="slirn-fine-upload-card{has}" data-kind="{kind}" data-source="{source or "none"}">'
            f'<div class="slirn-fine-upload-label">{icon} {label}{source_badge}</div>'
            f'<div class="slirn-fine-upload-hint">{accept}</div>'
            f'<input type="file" class="slirn-fine-file" id="slirn-fine-file-{kind}" '
            f'accept=".{",".join(accept.split("/"))}" data-kind="{kind}">'
            f'<button class="slirn-btn slirn-btn-xs" data-action="fine-upload" data-kind="{kind}">📤 上传文件</button>'
            # REQ-20260919-063 用户反馈：每个素材都要提供预览功能；预览窗口可缩放。
            # 已有素材（has=has-file）才显示 👁️ 按钮，缺文件时禁用。
            f'<button class="slirn-btn slirn-btn-xs" data-action="fine-mat-preview" data-kind="{kind}" '
            f'data-task-id="{_esc(task_id)}" '
            f'{"disabled" if not has else ""} '
            f'title="{_esc("请先上传或自动获取素材") if not has else _esc("打开预览窗口（可缩放）")}">'
            f'👁️ 预览</button>'
            f'{auto_btn_html}'
            f'<div class="slirn-fine-upload-status" data-status-kind="{kind}">{status_text}</div>'
            f'{default_bgm_html}'
            f'</div>'
        )
    upload_html = '<div class="slirn-fine-uploads">' + "".join(upload_cards) + '</div>'

    # 2. 3 个素材的位置/缩放控件（video/subtitle/bg）
    # REQ-20260919-061 扩展：cover 不再是角标小图 → 改为独立的「片头全屏」控制块，
    # 不在 layout 循环里（详见下面 cover_html）。
    # REQ-20260919-061a 用户反馈 v3：每个 block 单独渲染（不再合并成 .slirn-fine-layouts），
    # block 内部参数 = 单列堆叠；外层由 _render_fine_cut_zone 末尾组装为左右两列。
    position_blocks = {}
    for mat_key in ("video", "subtitle", "bg"):
        lc = layout[mat_key]
        label_icon = {"video": "🎬 视频", "subtitle": "📝 字幕", "cover": "🖼 封面", "bg": "🎨 背景"}[mat_key]
        # REQ-20260919-062 v13 用户反馈：画布 X/Y 允许负数（视频可半截出画布，做"露半边"效果）。
        # 允许范围 = [-画布宽, +画布宽] / [-画布高, +画布高]（-1920–1920 / -1080–1080）。
        # 渲染层（_render_fine_cut_zone）按绝对坐标直接定位素材；负值 = 素材左侧/上侧出画布。
        # v14 用户反馈：缩放 = crop_w / bg_w（视频原剪辑宽度 / 背景图片宽度）。
        # 默认 crop_w=1920 → scale=1.0；自定义 crop 后点按钮「🎯 按裁剪宽度」自动应用公式。
        scale_auto_btn = (
            f'<button class="slirn-btn slirn-btn-xs slirn-fine-scale-auto-btn" '
            f'data-action="fine-scale-auto" '
            f'title="把视频缩放自动设为 crop_w / 1920（即视频原裁剪宽度占背景图片宽度的百分比）">'
            f'🎯 按裁剪宽度</button>'
        ) if mat_key == "video" else ''
        params_html = (
            f'<div class="slirn-fine-params">'
            + _fine_param(
                f"X（-{_FINE_DESIGN_W}–{_FINE_DESIGN_W}）", f"slirn-fine-{mat_key}-x",
                f"{mat_key}.x", int(lc["x"]), -_FINE_DESIGN_W, _FINE_DESIGN_W, 1, "{:d}",
            )
            + _fine_param(
                f"Y（-{_FINE_DESIGN_H}–{_FINE_DESIGN_H}）", f"slirn-fine-{mat_key}-y",
                f"{mat_key}.y", int(lc["y"]), -_FINE_DESIGN_H, _FINE_DESIGN_H, 1, "{:d}",
            )
            + _fine_param(
                # REQ-20260919-062 v6 用户反馈：视频缩放精度 5% → 1%。
                # v16 用户反馈：视频缩放值要显示 4 位小数（与 v15 自动重算保留 4 位一致）。
                # v17 用户反馈：bug — 自动重算到 4 位后立刻被浏览器截到 2 位。
                #   根因：<input type="range" step="0.01"> 会把 value 吸附到 0.01 网格，
                #   导致 s.value = "0.6667" 变成 "0.67"。step 改成 0.0001 后浏览器保留 4 位精度。
                # 其它素材（subtitle/cover/bg）仍保持 5% step + 2 位小数显示。
                "缩放", f"slirn-fine-{mat_key}-scale",
                f"{mat_key}.scale", float(lc["scale"]), 0.1, 2.0,
                0.0001 if mat_key == "video" else 0.05,
                "{:.4f}" if mat_key == "video" else "{:.2f}",
            )
            + scale_auto_btn
            + '</div>'
        )
        # REQ-20260919-062 v7 用户反馈：给视频添加宽高信息。
        # 仅 video 块附一个只读"显示尺寸"行，由前端 JS 根据 crop_w/h + scale 实时计算。
        # v10 用户反馈：「视频播放时的宽度百分比为视频原裁剪的宽度除以背景图片整个区域的宽度」。
        # 背景图整个区域 = 设计空间 1920×1080；视频裁剪宽度 = crop_w；所以
        # 宽度百分比 = crop_w / 1920 × 100%。同时显示高同理（crop_h / 1080）。
        info_html = ""
        if mat_key == "video":
            try:
                _disp_w = int(round(int(lc["crop_w"]) * float(lc["scale"])))
                _disp_h = int(round(int(lc["crop_h"]) * float(lc["scale"])))
                _crop_w = int(lc["crop_w"])
                _crop_h = int(lc["crop_h"])
            except (TypeError, ValueError):
                _disp_w, _disp_h, _crop_w, _crop_h = 0, 0, 0, 0
            _scale_pct = round(float(lc["scale"]) * 100, 4)
            # 背景图整个区域 = 设计空间 1920×1080；百分比按此计算
            _bg_w, _bg_h = 1920, 1080
            _crop_w_pct = round(_crop_w / _bg_w * 100, 2) if _bg_w else 0
            _crop_h_pct = round(_crop_h / _bg_h * 100, 2) if _bg_h else 0
            info_html = (
                f'<div class="slirn-fine-video-info" id="slirn-fine-video-info">'
                f'📐 显示尺寸: '
                f'<strong id="slirn-fine-video-disp-w">{_disp_w}</strong> × '
                f'<strong id="slirn-fine-video-disp-h">{_disp_h}</strong> px'
                # REQ-20260919-062 v16：缩放百分比也显示 4 位小数（与滑块/auto-recompute 一致）。
                f'　|　🎞 缩放 <span id="slirn-fine-video-scale-pct">{_scale_pct:.4f}</span>%'
                f'　|　📊 宽高比 '
                f'<span id="slirn-fine-video-aspect">'
                f'{(_disp_h / _disp_w) if _disp_w > 0 else 0:.3f}'
                f'</span>'
                f'　|　📏 占背景图 '
                f'<span id="slirn-fine-video-crop-w-pct">{_crop_w_pct:.2f}</span>'
                f'×<span id="slirn-fine-video-crop-h-pct">{_crop_h_pct:.2f}</span>%'
                f'（裁剪 {_crop_w}×{_crop_h} / 背景 1920×1080）'
                f'</div>'
            )
        position_blocks[mat_key] = (
            f'<div class="slirn-fine-layout-block">'
            f'<div class="slirn-fine-layout-title">'
            f'<label><input type="checkbox" class="slirn-fine-enabled" data-key="{mat_key}" '
            f'{"checked" if lc["enabled"] else ""}> {label_icon}</label>'
            f'</div>'
            f'{params_html}'
            f'{info_html}'
            f'</div>'
        )

    # 2.5 视频源裁剪（REQ-20260919-061 用户补充：crop_* 也用像素，基于 1920×1080 设计空间，
    # 渲染时按源视频实际尺寸等比换算）
    vc = layout["video"]
    crop_params = []
    for ck, ck_max in (
        ("crop_x", _FINE_DESIGN_W), ("crop_y", _FINE_DESIGN_H),
        ("crop_w", _FINE_DESIGN_W), ("crop_h", _FINE_DESIGN_H),
    ):
        ck_label = {
            "crop_x": f"X 起点（0–{_FINE_DESIGN_W}）",
            "crop_y": f"Y 起点（0–{_FINE_DESIGN_H}）",
            "crop_w": f"宽度（0–{_FINE_DESIGN_W}）",
            "crop_h": f"高度（0–{_FINE_DESIGN_H}）",
        }[ck]
        crop_params.append(
            _fine_param(
                ck_label, f"slirn-fine-video-{ck}",
                f"video.{ck}", int(vc[ck]), 0, ck_max, 1, "{:d}",
            )
        )
    # 实时算 crop 矩形比例（设计空间下）
    if vc["crop_w"] > 0:
        aspect = vc["crop_h"] / vc["crop_w"]
    else:
        aspect = 0
    crop_html = (
        f'<div class="slirn-fine-crop-block">'
        f'<div class="slirn-fine-layout-title">🎥 视频源裁剪（从源画面里截一个矩形范围）</div>'
        f'<div class="slirn-fine-crop-presets">'
        f'<button class="slirn-btn slirn-btn-xs" data-action="fine-crop-preset" data-preset="full">📐 全幅（不裁剪）</button>'
        f'<button class="slirn-btn slirn-btn-xs" data-action="fine-crop-preset" data-preset="16x9">🎯 16:9 居中</button>'
        f'<button class="slirn-btn slirn-btn-xs" data-action="fine-crop-preset" data-preset="1x1">⬛ 1:1 居中</button>'
        f'</div>'
        f'<label class="slirn-fine-crop-link-toggle">'
        # REQ-20260919-062 v18：把勾选状态读自 layout.video.crop_aspect_lock，
        # 默认 True（与旧版 HTML 默认勾选一致）。
        f'<input type="checkbox" id="slirn-fine-crop-aspect-link" '
        f'data-key="video.crop_aspect_lock" '
        f'{"checked" if bool(vc.get("crop_aspect_lock", True)) else ""}> '
        f'🔗 锁定 16:9 比例（防变形；关闭后可任意调整）</label>'
        f'<div class="slirn-fine-crop-aspect">当前矩形比例 ≈ <span id="slirn-fine-crop-aspect-val">{aspect:.3f}</span>'
        f'（目标 16:9 = {16/9:.3f}，1:1 = 1.000）</div>'
        f'<div class="slirn-fine-params">'
        + "".join(crop_params) +
        f'</div>'
        f'</div>'
    )

    # 2.6 片头封面（REQ-20260919-061 扩展）：不再画角标小图，封面作为片头全屏海报展示 N 秒
    cover_layout = layout["cover"]
    cover_duration = float(cover_layout.get("duration", 2.0))
    cover_html = (
        f'<div class="slirn-fine-cover-block">'
        f'<div class="slirn-fine-layout-title">🎞 片头封面（全屏展示 N 秒后切视频）</div>'
        f'<label><input type="checkbox" class="slirn-fine-enabled" data-key="cover" '
        f'{"checked" if cover_layout["enabled"] else ""}> 启用片头封面</label>'
        f'<div class="slirn-fine-params">'
        f'{_fine_param("展示时长（0–10 秒）", "slirn-fine-cover-duration", "cover.duration", cover_duration, 0, 10, 0.5, "{:.1f}")}'
        f'</div>'
        f'<div class="slirn-form-hint">设为 0 秒 = 不显示片头；建议 1–3 秒。'
        f'封面图会自动按比例铺满 1920×1080 画布（黑边填充）。'
        f'<b>封面播放期间视频静音（不输出原声），封面结束后才开始播放</b>。</div>'
        f'</div>'
    )

    # 2.7 背景音乐控制（REQ-20260919-061 扩展）：与原声混合（amix），保留说话人语音
    audio_cfg = fc.get("audio") or _FINE_AUDIO_DEFAULTS
    audio_html = (
        f'<div class="slirn-fine-audio-block">'
        f'<div class="slirn-fine-layout-title">🎵 背景音乐（与原声混合播放，保留说话人语音）</div>'
        f'<label><input type="checkbox" class="slirn-fine-enabled" data-key="audio" '
        f'{"checked" if audio_cfg["enabled"] else ""}> 启用背景音乐</label>'
        f'<div class="slirn-fine-params">'
        f'{_fine_param("音量（0–1，0.4 = 不压人声）", "slirn-fine-audio-volume", "volume", float(audio_cfg["volume"]), 0, 1, 0.05, "{:.2f}", data_attr="data-audio-key")}'
        f'{_fine_param("淡入（0–5 秒）", "slirn-fine-audio-fade_in", "fade_in", float(audio_cfg["fade_in"]), 0, 5, 0.5, "{:.1f}", data_attr="data-audio-key")}'
        f'{_fine_param("淡出（0–5 秒）", "slirn-fine-audio-fade_out", "fade_out", float(audio_cfg["fade_out"]), 0, 5, 0.5, "{:.1f}", data_attr="data-audio-key")}'
        f'</div>'
        f'<div class="slirn-form-hint">上传 mp3/wav/m4a 文件 → 原说话人语音 + BGM 同时播放；'
        f'短 BGM 自动循环填充。'
        # REQ-20260920-082：系统默认 BGM 下拉已从参数区迁移到「🎵 背景音乐」素材上传卡内
        # （贴在 audio upload card status 行下方）。下方留 hint 引导用户去上传区选 BGM。
        f'或在上方「🎵 背景音乐」上传卡内点「📦 系统默认 BGM」选内置 lo-fi mp3。</div>'
        f'</div>'
    )

    # 3. AI 解析按钮
    has_reference = bool(materials.get("reference", {}).get("path"))
    # tooltip 显示「将调用的当前模型」，让用户清楚按钮背后是哪个模型
    from slirn_home import llm_config as _llm_cfg
    _cur_model_id = _llm_cfg.get_current(mgr.tasks_dir.parent) or "未选"
    ai_btn = (
        f'<button class="slirn-btn slirn-btn-primary" data-action="fine-ai-parse" '
        f'data-task-id="{_esc(task_id)}" '
        f'{"disabled" if not has_reference else ""} '
        f'title="{_esc("需先上传参考位置关系图") if not has_reference else _esc(f"调当前模型「{_cur_model_id}」自动解析参考图")}">'
        f'🤖 AI 智能布局{"" if has_reference else "（需参考图）"}</button>'
    )

    # 4. 字体 5 项
    font_family_options = (
        f'<option value="STHeitiMedium" {"selected" if font["family"] == "STHeitiMedium" else ""}>STHeitiMedium（系统）</option>'
        f'<option value="Noto Sans CJK SC" {"selected" if font["family"] == "Noto Sans CJK SC" else ""}>思源黑体 Noto Sans CJK SC</option>'
    )
    font_html = (
        f'<div class="slirn-fine-font-block">'
        f'<div class="slirn-fine-font-title">🔤 字幕字体设置</div>'
        f'<div class="slirn-fine-font-row">'
        f'<span class="slirn-fine-font-label">字体本身</span>'
        f'<select class="slirn-fine-font-sel" data-font-key="family">{font_family_options}</select>'
        f'</div>'
        f'<div class="slirn-fine-font-row">'
        f'<span class="slirn-fine-font-label">字体大小（px）</span>'
        f'<input type="number" class="slirn-fine-font-num" data-font-key="size" '
        f'min="12" max="96" value="{font["size"]}">'
        f'</div>'
        f'<div class="slirn-fine-font-row">'
        f'<span class="slirn-fine-font-label">粗体</span>'
        f'<label><input type="checkbox" class="slirn-fine-font-chk" data-font-key="bold" '
        f'{"checked" if font["bold"] else ""}> 启用</label>'
        f'</div>'
        f'<div class="slirn-fine-font-row">'
        f'<span class="slirn-fine-font-label">屏幕对齐</span>'
        f'<select class="slirn-fine-font-sel" data-font-key="align">'
        f'<option value="left" {"selected" if font["align"] == "left" else ""}>左对齐</option>'
        f'<option value="center" {"selected" if font["align"] == "center" else ""}>居中</option>'
        f'<option value="right" {"selected" if font["align"] == "right" else ""}>右对齐</option>'
        f'</select>'
        f'</div>'
        f'<div class="slirn-fine-font-row">'
        f'<span class="slirn-fine-font-label">描边宽度（px）</span>'
        f'<input type="number" class="slirn-fine-font-num" data-font-key="stroke_width" '
        f'min="0" max="10" value="{font["stroke_width"]}">'
        f'</div>'
        # REQ-20260919-062 v19：字幕文字本身颜色 picker（之前只有描边/背景，
        # 在白色 PPT 背景上默认白字=看不见 → 用户以为是「白框」）
        f'<div class="slirn-fine-font-row">'
        f'<span class="slirn-fine-font-label">文字颜色</span>'
        f'<input type="color" class="slirn-fine-font-color" data-font-key="color" '
        f'value="{font["color"]}">'
        f'</div>'
        f'<div class="slirn-fine-font-row">'
        f'<span class="slirn-fine-font-label">描边颜色</span>'
        f'<input type="color" class="slirn-fine-font-color" data-font-key="stroke_color" '
        f'value="{font["stroke_color"]}">'
        f'</div>'
        f'<div class="slirn-fine-font-row">'
        f'<span class="slirn-fine-font-label">背景框</span>'
        f'<label><input type="checkbox" class="slirn-fine-font-chk" data-font-key="bg_enabled" '
        f'{"checked" if font["bg_enabled"] else ""}> 启用</label>'
        f'</div>'
        f'<div class="slirn-fine-font-row">'
        f'<span class="slirn-fine-font-label">背景颜色</span>'
        f'<input type="color" class="slirn-fine-font-color" data-font-key="bg_color" '
        f'value="{font["bg_color"]}">'
        f'</div>'
        # REQ-20260919-061a v7：背景透明度 = label + slider + num（用浏览器原生 stepper，
        # 移除自定义 ▲▼ 按钮 — 与外层其他 num 框一致）。
        f'<div class="slirn-fine-params">'
        f'<div class="slirn-fine-param">'
        f'<span class="slirn-fine-param-label">背景透明度</span>'
        f'<input type="range" class="slirn-fine-font-slider" id="slirn-fine-font-bg_opacity" '
        f'data-font-key="bg_opacity" min="0" max="1" step="0.05" value="{font["bg_opacity"]}">'
        f'<input type="number" class="slirn-fine-num" id="slirn-fine-font-bg_opacity_num" '
        f'aria-label="数值输入" min="0" max="1" step="0.05" value="{font["bg_opacity"]}" '
        f'data-for="slirn-fine-font-bg_opacity">'
        f'</div>'
        f'</div>'
        f'</div>'
    )

    # 5. 输出 3 项
    output_html = (
        f'<div class="slirn-fine-output-block">'
        f'<div class="slirn-fine-font-title">📺 输出设置</div>'
        f'<div class="slirn-fine-font-row">'
        f'<span class="slirn-fine-font-label">分辨率</span>'
        f'<select class="slirn-fine-output-sel" data-output-key="resolution">'
        f'<option value="1080p" {"selected" if output["resolution"] == "1080p" else ""}>1080p（1920×1080）</option>'
        f'<option value="720p" {"selected" if output["resolution"] == "720p" else ""}>720p（1280×720）</option>'
        f'<option value="source" {"selected" if output["resolution"] == "source" else ""}>原始视频分辨率</option>'
        f'</select>'
        f'</div>'
        f'</div>'
    )

    # 6. 预览/导出按钮（Phase B：ffmpeg 渲染已就绪）
    #   启用条件：至少视频素材已就绪（auto-pick 上游产物或 手动上传）
    has_video = bool(materials.get("video", {}).get("path"))
    preview_btn_disabled = "" if has_video else "disabled"
    preview_btn_title = "渲染预览（ffmpeg overlay，时长 2–30 秒可调）" if has_video else "请先上传或自动获取视频素材"
    export_btn_disabled = "" if has_video else "disabled"
    export_btn_title = "导出完整视频到 outputs/fine_export.mp4" if has_video else "请先上传或自动获取视频素材"
    preview_btn = (
        f'<button class="slirn-btn" data-action="fine-preview" data-task-id="{_esc(task_id)}" '
        f'{preview_btn_disabled} title="{_esc(preview_btn_title)}">🎬 生成预览</button>'
    )
    # REQ-20260919-061a v6 用户反馈：删除预览时长旁的 ▲▼ 按钮 — 直接修改 number input 即可。
    # 仍保留 number input + min/max 限制 + Enter 提交语义。
    # REQ-20260919-064：新增「预览开始时间」input 在「预览时长」前 — 让用户能跳到视频
    # 不同时间点预览。start + duration 由后端钳到不超出源视频时长。
    # REQ-20260919-066 用户反馈：开始时间改为 时:分:秒 三段输入（比纯秒更直观）；
    # 前端在发送前转成总秒数发给后端。默认 00:00:00。
    preview_start = (
        f'<span class="slirn-fine-preview-start">'
        f'<span class="slirn-fine-actions-label">预览开始时间</span>'
        f'<input type="number" id="slirn-fine-preview-start-h" class="slirn-fine-num slirn-fine-preview-time" '
        f'aria-label="预览开始时间（小时）" min="0" step="1" value="0" maxlength="2">'
        f'<span class="slirn-fine-time-sep">:</span>'
        f'<input type="number" id="slirn-fine-preview-start-m" class="slirn-fine-num slirn-fine-preview-time" '
        f'aria-label="预览开始时间（分钟）" min="0" max="59" step="1" value="0" maxlength="2">'
        f'<span class="slirn-fine-time-sep">:</span>'
        f'<input type="number" id="slirn-fine-preview-start-s" class="slirn-fine-num slirn-fine-preview-time" '
        f'aria-label="预览开始时间（秒）" min="0" max="59" step="1" value="0" maxlength="2">'
        f'<span class="slirn-fine-actions-label">（时:分:秒）</span>'
        f'</span>'
    )
    preview_duration = (
        f'<span class="slirn-fine-preview-duration">'
        f'<span class="slirn-fine-actions-label">预览时长</span>'
        f'<input type="number" id="slirn-fine-preview-duration" class="slirn-fine-num" '
        f'aria-label="预览时长（秒，2-30）" min="2" max="30" step="1" value="10">'
        f'<span class="slirn-fine-actions-label">秒（2–30）</span>'
        f'</span>'
    )
    # REQ-20260920-077：导出按钮 + 右侧 inline 状态元素（替代 REQ-074 的进度模态框）。
    # 包成 cell：按钮 + 状态元素同行，flex 容器由 .slirn-fine-actions-bar 提供。
    export_btn = (
        f'<span class="slirn-fine-export-cell">'
        f'<button class="slirn-btn slirn-btn-primary" id="slirn-fine-export-btn" '
        f'data-action="fine-export" data-task-id="{_esc(task_id)}" '
        f'{export_btn_disabled} title="{_esc(export_btn_title)}">'
        f'💾 导出最终视频</button>'
        f'<span class="slirn-fine-export-status" id="slirn-fine-export-status" '
        f'data-state="idle" hidden></span>'
        f'</span>'
    )
    preview_box = (
        # REQ-20260919-062 v10 用户反馈：去掉页面内的预览框（设计空间画布），
        # 弹出窗口预览（.slirn-mat-preview-float）就够了；预览框占页面大块空间，
        # 不再需要。生成预览按钮仍保留，让用户能渲染后再用弹窗预览查看。
        '<div class="slirn-fine-preview-empty" id="slirn-fine-preview-empty" hidden>'
        '本页已去掉内嵌预览；点「🎬 生成预览」后用「👁️ 预览」弹窗查看效果。</div>'
    )

    # REQ-20260919-061 用户反馈：把「保存设置参数」+ 模板管理搬到顶部操作栏。
    # 设计：
    #   - 模板名输入框（空着也行，但保存时会弹窗要求填名）
    #   - 💾 保存设置参数：保存当前参数到 fine_compose；如有模板名 → 同步另存为全局模板
    #   - 📥 引用参数：弹出模态框列出所有已保存模板，点「应用」覆盖当前任务参数
    #   - 状态指示器：显示「未保存 / 保存中 / 上次保存 HH:MM:SS / 保存失败」
    #   - 自动保存（滑块拖动 300ms 防抖）仍然只写 fine_compose，不写模板（避免一堆「未命名」）
    save_status = '<span class="slirn-fine-save-status" id="slirn-fine-save-status" data-state="idle">未保存</span>'

    # 引用参数（本地）modal（默认 hidden）。列表内容由前端 fineImportShow() 动态填充。
    import_modal = (
        f'<div class="slirn-modal-overlay" id="slirn-fine-import-overlay" hidden>'
        f'<div class="slirn-modal-card slirn-fine-import-card">'
        f'<div class="slirn-modal-title">📥 引用参数模板（应用到当前任务）</div>'
        f'<div class="slirn-fine-import-list" id="slirn-fine-import-list">'
        f'<div class="slirn-fine-profile-empty">加载中…</div>'
        f'</div>'
        f'<div style="display:flex; gap:10px; justify-content:center; margin-top:12px;">'
        f'<button class="slirn-btn" data-action="fine-import-close">关闭</button>'
        f'</div>'
        f'<div class="slirn-form-hint" style="margin-top:10px;">'
        f'「应用」会覆盖当前任务的对应参数（位置/字体/输出/音频），弹窗确认后生效。'
        f'「导出」会把该模板的参数下载为 JSON 文件（不依赖任务，可在外部备份/分享）。'
        f'</div>'
        f'</div>'
        f'</div>'
    )

    # REQ-20260919-061a 用户反馈 v4：左右两列的前 2 个块固定为视频/视频源裁剪（左）、
    # 字幕/字幕字体设置（右）。剩余 4 块按主题续列：
    #   左列（视频主层相关）：视频位置 → 视频源裁剪 → 背景位置 → 背景音乐
    #   右列（叠加 + 收尾）：  字幕位置 → 字幕字体设置 → 片头封面 → 输出设置
    # 每个 block 内部参数仍是单列堆叠（_fine_param 3 列 mini-grid = label/slider/num）。
    left_col_blocks = (
        position_blocks["video"]
        + crop_html
        + position_blocks["bg"]
        + audio_html
    )
    right_col_blocks = (
        position_blocks["subtitle"]
        + font_html
        + cover_html
        + output_html
    )
    fine_cols_html = (
        f'<div class="slirn-fine-cols">'
        f'<div class="slirn-fine-col">{left_col_blocks}</div>'
        f'<div class="slirn-fine-col">{right_col_blocks}</div>'
        f'</div>'
    )

    # REQ-20260919-064：把预览/导出行 + 模板/保存行 拆成两行（每个一行 .slirn-fine-actions-bar）。
    # 模板名 input 用 flex:1 自动填充剩余宽度；不再需要 sep 分隔符。
    combined_actions_bar = (
        # 行 1：渲染操作（AI 解析 / 生成预览 / 预览开始 / 预览时长 / 导出最终）
        f'<div class="slirn-fine-actions-bar">'
        f'{ai_btn} {preview_btn} {preview_start} {preview_duration} {export_btn}'
        f'</div>'
        # 行 2：模板管理 + 参数文件导入导出（REQ-065 在引用参数后追加 📤 导出 / 📥 导入按钮）
        f'<div class="slirn-fine-actions-bar">'
        f'<span class="slirn-fine-actions-label">模板名</span>'
        f'<input type="text" class="slirn-fine-profile-name" id="slirn-fine-profile-name" '
        f'placeholder="（可选）填了名另存为模板" maxlength="30" autocomplete="off">'
        f'<button class="slirn-btn slirn-btn-primary" data-action="fine-save-all" '
        f'data-task-id="{_esc(task_id)}" title="保存当前参数；未填名会弹窗要求填">'
        f'💾 保存设置参数</button>'
        f'<button class="slirn-btn" data-action="fine-import-show" '
        f'data-task-id="{_esc(task_id)}" title="从本机已保存的全局参数模板中选择应用（不会发到外部）">'
        f'📥 引用参数（本地）</button>'
        f'<button class="slirn-btn" data-action="fine-export-params" '
        f'data-task-id="{_esc(task_id)}" '
        f'title="下载所有参数（布局/字体/输出/音频/检测区域）为 JSON 文件">'
        f'📤 导出参数</button>'
        f'<button class="slirn-btn" data-action="fine-import-params" '
        f'data-task-id="{_esc(task_id)}" '
        f'title="从 JSON 文件导入参数（覆盖当前参数；不动素材文件）">'
        f'📥 导入参数</button>'
        f'{save_status}'
        f'</div>'
    )

    # REQ-20260919-062：背景图区域检测面板（独立块）。
    # v8 用户反馈：
    #   - 「把背景图区域颜色剪下」→ 检测主色用大色块 + RGB + HEX 醒目标签
    #   - 「信息区挪到 AI 智能布局区域的上方」→ 整个检测块上移到 combined_actions_bar 之前
    #   - 「检测后存储检测信息」→ 命中检测后写 fc.bg_detect_cache；渲染时优先读缓存
    # 算法：
    #   - center_expand 从中心向 4 方向扩展，遇到颜色变化即停（默认；最稳）
    #   - pixel         像素扫描（白色 RGB≥threshold，已知是白色时用）
    #   - ai_color      LLM 识别左下角主色 → 像素扫描（颜色未知但需要彩色 bbox 时）
    #   - ai            LLM 直接给出 bbox（已废弃）
    has_bg = bool(materials.get("bg", {}).get("path"))
    # REQ-20260919-065：优先读 detected_region（新正式位置），fallback 到 bg_detect_cache（向后兼容）
    bg_cache = (fc.get("detected_region") or fc.get("bg_detect_cache") or {}) if isinstance(fc, dict) else {}
    _has_cache = bool(bg_cache)
    _hidden_attr = '' if _has_cache else ' hidden'
    # v12 用户反馈：「背景图主色」大色块已去掉；下方的几何信息区仍保留（角点+宽高+中心+像素数+原图尺寸）。
    # 缓存里的几何信息
    _cache_x = bg_cache.get("x")
    _cache_y = bg_cache.get("y")
    _cache_w = bg_cache.get("width")
    _cache_h = bg_cache.get("height")
    _cache_cx = bg_cache.get("center_x")
    _cache_cy = bg_cache.get("center_y")
    _cache_pixels = bg_cache.get("pixel_count")
    _cache_native_w = bg_cache.get("image_native_w")
    _cache_native_h = bg_cache.get("image_native_h")
    _cache_corners = bg_cache.get("corners") or {}
    _cache_algo = bg_cache.get("algorithm") or "—"
    _cache_at = bg_cache.get("detected_at") or ""
    def _cache_val(v):
        return "—" if v is None else str(v)
    def _cache_corner(name):
        c = _cache_corners.get(name) if isinstance(_cache_corners, dict) else None
        if not c or not isinstance(c, list) or len(c) != 2:
            return "—"
        return f"({c[0]}, {c[1]})"
    # REQ-20260919-072：背景图检测状态徽章（summary 右侧，折叠时一眼看到进度）
    if not has_bg:
        _bg_status_badge = '<span class="slirn-fine-section-status">⏳ 未上传背景图</span>'
    elif _has_cache and _cache_w and _cache_h:
        _bg_status_badge = (
            f'<span class="slirn-fine-section-status">✅ 已检测 '
            f'{_cache_val(_cache_w)} × {_cache_val(_cache_h)}</span>'
        )
    else:
        _bg_status_badge = '<span class="slirn-fine-section-status">⚠️ 未检测</span>'

    bg_detect_block = (
        f'<div class="slirn-fine-bg-detect-block">'
        # v12 用户反馈：「背景图主色」那个大色块看不出有什么用（结果区已含坐标/宽高，
        # 再展示 RGB/HEX 没意义；主色只是中间量，不是终态交付物）。已去掉主色显示区。
        f'<div class="slirn-fine-layout-title">🎨 背景图区域检测（4 角点 + 宽高）</div>'
        # 算法 + 阈值/色容差 + 触发按钮
        f'<div class="slirn-fine-bg-detect-controls">'
        f'<span class="slirn-fine-actions-label">算法</span>'
        f'<select class="slirn-fine-bg-detect-algo" id="slirn-fine-bg-detect-algo">'
        f'<option value="center_expand" selected>🎯 中心扩展（默认；颜色未知时推荐）</option>'
        f'<option value="pixel">🔍 像素扫描白色（已知是白色）</option>'
        f'<option value="ai_color">🤖 AI 识别主色（颜色未知，需彩色 bbox）</option>'
        f'<option value="ai">🤖 AI 直接给 bbox（已废弃）</option>'
        f'</select>'
        # 阈值/容差控件（仅 pixel 算法显示；ai_color 用 ±10 容差不可改，ai 无需）
        f'<span class="slirn-fine-bg-detect-threshold-row" id="slirn-fine-bg-detect-threshold-row">'
        f'<span class="slirn-fine-actions-label">阈值</span>'
        f'<select class="slirn-fine-bg-detect-threshold-sel" id="slirn-fine-bg-detect-threshold-sel">'
        f'<option value="255">纯白 (255)</option>'
        f'<option value="250" selected>近白 (250)</option>'
        f'<option value="240">宽松 (240)</option>'
        f'<option value="230">很宽松 (230)</option>'
        f'</select>'
        f'<input type="number" class="slirn-fine-num" id="slirn-fine-bg-detect-threshold-num" '
        f'min="200" max="255" step="1" value="250" '
        f'aria-label="手动输入阈值（200-255）">'
        f'</span>'
        f'<button class="slirn-btn slirn-btn-primary" data-action="fine-bg-detect" '
        f'data-task-id="{_esc(task_id)}" '
        f'{"disabled" if not has_bg else ""} '
        f'title="{_esc("请先上传背景图片") if not has_bg else _esc("运行区域检测")}">'
        f'🔍 检测区域</button>'
        # v8：清空缓存按钮（用「重新检测」也可，但显式清空能让用户区分「清缓存」与「再跑一次」）
        f'<button class="slirn-btn slirn-btn-xs" data-action="fine-bg-detect-clear" '
        f'data-task-id="{_esc(task_id)}" '
        f'{"disabled" if not _has_cache else ""} '
        f'title="清空检测缓存（之后可重新检测）">'
        f'🗑 清空缓存</button>'
        f'</div>'
        # 只读结果区
        f'<div class="slirn-fine-bg-detect-result" id="slirn-fine-bg-detect-result"{_hidden_attr}>'
        f'<div class="slirn-fine-bg-detect-grid">'
        f'<span class="slirn-fine-bg-detect-key">左上角 (X, Y)</span>'
        f'<span class="slirn-fine-bg-detect-val" id="slirn-fine-bg-detect-tl">{_cache_corner("topleft")}</span>'
        f'<span class="slirn-fine-bg-detect-key">右上角 (X, Y)</span>'
        f'<span class="slirn-fine-bg-detect-val" id="slirn-fine-bg-detect-tr">{_cache_corner("topright")}</span>'
        f'<span class="slirn-fine-bg-detect-key">左下角 (X, Y)</span>'
        f'<span class="slirn-fine-bg-detect-val" id="slirn-fine-bg-detect-bl">{_cache_corner("bottomleft")}</span>'
        f'<span class="slirn-fine-bg-detect-key">右下角 (X, Y)</span>'
        f'<span class="slirn-fine-bg-detect-val" id="slirn-fine-bg-detect-br">{_cache_corner("bottomright")}</span>'
        f'<span class="slirn-fine-bg-detect-key">宽 × 高 (px)</span>'
        f'<span class="slirn-fine-bg-detect-val" id="slirn-fine-bg-detect-wh">'
        f'{_cache_val(_cache_w)} × {_cache_val(_cache_h)}'
        f'</span>'
        f'<span class="slirn-fine-bg-detect-key">中心 (X, Y)</span>'
        f'<span class="slirn-fine-bg-detect-val" id="slirn-fine-bg-detect-center">'
        f'{_cache_val(_cache_cx)}, {_cache_val(_cache_cy)}'
        f'</span>'
        f'<span class="slirn-fine-bg-detect-key">白色像素数</span>'
        f'<span class="slirn-fine-bg-detect-val" id="slirn-fine-bg-detect-pixels">{_cache_val(_cache_pixels)}</span>'
        f'<span class="slirn-fine-bg-detect-key">原图分辨率</span>'
        f'<span class="slirn-fine-bg-detect-val" id="slirn-fine-bg-detect-native">'
        f'{_cache_val(_cache_native_w)} × {_cache_val(_cache_native_h)}'
        f'</span>'
        f'<span class="slirn-fine-bg-detect-key">检测算法</span>'
        f'<span class="slirn-fine-bg-detect-val" id="slirn-fine-bg-detect-algo-used">{_esc(_cache_algo)}</span>'
        f'<span class="slirn-fine-bg-detect-key">检测时间</span>'
        f'<span class="slirn-fine-bg-detect-val" id="slirn-fine-bg-detect-time">{_esc(_cache_at)}</span>'
        f'</div>'
        f'<div class="slirn-fine-bg-detect-actions">'
        f'<button class="slirn-btn" data-action="fine-bg-detect-apply" '
        f'data-task-id="{_esc(task_id)}" title="把白色区域应用到 video：X/Y=区域左上角，crop=从原视频(0,0)取区域宽高，scale=1.0，并把视频限定在该区域内">'
        f'✅ 填充到视频位置和裁剪</button>'
        f'</div>'
        f'</div>'
        f'<div class="slirn-form-hint">白色 = R/G/B 三通道均 ≥ 阈值；阈值越小（200）越宽松，越大（255）越严格。'
        f'检测到的坐标都是 1920×1080 设计空间像素，结果自动保存到任务里（避免反复扫描）。</div>'
        f'</div>'
    )

    # REQ-20260919-072 v1：上传区域状态徽章（折叠时一眼看到进度）
    _uploaded_count = sum(
        1 for k in ("video", "subtitle", "cover", "bg", "reference", "audio")
        if materials.get(k, {}).get("path")
    )
    if _uploaded_count == 0:
        _upload_status_badge = '<span class="slirn-fine-section-status">⏳ 0/6 已上传</span>'
    elif _uploaded_count == 6:
        _upload_status_badge = f'<span class="slirn-fine-section-status">✅ {_uploaded_count}/6 已上传</span>'
    else:
        _upload_status_badge = f'<span class="slirn-fine-section-status">⚠️ {_uploaded_count}/6 已上传</span>'

    return (
        f'<div class="slirn-wb-pane-card">'
        f'<div class="slirn-wb-pane-title">🎬 精剪视频 · 素材合成器</div>'
        # REQ-20260919-072 v1：说明挪到最顶端（标题之后立刻显示）
        f'<div class="slirn-form-hint">📐 位置坐标（X/Y）和缩放（归一化 0-1 / 0.1-2.0）</div>'
        # REQ-20260919-072 v1：素材上传区可折叠（默认折叠；不写 open 属性）
        f'<details class="slirn-fine-section" id="slirn-fine-uploads-details">'
        f'<summary class="slirn-fine-section-summary">📁 上传素材（6 项）{_upload_status_badge}</summary>'
        f'{upload_html}'
        f'</details>'
        # REQ-20260919-072 v1：背景图区域检测可折叠（默认折叠；不写 open 属性）
        f'<details class="slirn-fine-section" id="slirn-fine-bg-detect-details">'
        f'<summary class="slirn-fine-section-summary">🎨 背景图区域检测（4 角点 + 宽高）{_bg_status_badge}</summary>'
        f'{bg_detect_block}'
        f'</details>'
        f'{combined_actions_bar}'
        f'<div class="slirn-form-hint">💡 上传 5 个素材后调滑块调位置/缩放/字体；改动后 300ms 自动保存到当前任务。'
        f'点「💾 保存设置参数」会同时把当前参数另存为全局模板（未填模板名则弹窗要求填）。</div>'
        f'{preview_box}'
        f'{fine_cols_html}'
        f'{import_modal}'
        f'</div>'
    )


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
    # REQ-20260918-053：执行日志 — 非流水线阶段，单独追加在 rail 末尾（不计入
    # _WB_STAGES，避免 wbAutoNextMaybe 自动跳到此页）
    stage_items += (
        f'<div class="slirn-wb-stage slirn-wb-stage-logs pending" '
        f'data-action="wb-stage" data-pane="logs" '
        f'title="查看该任务所有阶段的执行历史（含耗时、错误信息）">'
        f'<span class="slirn-wb-stage-mark">📜</span>'
        f'<div class="slirn-wb-stage-body"><div class="slirn-wb-stage-title">📜 执行日志</div>'
        f'<div class="slirn-wb-stage-desc">查看所有阶段执行历史（生成/修订/切分/合成/优化）</div></div></div>'
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
        # REQ-20260919-061：精剪视频·四素材合成器（Phase A 骨架）
        "fine_cut": _render_fine_cut_zone(task_id, t, mgr),
        "logs": _render_exec_logs_pane(task_id),  # REQ-20260918-053
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
                <button class="slirn-btn slirn-btn-sm" data-action="refresh-wb" data-task-id="{_esc(task_id)}"
                        title="刷新工作台数据（也可按 F5 / Cmd+R，URL #wb= 自动回到本页）">🔄 刷新</button>
                <button class="slirn-btn slirn-btn-sm slirn-wb-stages-expand" data-action="wb-toggle-stages"
                        title="展开左侧阶段列表">🧭 展开阶段</button>
                <button class="slirn-btn slirn-btn-sm" data-action="edit-task" data-task-id="{_esc(task_id)}">✏️ 编辑任务</button>
                <button class="slirn-btn slirn-btn-sm" data-action="goto-tasks">📋 返回列表</button>
            </div>
        </div>
        {top_rows}
        {_render_exec_history_card(task_id, mgr)}
    </div>
    <!-- 流程配置面板 + 状态条挂载点（REQ-20260918-047，v2：去掉抽屉壳，工作台内常驻纵向面板）-->
    <div id="slirn-pipe-status" class="slirn-pipe-status" data-task-id="{_esc(task_id)}" hidden></div>
    <section id="slirn-pipe-panel" class="slirn-pipe-panel" data-task-id="{_esc(task_id)}"></section>
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

def _render_exec_logs_pane(task_id: str) -> str:
    """REQ-20260918-053 — 工作台「📜 执行日志」面板。

    不在服务端渲染记录（量大 + 需过滤），仅渲染过滤区 + 列表占位；
    由 router.js 在面板首次显示时拉 /slirn/api/list_logs（REQ-20260920-084 切换）。

    REQ-20260920-084：新增 模式 chip（手动/自动）+ 时间段 chip（今天/近 7 天/近 30 天/全部）。
    """
    from slirn_home import execution_history

    kind_chips = "".join(
        f'<button type="button" class="slirn-chip" data-log-kind="{_esc(k)}" '
        f'title="{_esc(v)}">{_esc(v)}</button>'
        for k, v in execution_history.KIND_LABELS.items()
    )
    return (
        f'<div class="slirn-wb-pane-card slirn-logs-pane" data-task-id="{_esc(task_id)}">'
        f'  <div class="slirn-wb-pane-title">📜 执行日志 <span class="slirn-logs-count" data-bind="logs-count">--</span></div>'
        f'  <div class="slirn-form-hint slirn-logs-hint">'
        f'    所有阶段的执行历史（含字幕生成/字幕修订/切分修剪/粗剪合成/优化字幕/精剪 AI/检测/预览/导出）。阶段多选；状态/模式/时间段单选；输入关键词搜错误信息。'
        f'  </div>'
        f'  <div class="slirn-logs-filter">'
        f'    <div class="slirn-logs-filter-row">'
        f'      <span class="slirn-logs-filter-label">阶段：</span>{kind_chips}'
        f'      <button type="button" class="slirn-chip slirn-chip-clear" data-action="logs-clear-kinds" title="清除阶段过滤">清除</button>'
        f'    </div>'
        f'    <div class="slirn-logs-filter-row">'
        f'      <span class="slirn-logs-filter-label">状态：</span>'
        f'      <button type="button" class="slirn-chip" data-log-status="success" title="仅看成功的">✅ 成功</button>'
        f'      <button type="button" class="slirn-chip" data-log-status="failed" title="仅看失败的">❌ 失败</button>'
        f'      <button type="button" class="slirn-chip" data-log-status="running" title="仅看运行中的">⏳ 运行中</button>'
        f'      <button type="button" class="slirn-chip slirn-chip-clear" data-action="logs-clear-statuses" title="清除状态过滤">清除</button>'
        f'    </div>'
        f'    <div class="slirn-logs-filter-row">'
        f'      <span class="slirn-logs-filter-label">模式：</span>'
        f'      <button type="button" class="slirn-chip" data-log-auto="any" title="不限（手动 + 自动）">全部</button>'
        f'      <button type="button" class="slirn-chip" data-log-auto="manual" title="仅看手动触发的">👆 手动</button>'
        f'      <button type="button" class="slirn-chip" data-log-auto="auto" title="仅看流程配置自动执行的">⚙ 自动</button>'
        f'    </div>'
        f'    <div class="slirn-logs-filter-row">'
        f'      <span class="slirn-logs-filter-label">时间：</span>'
        f'      <button type="button" class="slirn-chip" data-log-time="today" title="今天 00:00 至今">📅 今天</button>'
        f'      <button type="button" class="slirn-chip" data-log-time="7d" title="近 7 天">🗓 近 7 天</button>'
        f'      <button type="button" class="slirn-chip" data-log-time="30d" title="近 30 天">📆 近 30 天</button>'
        f'      <button type="button" class="slirn-chip" data-log-time="all" title="不限时间">∞ 全部</button>'
        f'    </div>'
        f'    <div class="slirn-logs-filter-row">'
        f'      <input type="text" class="slirn-input slirn-logs-keyword" id="slirn-logs-keyword" '
        f'             placeholder="🔍 搜错误信息或阶段（不区分大小写）" />'
        f'      <button type="button" class="slirn-btn slirn-btn-sm" data-action="logs-refresh" title="按当前过滤条件重新查询">🔄 刷新</button>'
        f'    </div>'
        f'  </div>'
        f'  <div class="slirn-logs-list" id="slirn-logs-list" data-task-id="{_esc(task_id)}">'
        f'    <div class="slirn-form-hint slirn-logs-empty">尚未查询。点击「🔄 刷新」或切换过滤条件自动加载。</div>'
        f'  </div>'
        f'</div>'
    )

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
# REQ-20260918-047：流程配置 + 自动执行 JS（与 router.js 同模式走静态文件）
PIPELINE_JS = '<script defer src="/slirn/static/pipeline.js"></script>'
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
    return theme_js + ROUTER_JS + PIPELINE_JS


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

    # from fastapi import Body  # 已在文件顶部 import
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

    # REQ-20260918-047：流程配置 + 自动执行 JS
    pipeline_js_path = Path(__file__).parent / "static" / "pipeline.js"

    @app.app.get("/slirn/static/pipeline.js")
    async def serve_pipeline_js():
        return FileResponse(
            pipeline_js_path,
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

    # ========== REQ-20260919-061：精剪视频·四素材合成器 API ==========

    @app.app.post("/slirn/api/save_fine_layout")
    async def save_fine_layout(body: dict = Body(default_factory=dict)):
        """保存精剪视频 4 素材的位置/缩放/启用（含 video 的 crop_* 字段）。

        REQ-20260919-061 用户反馈：原版对不存在的 task_id 抛 500（TaskNotFoundError），
        导致前端滑块拖动后看到保存失败但不知道为什么。统一走 try/except 返回友好错误。
        """
        tid = (body.get("task_id") or "").strip()
        layout = body.get("layout") or {}
        if not tid:
            return _err("缺少 task_id")
        try:
            t = mgr.get(tid)
        except Exception as e:  # noqa: BLE001 — TaskNotFoundError 等
            return _err(f"任务不存在: {tid}")
        if not t:
            return _err(f"任务不存在: {tid}")
        fc = _get_fine_compose(mgr, tid)
        # 字段白名单（含 crop_* + cover.duration + video.viewport + video.crop_aspect_lock）
        allowed_keys = (
            "x", "y", "scale", "enabled",
            "crop_x", "crop_y", "crop_w", "crop_h",
            "duration", "viewport", "crop_aspect_lock",
        )
        for mat_key, val in layout.items():
            if mat_key not in _FINE_LAYOUT_DEFAULTS:
                continue
            existing = fc["layout"].get(mat_key) or {}
            existing.update({k: v for k, v in val.items() if k in allowed_keys})
            # REQ-20260919-062 v18：crop_aspect_lock 强制 bool（前端可能传 true/"on"/1）
            if mat_key == "video" and "crop_aspect_lock" in existing:
                existing["crop_aspect_lock"] = bool(existing["crop_aspect_lock"])
            # REQ-20260919-062 v5：若 video 有 viewport，则把 x/y/scale 夹紧到 viewport 内
            if mat_key == "video":
                _clamp_video_to_viewport(existing)
            fc["layout"][mat_key] = existing
        _save_fine_compose(mgr, tid, fc)
        # 返回夹紧后的值，前端可据此同步滑块显示
        return _ok(
            toast="位置/缩放已保存",
            layout={k: dict(v) for k, v in fc["layout"].items()},
        )

    @app.app.post("/slirn/api/auto_pick_upstream_material")
    async def auto_pick_upstream_material(body: dict = Body(default_factory=dict)):
        """REQ-20260919-061：把 video/subtitle 的素材来源切到「自动获取上游产物」。

        - video  → outputs/rough_compose.mp4（粗剪合成阶段产物）
        - subtitle → optimize_subtitle.json → build_srt() → tmp/optimized_subs.srt

        上游产物不存在时返回错误，前端 toast 提示并保持当前来源。
        """
        tid = (body.get("task_id") or "").strip()
        kind = (body.get("kind") or "").strip()
        if not tid:
            return _err("缺少 task_id")
        if kind not in _FINE_AUTO_KINDS:
            return _err(f"该素材不支持自动获取: {kind}")
        t = mgr.get(tid)
        if not t:
            return _err(f"任务不存在: {tid}")
        upstream = _fine_upstream_path(tid, kind, mgr)
        if not upstream:
            kind_label = _FINE_MATERIAL_LABELS[kind][1]
            if kind == "video":
                return _err(f"上游无粗剪合成产物（rough_compose.mp4），请先完成「粗剪合成」阶段")
            return _err(f"上游无优化字幕产物，请先完成「优化字幕」阶段并确认保存")
        fc = _get_fine_compose(mgr, tid)
        fc["materials"][kind] = {
            "path": str(upstream.relative_to(mgr.tasks_dir)),
            "type": "video" if kind == "video" else "srt",
            "source": "auto",
        }
        _save_fine_compose(mgr, tid, fc)
        kind_label = _FINE_MATERIAL_LABELS[kind][1]
        return _ok(path=fc["materials"][kind]["path"], toast=f"已自动从上游获取 {kind_label}")

    @app.app.post("/slirn/api/clear_bg_detect_cache")
    async def clear_bg_detect_cache(body: dict = Body(default_factory=dict)):
        """REQ-20260919-062 v8：清空背景图区域检测的缓存（让用户能重新检测）。
        REQ-20260919-065 升级：同时清 detected_region（正式参数）。
        """
        tid = (body.get("task_id") or "").strip()
        if not tid:
            return _err("缺少 task_id")
        try:
            mgr.get(tid)
        except Exception as e:  # noqa: BLE001
            return _err(f"任务不存在: {e}")
        try:
            fc = _get_fine_compose(mgr, tid)
        except Exception as e:  # noqa: BLE001
            return _err(f"读取任务失败: {e}")
        if "bg_detect_cache" not in fc and "detected_region" not in fc:
            return _ok(toast="缓存已为空，无需清空")
        fc.pop("bg_detect_cache", None)
        fc.pop("detected_region", None)
        try:
            _save_fine_compose(mgr, tid, fc)
        except Exception as e:  # noqa: BLE001
            return _err(f"保存失败: {e}")
        return _ok(toast="🗑 已清空检测缓存（下次打开会重新渲染）")

    @app.app.post("/slirn/api/detect_bg_white_area")
    async def detect_bg_white_area(request: Request, body: dict = Body(default_factory=dict)):
        """REQ-20260919-062：检测背景图片的白色区域 4 角坐标 + 宽高。

        算法由前端 body.algorithm 选择：
          - "pixel": numpy + PIL 在原图分辨率扫描 RGB 三通道均 ≥ threshold 的像素，
            求连通白色区域的最小外接矩形（bbox）。坐标等比缩放到 1920×1080 设计空间。
          - "ai":    调多模态 LLM，让模型看图描述「白色矩形区域的左上角和右下角像素坐标」，
            再按原图分辨率折算回 1920×1080 设计空间。
        threshold: 像素模式用 RGB 三通道阈值（0-255，默认 250）；AI 模式忽略。
        返回：{x, y, width, height, center_x, center_y, pixel_count, image_native_w/h, algorithm, threshold}
        4 个角点：
          - 左上 (x, y)
          - 右上 (x + width - 1, y)
          - 左下 (x, y + height - 1)
          - 右下 (x + width - 1, y + height - 1)

        REQ-20260919-075：检测动作也写执行历史（之前没有）。
        """
        import numpy as _np
        from PIL import Image as _PILImage

        # REQ-20260919-075：auto 透传；检测区域写日志
        _auto = request.headers.get("X-Slirn-Auto") == "1"
        # REQ-20260920-081：session 透传
        _auto_session_id = request.headers.get("X-Slirn-Auto-Session") or ""
        from slirn_home import execution_history

        # REQ-20260919-075：失败路径也写历史（用包装函数替代裸 return _err）
        _bg_outputs_dir: Path | None = None
        _bg_exec_id: str = ""

        def _bg_finish_err(msg: str):
            """记录失败并返回 _err。"""
            try:
                if _bg_exec_id and _bg_outputs_dir:
                    execution_history.patch_fields(_bg_outputs_dir, _bg_exec_id, {
                        "description": f"检测失败：{msg[:80]}"
                    })
                    execution_history.record_finish(_bg_outputs_dir, _bg_exec_id,
                                                    success=False, error=msg)
            except Exception:  # noqa: BLE001
                pass
            return _err(msg)

        tid = (body.get("task_id") or "").strip()
        algorithm = (body.get("algorithm") or "pixel").strip()
        try:
            threshold = int(body.get("threshold") or 250)
        except (TypeError, ValueError):
            threshold = 250
        threshold = max(200, min(255, threshold))
        if not tid:
            return _err("缺少 task_id")
        if algorithm not in ("pixel", "ai", "ai_color", "center_expand"):
            return _err(f"不支持的算法: {algorithm}")
        try:
            mgr.get(tid)
        except Exception as e:  # noqa: BLE001
            return _err(f"任务不存在: {e}")

        outputs_dir = mgr.tasks_dir / tid / "outputs"
        _bg_outputs_dir = outputs_dir
        # REQ-20260919-075：开始记录 — 检测区域
        exec_id = execution_history.record_start(
            outputs_dir, execution_history.KIND_FINE_BG_DETECT,
            extra={"algorithm": algorithm, "threshold": threshold},
            auto=_auto,
            auto_session_id=_auto_session_id,
        )
        _bg_exec_id = exec_id

        fc = _get_fine_compose(mgr, tid)
        bg_mat = fc.get("materials", {}).get("bg") or {}
        bg_path_rel = bg_mat.get("path")
        if not bg_path_rel:
            return _bg_finish_err("请先上传背景图片")
        bg_path_abs = _resolve_mat_abs(mgr, tid, fc["materials"], "bg")
        if not bg_path_abs or not bg_path_abs.exists():
            return _bg_finish_err(f"背景图片文件不存在: {bg_path_rel}")

        # 加载图片（保留原图分辨率用于 image_native_w/h 反馈）
        try:
            with _PILImage.open(bg_path_abs) as _im:
                _im.load()
            img = _PILImage.open(bg_path_abs).convert("RGB")
        except Exception as e:  # noqa: BLE001
            return _bg_finish_err(f"背景图片读取失败: {e}")
        iw, ih = img.size
        if iw <= 0 or ih <= 0:
            return _bg_finish_err(f"背景图片尺寸异常: {iw}x{ih}")

        # 用户反馈：先缩放到设计空间（1920×1080）再做白色区域扫描，
        # 避免原图分辨率不同时坐标还要再折算，输出坐标即设计空间像素。
        # - pixel 算法：先 resize 再扫描，scan_img 已是 1920×1080，scale = 1
        # - ai 算法：AI 输出的坐标是原图坐标，仍需按比例折算 → scale_x/y
        #            （resize 会破坏原图细节，对 LLM 视觉判断不利，所以 AI 路径不 resize）
        scale_x = _FINE_DESIGN_W / iw
        scale_y = _FINE_DESIGN_H / ih

        def _result_payload(x_min: int, y_min: int, x_max: int, y_max: int,
                             pixel_count: int, sx: float = 1.0, sy: float = 1.0):
            """统一输出格式（设计空间像素 + 4 角点 + 原图尺寸）。

            sx/sy: 把 (x_min, y_min, x_max, y_max) 视为「当前工作图」坐标，乘以缩放系数
            折算到设计空间（1920×1080）。pixel 路径已 resize 到 1920×1080，sx=sy=1；
            AI 路径未 resize，sx=1920/iw, sy=1080/ih。
            """
            w_native = x_max - x_min + 1
            h_native = y_max - y_min + 1
            return {
                "x": int(round(x_min * sx)),
                "y": int(round(y_min * sy)),
                "width": int(round(w_native * sx)),
                "height": int(round(h_native * sy)),
                "center_x": int(round(((x_min + x_max) / 2) * sx)),
                "center_y": int(round(((y_min + y_max) / 2) * sy)),
                "pixel_count": int(pixel_count),
                "image_native_w": int(iw),
                "image_native_h": int(ih),
                "algorithm": algorithm,
                "threshold": int(threshold) if algorithm == "pixel" else None,
                "corners": {
                    "topleft":     [int(round(x_min * sx)), int(round(y_min * sy))],
                    "topright":    [int(round(x_max * sx)), int(round(y_min * sy))],
                    "bottomleft":  [int(round(x_min * sx)), int(round(y_max * sy))],
                    "bottomright": [int(round(x_max * sx)), int(round(y_max * sy))],
                },
            }

        # 缩放到设计空间（LANCZOS 高质量）— pixel / ai_color 共用
        scan_img = img.resize((_FINE_DESIGN_W, _FINE_DESIGN_H), _PILImage.LANCZOS)

        if algorithm == "pixel":
            # 在 scan_img（已是设计空间）上扫描白色像素
            arr = _np.array(scan_img)  # shape (1080, 1920, 3)
            mask = (
                (arr[:, :, 0] >= threshold)
                & (arr[:, :, 1] >= threshold)
                & (arr[:, :, 2] >= threshold)
            )
            ys, xs = _np.where(mask)
            n = int(xs.size)
            if n == 0:
                return _bg_finish_err(
                    f"未检测到任何白色像素（threshold={threshold}）；"
                    f"请调低阈值或换张图"
                )
            x_min, x_max = int(xs.min()), int(xs.max())
            y_min, y_max = int(ys.min()), int(ys.max())
            # scan_img 已是 1920×1080，sx=sy=1.0，输出即设计空间像素
            payload = _result_payload(x_min, y_min, x_max, y_max, n, 1.0, 1.0)
            _save_bg_detect_cache(mgr, tid, payload)  # v8：检测后存缓存，避免反复检测
            # REQ-20260919-075：检测成功时回填执行情况
            try:
                execution_history.patch_fields(outputs_dir, exec_id, {
                    "description": (f"检测算法 {algorithm} 完成：发现白色区域"
                                    f" ({payload['width']}×{payload['height']} 像素，"
                                    f" {n} 白色像素)")
                })
                execution_history.record_finish(outputs_dir, exec_id, success=True, error="")
            except Exception as _eh:  # noqa: BLE001
                log.warning("[bg_detect][%s] history 记录失败: %s", tid, _eh)
            return _ok(**payload)

        if algorithm == "center_expand":
                # 用户反馈 v3：以上方法检测结果都不对。换思路 ——
            #   1) 以背景图中心点周围 10×10 区域的平均色作为「基本颜色」
            #   2) 从中心向上/下/左/右 4 个方向扩展（每次扩展一行/列像素）
            #   3) 扩展信号是「矩线」（矩形边界线）：新行/列所有像素与基本颜色差值都在 ±10 内才继续
            #   4) 任一方向遇到颜色变化即停
            #   5) 4 条矩线围成的矩形 = 所求区域，输出左上角 + 宽高
            # 这个算法的优势：
            #   - 不需要先知道是什么颜色（自动从中心提取）
            #   - 对「背景图中央有一片同色区域」的典型布局最稳
            #   - 4 方向同时扩展，类似 flood fill 但沿坐标轴（更快、更可预测）
            arr_full = _np.array(scan_img).astype(int)  # (1080, 1920, 3) int
            ih_d, iw_d = arr_full.shape[:2]
            cx, cy = iw_d // 2, ih_d // 2
            # 中心 10×10 平均色（避开单像素噪声）
            patch_r = 5
            x0 = max(0, cx - patch_r); x1 = min(iw_d, cx + patch_r + 1)
            y0 = max(0, cy - patch_r); y1 = min(ih_d, cy + patch_r + 1)
            patch = arr_full[y0:y1, x0:x1, :]
            base_rgb = patch.reshape(-1, 3).mean(axis=0)  # (3,) float
            base_r, base_g, base_b = float(base_rgb[0]), float(base_rgb[1]), float(base_rgb[2])
            color_tol = 10
    
            def _row_match(y: int, x_start: int, x_end: int) -> bool:
                """行 [x_start..x_end] 所有像素与基本色差值是否都在 ±tol 内（用于水平扫描）。"""
                row = arr_full[y, x_start:x_end + 1, :]
                d = _np.abs(row - base_rgb)
                return bool((d <= color_tol).all())
    
            def _col_match(x: int, y_start: int, y_end: int) -> bool:
                """列 [y_start..y_end] 所有像素与基本色差值是否都在 ±tol 内（用于垂直扫描）。"""
                col = arr_full[y_start:y_end + 1, x, :]
                d = _np.abs(col - base_rgb)
                return bool((d <= color_tol).all())
    
            # 左扩展：x_left 一直左移，直到 _row_match 失败
            x_left = cx
            while x_left > 0 and _row_match(cy, x_left - 1, x_left - 1):
                x_left -= 1
            # 右扩展
            x_right = cx
            while x_right < iw_d - 1 and _row_match(cy, x_right + 1, x_right + 1):
                x_right += 1
            # 上扩展
            y_top = cy
            while y_top > 0 and _col_match(cx, y_top - 1, y_top - 1):
                y_top -= 1
            # 下扩展
            y_bottom = cy
            while y_bottom < ih_d - 1 and _col_match(cx, y_bottom + 1, y_bottom + 1):
                y_bottom += 1
    
            x_min, x_max = x_left, x_right
            y_min, y_max = y_top, y_bottom
            w = x_max - x_min + 1
            h = y_max - y_min + 1
            # 像素数估算（外接矩形面积；按真实分布应为同色面积，矩形面积作为上限）
            n_pixels = w * h
            # scan_img 已是 1920×1080，sx=sy=1.0
            payload = _result_payload(x_min, y_min, x_max, y_max, n_pixels, 1.0, 1.0)
            payload["algorithm"] = "center_expand"
            payload["threshold"] = None
            # 把基本颜色也带上，方便 UI 展示
            payload["detected_color"] = [int(round(base_r)), int(round(base_g)), int(round(base_b))]
            payload["color_tolerance"] = color_tol
            rgb_text = "RGB(" + str(int(round(base_r))) + "," + str(int(round(base_g))) + "," + str(int(round(base_b))) + ")"
            _save_bg_detect_cache(mgr, tid, payload)  # v8：检测后存缓存
            return _ok(
                **payload,
                toast=f"✅ 中心扩展找到区域 {w}x{h}（基本色 {rgb_text}）",
            )

        # algorithm == "ai_color"
        # 用户反馈：待检测区域的颜色无法确定时，先让 LLM 看背景图左下角识别主色，
        # 再用像素扫描找该颜色的 bbox。颜色容差 ±10（覆盖 LLM 略微偏差）。
        from slirn_home import llm_config, revision_service

        models = llm_config.list_models(repo_root)
        cur_id = llm_config.get_current(repo_root)
        cur_entry = next((m for m in models if m.get("id") == cur_id), None)
        if cur_entry is None or not revision_service._is_vision_entry(cur_entry):
            vision_alts = [m["id"] for m in models if revision_service._is_vision_entry(m)]
            hint = (
                f" — 已注册可用多模态模型：{', '.join(vision_alts)}，请在顶栏 ⚙️ 切换"
            ) if vision_alts else (
                " — 请在顶栏 ⚙️ 注册多模态模型（推荐 qwen-vl-plus）"
            )
            return _bg_finish_err(
                f"AI 颜色识别需多模态模型，当前模型不支持图片输入{hint}"
            )

        # 切左下角象限（设计空间尺寸的 1/4 = 960×540），保存到临时文件供 LLM 读取
        bl_quad = scan_img.crop((0, _FINE_DESIGN_H // 2, _FINE_DESIGN_W // 2, _FINE_DESIGN_H))
        # 用 bg_path_abs 同目录避免占用系统临时目录（清理方便）
        bl_tmp = bg_path_abs.parent / "_bg_bl_quad_tmp.png"
        try:
            bl_quad.save(bl_tmp)
        except Exception as e:  # noqa: BLE001
            return _bg_finish_err(f"左下角象限保存失败: {e}")

        system_prompt = (
            "你是图像颜色分析助手。用户提供了一张背景图的左下角区域，"
            "里面通常有一大片相同颜色的色块（设计上用来放置视频或装饰）。\n\n"
            "请分析图片，返回该主色块的颜色（RGB 三通道 0-255）。\n"
            "只输出合法 JSON，不要任何解释文字。格式严格如下：\n"
            '{"r": <0-255>, "g": <0-255>, "b": <0-255>}\n\n'
            "约束：r/g/b 都是整数 0-255。如果左下角有多种颜色，"
            "返回面积最大的那个纯色块的 RGB。"
        )
        user_text = (
            "请分析这张图（左下角象限）的最大色块，返回其 RGB 值。"
        )
        try:
            raw_color = revision_service._call_llm_vision(
                system_prompt, user_text, [bl_tmp], entry=cur_entry,
            )
        except Exception as e:  # noqa: BLE001
            return _bg_finish_err(f"AI 颜色识别失败: {e}")
        import re as _re2
        cleaned = _re2.sub(r"```(?:json)?\s*|\s*```", "", raw_color or "").strip()
        s_idx, e_idx = cleaned.find("{"), cleaned.rfind("}")
        if s_idx < 0 or e_idx <= s_idx:
            return _bg_finish_err(f"AI 颜色输出不是合法 JSON: {(raw_color or '')[:200]}")
        try:
            parsed_color = json.loads(cleaned[s_idx:e_idx + 1])
        except Exception as e:  # noqa: BLE001
            return _err(f"AI 颜色输出解析失败: {e}")
        try:
            cr = int(parsed_color.get("r", -1))
            cg = int(parsed_color.get("g", -1))
            cb = int(parsed_color.get("b", -1))
        except (TypeError, ValueError):
            return _err(f"AI 颜色输出字段类型错: {parsed_color}")
        if not (0 <= cr <= 255 and 0 <= cg <= 255 and 0 <= cb <= 255):
            return _err(
                f"AI 颜色输出 RGB 越界: r={cr}, g={cg}, b={cb}"
            )

        # 像素扫描：在设计空间 scan_img 上找与 AI 识别色容差±10 内的像素
        color_tol = 10
        arr_full = _np.array(scan_img)
        diff = _np.abs(arr_full.astype(int) - _np.array([cr, cg, cb]))
        mask = (diff[:, :, 0] <= color_tol) & (diff[:, :, 1] <= color_tol) & (diff[:, :, 2] <= color_tol)
        ys, xs = _np.where(mask)
        n = int(xs.size)
        if n == 0:
            return _err(
                f"未检测到 RGB({cr},{cg},{cb})±{color_tol} 的像素；"
                f"AI 识别的颜色可能在背景图里不存在（请换图或换算法）"
            )
        x_min, x_max = int(xs.min()), int(xs.max())
        y_min, y_max = int(ys.min()), int(ys.max())
        # scan_img 已是 1920×1080，sx=sy=1.0
        payload = _result_payload(x_min, y_min, x_max, y_max, n, 1.0, 1.0)
        # _result_payload 用的是闭包变量 algorithm（此时是 "ai_color"），
        # 但 _ok 不允许重复 key — 显式 pop 再覆盖，统一返回 algorithm="ai_color"
        payload["algorithm"] = "ai_color"
        payload["threshold"] = None
        payload["detected_color"] = [cr, cg, cb]
        payload["color_tolerance"] = color_tol
        _save_bg_detect_cache(mgr, tid, payload)  # v8：检测后存缓存
        return _ok(
            **payload,
            toast=f"✅ AI 识别主色 RGB({cr},{cg},{cb})，已定位区域",
            model=cur_id,
        )

        # algorithm == "ai"
        from slirn_home import llm_config, revision_service

        models = llm_config.list_models(repo_root)
        cur_id = llm_config.get_current(repo_root)
        cur_entry = next((m for m in models if m.get("id") == cur_id), None)
        if cur_entry is None or not revision_service._is_vision_entry(cur_entry):
            vision_alts = [m["id"] for m in models if revision_service._is_vision_entry(m)]
            hint = (
                f" — 已注册可用多模态模型：{', '.join(vision_alts)}，请在顶栏 ⚙️ 切换"
            ) if vision_alts else (
                " — 请在顶栏 ⚙️ 注册多模态模型（推荐 qwen-vl-plus）"
            )
            return _err(
                f"AI 检测需多模态模型，当前模型不支持图片输入{hint}"
            )
        system_prompt = (
            "你是图像区域检测助手。用户上传了一张背景图，里面通常有一个白色矩形区域"
            "（设计上用来放置视频的位置 — 即背景里的「洞」）。\n\n"
            "请分析图片，返回该白色矩形区域在**原图像素坐标**下的最小外接矩形。"
            "只输出合法 JSON，不要任何解释文字。格式严格如下：\n"
            '{"x_min": <int>, "y_min": <int>, "x_max": <int>, "y_max": <int>}\n\n'
            "约束：坐标都是原图像素坐标（非归一化、非设计空间），左上角原点；"
            "x_min < x_max, y_min < y_max。找不到白色矩形就返回全 0：{\"x_min\":0,\"y_min\":0,\"x_max\":0,\"y_max\":0}"
        )
        user_text = (
            f"请分析这张背景图（原图分辨率 {iw}×{ih}），"
            "输出其中最大白色矩形区域的 bbox（左上角 + 右下角）。"
        )
        try:
            raw = revision_service._call_llm_vision(
                system_prompt, user_text, [bg_path_abs], entry=cur_entry,
            )
        except Exception as e:  # noqa: BLE001
            return _err(f"AI 检测失败: {e}")
        import re as _re
        cleaned = _re.sub(r"```(?:json)?\s*|\s*```", "", raw or "").strip()
        s_idx, e_idx = cleaned.find("{"), cleaned.rfind("}")
        if s_idx < 0 or e_idx <= s_idx:
            return _err(f"AI 输出不是合法 JSON: {(raw or '')[:200]}")
        try:
            parsed = json.loads(cleaned[s_idx:e_idx + 1])
        except Exception as e:  # noqa: BLE001
            return _err(f"AI 输出解析失败: {e}")
        try:
            x_min = int(parsed.get("x_min", 0))
            y_min = int(parsed.get("y_min", 0))
            x_max = int(parsed.get("x_max", 0))
            y_max = int(parsed.get("y_max", 0))
        except (TypeError, ValueError):
            return _err(f"AI 输出字段类型错: {parsed}")
        if x_max <= x_min or y_max <= y_min:
            return _err(
                f"AI 未识别到有效白色区域（x_min={x_min}, y_min={y_min}, "
                f"x_max={x_max}, y_max={y_max}）；请换张图或改用像素扫描"
            )
        # 像素数未知，估算 = bbox 面积
        pixel_count = (x_max - x_min + 1) * (y_max - y_min + 1)
        # AI 输出是原图坐标，按 scale 折算到设计空间
        payload = _result_payload(x_min, y_min, x_max, y_max, pixel_count, scale_x, scale_y)
        _save_bg_detect_cache(mgr, tid, payload)  # v8：检测后存缓存
        return _ok(
            **payload,
            toast=f"✅ AI 已识别白色区域（{cur_id}）",
            model=cur_id,
        )

    @app.app.post("/slirn/api/parse_reference_layout")

    @app.app.post("/slirn/api/parse_reference_layout")
    async def parse_reference_layout(request: Request, body: dict = Body(default_factory=dict)):
        """REQ-20260919-061 Phase C：调多模态模型解析参考位置关系图 → 4 素材布局。

        模型选择：自动扫描已注册 entry，挑第一个 _is_vision_entry() 为真的模型；
        若没有则提示用户在顶栏 ⚙️ 注册一个多模态模型（qwen-vl-plus 等）。
        响应：写回 fine_compose.json: layout.{video,subtitle,cover,bg}.{x,y,scale,enabled}。

        REQ-20260919-075：AI 智能布局写执行历史。
        """
        from slirn_home import execution_history, llm_config, revision_service
        _auto = request.headers.get("X-Slirn-Auto") == "1"
        # REQ-20260920-081：session 透传
        _auto_session_id = request.headers.get("X-Slirn-Auto-Session") or ""

        tid = (body.get("task_id") or "").strip()
        if not tid:
            return _err("缺少 task_id")
        try:
            mgr.get(tid)
        except Exception as e:  # noqa: BLE001
            return _err(f"任务不存在: {e}")

        outputs_dir = mgr.tasks_dir / tid / "outputs"
        # REQ-20260919-075：开始记录 — AI 智能布局
        exec_id = execution_history.record_start(
            outputs_dir, execution_history.KIND_FINE_AI_LAYOUT,
            extra={},
            auto=_auto,
            auto_session_id=_auto_session_id,
        )

        # 1. 校验参考图
        fc = _get_fine_compose(mgr, tid)
        ref_mat = fc.get("materials", {}).get("reference") or {}
        ref_path_rel = ref_mat.get("path")
        if not ref_path_rel:
            return _err("请先上传参考位置关系图（精剪视频面板「参考位置关系图」素材卡）")
        ref_path_abs = _resolve_mat_abs(mgr, tid, fc["materials"], "reference")
        if not ref_path_abs or not ref_path_abs.exists():
            return _err(f"参考图文件不存在: {ref_path_rel}")

        # 2. 选 vision entry：严格使用当前选定的模型（尊重用户 ⚙️ 选择，不静默注册）
        models = llm_config.list_models(repo_root)
        cur_id = llm_config.get_current(repo_root)
        cur_entry = next((m for m in models if m.get("id") == cur_id), None)
        if cur_entry is None:
            return _err(
                "未选择当前模型 — 请在顶栏 ⚙️ 选定一个大模型后再试"
            )
        if not revision_service._is_vision_entry(cur_entry):
            # 列出已注册的多模态备选（如有），帮用户快速切换
            vision_alts = [m["id"] for m in models if revision_service._is_vision_entry(m)]
            hint = (
                f" — 已在配置中找到可用的多模态模型：{', '.join(vision_alts)}，"
                "请在顶栏 ⚙️ 切换当前模型"
            ) if vision_alts else (
                " — 请在顶栏 ⚙️ 添加多模态模型（推荐 qwen-vl-plus），"
                "需要环境变量 DASHSCOPE_API_KEY"
            )
            return _err(
                f"当前模型「{cur_id}」不支持图片输入{hint}"
            )
        vision_entry = cur_entry

        # 3. 调多模态模型 — REQ-20260919-061 用户补充：x/y 改为像素（1920×1080 设计空间）
        system_prompt = (
            "你是视频素材布局助手。用户上传了一张参考位置关系图（草图/截图/示意图都可能），"
            "里面展示了 4 个素材应该放在哪个相对位置：\n"
            "- video（主视频，通常占中间大面积）\n"
            "- subtitle（SRT 文件内嵌到视频上的文字）\n"
            "- cover（封面/角标，小图叠加层）\n"
            "- bg（背景图片，视频比例不一致时填充背景）\n\n"
            "请分析图片，输出 4 个素材的位置和缩放（1920×1080 画布的**像素坐标**，原点在左上角）：\n"
            "只返回合法 JSON，不要任何解释文字。格式严格如下：\n"
            "{\"video\":{\"x\":0,\"y\":0,\"scale\":1.0,\"enabled\":true},"
            "\"subtitle\":{\"x\":960,\"y\":972,\"scale\":1.0,\"enabled\":true},"
            "\"cover\":{\"x\":1536,\"y\":108,\"scale\":0.3,\"enabled\":true},"
            "\"bg\":{\"x\":0,\"y\":0,\"scale\":1.0,\"enabled\":false}}\n\n"
            "约束：x 是 -1920–1920 之间的整数像素（允许负数，做'露半边'效果）；"
            "y 是 -1080–1080 之间的整数像素（允许负数）；"
            "scale 是 0.1-2.0 之间的浮点数；enabled 必须是布尔值。"
        )
        user_text = (
            f"请分析这张参考位置关系图（任务 ID: {tid}），"
            f"输出 4 个素材在 {_FINE_DESIGN_W}×{_FINE_DESIGN_H} 画布上的像素坐标。"
        )

        try:
            raw = revision_service._call_llm_vision(
                system_prompt, user_text, [ref_path_abs], entry=vision_entry,
            )
        except Exception as e:  # noqa: BLE001
            return _err(f"多模态模型调用失败: {e}")

        # 4. 防御式解析 JSON
        import re as _re
        cleaned = _re.sub(r"```(?:json)?\s*|\s*```", "", raw or "").strip()
        # 截取首个 { 到最后一个 }
        s_idx, e_idx = cleaned.find("{"), cleaned.rfind("}")
        if s_idx < 0 or e_idx <= s_idx:
            return _err(f"模型输出不是合法 JSON: {(raw or '')[:200]}")
        try:
            parsed = json.loads(cleaned[s_idx:e_idx + 1])
        except Exception as e:  # noqa: BLE001
            return _err(f"模型输出不是合法 JSON: {e}")
        if not isinstance(parsed, dict):
            return _err("模型输出不是 JSON 对象")

        # 5. 字段校验 + 合并回 layout（兼容模型返回 0-1 旧版：自动按比例转像素）
        keys = ("video", "subtitle", "cover", "bg")
        merged: dict[str, dict] = {}
        for k in keys:
            v = parsed.get(k)
            if not isinstance(v, dict):
                continue
            try:
                x_raw = float(v.get("x", 0.5))
                y_raw = float(v.get("y", 0.5))
                scale = float(v.get("scale", 1.0))
                enabled = bool(v.get("enabled", True))
            except (TypeError, ValueError):
                continue
            scale = max(0.1, min(2.0, scale))
            # AI 模型可能仍按旧 prompt 输出 0-1：值在 [0,1] 时按比例放大；否则按像素护栏
            if 0.0 <= x_raw <= 1.0 and 0.0 <= y_raw <= 1.0:
                # 边界值 1.0 也按比例放大（避免 y=1.0 误判成「像素 1」）
                x = int(round(x_raw * _FINE_DESIGN_W))
                y = int(round(y_raw * _FINE_DESIGN_H))
            else:
                # v13：画布 X/Y 允许负数（−画布宽到+画布宽，−画布高到+画布高）
                x = max(-_FINE_DESIGN_W, min(_FINE_DESIGN_W, int(round(x_raw))))
                y = max(-_FINE_DESIGN_H, min(_FINE_DESIGN_H, int(round(y_raw))))
            merged[k] = {"x": x, "y": y, "scale": scale, "enabled": enabled}

        if not merged:
            execution_history.patch_fields(outputs_dir, exec_id, {
                "description": f"AI 未输出任何有效布局（{vision_entry['id']}）"
            })
            execution_history.record_finish(outputs_dir, exec_id, success=False,
                                            error="模型未输出任何有效素材布局")
            return _err(f"模型未输出任何有效素材布局: {(raw or '')[:200]}")

        for k, v in merged.items():
            fc["layout"][k] = {**fc["layout"].get(k, {}), **v}
        _save_fine_compose(mgr, tid, fc)
        # REQ-20260919-075：完成时回填具体执行情况
        execution_history.patch_fields(outputs_dir, exec_id, {
            "description": (f"AI 生成布局（{vision_entry['id']}）：{len(merged)} 个素材位置"
                            f"（{', '.join(merged.keys())}）")
        })
        execution_history.record_finish(outputs_dir, exec_id, success=True, error="")
        return _ok(
            layout={k: fc["layout"][k] for k in keys},
            toast=f"✅ AI 已生成布局（{vision_entry['id']}）",
            model=vision_entry["id"],
        )

    @app.app.post("/slirn/api/save_fine_font")
    async def save_fine_font(body: dict = Body(default_factory=dict)):
        """保存精剪视频字幕字体 5 项设置。"""
        tid = (body.get("task_id") or "").strip()
        font = body.get("font") or {}
        if not tid:
            return _err("缺少 task_id")
        try:
            t = mgr.get(tid)
        except Exception:  # noqa: BLE001 — TaskNotFoundError 等
            return _err(f"任务不存在: {tid}")
        if not t:
            return _err(f"任务不存在: {tid}")
        fc = _get_fine_compose(mgr, tid)
        # 字段白名单
        allowed = set(_FINE_FONT_DEFAULTS.keys())
        # REQ-20260919-062 v19：颜色字段只接受 #RRGGBB（前端 picker 一定给完整 6 位；
        # 拒绝非 hex 字符串以防注入或简写 #fff 导致渲染崩溃）
        for k, v in font.items():
            if k in allowed:
                if k in ("color", "stroke_color", "bg_color") and not _HEX_COLOR_OK.match(str(v)):
                    continue  # 非法值丢弃，保持原值
                fc["font"][k] = v
        _save_fine_compose(mgr, tid, fc)
        return _ok(toast="字体设置已保存")

    @app.app.post("/slirn/api/save_fine_output")
    async def save_fine_output(body: dict = Body(default_factory=dict)):
        """保存精剪视频输出设置（分辨率）。"""
        tid = (body.get("task_id") or "").strip()
        output = body.get("output") or {}
        if not tid:
            return _err("缺少 task_id")
        try:
            t = mgr.get(tid)
        except Exception:  # noqa: BLE001 — TaskNotFoundError 等
            return _err(f"任务不存在: {tid}")
        if not t:
            return _err(f"任务不存在: {tid}")
        fc = _get_fine_compose(mgr, tid)
        if "resolution" in output and output["resolution"] in ("1080p", "720p", "source"):
            fc["output"]["resolution"] = output["resolution"]
        _save_fine_compose(mgr, tid, fc)
        return _ok(toast="输出设置已保存")

    @app.app.post("/slirn/api/save_fine_audio")
    async def save_fine_audio(body: dict = Body(default_factory=dict)):
        """REQ-20260919-061 扩展：保存背景音乐设置（4 项：启用/音量/淡入/淡出）。"""
        tid = (body.get("task_id") or "").strip()
        audio = body.get("audio") or {}
        if not tid:
            return _err("缺少 task_id")
        try:
            t = mgr.get(tid)
        except Exception:  # noqa: BLE001 — TaskNotFoundError 等
            return _err(f"任务不存在: {tid}")
        if not t:
            return _err(f"任务不存在: {tid}")
        fc = _get_fine_compose(mgr, tid)
        # 字段白名单 + 类型规整
        allowed = set(_FINE_AUDIO_DEFAULTS.keys())
        for k, v in audio.items():
            if k not in allowed:
                continue
            if k == "enabled":
                fc["audio"][k] = bool(v)
            else:
                try:
                    fc["audio"][k] = float(v)
                except (TypeError, ValueError):
                    pass  # 非法值跳过
        _save_fine_compose(mgr, tid, fc)
        return _ok(toast="背景音乐设置已保存")

    # ---------- 精剪视频·系统默认 BGM（REQ-20260920-078） ----------
    @app.app.post("/slirn/api/list_default_bgms")
    async def list_default_bgms(body: dict = Body(default_factory=dict)):
        """REQ-20260920-078：列出系统默认 BGM（带 available 标记）。

        前端在精剪面板「🎵 背景音乐」块加载时 fetch，渲染下拉选项；
        文件缺失则灰显，避免运行时崩溃。
        """
        return _ok(bgms=_get_default_bgms())

    @app.app.post("/slirn/api/select_default_bgm")
    async def select_default_bgm(body: dict = Body(default_factory=dict)):
        """REQ-20260920-078：选某个系统默认 BGM → 复制到任务目录 + 写 fc。

        流程：
        1. 校验 bgm_id 在白名单内
        2. 校验源文件存在
        3. shutil.copy2 复制到 tasks/{tid}/materials/audio/<id>.mp3
        4. fc.materials.audio = {"path": "materials/audio/<id>.mp3"}
        5. fc.audio.enabled = True（自动启用）
        6. 返回 audio_url 用于前端预览
        """
        import shutil as _sh
        tid = (body.get("task_id") or "").strip()
        bgm_id = (body.get("bgm_id") or "").strip()
        if not tid or not bgm_id:
            return _err("缺少 task_id 或 bgm_id")
        bgm = next((b for b in _DEFAULT_BGMS if b["id"] == bgm_id), None)
        if not bgm:
            return _err(f"未知的 bgm_id: {bgm_id}")
        src = _DEFAULT_BGMS_DIR / bgm["filename"]
        if not src.exists():
            return _err(f"系统默认 BGM 文件不存在: {src}")
        try:
            t = mgr.get(tid)
        except Exception as e:  # noqa: BLE001
            return _err(f"任务不存在: {e}")
        if not t:
            return _err(f"任务不存在: {tid}")

        # 目标路径：tasks/{tid}/materials/audio/<id>.mp3
        mat_dir = mgr.tasks_dir / tid / "materials" / "audio"
        mat_dir.mkdir(parents=True, exist_ok=True)
        dst = mat_dir / f"{bgm_id}.mp3"
        try:
            _sh.copy2(src, dst)
        except OSError as e:
            return _err(f"复制 BGM 失败: {e}")

        # 写 fc：materials.audio.path + audio.enabled=True
        # REQ-20260920-083：路径必须含 tasks/<tid>/ 前缀，让 _resolve_mat_abs cand2 命中
        # （与 upload_fine_material_form 写的路径约定一致；旧版写 'materials/audio/<id>.mp3'
        # 无前缀 → resolver 找不到 → audio 被静默禁用 → 预览/导出没 BGM）
        fc = _get_fine_compose(mgr, tid)
        fc.setdefault("materials", {})["audio"] = {
            "path": f"tasks/{tid}/materials/audio/{bgm_id}.mp3",
        }
        fc.setdefault("audio", {})["enabled"] = True
        _save_fine_compose(mgr, tid, fc)

        audio_url = f"/slirn/api/video/{tid}?src=mat&kind=audio&t={int(time.time())}"
        return _ok(
            audio_url=audio_url,
            name=bgm["name"],
            toast=f"✅ 已选 BGM: {bgm['name']}",
        )

    # ---------- 精剪视频·全局参数模板（REQ-20260919-061 扩展：跨任务复用） ----------
    from slirn_home import fine_profiles as _fine_profiles

    @app.app.post("/slirn/api/list_fine_global_profiles")
    async def list_fine_global_profiles(body: dict = Body(default_factory=dict)):
        """列出所有精剪全局参数模板。"""
        profiles = _fine_profiles.list_profiles(repo_root)
        # 仅返回 UI 需要的字段（params 体积较大，省略除非显式需要）
        out = [
            {
                "id": p.get("id"),
                "name": p.get("name"),
                "saved_at": p.get("saved_at"),
                "task_id_origin": p.get("task_id_origin"),
            }
            for p in profiles
        ]
        return _ok(profiles=out)

    @app.app.post("/slirn/api/save_fine_global_profile")
    async def save_fine_global_profile(body: dict = Body(default_factory=dict)):
        """把当前 task 的 layout/font/output/audio 保存为命名模板。"""
        tid = (body.get("task_id") or "").strip()
        name = (body.get("name") or "").strip()
        if not tid:
            return _err("缺少 task_id")
        if not name:
            return _err("模板名不能为空")
        if len(name) > 30:
            return _err("模板名不能超过 30 字")
        try:
            t = mgr.get(tid)
        except Exception:  # noqa: BLE001
            return _err(f"任务不存在: {tid}")
        if not t:
            return _err(f"任务不存在: {tid}")
        fc = _get_fine_compose(mgr, tid)
        params = {
            "layout": fc.get("layout") or {},
            "font": fc.get("font") or {},
            "output": fc.get("output") or {},
            "audio": fc.get("audio") or {},
            # REQ-20260920-076：把背景图检测结果也存进模板（与任务级 export 同口径），
            # 旧模板（无 detected_region 字段）→ get() 返回 None，应用时不动目标任务的。
            "detected_region": fc.get("detected_region"),
        }
        profile = _fine_profiles.save_profile(repo_root, name, params, task_id_origin=tid)
        return _ok(
            profile={
                "id": profile["id"],
                "name": profile["name"],
                "saved_at": profile["saved_at"],
                "task_id_origin": profile["task_id_origin"],
            },
            toast=f"✅ 已保存模板「{profile['name']}」",
        )

    @app.app.post("/slirn/api/apply_fine_global_profile")
    async def apply_fine_global_profile(body: dict = Body(default_factory=dict)):
        """把模板 params 写入当前 task 的 fine_compose（覆盖 layout/font/output/audio）。

        不动 materials — 任务自己的素材文件路径保持不变。
        返回新的 wb HTML，UI 直接替换刷新。
        """
        tid = (body.get("task_id") or "").strip()
        profile_id = (body.get("profile_id") or "").strip()
        if not tid:
            return _err("缺少 task_id")
        if not profile_id:
            return _err("缺少 profile_id")
        try:
            t = mgr.get(tid)
        except Exception:  # noqa: BLE001
            return _err(f"任务不存在: {tid}")
        if not t:
            return _err(f"任务不存在: {tid}")
        profile = _fine_profiles.get_profile(repo_root, profile_id)
        if not profile:
            return _err(f"模板不存在或已删除: {profile_id}")
        fc = _get_fine_compose(mgr, tid)
        params = profile.get("params") or {}
        # 只覆盖白名单字段（materials 永不动）
        for key in _fine_profiles.PROFILE_PARAM_KEYS:
            if key in params:
                fc[key] = params[key]
        _save_fine_compose(mgr, tid, fc)
        # 返新 wb HTML，前端直接替换 #slirn-tab-workbench-inner
        wb_html = _render_workbench(tid, mgr)
        return _ok(html=wb_html, toast=f"✅ 已应用模板「{profile.get('name')}」")

    @app.app.post("/slirn/api/delete_fine_global_profile")
    async def delete_fine_global_profile(body: dict = Body(default_factory=dict)):
        """从全局 JSON 移除指定模板（不影响已应用的任务）。"""
        profile_id = (body.get("profile_id") or "").strip()
        if not profile_id:
            return _err("缺少 profile_id")
        ok = _fine_profiles.delete_profile(repo_root, profile_id)
        if not ok:
            return _err(f"模板不存在或已删除: {profile_id}")
        return _ok(toast="🗑 已删除模板")

    @app.app.post("/slirn/api/rename_fine_global_profile")
    async def rename_fine_global_profile(body: dict = Body(default_factory=dict)):
        """重命名指定模板（重名时自动加 `(2)` 后缀）。"""
        profile_id = (body.get("profile_id") or "").strip()
        new_name = (body.get("name") or "").strip()
        if not profile_id:
            return _err("缺少 profile_id")
        if not new_name:
            return _err("新名不能为空")
        if len(new_name) > 30:
            return _err("新名不能超过 30 字")
        ok = _fine_profiles.rename_profile(repo_root, profile_id, new_name)
        if not ok:
            return _err(f"模板不存在或已删除: {profile_id}")
        return _ok(toast="✏️ 已重命名")

    @app.app.post("/slirn/api/export_fine_global_profile")
    async def export_fine_global_profile(body: dict = Body(default_factory=dict)):
        """REQ-20260919-070：把单个全局模板参数下载为 JSON 文件。

        与 `export_fine_params`（当前任务）同口径：返回 {filename, content, mime}，
        前端用 Blob 触发下载。模板不含 materials（按 REQ-20260919-061 设计），
        故 payload.materials = {}（与任务导出口径一致，导入端按"无 materials"
        处理）。
        """
        pid = (body.get("profile_id") or "").strip()
        if not pid:
            return _err("缺少 profile_id")
        prof = _fine_profiles.get_profile(repo_root, pid)
        if not prof:
            return _err(f"模板不存在或已删除: {pid}")
        params = prof.get("params") or {}
        payload = {
            "_schema": 3,
            "_exported_at": datetime.now().isoformat(timespec="seconds"),
            "_source_profile_id": pid,
            "_source_profile_name": prof.get("name", ""),
            "_source_task_id": prof.get("task_id_origin", ""),
            "materials": {},  # 全局模板按设计不含素材
            "layout": params.get("layout"),
            "font": params.get("font"),
            "output": params.get("output"),
            "audio": params.get("audio"),
            # REQ-20260920-076：与任务级 export_fine_params 口径一致；旧模板
            # （无 detected_region 字段）→ params.get("detected_region") = None
            "detected_region": params.get("detected_region"),
        }
        # 文件名：清理模板名里的非法字符 + 时间戳
        safe_name = re.sub(r'[\\/:*?"<>|\s]+', "_", prof.get("name") or "profile")[:30]
        filename = (
            f"fine_params_profile_{safe_name}_"
            f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        )
        try:
            content = json.dumps(payload, ensure_ascii=False, indent=2)
        except Exception as e:  # noqa: BLE001
            return _err(f"序列化失败: {e}")
        return _ok(filename=filename, content=content, mime="application/json")

    @app.app.post("/slirn/api/render_fine_preview")
    async def render_fine_preview(request: Request, body: dict = Body(default_factory=dict)):
        """REQ-20260919-061 Phase B：渲染精剪视频预览（ffmpeg overlay）。

        REQ-20260919-061 用户反馈：预览时长可调（2–30 秒，默认 10）。body 里读
        `duration` 字段，无效值兜底 10。

        REQ-20260919-064：新增 `preview_start` 字段（默认 0），让用户能跳到源视频
        任意时间点预览。start + duration 由后端钳到不超出源视频时长
        （start 不会负；不会因 ffmpeg 0-frame 报错）。

        输出：outputs/fine_preview.mp4（相对 slirn-standalone 根）

        REQ-20260920-081：执行历史埋点（KIND_FINE_PREVIEW，同步任务，开始/结束成对记录）。
        """
        from slirn_home import execution_history
        _auto = request.headers.get("X-Slirn-Auto") == "1"
        _auto_session_id = request.headers.get("X-Slirn-Auto-Session") or ""
        tid = (body.get("task_id") or "").strip()
        if not tid:
            return _err("缺少 task_id")
        try:
            mgr.get(tid)
        except Exception as e:  # noqa: BLE001
            return _err(f"任务不存在: {e}")
        # REQ-20260920-081：执行历史 — 同步渲染开始时记 running
        outputs_dir = mgr.tasks_dir / tid / "outputs"
        _exec_id = execution_history.record_start(
            outputs_dir,
            execution_history.KIND_FINE_PREVIEW,
            extra={"duration_sec": None, "preview_start": 0.0},  # 下方覆盖
            auto=_auto,
            auto_session_id=_auto_session_id,
        )
        # 解析预览时长，钳到 [2, 30]（前端已有 min/max 限制，后端兜底防越界）
        raw_dur = body.get("duration")
        try:
            preview_dur = float(raw_dur)
        except (TypeError, ValueError):
            preview_dur = 10.0
        preview_dur = max(2.0, min(30.0, preview_dur))
        # REQ-20260919-064：解析预览开始时间，默认 0
        raw_start = body.get("preview_start", 0)
        try:
            preview_start = float(raw_start)
        except (TypeError, ValueError):
            preview_start = 0.0
        preview_start = max(0.0, preview_start)
        # 钳 start+duration 不超出源视频时长（用 ffprobe 拿时长；失败兜底不钳）
        try:
            import subprocess as _sp
            from pathlib import Path as _P
            fc_for_dur = _get_fine_compose(mgr, tid)
            vp = _resolve_mat_abs(mgr, tid, fc_for_dur["materials"], "video")
            if vp and _P(vp).exists():
                _pr = _sp.run(
                    ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=nw=1:nk=1", str(vp)],
                    capture_output=True, text=True, timeout=10,
                )
                _vdur = float((_pr.stdout or "0").strip() or "0")
                if _vdur > 0 and preview_start + preview_dur > _vdur:
                    # start 后移，让 start+duration = video_duration
                    preview_start = max(0.0, _vdur - preview_dur)
        except Exception:
            pass  # ffprobe 失败 → 不钳，让 ffmpeg 自己处理（最多产生 0-frame 报错）
        out_path = mgr.tasks_dir / tid / "outputs" / "fine_preview.mp4"
        result = _run_fine_render(tid, mgr, out_path,
                                   duration=preview_dur, preview_start=preview_start)
        # REQ-20260920-081：执行历史 — 同步渲染完成回填 finish
        if _exec_id:
            try:
                execution_history.patch_extra(
                    outputs_dir, _exec_id,
                    {"duration_sec": preview_dur, "preview_start": preview_start,
                     "output_path": str(out_path)},
                )
                execution_history.record_finish(
                    outputs_dir, _exec_id,
                    success=bool(result.get("ok")),
                    error=str(result.get("error") or "")[:500],
                )
            except Exception:
                pass
        if not result.get("ok"):
            return result
        return _ok(
            url=f"/slirn/api/video/{tid}?src=fine_preview&t={int(time.time())}",
            toast=f"🎬 预览已生成（第 {preview_start:g}–{preview_start + preview_dur:g} 秒）",
        )

    @app.app.post("/slirn/api/export_fine_video")
    async def export_fine_video(request: Request, body: dict = Body(default_factory=dict)):
        """REQ-20260919-074：导出最终精剪视频（异步后台任务）。

        用户反馈：实际最终视频 1-3 小时长，旧的同步 `_run_fine_render` timeout=120s
        完全不够；fetch 同步等 1-3 小时浏览器也会卡死。这次改为：

        1. 本端点立即返回 `{job_id, toast}`（不阻塞，<0.5 秒）
        2. 后台 daemon 线程跑 `_run_fine_render_async`，写 `_JOB_REGISTRY[job_id]`
        3. 前端 GET `/slirn/api/render_status?job_id=X` 轮询拿进度
        4. 完成后前端点关闭 → 调 `/slirn/api/cancel_render` 不会触发（job 已 done）

        REQ-20260920-081：record_start 写一条 KIND_FINE_EXPORT 历史，exec_id 和
        outputs_dir 传给后台线程用于 record_finish（4 个出口：assemble 失败 /
        FileNotFound / 取消 / returncode 非 0 / returncode 0）。auto + session_id
        从 X-Slirn-Auto / X-Slirn-Auto-Session 透传（REQ-20260919-075 + 081）。
        """
        from slirn_home import execution_history
        _auto = request.headers.get("X-Slirn-Auto") == "1"
        _auto_session_id = request.headers.get("X-Slirn-Auto-Session") or ""
        tid = (body.get("task_id") or "").strip()
        if not tid:
            return _err("缺少 task_id")
        try:
            mgr.get(tid)
        except Exception as e:  # noqa: BLE001
            return _err(f"任务不存在: {e}")

        # 清理过期 job
        _cleanup_stale_jobs(mgr)

        # 同一任务已有运行中/排队的 job → 拒绝重启（避免并发写同一文件）
        with _JOB_LOCK:
            for existing in _JOB_REGISTRY.values():
                if existing.task_id == tid and existing.state in ("queued", "running"):
                    return _err(
                        f"该任务已有运行中的渲染（job_id={existing.job_id}），请等待完成或取消"
                    )

        # 创建 job + 启线程
        job_id = f"job_{int(time.time() * 1000)}_{os.getpid()}"
        out_path = mgr.tasks_dir / tid / "outputs" / "fine_export.mp4"
        outputs_dir = mgr.tasks_dir / tid / "outputs"
        job = _RenderJob(job_id=job_id, task_id=tid)
        with _JOB_LOCK:
            _JOB_REGISTRY[job_id] = job
        # REQ-20260920-084：写 .export_job.json 落盘文件，供页面刷新后回查
        _write_active_export_job(mgr, tid, job_id, "queued")

        # REQ-20260920-081：执行历史埋点（立即记 running；后台线程 4 个出口都补 finish）
        fc_for_extra = _get_fine_compose(mgr, tid)
        _ext_exec = execution_history.record_start(
            outputs_dir,
            execution_history.KIND_FINE_EXPORT,
            extra={
                "job_id": job_id,
                "resolution": (fc_for_extra.get("output") or {}).get("resolution", "1080p"),
                "bgm_enabled": bool((fc_for_extra.get("audio") or {}).get("enabled", False)),
            },
            auto=_auto,
            auto_session_id=_auto_session_id,
        )

        t = threading.Thread(
            target=_run_fine_render_async,
            args=(job, tid, mgr, out_path, _ext_exec, outputs_dir),
            daemon=True,
            name=f"fine-render-{job_id}",
        )
        t.start()

        return _ok(
            job_id=job_id,
            toast=f"🎬 导出已启动（{tid[:8]}），可在本面板下方看进度",
        )

    @app.app.get("/slirn/api/render_status")
    async def render_status(job_id: str):
        """REQ-20260919-074：返回指定 job 的实时进度。

        前端每 1.5 秒轮询一次。完成后保留 5 分钟供最后一次查询拿到 output_url。

        REQ-20260920-084：elapsed_sec 在 GET 时实时算（基于 time.monotonic()），
        避免 ffmpeg init 阶段没输出 out_time_ms= 导致进度时间一直 0。
        """
        if not job_id:
            return _err("缺少 job_id")
        with _JOB_LOCK:
            job = _JOB_REGISTRY.get(job_id)
        if not job:
            return _err(f"job 不存在或已过期（>{_JOB_TTL_SEC // 60} 分钟）: {job_id}")
        # REQ-20260920-084：实时算 elapsed（不依赖 ffmpeg progress 行）
        if job.state == "running" and job.started_at > 0:
            elapsed_live = time.monotonic() - job.started_at
        elif job.state in ("done", "failed", "cancelled") and job.started_at > 0 and job.finished_at > 0:
            elapsed_live = job.finished_at - job.started_at
        else:
            elapsed_live = job.elapsed_sec  # queued 或未启动
        return _ok(
            job_id=job.job_id,
            task_id=job.task_id,
            state=job.state,
            elapsed_sec=round(elapsed_live, 1),
            progress_pct=round(job.progress_pct, 1),
            progress_time_ms=job.progress_time_ms,
            total_duration_ms=job.total_duration_ms,
            speed_x=round(job.speed_x, 2),
            eta_sec=round(job.eta_sec, 1) if job.eta_sec >= 0 else None,
            error=job.error,
            output_url=job.output_url,
        )

    @app.app.get("/slirn/api/active_export_for_task")
    async def active_export_for_task(task_id: str):
        """REQ-20260920-084：查指定 task 是否有 in-flight 导出 job（页面刷新后回显）。

        优先查内存 _JOB_REGISTRY（权威、实时）；落盘 .export_job.json 兜底
        （服务重启但 ffmpeg 仍在的场景）。
        返回：{ok: true, job: {job_id, state, started_at, source, warning?}} 或 job: null
        """
        if not task_id:
            return _err("缺少 task_id")
        try:
            mgr.get(task_id)
        except Exception as e:  # noqa: BLE001
            return _err(f"任务不存在: {e}")

        # 优先：内存注册表（权威，实时）
        with _JOB_LOCK:
            for j in _JOB_REGISTRY.values():
                if j.task_id == task_id and j.state in ("queued", "running"):
                    return _ok(job={
                        "job_id": j.job_id,
                        "state": j.state,
                        "started_at": j.wall_started_at,
                        "source": "registry",
                    })

        # 兜底：落盘文件（服务重启但 ffmpeg 仍在跑）
        disk = _read_active_export_job(mgr, task_id)
        if not disk:
            return _ok(job=None)
        job_id_disk = disk.get("job_id") or ""
        if not job_id_disk:
            try: _delete_active_export_job(mgr, task_id)
            except Exception: pass
            return _ok(job=None)
        return _ok(job={
            "job_id": job_id_disk,
            "state": disk.get("state", "running"),
            "started_at": disk.get("started_at", 0),
            "source": "disk",
            "warning": "内存无此 job（服务可能已重启）；ffmpeg 状态未知",
        })

    @app.app.post("/slirn/api/cancel_render")
    async def cancel_render(body: dict = Body(default_factory=dict)):
        """REQ-20260919-074：取消正在渲染的 job（SIGTERM → 5 秒后 SIGKILL 兜底）。"""
        job_id = (body.get("job_id") or "").strip()
        if not job_id:
            return _err("缺少 job_id")
        with _JOB_LOCK:
            job = _JOB_REGISTRY.get(job_id)
        if not job:
            return _err(f"job 不存在: {job_id}")
        if job.state not in ("queued", "running"):
            return _err(f"job 已处于终态（{job.state}），无法取消")
        # 先标记 cancelled，再发信号（让后台线程主循环的 stdout 读返回后能立即退出）
        job.state = "cancelled"
        job.finished_at = time.monotonic()
        job.wall_finished_at = time.time()
        if job.proc:
            try:
                job.proc.terminate()
            except Exception as e:
                return _err(f"取消信号发送失败: {e}")
        return _ok(toast="⏹ 已发送取消信号")

    # REQ-20260919-065：精剪参数 JSON 导出/导入
    @app.app.post("/slirn/api/export_fine_params")
    async def export_fine_params(body: dict = Body(default_factory=dict)):
        """导出精剪参数为 JSON 字符串（不含素材文件本体，也不上传素材路径）。

        REQ-20260919-073：materials 是任务本地上传的素材（视频/字幕/封面/BGM/参考图
        的文件路径），跨任务/跨机器复用无意义。导入端始终不动 materials（见
        import_fine_params），故导出端本就不该把 materials 写进文件 — 与全局模板
        `export_fine_global_profile` 同口径。

        返回 {ok, filename, content, mime} → 前端用 Blob 触发下载。
        """
        tid = (body.get("task_id") or "").strip()
        if not tid:
            return _err("缺少 task_id")
        try:
            mgr.get(tid)
        except Exception as e:  # noqa: BLE001
            return _err(f"任务不存在: {e}")
        fc = _get_fine_compose(mgr, tid)
        payload = {
            "_schema": 3,
            "_exported_at": datetime.now().isoformat(timespec="seconds"),
            "_source_task_id": tid,
            "materials": {},    # REQ-20260919-073：素材路径不是设置参数，不导出
            "layout": fc.get("layout"),
            "font": fc.get("font"),
            "output": fc.get("output"),
            "audio": fc.get("audio"),
            "detected_region": fc.get("detected_region"),
        }
        filename = f"fine_params_{tid}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        try:
            content = json.dumps(payload, ensure_ascii=False, indent=2)
        except Exception as e:  # noqa: BLE001
            return _err(f"序列化失败: {e}")
        return _ok(filename=filename, content=content, mime="application/json")

    @app.app.post("/slirn/api/import_fine_params")
    async def import_fine_params(body: dict = Body(default_factory=dict)):
        """导入精剪参数 JSON 字符串 → 覆盖当前任务 layout/font/output/audio/detected_region。
        不修改 materials（视频/封面/BGM 文件保持当前任务的）。
        返回 {ok, applied_fields} → 前端用 toast + 刷新 wb。
        """
        tid = (body.get("task_id") or "").strip()
        if not tid:
            return _err("缺少 task_id")
        try:
            mgr.get(tid)
        except Exception as e:  # noqa: BLE001
            return _err(f"任务不存在: {e}")
        raw = body.get("content")
        if not isinstance(raw, str):
            return _err("导入内容必须为 JSON 字符串")
        # 64KB 上限（实际参数体积远低于此）
        if len(raw) > 64 * 1024:
            return _err("文件过大（>64KB），拒绝导入")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as e:
            return _err(f"JSON 解析失败: {e}")
        if not isinstance(payload, dict):
            return _err("JSON 顶层必须是对象")
        schema = payload.get("_schema")
        if schema not in (2, 3):
            return _err(
                f"参数文件 _schema 不兼容（当前仅支持 2/3，文件为 {schema}）"
            )
        # 校验 5 个字段类型（必须 dict 或 null/缺省）
        for key in ("layout", "font", "output", "audio", "detected_region"):
            v = payload.get(key)
            if v is not None and not isinstance(v, dict):
                return _err(f"{key} 必须是 dict 或 null")
        fc = _get_fine_compose(mgr, tid)
        applied: list[str] = []
        for key in ("layout", "font", "output", "audio", "detected_region"):
            if key in payload:
                fc[key] = payload[key]
                applied.append(key)
        try:
            _save_fine_compose(mgr, tid, fc)
        except Exception as e:  # noqa: BLE001
            return _err(f"保存失败: {e}")
        return _ok(applied_fields=applied, toast=f"✅ 已应用 {len(applied)} 个字段")

    @app.app.post("/slirn/api/upload_fine_material_form")
    async def upload_fine_material_form(
        task_id: str = _Form(...),
        kind: str = _Form(...),
        file: UploadFile = File(...),
    ):
        """REQ-20260919-061：上传精剪视频素材 — multipart/form-data。
        file 存到 tasks/{tid}/upload/{kind}_{filename}，路径写到 task.json:fine_compose.materials.{kind}.path
        """
        if kind not in _FINE_MATERIAL_KINDS:
            return _err(f"非法素材类型: {kind}")
        t = mgr.get(task_id)
        if not t:
            return _err(f"任务不存在: {task_id}")
        # 防路径穿越 + 安全文件名
        safe_name = Path(file.filename or f"{kind}_upload").name
        save_dir = repo_root / "tasks" / task_id / "upload"
        save_dir.mkdir(parents=True, exist_ok=True)
        save_path = save_dir / f"{kind}_{safe_name}"
        # 写文件
        try:
            content = await file.read()
            save_path.write_bytes(content)
        except Exception as e:  # noqa: BLE001
            return _err(f"保存失败: {e}")
        # 写回 task.json
        fc = _get_fine_compose(mgr, task_id)
        fc["materials"][kind] = {
            "path": str(save_path.relative_to(repo_root)),
            "type": {"video": "video", "subtitle": "srt", "audio": "audio"}.get(kind, "image"),
            "source": "upload",
        }
        _save_fine_compose(mgr, task_id, fc)
        # REQ-20260920-085：上传音频素材时自动启用 BGM（与 select_default_bgm 对齐）
        # 修复「上传 mp3 后生成预览无 BGM」BUG：原代码只写 materials.audio.path，
        # 不写 audio.enabled → _assemble_fine_filter audio gate 把 BGM 静默禁用。
        # 仅当 enabled 仍是默认值 False 时才自动启用（不覆盖用户手动 toggle）：
        #   - 全新任务（fc.audio 由 _get_fine_compose 默认填 enabled=False）→ 自动开 ✅
        #   - 用户已 save_fine_audio 设过 enabled=True → 已是 True（再写幂等）✅
        #   - 用户已 save_fine_audio 设过 enabled=False → 保留 False，不强行打开 ✅
        # volume 已经是 _FINE_AUDIO_DEFAULTS["volume"]=0.4，无需再写。
        if kind == "audio":
            audio_block = fc.setdefault("audio", {})
            if audio_block.get("enabled", _FINE_AUDIO_DEFAULTS["enabled"]) == _FINE_AUDIO_DEFAULTS["enabled"]:
                audio_block["enabled"] = True
            _save_fine_compose(mgr, task_id, fc)
        # REQ-20260920-079：超大图片上传提示（前端 toast；不动原图）
        warning: str | None = None
        if kind in ("bg", "cover", "reference") and save_path.suffix.lower() in _PIL_IMAGE_EXTS:
            try:
                from PIL import Image as _PILImage2
                with _PILImage2.open(save_path) as _im:
                    iw, ih = _im.size
                    if max(iw, ih) > _PRESCALE_THRESHOLD_PX:
                        res = (fc.get("output") or {}).get("resolution", "1080p")
                        tw, th = (1280, 720) if res == "720p" else (1920, 1080)
                        warning = (
                            f"💡 上传图片 {iw}×{ih} 较大，合成视频时会自动缩放至 "
                            f"{tw}×{th}（源文件保留原图）"
                        )
            except Exception:
                pass  # 损坏的图片 / PIL 异常 → 不警告，让后续渲染兜底
        return _ok(
            path=fc["materials"][kind]["path"],
            toast=f"{_FINE_MATERIAL_LABELS[kind][1]}已上传",
            warning=warning,
        )

    # REQ-20260919-063 用户反馈：每个素材都要可预览（图片/视频/音频/SRT 文本）。
    # 直接 GET 这个端点拿到素材文件本体（带正确 Content-Type），前端用 <img>/<video>/<audio>
    # 或 fetch() 文本即可。不经 Gradio 文件白名单（任务文件在 slirn-standalone/tasks/ 下）。
    @app.app.get("/slirn/api/fine_material_file")
    async def serve_fine_material_file(
        task_id: str, kind: str,
    ):
        """返回指定素材文件本体。前端 <img>/<video>/<audio> 直接 src= 此 URL。

        - 图片（cover/bg/reference）→ image/png|jpeg 等
        - 视频（video）→ video/mp4 等
        - 音频（audio）→ audio/mpeg|wav|mp4 等
        - 字幕（subtitle，.srt）→ text/plain；charset=utf-8
        """
        from fastapi.responses import FileResponse as _FR
        from fastapi import HTTPException as _HTTP

        if kind not in _FINE_MATERIAL_KINDS:
            raise _HTTP(400, f"非法素材类型: {kind}")
        try:
            t = mgr.get(task_id)
        except Exception:
            raise _HTTP(404, f"任务不存在: {task_id}")
        if not t:
            raise _HTTP(404, f"任务不存在: {task_id}")
        fc = _get_fine_compose(mgr, task_id)
        mat = fc.get("materials", {}).get(kind) or {}
        path_rel = mat.get("path")
        if not path_rel:
            raise _HTTP(404, f"任务 {task_id} 未上传 {kind}")
        abs_path = _resolve_mat_abs(mgr, task_id, fc["materials"], kind)
        if not abs_path or not abs_path.exists():
            raise _HTTP(404, f"素材文件不存在: {path_rel}")
        # 按扩展名选 Content-Type
        ext = abs_path.suffix.lower()
        _MT = {
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".webp": "image/webp", ".bmp": "image/bmp", ".gif": "image/gif",
            ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
            ".mkv": "video/x-matroska", ".avi": "video/x-msvideo",
            ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4",
            ".srt": "text/plain; charset=utf-8",
        }
        media_type = _MT.get(ext, "application/octet-stream")
        # 不让浏览器强缓存（任务素材可能被替换）
        return _FR(
            abs_path, media_type=media_type,
            headers={"Cache-Control": "no-cache"},
        )

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
        # ?src=fine_preview → 精剪视频前 10 秒预览（Phase B，REQ-20260919-061）
        # ?src=fine_export → 精剪视频完整导出（Phase B，REQ-20260919-061）
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
            elif src_q in ("fine_preview", "fine_export"):
                p = mgr.tasks_dir / tid / "outputs" / f"{src_q}.mp4"
                video = p if p.exists() else None
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
    async def gen_subtitle(request: Request, body: dict = Body(default_factory=dict)):
        # REQ-20260919-075：auto 透传 — pipeline_service 调时会带 X-Slirn-Auto header
        _auto = request.headers.get("X-Slirn-Auto") == "1"
        # REQ-20260920-081：session 透传 — 同一次自动流的多次操作共用 session_id
        _auto_session_id = request.headers.get("X-Slirn-Auto-Session") or ""
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
            auto=_auto,
            auto_session_id=_auto_session_id,
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
        """添加模型注册项：{id, provider, base_url, api_key_env, protocol?, vision?}。

        vision（REQ-20260919-061 用户补充）：是否支持图片输入，由用户在 UI 显式勾选。
        """
        from slirn_home import llm_config

        try:
            llm_config.add_model(
                repo_root,
                body.get("id", ""), body.get("provider", ""),
                body.get("base_url", ""), body.get("api_key_env", ""),
                body.get("protocol", "openai"),
                bool(body.get("vision", False)),
            )
        except llm_config.LLMConfigError as e:
            return _err(str(e))
        return _ok("", models=llm_config.list_models(repo_root),
                   current=llm_config.get_current(repo_root),
                   toast=f"✅ 已添加模型 {body.get('id', '')}")

    @app.app.post("/slirn/api/llm_config/update")
    async def llm_config_update(body: dict = Body(default_factory=dict)):
        """修改模型注册项：{id, new_id, provider, base_url, api_key_env, protocol?, vision?}。"""
        from slirn_home import llm_config

        try:
            llm_config.update_model(
                repo_root,
                str(body.get("id") or ""), body.get("new_id", ""),
                body.get("provider", ""), body.get("base_url", ""),
                body.get("api_key_env", ""), body.get("protocol", "openai"),
                bool(body.get("vision", False)),
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

    @app.app.post("/slirn/api/list_logs")
    async def list_logs(body: dict = Body(default_factory=dict)):
        """REQ-20260920-081：执行日志查询（按 task_id + 时间段 + 阶段 + 操作 + 模式过滤）。

        输入 body：
            task_id: 必填；查询该任务的 execution_history.json
            time_from / time_to: 可选 ISO 8601；按 started_at 区间过滤
            kinds: 可选操作类型列表（如 ["rough_compose", "rough_compose_delete"]）
            statuses: 可选状态列表（如 ["success", "failed"]）
            auto: 可选 "manual" | "auto" | "any"（默认 "any"）
            limit: 可选返回条数（默认 200）
        返回：
            {ok: true, items: [...], total: N}
        """
        tid = (body.get("task_id") or "").strip()
        if not tid:
            return _err("缺少 task_id")
        try:
            mgr.get(tid)
        except Exception as e:  # noqa: BLE001
            return _err(f"任务不存在: {e}")

        from slirn_home import execution_history

        # ISO 8601 → epoch 秒（带或不带时区都容错）
        def _parse_iso(s: str | None) -> float | None:
            if not s:
                return None
            try:
                from datetime import datetime as _dt
                s = str(s).strip()
                # 兼容带 Z 结尾（UTC）
                if s.endswith("Z"):
                    s = s[:-1] + "+00:00"
                return _dt.fromisoformat(s).timestamp()
            except Exception:
                return None

        time_from_ts = _parse_iso(body.get("time_from"))
        time_to_ts = _parse_iso(body.get("time_to"))
        kinds_in = body.get("kinds") or []
        statuses_in = body.get("statuses") or []
        auto_mode = (body.get("auto") or "any").lower()
        try:
            limit = int(body.get("limit") or 200)
        except (TypeError, ValueError):
            limit = 200
        limit = max(1, min(limit, 1000))

        # task_id 隔离：通过 outputs_dir（每个 task 独立目录）天然隔离
        outputs_dir = mgr.tasks_dir / tid / "outputs"
        items = execution_history.query_history(
            outputs_dir,
            kinds=list(kinds_in) if kinds_in else None,
            statuses=list(statuses_in) if statuses_in else None,
            limit=limit,
            time_from_ts=time_from_ts,
            time_to_ts=time_to_ts,
            auto=auto_mode,
        )
        return _ok("", items=items, total=len(items))

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
    async def build_cutlist(request: Request, body: dict = Body(default_factory=dict)):
        """生成切分修剪清单（REQ-20260916-008）：决策落盘 cutlist.json + 推进 ROUGH_CUT_DONE。"""
        import time as _time

        # REQ-20260919-075：auto 透传；执行切分修剪写日志
        _auto = request.headers.get("X-Slirn-Auto") == "1"
        # REQ-20260920-081：session 透传
        _auto_session_id = request.headers.get("X-Slirn-Auto-Session") or ""
        from slirn_home import asr_service, cutlist_service, execution_history, revision_service

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
        # REQ-20260919-075：开始记录 — 执行切分修剪
        exec_id = execution_history.record_start(
            outputs_dir, execution_history.KIND_ROUGH_CUT,
            auto=_auto,
            auto_session_id=_auto_session_id,
        )
        try:
            cutlist = cutlist_service.build_cutlist(sub_meta, rev)
            cutlist_service.save_cutlist(outputs_dir, cutlist)
        except Exception as e:  # noqa: BLE001
            execution_history.record_finish(outputs_dir, exec_id, success=False, error=str(e))
            return _err(f"生成切分清单失败: {e}")
        stats = cutlist.get("stats", {})
        try:
            mgr.update_status(tid, TaskStatus.ROUGH_CUT_DONE)
        except Exception as e:  # noqa: BLE001
            revision_service.log.warning("更新任务 %s 状态失败: %s", tid, e)
        saved_at = _time.strftime("%Y-%m-%dT%H:%M:%S")
        # REQ-20260919-075：完成时回填具体执行情况
        execution_history.patch_fields(outputs_dir, exec_id, {
            "description": (f"按切分决策生成切分清单：带入 {stats.get('brought', 0)} 段，"
                            f"剔除 {stats.get('dropped', 0)} 条，切分子段 {stats.get('split_subs', 0)}")
        })
        execution_history.record_finish(outputs_dir, exec_id, success=True, error="")
        return _ok("", toast=(
            f"✅ 切分清单已生成（带入 {stats.get('brought', 0)} 段 · 剔除 {stats.get('dropped', 0)} 条"
            f" · 切分子段 {stats.get('split_subs', 0)}）· 切分修剪完成，可进入精剪字幕"
        ), saved_at=saved_at, stats=stats)

    @app.app.post("/slirn/api/cut_speaker_link")
    async def cut_speaker_link(request: Request, body: dict = Body(default_factory=dict)):
        """切分修剪阶段关联人员ID（REQ-20260917-031）：按时间段重叠对齐 subtitle 段级 spk。

        服务端实时重建切分清单预览（与面板同口径：修订实时 + 已保存手工决策并入），
        返回每行人员编号 + 按人员统计（总数/执行口径保留数）。不落盘 — 再次点击
        即按最新时间窗重算；删除改判的落盘走既有 save_cut_decisions。
        """
        # REQ-20260919-075：auto 透传；关联人员ID写日志
        _auto = request.headers.get("X-Slirn-Auto") == "1"
        # REQ-20260920-081：session 透传
        _auto_session_id = request.headers.get("X-Slirn-Auto-Session") or ""
        from slirn_home import asr_service, cut_speaker, cutlist_service, execution_history, revision_service

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
        # REQ-20260919-075：开始记录 — 关联人员ID
        exec_id = execution_history.record_start(
            outputs_dir, execution_history.KIND_ROUGH_CUT_LINK_PERSON,
            extra={"rows": len(cutlist.get("items") or [])},
            auto=_auto,
            auto_session_id=_auto_session_id,
        )
        try:
            link = cut_speaker.link_speakers(sub_meta, cutlist)
            if not link.get("available"):
                execution_history.patch_fields(outputs_dir, exec_id, {
                    "description": "字幕无人员编号（生成字幕时未开启说话人分离），跳过关联"
                })
                execution_history.record_finish(outputs_dir, exec_id, success=True, error="")
                return _err(
                    "字幕无人员编号 — 该任务生成字幕时未开启说话人分离（或为旧任务）。"
                    "请到「字幕生成」阶段开启「区分说话人」重新生成后再关联"
                )
            # REQ-033：关联状态落盘 — 重进面板时徽章 + 统计条直接渲染（渲染端现算，快照备查）
            saved_link = cut_speaker.save_link(outputs_dir, link)
        except Exception as e:  # noqa: BLE001
            execution_history.record_finish(outputs_dir, exec_id, success=False, error=str(e))
            return _err(f"关联失败: {e}")
        # REQ-20260919-075：完成时回填具体执行情况
        # cut_speaker.link_speakers 返回 stats: [{spk, count}, ...] — 取 spk 集合的大小
        stats_list = link.get("stats") or []
        speakers_count = len({s.get("spk") for s in stats_list if s.get("spk") is not None})
        execution_history.patch_fields(outputs_dir, exec_id, {
            "description": (f"切分 {len(cutlist.get('items') or [])} 段关联到人员ID，"
                            f"识别 {speakers_count} 位说话人")
        })
        execution_history.record_finish(outputs_dir, exec_id, success=True, error="")
        return _ok("", link=link, rows=len(cutlist.get("items") or []),
                   linked_at=saved_link.get("linked_at"))

    # REQ-20260919-068：字幕修订阶段关联人员 ID（与切分修剪同口径）。
    # 修订阶段不强制 all_decided（用户可边决策边关联预览；落盘走 save_revision 路径）。
    @app.app.post("/slirn/api/rev_speaker_link")
    async def rev_speaker_link(body: dict = Body(default_factory=dict)):
        """字幕修订阶段关联人员ID（REQ-20260919-068）：按时间段重叠对齐 subtitle 段级 spk。

        返回每行人员编号 + 按人员统计（未删除决策的行数）。落盘
        rev_speaker_link.json — 重进面板时徽章 + 统计条直接渲染（渲染端现算，
        快照仅备查）。修订变化无需重算链接。
        """
        from slirn_home import asr_service, rev_speaker, revision_service

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
        sub_meta = asr_service.load_subtitle(outputs_dir)
        if not (sub_meta and sub_meta.get("segments")):
            return _err("缺少字幕生成产物（subtitle.json），请先在「字幕生成」阶段生成字幕")
        link = rev_speaker.link_speakers(sub_meta, rev)
        if not link.get("available"):
            return _err(
                "字幕无人员编号 — 该任务生成字幕时未开启说话人分离（或为旧任务）。"
                "请到「字幕生成」阶段开启「区分说话人」重新生成后再关联"
            )
        saved_link = rev_speaker.save_link(outputs_dir, link)
        return _ok("", link=link, rows=len(rev.get("entries") or []),
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
    async def compose_rough(request: Request, body: dict = Body(default_factory=dict)):
        """启动粗剪合成（REQ-20260916-018，必做阶段）：上游 VideoClipper 方法合成。

        口径与切分修剪面板一致（修订实时 + 已保存手工翻转/改判）；行文本取
        最新确认版（热词替换未撤销的行用 new_text，REQ-20260916-017）—
        「处理之后的字幕 + 原视频」交给上游合成方法（video_clip）。
        不推进任务状态。已在跑 → 返回 running 供前端接续轮询。

        REQ-20260920-081：auto_session_id 透传给 compose_service.start_compose。
        """
        # REQ-20260919-075：auto 透传
        _auto = request.headers.get("X-Slirn-Auto") == "1"
        # REQ-20260920-081：session 透传
        _auto_session_id = request.headers.get("X-Slirn-Auto-Session") or ""
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
            tid, video, intervals_ms, compose_service.rough_compose_path(outputs_dir), lines,
            auto=_auto, auto_session_id=_auto_session_id)
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
    async def compose_rough_delete(request: Request, body: dict = Body(default_factory=dict)):
        """删除粗剪成片（REQ-20260916-019）：用户主动清理产物以便重合成。

        REQ-20260920-081：auto_session_id 透传给 compose_service.delete_rough_compose。
        """
        # REQ-20260919-075：auto 透传
        _auto = request.headers.get("X-Slirn-Auto") == "1"
        # REQ-20260920-081：session 透传
        _auto_session_id = request.headers.get("X-Slirn-Auto-Session") or ""
        from slirn_home import compose_service

        tid = (body.get("task_id") or "").strip()
        if not tid:
            return _err("缺少 task_id")
        try:
            mgr.get(tid)
        except Exception as e:  # noqa: BLE001
            return _err(f"任务不存在: {e}")
        outputs_dir = mgr.tasks_dir / tid / "outputs"
        res = compose_service.delete_rough_compose(outputs_dir, auto=_auto,
                                                  auto_session_id=_auto_session_id)
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

    @app.app.post("/slirn/api/execution_history_query")
    async def execution_history_query_endpoint(body: dict = Body(default_factory=dict)):
        """REQ-20260918-053 — 执行日志查询（按阶段/状态过滤 + 关键词搜错误信息）。

        返回倒序最多 N 条（默认 200）；过滤条件全 AND：
        - kinds  (可选) list[str]  — 限定阶段（如 ["rough_compose","optimize"]）
        - statuses (可选) list[str] — 限定状态（running/success/failed）
        - keyword (可选) str        — 搜错误信息 / 阶段串（不区分大小写）
        - limit (可选) int          — 默认 200，上限 1000
        """
        from slirn_home import execution_history

        tid = (body.get("task_id") or "").strip()
        if not tid:
            return _err("缺少 task_id")
        try:
            mgr.get(tid)
        except Exception as e:  # noqa: BLE001
            return _err(f"任务不存在: {e}")
        kinds = body.get("kinds") or None
        statuses = body.get("statuses") or None
        keyword = body.get("keyword") or ""
        try:
            limit = int(body.get("limit") or 200)
        except Exception:  # noqa: BLE001
            limit = 200
        if limit < 1:
            limit = 1
        if limit > 1000:
            limit = 1000
        # 校验 kinds / statuses 是否合法（防止前端传错）
        if kinds is not None:
            kinds = [k for k in kinds if k in execution_history.ALL_KINDS]
        valid_statuses = {"running", "success", "failed"}
        if statuses is not None:
            statuses = [s for s in statuses if s in valid_statuses]
        outputs_dir = mgr.tasks_dir / tid / "outputs"
        items = execution_history.query_history(
            outputs_dir, kinds=kinds, statuses=statuses, keyword=keyword, limit=limit
        )
        return _ok("", items=items, total=len(items),
                   kinds=kinds or sorted(execution_history.ALL_KINDS))

    # ---------- 流程配置 + 自动执行（REQ-20260918-047）----------
    # 工作台顶部「⚙ 流程」按钮 → 抽屉编辑器 → 配置存 tasks/<tid>/outputs/pipeline.json
    # 后台守护线程（pipeline_service）按 STAGE_ORDER 顺序跑，每步调本组原 stage API
    # （/gen_subtitle、/revise_subtitle、/build_cutlist、/compose_rough、/optimize_subtitle）。
    # 任何阶段报错即停，错误信息写 pipeline.json history；手动按钮始终可用。
    from slirn_home import pipeline_service

    @app.app.post("/slirn/api/pipeline_get")
    async def pipeline_get(body: dict = Body(default_factory=dict)):
        """读取任务的 pipeline 配置 + 当前 job 状态 + 历史 summary。

        没写过 pipeline.json → 返回默认配置（AC-1）。
        """
        tid = (body.get("task_id") or "").strip()
        if not tid:
            return _err("缺少 task_id")
        try:
            mgr.get(tid)
        except Exception as e:  # noqa: BLE001
            return _err(f"任务不存在: {e}")
        outputs_dir = mgr.tasks_dir / tid / "outputs"
        data = pipeline_service.load_pipeline(outputs_dir)
        if data is None:
            # 还没写过 → 返回默认
            data = {
                "version": 1,
                "config": pipeline_service.default_config(),
                "updated_at": None,
                "history": [],
            }
        status = pipeline_service.pipeline_status(tid)
        return _ok("", config=data["config"], updated_at=data.get("updated_at"),
                   history=data.get("history") or [], status=status)

    @app.app.post("/slirn/api/pipeline_save")
    async def pipeline_save(body: dict = Body(default_factory=dict)):
        """保存 pipeline 配置；缺字段用默认值补全（容错）。"""
        tid = (body.get("task_id") or "").strip()
        if not tid:
            return _err("缺少 task_id")
        try:
            mgr.get(tid)
        except Exception as e:  # noqa: BLE001
            return _err(f"任务不存在: {e}")
        outputs_dir = mgr.tasks_dir / tid / "outputs"
        cfg = body.get("config") or {}
        ts = pipeline_service.save_pipeline(outputs_dir, cfg)
        return _ok("", saved_at=ts, toast="⚙️ 流程配置已保存")

    @app.app.post("/slirn/api/pipeline_run")
    async def pipeline_run(body: dict = Body(default_factory=dict)):
        """启动后台自动执行（守护线程）；已有 running job → False。"""
        tid = (body.get("task_id") or "").strip()
        if not tid:
            return _err("缺少 task_id")
        try:
            mgr.get(tid)
        except Exception as e:  # noqa: BLE001
            return _err(f"任务不存在: {e}")
        since = body.get("since") or None
        outputs_dir = mgr.tasks_dir / tid / "outputs"
        # base url：调度器走 in-process HTTP client（同进程同端口）。
        # 包含 /slirn/api 前缀 — pipeline_service._http_post(api, "/gen_subtitle")
        # 直接拼接 base + path，所以 base 必须含 API 前缀，否则 404。
        base_url = _os.environ.get("SLIRN_API_BASE") or ""
        if not base_url:
            try:
                port = getattr(app.app, "port", 7861)
                base_url = f"http://127.0.0.1:{port}/slirn/api"
            except Exception:  # noqa: BLE001 — 兜底走默认端口
                base_url = "http://127.0.0.1:7861/slirn/api"
        started = pipeline_service.run_pipeline(tid, base_url, outputs_dir, since=since)
        if not started:
            return _ok("", started=False,
                       toast="⏳ 流程已在运行 — 等待完成或先点 ⏹ 停止")
        return _ok("", started=True, toast="▶ 流程已启动（后台运行，可在状态条查看进度）")

    @app.app.post("/slirn/api/pipeline_status")
    async def pipeline_status_endpoint(body: dict = Body(default_factory=dict)):
        """读取当前 job 状态：state / current_stage / percent / log / history。"""
        tid = (body.get("task_id") or "").strip()
        if not tid:
            return _err("缺少 task_id")
        try:
            mgr.get(tid)
        except Exception as e:  # noqa: BLE001
            return _err(f"任务不存在: {e}")
        status = pipeline_service.pipeline_status(tid)
        # 同步补一次 history（落盘数据，状态条初始显示用）
        outputs_dir = mgr.tasks_dir / tid / "outputs"
        disk_data = pipeline_service.load_pipeline(outputs_dir)
        disk_history = (disk_data or {}).get("history") or []
        if status is None:
            # 从未跑过 → state=idle
            return _ok("", state="idle", current_stage=None, percent=0.0,
                       log=[], error=None, history=disk_history)
        return _ok("", **status, history=status.get("history") or disk_history)

    @app.app.post("/slirn/api/pipeline_stop")
    async def pipeline_stop(body: dict = Body(default_factory=dict)):
        """请求停止（设置标志位，下次循环检查时退出）。无 running job → False。"""
        tid = (body.get("task_id") or "").strip()
        if not tid:
            return _err("缺少 task_id")
        stopped = pipeline_service.stop_pipeline(tid)
        if not stopped:
            return _ok("", stopped=False, toast="没有正在运行的流程")
        return _ok("", stopped=True, toast="⏹ 已请求停止（当前阶段完成后退出）")

    @app.app.post("/slirn/api/optimize_subtitle")
    async def optimize_subtitle(request: Request, body: dict = Body(default_factory=dict)):
        """启动优化字幕（REQ-20260917-030）：ASR 重识别粗剪成片 → 大模型提取不明确字词。

        已有优化结果时须 force（前端二次确认）。热词为空不阻塞（提示级）。
        """
        # REQ-20260919-075：auto 透传
        _auto = request.headers.get("X-Slirn-Auto") == "1"
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
            auto=_auto,
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
    async def save_optimize_subtitle(request: Request, body: dict = Body(default_factory=dict)):
        """保存优化字幕人工替换决定（REQ-20260917-030）：决定合并落盘 + 阶段完成。

        decisions：[{occ_id, applied, after}] 全量口径 — 未列出的出现项一律不
        采纳；after 可为人工编辑值（生效要求非空且 ≠ 原文）。幂等推进
        FINE_SUBTITLE_REVIEWED。

        REQ-20260919-075：「确认保存」动作也写执行日志（之前只有大模型分析记录）。
        """
        # REQ-20260919-075：auto 透传；确认保存写日志
        _auto = request.headers.get("X-Slirn-Auto") == "1"
        # REQ-20260920-081：session 透传
        _auto_session_id = request.headers.get("X-Slirn-Auto-Session") or ""
        from tasklib.models import TaskStatus

        from slirn_home import execution_history, optimize_service

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
        # REQ-20260919-075：开始记录 — 确认保存
        exec_id = execution_history.record_start(
            outputs_dir, execution_history.KIND_OPTIMIZE,
            extra={"decisions_count": len(decisions)},
            auto=_auto,
            auto_session_id=_auto_session_id,
        )
        try:
            data, applied_n = optimize_service.save_decisions(outputs_dir, decisions)
        except Exception as e:  # noqa: BLE001
            execution_history.record_finish(outputs_dir, exec_id, success=False, error=str(e))
            return _err(f"保存失败: {e}")
        est = optimize_service.effective_stats(data)
        mgr.update_status(tid, TaskStatus.FINE_SUBTITLE_REVIEWED)  # 幂等：结果在盘即完成
        # REQ-20260919-075：完成时回填具体执行情况
        execution_history.patch_fields(outputs_dir, exec_id, {
            "description": (f"优化字幕保存：{len(decisions)} 条人工决定，生效 {applied_n} 处"
                            f"（未采纳 {est.get('skipped', 0)} 处）")
        })
        execution_history.record_finish(outputs_dir, exec_id, success=True, error="")
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
