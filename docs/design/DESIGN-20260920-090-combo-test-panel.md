# DESIGN-20260920-090 合成元素组合测试面板（debug）

## 目标

实现「5-checkbox 调试面板 + 诊断端点 + 一键合成测试 + ffprobe 检测」完整闭环，让用户能**逐步勾选组合验证 BGM 是否存在**。

## 关键决策

### D1：diagnose 端点用纯函数而非复用 `_assemble_fine_filter`

**理由**：`_assemble_fine_filter` 会在内部创建临时文件（subtitle offset 调整 + bg/cover prescale）。diagnose 是高频调试入口，跑临时文件开销过大，且可能在 fc 没上传完整素材时报错。

**做法**：抽出 `_predict_audio_path(fc)` 纯函数，复刻 `_assemble_fine_filter` 的 inputs_count + audio_idx + filter_complex 计算逻辑，不创建临时文件。

### D2：测试面板放在 `_render_fine_cut_zone` 顶部（detail 折叠块）

**理由**：
- 用户明确要求工作台顶部独立 panel
- 「跨任务可用」由 details 折叠 + 默认展开保证
- 复用现有 `<details class="slirn-fine-section">` 模式（REQ-075 已建立的视觉一致性）
- 顶部位置在 `{combined_actions_bar}` 之后、`{preview_box}` 之前（line 3558）

### D3：snapshot 用前端 state 而非后端 session

**理由**：
- snapshot 仅用于「立刻还原」，不需要持久化
- 刷新页面后用户会从 fc 重新加载，无需保留 snapshot
- 前端 state 实现简单：apply 前用 `window._comboSnapshot` 保存原 5 个 enabled

### D4：probe_output_audio 用 ffprobe volumedetect

**理由**：
- BGM 是否存在的客观证据是「output 文件有 audio stream」+「音频能量接近 BGM 源」
- ffprobe `volumedetect` 给出 mean_volume / max_volume，是行业标准方法
- 与 diagnose（预测）形成对照：predict=理论值 / probe=实测值

### D5：combo-test 复用 REQ-089 异步基础设施，不另起一套

**理由**：
- 用户决策已确认「复用现有 export_fine_video 异步基础设施」
- 进度条 + ⏹ 取消按钮已有，直接复用避免重复实现
- combo-test 只调 export_fine_video + 轮询 + probe_output_audio，不复制 ffmpeg 启动逻辑

## 架构图

```
[Browser] 用户勾 5 个 checkbox
    ↓ click combo-test
[router.js] combo-test action
    ↓ snapshot 原状态 → window._comboSnapshot
    ↓ POST /save_fine_layout (4 elements) + /save_fine_audio (1)
    ↓ POST /export_fine_video {task_id} → {job_id}
    ↓ 1.5s 轮询 GET /render_status?job_id=X
    ├─ state=running → 继续轮询
    ├─ state=done → POST /probe_output_audio {task_id} → 渲染报告
    ├─ state=failed → 显示错误
    └─ state=cancelled → 显示已取消
    ↓ 显示「↩️ 还原上次配置」按钮 (点击后写回 snapshot)
```

## 关键实现

### E1：`/slirn/api/diagnose_bgm` 端点

**位置**：`[slirn_home/app.py](slirn_home/app.py)`，参考 `/slirn/api/list_logs` 模式（async def + Body(default_factory=dict)）。

**签名**：

```python
@app.post("/slirn/api/diagnose_bgm")
async def diagnose_bgm(body: dict = Body(default_factory=dict)):
    """根据 fc 状态预测 BGM 合成路径（不跑 ffmpeg、不创建临时文件）。"""
    tid = body.get("task_id")
    if not tid:
        return {"ok": False, "error": "missing task_id"}
    try:
        fc = _get_fine_compose(tid)
    except FileNotFoundError:
        return {"ok": False, "error": "fc not found"}
    return _predict_audio_path(fc)
```

### E2：`_predict_audio_path` 纯函数

**位置**：`[slirn_home/app.py](slirn_home/app.py)`，紧邻 `_assemble_fine_filter` 之后（line 2151 附近）。

**逻辑**（从 `_assemble_fine_filter` 抽出，复刻 inputs_count + audio_idx + filter_complex 计算）：

