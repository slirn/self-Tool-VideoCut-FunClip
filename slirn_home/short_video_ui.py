"""短视频混剪页面的服务端 HTML 渲染。"""

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


def render_project_list(tasks: list[dict], projects: list[dict]) -> str:
    task_options = "".join(
        f'<option value="{esc(t.get("task_id"))}">{esc(t.get("task_id"))} · {esc(t.get("name"))}</option>'
        for t in tasks
    )
    cards = []
    for p in projects:
        card = (
            f'<div class="slirn-sv-card" data-project-id="{esc(p.get("id"))}">'
            f'<div class="slirn-sv-card-head"><strong>{esc(p.get("name"))}</strong>'
            f'<span class="slirn-sv-badge">{esc(p.get("storyboard", {}).get("status") or "empty")}</span></div>'
            f'<div class="slirn-sv-muted">任务 {esc(p.get("task_id"))} · 素材 {len(p.get("materials") or [])} · '
            f'版本 {len((p.get("storyboard") or {}).get("variants") or [])}</div>'
            f'<div class="slirn-sv-card-actions">'
            f'<button class="slirn-btn slirn-btn-primary" data-action="sv-open" '
            f'data-task-id="{esc(p.get("task_id"))}" data-project-id="{esc(p.get("id"))}">打开</button>'
            f'<button class="slirn-btn slirn-btn-danger" data-action="sv-delete" '
            f'data-task-id="{esc(p.get("task_id"))}" data-project-id="{esc(p.get("id"))}">删除</button>'
            f'</div></div>'
        )
        cards.append(card)
    if not cards:
        cards.append('<div class="slirn-empty"><div class="slirn-empty-text">还没有短视频项目</div></div>')
    return f'''<div id="slirn-short-video-inner" class="slirn-tab-inner">
  <div class="slirn-sv-header">
    <div><h2>短视频多素材混剪</h2><p>任务素材 + B-roll + 标题 / Hook / 字幕 / BGM，输出 9:16 多版本。</p></div>
    <button class="slirn-btn" data-action="sv-refresh">刷新</button>
  </div>
  <div class="slirn-sv-create">
    <div class="slirn-sv-section-title">创建项目</div>
    <div class="slirn-sv-form-grid">
      <label>来源任务<select id="slirn-sv-create-task">{task_options}</select></label>
      <label>项目名<input id="slirn-sv-create-name" placeholder="例如：产品种草混剪"></label>
      <label class="slirn-sv-wide">创作 Brief<textarea id="slirn-sv-create-brief" rows="3" placeholder="说明受众、卖点、语气、必须出现的素材和行动号召"></textarea></label>
      <label class="slirn-sv-toggle">
        <input type="checkbox" id="slirn-sv-create-llm" checked>
        <span class="slirn-sv-toggle-track"></span>
        <span>允许调用外部 LLM</span>
      </label>
      <button class="slirn-btn slirn-btn-primary slirn-sv-create-btn" data-action="sv-create">创建项目</button>
    </div>
  </div>
  <div class="slirn-sv-section-title">项目列表</div>
  <div class="slirn-sv-grid">{"".join(cards)}</div>
</div>'''


def _material_options(materials: list[dict], kind: str, selected: str = "") -> str:
    rows = []
    for material in materials:
        if material.get("kind") != kind:
            continue
        value = str(material.get("id") or "")
        label = f'{material.get("name")} · {kind}'
        sel = " selected" if value == selected else ""
        rows.append(f'<option value="{esc(value)}"{sel}>{esc(label)}</option>')
    if not rows:
        rows.append('<option value="">（无可用素材）</option>')
    return "".join(rows)


def _segment_rows(project: dict, variant: dict) -> str:
    rows = []
    for i, seg in enumerate(variant.get("segments") or []):
        rows.append(
            '<div class="slirn-sv-row slirn-sv-segment" data-seg-index="%d">'
            '<span class="slirn-sv-row-no">%d</span>'
            '<select data-sv-field="material_id">%s</select>'
            '<input type="number" min="0" step="0.1" data-sv-field="start" value="%s" title="起点秒">'
            '<input type="number" min="0.5" step="0.1" data-sv-field="duration" value="%s" title="时长秒">'
            '<select data-sv-field="transition">%s</select>'
            '<button class="slirn-btn-mini slirn-btn-mini-danger" data-action="sv-remove-segment">删</button>'
            '</div>' % (
                i, i + 1,
                _material_options(project.get("materials") or [], "video", seg.get("material_id")),
                esc(seg.get("start")),
                esc(seg.get("duration")),
                _transition_options(seg.get("transition") or "fade"),
            )
        )
    return "".join(rows)


def _transition_options(selected: str) -> str:
    options = [
        ("fade", "淡入淡出"),
        ("wipeleft", "左擦除"),
        ("wiperight", "右擦除"),
        ("slideup", "上滑"),
        ("slidedown", "下滑"),
        ("circleopen", "圆形展开"),
        ("none", "无转场"),
    ]
    return "".join(
        f'<option value="{v}"{" selected" if selected == v else ""}>{label}</option>'
        for v, label in options
    )


