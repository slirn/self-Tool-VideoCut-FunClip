"""短视频单源 AI 拆条工作台的服务端 HTML 渲染。

REQ-20261003-098：6 阶段管线（选源 → 抽字幕 → AI 拆条 → 粗剪 → 字幕优化 → 精剪混编）。
"""

from __future__ import annotations

import html
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


# ---------- 项目列表（保持兼容） ----------

def render_project_list(tasks: list[dict], projects: list[dict]) -> str:
    task_options = "".join(
        f'<option value="{esc(t.get("task_id"))}">{esc(t.get("task_id"))} · {esc(t.get("name"))}</option>'
        for t in tasks
    )
    cards = []
    for p in projects:
        kind = str(p.get("kind") or "mixcut")
        kind_label = "AI 拆条" if kind == "split" else "多素材混剪（旧）"
        cards.append(
            f'<div class="slirn-sv-card" data-project-id="{esc(p.get("id"))}">'
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
    return f'''<div id="slirn-short-video-inner" class="slirn-tab-inner">
  <div class="slirn-sv-header">
    <div><h2>单源 AI 短视频内容拆条</h2><p>从一段完整视频出发，AI 通过字幕重组+重排序产生 3-5 个独立短视频。</p></div>
    <button class="slirn-btn" data-action="sv-refresh">刷新</button>
  </div>
  <div class="slirn-sv-create">
    <div class="slirn-sv-section-title">创建项目</div>
    <div class="slirn-sv-form-grid">
      <label>来源任务<select id="slirn-sv-create-task">{task_options}</select></label>
      <label>项目名<input id="slirn-sv-create-name" placeholder="例如：公开课拆条"></label>
      <button class="slirn-btn slirn-btn-primary slirn-sv-create-btn" data-action="sv-create">创建项目</button>
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


def _stage3_analyze(project: dict) -> str:
    s3 = project.get("pipeline", {}).get("stage3_analyze", {}) or {}
    n = int(s3.get("n_clips") or 4)
    template = str(s3.get("template") or "hook_first")
    templates = [
        ("hook_first", "钩子优先"),
        ("topic_cluster", "主题聚类"),
        ("story_arc", "起承转合"),
    ]
    template_radios = "".join(
        f'<label class="slirn-stage3-radio">'
        f'<input type="radio" name="sv-stage3-template" value="{v}"{" checked" if template == v else ""}>'
        f'<span>{label}</span></label>'
        for v, label in templates
    )
    error_html = ""
    if s3.get("status") == "failed":
        err = s3.get("error") or "失败"
        error_html = f'<div class="slirn-stage-error">{esc(err)}</div>'

    # 高亮条目可编辑标题 + 删除 + 完整字幕预览（REQ-20261004-UX：用户要直接看到全部文字）
    hl_rows = []
    for hl in (s3.get("highlights") or []):
        idx = hl.get("index") or len(hl_rows) + 1
        title = hl.get("title") or f"片段 #{idx}"
        start = hl.get("start_ms") or 0
        end = hl.get("end_ms") or 0
        dur = (end - start) / 1000.0
        sub_lines = hl.get("subtitle_lines") or []
        sub_count = len(sub_lines)
        # 字幕预览：渲染全部行（不再限制 5 行）；每行 `<src_index> 文本`；
        # 同时把完整字幕文本塞到 data-sub-text 供「复制」按钮用
        full_text = "\n".join(
            f"[{int(sl.get('src_index') or 0)}] {str(sl.get('text') or '')}"
            for sl in sub_lines
        )
        preview_items = []
        for sl in sub_lines:
            t = esc(str(sl.get("text") or ""))
            src = int(sl.get("src_index") or 0)
            preview_items.append(
                f'<div class="slirn-stage3-hl-sub"><span class="slirn-stage3-hl-src">[{src}]</span> {t}</div>'
            )
        sub_preview = "".join(preview_items) or '<div class="slirn-sv-muted">无字幕</div>'
        hl_rows.append(
            f'<div class="slirn-stage3-hl-row" data-hl-index="{idx}" data-sub-text="{esc(full_text)}">'
            f'<div class="slirn-stage3-hl-head">'
            f'<span class="slirn-sv-row-no">#{idx}</span>'
            f'<input type="text" class="slirn-stage3-hl-title" data-hl-field="title" value="{esc(title)}" placeholder="标题">'
            f'<span class="slirn-sv-muted slirn-stage3-hl-meta">{_format_ms(start)} - {_format_ms(end)} · {dur:.1f}s · {sub_count} 字幕行</span>'
            f'<button class="slirn-btn-mini slirn-btn-mini-danger" data-action="sv-stage3-remove-hl" '
            f'data-hl-index="{idx}">删</button>'
            f'</div>'
            f'<div class="slirn-stage3-hl-preview">{sub_preview}</div>'
            f'</div>'
        )
    hl_list = "".join(hl_rows) or '<div class="slirn-sv-muted">运行后将列出 3-5 条 highlights</div>'

    return f'''<div class="slirn-stage" data-stage="3">
  <div class="slirn-stage-head">
    <span class="slirn-stage-num">3</span>
    <strong>AI 拆条</strong>
    {_stage_badge(s3.get("status"))}
    {_stage_elapsed(s3)}
  </div>
  <div class="slirn-stage-body">
    <div class="slirn-sv-form-grid">
      <label class="slirn-sv-wide">模板
        <div class="slirn-stage3-radios">{template_radios}</div>
      </label>
      <label>片段数<input type="number" min="3" max="5" id="slirn-stage3-n" value="{n}"></label>
    </div>
    <div class="slirn-sv-card-actions">
      <button class="slirn-btn slirn-btn-primary" data-action="sv-stage3-run">AI 拆条</button>
      <span class="slirn-sv-muted">{int(s3.get("count") or 0)} 条 highlights</span>
    </div>
    <div class="slirn-stage3-hl-list">{hl_list}</div>
    {error_html}
    {_stage_logs(s3)}
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


def render_project(project: dict, all_tasks: list[dict] | None = None) -> str:
    task_id = str(project.get("task_id") or "")
    project_id = str(project.get("id") or "")
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
  {_stage1_select_source(project)}
  {_stage2_extract(project)}
  {_stage3_analyze(project)}
  {_stage4_coarse(project)}
  {_stage5_refine(project)}
  {_stage6_finalize(project)}
</div>'''