```python
def _predict_audio_path(fc: dict) -> dict:
    layout = fc.get("layout") or {}
    materials = fc.get("materials") or {}
    audio_cfg = fc.get("audio") or {}

    inputs_count = 1  # video 始终
    if layout.get("bg", {}).get("enabled") and (materials.get("bg") or {}).get("path"):
        inputs_count += 1
    cover_input_enabled = (
        layout.get("cover", {}).get("enabled")
        and float(layout.get("cover", {}).get("duration", 0)) > 0
        and (materials.get("cover") or {}).get("path")
    )
    if cover_input_enabled:
        inputs_count += 1
    audio_input_enabled = audio_cfg.get("enabled") and (materials.get("audio") or {}).get("path")
    if audio_input_enabled:
        inputs_count += 1

    audio_idx = inputs_count - 1 if audio_input_enabled else -1

    elements = {...}  # 5 元素 status

    predicted_audio_filters = ""
    if audio_input_enabled and audio_idx >= 0:
        vol = float(audio_cfg.get("volume", 0.4))
        fade_in = float(audio_cfg.get("fade_in", 0.0))
        fade_out = float(audio_cfg.get("fade_out", 0.0))
        bgm_chain = f"[{audio_idx}:a]aloop=loop=-1:size=2e9,volume={vol:.2f}"
        if fade_in > 0: bgm_chain += f",afade=t=in:st=0:d={fade_in:.2f}"
        if fade_out > 0: bgm_chain += f",afade=t=out:st=0:d={fade_out:.2f}"
        bgm_chain += "[bgm]; [voice][bgm]amix=inputs=2:duration=first:normalize=0[aout]"
        predicted_audio_filters = bgm_chain

    why_no_bgm = ""
    if not audio_cfg.get("enabled"):
        why_no_bgm = "fc.audio.enabled = False（未勾选 BGM）"
    elif not (materials.get("audio") or {}).get("path"):
        why_no_bgm = "materials.audio.path 缺失（未上传音频素材）"

    return {
        "ok": True,
        "elements": elements,
        "inputs_count": inputs_count,
        "audio_idx": audio_idx,
        "predicted_has_bgm": bool(audio_input_enabled and audio_idx >= 0),
        "predicted_audio_filters": predicted_audio_filters,
        "why_no_bgm": why_no_bgm,
    }
```

### E3：测试面板 HTML（details 折叠块）