def _overlay_rows(project: dict, variant: dict) -> str:
    rows = []
    materials = [
        m for m in project.get("materials") or []
        if m.get("kind") in ("video", "image")
    ]
    for i, ov in enumerate(variant.get("overlays") or []):
        opts = []
        for m in materials:
            value = str(m.get("id") or "")
            sel = " selected" if value == ov.get("material_id") else ""
            opts.append(
                f'<option value="{esc(value)}"{sel}>{esc(m.get("name"))} · {esc(m.get("kind"))}</option>'
            )
        rows.append(
            '<div class="slirn-sv-row slirn-sv-overlay" data-overlay-index="%d">'
            '<span class="slirn-sv-row-no">B%d</span>'
            '<select data-sv-field="material_id">%s</select>'
            '<input type="number" min="0" step="0.1" data-sv-field="start" value="%s" title="起点秒">'
            '<input type="number" min="0.5" step="0.1" data-sv-field="duration" value="%s" title="时长秒">'
            '<input type="number" data-sv-field="x" value="%s" title="X">'
            '<input type="number" data-sv-field="y" value="%s" title="Y">'
            '<input type="number" min="0.05" step="0.01" data-sv-field="scale" value="%s" title="缩放">'
            '<button class="slirn-btn-mini slirn-btn-mini-danger" data-action="sv-remove-overlay">删</button>'
            '</div>' % (
                i, i + 1, "".join(opts) or '<option value="">（无素材）</option>',
                esc(ov.get("start")), esc(ov.get("duration")),
                esc(ov.get("x")), esc(ov.get("y")), esc(ov.get("scale")),
            )
        )
    return "".join(rows)


def _subtitle_rows(variant: dict) -> str:
    rows = []
    for i, sub in enumerate(variant.get("subtitles") or []):
        rows.append(
            '<div class="slirn-sv-row slirn-sv-subtitle" data-subtitle-index="%d">'
            '<span class="slirn-sv-row-no">字%d</span>'
            '<input type="number" min="0" step="0.1" data-sv-field="start" value="%s">'
            '<input type="number" min="0.1" step="0.1" data-sv-field="end" value="%s">'
            '<input type="text" data-sv-field="text" value="%s" placeholder="字幕文本">'
            '<button class="slirn-btn-mini slirn-btn-mini-danger" data-action="sv-remove-subtitle">删</button>'
            '</div>' % (
                i, i + 1, esc(sub.get("start")), esc(sub.get("end")), esc(sub.get("text")),
            )
        )
    return "".join(rows)


def _renders_for_variant(project: dict, variant_id: str) -> str:
    cards = []
    for job in project.get("render_jobs") or []:
        for out in job.get("outputs") or []:
            if out.get("variant_id") != variant_id:
                continue
            path = Path(str(out.get("path") or ""))
            url = _file_url(
                str(project.get("task_id") or ""),
                str(project.get("id") or ""),
                "output",
                path.name,
            )
            cards.append(
                f'<div class="slirn-sv-output"><video controls src="{esc(url)}"></video>'
                f'<a class="slirn-btn slirn-btn-sm" href="{esc(url)}" download>下载</a></div>'
            )
    return "".join(cards)


