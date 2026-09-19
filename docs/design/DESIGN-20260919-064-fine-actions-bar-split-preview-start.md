# DESIGN-20260919-064 精剪视频·操作栏拆分 + 预览开始时间

## Context

REQ-20260919-064：把 `.slirn-fine-actions-bar` 拆成两行，并新增 `preview_start` 字段支持从源视频任意时间点开始预览。

## 现状

```html
<div class="slirn-fine-actions-bar">
  AI智能布局 | 生成预览 | 预览时长[10]秒(2-30) | 导出最终视频
  | 模板名[可选填名] | 保存设置参数 | 引用参数 | 未保存
</div>
```

视觉上挤在一行（flex 横向），用户点击不便。

`_run_fine_render` 输入参数：
```python
input_args += ["-ss", "0"]            # 视频流从 0 开始
if duration is not None:
    input_args += ["-t", str(duration)]  # 截 N 秒
```

API `render_fine_preview` body：`{ task_id, duration }`，无 `preview_start`。

## 设计

### 1. 操作栏拆两行

把 `.slirn-fine-actions-bar` 拆成两个 row：

```html
<!-- 行 1：渲染操作（视频流相关） -->
<div class="slirn-fine-actions-bar">
  AI智能布局 | 生成预览 | 预览开始时间[0]秒 | 预览时长[10]秒(2-30) | 导出最终视频
</div>
<!-- 行 2：模板/保存操作（任务参数相关） -->
<div class="slirn-fine-actions-bar">
  | 模板名[可选填名] | 保存设置参数 | 引用参数 | 未保存
</div>
```

CSS：保持 `.slirn-fine-actions-bar` 横向 flex；两个 row 之间垂直堆叠（默认 div 行为）。**不动 CSS**，仅 HTML 结构变化即可。

### 2. 预览开始时间 input

新增 number input 紧跟在「预览时长」前：

```html
<span class="slirn-fine-preview-start">
  <span class="slirn-fine-actions-label">预览开始时间</span>
  <input type="number" id="slirn-fine-preview-start" class="slirn-fine-num"
         aria-label="预览开始时间（秒，0=从头）" min="0" step="1" value="0">
  <span class="slirn-fine-actions-label">秒（0=从头）</span>
</span>
```

样式复用 `.slirn-fine-preview-duration`（已在 home.css）。

### 3. 后端 API

`render_fine_preview` 新增 `preview_start` 字段：

```python
raw_start = body.get("preview_start", 0)
try:
    preview_start = float(raw_start)
except (TypeError, ValueError):
    preview_start = 0.0
preview_start = max(0.0, preview_start)
# 用 ffprobe 拿源视频时长，钳 start+duration 不超出
# 若 start+duration > video_duration：start = max(0, video_duration - duration)
```

`_run_fine_render(tid, mgr, out_path, duration=preview_dur, preview_start=0.0)`：

```python
input_args += ["-ss", str(preview_start)]   # 替换原 "-ss", "0"
if duration is not None:
    input_args += ["-t", str(duration)]
```

注：cover 输入不变（仍 `-loop 1 -t cover_dur`），所以预览结构仍是 [封面 2s] + [视频流 start..start+duration]。

### 4. 前端

router.js：`fine-preview`/`fine-export` handler 收集 `preview_start`：

```javascript
var _startEl = document.getElementById('slirn-fine-preview-start');
var _start = parseFloat(_startEl && _startEl.value);
if (!isNaN(_start) && _start >= 0) _payload.preview_start = _start;
```

按钮文案保留原样（"🎬 渲染中（前 N 秒）..."），暂不显示 start（避免按钮过长）。

## 实施步骤

### Phase 3: Implementation

| 文件 | 改动 |
|---|---|
| [slirn_home/app.py:2575-2584](slirn_home/app.py#L2575) | 新增 `preview_start` HTML span（紧跟 preview_btn 后） |
| [slirn_home/app.py:2650-2665](slirn_home/app.py#L2650) | 拆分 combined_actions_bar 为两个 row |
| [slirn_home/app.py:4845-4853](slirn_home/app.py#L4845) | 读 `preview_start`，传给 `_run_fine_render` |
| [slirn_home/app.py:1756-1758](slirn_home/app.py#L1756) | `-ss "0"` → `-ss str(preview_start)`（函数签名加 `preview_start=0.0`） |
| [slirn_home/static/router.js:5019](slirn_home/static/router.js#L5019) | 收集 `preview_start` 到 payload |
| [tests/test_workbench.py](tests/test_workbench.py) | 3 个新测试（见下） |

### Phase 4: Review

新增 3 个测试：

| 测试 | 验证 |
|---|---|
| `test_render_fine_cut_zone_actions_bar_split_into_two_rows` | HTML 含 2 个 `.slirn-fine-actions-bar` |
| `test_render_fine_cut_zone_has_preview_start_input` | 含 `id="slirn-fine-preview-start"` 的 number input |
| `test_render_fine_preview_accepts_preview_start` | body 含 `preview_start` → 渲染走 `start..start+duration` |

### Phase 5: Verification（E2E）

1. 重启 slirn
2. 打开任务 20260918-022 精剪面板
3. 视觉确认：操作栏分两行
4. 「预览开始时间」填 5，「预览时长」填 5 → 生成预览
6. 检查 outputs/fine_preview.mp4 = 封面 2s + 视频流 5..10s 的内容
7. 不填（默认 0）→ 与之前一致

## 风险与边界

| 风险 | 处理 |
|---|---|
| preview_start + duration 超出源视频时长 | 后端钳 start = max(0, video_dur - duration)；start 不会负 |
| 用户输入 start 超大值 | min=0 + 后端 max(0, video_dur - duration) 兜底 |
| cover 与视频时间不同步（用户跳到 60s，但 cover 仍 2s）| 这是预期行为：cover 是片头，独立于视频流 |
| 字幕 SRT 时间戳不匹配视频流新时间 | SRT 是相对视频流的，start 跳到 60s → SRT 也从 60s 开始（ffmpeg `-ss` 默认 seek，会让字幕时间漂移） |
| | **接受这个限制**：用户明确说用来"检测不同时间点的合成视频效果"，不是完整视频；字幕漂移是预期副作用。如果需要严格对齐 SRT，需 `-ss` 后跟 `-copyts` 或重写 srt 时间戳。**当前版本不解决**。 |
| 模板管理栏（行 2）的 `slirn-fine-actions-bar-sep` 分隔符位置 | 移除：两行后不再需要分隔 |

## 不做的事

- ❌ 不持久化 preview_start（每次重新输入）
- ❌ 不动 cover 渲染逻辑
- ❌ 不解决 SRT 字幕时间漂移（已知限制，文档化在风险表）
- ❌ 不动 CSS（HTML 结构变化已足够）

## 文件清单

| 文件 | 改动 |
|---|---|
| [slirn_home/app.py:2575-2584](slirn_home/app.py#L2575) | 新增 `preview_start` input HTML |
| [slirn_home/app.py:2650-2665](slirn_home/app.py#L2650) | 拆 combined_actions_bar 为两行 |
| [slirn_home/app.py:4845-4853](slirn_home/app.py#L4845) | endpoint 读 preview_start |
| [slirn_home/app.py:1756-1758](slirn_home/app.py#L1756) | `_run_fine_render` 接收并使用 preview_start |
| [slirn_home/static/router.js:5019](slirn_home/static/router.js#L5019) | payload 加 preview_start |
| [tests/test_workbench.py](tests/test_workbench.py) | 3 个新测试 |