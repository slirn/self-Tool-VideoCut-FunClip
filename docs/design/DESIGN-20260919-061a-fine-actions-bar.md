# DESIGN-20260919-061a — 精剪视频·顶部操作栏与预览时长

> 父设计：随父需求 REQ-20260919-061 演变，原计划 [tingly-dancing-kahan.md](C:\Users\Administrator\.claude\plans\tingly-dancing-kahan.md) 已被本设计替代。

## Context

精剪视频面板参数众多（位置/缩放/字体/输出/音频）。原方案把「💾 保存全部」放卡片标题、模板管理放底部 inline 列表，用户实际操作路径长且与设置参数区域割裂。本设计：

1. 把保存/引用操作统一到 **顶部 actions bar**（设置参数区上方）
2. 用 **模态弹窗** 替代底部 inline 列表
3. 预览时长暴露为可调参数

## 关键决策（与 REQ 同源）

| 决策 | 选择 | 理由 |
|---|---|---|
| 入口位置 | **顶部 actions bar**（`.slirn-fine-actions-bar`） | 用户原话；与「设置参数」区域在视觉/语义上绑定 |
| 模板应用行为 | **直接覆盖**（不弹确认） | 用户没要求；模板仅参数，无素材副作用；可一键再保存覆盖回去 |
| 模态复用现有 CSS | **新增** `.slirn-modal-overlay` / `.slirn-modal-card` 通用类（其他面板可复用） | 不与已有弹窗混样式 |
| 预览时长控件 | **number + ▲▼ 步进**（无 slider） | 单一数值，slider 不必要；步进与全局滑块行视觉一致 |
| 数据契约 | **`save_fine_layout`** 白名单新增 `"duration"` 键；**`render_fine_preview`** Body 新增 `duration` 字段 | 不破坏旧调用 |

## 数据契约变更

### `POST /slirn/api/save_fine_layout`

Body（变更）：
```diff
 {
   "task_id": "...",
   "layout": {
     "video":    {...},
     "subtitle": {...},
     "bg":       {...},
-    "cover":    {"enabled": false}
+    "cover":    {"enabled": false, "duration": 3.0}   // ← 新增 duration
   }
 }
```

白名单同步：
```python
allowed_keys = {"video", "subtitle", "bg", "cover", "duration"}   # ← 新增
```

### `POST /slirn/api/render_fine_preview`（变更）

Body：
```diff
 {
   "task_id": "...",
-  "duration": 10        // 默认 10（兼容旧调用）
+  "duration": 10         // 范围 [2, 30]，超出则 clamp
 }
```

```python
preview_dur = max(2.0, min(30.0, preview_dur))
result = _run_fine_render(tid, mgr, out_path, duration=preview_dur)
```

## UI 结构

```html
<div class="slirn-fine-actions-bar">                <!-- 顶部 -->
  <span class="slirn-fine-actions-label">模板名</span>
  <input id="slirn-fine-profile-name" type="text" placeholder="..." maxlength="30">
  <button data-action="fine-save-all">💾 保存设置参数</button>
  <button data-action="fine-import-show">📥 引用参数</button>
  <span class="slirn-fine-save-status" data-state="idle|saving|ok|err"></span>
</div>

<!-- ... 原有的设置参数 / 字体 / 输出 / 音频 / 布局 ... -->

<div class="slirn-fine-preview-bar">                 <!-- 预览按钮行 -->
  <button data-action="fine-preview">🎬 生成预览</button>
  <span class="slirn-fine-preview-duration">
    <span class="slirn-fine-actions-label">预览时长</span>
    <input id="slirn-fine-preview-duration" type="number" min="2" max="30" step="1" value="10">
    <button class="slirn-fine-step-btn" data-step-dir="up" data-for="slirn-fine-preview-duration">▲</button>
    <button class="slirn-fine-step-btn" data-step-dir="down" data-for="slirn-fine-preview-duration">▼</button>
    <span class="slirn-fine-actions-label">秒（2–30）</span>
  </span>
</div>

<!-- 引用模态（默认 hidden） -->
<div class="slirn-modal-overlay" id="slirn-fine-import-overlay" hidden>
  <div class="slirn-modal-card slirn-fine-import-card">
    <div class="slirn-modal-title">📥 引用参数模板（应用到当前任务）</div>
    <div id="slirn-fine-import-list" class="slirn-fine-import-list">
      <!-- JS 注入：每行 = 模板名 + 时间 + 应用/重命名/删除 按钮 -->
    </div>
    <button class="slirn-btn" data-action="fine-import-close">关闭</button>
  </div>
</div>
```

## JS 交互

