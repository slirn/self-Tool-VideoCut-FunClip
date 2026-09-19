# DESIGN-20260920-084 — 精剪·导出进度时间显示 0 + 页面回显 + 执行日志显示

## Context

REQ-20260920-084 修 3 个关联 BUG：
1. 进度条 elapsed_sec 一直 0（ffmpeg init 阶段 + 任何无 progress 行的间隔）
2. 页面刷新后 in-flight 进度丢失（job_id 无持久化）
3. 执行日志面板：记录写盘但前端 KIND 标签缺 + 调旧端点

5 阶段 SOP 第 2 阶段。

## ADR-084-1：在 `render_status` GET handler 实时算 elapsed_sec

**决策**：把 `elapsed_sec` 计算从 `_run_fine_render_async` 主循环移到 `render_status` GET handler。

**理由**：
- 旧代码只在 `out_time_ms` 行 + 0.5s 节流时更新 `job.elapsed_sec`；ffmpeg init 阶段 / 编码卡顿 / 长 GOP 时不变
- `time.monotonic()` 单调递增，与 `started_at` 减法永远准；不依赖 ffmpeg 输出
- 0 线程改动，0 风险；唯一改动：3 行 `if` 分支

**考虑过的方案**：
| 方案 | 优劣 |
|---|---|
| **A. GET handler 实时算**（采纳） | 简单；零线程改动；前端每 1.5s 轮询 → 永远拿到最新 |
| B. 主循环加 1 秒 tick 线程单独更新 elapsed | 多线程协调复杂；signal/cancel 都要管 |
| C. 客户端自己算（前端记录 start_time，render_status 返回 started_at_wall） | 改前端格式；多端时间可能不一致 |
| D. ffmpeg 加 `-progress` flag 改用 `-vstats_file` | ffmpeg 命令变更风险大 |

**采纳 A**：直接 `time.monotonic() - job.started_at`（如果 running）或 `job.finished_at - job.started_at`（如果终态）。

## ADR-084-2：job_id 落盘文件 `.export_job.json`

**决策**：每个 task 维护 1 个 `.export_job.json` 文件（task 级），记录当前 in-flight job 的 job_id + state + started_at。

**理由**：
- 跨刷新 / 跨服务重启：内存 dict 丢，文件不丢
- task 级隔离天然（每个 task 自己的 `outputs/` 目录）
- 文件格式简单（3 字段）；并发安全靠 atomic write（write_text 单调用原子性）

**考虑过的方案**：
| 方案 | 优劣 |
|---|---|
| **A. task 级文件**（采纳） | 简单；天然 task 隔离；清理时机可控 |
| B. 全局文件 `_job_registry.json`（所有 task 共享） | 并发写复杂；文件会很大 |
| C. localStorage（前端） | 跨设备 / 跨浏览器不行；用户清缓存丢 |
| D. SQLite | 杀鸡用牛刀；现有约定是 JSON |

**采纳 A**。

**文件清理策略**：
- job 进入终态（done/failed/cancelled）后 5 分钟（与 `_JOB_TTL_SEC` 对齐）
- `_cleanup_stale_jobs` 清理内存时同步清理文件
- 用户取消 / 失败时立即清理（不等 5 分钟）
- 前端最后一次轮询拿到结果后**前端负责删文件**（通过现有 cancel / 终态处理路径触发 DELETE 端点 / 或下次 active_export_for_task 检测到终态后自删）

**采纳**：5 分钟保留期 + 终态立即触发删除（前端取消 / 失败时） + `_cleanup_stale_jobs` 兜底。

## ADR-084-3：新增 `active_export_for_task` 端点（registry 优先 + 落盘兜底）

**决策**：新建 `GET /slirn/api/active_export_for_task?task_id=X`，先查 `_JOB_REGISTRY`（权威），落盘 `.export_job.json` 兜底（服务重启场景）。

**响应**：
```json
// in-memory 命中
{ok: true, job: {job_id, state: "running", started_at, source: "registry"}}

// 落盘命中（内存无）
{ok: true, job: {job_id, state: "running", started_at, source: "disk", warning: "..."}}

// 无 in-flight
{ok: true, job: null}
```

**理由**：
- registry 命中时 100% 准确（实时状态）
- 落盘命中时不可靠（ffmpeg 可能已死）→ 必须有 `warning` 提示用户
- 区分 source 让前端能识别「服务重启了」并显示提示

**考虑过的方案**：
| 方案 | 优劣 |
|---|---|
| **A. registry + disk 二级查询**（采纳） | 准确性 + 鲁棒性兼顾；用户感知友好 |
| B. 只查 disk | 服务运行中也慢（每次读文件）；registry 是 in-memory dict 更快 |
| C. 落盘 + 写 task 状态时同步 ffmpeg PID | 复杂度高；跨平台 PID 不一致 |

**采纳 A**。

## ADR-084-4：前端日志面板接入 `/list_logs`（REQ-081 新端点）

**决策**：`loadLogs()` 改调 `/slirn/api/list_logs`，payload 加 `time_from` / `time_to` / `auto` 字段。

