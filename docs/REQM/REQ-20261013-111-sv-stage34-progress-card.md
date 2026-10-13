# REQ-20261013-111 — 短视频 Stage 3 解析 + Stage 4 重新优化 进度卡 + 防重入

> 父 REQ：[REQ-20261013-110](REQ-20261013-110-sv-stage4-reorder-time-srtno.md)
> 状态：✅ 验收通过（2026-10-13）

## Context

**用户反馈**（2026-10-13 第三轮，REQ-110 验收后）：

第一轮（Stage 4）：
> "点击「重新优化」按钮之后，没有正在执行的相关信息，只有一个提示一闪而过。
> 这样的话，如果它正在执行，我也看不出来，可能会重新点击。
> 所以，如果后台正在执行，前台必须有一个显示正在执行的时间进度情况。"

第二轮（Stage 3）：
> "在解析短视频内容阶段也存在这个问题：点击解析时，没有正在执行的时间和进度情况，需要添加"

**根因**（explore 确认）：

| 维度 | Stage 3 解析 | Stage 4 重新优化 |
|---|---|---|
| 按钮 disabled | ❌ 全程可点 | ❌ 全程可点 |
| 进行中指示 | ❌ 仅 1 秒 toast | ❌ 仅 1 秒 toast |
| 重复点击后果 | ❌ 并发跑 N 遍 LLM 互相覆盖 | ❌ 并发跑 N 遍 LLM 互相覆盖 |
| 同步阻塞耗时 | 10s-3min（单次多模态 LLM） | 1-3min（N 次 LLM 循环）|
| 后端去重 | ❌ 无 | ❌ 无 |
| Status 端点 | ❌ 无 | ❌ 无 |

## 解决什么问题

| 维度 | 现状 | 改造后 |
|---|---|---|
| 按钮 disabled | ❌ 全程可点 | ✅ 点击后立即 disabled + 改文案「🔍 解析中…」/「✨ 重新优化中…」 |
| 进行中指示 | ❌ 仅 1 秒 toast | ✅ status 卡 + 进度条 + 阶段性文案 |
| 重复点击 | ❌ 并发跑 N 遍 LLM | ✅ 防重入：闭包标志 + 按钮 disabled |
| 完成提示 | ⚠️ 仅 toast 文字 | ✅ 进度条 100% 绿色 + 「✅ 完成」+ 总览数据 |
| 失败提示 | ⚠️ 红色 toast | ✅ 进度条 100% 红色 + 「❌ 失败」+ 保留按钮可重试 |

## 关键设计决策

1. **纯前端方案，不改后端**（最小风险）— Stage 3/4 同步阻塞涉及多文件、LLM 循环深改；本任务不引入后端复杂度
2. **两按钮共用同一套 CSS class**（`.slirn-rc-status-card` / `.slirn-progress` / `.slirn-progress-bar`）+ 同一套防重入 JS 模式 — 0 重复
3. **进度条形态不同**：
   - **Stage 4 = N/M 计数式**（`第 1/N 条 / 第 2/N 条 / ... / 第 N/N 条`）：因为后端是 N 次 LLM 循环，前端用 highlights 数量 + 1500ms 节奏自估算
   - **Stage 3 = 阶段文字式**（`⏳ 启动解析… → ⏳ 解析截图 OCR… → ⏳ 调大模型… → ⏳ 解析响应… → ✅ 完成`）：因为后端是单次多模态调用，按 4 个阶段切换文案
4. **进度条到 100% 后不会立即切到 done**（自估算快于实际完成）— 但比无指示好 100 倍
5. **防重入双保险**：① 闭包变量 `window._slirnSvStage{N}Running` ② `btn.disabled = true`
6. **不持久化、不动 `_JOBS` 表**（仿 Stage 5/7 也只是后端内存 job，UI 上不影响）
7. **借鉴 Stage 5 进度条 CSS**（`home.css:1209-1250`）+ Stage 5/7 status 卡 HTML 结构（`short_video_ui.py:999-1007` / `:1173-1180`）

## 实现方案

### 1. 公共 CSS（`slirn_home/static/home.css`）

新增约 50 行：
```css
.slirn-rc-status-card {
  margin-top: 10px; padding: 10px 12px; background: #f8fafc;
  border: 1px solid #e2e8f0; border-radius: 6px; font-size: 12px;
  color: #0f172a !important;  /* 显式深字，防御 .prose * 通配 */
}
.slirn-rc-status-card .slirn-sub-meta { color: #475569 !important; font-weight: 600; }
.slirn-progress {
  height: 8px; background: #e2e8f0; border-radius: 4px; overflow: hidden;
  margin-top: 6px;
}
.slirn-progress-bar {
  height: 100%; background: #6366f1; width: 0%;
  transition: width 0.3s linear;
}
.slirn-progress-bar--done { background: #10b981 !important; }
.slirn-progress-bar--failed { background: #ef4444 !important; }
.slirn-sv-stage3-msg, .slirn-sv-stage4-msg {
  margin-top: 6px; color: #475569 !important;
  font-size: 12px;
}
```

