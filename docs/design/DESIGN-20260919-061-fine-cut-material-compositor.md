# DESIGN-20260919-061 — 精剪视频·四素材合成器（位置/缩放/字幕字体）

## Context

[REQ-20260919-061](../REQM/REQ-20260919-061-fine-cut-material-compositor.md)

精剪视频阶段是把 4 个素材（粗剪视频 + 字幕 + 封面 + 背景）合成 1 个视频。上游 FunClip 没这个能力，需要在 fork 里新建。

## 决策摘要（已确认）

| 项 | 选择 |
|---|---|
| 预览机制 | 按需生成（调整数值不渲染，点「🎬 生成预览」才渲染） |
| 参考图解析 | dashscope 多模态（Qwen-VL-Plus/Max），已配置即可用 |
| 字体设置 5 项 | 字体大小 + 描边 + 背景 + 粗细/对齐 + 字体本身 |
| 输出分辨率 | UI 下拉：1080p / 720p / 原始 |
| 预览长度 | 前 10 秒 |
| 默认字体 | 下拉：STHeitiMedium / 思源黑体 |
| 导出格式 | MP4 H.264 + AAC |

## ⚠️ 关键合成策略（用户 2026-09-19 补充）

> **视频不是铺满整个画布，只展现主要的授课内容区；右侧人员区是背景图片本就有的内容，会被视频"挡住"是不对的。**

**正确理解**：
- 视频（粗剪视频）只占画布**左侧**（默认 70% 宽度），用于显示主要授课画面
- 背景图的**右侧人员区**（讲师头像、角色图）应当**透过**显示，**不能被视频覆盖**
- 字幕（drawtext）只画在视频区域内，不能画到右侧背景区上

**Phase B 实现要点**：
- ffmpeg 滤镜图构造：背景图先 scale 到目标分辨率 → 视频 scale 到 `W * 0.7 : H * (0.7*视频实际比例)` → overlay 在 `0:0`（不覆盖右侧）
- 字幕 drawtext 的 `x` 限制在视频区域内（x ∈ [0, W*0.7]）
- video.layout.scale 默认值从 1.0 → **0.7**（占背景图宽度的 70%）
- 如果用户希望视频铺满可手动调大 scale 滑块（>0.95 即覆盖右侧）

## 数据模型（task.json 新增字段）

```json
{
  "fine_compose": {
    "materials": {
      "video":     {"path": "tasks/20260918-022/cut/rough.mp4",  "type": "video"},
      "subtitle":  {"path": "tasks/20260918-022/subtitle/opt.srt", "type": "srt"},
      "cover":     {"path": "tasks/20260918-022/upload/cover.jpg","type": "image"},
      "bg":        {"path": "tasks/20260918-022/upload/bg.png",   "type": "image"},
      "reference": {"path": "tasks/20260918-022/upload/ref.png",  "type": "image"}
    },
    "layout": {
      "video":    {"x": 0.0,  "y": 0.0,  "scale": 1.0, "enabled": true},
      "cover":    {"x": 0.05, "y": 0.85, "scale": 0.3, "enabled": true},
      "bg":       {"x": 0.0,  "y": 0.0,  "scale": 1.0, "enabled": false},
      "subtitle": {"x": 0.5,  "y": 0.9,  "scale": 1.0, "enabled": true}
    },
    "font": {
      "size":          36,
      "stroke_width":  2,
      "stroke_color":  "#000000",
      "bg_enabled":    false,
      "bg_color":      "#000000",
      "bg_opacity":    0.6,
      "bg_radius":     4,
      "bold":          true,
      "align":         "center",  // left/center/right
      "family":        "STHeitiMedium"  // 或 "Noto Sans CJK SC"
    },
    "output": {
      "resolution": "1080p",  // 720p / 1080p / source
      "codec":      "h264",
      "audio_codec": "aac"
    }
  }
}
```

**重要约束**：
- 位置 `x,y` 用**归一化坐标 0-1**（左下为原点）；ffmpeg 渲染时按目标分辨率乘
- 缩放 `scale` 0.1-2.0（1.0 = 原始大小）
- `enabled` 开关：video 和 subtitle 默认可关但**默认开**；cover/bg **默认关**

## API 端点

### 1. `POST /slirn/api/upload_fine_material`

multipart/form-data，参数：
- `task_id`: 任务 ID
- `kind`: video/subtitle/cover/bg/reference
- `file`: 上传文件