**位置**：[slirn_home/app.py:3558](slirn_home/app.py#L3558) 之后插入。

**HTML**（用 `<details class="slirn-fine-section">` 模式）：

```python
f'''<details class="slirn-fine-section slirn-combo-test-details" id="slirn-combo-test-details" open>
  <summary class="slirn-fine-section-summary">
    🧪 合成元素组合测试 <span class="slirn-fine-section-status">5 项可勾选</span>
  </summary>
  <div class="slirn-combo-test-block">
    <div class="slirn-form-hint">⚠️ 这会修改 fc 当前勾选状态；测试后会提示还原</div>
    <div class="slirn-combo-test-row">
      <label><input type="checkbox" data-combo-kind="video" checked> 🎬 视频</label>
      <label><input type="checkbox" data-combo-kind="subtitle"> 📝 字幕</label>
      <label><input type="checkbox" data-combo-kind="cover"> 🖼 封面</label>
      <label><input type="checkbox" data-combo-kind="bg"> 🎨 背景图片</label>
      <label><input type="checkbox" data-combo-kind="audio" checked> 🎵 BGM</label>
    </div>
    <div class="slirn-combo-test-actions">
      <button class="slirn-btn" data-action="combo-apply" data-task-id="{tid}">📝 应用勾选（写 fc）</button>
      <button class="slirn-btn slirn-btn-primary" data-action="combo-test" data-task-id="{tid}">🚀 一键测试合成</button>
      <button class="slirn-btn slirn-btn-xs" data-action="combo-diagnose" data-task-id="{tid}">🔍 仅诊断</button>
      <button class="slirn-btn slirn-btn-xs" data-action="combo-restore" data-task-id="{tid}" hidden>↩️ 还原上次配置</button>
    </div>
    <div class="slirn-combo-test-output" id="slirn-combo-test-output-{tid}"></div>
  </div>
</details>'''
```

### E4：router.js 3 个新 action dispatch

**位置**：[slirn_home/static/router.js](slirn_home/static/router.js)，参考 REQ-089 取消按钮的 `else if (action === 'fine-export-cancel')` 模式。

| Action | 行为 |
|---|---|
| `combo-apply` | 读 5 个 checkbox → 调 `/save_fine_layout` (4 个 elements) + `/save_fine_audio` (1) |
| `combo-diagnose` | fetch `/slirn/api/diagnose_bgm` → 渲染结构化报告 |
| `combo-test` | snapshot → apply → 调 `/export_fine_video` → 轮询 `/render_status` → 完成后 `/probe_output_audio` → 渲染完整报告 + 显示「还原」按钮 |
| `combo-restore` | （不需后端 API；纯前端：从 `window._comboSnapshot` 读 + 调 apply） |

### E5：`/slirn/api/probe_output_audio` 端点

**作用**：ffprobe volumedetect 探测 output 文件的音频信息。

**签名**：

```python
@app.post("/slirn/api/probe_output_audio")
async def probe_output_audio(body: dict = Body(default_factory=dict)):
    tid = body.get("task_id")
    output_rel = body.get("output_path")  # e.g. outputs/final.mp4
    bgm_rel = body.get("bgm_path")        # e.g. materials/audio.mp3 (可选)
    # 解析为绝对路径 → ffprobe -v error -show_streams -show_format
    # 提取 audio stream count + duration + tags
    # 如果有 bgm_rel → ffprobe -v error -af volumedetect -of json → mean_volume/max_volume
    return {"ok": True, "streams": [...], "mean_volume_db": -17.7, ...}
```

### E6：CSS

**位置**：[slirn_home/static/home.css](slirn_home/static/home.css)，参考 `.slirn-bg-detect-block` 模式。

```css
.slirn-combo-test-block {
  padding: 8px 12px;
  border: 1px dashed #888;
  border-radius: 4px;
  background: #fff8e1;
}
.slirn-combo-test-row {
  display: flex;
  gap: 16px;
  margin: 8px 0;
  flex-wrap: wrap;
}
.slirn-combo-test-actions {
  display: flex;
  gap: 6px;
  margin-bottom: 6px;
  flex-wrap: wrap;
}
.slirn-combo-test-output {
  margin-top: 6px;
  padding: 6px 8px;
  background: #f6f8fa;
  border-radius: 4px;
  font-family: monospace;
  font-size: 11px;
  white-space: pre-wrap;
  max-height: 300px;
  overflow-y: auto;
}
.slirn-combo-test-output:empty { display: none; }
```

### E7：4 个新测试

**位置**：[tests/test_workbench.py](tests/test_workbench.py)，参考 REQ-088 的 11 个测试模式。

| 测试 | 验证 |
|---|---|
| `test_combo_test_zone_in_workbench_html` | `_render_fine_cut_zone` 输出含 `🧪 合成元素组合测试` + 5 个 `data-combo-kind` checkbox |
| `test_diagnose_bgm_endpoint_exists` | `/slirn/api/diagnose_bgm` 端点定义存在 + 返回结构含 `predicted_has_bgm` / `predicted_audio_filters` |
| `test_router_js_combo_actions` | router.js 包含 `combo-apply` / `combo-test` / `combo-diagnose` 3 个 action 分支 |
| `test_combo_test_saves_fc_layout_audio` | combo-apply handler 内含 `/save_fine_layout` + `/save_fine_audio` 调用 + payload 含 `enabled` |

## 复用现有基础设施

- `_get_fine_compose`（[app.py:2607](slirn_home/app.py#L2607)）—— 读 fc
- `_save_fine_compose`（[app.py:2872](slirn_home/app.py#L2872)）—— 写 fc（不直接用，但 save_fine_layout/save_fine_audio 内部用）
- `_assemble_fine_filter`（[app.py:1899](slirn_home/app.py#L1899)）—— **不复用**（会创建临时文件）
- `/slirn/api/save_fine_layout` + `/slirn/api/save_fine_audio`（已有 endpoint）
- `/slirn/api/export_fine_video` + `/render_status` + `/cancel_render`（已有）
- REQ-089 的 inline progress 模式（router.js `startFineExportInline`）—— 简化版复用
- ffprobe 命令（已用在前面的 bg-detect / time probe）

## 验证

1. `pytest tests/test_workbench.py -q` → 624 passed（620 + 4 new）
2. 重启 slirn → 进 task 22 → 工作台顶部看到「🧪 合成元素组合测试」面板
3. **诊断测试**：勾「视频 + BGM」+ 点「🔍 仅诊断」→ `predicted_has_bgm: true` + `predicted_audio_filters` 包含 `[1:a]aloop=...`
4. **反面对照**：取消勾 BGM + 仅诊断 → `predicted_has_bgm: false` + `predicted_audio_filters: ""` + `why_no_bgm`
5. **一键测试**：勾「视频 + 字幕 + BGM」+ 一键测试 → 异步跑 → 完成 → 「✅ BGM 已合成（mean=-XX dB，匹配 BGM 源）」
6. **边界测试**：勾 5 个全部 → 一键测试 → inputs_count=4, audio_idx=3 → BGM 正常
7. **还原测试**：修改前 → apply → 还原 → fc 状态回到 apply 之前

## 风险与边界

| 风险 | 处理 |
|---|---|
| `combo-apply` 写 fc 会覆盖用户当前配置 | 顶部红色提示 + 「↩️ 还原」按钮 |
| `combo-test` 每次跑 ffmpeg 1-10 秒 | 异步跑，轮询 + 取消按钮复用 |
| 勾选没对应素材 | diagnose 检测 → 报告里提示「素材缺失，filter 不会渲染」 |
| diagnose 报告太技术化 | emoji + 简洁描述 |
| 用户连续点 combo-test | 启动时禁用按钮，job 完成后恢复 |

## Why

**组合测试 = BGM 路径回归防护**。当前 BGM 修复（REQ-080/083/085/089）只验证了「单独视频 + BGM」场景。当用户启用更多元素时，`inputs_count` 变化 → `audio_idx` 重算 → 可能引入新回归。调试面板让用户**逐个组合验证**，是防止 BGM 回归的最后一道保险。

## How to apply

未来添加新合成元素（watermark / sticker / transition）时：
1. 加进 `_FINE_LAYOUT_DEFAULTS` + 在 `_assemble_fine_filter` 处理 gate
2. 在测试面板的勾选列表里加一项
3. diagnose 报告里加对应元素的状态字段

调试面板可长期保留作为「合成元素组合回归防护」——每次改 filter assembly 都要跑一遍。