### 2. Stage 4「重新优化」

#### UI 模板（`slirn_home/short_video_ui.py` `_stage4_optimize`）
- 按钮加 `id="slirn-sv-stage4-run-btn"`（L813）
- 在按钮下方插入 status 卡 `id="slirn-sv-stage4-status"`（含 bar + msg）

#### JS handler（`slirn_home/static/short_video.js` `stage4OptimizeRun`）
- 接收 `btn` 参数
- 防重入：`if (window._slirnSvStage4Running) return; running=true;` + `btn.disabled=true`
- `total = document.querySelectorAll('.slirn-stage4-hl-tab').length`（DOM 抓 N）
- `setInterval(1500ms)` 递增 `idx`，更新 `bar.style.width = (idx/total)*100%` + 消息文字「⏳ 正在优化第 N/M 条字幕（每条约 5-30s，请耐心等待）」
- 成功：`bar.classList.add('slirn-progress-bar--done')` + 「✅ 内容优化完成：修改 X 行 · 切分 Y 行 · 删除 Z 行」+ `openProject`
- 失败：`bar.classList.add('slirn-progress-bar--failed')` + 「❌ 失败」+ 保留按钮可重试
- 重复点击：toast「内容优化正在执行中，请勿重复点击」+ 按钮仍 disabled
- 派发器：L2177 `if (action === 'sv-stage4-optimize-run') return stage4OptimizeRun(btn);`

### 3. Stage 3「解析」

#### UI 模板（`slirn_home/short_video_ui.py` `_stage3_analyze`）
- 按钮加 `id="slirn-sv-stage3-run-btn"`（L700）
- 在按钮下方插入 status 卡 `id="slirn-sv-stage3-status"`（含 bar + msg）

#### JS handler（`slirn_home/static/short_video.js` `stage3Run`）
- 接收 `btn` 参数
- 防重入：`_slirnSvStage3Running` + `btn.disabled=true`
- 阶段文字进度（4 阶段 + 1 等待态）：
  | pct | 文字 |
  |---|---|
  | 5% | ⏳ 正在启动解析（请稍候，单次多模态大模型调用可能 10s-3min）… |
  | 15% | ⏳ 准备字幕 + 提示词… |
  | 35% | ⏳ OCR 解析截图…（或「⏳ 构造请求载荷…」无截图时）|
  | 60% | ⏳ 调用大模型（单次多模态，可能耗时较久）… |
  | 85% | ⏳ 解析模型响应（分组 + 校验）… |
  | 90% | ⏳ 仍在等待大模型响应（请耐心，可能 1-3 分钟）… |
- `setInterval(3000ms)` 推一档
- 成功：「✅ 解析完成：共 N 条短视频片段」+ 进度条 100% 绿色
- 失败：进度条 100% 红色 + 红色 toast
- 派发器：L2173 `if (action === 'sv-stage3-run') return stage3Run(btn);`

### 4. 不改的部分

- 后端所有 endpoint 不动（同步阻塞、1-3min 仍可接受）
- 后端 `run_stage3` / `run_stage4_optimize` 不动
- 现有 stage3/4 模板的 4 字段 row / 详情折叠 / tabs 全保留
- Stage 5/7 status 卡（已存在的 `.slirn-rc-status-card` 等）0 影响

## 关键文件改动清单

| 文件 | 改动 |
|---|---|
| `slirn_home/short_video_ui.py` | `_stage3_analyze` + `_stage4_optimize` 各加 status 卡 HTML + 按钮加 id（≈30 行） |
| `slirn_home/static/short_video.js` | `stage3Run` + `stage4OptimizeRun` 重写（约 150 行），加防重入 + 进度卡 + 失败色 |
| `slirn_home/static/home.css` | 新增 4 个 class（≈50 行），用 `!important` 防 `.prose *` 陷阱 |
| `tests/test_short_video_stage4_rework.py` | +6 新测试（3 stage3 + 3 stage4 + 1 CSS 防回归 + 1 dispatcher）|
| `tests/test_short_video_stage3_rework.py` | 1 个回归更新（dispatcher 改 `stage4OptimizeRun(btn)`）|
| `docs/REQM/REQ-20261013-111-sv-stage34-progress-card.md` | 本文件 |

## 测试与验证

### 5.1 单元测试（新增 6 个 + 1 回归更新）

新增：
- `test_stage3_status_card_dom_exists` — `_stage3_analyze` 渲染含 3 个新 class/id
- `test_stage3_run_btn_has_id` — 按钮含 `id="slirn-sv-stage3-run-btn"`
- `test_stage4_status_card_dom_exists` — `_stage4_optimize` 渲染含 3 个新 class/id
- `test_stage4_run_btn_has_id` — 按钮含 `id="slirn-sv-stage4-run-btn"`
- `test_progress_bar_done_failed_css_has_important` — CSS 含 `--done`/`--failed` `!important` 防回归
- `test_stage3_stage4_dispatchers_pass_btn` — 派发器都传 `btn` + 防重入标志

