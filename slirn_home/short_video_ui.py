"""短视频单源 AI 拆条工作台的服务端 HTML 渲染。

REQ-20261003-098：6 阶段管线（选源 → 抽字幕 → 解析短视频内容 → 粗剪 → 字幕优化 → 精剪混编）。
REQ-20261012-099：Stage 3 由「AI 拆条」改名并改版 —— 用户录入解析提示词（默认 +
最新 10 条历史两级复用）、可选上传截图一起发给多模态 AI；每个片段可单独重新解析。
修订（验收反馈）：移除快速模板 UI；历史条目可展开查看完整内容、⭐ 标默认；
截图区块标题改为「智能纪要课程重点截图」；保存提示词后历史下拉原地自动刷新。
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from urllib.parse import quote

from .short_video_service import (
    STAGE3_SYSTEM_PROMPT,
    STAGE4_SYSTEM_PROMPT,
    stage3_system_prompt_template,
)
from . import short_video_optimize as _sv_opt


def esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _file_url(task_id: str, project_id: str, kind: str, name: str) -> str:
    return (
        "/slirn/api/short_video_file?"
        f"task_id={quote(str(task_id))}&project_id={quote(str(project_id))}"
        f"&kind={quote(kind)}&name={quote(name)}"
    )


def _source_video_url(project: dict) -> str:
    """当前选源视频的可访问 URL；未选源返回空串。"""
    task_id = str(project.get("task_id") or "")
    project_id = str(project.get("id") or "")
    materials = project.get("materials") or []
    base_id = project.get("base_material_id") or ""
    src = next(
        (m for m in materials if m.get("id") == base_id and m.get("kind") == "video"),
        None,
    )
    if not src:
        return ""
    return _file_url(task_id, project_id, "material", str(src.get("id") or ""))


def _duration_str(seconds: float | None) -> str:
    if not seconds or seconds <= 0:
        return ""
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


def _format_ms(ms: int) -> str:
    if ms <= 0:
        return "00:00"
    s = ms / 1000.0
    return _duration_str(s)


def render_prompt_history_list(prompt_settings: dict) -> str:
    """渲染全局提示词历史列表（REQ-099 修订：可展开查看完整内容 + 标出默认）。

    既用于 Stage 3 面板初始渲染，也作为 ``short_video_save_default_prompt`` /
    ``short_video_prompt_history_save`` 端点的 ``history_html`` 片段返回 —— 前端
    保存后原地替换 ``.slirn-stage3-hist-list``，实现「存入列表后下拉自动刷新」。
    """
    default_prompt = str(((prompt_settings.get("default_prompt") or {}).get("prompt")) or "")
    items = []
    for item in prompt_settings.get("history") or []:
        p = str(item.get("prompt") or "")
        if not p.strip():
            continue
        head = p.strip().replace("\n", " ")
        head = head[:80] + ("…" if len(head) > 80 else "")
        when = str(item.get("updated_at") or "")[:16].replace("T", " ")
        star = (
            '<span class="slirn-sv-badge slirn-sv-badge-ok slirn-stage3-hist-default">⭐ 默认</span>'
            if default_prompt and p.strip() == default_prompt.strip() else ""
        )
        items.append(
            f'<div class="slirn-stage3-hist-item">'
            f'<div class="slirn-stage3-hist-head">'
            f'<button type="button" class="slirn-stage3-hist-toggle" '
            f'data-action="sv-stage3-toggle-hist" title="展开 / 收起完整内容">{esc(head)}{star}'
            f'<span class="slirn-sv-muted slirn-stage3-hist-time">{esc(when)}</span></button>'
            f'<button type="button" class="slirn-btn-mini" data-action="sv-stage3-load-prompt" '
            f'data-prompt="{esc(p)}" title="载入到提示词文本域">载入</button>'
            f'</div>'
            f'<pre class="slirn-stage3-hist-full" hidden>{esc(p)}</pre>'
            f'</div>'
        )
    return "".join(items) or '<div class="slirn-sv-muted">尚无保存的提示词</div>'


# ---------- 项目列表（保持兼容） ----------

def render_project_list(
    tasks: list[dict],
    projects: list[dict],
    scope: str = "all",
    is_admin: bool = False,
) -> str:
    """短视频项目列表 HTML。

    REQ-20261012-099 修订四：对齐长视频列表 —
    - scope radio（我的任务/全部任务，仅 admin；普通用户固定「我的」由后端钳制）
    - 按短视频任务名（项目名）搜索（前端 client-side 过滤）
    - 「➕ 新建短视频任务」按钮从 hero 挪入列表头部
    """
    task_options = "".join(
        f'<option value="{esc(t.get("task_id"))}">{esc(t.get("task_id"))} · {esc(t.get("name"))}</option>'
        for t in tasks
    )
    cards = []
    for p in projects:
        kind = str(p.get("kind") or "mixcut")
        kind_label = "AI 拆条" if kind == "split" else "多素材混剪（旧）"
        cards.append(
            f'<div class="slirn-sv-card" data-task-id="{esc(p.get("task_id"))}" '
            f'data-project-id="{esc(p.get("id"))}">'
            f'<div class="slirn-sv-card-head"><strong>{esc(p.get("name"))}</strong>'
            f'<span class="slirn-sv-badge">{esc(kind_label)}</span></div>'
            f'<div class="slirn-sv-muted">任务 {esc(p.get("task_id"))} · 素材 {len(p.get("materials") or [])}</div>'
            f'<div class="slirn-sv-card-actions">'
            f'<button class="slirn-btn slirn-btn-primary" data-action="sv-open" '
            f'data-task-id="{esc(p.get("task_id"))}" data-project-id="{esc(p.get("id"))}">打开</button>'
            f'<button class="slirn-btn slirn-btn-danger" data-action="sv-delete" '
            f'data-task-id="{esc(p.get("task_id"))}" data-project-id="{esc(p.get("id"))}">删除</button>'
            f'</div></div>'
        )
    if not cards:
        cards.append('<div class="slirn-empty"><div class="slirn-empty-text">还没有短视频项目</div></div>')
    # scope radio（admin 才显示「我的任务/全部任务」切换；普通用户固定「我的」）
    scope_html = (
        '<div class="slirn-scope-toggle">'
        '<label><input type="radio" name="slirn-sv-scope" value="mine"'
        + (' checked' if scope == "mine" else '') + '> 我的任务</label>'
        '<label><input type="radio" name="slirn-sv-scope" value="all"'
        + (' checked' if scope != "mine" else '') + '> 全部任务</label>'
        '</div>'
    ) if is_admin else ''
    return f'''<div id="slirn-short-video-inner" class="slirn-tab-inner">
  <div class="slirn-sv-header">
    <div><h2>单源 AI 短视频内容拆条</h2><p>从一段完整视频出发，AI 通过字幕重组+重排序产生 3-5 个独立短视频。</p></div>
    <div class="slirn-sv-header-actions">
      <button class="slirn-btn slirn-btn-primary" data-action="sv-new">➕ 新建短视频任务</button>
      <button class="slirn-btn" data-action="sv-refresh">刷新</button>
    </div>
  </div>
  <div class="slirn-sv-create" id="slirn-sv-create">
    <div class="slirn-sv-section-title">新建短视频任务</div>
    <div class="slirn-sv-form-grid">
      <label>来源任务<select id="slirn-sv-create-task">{task_options}</select></label>
      <label>项目名<input id="slirn-sv-create-name" placeholder="例如：公开课拆条"></label>
      <button class="slirn-btn slirn-btn-primary slirn-sv-create-btn" data-action="sv-create">创建项目</button>
    </div>
  </div>
  <div class="slirn-toolbar">
    {scope_html}
    <div class="slirn-search-wrap">
      <input class="slirn-search-box" id="slirn-sv-search" placeholder="搜索短视频任务名 / 任务 ID..." />
    </div>
  </div>
  <div class="slirn-sv-section-title">项目列表</div>
  <div class="slirn-sv-grid">{"".join(cards)}</div>
</div>'''


# ---------- 6 阶段管线详情页 ----------

def _kind_label(kind: object) -> str:
    return {"video": "视频", "image": "图片", "audio": "音频", "bg_image": "背景图片", "file": "文件"}.get(
        str(kind or ""), str(kind or "素材"),
    )


def _stage_badge(status: str | None) -> str:
    """阶段状态徽章（pending / running / done / failed）。"""
    s = str(status or "pending").lower()
    label = {"pending": "○ 未开始", "running": "⏳ 处理中", "done": "✓ 完成",
             "failed": "✗ 失败", "running_asr": "⏳ ASR 中", "user_editing": "✎ 待修正",
             "confirmed": "✓ 已确认"}.get(s, s)
    return f'<span class="slirn-stage-badge slirn-stage-{s}">{esc(label)}</span>'


def _stage_elapsed(state: dict) -> str:
    """渲染阶段已用时（仅 running/running_asr 时显示）。

    REQ-20261004-UX：用户希望在「处理中」状态看到已经执行多长时间。
    起始时间戳在 ``state["started_at"]``（ISO）；客户端每秒刷新一次文本。
    """
    status = str(state.get("status") or "").lower()
    if status not in ("running", "running_asr"):
        return ""
    started = str(state.get("started_at") or "")
    if not started:
        return ""
    return (
        f'<span class="slirn-stage-elapsed" '
        f'data-stage-elapsed data-started-at="{esc(started)}">0:00</span>'
    )


def _stage_logs(state: dict) -> str:
    """渲染阶段执行日志（可折叠 details）。

    REQ-20261004-UX：每个阶段记录自身操作过程（启动/完成/失败/警告），用户点击展开查看。
    日志为空时不显示面板。
    """
    logs = state.get("logs") or []
    if not logs:
        return ""
    rows = "".join(
        f'<div class="slirn-stage-log-row">{esc(line)}</div>' for line in logs
    )
    return (
        f'<details class="slirn-stage-logs"><summary>执行日志（{len(logs)} 条）</summary>'
        f'<div class="slirn-stage-log-body">{rows}</div></details>'
    )


# REQ-20261013-110：Stage 4 优化详情 + 警告的折叠面板。
# 与 _stage_logs 同款 <details> shell，但 body 走浅色（不混入 dark monospace，
# 因为 stats 行的 badge 在暗色底上会失真）。
def _stage4_detail(detail_html: str, warn_html: str) -> str:
    body = (detail_html or "") + (warn_html or "")
    if not body.strip():
        return ""
    return (
        f'<details class="slirn-stage4-detail">'
        f'<summary>📊 优化详情与警告</summary>'
        f'<div class="slirn-stage4-detail-body">{body}</div>'
        f'</details>'
    )


# REQ-20261013-110：毫秒 → SRT 格式 00:00:00,000（项目惯例 2 位小时）。
# 与 slirn_home.short_video_coarse._fmt_srt_time / compose_service._ms_to_srt_time 同款。
# 负数 / None 钳到 0（防御性）。
def _fmt_srt_time(ms: int | float | None) -> str:
    ms_i = max(0, int(ms or 0))
    h, rem = divmod(ms_i, 3600 * 1000)
    m, rem = divmod(rem, 60 * 1000)
    s, msec = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{msec:03d}"



def _material_options(materials: list[dict], kind: str, selected: str = "",
                       empty_label: str = "（无可用素材）") -> str:
    rows = []
    for m in materials:
        if m.get("kind") != kind:
            continue
        value = str(m.get("id") or "")
        label = f'{m.get("name")} · {_kind_label(m.get("kind"))}'
        sel = " selected" if value == selected else ""
        rows.append(f'<option value="{esc(value)}"{sel}>{esc(label)}</option>')
    if not rows:
        rows.append(f'<option value="">{esc(empty_label)}</option>')
    return "".join(rows)


def _stage1_select_source(project: dict) -> str:
    task_id = str(project.get("task_id") or "")
    project_id = str(project.get("id") or "")
    base_id = str(project.get("base_material_id") or "")
    s1 = project.get("pipeline", {}).get("stage1_source", {}) or {}
    materials = project.get("materials") or []
    options = _material_options(materials, "video", base_id, empty_label="（请先上传视频素材）")
    src_material = next((m for m in materials if m.get("id") == base_id and m.get("kind") == "video"), None)
    src_preview = ""
    if src_material:
        url = _file_url(task_id, project_id, "material", str(src_material.get("id") or ""))
        src_preview = (
            f'<div class="slirn-stage-source-preview">'
            f'<video controls preload="metadata" src="{esc(url)}" '
            f'data-action="sv-preview" data-url="{esc(url)}" data-kind="video" '
            f'data-name="{esc(src_material.get("name") or "")}"></video>'
            f'<div class="slirn-stage-source-meta">'
            f'<strong>{esc(src_material.get("name") or "")}</strong>'
            f'<span>· {esc(str(src_material.get("duration") or ""))}s · '
            f'{esc(str(src_material.get("width") or ""))}x{esc(str(src_material.get("height") or ""))}</span>'
            f'</div></div>'
        )
    # REQ-20261003-098 + REQ-20261001-095/096/097：素材库复用（UI 改用于源视频选择）
    material_cards = []
    for m in materials:
        url = _file_url(task_id, project_id, "material", str(m.get("id") or ""))
        kind = str(m.get("kind") or "file")
        source_label = "任务导入" if m.get("source") == "auto" else "上传"
        material_cards.append(
            f'<div class="slirn-sv-material">'
            f'<span class="slirn-sv-kind-badge slirn-sv-kind-{esc(kind)}">{esc(_kind_label(kind))}</span>'
            f'<strong class="slirn-sv-material-name" title="{esc(m.get("name"))}">{esc(m.get("name"))}</strong>'
            f'<span class="slirn-sv-material-meta">{esc(source_label)}</span>'
            f'<div class="slirn-sv-material-actions">'
            f'<button class="slirn-btn-mini" data-action="sv-preview" data-url="{esc(url)}" '
            f'data-kind="{esc(kind)}" data-name="{esc(m.get("name"))}">查看</button>'
            f'<button class="slirn-btn-mini slirn-btn-mini-danger" data-action="sv-remove-material" '
            f'data-material-id="{esc(m.get("id"))}">删</button>'
            f'</div>'
            f'</div>'
        )
    upload_section = (
        f'<div class="slirn-stage-body"><div class="slirn-sv-form-grid">'
        f'<label>上传新视频素材（加入素材库）<input type="file" id="slirn-sv-upload-file" accept="video/*"></label>'
        f'</div>'
        f'<div class="slirn-sv-card-actions"><button class="slirn-btn" data-action="sv-upload">上传</button></div></div>'
    )
    materials_block = (
        '<div class="slirn-sv-materials">'
        + ("".join(material_cards) or '<span class="slirn-sv-muted">暂无素材</span>')
        + '</div>'
    )
    return f'''<div class="slirn-stage" data-stage="1">
  <div class="slirn-stage-head">
    <span class="slirn-stage-num">1</span>
    <strong>选源视频</strong>
    {_stage_badge(s1.get("status"))}
  </div>
  <div class="slirn-stage-body">
    <div class="slirn-sv-form-grid">
      <label class="slirn-sv-wide">从素材库选一个视频作为拆条源
        <select id="slirn-stage1-source">{options}</select>
      </label>
    </div>
    <button class="slirn-btn slirn-btn-primary" data-action="sv-stage1-select">选为源视频</button>
    {src_preview}
    <details style="margin-top:10px"><summary>素材库（{len(materials)} 个）</summary>
      {upload_section}
      {materials_block}
    </details>
  </div>
</div>'''


def _stage2_extract(project: dict) -> str:
    task_id = str(project.get("task_id") or "")
    project_id = str(project.get("id") or "")
    s2 = project.get("pipeline", {}).get("stage2_extract", {}) or {}
    srt_path = s2.get("srt_path") or ""
    srt_url = _file_url(task_id, project_id, "stage2", "raw.srt") if s2.get("status") == "done" else ""
    srt_chars = int(s2.get("srt_chars") or 0)
    error_html = ""
    if s2.get("status") == "failed":
        err = (s2.get("error") or "失败") + " · " + (s2.get("stderr_tail") or "")[-200:]
        error_html = f'<div class="slirn-stage-error">{esc(err)}</div>'
    preview_btn = (
        f'<button class="slirn-btn-mini" data-action="sv-stage2-preview-srt" '
        f'data-url="{esc(srt_url)}">查看 raw.srt</button>'
        if srt_url else ""
    )
    meta = f'<span class="slirn-sv-muted">{srt_chars} 字</span>' if srt_chars else ""
    # REQ-20261015-123：热词随 Stage 2 运行落盘，重渲染/刷新后回填输入框。
    hotwords_v = esc(s2.get("hotwords") or "")
    return f'''<div class="slirn-stage" data-stage="2">
  <div class="slirn-stage-head">
    <span class="slirn-stage-num">2</span>
    <strong>提取字幕</strong>
    {_stage_badge(s2.get("status"))}
    {_stage_elapsed(s2)}
    {meta}
  </div>
  <div class="slirn-stage-body">
    <div class="slirn-sv-form-grid">
      <label class="slirn-sv-wide">热词（可选）<input id="slirn-stage2-hotwords" value="{hotwords_v}" placeholder="半角逗号分隔，提升专有名词识别"></label>
    </div>
    <div class="slirn-sv-card-actions">
      <button class="slirn-btn slirn-btn-primary" data-action="sv-stage2-run">提取字幕</button>
      {preview_btn}
    </div>
    {error_html}
    {_stage_logs(s2)}
  </div>
</div>'''


def _stage3_analyze(project: dict, source_url: str = "", srt_lines: list | None = None) -> str:
    s3 = project.get("pipeline", {}).get("stage3_analyze", {}) or {}
    n = int(s3.get("n_clips") or 4)
    # REQ-20261015-120：时长区间输入初值 = 上次运行的生效区间（缺省 60-90）
    try:
        _dr = s3.get("duration_range") or []
        dmin_v = int(float(_dr[0]))
        dmax_v = int(float(_dr[1]))
    except (TypeError, ValueError, IndexError):
        dmin_v, dmax_v = 60, 90
    dmin_v = min(max(dmin_v, 10), 600)
    dmax_v = min(max(dmax_v, dmin_v + 5), 900)
    template = str(s3.get("template") or "hook_first")
    prompt_state = s3.get("prompt_state") or {}
    custom_prompts = prompt_state.get("custom") or {}
    default_prompts = prompt_state.get("defaults") or {}

    # REQ-20261012-099 修订：快速模板 UI 已移除；模板仅作为提示词文本域的
    # 初始填充兜底（全局默认优先，历史自定义覆盖仍优先于系统默认文本）。
    def _tpl_effective_text(key: str) -> str:
        custom_entry = custom_prompts.get(key) or {}
        return str(custom_entry.get("prompt") or default_prompts.get(key) or "")

    # REQ-20261012-099：提示词文本域初始值 — 全局默认提示词 → 当前模板生效文本
    prompt_settings = s3.get("prompt_settings") or {}
    global_default = prompt_settings.get("default_prompt") or {}
    initial_prompt = str(global_default.get("prompt") or "") or _tpl_effective_text(template)
    default_badge = (
        '<span class="slirn-sv-badge slirn-sv-badge-muted">初始已填默认提示词</span>'
        if global_default.get("prompt") else ""
    )

    # 历史提示词列表（≤ 10 条，最新在前；⭐ 标默认；条目可展开查看完整内容）
    hist_html = render_prompt_history_list(prompt_settings)

    # REQ-20261012-099：截图（上传 / 缩略图 / 移除 / 发送开关）
    cur_image = s3.get("image") or {}
    img_name = str(cur_image.get("name") or "")
    task_id = str(project.get("task_id") or "")
    project_id = str(project.get("id") or "")
    if img_name:
        img_url = _file_url(task_id, project_id, "stage3", img_name)
        image_area = (
            f'<div class="slirn-stage3-image-row">'
            f'<a href="{esc(img_url)}" target="_blank" rel="noopener" title="查看大图">'
            f'<img class="slirn-stage3-image-thumb" src="{esc(img_url)}" alt="解析截图"></a>'
            f'<label class="slirn-stage3-image-send">'
            f'<input type="checkbox" id="slirn-stage3-use-image" checked> 发送截图给 AI</label>'
            f'<button class="slirn-btn-mini slirn-btn-mini-danger" '
            f'data-action="sv-stage3-remove-image">✖ 移除截图</button>'
            f'</div>'
        )
    else:
        image_area = (
            '<div class="slirn-stage3-image-row">'
            '<span class="slirn-sv-muted">未上传截图（可选：上传后解析时先提取文字作为参考方向）</span>'
            '</div>'
        )

    # 修订六：截图 OCR 提取结果提示（提取到的文字已作为参考方向拼进提示词）
    ocr_info = s3.get("ocr") or {}
    ocr_note = ""
    if ocr_info.get("source_image"):
        ocr_chars = int(ocr_info.get("chars") or 0)
        ocr_cached = "（缓存复用）" if ocr_info.get("cached") else ""
        ocr_note = (
            f'<div class="slirn-stage3-ocr-note">'
            f'<span class="slirn-sv-badge slirn-sv-badge-ok">✅ 截图文字已提取{ocr_cached}</span>'
            f'<span class="slirn-sv-muted">{ocr_chars} 字 · 作为参考方向拼入提示词</span>'
            f'<button class="slirn-btn-mini" data-action="sv-preview-srt" '
            f'data-url="{_file_url(task_id, project_id, "stage3", "ocr_text.txt")}" '
            f'title="查看从截图提取出的全部文字">📖 查看提取文字</button>'
            f'</div>'
        )

    error_html = ""
    if s3.get("status") == "failed":
        err = s3.get("error") or "失败"
        error_html = f'<div class="slirn-stage-error">{esc(err)}</div>'

    # REQ-20261004-stage3-restructure：把 srt_lines 转成 {idx → (start_ms, end_ms)} 查表，
    # 字幕行 UI 改显示 [HH:MM:SS] text + drag handle
    srt_lines = srt_lines or []
    srt_index_to_time: dict[int, tuple[int, int]] = {}
    for ln in srt_lines:
        try:
            idx = int(ln.get("index") or 0)
            sm = int(ln.get("start_ms") or 0)
            em = int(ln.get("end_ms") or 0)
        except (TypeError, ValueError):
            continue
        if idx > 0:
            srt_index_to_time[idx] = (sm, em)

    def _ts(ms: int) -> str:
        # 修订七（用户验收）：行内时间戳要完整 hh:mm:ss,ms（与 LLM 输出/SRT
        # 时间格式一致，长视频 mm:ss 会看不出在哪个小时）
        ms = max(0, int(ms))
        h, rem = divmod(ms, 3_600_000)
        m, rem = divmod(rem, 60_000)
        s, milli = divmod(rem, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"

    # 高亮条目可编辑标题 + 删除 + 完整字幕预览（REQ-20261004-UX：用户要直接看到全部文字）
    # 修订六：一组 = 一条短视频（围绕一个标题，字幕合计约 70s）；组内每行带
    # 唯一编号 no + 时间戳（拖拽重排后编号随行移动不变，「还原顺序」按 no 升序）
    hl_rows = []
    # 修订九：N 条短视频逐条页签（点页签切换，不再整页长滚动）；首签默认选中
    hl_tabs = []
    for hl in s3.get("highlights") or []:
        idx = hl.get("index") or len(hl_rows) + 1
        title = hl.get("title") or f"短视频 #{idx}"
        is_first = len(hl_rows) == 0
        _tab_label = title if len(title) <= 10 else title[:10] + "…"
        hl_tabs.append(
            f'<button class="slirn-stage3-hl-tab{" active" if is_first else ""}" '
            f'data-action="sv-stage3-hl-tab" data-hl-index="{idx}" '
            f'title="{esc(title)}">#{idx} {esc(_tab_label)}</button>'
        )
        start = hl.get("start_ms") or 0
        end = hl.get("end_ms") or 0
        sub_lines = hl.get("subtitle_lines") or []
        sub_count = len(sub_lines)
        # 字幕合计时长（修订六验收：每条约 70 秒）——行级时间戳优先，缺失查源行
        total_ms = 0
        nos: list[int] = []
        for pos, sl in enumerate(sub_lines, start=1):
            no = int(sl.get("no") or pos)
            nos.append(no)
            times = None
            try:
                _s = int(sl.get("start_ms") or 0)
                _e = int(sl.get("end_ms") or 0)
                if _e > _s:
                    times = (_s, _e)
            except (TypeError, ValueError):
                times = None
            if times is None:
                times = srt_index_to_time.get(int(sl.get("src_index") or 0))
            if times:
                total_ms += max(0, times[1] - times[0])
        full_text = "\n".join(
            f"[{int(sl.get('src_index') or 0)}] {str(sl.get('text') or '')}"
            for sl in sub_lines
        )
        preview_items = []
        # 修订八：预览要按各字幕行的时间戳**逐段**截取播放（与拼接产物一致），
        # 不再从组起点连播到组终点。窗口 = 行时间戳 + 100ms 防切词尾（同拼接）。
        seg_times: list[tuple[int, int]] = []
        for pos, sl in enumerate(sub_lines, start=1):
            t = esc(str(sl.get("text") or ""))
            src = int(sl.get("src_index") or 0)
            no = int(sl.get("no") or pos)
            try:
                _s = int(sl.get("start_ms") or 0)
                _e = int(sl.get("end_ms") or 0)
                times = (_s, _e) if _e > _s else srt_index_to_time.get(src)
            except (TypeError, ValueError):
                times = srt_index_to_time.get(src)
            if times:
                seg_times.append((times[0], times[1] + 100))
                # 修订七：行格式 = 编号. [hh:mm:ss,ms → hh:mm:ss,ms] 字幕文本（带方括号）
                range_html = f'[{_ts(times[0])} → {_ts(times[1])}]'
                tip = f"源字幕 #{src} @ {_ts(times[0])}-{_ts(times[1])}"
            else:
                range_html = "[??:??:??,???]"
                tip = f"源字幕 #{src}"
            preview_items.append(
                f'<div class="slirn-stage3-hl-sub" draggable="true" '
                f'data-hl-index="{idx}" data-src-index="{src}" title="{esc(tip)}">'
                f'<span class="slirn-stage3-hl-drag" aria-hidden="true">⋮⋮</span>'
                f'<span class="slirn-stage3-hl-no">{no}.</span>'
                f'<span class="slirn-stage3-hl-ts">{range_html}</span>'
                f'<span class="slirn-stage3-hl-text">{t}</span>'
                f'</div>'
            )
        sub_preview = "".join(preview_items) or '<div class="slirn-sv-muted">无字幕（拖拽排序需先有字幕）</div>'
        # 修订八：data-segments 按当前行序给出逐段切割窗（毫秒，已含 +100ms
        # 词尾余量，与 run_stage3_splice 完全一致）——悬浮预览逐段 seek 播放
        segs_attr = (
            f' data-segments="{esc(json.dumps(seg_times))}"' if seg_times else ""
        )
        # 源视频未选 → 预览按钮禁用（避免点了没反应）
        preview_btn = (
            f'<button class="slirn-btn-mini slirn-btn-mini-primary" '
            f'data-action="sv-stage3-preview" '
            f'data-src-url="{esc(source_url)}" '
            f'data-start-ms="{int(start)}" data-end-ms="{int(end)}"{segs_attr} '
            f'data-title="{esc(title)}" '
            f'title="按各字幕行时间戳逐段截取预览（浏览器 seek，无需生成 mp4）">'
            f'🎬 预览片段</button>'
            if source_url else
            '<button class="slirn-btn-mini" disabled title="请先在 Stage 1 选源视频">🎬 预览片段</button>'
        )
        # 还原顺序：当前行序 == 初始编号序（no 升序）时无需还原 → 禁用
        is_original = nos == sorted(nos)
        restore_btn = (
            f'<button class="slirn-btn-mini" data-action="sv-stage3-restore-order" '
            f'data-hl-index="{idx}" '
            f'title="把该条短视频的字幕行还原为大模型最初给出的顺序（编号不变）">'
            f'↩️ 还原顺序</button>'
            if not is_original else
            f'<button class="slirn-btn-mini" data-action="sv-stage3-restore-order" '
            f'data-hl-index="{idx}" disabled title="当前已是大模型最初顺序">↩️ 还原顺序</button>'
        )
        # REQ-20261015-120：合计时长越出当前区间 → ⚠ 标记（区间来自上次运行）
        total_sec = total_ms / 1000.0
        dur_flag = (
            f' ⚠️ 未达 {dmin_v}s' if total_sec < dmin_v
            else (f' ⚠️ 超 {dmax_v}s' if total_sec > dmax_v else "")
        )
        hl_rows.append(
            # 修订九：每条短视频包一层页签面板（tab bar 在列表头部），点页签切换显示
            f'<div class="slirn-stage3-hl-pane{" active" if is_first else ""}" data-hl-index="{idx}">'
            f'<div class="slirn-stage3-hl-row" data-hl-index="{idx}" data-sub-text="{esc(full_text)}">'
            f'<div class="slirn-stage3-hl-head">'
            f'<span class="slirn-sv-row-no">#{idx}</span>'
            f'<input type="text" class="slirn-stage3-hl-title" data-hl-field="title" value="{esc(title)}" placeholder="标题">'
            f'<span class="slirn-sv-muted slirn-stage3-hl-meta">{_format_ms(start)} - {_format_ms(end)} · 字幕合计 {total_sec:.0f}s{dur_flag} · {sub_count} 行</span>'
            f'{preview_btn}'
            f'{restore_btn}'
            f'<button class="slirn-btn-mini" data-action="sv-stage3-rerun" data-hl-index="{idx}" '
            f'title="针对该条的标题/主题，用当前提示词+截图参考单独重新解析（时间段与字幕内容会替换）">'
            f'🔄 重新解析</button>'
            f'<button class="slirn-btn-mini slirn-btn-mini-danger" data-action="sv-stage3-remove-hl" '
            f'data-hl-index="{idx}" title="删除这条短视频（需确认）">删除</button>'
            f'</div>'
            f'<div class="slirn-stage3-hl-preview" data-hl-index="{idx}">{sub_preview}</div>'
            f'<div class="slirn-sv-muted slirn-stage3-hl-hint">⋮⋮ 拖动调整本条短视频内字幕的先后顺序（行首编号保持不变，拼接按当前行序）；↩️ 还原顺序 = 恢复大模型最初给出的顺序</div>'
            f'</div>'
            f'</div>'
        )
    hl_list = (
        '<div class="slirn-stage3-hl-tabs">' + "".join(hl_tabs) + '</div>' + "".join(hl_rows)
        if hl_rows else
        '<div class="slirn-sv-muted">解析后将列出各条短视频</div>'
    )

    # REQ-20261013-111：Stage 3 解析进度卡（仿 stage5/7 status-card 结构）
    stage3_status_card = (
        f'<div class="slirn-rc-status-card" id="slirn-sv-stage3-status" '
        f'data-task-id="{esc(task_id)}" data-project-id="{esc(project_id)}" '
        f'style="display:none;">'
        f'<div class="slirn-sub-meta">🔍 解析短视频内容</div>'
        f'<div class="slirn-progress" style="margin-top:6px;">'
        f'<div class="slirn-progress-bar slirn-sv-stage3-bar" style="width:0%;"></div>'
        f'</div>'
        f'<div class="slirn-status-msg slirn-sv-stage3-msg" style="display:none;"></div>'
        f'</div>'
    )

    prompt_badge = ""
    if s3.get("prompt_source") == "user" or s3.get("custom_prompt_used"):
        prompt_badge = '<span class="slirn-sv-badge slirn-sv-badge-warning">使用自定义提示词</span>'
    img_used_badge = (
        '<span class="slirn-sv-badge slirn-sv-badge-warning">已带截图</span>'
        if s3.get("image_used") else ""
    )

    return f'''<div class="slirn-stage" data-stage="3">
  <div class="slirn-stage-head">
    <span class="slirn-stage-num">3</span>
    <strong>解析短视频内容</strong>
    {_stage_badge(s3.get("status"))}
    {_stage_elapsed(s3)}
  </div>
  <div class="slirn-stage-body">
    <label class="slirn-sv-wide slirn-stage3-prompt-label2">解析提示词 {default_badge}
      <textarea id="slirn-stage3-prompt" class="slirn-stage3-prompt-input" rows="8"
        placeholder="录入提取短视频内容所用的提示词；可「引用系统提示词」「设为默认」或「存入列表」复用">{esc(initial_prompt)}</textarea>
    </label>
    <div class="slirn-stage3-prompt-toolbar">
      <div class="slirn-stage3-prompt-actions2">
        <button class="slirn-btn-mini" data-action="sv-stage3-use-system-prompt"
          data-prompt="{esc(STAGE3_SYSTEM_PROMPT)}"
          data-prompt-tmpl="{esc(stage3_system_prompt_template())}"
          title="把系统内置的解析提示词填入文本域（独立于默认/历史；可继续编辑后再解析或设为默认）；条数与时长上下限按当前设置值自动代入">🧭 引用系统提示词</button>
        <button class="slirn-btn-mini slirn-btn-mini-primary" data-action="sv-stage3-save-default"
          title="把当前文本域提示词保存为默认提示词（全局生效，新项目初始填充）">💾 设为默认</button>
        <button class="slirn-btn-mini" data-action="sv-stage3-save-history"
          title="存入提示词列表（最多保留最新 10 条）">📜 存入列表</button>
        <button class="slirn-btn-mini" data-action="sv-stage3-clear-prompt"
          title="清空文本域">🧹 清空</button>
      </div>
    </div>
    <details class="slirn-stage3-prompts-section">
      <summary>📜 历史提示词（最新 10 条 · ⭐ 为默认 · 点击条目查看完整内容）</summary>
      <div class="slirn-stage3-hist-list">{hist_html}</div>
    </details>
    <div class="slirn-stage3-image-block">
      <span class="slirn-sv-muted">智能纪要课程重点截图：</span>
      <input type="file" id="slirn-stage3-image-file" accept="image/png,image/jpeg,image/webp,image/gif,image/bmp">
      <button class="slirn-btn-mini" data-action="sv-stage3-upload-image">📤 上传</button>
      {image_area}
      {ocr_note}
    </div>
    <div class="slirn-sv-form-grid slirn-sv-form-grid--3col">
      <label title="本次解析要挑选出的短视频条数（3-5）">片段数<input type="number" min="3" max="5" id="slirn-stage3-n" value="{n}"></label>
      <label title="每个片段的总时长（选中字幕行秒数之和）必须 ≥ 该下限；解析提示词与结果核查都按此执行">每条时长下限(秒)<input type="number" min="10" max="600" step="5" id="slirn-stage3-dmin" value="{dmin_v}"></label>
      <label title="每个片段的总时长（选中字幕行秒数之和）必须 ≤ 该上限；解析提示词与结果核查都按此执行">每条时长上限(秒)<input type="number" min="20" max="900" step="5" id="slirn-stage3-dmax" value="{dmax_v}"></label>
    </div>
    <div class="slirn-sv-card-actions">
      <button id="slirn-sv-stage3-run-btn" class="slirn-btn slirn-btn-primary" data-action="sv-stage3-run">🔍 解析</button>
      <span class="slirn-sv-muted">{int(s3.get("count") or 0)} 条内容片段</span>
      {prompt_badge}{img_used_badge}
    </div>
    {stage3_status_card}
    <div class="slirn-stage3-hl-list">{hl_list}</div>
    {error_html}
    {_stage_logs(s3)}
  </div>
</div>'''


def _render_stage4_sub_row(hl_index: int, sl: dict, per_hl: dict) -> str:
    """渲染一条 Stage 4 编辑行（每条字幕行 1 行）。"""
    sid = sl.get("src_index") or sl.get("no")
    text = sl.get("text") or ""
    splits = (per_hl.get("split_marks") or {}).get(str(sid)) or []
    marks = [int(x) for x in (per_hl.get("line_marks") or [])]
    is_marked = int(sid) in marks
    is_split = bool(splits)
    cls = "slirn-stage4-sub-row"
    if is_marked:
        cls += " slirn-stage4-sub-row-deleted"
    if is_split:
        cls += " slirn-stage4-sub-row-split"
    badge = ""
    if is_marked:
        badge = '<span class="slirn-sv-badge slirn-sv-badge-failed">🗑 已删</span>'
    elif is_split:
        badge = f'<span class="slirn-sv-badge slirn-sv-badge-warning">✂ 已切 {len(splits)} 段</span>'
    # REQ-20261013-110：4 字段 — 井N / @SRT#K / 时间段 / 文字
    new_no = int(sl.get("no") or 0)
    raw_src = int(sl.get("src_index") or 0)
    split_from = sl.get("split_from")
    # split 子行优先显示父 src_index（更易对应回原 SRT）
    display_src = int(split_from) if split_from is not None else raw_src
    start_ms = int(sl.get("start_ms") or 0)
    end_ms = int(sl.get("end_ms") or start_ms)
    time_range = f"{_fmt_srt_time(start_ms)} → {_fmt_srt_time(end_ms)}"
    return (
        f'<div class="{cls}" data-seg-id="{esc(sid)}" data-hl-index="{hl_index}" '
        f'data-start-ms="{start_ms}" data-end-ms="{end_ms}" '
        f'data-orig-text="{esc(text)}" data-eff-text="{esc(text)}" '
        f'data-deleted="{1 if is_marked else 0}">'
        f'<span class="slirn-stage4-sub-no" title="本段在 effective 顺序里的位置（编辑后重编）">#{new_no}</span>'
        f'<span class="slirn-stage4-sub-srt" title="原始 SRT 行号（编辑/重排前固定）">@SRT#{display_src}</span>'
        f'<span class="slirn-stage4-sub-time" title="本段时间戳（来自原 SRT）">{time_range}</span>'
        f'<span class="slirn-stage4-sub-text">{esc(text)}</span>'
        f'<span class="slirn-stage4-sub-actions">'
        f'{badge}'
        f'<button type="button" class="slirn-btn-mini" data-action="sv-stage4-line-edit" '
        f'data-seg-id="{esc(sid)}" data-hl-index="{hl_index}" title="整行替换文字">✏️</button>'
        f'<button type="button" class="slirn-btn-mini" data-action="sv-stage4-line-resplit" '
        f'data-seg-id="{esc(sid)}" data-hl-index="{hl_index}" title="重新切分本行为多段">✂️</button>'
        f'<button type="button" class="slirn-btn-mini" data-action="sv-stage4-line-toggle" '
        f'data-seg-id="{esc(sid)}" data-hl-index="{hl_index}" '
        f'title="{"恢复" if is_marked else "删除"}本行">'
        f'{"✚" if is_marked else "🗑️"}</button>'
        f'</span>'
        f'</div>'
    )


def _render_stage4_clip_card(hl_index: int, hl: dict, per_hl: dict,
                              effective: list[dict]) -> str:
    """渲染一条 highlight 的编辑卡（标题 + sub 列表 + 预览/初剪按钮）。"""
    title = esc(hl.get("title") or f"片段 {hl_index}")
    head = esc(hl.get("title") or f"片段 {hl_index}")
    sub_rows = "".join(_render_stage4_sub_row(hl_index, s, per_hl) for s in effective)
    if not sub_rows:
        sub_rows = '<div class="slirn-sv-muted">（无有效字幕行）</div>'
    return f'''<div class="slirn-stage4-clip" data-hl-index="{hl_index}">
  <div class="slirn-stage4-clip-head">
    <span class="slirn-stage4-clip-no">#{hl_index}</span>
    <span class="slirn-stage4-clip-title">{head}</span>
  </div>
  <div class="slirn-stage4-clip-subs">{sub_rows}</div>
  <div class="slirn-stage4-clip-actions">
    <button type="button" class="slirn-btn-mini" data-action="sv-stage4-preview"
      data-hl-index="{hl_index}" title="按当前编辑结果拼一段预览 mp4">▶ 预览该高亮</button>
    <button type="button" class="slirn-btn-mini" data-action="sv-stage4-go-coarse"
      data-hl-index="{hl_index}" title="切换到 Stage 5 并只跑该条粗剪">🎬 跳到 Stage 5（仅该条）</button>
    <span class="slirn-sv-muted slirn-stage4-clip-preview-status"></span>
  </div>
</div>'''


def _stage4_optimize(project: dict, *, task_id: str = "", project_id: str = "",
                      repo_root: str | Path | None = None) -> str:
    """REQ-20261013-100 + REQ-20261013-108：Stage 4「内容优化」。

    - 顶部：优化提示词输入区（textarea + 4 按钮 + 历史列表，镜像 Stage 3）。
    - 中部：现有「✨ 优化字幕」按钮 + 每条 highlight 统计 + warnings。
    - 下部：每条 highlight 一张编辑卡（含 sub 行 + ✏️/✂️/🗑️ 按钮 + 预览/初剪按钮）。
    - 用户手动编辑只写 per-hl JSON，不污染 stage3/highlights.json。
    """
    s4o = project.get("pipeline", {}).get("stage4_optimize", {}) or {}
    s3 = project.get("pipeline", {}).get("stage3_analyze", {}) or {}
    status = s4o.get("status") or "pending"
    hl_rows = s4o.get("highlights") or []
    warnings = s4o.get("warnings") or []
    error_html = ""
    if status == "failed" and not hl_rows:
        err = s4o.get("error") or "内容优化失败"
        error_html = f'<div class="slirn-stage-error">{esc(err)}</div>'

    # 是否允许运行：Stage 3 必须 done 且至少有 1 条 highlight
    s3_done = s3.get("status") == "done"
    hl_list = s3.get("highlights") or []
    can_run = s3_done and bool(hl_list)
    btn_label = "✨ 重新优化" if status == "done" else "✨ 优化字幕"
    btn_class = "slirn-btn" if status == "done" else "slirn-btn slirn-btn-primary"
    disabled_attr = "" if can_run else " disabled title=\"请先完成 Stage 3 解析短视频内容\""
    run_btn = (
        f'<button id="slirn-sv-stage4-run-btn" class="{btn_class}" '
        f'data-action="sv-stage4-optimize-run"{disabled_attr}>'
        f'{btn_label}</button>'
    )

    # REQ-20261013-111：Stage 4 重新优化进度卡（仿 stage5/7 status-card 结构）
    stage4_status_card = (
        f'<div class="slirn-rc-status-card" id="slirn-sv-stage4-status" '
        f'data-task-id="{esc(task_id)}" data-project-id="{esc(project_id)}" '
        f'style="display:none;">'
        f'<div class="slirn-sub-meta">🔄 内容优化进度</div>'
        f'<div class="slirn-progress" style="margin-top:6px;">'
        f'<div class="slirn-progress-bar slirn-sv-stage4-bar" style="width:0%;"></div>'
        f'</div>'
        f'<div class="slirn-status-msg slirn-sv-stage4-msg" style="display:none;"></div>'
        f'</div>'
    )

    # 提示词区（REQ-20261013-108）
    # initial_prompt 优先项目级 prompt_user；其次全局 stage4 default；都没有 → 系统级 STAGE4_SYSTEM_PROMPT
    project_prompt = str(s4o.get("prompt_user") or s4o.get("prompt") or "")
    initial_prompt = project_prompt  # 前端 loadPanel 会再用 load_prompts 覆盖
    default_badge = ""
    if project_prompt:
        default_badge = '<span class="slirn-sv-badge slirn-sv-badge-ok">当前项目已填</span>'
    # 历史列表 HTML 由前端 loadPanel 拉 load_prompts 后再注入；这里留空壳
    hist_html = '<div class="slirn-sv-muted">（加载中…）</div>'

    # 每条短视频的优化统计行
    detail_rows = []
    for hl in hl_rows:
        idx = hl.get("index") or len(detail_rows) + 1
        st = hl.get("status") or "pending"
        badge_cls = {
            "ok": "slirn-sv-badge-ok",
            "kept_original": "slirn-sv-badge-warning",
            "failed": "slirn-sv-badge-failed",
        }.get(st, "slirn-sv-badge-muted")
        badge_text = {
            "ok": "✅ 已优化",
            "kept_original": "⚠️ 保留原行",
            "failed": "❌ 失败",
        }.get(st, st)
        detail_rows.append(
            f'<div class="slirn-stage4-opt-row">'
            f'<span class="slirn-sv-row-no">#{idx}</span>'
            f'<span class="slirn-sv-badge {badge_cls}">{badge_text}</span>'
            f'<span class="slirn-sv-muted">'
            f'{int(hl.get("lines_before") or 0)} 行 → {int(hl.get("lines_after") or 0)} 行 · '
            f'保留 {int(hl.get("kept") or 0)} · 修改 {int(hl.get("fixed") or 0)} · '
            f'切分 {int(hl.get("split") or 0)} · 删除 {int(hl.get("deleted") or 0)}'
            f'</span>'
            f'</div>'
        )
    detail_html = "".join(detail_rows)
    warn_html = ""
    if warnings:
        items = "".join(f'<li>{esc(w)}</li>' for w in warnings[:8])
        more = f'<li>… 其余 {len(warnings) - 8} 条见日志</li>' if len(warnings) > 8 else ""
        warn_html = f'<ul class="slirn-stage4-opt-warns">{items}{more}</ul>'

    # 每条 highlight 的编辑卡（REQ-20261013-108 + REQ-20261013-109 tab 化）
    # tab 化后：tab bar 顶部 + 每个 highlight 套一层 .slirn-stage4-hl-pane，
    # 第 1 个 tab / pane 加 active；切换由前端 sv-stage4-hl-tab action 触发
    # switchHlTab（共用 stage3 一套 CSS + 状态记忆 window._slirnSvHlTab）。
    clip_tabs: list[str] = []
    clip_panes: list[str] = []
    root: Path | None = None
    if repo_root:
        try:
            root = Path(str(repo_root))
        except Exception:  # noqa: BLE001
            root = None
    if root and task_id and project_id and hl_list:
        for i, hl in enumerate(hl_list, start=1):
            try:
                per_hl = _sv_opt.load_per_hl(root, task_id, project_id, i)
                effective = _sv_opt.effective_subtitle_lines(root, task_id, project_id, i)
            except Exception:  # noqa: BLE001
                per_hl = {}
                effective = []
            title = hl.get("title") or f"片段 {i}"
            is_first = len(clip_panes) == 0
            _tab_label = title if len(title) <= 10 else title[:10] + "…"
            clip_tabs.append(
                f'<button class="slirn-stage4-hl-tab{" active" if is_first else ""}" '
                f'data-action="sv-stage4-hl-tab" data-hl-index="{i}" '
                f'title="{esc(title)}">#{i} {esc(_tab_label)}</button>'
            )
            clip_panes.append(
                f'<div class="slirn-stage4-hl-pane{" active" if is_first else ""}" '
                f'data-hl-index="{i}">'
                f'{_render_stage4_clip_card(i, hl, per_hl, effective)}'
                f'</div>'
            )
    if clip_panes:
        clip_html = (
            '<div class="slirn-stage4-clips">'
            '<div class="slirn-stage4-hl-tabs">' + "".join(clip_tabs) + '</div>'
            + "".join(clip_panes) +
            '</div>'
        )
    else:
        clip_html = '<div class="slirn-stage4-clips">（无 highlight，请先完成 Stage 3）</div>'

    return f'''<div class="slirn-stage" data-stage="4">
  <div class="slirn-stage-head">
    <span class="slirn-stage-num">4</span>
    <strong>内容优化</strong>
    {_stage_badge(status)}
    {_stage_elapsed(s4o)}
  </div>
  <div class="slirn-stage-body">
    <div class="slirn-sv-muted slirn-stage4-opt-help">
      按标题让大模型优化每条短视频的字幕：<strong>切分长行</strong>（子句拼接须逐字等于原文，时间戳按占比估算）、
      <strong>修改文字</strong>（去口水/错字）、<strong>删除冗余行</strong>。
      结果写回 Stage 3 字幕（首次优化前自动备份），下方可逐条手动微调；拼接与后续阶段将作废重跑。
    </div>

    <div class="slirn-stage4-prompt-card">
      <label class="slirn-sv-wide slirn-stage4-prompt-label">优化提示词 {default_badge}
        <textarea id="slirn-stage4-prompt" class="slirn-stage4-prompt-input" rows="6"
          placeholder="录入「内容优化」所用的提示词；可「引用系统提示词」「设为默认」或「存入列表」复用">{esc(initial_prompt)}</textarea>
      </label>
      <div class="slirn-stage4-prompt-toolbar">
        <div class="slirn-stage4-prompt-actions">
          <button class="slirn-btn-mini" data-action="sv-stage4-use-system-prompt"
            data-prompt="{esc(STAGE4_SYSTEM_PROMPT)}"
            title="把系统内置的优化提示词填入文本域（独立于默认/历史；可继续编辑后再优化或设为默认）">🧭 引用系统提示词</button>
          <button class="slirn-btn-mini slirn-btn-mini-primary"
            data-action="sv-stage4-save-default"
            title="把当前文本域提示词保存为 Stage 4 默认提示词（全局生效，新项目初始填充）">💾 设为默认</button>
          <button class="slirn-btn-mini" data-action="sv-stage4-save-history"
            title="存入提示词列表（最多保留最新 10 条）">📜 存入列表</button>
          <button class="slirn-btn-mini" data-action="sv-stage4-clear-prompt"
            title="清空文本域">🧹 清空</button>
        </div>
      </div>
      <details class="slirn-stage4-prompts-section">
        <summary>📜 Stage 4 历史提示词（最新 10 条 · ⭐ 为默认 · 点击条目查看完整内容）</summary>
        <div class="slirn-stage4-hist-list">{hist_html}</div>
      </details>
    </div>

    <div class="slirn-sv-card-actions">
      {run_btn}
      <button class="slirn-btn slirn-btn-primary" data-action="sv-stage4-go-coarse-all"{disabled_attr}
        title="切换到 Stage 5 并触发全量粗剪（7 条）">🎬 生成初剪文件（全部 {len(hl_list) or 0} 条）</button>
    </div>
    {stage4_status_card}
    {error_html}
    {_stage4_detail(detail_html, warn_html)}
    {_stage_logs(s4o)}

    <div class="slirn-stage4-section-title">逐条编辑（手动覆盖：仅本项目，不回写 Stage 3）</div>
    {clip_html}
  </div>
</div>'''


def _stage5_coarse(project: dict) -> str:
    """Stage 5 粗剪 UI（REQ-20261013-103）：仿长视频 compose_rough 顶部状态卡 +
    异步重新合成 + 删除 + 连续 SRT 复制/下载；每条 clip 卡片内加单条 SRT 操作。
    """
    task_id = str(project.get("task_id") or "")
    project_id = str(project.get("id") or "")
    s4 = project.get("pipeline", {}).get("stage5_coarse", {}) or {}
    has_artifact = bool(s4.get("highlights"))
    total_sec = float(s4.get("total_duration_sec") or 0)
    count_n = int(s4.get("count") or 0)

    # ---- 顶部状态卡（仿长视频 _render_rough_compose_zone L1200-1216）----
    stats_line = (
        f'{count_n} 条 · 总时长 {total_sec:.1f}s'
        if has_artifact
        else '尚未合成'
    )
    # progress 条：状态 done/failed → 100/0；其他 → 占位 0（异步轮询会持续更新）
    s4_status = str(s4.get("status") or "pending")
    progress_pct = 100 if s4_status == "done" else (0 if s4_status != "running" else 30)
    progress_color = (
        "slirn-progress-bar--done" if s4_status == "done"
        else ("slirn-progress-bar--failed" if s4_status == "failed" else "")
    )
    compose_btn_text = "🎬 重新合成" if has_artifact else "🎬 合成粗剪"
    delete_btn = (
        f'<button class="slirn-btn slirn-btn-danger" data-action="sv-stage5-delete" '
        f'data-task-id="{esc(task_id)}" data-project-id="{esc(project_id)}" '
        f'style="margin-left:8px;">🗑️ 删除粗剪成品</button>'
        if has_artifact else ""
    )
    # ---- 连续 SRT 复制/下载（仅在有产物时显示）----
    subs_actions = ""
    if has_artifact:
        subs_actions = f'''<div class="slirn-sv-card-actions" style="margin-top:8px;">
      <button class="slirn-btn slirn-btn-sm" data-action="sv-stage5-subs-copy"
              data-task-id="{esc(task_id)}" data-project-id="{esc(project_id)}">📋 复制连续 SRT</button>
      <button class="slirn-btn slirn-btn-sm" data-action="sv-stage5-subs-download"
              data-task-id="{esc(task_id)}" data-project-id="{esc(project_id)}">⬇️ 下载连续 SRT</button>
    </div>'''
    status_card = f'''<div class="slirn-rc-status-card" id="slirn-sv-stage5-status"
        data-task-id="{esc(task_id)}" data-project-id="{esc(project_id)}">
      <div class="slirn-sub-meta">🎞️ 粗剪成片 · {esc(stats_line)}</div>
      <div class="slirn-progress" style="margin-top:6px;">
        <div class="slirn-progress-bar {progress_color}"
             style="width:{progress_pct}%;"></div>
      </div>
      <div class="slirn-status-msg slirn-sv-stage5-msg" style="display:none;"></div>
    </div>'''

    # ---- clip 列表（每条加「📋 复制 SRT」+「⬇ 下载 SRT」）----
    rows = []
    for hl in (s4.get("highlights") or []):
        idx = hl.get("index") or len(rows) + 1
        mp4_name = Path(str(hl.get("coarse_mp4") or "")).name
        srt_name = Path(str(hl.get("coarse_srt") or "")).name
        url = _file_url(task_id, project_id, "stage4", mp4_name) if mp4_name else ""
        srt_url = _file_url(task_id, project_id, "stage4", srt_name) if srt_name else ""
        dur = float(hl.get("duration_sec") or 0)
        err = hl.get("error") or ""
        status = hl.get("status") or "pending"
        video = f'<video controls preload="metadata" src="{esc(url)}"></video>' if url else '<span class="slirn-sv-muted">未生成</span>'
        err_html = f'<div class="slirn-stage-error">{esc(err)}</div>' if err else ""
        clip_subs_actions = (
            f'<button class="slirn-btn-mini" data-action="sv-stage5-clip-subs-copy" '
            f'data-clip-index="{idx}" data-task-id="{esc(task_id)}" '
            f'data-project-id="{esc(project_id)}">📋 复制 SRT</button>'
            f'<button class="slirn-btn-mini" data-action="sv-stage5-clip-subs-download" '
            f'data-clip-index="{idx}" data-task-id="{esc(task_id)}" '
            f'data-project-id="{esc(project_id)}">⬇ 下载 SRT</button>'
            if srt_url else ''
        )
        rows.append(
            f'<div class="slirn-stage4-clip" data-clip-index="{idx}">'
            f'<div class="slirn-stage4-clip-head">'
            f'<span class="slirn-sv-row-no">#{idx}</span>'
            f'{_stage_badge(status)}'
            f'<span class="slirn-sv-muted">{dur:.1f}s</span>'
            f'<a class="slirn-btn-mini" href="{esc(url)}" download>下载视频</a>'
            f'{clip_subs_actions}'
            f'</div>'
            f'{video}'
            f'{err_html}'
            f'</div>'
        )

    return f'''<div class="slirn-stage" data-stage="5">
  <div class="slirn-stage-head">
    <span class="slirn-stage-num">5</span>
    <strong>粗剪合成</strong>
    {_stage_badge(s4.get("status"))}
    {_stage_elapsed(s4)}
    <span class="slirn-sv-muted">{count_n} 条</span>
  </div>
  <div class="slirn-stage-body">
    {status_card}
    <div class="slirn-sv-card-actions" style="margin-top:10px;">
      <button class="slirn-btn slirn-btn-primary" data-action="sv-stage5-export"
              data-task-id="{esc(task_id)}" data-project-id="{esc(project_id)}">{compose_btn_text}</button>
      {delete_btn}
    </div>
    {subs_actions}
    <div class="slirn-stage4-list">{"".join(rows) or '<div class="slirn-sv-muted">先完成 Stage 3 再粗剪</div>'}</div>
    {_stage_logs(s4)}
  </div>
</div>'''


def _stage6_refine(project: dict) -> str:
    """Stage 6 字幕优化 UI（REQ-20261013-104）：每条 clip 加「✨ LLM 优化」+ 词频 chips
    + 行级 ✓/✕/✏/🗑/✂ 5 按钮（行级渲染由前端 JS 拉 occurrences 后填）；
    顶部加批量替换面板（🔍 搜索 → 预览 → ✅ 全部替换）。
    """
    task_id = str(project.get("task_id") or "")
    project_id = str(project.get("id") or "")
    s5 = project.get("pipeline", {}).get("stage6_refine", {}) or {}
    rows = []
    for hl in (s5.get("highlights") or []):
        idx = hl.get("index") or len(rows) + 1
        status = hl.get("status") or "pending"
        asr_name = Path(str(hl.get("asr_srt") or "")).name
        refined_name = Path(str(hl.get("refined_srt") or "")).name
        asr_url = _file_url(task_id, project_id, "stage5", asr_name) if asr_name else ""
        # 读 SRT 内容（如果有 refined）— 文件小直接 inline
        srt_inline = ""
        if refined_name:
            srt_path = Path(__file__).resolve().parent.parent  # repo root
            full_path = srt_path / "tasks" / task_id / "short_video" / project_id / "stage5" / refined_name
            if full_path.is_file():
                try:
                    txt = full_path.read_text(encoding="utf-8", errors="replace")
                    srt_inline = esc(txt[:3000])
                except Exception:  # noqa: BLE001
                    srt_inline = ""
        asr_chars = int(hl.get("asr_chars") or 0)
        asr_meta = f'<span class="slirn-sv-muted">ASR {asr_chars} 字</span>' if asr_chars else ""
        err_html = ""
        if hl.get("asr_error"):
            err_html = f'<div class="slirn-stage-error">{esc(hl.get("asr_error"))}</div>'
        rows.append(
            f'<div class="slirn-stage5-clip" data-clip-index="{idx}">'
            f'<div class="slirn-stage5-clip-head">'
            f'<span class="slirn-sv-row-no">#{idx}</span>'
            f'{_stage_badge(status)}'
            f'{asr_meta}'
            f'</div>'
            f'<div class="slirn-sv-card-actions">'
            f'<button class="slirn-btn-mini" data-action="sv-stage5-asr" data-clip-index="{idx}">重新 ASR</button>'
            f'<button class="slirn-btn-mini" data-action="sv-stage5-save" data-clip-index="{idx}">保存修正</button>'
            f'<button class="slirn-btn-mini" data-action="sv-preview-srt" data-url="{esc(asr_url)}">查看 ASR 字幕</button>'
            f'<button class="slirn-btn slirn-btn-primary" data-action="sv-stage6-optimize" '
            f'data-clip-index="{idx}">✨ LLM 优化</button>'
            f'</div>'
            f'<div class="slirn-sv-stage6-words" data-clip-index="{idx}"></div>'
            f'<div class="slirn-sv-stage6-rows" data-clip-index="{idx}"></div>'
            f'<textarea rows="6" class="slirn-stage5-srt" data-clip-index="{idx}" '
            f'placeholder="点击「重新 ASR」后再编辑">{srt_inline}</textarea>'
            f'{err_html}'
            f'</div>'
        )
    return f'''<div class="slirn-stage" data-stage="6">
  <div class="slirn-stage-head">
    <span class="slirn-stage-num">6</span>
    <strong>优化字幕</strong>
    {_stage_badge(s5.get("status"))}
  </div>
  <div class="slirn-stage-body">
    <div class="slirn-sv-muted">每条独立处理：先重新 ASR → LLM 优化 → 行级采纳/拒绝/编辑 → 保存</div>
    <div class="slirn-sv-card-actions" style="margin:10px 0; padding:8px; background:rgba(0,0,0,.04); border-radius:6px;">
      <strong>🔍 批量替换（按当前项目所有 clip）</strong>
      <input type="text" class="slirn-sv-batch-word" placeholder="搜索词" style="margin:0 6px; padding:4px 8px;">
      <span class="slirn-sv-muted">→</span>
      <input type="text" class="slirn-sv-batch-replacement" placeholder="替换为" style="margin:0 6px; padding:4px 8px;">
      <button class="slirn-btn-mini" data-action="sv-stage6-batch-search">🔍 搜索</button>
      <button class="slirn-btn-mini" data-action="sv-stage6-batch-replace">✅ 全部替换</button>
      <div class="slirn-sv-batch-result" style="margin-top:6px;"></div>
    </div>
    <div class="slirn-stage5-list">{"".join(rows) or '<div class="slirn-sv-muted">先完成 Stage 5 粗剪</div>'}</div>
    {_stage_logs(s5)}
  </div>
</div>'''


def _stage7_finalize(project: dict) -> str:
    """Stage 7 精剪混编 v1 完整版 UI（REQ-20261013-105 + REQ-20261013-106 重排）。

    REQ-20261013-106：按长视频「精剪合成」11 block 顺序重排 UI：
      1) 素材上传（6 项）  2) 背景图区域检测  3) 顶部操作条（导出+参数导入导出）
      4) 模板条           5) 预览          6) 视频/字幕/背景 三块画布
      7) 字幕字体设置      8) 视频源裁剪     9) 片头封面
      10) 背景音乐        11) 输出设置

    每条 highlight 各自一份完整卡片（仿 stage5/6 clip 卡片风格），每条卡片
    顶部是产物（视频+下载/查看 srt/预览），下面 11 block 字段集。
    短/长隔离：params 落 project.json → stage7_finalize.params；模板落
    tasks/_global_short_video_fine_profiles.json（不写长视频）。
    """
    task_id = str(project.get("task_id") or "")
    project_id = str(project.get("id") or "")
    s6 = project.get("pipeline", {}).get("stage7_finalize", {}) or {}
    p = s6.get("params") or {}
    font = p.get("font") or {}
    output = p.get("output") or {}
    audio = p.get("audio") or {}
    preview = p.get("preview") or {}
    layout = p.get("layout") or {}
    materials = p.get("materials") or {}
    detected = p.get("detected_region")
    res = output.get("resolution") or "720p"
    audio_enabled = "checked" if audio.get("enabled") else ""
    preview_dur = int(preview.get("duration_sec") or 5)

    # ---- 异步状态卡（仿 Stage 5 顶部卡） ----
    n_hl = len(s6.get("highlights") or [])
    status_card = f'''<div class="slirn-rc-status-card" id="slirn-sv-stage7-status"
        data-task-id="{esc(task_id)}" data-project-id="{esc(project_id)}">
      <div class="slirn-sub-meta">🎞️ 精剪混编 · {esc(res)} · {n_hl} 条</div>
      <div class="slirn-progress" style="margin-top:6px;">
        <div class="slirn-progress-bar" style="width:0%;"></div>
      </div>
      <div class="slirn-status-msg slirn-sv-stage7-msg" style="display:none;"></div>
    </div>'''

    def _render_one_card(idx: int, hl: dict | None) -> str:
        """单 highlight 卡片：11 block。hl=None 时用占位数据。"""
        if hl is None:
            hl = {}
        status = hl.get("status") or "pending"
        err = hl.get("error") or ""
        dur = float(hl.get("duration_sec") or 0)
        mp4_name = Path(str(hl.get("final_mp4") or "")).name
        srt_name = Path(str(hl.get("continuous_srt") or "")).name
        mp4_url = _file_url(task_id, project_id, "stage6", mp4_name) if mp4_name else ""
        srt_url = _file_url(task_id, project_id, "stage6", srt_name) if srt_name else ""
        video_block = (
            f'<video controls preload="metadata" src="{esc(mp4_url)}" style="max-width:100%; max-height:280px;"></video>'
            if mp4_url else '<span class="slirn-sv-muted">未生成（点「💾 导出最终视频」渲染）</span>'
        )
        err_html = f'<div class="slirn-stage-error">{esc(err)}</div>' if err else ""

        # 6 素材卡：video/subtitle 自动从 stage5/6 推导；其他 4 项 UI 显示但留空
        auto_video = materials.get("video") or mp4_name
        auto_subtitle = materials.get("subtitle") or srt_name
        mat_rows = []
        for slot, label, hint, val in [
            ("video",     "🎬 视频",     "必选 · 来自 Stage 5 粗剪",  auto_video),
            ("subtitle",  "📝 字幕",     "必选 · 来自 Stage 6 优化",  auto_subtitle),
            ("cover",     "🖼 封面",     "可选 · 顶部封面图",         materials.get("cover", "")),
            ("bg",        "🎨 背景",     "可选 · 背景图",            materials.get("bg", "")),
            ("reference", "🔖 参考图",   "可选 · AI 智能布局输入",    materials.get("reference", "")),
            ("audio",     "🎵 附加音频", "可选 · 独立音轨",          materials.get("audio", "")),
        ]:
            mat_rows.append(
                f'<div class="slirn-sv-mat-row" data-slot="{esc(slot)}">'
                f'<label class="slirn-sv-mat-label">{esc(label)}</label>'
                f'<input type="text" class="slirn-sv-mat-input" data-mat-slot="{esc(slot)}" '
                f'data-clip-index="{esc(idx)}" '
                f'value="{esc(str(val))}" placeholder="{esc(hint)}" '
                f'style="flex:1; min-width:200px; padding:4px 8px;">'
                f'</div>'
            )
        upload_html = (
            f'<details class="slirn-fine-section" open>'
            f'<summary class="slirn-fine-section-summary">📁 上传素材（6 项）</summary>'
            f'<div class="slirn-sv-mat-grid">{"".join(mat_rows)}</div>'
            f'</details>'
        )

        # block 2：背景图区域检测
        detected_info = ""
        if detected and isinstance(detected, dict):
            detected_info = (
                f'<div class="slirn-sv-muted">检测结果：x={int(detected.get("x") or 0)}, '
                f'y={int(detected.get("y") or 0)}, w={int(detected.get("w") or 0)}, '
                f'h={int(detected.get("h") or 0)} (ratio={detected.get("content_ratio", 0):.2%})</div>'
            )
        bg_detect_block = (
            f'<details class="slirn-fine-section">'
            f'<summary class="slirn-fine-section-summary">🎨 背景图区域检测（4 角点 + 宽高）</summary>'
            f'<div class="slirn-sv-card-actions" style="margin:6px 0;">'
            f'<button class="slirn-btn-mini" data-action="sv-stage7-detect-bg" data-clip-index="{esc(idx)}">🔍 检测区域</button>'
            f'<button class="slirn-btn-mini" data-action="sv-stage7-apply-detected" data-clip-index="{esc(idx)}">✅ 填充到视频位置和裁剪</button>'
            f'<button class="slirn-btn-mini" data-action="sv-stage7-parse-layout" data-clip-index="{esc(idx)}">🤖 AI 智能识别布局</button>'
            f'</div>'
            f'{detected_info}'
            f'</details>'
        )

        # block 3：顶部操作条
        actions_bar_1 = (
            f'<div class="slirn-fine-actions-bar">'
            f'<button class="slirn-btn slirn-btn-primary" data-action="sv-stage7-export" data-clip-index="{esc(idx)}">💾 导出最终视频</button>'
            f'<button class="slirn-btn" data-action="sv-stage7-save-params" data-clip-index="{esc(idx)}">📤 导出参数</button>'
            f'<button class="slirn-btn" data-action="sv-stage7-load-default" data-clip-index="{esc(idx)}">📥 导入参数</button>'
            f'<span class="slirn-sv-muted">导出后输出在 stage6/final_*.mp4</span>'
            f'</div>'
        )

        # block 4：模板条
        actions_bar_2 = (
            f'<div class="slirn-fine-actions-bar">'
            f'<button class="slirn-btn slirn-btn-primary" data-action="sv-stage7-profiles-save" data-clip-index="{esc(idx)}">💾 保存参数为模板</button>'
            f'<span class="slirn-fine-actions-label">模板名</span>'
            f'<input type="text" class="slirn-sv-profile-name" data-clip-index="{esc(idx)}" '
            f'placeholder="（可选）填了名另存为模板" maxlength="30" autocomplete="off">'
            f'<button class="slirn-btn" data-action="sv-stage7-profiles-apply" data-clip-index="{esc(idx)}">📥 引用参数（本地）</button>'
            f'<span class="slirn-sv-muted">未保存</span>'
            f'</div>'
        )

        # block 5：预览
        preview_block = (
            f'<details class="slirn-fine-section" open>'
            f'<summary class="slirn-fine-section-summary">🎬 生成预览（开始时间 + 时长 2–30 秒）</summary>'
            f'<div style="display:flex; align-items:center; gap:8px; margin:6px 0;">'
            f'<button class="slirn-btn slirn-btn-primary" data-action="sv-stage7-preview" data-clip-index="{esc(idx)}">🎬 生成预览</button>'
            f'<span class="slirn-fine-actions-label">预览时长</span>'
            f'<input type="number" class="slirn-sv-preview-duration" data-clip-index="{esc(idx)}" '
            f'min="2" max="30" value="{preview_dur}" style="width:60px; padding:3px 6px;">'
            f'<span class="slirn-sv-muted">秒（2-30）</span>'
            f'</div>'
            f'</details>'
        )

        # block 6：视频/字幕/背景 三块画布
        def _xy_block(mat_key: str, icon: str, name: str, d: dict) -> str:
            d = d or {}
            x = int(d.get("x") or 0)
            y = int(d.get("y") or 0)
            w = int(d.get("w") or 0)
            h = int(d.get("h") or 0)
            return (
                f'<div class="slirn-fine-layout-block">'
                f'<div class="slirn-fine-layout-title">'
                f'<label><input type="checkbox" class="slirn-fine-enabled" data-key="{esc(mat_key)}" '
                f'data-clip-index="{esc(idx)}" '
                f'{"checked" if d.get("enabled", True) else ""}> {esc(icon)} {esc(name)}</label>'
                f'</div>'
                f'<div class="slirn-fine-params">'
                f'<div class="slirn-fine-param">'
                f'<span class="slirn-fine-param-label">X (-1080–1080)</span>'
                f'<input type="number" class="slirn-fine-num" data-key="{esc(mat_key)}.x" '
                f'value="{x}" min="-1080" max="1080" step="1">'
                f'</div>'
                f'<div class="slirn-fine-param">'
                f'<span class="slirn-fine-param-label">Y (-1920–1920)</span>'
                f'<input type="number" class="slirn-fine-num" data-key="{esc(mat_key)}.y" '
                f'value="{y}" min="-1920" max="1920" step="1">'
                f'</div>'
                f'<div class="slirn-fine-param">'
                f'<span class="slirn-fine-param-label">宽</span>'
                f'<input type="number" class="slirn-fine-num" data-key="{esc(mat_key)}.w" '
                f'value="{w}" min="0" max="1080" step="1">'
                f'</div>'
                f'<div class="slirn-fine-param">'
                f'<span class="slirn-fine-param-label">高</span>'
                f'<input type="number" class="slirn-fine-num" data-key="{esc(mat_key)}.h" '
                f'value="{h}" min="0" max="1920" step="1">'
                f'</div>'
                f'</div>'
                f'</div>'
            )
        canvas_block = (
            f'<details class="slirn-fine-section">'
            f'<summary class="slirn-fine-section-summary">🎬 视频  +  📝 字幕  +  🎨 背景（X/Y/W/H）</summary>'
            f'<div class="slirn-fine-cols">'
            f'<div class="slirn-fine-col">'
            f'{_xy_block("video", "🎬", "视频", layout.get("video"))}'
            f'</div>'
            f'<div class="slirn-fine-col">'
            f'{_xy_block("subtitle", "📝", "字幕", layout.get("subtitle"))}'
            f'{_xy_block("bg", "🎨", "背景", layout.get("bg"))}'
            f'</div>'
            f'</div>'
            f'</details>'
        )

        # block 7：字幕字体设置 11 项
        f_family = str(font.get("family") or "STHeitiMedium.ttc")
        font_family_options = (
            f'<option value="STHeitiMedium.ttc" {"selected" if "STHeiti" in f_family else ""}>STHeitiMedium（系统）</option>'
            f'<option value="Noto Sans CJK SC" {"selected" if "Noto" in f_family else ""}>思源黑体 Noto Sans CJK SC</option>'
        )
        def _font_row(label: str, ctrl: str) -> str:
            return (
                f'<div class="slirn-fine-font-row">'
                f'<span class="slirn-fine-font-label">{esc(label)}</span>'
                f'{ctrl}'
                f'</div>'
            )
        # 字段字符串提到外面（f-string 不支持内嵌反斜杠转义）
        family_ctrl = (
            '<select class="slirn-fine-font-sel" data-font-key="family" data-clip-index="' + esc(idx) + '">'
            + font_family_options
            + '</select>'
        )
        size_ctrl = (
            '<input type="number" class="slirn-fine-font-num" data-font-key="size" data-clip-index="'
            + esc(idx) + '" min="12" max="96" value="' + str(int(font.get("size") or 48)) + '">'
        )
        bold_chk = " checked" if font.get("bold") else ""
        bold_ctrl = (
            '<label><input type="checkbox" class="slirn-fine-font-chk" '
            'data-font-key="bold" data-clip-index="' + esc(idx) + '"' + bold_chk + '> 启用</label>'
        )
        align_v = font.get("align") or "center"
        align_ctrl = (
            '<select class="slirn-fine-font-sel" data-font-key="align" data-clip-index="'
            + esc(idx) + '">'
            + ('<option value="left"' + (' selected' if align_v == "left" else "") + '>左对齐</option>')
            + ('<option value="center"' + (' selected' if align_v == "center" else "") + '>居中</option>')
            + ('<option value="right"' + (' selected' if align_v == "right" else "") + '>右对齐</option>')
            + '</select>'
        )
        offset_ctrl = (
            '<input type="number" class="slirn-fine-font-num" data-font-key="offset_ms" '
            'data-clip-index="' + esc(idx) + '" min="-1920" max="1920" step="1" value="'
            + str(int(font.get("offset_ms") or 0)) + '">'
        )
        stroke_w_ctrl = (
            '<input type="number" class="slirn-fine-font-num" data-font-key="stroke_width" '
            'data-clip-index="' + esc(idx) + '" min="0" max="10" value="'
            + str(int(font.get("stroke_width") or 2)) + '">'
        )
        color_ctrl = (
            '<input type="color" class="slirn-fine-font-color" data-font-key="color" '
            'data-clip-index="' + esc(idx) + '" value="'
            + esc(str(font.get("color") or "#FFFFFF")) + '">'
        )
        stroke_color_ctrl = (
            '<input type="color" class="slirn-fine-font-color" data-font-key="stroke_color" '
            'data-clip-index="' + esc(idx) + '" value="'
            + esc(str(font.get("stroke_color") or "#000000")) + '">'
        )
        bg_chk = " checked" if font.get("bg_enabled") else ""
        bg_enabled_ctrl = (
            '<label><input type="checkbox" class="slirn-fine-font-chk" '
            'data-font-key="bg_enabled" data-clip-index="' + esc(idx) + '"' + bg_chk + '> 启用</label>'
        )
        bg_color_ctrl = (
            '<input type="color" class="slirn-fine-font-color" data-font-key="bg_color" '
            'data-clip-index="' + esc(idx) + '" value="'
            + esc(str(font.get("bg_color") or "#000000")) + '">'
        )
        bg_op = str(float(font.get("bg_opacity") or 0.5))
        bg_opacity_ctrl = (
            '<input type="range" class="slirn-fine-font-slider" data-font-key="bg_opacity" '
            'data-clip-index="' + esc(idx) + '" min="0" max="1" step="0.05" value="' + bg_op + '">'
            '<input type="number" class="slirn-fine-num" min="0" max="1" step="0.05" value="' + bg_op + '">'
        )
        font_block = (
            f'<details class="slirn-fine-section">'
            f'<summary class="slirn-fine-section-summary">🔤 字幕字体设置（11 项）</summary>'
            f'<div class="slirn-fine-font-block">'
            f'{_font_row("字体本体", family_ctrl)}'
            f'{_font_row("字体大小（px）", size_ctrl)}'
            f'{_font_row("粗体", bold_ctrl)}'
            f'{_font_row("屏幕对齐", align_ctrl)}'
            f'{_font_row("偏移量（px）", offset_ctrl)}'
            f'{_font_row("描边宽度（px）", stroke_w_ctrl)}'
            f'{_font_row("文字颜色", color_ctrl)}'
            f'{_font_row("描边颜色", stroke_color_ctrl)}'
            f'{_font_row("背景框", bg_enabled_ctrl)}'
            f'{_font_row("背景颜色", bg_color_ctrl)}'
            f'{_font_row("背景透明度", bg_opacity_ctrl)}'
            f'</div>'
            f'</details>'
        )

        # block 8：视频源裁剪
        crop_block = (
            f'<details class="slirn-fine-section">'
            f'<summary class="slirn-fine-section-summary">🎥 视频源裁剪（从源画面里截一个矩形范围）</summary>'
            f'<div class="slirn-fine-crop-block">'
            f'<div class="slirn-fine-crop-presets">'
            f'<button class="slirn-btn-mini" data-action="sv-stage7-crop-preset" data-preset="full" data-clip-index="{esc(idx)}">📐 全幅（不裁剪）</button>'
            f'<button class="slirn-btn-mini" data-action="sv-stage7-crop-preset" data-preset="16x9" data-clip-index="{esc(idx)}">🎯 16:9 居中</button>'
            f'<button class="slirn-btn-mini" data-action="sv-stage7-crop-preset" data-preset="1x1" data-clip-index="{esc(idx)}">⬛ 1:1 居中</button>'
            f'</div>'
            f'<label><input type="checkbox" class="slirn-sv-crop-aspect-link" data-clip-index="{esc(idx)}" checked> 🔗 锁定 16:9 比例（防变形）</label>'
            f'<div class="slirn-sv-muted">短视频源裁剪：X/Y/W/H 滑块（占位，参数 v2 接入）</div>'
            f'</div>'
            f'</details>'
        )

        # block 9：片头封面
        cover_dur = float((layout.get("cover") or {}).get("duration", 2.0) or 2.0)
        cover_block = (
            f'<details class="slirn-fine-section">'
            f'<summary class="slirn-fine-section-summary">🎞 片头封面（全屏展示 N 秒后切视频）</summary>'
            f'<div class="slirn-fine-cover-block">'
            f'<label><input type="checkbox" class="slirn-fine-enabled" data-key="cover" data-clip-index="{esc(idx)}"> 启用片头封面</label>'
            f'<div class="slirn-fine-params">'
            f'<div class="slirn-fine-param">'
            f'<span class="slirn-fine-param-label">展示时长（0–10 秒）</span>'
            f'<input type="number" class="slirn-fine-num" data-key="cover.duration" value="{cover_dur:.1f}" min="0" max="10" step="0.5">'
            f'</div>'
            f'</div>'
            f'<div class="slirn-form-hint">设为 0 秒 = 不显示片头；建议 1–3 秒（短视频一般不用）。</div>'
            f'</div>'
            f'</details>'
        )

        # block 10：背景音乐
        vol_db = int(audio.get("volume_db") or -6)
        fade_in = int((audio.get("fade_in_ms") or 500) / 1000 * 10) / 10
        fade_out = int((audio.get("fade_out_ms") or 1000) / 1000 * 10) / 10
        audio_block = (
            f'<details class="slirn-fine-section">'
            f'<summary class="slirn-fine-section-summary">🎵 背景音乐（与原声混合播放，保留说话人语音）</summary>'
            f'<div class="slirn-fine-audio-block">'
            f'<label><input type="checkbox" class="slirn-sv-audio-enabled" data-clip-index="{esc(idx)}" {audio_enabled}> 启用背景音乐</label>'
            f'<div class="slirn-sv-card-actions" style="margin:6px 0;">'
            f'<button class="slirn-btn-mini" data-action="sv-stage7-list-bgm" data-clip-index="{esc(idx)}">🎵 选系统 BGM</button>'
            f'<span class="slirn-sv-muted" style="margin-left:6px;">当前：{esc(str(audio.get("bgm_path") or "未选"))}</span>'
            f'</div>'
            f'<div class="slirn-fine-params">'
            f'<div class="slirn-fine-param">'
            f'<span class="slirn-fine-param-label">音量（dB 衰减，0 = 原曲）</span>'
            f'<input type="number" class="slirn-fine-num" data-key="audio.volume_db" value="{vol_db}" min="-40" max="0" step="1">'
            f'</div>'
            f'<div class="slirn-fine-param">'
            f'<span class="slirn-fine-param-label">淡入（0–5 秒）</span>'
            f'<input type="number" class="slirn-fine-num" data-key="audio.fade_in_sec" value="{fade_in}" min="0" max="5" step="0.5">'
            f'</div>'
            f'<div class="slirn-fine-param">'
            f'<span class="slirn-fine-param-label">淡出（0–5 秒）</span>'
            f'<input type="number" class="slirn-fine-num" data-key="audio.fade_out_sec" value="{fade_out}" min="0" max="5" step="0.5">'
            f'</div>'
            f'</div>'
            f'</div>'
            f'</details>'
        )

        # block 11：输出设置
        output_block = (
            f'<details class="slirn-fine-section">'
            f'<summary class="slirn-fine-section-summary">📺 输出设置</summary>'
            f'<div class="slirn-fine-output-block">'
            f'<div class="slirn-fine-font-row">'
            f'<span class="slirn-fine-font-label">分辨率</span>'
            f'<select class="slirn-sv-output-resolution" data-clip-index="{esc(idx)}" style="margin:0 6px; padding:3px 6px;">'
            f'<option value="720p" {"selected" if res == "720p" else ""}>720p（1280×720）</option>'
            f'<option value="1080p" {"selected" if res == "1080p" else ""}>1080p（1920×1080）</option>'
            f'<option value="source" {"selected" if res == "source" else ""}>源分辨率</option>'
            f'</select>'
            f'</div>'
            f'</div>'
            f'</details>'
        )

        return (
            f'<div class="slirn-stage6-clip slirn-fine-card" data-clip-index="{esc(idx)}">'
            f'<div class="slirn-stage6-clip-head">'
            f'<span class="slirn-sv-row-no">#{esc(idx)}</span>'
            f'{_stage_badge(status)}'
            f'<span class="slirn-sv-muted">{dur:.1f}s</span>'
            f'<a class="slirn-btn-mini" href="{esc(mp4_url)}" download>下载 mp4</a>'
            f'<button class="slirn-btn-mini" data-action="sv-preview-srt" data-url="{esc(srt_url)}">查看 srt</button>'
            f'</div>'
            f'<div class="slirn-sv-card-actions" style="margin:6px 0;">'
            f'<button class="slirn-btn-mini" data-action="sv-stage7-preview" data-clip-index="{esc(idx)}">🎬 预览</button>'
            f'<button class="slirn-btn-mini" data-action="sv-stage7-save-params" data-clip-index="{esc(idx)}">💾 保存参数</button>'
            f'<button class="slirn-btn slirn-btn-primary" data-action="sv-stage7-export" data-clip-index="{esc(idx)}">💾 导出最终视频</button>'
            f'</div>'
            f'{video_block}'
            f'{err_html}'
            f'{actions_bar_1}'
            f'{actions_bar_2}'
            f'{upload_html}'
            f'{bg_detect_block}'
            f'{preview_block}'
            f'{canvas_block}'
            f'{font_block}'
            f'{crop_block}'
            f'{cover_block}'
            f'{audio_block}'
            f'{output_block}'
            f'</div>'
        )

    hls = s6.get("highlights") or []
    cards = [_render_one_card(i, hl) for i, hl in enumerate(hls, start=1)]
    if not cards:
        cards.append(_render_one_card(1, None))

    return f'''<div class="slirn-stage" data-stage="7">
  <div class="slirn-stage-head">
    <span class="slirn-stage-num">7</span>
    <strong>精剪混编（v1 完整版 · 长视频布局对齐）</strong>
    {_stage_badge(s6.get("status"))}
    {_stage_elapsed(s6)}
  </div>
  <div class="slirn-stage-body">
    {status_card}
    <div class="slirn-form-hint">每条 highlight 各自一份完整卡（仿长视频「精剪合成」11 block 布局）。</div>
    <div class="slirn-stage6-list">{"".join(cards)}</div>
    {_stage_logs(s6)}
  </div>
</div>'''


_SV_STAGE_NAV = (
    ("stage1", "1", "选择源视频", "从任务视频中选择本项目的唯一源视频。", "stage1_source"),
    ("stage2", "2", "提取字幕", "运行 ASR 并生成后续解析所需字幕。", "stage2_extract"),
    ("stage3", "3", "解析短视频内容", "使用提示词和可选截图解析片段内容。", "stage3_analyze"),
    ("stage4", "4", "内容优化", "按标题让大模型切分长行、修改文字、删除冗余字幕行。", "stage4_optimize"),
    ("stage5", "5", "粗剪", "按解析结果生成候选短视频片段。", "stage5_coarse"),
    ("stage6", "6", "字幕优化", "优化字幕文本、断句和时间范围。", "stage6_refine"),
    ("stage7", "7", "精剪混编", "生成最终短视频文件并检查导出结果。", "stage7_finalize"),
)


def _stage_nav_state(status: object) -> str:
    value = str(status or "pending").lower()
    if value in ("done", "confirmed"):
        return "done"
    if value in ("running", "running_asr", "user_editing"):
        return "current"
    if value == "failed":
        return "failed"
    return "pending"


def render_project(project: dict, all_tasks: list[dict] | None = None,
                    *, repo_root: str | Path | None = None) -> str:
    task_id = str(project.get("task_id") or "")
    project_id = str(project.get("id") or "")
    pipeline = project.get("pipeline") or {}
    if repo_root is not None:
        # 注入到 project dict 副本（不污染原对象），下游 stage 渲染可读
        project = dict(project)
        project["_repo_root"] = str(repo_root)
    stage_states = {
        key: _stage_nav_state((pipeline.get(stage_key) or {}).get("status"))
        for key, _num, _title, _desc, stage_key in _SV_STAGE_NAV
    }
    focus_key = next(
        (key for key, _num, _title, _desc, _stage_key in _SV_STAGE_NAV
         if stage_states[key] != "done"),
        _SV_STAGE_NAV[-1][0],
    )
    nav_items = []
    for key, number, title, desc, _stage_key in _SV_STAGE_NAV:
        state = stage_states[key]
        mark = "✓" if state == "done" else ("!" if state == "failed" else number)
        active = key == focus_key
        nav_items.append(
            f'<button type="button" class="slirn-sv-stage-nav {state}'
            f'{" active" if active else ""}" data-action="sv-stage-nav" '
            f'data-stage-key="{esc(key)}" aria-current="{"step" if active else "false"}">'
            f'<span class="slirn-sv-stage-mark">{esc(mark)}</span>'
            f'<span class="slirn-sv-stage-copy"><strong>{esc(number)} · {esc(title)}</strong>'
            f'<small>{esc(desc)}</small></span></button>'
        )
    panes = (
        ("stage1", _stage1_select_source(project)),
        ("stage2", _stage2_extract(project)),
        ("stage3", _stage3_analyze(
            project,
            source_url=_source_video_url(project),
            srt_lines=(pipeline.get("stage3_analyze", {}) or {}).get("srt_lines") or [],
        )),
        ("stage4", _stage4_optimize(
            project, task_id=task_id, project_id=project_id,
            repo_root=project.get("_repo_root"),
        )),
        ("stage5", _stage5_coarse(project)),
        ("stage6", _stage6_refine(project)),
        ("stage7", _stage7_finalize(project)),
    )
    pane_html = "".join(
        f'<section class="slirn-sv-stage-pane{" active" if key == focus_key else ""}" '
        f'id="slirn-sv-stage-pane-{esc(key)}" data-stage-key="{esc(key)}">{content}</section>'
        for key, content in panes
    )
    return f'''<div id="slirn-short-video-inner" class="slirn-tab-inner"
  data-task-id="{esc(task_id)}" data-project-id="{esc(project_id)}">
  <div class="slirn-sv-header">
    <div>
      <h2>{esc(project.get("name"))}</h2>
      <p>任务 {esc(task_id)} · REQ-098 单源 AI 拆条</p>
    </div>
    <div class="slirn-sv-card-actions">
      <button class="slirn-btn" data-action="sv-back">返回项目列表</button>
      <button class="slirn-btn" data-action="sv-refresh">刷新状态</button>
    </div>
  </div>
  <div class="slirn-sv-pipeline">
    <aside class="slirn-sv-stages-card" aria-label="短视频阶段">
      <div class="slirn-sv-stages-title">阶段</div>
      <div class="slirn-sv-stage-nav-list">{"".join(nav_items)}</div>
    </aside>
    <div class="slirn-sv-stage-panes">{pane_html}</div>
  </div>
</div>'''