实现：
```python
@app.post("/slirn/api/upload_fine_material")
async def upload_fine_material(task_id: str, kind: str, file: UploadFile):
    safe_kind = kind if kind in {"video", "subtitle", "cover", "bg", "reference"} else "misc"
    save_dir = repo_root / "tasks" / task_id / "upload"
    save_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(file.filename).name  # 防路径穿越
    save_path = save_dir / f"{safe_kind}_{safe_name}"
    with save_path.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    # 写回 task.json
    t = mgr.get(task_id)
    fc = t.setdefault("fine_compose", {"materials": {}, "layout": {...}, "font": {...}, "output": {...}})
    fc["materials"][safe_kind] = {"path": str(save_path.relative_to(repo_root)), "type": _kind_type(safe_kind)}
    mgr.save(t)
    return {"ok": True, "path": fc["materials"][safe_kind]["path"]}
```

### 2. `POST /slirn/api/parse_reference_layout`

```python
@app.post("/slirn/api/parse_reference_layout")
async def parse_reference_layout(task_id: str):
    t = mgr.get(task_id)
    ref = (t.get("fine_compose", {}).get("materials", {}).get("reference") or {}).get("path")
    if not ref:
        return {"ok": False, "error": "未上传参考位置关系图"}
    ref_abs = repo_root / ref
    if not ref_abs.exists():
        return {"ok": False, "error": "参考图文件不存在"}

    # dashscope 多模态调用
    from dashscope import MultiModalConversation
    import base64
    b64 = base64.b64encode(ref_abs.read_bytes()).decode()
    messages = [{
        "role": "user",
        "content": [
            {"image": f"data:image/png;base64,{b64}"},
            {"text": _REFERENCE_LAYOUT_PROMPT}
        ]
    }]
    resp = MultiModalConversation.call(model="qwen-vl-plus", messages=messages)
    raw = resp["output"]["choices"][0]["message"]["content"]
    # 解析 JSON（容错：正则提取 { ... } 块）
    layout = _parse_layout_json(raw)
    # 写回 task.json
    fc = t.setdefault("fine_compose", {})
    fc.setdefault("layout", {})
    for key in ("video", "subtitle", "cover", "bg"):
        if key in layout:
            fc["layout"][key] = {
                "x": float(layout[key].get("x", 0.0)),
                "y": float(layout[key].get("y", 0.0)),
                "scale": float(layout[key].get("scale", 1.0)),
                "enabled": bool(layout[key].get("enabled", True))
            }
    mgr.save(t)
    return {"ok": True, "layout": fc["layout"]}
```

**Prompt**（[slirn_home/prompts/fine_cut_layout.py](slirn_home/prompts/fine_cut_layout.py)）：
```python
_REFERENCE_LAYOUT_PROMPT = """
你是视频素材布局助手。用户上传了一张参考位置关系图（草图/截图/示意图都可能），
里面展示了 4 个素材应该放在哪个相对位置：
- 粗剪视频（主视频，通常占中间大面积）
- 字幕（SRT 文件内嵌到视频上的文字）
- 封面图片（小图，叠加层）
- 背景图片（视频比例不一致时填充背景）

请分析图片，输出 4 个素材的位置和缩放（归一化坐标 0-1，原点在左下角）：

{
  "video":    {"x": 0.5, "y": 0.5, "scale": 1.0, "enabled": true},
  "subtitle": {"x": 0.5, "y": 0.9, "scale": 1.0, "enabled": true},
  "cover":    {"x": 0.1, "y": 0.9, "scale": 0.2, "enabled": true},
  "bg":       {"x": 0.5, "y": 0.5, "scale": 1.0, "enabled": false}
}

只返回 JSON，不要其他文字。如果图不清楚，scale 都填 1.0，enabled 根据可见性判断。
"""
```

### 3. `POST /slirn/api/render_fine_preview`

```python
@app.post("/slirn/api/render_fine_preview")
async def render_fine_preview(task_id: str):
    """渲染前 10 秒预览，返回 mp4 临时 URL。"""
    t = mgr.get(task_id)
    fc = t.get("fine_compose")
    if not fc or not fc.get("materials", {}).get("video"):
        return {"ok": False, "error": "未上传粗剪视频"}
    tmp_dir = repo_root / "tasks" / task_id / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    out_path = tmp_dir / "fine_preview.mp4"
    _render_fine_video(
        repo_root=repo_root,
        materials=fc["materials"],
        layout=fc.get("layout", {}),
        font=fc.get("font", {}),
        output=fc.get("output", {"resolution": "1080p", "codec": "h264"}),
        duration_sec=10,
        out_path=out_path,
    )
    rel = str(out_path.relative_to(repo_root))
    return {"ok": True, "url": f"/slirn/api/file?path={rel}&token=preview"}
```