def render_project(project: dict, all_tasks: list[dict] | None = None) -> str:
    task_id = str(project.get("task_id") or "")
    project_id = str(project.get("id") or "")
    cfg = project.get("config") or {}
    materials = project.get("materials") or []
    material_cards = []
    for m in materials:
        url = _file_url(task_id, project_id, "asset", Path(str(m.get("path") or "")).name)
        material_cards.append(
            '<div class="slirn-sv-material">'
            f'<div><strong>{esc(m.get("name"))}</strong><span>{esc(m.get("kind"))}</span></div>'
            f'<div class="slirn-sv-muted">{float(m.get("duration") or 0):.1f}s · '
            f'{esc(m.get("width") or 0)}x{esc(m.get("height") or 0)} · {esc(m.get("source"))}</div>'
            f'<a class="slirn-btn-mini" href="{esc(url)}" target="_blank">查看</a>'
            '</div>'
        )
    variants_html = []
    for variant in (project.get("storyboard") or {}).get("variants") or []:
        vid = str(variant.get("id") or "")
        bgm_opts = _material_options(materials, "audio", str(variant.get("bgm_material_id") or ""))
        variants_html.append(f'''<div class="slirn-sv-variant" data-variant-id="{esc(vid)}">
  <div class="slirn-sv-variant-head">
    <strong>{esc(variant.get("name"))}</strong>
    <button class="slirn-btn slirn-btn-primary" data-action="sv-render" data-variant-id="{esc(vid)}">渲染此版本</button>
  </div>
  <div class="slirn-sv-form-grid">
    <label>标题<input data-sv-field="title" value="{esc(variant.get("title"))}"></label>
    <label>Hook<input data-sv-field="hook" value="{esc(variant.get("hook"))}"></label>
    <label>CTA<input data-sv-field="cta" value="{esc(variant.get("cta"))}"></label>
    <label>BGM 音量 dB<input type="number" data-sv-field="bgm_volume_db" min="-40" max="0" value="{esc(variant.get("bgm_volume_db"))}"></label>
    <label class="slirn-sv-wide">BGM 素材<select data-sv-field="bgm_material_id"><option value="">（无 BGM）</option>{bgm_opts}</select></label>
  </div>
  <div class="slirn-sv-subsection">
    <div class="slirn-sv-subtitle-row"><strong>主视频片段</strong><button class="slirn-btn-mini" data-action="sv-add-segment">加片段</button></div>
    <div class="slirn-sv-rows">{_segment_rows(project, variant)}</div>
  </div>
  <div class="slirn-sv-subsection">
    <div class="slirn-sv-subtitle-row"><strong>B-roll 叠加</strong><button class="slirn-btn-mini" data-action="sv-add-overlay">加 B-roll</button></div>
    <div class="slirn-sv-rows">{_overlay_rows(project, variant)}</div>
  </div>
  <div class="slirn-sv-subsection">
    <div class="slirn-sv-subtitle-row"><strong>字幕</strong><button class="slirn-btn-mini" data-action="sv-add-subtitle">加字幕</button></div>
    <div class="slirn-sv-rows">{_subtitle_rows(variant)}</div>
  </div>
  <div class="slirn-sv-outputs">{_renders_for_variant(project, vid)}</div>
</div>''')
    if not variants_html:
        variants_html.append(
            '<div class="slirn-empty"><div class="slirn-empty-text">尚未生成分镜，请先点击“AI 生成三版分镜”</div></div>'
        )
    return f'''<div id="slirn-short-video-inner" class="slirn-tab-inner"
  data-task-id="{esc(task_id)}" data-project-id="{esc(project_id)}">
  <div class="slirn-sv-header">
    <div><h2>{esc(project.get("name"))}</h2><p>任务 {esc(task_id)} · 模型 {esc((project.get("storyboard") or {}).get("model") or "未生成")}</p></div>
    <div class="slirn-sv-card-actions">
      <button class="slirn-btn" data-action="sv-back">返回项目列表</button>
      <button class="slirn-btn slirn-btn-primary" data-action="sv-save">保存全部调整</button>
    </div>
  </div>
  <div class="slirn-sv-form-grid slirn-sv-config">
    <label>画布宽<input type="number" data-sv-config="width" value="{esc(cfg.get("width"))}"></label>
    <label>画布高<input type="number" data-sv-config="height" value="{esc(cfg.get("height"))}"></label>
    <label>版本数<input type="number" min="1" max="5" data-sv-config="variants" value="{esc(cfg.get("variants"))}"></label>
    <label>最短秒<input type="number" min="5" data-sv-config="duration_min" value="{esc(cfg.get("duration_min"))}"></label>
    <label>最长秒<input type="number" min="5" data-sv-config="duration_max" value="{esc(cfg.get("duration_max"))}"></label>
    <label>转场时长<input type="number" min="0.1" max="2" step="0.1" data-sv-config="transition_duration" value="{esc(cfg.get("transition_duration"))}"></label>
    <label class="slirn-sv-toggle">
      <input type="checkbox" data-sv-config="allow_external_llm" {"checked" if cfg.get("allow_external_llm") else ""}>
      <span class="slirn-sv-toggle-track"></span>
      <span>允许调用外部 LLM</span>
    </label>
  </div>
  <div class="slirn-sv-section">
    <div class="slirn-sv-section-title">素材库</div>
    <div class="slirn-sv-upload">
      <select id="slirn-sv-upload-kind"><option value="auto">自动识别</option><option value="video">视频</option><option value="image">图片</option><option value="audio">音频</option></select>
      <input type="file" id="slirn-sv-upload-file" accept="video/*,image/*,audio/*">
      <button class="slirn-btn" data-action="sv-upload">上传素材</button>
    </div>
    <div class="slirn-sv-materials">{"".join(material_cards) or '<div class="slirn-sv-muted">暂无素材</div>'}</div>
  </div>
  <div class="slirn-sv-section">
    <div class="slirn-sv-section-title">创作 Brief</div>
    <textarea id="slirn-sv-brief" rows="4" placeholder="受众、卖点、语气、需要强调的素材、行动号召">{esc(project.get("brief"))}</textarea>
    <div class="slirn-sv-card-actions">
      <button class="slirn-btn slirn-btn-primary" data-action="sv-ai">AI 生成三版分镜</button>
      <span class="slirn-sv-muted">外部 LLM 失败时自动使用本地规则生成可编辑分镜。</span>
    </div>
  </div>
  <div class="slirn-sv-section">
    <div class="slirn-sv-section-title">版本编辑与渲染</div>
    <div class="slirn-sv-status" id="slirn-sv-render-status" hidden></div>
    {"".join(variants_html)}
  </div>
</div>'''