**理由**：
- REQ-081 已建好端点（[app.py:6447-6507](slirn_home/app.py#L6447)），支持 `auto` / `time_from` / `time_to` 过滤
- 旧端点 `execution_history_query`（REQ-053）不支持这些
- 不删旧端点（pipeline.js 等可能在用）；只是切换 logs 面板的调用

**考虑过的方案**：
| 方案 | 优劣 |
|---|---|
| **A. 切换到新端点**（采纳） | 新能力生效；代码改动小 |
| B. 两端点并存，UI 双 tab | 过度设计；用户认知成本 |
| C. 改旧端点加过滤 | 影响其他调用方；风险大 |

**采纳 A**。

## ADR-084-5：补齐 `LOG_KIND_LABELS` 6 个 kind

**决策**：前端 `LOG_KIND_LABELS`（[router.js:935-942](slirn_home/static/router.js#L935-L942)）补 6 个 kind，与后端 `execution_history.KIND_LABELS`（[execution_history.py:46](slirn_home/execution_history.py#L46)）对齐。

**补全清单**：
| 后端 kind | 前端标签 |
|---|---|
| `rough_cut_link_person` | 关联人员ID |
| `rough_compose_delete` | 删除粗剪成品 |
| `fine_ai_layout` | AI 智能布局 |
| `fine_bg_detect` | 检测区域 |
| `fine_preview` | 生成预览 |
| `fine_export` | 最终导出视频 |

**理由**：
- 前端 `_renderLogsList` 用 `LOG_KIND_LABELS[it.kind] || it.kind` —— 缺则显示原始 key
- 用户「找不到导出日志」可能就是因为 kind 显示 `fine_export` 而非「最终导出视频」

**考虑过的方案**：
| 方案 | 优劣 |
|---|---|
| **A. 前端硬编码补齐**（采纳） | 简单；与现有模式一致 |
| B. 后端暴露 `/list_kinds` API，前端动态拉 | 单一来源更稳；本 REQ 改动大 |
| C. 用 `_formatLogsKind` 函数后端 label 字段 | 后端加字段；侵入大 |

**采纳 A**（最小改动）。理想是 B（未来 REQ）。

## ADR-084-6：UI 过滤条升级（时间段 chip + 模式 chip）

**决策**：在「📜 执行日志」面板（[app.py:3596-3625](slirn_home/app.py#L3596-L3625)）增加 2 组 chip：
- **时间段**：今天 / 近 7 天 / 近 30 天 / 全部（默认「近 7 天」）
- **模式**：全部 / 仅手动 / 仅自动（默认「全部」）

**理由**：
- 用户实测：自动流程会跑几十条记录 → 全显示太乱；按时间段过滤后清爽
- 「自动 / 手动」过滤让用户能区分「流程配置」 vs 「我手动点的」

**最小侵入**：HTML 加 chip 元素 + JS `logsState` 加 2 字段 + chip 点击后调 `loadLogs()`。**不动 backend**（list_logs 已支持这些参数）。

## 实施步骤

### Phase 1 — REQ ✓
产出：[docs/REQM/REQ-20260920-084-export-progress-time-restore-logs.md](docs/REQM/REQ-20260920-084-export-progress-time-restore-logs.md)

### Phase 2 — DESIGN（本文件）

### Phase 3 — 实现

**Step 1**：`render_status` 实时算 elapsed（[slirn_home/app.py:5863](slirn_home/app.py#L5863)）

**Step 2**：3 个 helper + `_active_export_job_path` + 写 / 读 / 删（[slirn_home/app.py](slirn_home/app.py) 紧邻 `_cleanup_stale_jobs`）

**Step 3**：`export_fine_video` 端点创建 job 后调 `_write_active_export_job`（[slirn_home/app.py:5822](slirn_home/app.py#L5822) 之后）

**Step 4**：`_run_fine_render_async` 4 个出口补 `_delete_active_export_job(tid)`

**Step 5**：`_cleanup_stale_jobs` 同步删文件（[slirn_home/app.py:2264](slirn_home/app.py#L2264)）

**Step 6**：新增 `active_export_for_task` 端点（紧邻 `render_status`）

**Step 7**：前端 `LOG_KIND_LABELS` 补 6 个 + `loadLogs` 切到 `/list_logs`（[slirn_home/static/router.js:935-942](slirn_home/static/router.js#L935-L942) + [router.js:906-933](slirn_home/static/router.js#L906-L933)）

**Step 8**：前端 `loadPanel` 末尾挂 `active_export_for_task` → `startFineExportInline`（[slirn_home/static/pipeline.js:599-638](slirn_home/static/pipeline.js#L599-L638)）

**Step 9**：UI 加时间段 chip + 模式 chip（[slirn_home/app.py:3596-3625](slirn_home/app.py#L3596-L3625) `_render_exec_logs_pane`）

**Step 10**：测试（5 个）

### Phase 4 — Review

- 重点 1：`render_status` 实时算 elapsed 的幂等性（并发轮询不冲突）
- 重点 2：`.export_job.json` 写入失败兜底（log.warning + 不阻塞）
- 重点 3：4 个 exit 路径都补 `_delete_active_export_job` —— 不能漏 cancel / fail
- 重点 4：`active_export_for_task` 在 `_JOB_REGISTRY` 持锁时不能阻塞太久（dict 遍历 + return，O(N) 短）
- 重点 5：UI 时间段 chip 用 ISO 格式（复用 `list_logs` 的 `_parse_iso`）
- 重点 6：测试用真文件 + monkeypatch，避免 mock 误判
- 重点 7：`node --check static/router.js` + `static/pipeline.js`

### Phase 5 — 验证

1. `pytest tests/test_workbench.py -q` → 211 passed
2. 重启 slirn
3. **V1**：上传视频 → 点导出 → 进度条 `已用 00:00:01` 立即刷新（验证 elapsed）
4. **V2**：导出 30 秒后 F5 → 进度条重新出现 + 继续累加（验证回显）
5. **V3**：导出完成 → 切「📜 执行日志」→ 看到「✅ 最终导出视频」1 条（验证日志显示）
6. **V4**：kill slirn 进程 → 重启 → 调 active_export_for_task → 返回 disk + warning
7. **V5**：点「仅自动」chip → 列表只剩 auto=true
8. **V6**：点「近 30 天」chip → 列表显示范围改变
9. **V7**：5 分钟后 → `.export_job.json` 消失（_cleanup_stale_jobs 兜底）

## 关键文件改动汇总

| 文件 | 改动 | 估算行数 |
|---|---|---|
| [slirn_home/app.py](slirn_home/app.py) | render_status + helpers + 端点 + 落盘/清理 | +90 / -5 |
| [slirn_home/static/router.js](slirn_home/static/router.js) | LOG_KIND_LABELS + loadLogs | +25 / -5 |
| [slirn_home/static/pipeline.js](slirn_home/static/pipeline.js) | loadPanel 末尾挂载 | +20 / -0 |
| [slirn_home/static/home.css](slirn_home/static/home.css) | 时间段 / auto chip 样式 | +15 / -0 |
| [slirn_home/app.py](slirn_home/app.py) | _render_exec_logs_pane 加 chip | +30 / -0 |
| [tests/test_workbench.py](tests/test_workbench.py) | +5 测试 | +120 / -0 |
| [docs/REQM/](docs/REQM/) | 新建 | +130 |
| [docs/design/](docs/design/) | 新建（本文件） | +280 |
| [docs/verification/](docs/verification/) | 新建 | +120 |

净代码约 **+280 行**。

## 风险与边界

| 风险 | 处理 |
|---|---|
| `.export_job.json` 与 ffmpeg 状态不一致 | endpoint 注明 source=disk + warning |
| 多个浏览器 tab 同时开 | 都挂轮询，无副作用 |
| `_JOB_REGISTRY` 与文件不同步 | `_cleanup_stale_jobs` 同时清两边；TTL 5 分钟 |
| 老的 `execution_history_query` 端点 | 保留（pipeline.js 可能用）；本 REQ 不删 |
| 时间段 chip 的 ISO 格式 | 复用 `_parse_iso` |
| 用户取消时文件清理 | 取消分支补 `_delete_active_export_job` |
| 测试中 mock ffmpeg | 用 `_run_fine_render_async` 直接调（不实际跑 ffmpeg）|

## 复用现有基础设施

- `_JOB_REGISTRY` / `_JOB_LOCK` / `_RenderJob`（[app.py:2259-2256](slirn_home/app.py#L2259)）
- `_run_fine_render_async` 4 个 exit 路径
- `startFineExportInline`（[router.js:3406-3480](slirn_home/static/router.js#L3406)）—— 复用，不改
- `/slirn/api/list_logs` 端点 + `_parse_iso` + `query_history`（REQ-081）
- `_render_exec_logs_pane` 现有 HTML 结构（[app.py:3596-3625](slirn_home/app.py#L3596-L3625)）
- `mgr.tasks_dir / tid / outputs/` 路径约定

## 关联

- [REQ-20260919-074-fine-export-async-progress.md](docs/REQ-20260919-074-fine-export-async-progress.md) — 异步导出基础设施
- [REQ-20260920-077-fine-export-inline-progress.md](docs/REQ-20260920-077-fine-export-inline-progress.md) — inline 进度条
- [REQ-20260920-081-execution-log.md](docs/REQ-20260920-081-execution-log.md) — execution_history + list_logs 端点
- [REQ-20260918-053-execution-history.md](docs/REQ-20260918-053-execution-history.md) — execution_history_query 旧端点
- [REQ-20260920-082-move-bgm-selector.md](docs/REQ-20260920-082-move-bgm-selector.md) — 同样有 UI ↔ 端点对接教训
- [REQ-20260920-083-bgm-path-resolver-mismatch.md](docs/REQ-20260920-083-bgm-path-resolver-mismatch.md) — 同样有「UI 显示成功 + 后端静默失败」教训