### 4. `POST /slirn/api/export_fine_video`

```python
@app.post("/slirn/api/export_fine_video")
async def export_fine_video(task_id: str):
    """渲染完整视频，写到 tasks/{tid}/output/fine_compose.mp4。"""
    t = mgr.get(task_id)
    fc = t.get("fine_compose")
    if not fc or not fc.get("materials", {}).get("video"):
        return {"ok": False, "error": "未上传粗剪视频"}
    out_dir = repo_root / "tasks" / task_id / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"fine_compose_{int(time.time())}.mp4"
    _render_fine_video(
        repo_root=repo_root,
        materials=fc["materials"],
        layout=fc.get("layout", {}),
        font=fc.get("font", {}),
        output=fc.get("output", {"resolution": "1080p", "codec": "h264"}),
        duration_sec=None,  # None = 完整时长
        out_path=out_path,
    )
    return {"ok": True, "path": str(out_path.relative_to(repo_root))}
```

## ffmpeg 滤镜模板

### 核心函数：`_render_fine_video()`

```python
def _render_fine_video(repo_root, materials, layout, font, output, duration_sec, out_path):
    """构造 ffmpeg 滤镜图，渲染精剪视频。"""
    video = repo_root / materials["video"]["path"]
    srt = (repo_root / materials["subtitle"]["path"]) if materials.get("subtitle", {}).get("path") else None

    # 1. 解析输出分辨率
    out_res = _resolve_resolution(output.get("resolution", "1080p"), video)

    # 2. 构造 ffmpeg 命令
    cmd = ["ffmpeg", "-y", "-i", str(video)]

    # 3. 背景层（可选）
    filter_parts = []
    last = "[0:v]"
    if layout.get("bg", {}).get("enabled") and materials.get("bg", {}).get("path"):
        cmd += ["-i", str(repo_root / materials["bg"]["path"])]
        # bg 缩放到输出分辨率
        filter_parts.append(f"[1:v]scale={out_res[0]}:{out_res[1]}[bg]")
        last = "[bg]"
        bg_idx = 2
    else:
        bg_idx = None

    # 4. 视频层（缩放到输出分辨率 + 应用 layout.scale）
    bw = layout.get("bg", {}).get("enabled", False)
    # ...（按 enable 顺序构造 input 索引）

    # 5. overlay
    overlay_parts = []
    # bg overlay
    # video overlay
    # cover overlay（如果 enabled）

    # 6. drawtext 字幕
    if srt and layout.get("subtitle", {}).get("enabled", True):
        force_style = _build_force_style(font, out_res)
        filter_parts.append(f"{last}subtitles={srt}:force_style='{force_style}'[v]")
        last = "[v]"

    # 7. 组装
    filter_complex = ";\n".join(filter_parts + overlay_parts)

    cmd += ["-filter_complex", filter_complex, "-map", last, "-map", "0:a?",
            "-c:v", "libx264", "-crf", "23", "-c:a", "aac", "-b:a", "128k",
            "-t", str(duration_sec)]  # None 时不传 -t

    subprocess.run(cmd, check=True, capture_output=True)
```

### `_build_force_style(font, out_res)`

```python
def _build_force_style(font, out_res):
    """构造 ASS force_style 字符串（libass 兼容）。"""
    family = font.get("family", "STHeitiMedium")
    size = int(font.get("size", 36))
    align = {"left": "&L", "center": "&C", "right": "&R"}.get(font.get("align", "center"), "&C")
    parts = [
        f"FontName={family}",
        f"FontSize={size}",
        f"Alignment={_alignment_to_ass(font.get('align', 'center'))}",
    ]
    if font.get("bold"):
        parts.append("Bold=1")
    if font.get("stroke_width", 0) > 0:
        parts.append(f"BorderStyle=1")  # 1 = 描边+阴影
        parts.append(f"Outline={font['stroke_width']}")
        parts.append(f"OutlineColor={_hex_to_ass(font.get('stroke_color', '#000000'))}")
    if font.get("bg_enabled"):
        parts.append("BackColour=&H" + _hex_to_ass_bg(font.get("bg_color", "#000000"), font.get("bg_opacity", 0.6)))
    return ",".join(parts)
```

## wb 渲染：`_render_fine_cut_zone()`

