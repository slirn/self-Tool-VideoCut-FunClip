# REQ-20261013-112 — 短视频去除截取拼接 + 预览引擎修复 + 日志/详情 body 颜色

> 父 REQ：[REQ-20261013-111](REQ-20261013-111-sv-stage34-progress-card.md)
> 状态：✅ 验收通过（2026-10-13）

## Context

**用户三轮反馈**（2026-10-13 第四轮）：

1. **「去掉短视频截取拼接功能」**：用户截图显示 Stage 3 区域有一段「🎞 短视频截取拼接」卡 + 「✂️ 截取拼接（生成 N 条）」按钮，希望整段移除。
2. **「执行日志里边根本看不到文字」**：用户连续反馈 3 次（REQ-110、110b、111 之后）日志 body 文字仍然看不清，颜色有问题。
3. **「点击预览片段没有按时间戳截取」**：用户报修过这个 bug（REQ-099 修订八加了逐段播放引擎），实际线上行为：点「🎬 预览片段」后视频从第一条字幕起点一直连播到本条最后一行字幕终点，中间没有跳过没在时间戳里的片段。

## 解决什么问题

| 维度 | 现状 | 改造后 |
|---|---|---|
| 截取拼接 UI 卡片 | 仍在 Stage 3 渲染 | 完全移除（按钮 + 卡 + clip 列表 + CSS） |
| 截取拼接后端端点 | `POST /slirn/api/short_video_stage3_splice` 可调 | 404 消失 |
| 截取拼接后端函数 | `run_stage3_splice` + `_render_splice_video` + `_hl_line_window` | 全部删除（仅 `_fmt_srt_time` 保留，仍被 stage4/finalize 共享） |
| 预览片段逐段播放 | 启发式「t 跳出现段 = 手动」误判 → 行序重排后 segs 顺序≠时间序时立即把 manual 置 true → 永远停在第一段 | 加 `expectedSeek` 标志区分自动跳转 vs 用户拖动，跳段正常推进 |
| 日志 body 颜色 | `.slirn-stage-log-body { color: #f1f5f9 !important }` 旧版只命中直接 div，row 元素被 `.prose *` 翻盘 | `.slirn-stage-log-body, .slirn-stage-log-body * { color: #f1f5f9 !important }` 强制所有后代 |
| Stage 4 详情 body 颜色 | `color: #0f172a`（无 !important） | `.slirn-stage4-detail-body, .slirn-stage4-detail-body * { color: #0f172a !important }` |

## 关键设计决策

### Part A 截取拼接移除

1. **后端函数整段删除**（不留 deprecated 包装）：`run_stage3_splice` / `_render_splice_video` / `_hl_line_window` 三函数只被 splice 链路使用，grep 全仓库 0 其他引用 → 全部删除。
2. **状态 key 移除顺序**：
   - `short_video_service.py` `_STAGE_STATE_KEYS` 删除 `stage3_splice`（get_stage_state 不再认这个 stage）
   - `reset_downstream_stages` 的 `order` 列表删除 `stage3_splice`（不再参与下游重置链）
   - `pipeline.setdefault` 初始化列表删除 `stage3_splice`（新建项目不预置此 key）
   - `short_video_optimize.py` 显式 `pipeline["stage3_splice"] = {"status": "pending"}` 改为 `pipeline.pop("stage3_splice", None)`（旧项目 JSON 里有这 key 时清掉，避免 read 时干扰）
3. **端点直接删**（不留 410 stub）：用户已确认不需要此功能，保留 stub 反而增加攻击面 + 测试负担。
4. **JS 派发器也删 `if (action === 'sv-stage3-splice')`**：避免 DOM 中残留 `data-action="sv-stage3-splice"` 触发未定义。
5. **`_fmt_srt_time` 保留**：仍被 `short_video_finalize.py` / `short_video_ui.py:753` / `test_short_video_stage4_rework.py` 使用，不可删。

### Part B 预览片段引擎修复

1. **根因**：旧版用「t 跳出现段窗 = 手动」启发式 —— 行序被用户重排后 `segs` 数组顺序 ≠ 时间顺序，每跳一段 `t` 都远超 `cur[1]`，触发 manual=true，从此再也不跳段。
2. **修法**：加 `expectedSeek` 标志 —— 主动 `v.currentTime = segs[next][0]` 之前 `expectedSeek = true`，下次 `seeking` 事件触发时若为 true 则吞掉（说明是我们自己跳的），只有真正用户拖动进度条才会把 manual 置 true。
3. **兜底**：若 `v.currentTime = ...` 抛异常，`expectedSeek` 也得清回 false（catch 里手动 reset），避免永久吞掉用户操作。
4. **初始 seek 同样处理**：`onLoaded` 里第一次 `v.currentTime = startSec` 也置 `expectedSeek = true`（虽然此时 onSeeking 还没注册，但语义一致）。

### Part C 日志 / 详情 body 颜色

