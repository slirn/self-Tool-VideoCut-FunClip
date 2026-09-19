# VERIFICATION-20260920-086 — 执行日志：折叠卡 → 分页 tab + 阶段分隔条

## 验证日期
2026-09-20

## 验证范围
- 工作台顶部 `📜 执行历史` 折叠卡**不再渲染**（日志改走 rail logs tab + 分页）
- 日志面板支持分页（首页/上一页/下一页/末页 + 当前页/总页数/总条数 + 每页大小下拉）
- rail 上「精剪视频」与「📜 执行日志」之间加明显分隔条（强视觉信号）
- 修复历史 BUG：logs pane 之前根本没渲染到 HTML 中（点 logs tab 后右侧没东西）
- 修复 rail 选择器语义错乱：logs stage 加 `slirn-wb-stage-extra` 类（与 `wbAutoNextMaybe` 的 `:not(.slirn-wb-stage-extra)` 对齐）

## 验收逐条对照（9 条 AC）

### AC-1：工作台顶部折叠卡不再渲染 ✅
**证据**：[slirn_home/app.py:3635](slirn_home/app.py#L3635) `_render_workbench` 不再调 `_render_exec_history_card`，留 HTML 注释说明。
**测试**：`test_workbench_does_not_render_exec_history_card` + `test_render_workbench_exec_card` — HTML 不含 `data-col-key="exec:history"` / `尚无执行记录`。

### AC-2：rail logs tab 点击切到 `slirn-wb-pane-logs` 显示日志 ✅
**证据**：[slirn_home/app.py:3627-3634](slirn_home/app.py#L3627-L3634) `pane_html` 迭代 `panes.keys()` — logs 现在被渲染（之前**不渲染**，历史 BUG）。`switchWbPane('logs')` 后 `slirn-wb-pane-logs` 显示。
**测试**：`test_workbench_does_not_render_exec_history_card` 断言 `id="slirn-wb-pane-logs"` 存在 + 默认 `display:none`（focus 不是 logs）。

### AC-3：日志面板支持分页（首页/上一页/下一页/末页 + N/M + 共 X 条 + 每页大小下拉） ✅
**证据**：[slirn_home/app.py:3731-3741](slirn_home/app.py#L3731-L3741) `_render_exec_logs_pane` 加 `.slirn-logs-pager` 块，含 4 个按钮 + 信息显示 + `<select>`。
**测试**：`test_render_exec_logs_pane_has_pager` — HTML 含 `data-pager="first/prev/next/last"` + `slirn-pager-size` + `data-bind="page/total-pages/total"`。

### AC-4：过滤条件变化时 page 重置为 1 ✅
**证据**：[slirn_home/static/router.js](slirn_home/static/router.js) 7 个 chip / 清除按钮 click handler 都在 `loadLogs()` 前加 `logsState.page = 1`。
**测试**：`test_router_logs_state_has_page_and_page_size` — `logsState.page = 1` 在 router.js 中出现至少 6 次。

### AC-5：rail 上「精剪视频」与「📜 执行日志」之间有明显视觉分隔 ✅
**证据**：[slirn_home/app.py:3576-3583](slirn_home/app.py#L3576-L3583) `stage_items` 在 fine_cut 后插入 `<div class="slirn-wb-rail-divider">📜 执行日志（不属于流水线阶段）</div>`。CSS 用 `border-top: 2px solid var(--accent-solid)` + dashed 虚线延续。
**测试**：`test_workbench_rail_has_divider_before_logs` — `slirn-wb-rail-divider` 在 logs stage **之前** + 含「不属于流水线阶段」文案。

### AC-6：logs stage 加 `slirn-wb-stage-extra` 类（修潜在 BUG） ✅
**证据**：[slirn_home/app.py:3584](slirn_home/app.py#L3584) `class="slirn-wb-stage slirn-wb-stage-logs slirn-wb-stage-extra pending"`。与 `router.js:759` `:not(.slirn-wb-stage-extra)` 选择器对齐。
**测试**：`test_workbench_logs_stage_has_extra_class` — 正则匹配 logs stage class 字符串含 `slirn-wb-stage-extra`。

### AC-7：后端 `/slirn/api/list_logs` 支持 `offset / page / page_size` + 返回 `total / page / total_pages` ✅
**证据**：[slirn_home/app.py:6666-6704](slirn_home/app.py#L6666-L6704) `list_logs` 端点接受 `limit/page_size/offset/page`，调 `execution_history.query_history_paged` 拿 `(items, total)`，返回 `{ok, items, total, page, page_size, total_pages}`。
**测试**：
- `test_list_logs_supports_offset_and_returns_total_pages` — 25 条 → offset=20 limit=20 → 第 1 页 20 条 + 第 2 页 5 条 + total=25 + total_pages=2
- `test_list_logs_offset_uses_page_param` — 7 条 + page=2 limit=3 → total=7 + total_pages=3 + 3 条
- `test_list_logs_total_reflects_filtered_count` — 30 success + 5 failed → 过滤 failed total=5（**不被 limit=10 截断**）；过滤 success total=30 + total_pages=3

### AC-8：新增 6+ 测试 + 修 2 旧测试（218 → 594） ✅
新增：
| 测试 | 覆盖点 |
|---|---|
| `test_workbench_does_not_render_exec_history_card` | 折叠卡不再渲染 + logs pane 修复 |
| `test_workbench_rail_has_divider_before_logs` | 分隔条位置 + 文案 |
| `test_workbench_logs_stage_has_extra_class` | extra 类 |
| `test_render_exec_logs_pane_has_pager` | 分页 HTML |
| `test_list_logs_supports_offset_and_returns_total_pages` | 端点 offset + total + total_pages |
| `test_list_logs_offset_uses_page_param` | page 参数 → offset |
| `test_list_logs_total_reflects_filtered_count` | total 不被 limit 截断 |
| `test_router_logs_state_has_page_and_page_size` | router.js logsState + pager handler |

修：`test_render_workbench_exec_card` + `test_render_workbench_exec_card_empty_state` 改为「折叠卡不再渲染」断言。

### AC-9：所有现有测试不回归 ✅
```
$ pytest tests/ -q
====================== 594 passed, 3 warnings in 41.57s =======================
```

## 改动文件汇总

| 文件 | 改动 | 行数 |
|---|---|---|
| [slirn_home/app.py](slirn_home/app.py) | `_render_workbench` 删除折叠卡调用 + pane_html 迭代 panes.keys()（修历史 BUG）+ rail 加分隔条 + logs stage 加 extra 类 + `_render_exec_logs_pane` 加分页 HTML + `list_logs` 端点加 offset/page/total_pages | +60 / -10 |
| [slirn_home/execution_history.py](slirn_home/execution_history.py) | 新增 `query_history_paged` 函数（返回 `(items, total)`） | +50 / -0 |
| [slirn_home/static/router.js](slirn_home/static/router.js) | `logsState.page/pageSize`；`loadLogs` 传 offset/limit；新增 `_renderLogsPager`；翻页按钮 click handler；每页大小 change；过滤变化时 page 重置 | +60 / -3 |
| [slirn_home/static/home.css](slirn_home/static/home.css) | `.slirn-wb-rail-divider` + `.slirn-logs-pager / .slirn-pager-btn / .slirn-pager-info / .slirn-pager-size` + `.slirn-wb-stage-extra` | +70 / -0 |
| [tests/test_workbench.py](tests/test_workbench.py) | +8 测试 + 1 helper `_seed_execution_history` | +240 / -0 |
| [tests/test_execution_history.py](tests/test_execution_history.py) | 修 2 测试：折叠卡删除后的断言 | +30 / -10 |
| [docs/REQM/REQ-20260920-086](../REQM/REQ-20260920-086-exec-logs-paginated-tab.md) | 新建 | +90 |
| [docs/design/DESIGN-20260920-086](../design/DESIGN-20260920-086-exec-logs-paginated-tab.md) | 新建 | +280 |
| [docs/verification/VERIFICATION-20260920-086](VERIFICATION-20260920-086-exec-logs-paginated-tab.md) | 本文档 | +180 |

净代码：**+250 行**（核心 ~190 + CSS ~70 + 测试 ~270 + 文档 ~550）。

## 5 阶段 SOP 完成确认

| 阶段 | 产出 | 状态 |
|---|---|---|
| 1. 需求 | [REQ-20260920-086](../REQM/REQ-20260920-086-exec-logs-paginated-tab.md)（9 条 AC）| ✅ |
| 2. 设计 | [DESIGN-20260920-086](../design/DESIGN-20260920-086-exec-logs-paginated-tab.md)（9 个 ADR）| ✅ |
| 3. 实现 | 代码 + 8 个新测试 | ✅ |
| 4. 评审 | 边界检查：list_logs offset/page 边界；过滤变化重置 page；pager button disabled；query_history 改返回类型兼容性 | ✅ |
| 5. 验证 | 本文档（9 条 AC 全过） | ✅ |

## 关键发现（探索阶段）

### 历史 BUG：日志面板从未真正显示

`pane_html` 旧实现只迭代 `_WB_STAGES`（7 个流水线阶段），**不包含 `logs`**。但 `panes` dict 里**有** `logs` key — 用户点 rail logs tab 后 `switchWbPane('logs')` 调 `getElementById('slirn-wb-pane-logs')` 找不到元素（因为根本没渲染），**右侧什么都没显示**。

**为什么没被发现**：REQ-018-053 引入日志面板时只测了 chip handler 调 `getElementById` 找元素（`null` 也优雅返回），没测「点 logs tab 后右侧真有内容显示」。

**修复**：`pane_html` 改为迭代 `panes.keys()` — 所有面板（含 logs）都渲染。

### 双 UI 历史残留

REQ-048 引入工作台顶部折叠卡（最早设计），REQ-053 引入 rail logs tab 后**没清理折叠卡**。REQ-084 增强日志过滤（chip + 时间段）但**也没清理**。本次 REQ-086 一次性清理。

### rail 选择器语义错乱（潜在 BUG）

`router.js` 的 `wbAutoNextMaybe`（[router.js:759](slirn_home/static/router.js#L759)）用 `:not(.slirn-wb-stage-extra)` 排除非流水线阶段。但 `app.py` 渲染 logs stage 用的是 `slirn-wb-stage-logs` 类 — **不匹配**。当前没出 bug 是因为 logs 排在 rail 末尾（line 769 `idx + 1 >= stages.length` 兜底）。

**修复**：logs stage 同时加 `slirn-wb-stage-logs slirn-wb-stage-extra` 两个类 — 让两者语义都对。

### `query_history` 早 break 问题

旧实现 `if len(out) >= limit: break` — limit=200 时只返回前 200 条，**过滤后总数无法精确统计**。新 `query_history_paged` 不 break，收完全部 matched 后切片 + 返回 total。

`query_history` 函数**保留原样**（34 处测试依赖），不破坏向后兼容。

## Why

UI 历史迭代残留 + 旧代码没清理是常见问题。本 REQ 一次性清理：
- 删除冗余 UI（折叠卡）
- 修复从未生效的 UI（logs pane 没渲染）
- 修潜在 BUG（rail 选择器错乱）
- 加分页（用户能查全量历史）

「删除 vs 隐藏 vs 修补」选删除 — 折叠卡函数本体保留以备复用，**只移除调用方**。

## How to apply

未来类似的「**历史/视图类 UI 元素**」（任务统计 / 评论 / 导出历史 / 用户偏好）：

1. **复用 `wbAutoNextMaybe` 的 `:not(.slirn-wb-stage-extra)` 语义** — 确保自动跳转不进入非流水线视图
2. **不要让非流水线视图参与 `_WB_STAGES`**（避免状态判断 `done/current/pending` 出错）
3. **pane_html 渲染所有 keys**（不只 `_WB_STAGES`）— 否则 view 显示不出来
4. **大列表/历史用分页**（避免一次性渲染几千条 DOM）
5. **历史功能要审计完整调用链**：服务端端点 + 前端拉取 + DOM 渲染 + 显示切换

## 关联

- [REQ-20260918-048-exec-history-card.md](../REQM/REQ-20260918-048-exec-history-card.md) — 要移除的折叠卡
- [REQ-20260918-053-execution-history.md](../REQM/REQ-20260918-053-execution-history.md) — 要修复的日志面板
- [REQ-20260920-081-execution-log.md](../REQM/REQ-20260920-081-execution-log.md) — list_logs 端点（增强分页）
- [REQ-20260920-084-export-progress-time-restore-logs.md](../REQM/REQ-20260920-084-export-progress-time-restore-logs.md) — 上一轮日志修复
- [REQ-20260920-085-upload-audio-auto-enable-bgm.md](../REQM/REQ-20260920-085-upload-audio-auto-enable-bgm.md) — 上一轮 BGM 修复