```python
def _render_fine_cut_zone(task_id, t, mgr):
    fc = t.get("fine_compose", {})
    materials = fc.get("materials", {})
    layout = fc.get("layout", {})
    font = fc.get("font", {})
    output = fc.get("output", {"resolution": "1080p", "codec": "h264"})

    # 5 个素材上传控件
    upload_html = _render_upload_strip(materials)

    # 4 个素材的位置/缩放控件
    layout_html = _render_layout_controls(layout)

    # 字体 5 项 + 输出 3 项
    font_html = _render_font_controls(font, output)

    # AI 解析按钮
    ai_btn = '<button class="slirn-btn slirn-btn-primary" data-action="fine-ai-parse" data-task-id="{tid}">🤖 AI 智能布局（需参考图）</button>'

    # 预览/导出按钮
    preview_btn = '<button class="slirn-btn" data-action="fine-preview" data-task-id="{tid}">🎬 生成预览（前 10 秒）</button>'
    export_btn = '<button class="slirn-btn slirn-btn-primary" data-action="fine-export" data-task-id="{tid}">💾 导出最终视频</button>'

    # 预览视频框
    preview_box = '<div class="slirn-fine-preview" id="slirn-fine-preview"><div class="slirn-fine-preview-empty">尚未生成预览</div></div>'

    return f"""
<div class="slirn-wb-pane-card">
  <div class="slirn-wb-pane-title">🎬 精剪视频 · 素材合成器</div>
  {upload_html}
  <div class="slirn-form-hint">💡 提示：上传 5 个素材后，点「AI 智能布局」自动生成位置；可手动微调每个素材的 X/Y/缩放；调整完后点「生成预览」看效果。</div>
  {ai_btn}
  {preview_btn}
  {export_btn}
  {preview_box}
  {layout_html}
  {font_html}
</div>
"""
```

**关键**：每个滑块加 `data-action="fine-layout-set"` `data-key="cover.x"` 等属性，自动 debounce 300ms 触发 `PUT /slirn/api/save_fine_layout`。

## 前端 handler（router.js）

```javascript
// 上传
function fineUpload(btn, kind) {
  var file = btn.previousElementSibling.files[0];
  if (!file) return;
  var fd = new FormData();
  fd.append('kind', kind);
  fd.append('file', file);
  fetch('/slirn/api/upload_fine_material?task_id=' + currentTid, { method: 'POST', body: fd })
    .then(r => r.json()).then(j => { if (j.ok) location.reload(); else toast(j.error); });
}

// AI 解析
function fineAiParse(btn) {
  btn.disabled = true;
  btn.textContent = '🤖 解析中...';
  fetch('/slirn/api/parse_reference_layout?task_id=' + currentTid, { method: 'POST' })
    .then(r => r.json()).then(j => {
      btn.disabled = false;
      btn.textContent = '🤖 AI 智能布局';
      if (j.ok) { applyLayoutToInputs(j.layout); toast('✅ AI 已生成布局'); }
      else toast('❌ ' + j.error);
    });
}

// 滑块（自动持久化 + 防抖）
function bindFineSliders() {
  document.querySelectorAll('.slirn-fine-slider').forEach(function(slider) {
    var t = null;
    slider.addEventListener('input', function() {
      // 同步数值显示
      var valSpan = document.getElementById(slider.id + '_val');
      if (valSpan) valSpan.textContent = slider.value;
      // 防抖 300ms 保存
      if (t) clearTimeout(t);
      t = setTimeout(function() { fineSaveLayout(); }, 300);
    });
  });
}
function fineSaveLayout() {
  var data = {};
  document.querySelectorAll('.slirn-fine-slider').forEach(function(s) {
    var key = s.dataset.key;  // "cover.x" 等
    var parts = key.split('.');
    var k1 = parts[0], k2 = parts[1];
    data[k1] = data[k1] || {};
    data[k1][k2] = parseFloat(s.value);
  });
  // 同时收集 enabled checkbox
  document.querySelectorAll('.slirn-fine-enabled').forEach(function(c) {
    var key = c.dataset.key;
    data[key] = data[key] || {};
    data[key].enabled = c.checked;
  });
  fetch('/slirn/api/save_fine_layout?task_id=' + currentTid, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ layout: data })
  });
}

// 预览
function finePreview(btn) {
  btn.disabled = true;
  btn.textContent = '🎬 渲染中（可能 5-10 秒）...';
  fetch('/slirn/api/render_fine_preview?task_id=' + currentTid, { method: 'POST' })
    .then(r => r.json()).then(j => {
      btn.disabled = false;
      btn.textContent = '🎬 生成预览（前 10 秒）';
      if (j.ok) {
        var box = document.getElementById('slirn-fine-preview');
        box.innerHTML = '<video controls src="' + j.url + '" style="max-width:100%;"></video>';
      } else toast('❌ ' + j.error);
    });
}

// 导出
function fineExport(btn) {
  btn.disabled = true;
  btn.textContent = '💾 导出中（可能 1-3 分钟）...';
  fetch('/slirn/api/export_fine_video?task_id=' + currentTid, { method: 'POST' })
    .then(r => r.json()).then(j => {
      btn.disabled = false;
      btn.textContent = '💾 导出最终视频';
      if (j.ok) toast('✅ 已导出：' + j.path);
      else toast('❌ ' + j.error);
    });
}
```