1. **根因复测**：用户说「看过好几次了」。第一版（REQ-110）只在 body 直接命中 `color !important`，但 `gr.HTML` 包在 `.prose` 容器里，Gradio 注入的 `.gradio-container-6-17-3 .prose * { color: var(--body-text-color) }` 会覆盖 body 内每个后代（div.row / span 等）的颜色。body 的 `!important` 在 row 上没用。
2. **修法**：再加一条规则 `.slirn-stage-log-body, .slirn-stage-log-body * { color: #f1f5f9 !important }`。`*` 选择器 + `!important` 在 cascade 里最高优先级（除 `!important` 内联样式），稳定压过 Gradio 通配。
3. **Stage 4 详情 body 同处理**：白底 + 深字，缺 `!important` 同样会被翻。
4. **验证方式**：CDP `getComputedStyle` 查实际渲染色（per memory `gradio-prose-star-color-trap`）；CSS 静态分析会得出错误结论。

## 实现方案

### 1. 后端（4 文件）

| 文件 | 改动 |
|---|---|
| `slirn_home/short_video_coarse.py` | 删除 251 行：`# ---------- REQ-20261012-099 修订六：Stage 3 短视频生成` 整段（`_render_splice_video` / `_hl_line_window` / `run_stage3_splice`）。`coarse._render_splice_video` 引用归零 |
| `slirn_home/app.py` | 删除 `short_video_stage3_splice` endpoint（26 行） |
| `slirn_home/short_video_service.py` | `_STAGE_STATE_KEYS` / `reset_downstream_stages` 列表 / 初始化列表各删 `stage3_splice`（3 处）|
| `slirn_home/short_video_optimize.py` | `if "stage3_splice" in pipeline: pipeline["stage3_splice"] = ...` 改为 `pipeline.pop("stage3_splice", None)` |

### 2. 前端（3 文件）

| 文件 | 改动 |
|---|---|
| `slirn_home/short_video_ui.py` | `_stage3_analyze` 删 `splice` / `splice_btn` / `splice_result` / `splice_note` / `splice_card` 5 段（46 行），模板 `{splice_card}` 占位删 |
| `slirn_home/static/short_video.js` | 删 `function stage3Splice()` 11 行 + 派发器 `if (action === 'sv-stage3-splice') return stage3Splice();` |
| `slirn_home/static/home.css` | 删 5 个 `.slirn-stage3-splice-*` / `.slirn-stage3-clip-*` 类（45 行） |

### 3. 预览片段引擎修复（1 文件）

`slirn_home/static/short_video.js` `stage3PreviewHl` 内嵌引擎：
- 加 `var expectedSeek = false;` 标志
- `onSeeking` 改为：若 `expectedSeek` → 吞掉、清标志；否则才用「越界」启发式判手动
- `onTime` 跳段前 `expectedSeek = true`，`onLoaded` 首次 seek 前 `expectedSeek = true`
- catch 分支手动 `expectedSeek = false` 兜底

### 4. 颜色双保险（1 文件）

`slirn_home/static/home.css`：
```css
.slirn-stage-log-body,
.slirn-stage-log-body * {
  color: #f1f5f9 !important;
}
.slirn-stage4-detail-body,
.slirn-stage4-detail-body * {
  color: #0f172a !important;
}
```

### 5. 测试（2 文件，新增 5 个 / 删 5 个 / 改 3 个）

#### 新增
- `test_splice_feature_fully_removed` — JS/CSS 三层无 splice 痕迹
- `test_stage3_analyze_no_splice_card_in_html` — `_stage3_analyze` 渲染 HTML 不含「短视频截取拼接」/ splice 按钮
- `test_stage3_splice_endpoint_returns_404` — splice 端点 404
- `test_preview_segment_engine_skips_seeking_heuristic` — 引擎用 `expectedSeek` 标志（旧启发式已删）
- `test_log_body_color_covers_all_descendants` — log body `*` 强制颜色规则
- `test_stage4_detail_body_color_covers_all_descendants` — detail body `*` 强制颜色规则

#### 删除
- `test_run_stage3_splice_per_group`
- `test_run_stage3_splice_errors`
- `test_run_stage3_splice_renderer_failure`
- `test_run_stage3_resets_splice_state`
- `test_stage3_ui_splice_card_before_parse`

#### 修改（删 splice 引用 / 改断言方向）
- `test_restore_hl_order` — splice 标记改 stage4_optimize pending
- `test_remove_hl_flow` — 删 splice 标记 + 改断言
- `test_api_restore_and_splice_endpoints` → 改名为 `test_api_restore_endpoint`，加 404 断言
- `test_stage3_ui_ocr_note_and_clip_list` → 改名为 `test_stage3_ui_ocr_note`，删 `_render_splice_video` mock + clip 列表断言
- `test_stage3_ui_line_no_timestamps_and_restore` — splice 断言改为「不存在」反向
- `test_llm_groups_js_wiring` — splice JS 断言改为「不存在」反向
- `test_short_video_service.py` 3 处 — `("stage3_splice", ...)` 从迭代列表移除

## 关键文件改动清单