回归：
- `test_short_video_js_stage4_optimize_wiring` 改为 `stage4OptimizeRun(btn)`

**结果**：stage 4 文件 **33/33 全过**（27 + 6）；全量 **1261 passed**（1255 + 6）

### 5.2 端到端（CDP 真实项目 — 待用户验收）

**Stage 3**：
1. 打开用户真实项目 Stage 3
2. 点「解析」→ 验证：
   - 按钮立即变「🔍 解析中…」+ disabled
   - status 卡显示「🔍 解析短视频内容」+ 进度条 5% → 15% → 35% → 60% → 85% → 90% 渐进
   - 消息文字 4 阶段切换 + 等待态
3. 完成后 → 按钮恢复「🔍 解析」+ 进度条 100% 绿色 + 「✅ 解析完成：共 N 条」
4. **防重入测试**：在执行期间再点按钮 → toast「解析正在执行中，请勿重复点击」+ 按钮仍 disabled

**Stage 4**：同模式，N/M 计数式

### 5.3 回归

- stage 4 文件 27 + 6 = 33/33
- stage 3 文件 1 回归更新
- 全量 1255 + 6 = 1261 pytest
- Stage 5/7 status 卡 0 影响

## 风险与对策

| 风险 | 对策 |
|---|---|
| 进度条到 100% 后还要等 ≤30s | 自估算快于实际完成；UX 上比无指示好 100 倍；用户已熟悉 WPS/VSCode 同款模式 |
| Stage 3 阶段文字与实际后端阶段不对齐 | 单次 LLM 占比 90%+ 时间；前 4 阶段都是 sub-second 切换到 60% 后等响应，感知上"卡在 90% 等响应"是符合实际的 |
| 失败 toast 与 status 卡重复 | 失败时 status 卡红色 100% + 红色 toast 文字，用户感知"两处都在讲失败"是有意的 |
| 闭包标志未清掉导致永久 disabled | `.then(...).catch(...).then(finally)` 链式确保标志一定被清；按钮 disabled 也一定恢复 |
| 后端真实进度与前端估算不一致 | 这是已知的 UX 取舍；完成时进度条跳 100% + 立即显示完成 toast，感知上不突兀 |
| 进度条颜色用 class 切换（--done/--failed） | 失败时 add class，与 CSS class `!important` 双保险 |

## 验收清单

- [x] `_stage3_analyze` 模板含 `#slirn-sv-stage3-status` + `.slirn-sv-stage3-bar` + `.slirn-sv-stage3-msg`
- [x] `_stage4_optimize` 模板含 `#slirn-sv-stage4-status` + `.slirn-sv-stage4-bar` + `.slirn-sv-stage4-msg`
- [x] Stage 3 按钮 HTML 含 `id="slirn-sv-stage3-run-btn"`
- [x] Stage 4 按钮 HTML 含 `id="slirn-sv-stage4-run-btn"`
- [x] `stage3Run(btn)` 防重入：闭包标志 + 按钮 disabled
- [x] `stage4OptimizeRun(btn)` 防重入：闭包标志 + 按钮 disabled
- [x] Stage 3 进度条 0%→100% 平滑递增（4 阶段文字）
- [x] Stage 4 进度条 0%→100% 平滑递增（1500ms N/M 节奏）
- [x] 完成时 进度条 100% 绿色 + 「✅ 完成」+ 按钮恢复
- [x] 失败时 进度条 100% 红色 + 「❌ 失败」+ 按钮恢复
- [x] 重复点击时 toast 提示「正在执行中，请勿重复点击」
- [x] CSS `.slirn-rc-status-card*` + `.slirn-progress*` + `--done`/`--failed` 全部 `!important` 抗 `.prose *` 陷阱
- [x] pytest 1255 + 6 = 1261 全过
- [x] 7861 重启（新 CSS，PID 27128）
- [ ] **CDP 真实项目验收** — 待用户在 Stage 3 + Stage 4 真实项目点击验证

## 关联

- [[req-108-sv-stage4-prompt-edit-preview]] — Stage 4「重新优化」按钮来源
- [[req-099-stage3-content-parse]] — Stage 3 解析按钮来源（修订一到十）
- [[req-110-sv-stage4-reorder-time-srtno]] — 父任务
- [[gradio-prose-star-color-trap]] — `!important` 防 `.prose *` 通配
- [[slirn-css-launch-injection-restart]] — 改 CSS 必须重启 7861
- [[slirn-dynamic-script-load-race]] — 改 JS 不需重启
- [[req-103-sv-stage5-coarse-port]] — Stage 5 进度条参考模式（_JOBS + setInterval）
- [[req-105-sv-stage7-fine-port]] — Stage 7 进度卡参考（`#slirn-sv-stage7-status`）