## CSS 样式

```css
/* REQ-20260919-061：fine_cut pane */
.slirn-fine-uploads { display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; }
.slirn-fine-upload-card { padding: 10px; border: 1px dashed var(--border); border-radius: 8px; }
.slirn-fine-upload-card.has-file { border-style: solid; background: rgba(34, 197, 94, 0.08); }
.slirn-fine-layout-row { display: grid; grid-template-columns: 80px 1fr 80px; gap: 8px; align-items: center; }
.slirn-fine-layout-row .slirn-fine-slider { width: 100%; }
.slirn-fine-font-row { display: grid; grid-template-columns: 120px 1fr; gap: 8px; align-items: center; }
.slirn-fine-preview { margin: 12px 0; padding: 12px; background: #000; border-radius: 8px; min-height: 200px; }
.slirn-fine-preview-empty { color: #999; text-align: center; padding: 60px 0; }
```

## 关键文件清单

| 文件 | 改动 |
|---|---|
| [slirn_home/app.py](slirn_home/app.py) | 新增 `_render_fine_cut_zone`、`_render_fine_video`、`_build_force_style`、`_resolve_resolution` 函数 + 4 个 API endpoint |
| [slirn_home/app.py:1521](slirn_home/app.py#L1521) | `panes["fine_cut"]` 注册到 `_render_workbench` |
| [slirn_home/static/router.js](slirn_home/static/router.js) | `fineUpload`/`fineAiParse`/`bindFineSliders`/`fineSaveLayout`/`finePreview`/`fineExport` + 委托 |
| [slirn_home/static/home.css](slirn_home/static/home.css) | fine_cut pane 样式 |
| [slirn_home/prompts/fine_cut_layout.py](slirn_home/prompts/fine_cut_layout.py)（新建）| `_REFERENCE_LAYOUT_PROMPT` |
| [tests/test_fine_cut.py](tests/test_fine_cut.py)（新建） | roundtrip + layout 解析 + ffmpeg 滤镜构造（mock subprocess） |

## 验证

### 静态
- `node --check router.js`
- `python -c "import ast; ast.parse(open('slirn_home/app.py', encoding='utf-8').read())"`
- `pytest tests/test_fine_cut.py -v`（新文件）

### E2E（CDP + 真机）
1. 进任务 → 切到「精剪视频」pane
2. 上传 5 个素材（mp4 / srt / cover.jpg / bg.png / reference.png）
3. 点「🤖 AI 智能布局」→ 调 Qwen-VL → 4 个素材位置自动填入
4. 拖动 X/Y/缩放滑块 → 数值实时变但**不渲染**
5. 点「🎬 生成预览」→ 等 5-10s → 显示视频预览框
6. 验证字幕样式：字体大小/描边/背景/粗体对齐可见
7. 点「💾 导出最终视频」→ 等 1-3 分钟 → 输出到 `tasks/{tid}/output/`
8. 关闭浏览器 → 重开 → 所有数值保留
9. ffmpeg 路径检查（缺失时按钮置灰）
10. dashscope key 检查（缺失时 AI 按钮置灰）

### 真机手测
- 浏览器硬刷 → pane 显示
- 真实上传 5 个文件 → 看到「✅ 已上传」反馈
- AI 解析 toast 提示
- 预览视频可在面板播放
- 字体大小/描边肉眼可见效果

## 风险与处理（同 REQ）

详见 [REQ 风险表](../REQM/REQ-20260919-061-fine-cut-material-compositor.md#风险与处理)

## 不做的事
- ❌ 不做实时拖动预览（按需机制）
- ❌ 不做模板预设（用户没要）
- ❌ 不做多字幕轨道
- ❌ 不做音频混音
- ❌ 不改 `funclip/` 上游
- ❌ 不做模板市场/分享

## 实施顺序
1. 后端：upload API + parse API + render API + ffmpeg 滤镜构造
2. wb 渲染：`_render_fine_cut_zone`
3. 前端：上传/AI/滑块/预览/导出 5 个 handler
4. CSS：fine_cut pane 样式
5. 测试：mock subprocess + roundtrip
6. 集成测试：用真实视频短片（5-10 秒测试视频）走完整流程