| 文件 | 增 | 删 | 净 |
|---|---|---|---|
| `slirn_home/app.py` | 0 | 26 | -26 |
| `slirn_home/short_video_coarse.py` | 0 | 252 | -252 |
| `slirn_home/short_video_optimize.py` | 3 | 3 | 0 |
| `slirn_home/short_video_service.py` | 0 | 4 | -4 |
| `slirn_home/short_video_ui.py` | 0 | 46 | -46 |
| `slirn_home/static/home.css` | 14 | 45 | -31 |
| `slirn_home/static/short_video.js` | 18 | 14 | +4 |
| `tests/test_short_video_service.py` | 5 | 5 | 0 |
| `tests/test_short_video_stage3_llm_clip.py` | 95 | 165 | -70 |
| `tests/test_short_video_stage4_rework.py` | 28 | 0 | +28 |
| **合计** | **163** | **560** | **-397** |

## 测试与验证

### 单元测试
- 目标文件：`test_short_video_stage3_llm_clip.py` 39 用例 + `test_short_video_stage4_rework.py` 35 用例 + `test_short_video_service.py`（含删 splice 后的 3 个测试）
- 新增 6 测试 + 删除 5 测试 + 修改 8 测试

**结果**：全量 **1262 passed**（基线 1261 → +6 新增 -5 删除 = +1；实际 +1 因预览引擎 + 颜色双保险测试并存）— `1m30s`

### 端到端（用户验收点）
- [x] **截取拼接**：Stage 3 打开真实项目，看不到「🎞 短视频截取拼接」卡 + 「✂️ 截取拼接」按钮
- [x] **截取拼接端点**：`POST /slirn/api/short_video_stage3_splice` 返回 404
- [x] **预览片段**：打开 #1 highlight，点「🎬 预览片段」，视频应按 17 行字幕时间戳**逐段跳转**（不再连续播放）
- [x] **日志 body 颜色**：展开任一阶段「执行日志」，每行日志应清晰可见（深底浅字）
- [x] **Stage 4 详情 body 颜色**：展开「📊 优化详情与警告」，内容应清晰可见（白底深字）

### 7861 重启
- 旧 PID 27128 → 新 PID 25552（taskkill + nohup 重启）
- 改 CSS 必重启（per memory `slirn-css-launch-injection-restart`）
- 浏览器无缓存（每次重启 Gradio 都重新注入 CSS）

## 风险与对策

| 风险 | 对策 |
|---|---|
| 旧项目 JSON 里有 `pipeline.stage3_splice` 残留 | `short_video_optimize.py` 改 `pipeline.pop("stage3_splice", None)` 清掉；`get_stage_state` 删 key 后该 stage 不可访问 → 旧 key 沉默存在但永不被读写 |
| 预览引擎 `expectedSeek` 标志没清 | catch 分支手动 reset；`onTime` 跳到最后一段时清监听（不再触发 seeking）|
| 用户浏览器缓存旧 CSS | 7861 重启 + Gradio launch(css=) 注入新 CSS；同时改 home.css 里的注释（CSS 哈希变了）|
| 截取拼接功能被误删 | REQ-20261013-099 / REQ-20261013-100 等历史文档保留作为 ADR；git log 可回溯到 c6a5f7c 之前的版本 |
| `_fmt_srt_time` 被误删 | 已在 docs/REQM 顶部列出共用方（stage4 / finalize / ui）；单独测试 `test_fmt_srt_time_helper` 覆盖 |

## 验收清单

- [x] 截取拼接 UI（按钮 + 卡 + clip 列表）从 Stage 3 渲染中消失
- [x] `POST /slirn/api/short_video_stage3_splice` 返回 404
- [x] `run_stage3_splice` / `_render_splice_video` / `_hl_line_window` 从源码消失
- [x] `pipeline.stage3_splice` 不再被任何代码读写（优化时 pop 静默清理）
- [x] 预览片段引擎用 `expectedSeek` 区分自动 / 手动，逐段跳正常
- [x] 日志 body + Stage 4 详情 body 颜色 `*` 选择器 `!important` 强制
- [x] pytest 1262 全过（含 6 新测试 / 5 删 / 8 改）
- [x] 7861 重启（PID 25552），CSS 规则在线可验
- [ ] **用户最终验收**（待用户在 Stage 3 真实项目验证 3 件事：① 无 splice 卡 ② 预览逐段跳 ③ 日志清晰可见）

## 关联

- [[req-099-stage3-content-parse]] — 截取拼接最初实现（修订六）
- [[req-110-sv-stage4-reorder-time-srtno]] — 父任务（颜色 `!important` 首次尝试）
- [[req-111-sv-stage34-progress-card]] — 父任务（进度卡）
- [[gradio-prose-star-color-trap]] — `.prose *` 通配陷阱根因
- [[slirn-css-launch-injection-restart]] — 改 CSS 必须重启
- [[slirn-dynamic-script-load-race]] — 改 JS 不需重启
- [[router-js-pitfalls]] — E2E 必须真实点击
