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
      <label>模型<select id="slirn-stage2-model">
        <option value="paraformer">paraformer-zh（默认）</option>
        <option value="seaco_paraformer">seaco_paraformer（更准）</option>
        <option value="sensevoice">SenseVoiceSmall</option>
        </select></label>
      <label>语言<input id="slirn-stage2-lang" value="zh" placeholder="zh / en"></label>
      <label class="slirn-sv-wide">热词（可选）<input id="slirn-stage2-hotwords" placeholder="空格分隔，提升专有名词识别"></label>
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
        hl_rows.append(
            # 修订九：每条短视频包一层页签面板（tab bar 在列表头部），点页签切换显示
            f'<div class="slirn-stage3-hl-pane{" active" if is_first else ""}" data-hl-index="{idx}">'
            f'<div class="slirn-stage3-hl-row" data-hl-index="{idx}" data-sub-text="{esc(full_text)}">'
            f'<div class="slirn-stage3-hl-head">'
            f'<span class="slirn-sv-row-no">#{idx}</span>'
            f'<input type="text" class="slirn-stage3-hl-title" data-hl-field="title" value="{esc(title)}" placeholder="标题">'
            f'<span class="slirn-sv-muted slirn-stage3-hl-meta">{_format_ms(start)} - {_format_ms(end)} · 字幕合计 {total_ms / 1000:.0f}s · {sub_count} 行</span>'
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

    # 修订六：短视频生成卡 —— 每条（组）按其字幕行时间戳截取拼接成独立 mp4
    splice = project.get("pipeline", {}).get("stage3_splice") or {}
    can_splice = bool(hl_rows) and bool(source_url)
    splice_btn = (
        f'<button class="slirn-btn slirn-btn-primary" data-action="sv-stage3-splice" '
        f'title="按各条短视频当前字幕行的时间戳，从源视频逐行截取并拼接成独立视频">'
        f'✂️ 截取拼接（生成 {len(hl_rows)} 条）</button>'
        if can_splice else
        '<button class="slirn-btn slirn-btn-primary" data-action="sv-stage3-splice" disabled '
        'title="需先在 Stage 1 选源视频并完成 Stage 3 解析">✂️ 截取拼接</button>'
    )
    clip_items = []
    for c in splice.get("clips") or []:
        clip_items.append(
            f'<div class="slirn-stage3-clip-item">'
            f'<span class="slirn-sv-row-no">#{int(c.get("index") or 0)}</span>'
            f'<span class="slirn-stage3-clip-title">{esc(str(c.get("title") or ""))}</span>'
            f'<span class="slirn-sv-muted">{_duration_str(float(c.get("total_sec") or 0))}</span>'
            f'<button class="slirn-btn-mini slirn-btn-mini-primary" data-action="sv-preview" '
            f'data-url="{_file_url(task_id, project_id, "stage3", str(c.get("mp4") or ""))}" '
            f'data-name="短视频 #{c.get("index")} {c.get("title") or ""}">🎬 播放</button>'
            f'<button class="slirn-btn-mini" data-action="sv-preview-srt" '
            f'data-url="{_file_url(task_id, project_id, "stage3", str(c.get("srt") or ""))}">📖 字幕</button>'
            f'</div>'
        )
    splice_result = (
        f'<div class="slirn-stage3-clip-list">{"".join(clip_items)}</div>'
        if clip_items else ""
    )
    splice_note = ""
    if splice.get("status") == "failed" and splice.get("error"):
        splice_note = f'<div class="slirn-stage-error">{esc(splice["error"])}</div>'
    splice_card = (
        '<div class="slirn-stage3-splice-card">'
        f'<div class="slirn-stage3-splice-head"><strong>🎞 短视频截取拼接</strong>'
        f'{_stage_badge(splice.get("status"))}</div>'
        f'<div class="slirn-sv-muted">每组（一条短视频）按当前字幕行的时间戳从源视频'
        f'逐行截取并拼接为独立视频（行序即拼接顺序）</div>'
        f'<div class="slirn-stage3-splice-actions">{splice_btn}</div>'
        f'{splice_result}'
        f'{splice_note}'
        '</div>'
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
        placeholder="录入提取短视频内容所用的提示词；可「设为默认」或「存入列表」复用">{esc(initial_prompt)}</textarea>
    </label>
    <div class="slirn-stage3-prompt-toolbar">
      <div class="slirn-stage3-prompt-actions2">
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
    <div class="slirn-sv-form-grid">
      <label>片段数<input type="number" min="3" max="5" id="slirn-stage3-n" value="{n}"></label>
    </div>
    <div class="slirn-sv-card-actions">
      <button class="slirn-btn slirn-btn-primary" data-action="sv-stage3-run">🔍 解析</button>
      <span class="slirn-sv-muted">{int(s3.get("count") or 0)} 条内容片段</span>
      {prompt_badge}{img_used_badge}
    </div>
    <div class="slirn-stage3-hl-list">{hl_list}</div>
    {splice_card}
    {error_html}
    {_stage_logs(s3)}
  </div>
</div>'''


def _stage35_verify(project: dict) -> str:
    """REQ-20261004-verify：Stage 3.5 字幕一致性核验。

    仅生成报告，不修改任何数据。报告按 highlight 列出每条字幕行的状态：
    - ✅ ok（相似度 ≥ 0.7）— 与音频相符
    - ⚠️ partial（0.4-0.7）— 文本部分相符，建议核对
    - ❌ mismatch（< 0.4）— 文本错误，建议改用 ASR 文本
    - ❌ missing — 字幕找不到对应音频（可能切片错位或 funasr 漏识别）
    - 🆕 extra — ASR 中有但字幕没收录（建议补字幕）
    """
    s3v = project.get("pipeline", {}).get("stage3_verify", {}) or {}
    s3 = project.get("pipeline", {}).get("stage3_analyze", {}) or {}
    status = s3v.get("status") or "pending"
    hl_count = s3v.get("count") or 0
    hl_rows = s3v.get("highlights") or []
    summary = s3v.get("summary") or {}
    error_html = ""
    if status == "failed" and not hl_rows:
        err = s3v.get("error") or "核验失败"
        error_html = f'<div class="slirn-stage-error">{esc(err)}</div>'

    # 是否允许运行核验：Stage 3 必须 done 且至少有 1 条 highlight
    s3_done = s3.get("status") == "done"
    hl_list = s3.get("highlights") or []
    can_run = s3_done and bool(hl_list)
    btn_label = "🔍 重新核验" if status == "done" else "🔍 跑一致性核验"
    btn_class = "slirn-btn" if status == "done" else "slirn-btn slirn-btn-primary"
    disabled_attr = "" if can_run else " disabled title=\"请先完成 Stage 3 解析短视频内容\""
    run_btn = (
        f'<button class="{btn_class}" data-action="sv-stage3-verify-run"{disabled_attr}>'
        f'{btn_label}</button>'
    )

    # 报告摘要
    summary_html = ""
    if summary:
        summary_html = (
            f'<div class="slirn-stage35-summary">'
            f'<span class="slirn-sv-badge slirn-sv-badge-ok">✅ {summary.get("ok", 0)} 一致</span>'
            f'<span class="slirn-sv-badge slirn-sv-badge-warning">⚠️ {summary.get("warning", 0)} 部分</span>'
            f'<span class="slirn-sv-badge slirn-sv-badge-failed">❌ {summary.get("failed", 0)} 不符</span>'
            f'<span class="slirn-sv-badge slirn-sv-badge-muted">— {summary.get("skipped", 0)} 跳过</span>'
            f'</div>'
        )

    # 详细行：每条 highlight 一行
    detail_rows = []
    for hl in hl_rows:
        idx = hl.get("index") or len(detail_rows) + 1
        st = hl.get("status") or "pending"
        sub = hl.get("subtitle_count") or 0
        asr = hl.get("asr_count") or 0
        matched = hl.get("matched_count") or 0
        mismatched = hl.get("mismatched_count") or 0
        missing = hl.get("missing_in_audio") or 0
        extra = hl.get("extra_in_audio") or 0
        err = hl.get("error") or ""
        badge_cls = {
            "ok": "slirn-sv-badge-ok",
            "warning": "slirn-sv-badge-warning",
            "failed": "slirn-sv-badge-failed",
            "skipped": "slirn-sv-badge-muted",
        }.get(st, "slirn-sv-badge-muted")
        badge_text = {
            "ok": "✅ 一致",
            "warning": "⚠️ 部分",
            "failed": "❌ 不符",
            "skipped": "— 跳过",
        }.get(st, st)
        err_html = f'<div class="slirn-stage-error">{esc(err)}</div>' if err else ""
        detail_rows.append(
            f'<div class="slirn-stage35-row" data-clip-index="{idx}">'
            f'<div class="slirn-stage35-row-head">'
            f'<span class="slirn-sv-row-no">#{idx}</span>'
            f'<span class="slirn-sv-badge {badge_cls}">{badge_text}</span>'
            f'<span class="slirn-sv-muted">'
            f'字幕 {sub} 行 · ASR {asr} 行 · '
            f'一致 {matched} · 不符 {mismatched} · 缺失 {missing} · 多余 {extra}'
            f'</span>'
            f'</div>'
            f'{err_html}'
            f'</div>'
        )
    detail_html = "".join(detail_rows)

    return f'''<div class="slirn-stage" data-stage="3.5">
  <div class="slirn-stage-head">
    <span class="slirn-stage-num">3.5</span>
    <strong>字幕一致性核验</strong>
    {_stage_badge(status)}
    {_stage_elapsed(s3v)}
  </div>
  <div class="slirn-stage-body">
    <div class="slirn-sv-muted slirn-stage35-help">
      对 Stage 3 的每个 highlight 跑 ffmpeg 抽音频 + funasr 重 ASR，与现有字幕做相似度对比。
      <strong>仅生成报告</strong>，不修改任何数据；根据报告决定是否调整字幕文本或重新裁剪。
    </div>
    <div class="slirn-sv-card-actions">
      {run_btn}
      {summary_html}
    </div>
    <div class="slirn-stage35-detail">{detail_html}</div>
    {error_html}
    {_stage_logs(s3v)}
  </div>
</div>'''


def _stage4_coarse(project: dict) -> str:
    task_id = str(project.get("task_id") or "")
    project_id = str(project.get("id") or "")
    s4 = project.get("pipeline", {}).get("stage4_coarse", {}) or {}
    rows = []
    for hl in (s4.get("highlights") or []):
        idx = hl.get("index") or len(rows) + 1
        mp4_name = Path(str(hl.get("coarse_mp4") or "")).name
        url = _file_url(task_id, project_id, "stage4", mp4_name) if mp4_name else ""
        dur = float(hl.get("duration_sec") or 0)
        err = hl.get("error") or ""
        status = hl.get("status") or "pending"
        video = f'<video controls preload="metadata" src="{esc(url)}"></video>' if url else '<span class="slirn-sv-muted">未生成</span>'
        err_html = f'<div class="slirn-stage-error">{esc(err)}</div>' if err else ""
        rows.append(
            f'<div class="slirn-stage4-clip" data-clip-index="{idx}">'
            f'<div class="slirn-stage4-clip-head">'
            f'<span class="slirn-sv-row-no">#{idx}</span>'
            f'{_stage_badge(status)}'
            f'<span class="slirn-sv-muted">{dur:.1f}s</span>'
            f'<a class="slirn-btn-mini" href="{esc(url)}" download>下载</a>'
            f'</div>'
            f'{video}'
            f'{err_html}'
            f'</div>'
        )
    return f'''<div class="slirn-stage" data-stage="4">
  <div class="slirn-stage-head">
    <span class="slirn-stage-num">4</span>
    <strong>粗剪合成</strong>
    {_stage_badge(s4.get("status"))}
    {_stage_elapsed(s4)}
    <span class="slirn-sv-muted">{int(s4.get("count") or 0)} 条</span>
  </div>
  <div class="slirn-stage-body">
    <div class="slirn-sv-card-actions">
      <button class="slirn-btn slirn-btn-primary" data-action="sv-stage4-run">粗剪全部</button>
      <span class="slirn-sv-muted">每条 highlight 独立 mp4（不拼接）</span>
    </div>
    <div class="slirn-stage4-list">{"".join(rows) or '<div class="slirn-sv-muted">先完成 Stage 3 再粗剪</div>'}</div>
    {_stage_logs(s4)}
  </div>
</div>'''


def _stage5_refine(project: dict) -> str:
    task_id = str(project.get("task_id") or "")
    project_id = str(project.get("id") or "")
    s5 = project.get("pipeline", {}).get("stage5_refine", {}) or {}
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
            f'</div>'
            f'<textarea rows="6" class="slirn-stage5-srt" data-clip-index="{idx}" '
            f'placeholder="点击「重新 ASR」后再编辑">{srt_inline}</textarea>'
            f'{err_html}'
            f'</div>'
        )
    return f'''<div class="slirn-stage" data-stage="5">
  <div class="slirn-stage-head">
    <span class="slirn-stage-num">5</span>
    <strong>优化字幕</strong>
    {_stage_badge(s5.get("status"))}
  </div>
  <div class="slirn-stage-body">
    <div class="slirn-sv-muted">每条独立处理：先重新 ASR → 编辑文本 → 保存</div>
    <div class="slirn-stage5-list">{"".join(rows) or '<div class="slirn-sv-muted">先完成 Stage 4 粗剪</div>'}</div>
    {_stage_logs(s5)}
  </div>
</div>'''


def _stage6_finalize(project: dict) -> str:
    task_id = str(project.get("task_id") or "")
    project_id = str(project.get("id") or "")
    s6 = project.get("pipeline", {}).get("stage6_finalize", {}) or {}
    rows = []
    for hl in (s6.get("highlights") or []):
        idx = hl.get("index") or len(rows) + 1
        mp4_name = Path(str(hl.get("final_mp4") or "")).name
        srt_name = Path(str(hl.get("continuous_srt") or hl.get("refined_srt") or "")).name
        mp4_url = _file_url(task_id, project_id, "stage6", mp4_name) if mp4_name else ""
        srt_url = _file_url(task_id, project_id, "stage6", srt_name) if srt_name else ""
        dur = float(hl.get("duration_sec") or 0)
        status = hl.get("status") or "pending"
        err = hl.get("error") or ""
        video = f'<video controls preload="metadata" src="{esc(mp4_url)}"></video>' if mp4_url else '<span class="slirn-sv-muted">未生成</span>'
        err_html = f'<div class="slirn-stage-error">{esc(err)}</div>' if err else ""
        rows.append(
            f'<div class="slirn-stage6-clip" data-clip-index="{idx}">'
            f'<div class="slirn-stage6-clip-head">'
            f'<span class="slirn-sv-row-no">#{idx}</span>'
            f'{_stage_badge(status)}'
            f'<span class="slirn-sv-muted">{dur:.1f}s</span>'
            f'<a class="slirn-btn-mini" href="{esc(mp4_url)}" download>下载</a>'
            f'<button class="slirn-btn-mini" data-action="sv-preview-srt" data-url="{esc(srt_url)}">查看 srt</button>'
            f'</div>'
            f'{video}'
            f'{err_html}'
            f'</div>'
        )
    return f'''<div class="slirn-stage" data-stage="6">
  <div class="slirn-stage-head">
    <span class="slirn-stage-num">6</span>
    <strong>精剪混编</strong>
    {_stage_badge(s6.get("status"))}
    {_stage_elapsed(s6)}
  </div>
  <div class="slirn-stage-body">
    <div class="slirn-sv-card-actions">
      <button class="slirn-btn slirn-btn-primary" data-action="sv-stage6-run">精剪全部</button>
      <span class="slirn-sv-muted">每条 highlight 独立 mp4，与长视频产物完全隔离</span>
    </div>
    <div class="slirn-stage6-list">{"".join(rows) or '<div class="slirn-sv-muted">先完成 Stage 5</div>'}</div>
    {_stage_logs(s6)}
  </div>
</div>'''


_SV_STAGE_NAV = (
    ("stage1", "1", "选择源视频", "从任务视频中选择本项目的唯一源视频。", "stage1_source"),
    ("stage2", "2", "提取字幕", "运行 ASR 并生成后续解析所需字幕。", "stage2_extract"),
    ("stage3", "3", "解析短视频内容", "使用提示词和可选截图解析片段内容。", "stage3_analyze"),
    ("stage35", "3.5", "字幕一致性核验", "核对 AI 拆条字幕与视频音频是否一致。", "stage3_verify"),
    ("stage4", "4", "粗剪", "按解析结果生成候选短视频片段。", "stage4_coarse"),
    ("stage5", "5", "字幕优化", "优化字幕文本、断句和时间范围。", "stage5_refine"),
    ("stage6", "6", "精剪混编", "生成最终短视频文件并检查导出结果。", "stage6_finalize"),
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


def render_project(project: dict, all_tasks: list[dict] | None = None) -> str:
    task_id = str(project.get("task_id") or "")
    project_id = str(project.get("id") or "")
    pipeline = project.get("pipeline") or {}
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
        ("stage35", _stage35_verify(project)),
        ("stage4", _stage4_coarse(project)),
        ("stage5", _stage5_refine(project)),
        ("stage6", _stage6_finalize(project)),
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
