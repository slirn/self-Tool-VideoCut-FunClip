# REQ-20260920-086 — 执行日志：折叠卡 → 分页 tab + 阶段分隔条

## 背景

### 现象

剪辑工作台里「执行日志」UI **双轨并行**：

1. **红框区（工作台顶部）**：折叠卡 `📜 执行历史`（`[slirn_home/app.py:3720-3795](slirn_home/app.py#L3720-L3795)`），服务端渲染最近 10 条，含子状态、耗时、错误信息
2. **蓝框区（rail 末尾）**：stage 项 `📜 执行日志`，点击切换到右侧日志面板 `slirn-wb-pane-logs`，渲染 chip 过滤 + 列表（前端拉 `/slirn/api/list_logs`）

**问题**：

- **同一类内容在两处显示** — 用户看到顶部折叠卡里有 10 条，再点进 tab 又有更多（重叠感 + 困惑：「我到底看哪个？」）
- **顶部折叠卡无过滤** — 固定最近 10 条，不能按阶段/状态/时间筛选
- **顶部折叠卡无分页** — 任务跑了几百次后无法翻历史
- **视觉混淆**：rail 里 `📜 执行日志` 长得和其他 7 个流水线阶段一样（数字编号 + 标题 + 描述），但它**不是流水线阶段**，没有自动跳转语义 — 用户误以为它是「第 8 阶段」

### 用户期望

> 去掉红框区域的日志显示内容，把相关日志的内容显示在篮框的功能内，篮框是点击相当于一个页签，然后在右侧显示所有的日志，显示日志时要分页显示，还需要在最后一个精简视频阶段和执行日志中间加一个明显的分割条或者是图片之类的，把执行日志跟上面的所有阶段分隔开，明确表示执行日志不属于阶段，表示是另一类内容

拆解：

1. **删除工作台顶部折叠卡**（`_render_exec_history_card` 不再被调用）
2. **蓝框 tab 化** — 「📜 执行日志」点击切到右侧面板，显示**所有**日志
3. **分页** — tab 里加页码 + 上下页
4. **视觉分隔** — 「精剪视频」阶段和「📜 执行日志」之间加明显分割条/图标，标明「执行日志不属于流水线阶段」

## 根因

`📜 执行历史` 折叠卡（REQ-20260918-048）是早期设计（2026-09-18 引入），当时 `slirn-wb-pane-logs` 日志面板还没建好。REQ-20260918-053（2026-09-19）补建了日志面板后，**两套并存**一直未清理。

视觉混淆来自 `_render_workbench` 把 `📜 执行日志` 直接 append 到 `stage_items`（[app.py:3576-3583](slirn_home/app.py#L3576-L3583)），用 `slirn-wb-stage pending` 类，跟其他 7 个流水线阶段外观无差别。

## 验收标准

| ID | 描述 | 优先级 |
|---|---|---|
| AC-1 | 工作台顶部 `📜 执行历史` 折叠卡**不再渲染**（DOM 中找不到 `.slirn-exec-card`） | P0 |
| AC-2 | rail 末尾 `📜 执行日志` 仍可点击，切换到 `slirn-wb-pane-logs` 显示日志 | P0 |
| AC-3 | 日志面板支持分页 — 默认每页 20 条，含「首页 / 上一页 / 下一页 / 末页 + 当前页 / 总页数」 | P0 |
| AC-4 | 日志面板分页导航在过滤条件变化时重置到第 1 页 | P1 |
| AC-5 | rail 上 `精剪视频` 与 `📜 执行日志` 之间有明显视觉分隔（粗线 + 图标 + 文字：「执行日志（不属于流水线阶段）」） | P0 |
| AC-6 | rail 上 `📜 执行日志` 加 `slirn-wb-stage-extra` 类（与 router.js `wbAutoNextMaybe` 的 `:not(.slirn-wb-stage-extra)` 选择器对齐 — 修潜在 BUG） | P1 |
| AC-7 | 后端 `/slirn/api/list_logs` 支持 `offset / limit` 参数，返回 `{items, total, page, page_size}` 完整分页元数据 | P0 |
| AC-8 | 新增 5+ 测试覆盖：分页 offset/limit + 总数返回 + 第一页/第二页数据正确 + 工作台不再渲染折叠卡 + 分隔条存在 | P0 |
| AC-9 | 所有现有测试不回归（218 → ≥223） | P0 |

## 范围

- 改：[slirn_home/app.py](slirn_home/app.py) `_render_workbench` / `_render_exec_logs_pane` / `list_logs` endpoint
- 改：[slirn_home/execution_history.py](slirn_home/execution_history.py) `query_history` 加 `offset` 参数
- 改：[slirn_home/static/router.js](slirn_home/static/router.js) `loadLogs` 加分页 + `logsState.page`
- 改：[slirn_home/static/home.css](slirn_home/static/home.css) 加分隔条 + 分页按钮样式
- 测试：[tests/test_workbench.py](tests/test_workbench.py) +5
- 文档：本 REQ + DESIGN + VERIFICATION
- 不动：rail 其他 7 个阶段、顶部任务信息卡、过滤 chip、LOG_KIND_LABELS

## 不做什么

- ❌ 不删除 `execution_history.load_history` / `format_duration` / `query_history`（函数仍被 logs 面板和后续功能用）
- ❌ 不删 `_render_exec_history_card` 函数本体（保留以备未来若需复用；只是不调用）
- ❌ 不动 rail 7 个流水线阶段的顺序、标题、icon
- ❌ 不改 `wbAutoNextMaybe` 行为（即使之前它正确排除 logs，修 AC-6 让代码与意图一致）

## 关联

- [REQ-20260918-048-exec-history-card.md](REQ-20260918-048-exec-history-card.md) — 工作台顶部折叠卡（要移除的）
- [REQ-20260918-053-execution-history.md](REQ-20260918-053-execution-history.md) — 执行日志面板（要增强的）
- [REQ-20260920-081-execution-log.md](REQ-20260920-081-execution-log.md) — list_logs 端点 + auto_session_id
- [REQ-20260920-084-export-progress-time-restore-logs.md](REQ-20260920-084-export-progress-time-restore-logs.md) — 上一轮日志显示修复
- [REQ-20260920-085-upload-audio-auto-enable-bgm.md](REQ-20260920-085-upload-audio-auto-enable-bgm.md) — 上一轮 BGM 修复