### `fineSaveAll(showToast, asTemplate)`
- `showToast=true`：用户主动点保存 → 显示保存中/成功/失败 toast
- `asTemplate=true`：若模板名输入为空 → `window.prompt('请填写模板名...', '')`；用户取消 → 仅保存当前参数；用户填写 → 正常存为模板

### `fineImportShow()` / `fineImportClose()`
- `fineImportShow()`：fetch `list_fine_global_profiles` → 渲染到 `#slirn-fine-import-list` → 移除 `hidden`
- `fineImportClose()`：加 `hidden`

### `fineImportApply(profileId)` / `fineImportDelete(profileId)` / `fineImportRename(profileId)`
- Apply：`POST apply_fine_global_profile` → 重渲 wb
- Delete：`window.confirm('确认删除？')` → `POST delete_fine_global_profile`
- Rename：`window.prompt('新名称', oldName)` → `POST save_fine_global_profile`（同 save 但覆盖）

### `fine-preview` action
- 读 `#slirn-fine-preview-duration.value`，转 float → fetch `render_fine_preview` 带 `duration` 字段

### `bindFineSteppers()`
- 通用绑定：找 `data-for="<id>"` 的 ▲▼ 按钮 → 给 `<id>` 或 `<id>_num` 元素 +/- step
- 支持无关联 slider 的纯数字输入（预览时长就是这种）

## CSS 增量

```css
.slirn-fine-actions-bar { display: flex; gap: 8px; align-items: center; padding: 8px 0; border-bottom: 1px dashed #ddd; margin-bottom: 12px; }
.slirn-fine-actions-label { font-size: 13px; color: #666; }
.slirn-fine-profile-name { flex: 1 1 240px; min-width: 180px; max-width: 320px; }
.slirn-fine-save-status[data-state="saving"] { color: #999; }
.slirn-fine-save-status[data-state="ok"]     { color: #28a745; }
.slirn-fine-save-status[data-state="err"]    { color: #dc3545; }

.slirn-fine-preview-bar       { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
.slirn-fine-preview-duration  { display: inline-flex; gap: 6px; align-items: center; }
.slirn-fine-num               { width: 64px; }
.slirn-fine-step-btn          { width: 22px; height: 22px; padding: 0; line-height: 1; }

.slirn-modal-overlay    { position: fixed; inset: 0; background: rgba(0,0,0,.4); display: flex; align-items: center; justify-content: center; z-index: 9999; }
.slirn-modal-overlay[hidden] { display: none; }
.slirn-modal-card       { background: #fff; border-radius: 8px; padding: 16px 20px; min-width: 360px; max-width: 640px; max-height: 80vh; overflow: auto; box-shadow: 0 8px 24px rgba(0,0,0,.15); }
.slirn-fine-import-list { display: flex; flex-direction: column; gap: 6px; margin: 8px 0; }
.slirn-fine-import-row  { display: flex; gap: 8px; align-items: center; padding: 6px 8px; border: 1px solid #eee; border-radius: 4px; }
```

## 测试增量

`tests/test_workbench.py`：

| 测试 | 覆盖 |
|---|---|
| `test_save_fine_layout_accepts_cover_duration_field` | 白名单接受 `duration` |
| `test_save_fine_layout_response_shape_uses_ok_field` | 响应 `{"ok": true}` |
| `test_render_fine_cut_zone_renames_save_button_to_settings` | 按钮文案 |
| `test_run_fine_render_cover_audio_adelay_filter` | adelay filter |
| `test_run_fine_render_no_cover_no_adelay_filter` | 无 adelay |
| `test_render_fine_preview_accepts_duration_in_range` | 时长端点 |
| `test_render_fine_preview_clamps_out_of_range` | clamp |
| `test_render_fine_zone_includes_preview_duration_input` | UI 渲染 |
| `test_render_fine_cut_zone_includes_profile_block` | actions bar + 模态 |

## 不做的事

- ❌ 不改 `_FINE_LAYOUT_DEFAULTS`（默认仍是 10s）
- ❌ 不在引用时弹确认（用户没要求；模板仅参数无副作用）
- ❌ 不把模板管理的 inline 列表再放回去（已被模态替代）
- ❌ 不动 funclip/ 上游源码

## 关键文件

| 文件 | 改动 |
|---|---|
| [slirn_home/app.py](../../slirn_home/app.py) | `_render_fine_cut_zone` actions bar + 模态；`render_fine_preview` 接收 `duration`；`save_fine_layout` 白名单加 `duration` |
| [slirn_home/static/router.js](../../slirn_home/static/router.js) | `fineSaveAll`/`fineImport*`/`bindFineSteppers`；`fine-preview` 读取时长 |
| [slirn_home/static/home.css](../../slirn_home/static/home.css) | actions bar / 模态 / 步进按钮 |
| [tests/test_workbench.py](../../tests/test_workbench.py) | 9 个新测试 |