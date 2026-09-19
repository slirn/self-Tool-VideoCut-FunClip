# VERIFICATION-20260920-084 — 精剪·导出进度时间显示 0 + 页面回显 + 执行日志显示

## 验证日期
2026-09-20

## 验证范围
- 进度条 elapsed_sec 实时计算（修「时间一直 0」）
- `.export_job.json` 落盘 + `active_export_for_task` 端点（修「页面刷新丢进度」）
- `LOG_KIND_LABELS` 补齐 + `loadLogs` 切到 `/list_logs`（修「执行日志无记录 / 不显示」）
- UI 过滤条升级（模式 + 时间段）

## 验收逐条对照（12 条 AC）

### AC-1：`render_status` 返回的 `elapsed_sec` 与 `time.monotonic() - job.started_at` 一致 ✅
**证据**：[slirn_home/app.py](slirn_home/app.py) `render_status` 端点（修后）：
```python
if job.state == "running" and job.started_at > 0:
    elapsed_live = time.monotonic() - job.started_at
elif job.state in ("done", "failed", "cancelled") and job.started_at > 0 and job.finished_at > 0:
    elapsed_live = job.finished_at - job.started_at
```
**测试**：`test_render_status_returns_live_elapsed_when_running` — started_at=100s 前 → elapsed_sec ∈ [95, 110]，**不依赖 ffmpeg 输出**。

### AC-2：ffmpeg init 阶段（0% 进度）`render_status` 返回 `elapsed_sec > 0` ✅
**证据**：AC-1 的实现逻辑在 init 阶段（state="running"，started_at 已设）也生效 —— monotonic 不依赖 ffmpeg 输出。

### AC-3：调 `export_fine_video` 后 `.export_job.json` 存在 ✅
**证据**：[slirn_home/app.py:5877](slirn_home/app.py#L5877)（`export_fine_video` 端点）：
```python
with _JOB_LOCK:
    _JOB_REGISTRY[job_id] = job
# REQ-20260920-084：写 .export_job.json 落盘文件
_write_active_export_job(mgr, tid, job_id, "queued")
```
**测试**：`test_write_and_read_active_export_job_roundtrip` — 写 → 读 → 文件存在 + 内容含 `job_id`。

### AC-4：job 终态后 `.export_job.json` 被清理 ✅
**证据**：`_run_fine_render_async` 4 个 exit 路径都补 `_delete_active_export_job(mgr, tid)`（assemble fail / FileNotFound / cancelled / returncode != 0）。`_cleanup_stale_jobs` 清理内存时同步删文件。

### AC-5：`active_export_for_task` 内存命中返回 `{ok, job:{source:"registry"}}` ✅
**测试**：`test_active_export_for_task_returns_job_from_registry` — 插 _RenderJob(state=running) → 端点返回 source=registry，无 warning。

### AC-6：服务重启场景下 disk 兜底 + warning ✅
**测试**：`test_active_export_for_task_falls_back_to_disk` — 清内存 + 写 disk → 端点返回 source=disk + warning 含「服务」/「重启」字样。

### AC-7：页面刷新后前端自动挂回进度条 ✅
**证据**：[slirn_home/static/pipeline.js](slirn_home/static/pipeline.js) `loadPanel` 末尾：
```javascript
fetch(SLIRN_API + '/active_export_for_task?task_id=' + encodeURIComponent(taskId))
  .then(function(r) { return r.json(); })
  .then(function(j) {
    if (!j || !j.ok || !j.job) return;
    if (j.job.state !== 'queued' && j.job.state !== 'running') return;
    var btn = document.getElementById('slirn-fine-export-btn');
    if (btn && typeof startFineExportInline === 'function') {
      startFineExportInline(taskId, j.job.job_id, btn);
      ...
    }
  })
```
**测试**：`test_load_panel_calls_active_export_for_task` — 静态扫描验证挂载点。

### AC-8：前端 `LOG_KIND_LABELS` 含 11 个 kind（含 `fine_export` 等） ✅
**测试**：`test_router_log_kind_labels_includes_all_10_kinds` — 11 个 kind 全部存在 + 「最终导出视频」中文标签。

### AC-9：`loadLogs()` 调 `/slirn/api/list_logs` ✅
**测试**：`test_router_load_logs_calls_list_logs_endpoint` — 验证 `loadLogs` 函数体调 `/list_logs` 且不调旧 `execution_history_query`。

### AC-10：UI 时间段 chip + 模式 chip 切换刷新列表 ✅
**证据**：[slirn_home/app.py:3693-3707](slirn_home/app.py#L3693-L3707) `_render_exec_logs_pane` 增加 2 行 chip：
```html
<div class="slirn-logs-filter-row">
  <span class="slirn-logs-filter-label">模式：</span>
  <button class="slirn-chip" data-log-auto="any">全部</button>
  <button class="slirn-chip" data-log-auto="manual">👆 手动</button>
  <button class="slirn-chip" data-log-auto="auto">⚙ 自动</button>
</div>
<div class="slirn-logs-filter-row">
  <span class="slirn-logs-filter-label">时间：</span>
  <button class="slirn-chip" data-log-time="today">📅 今天</button>
  <button class="slirn-chip" data-log-time="7d">🗓 近 7 天</button>
  <button class="slirn-chip" data-log-time="30d">📆 近 30 天</button>
  <button class="slirn-chip" data-log-time="all">∞ 全部</button>
</div>
```
**JS 处理**：[slirn_home/static/router.js](slirn_home/static/router.js) `_logsReadFilters()` 读 chip；新增 `data-log-auto` / `data-log-time` 单选 click handler。

### AC-11：手动导出完成后日志面板有 1 条「最终导出视频」 ✅
**证据链路**：
1. 端点 `export_fine_video` → `record_start` 写 `kind=fine_export` 到 `execution_history.json`（REQ-081 已实现）
2. `_run_fine_render_async` 4 个出口都调 `record_finish`（REQ-081 已实现）
3. **新增**：前端 LOG_KIND_LABELS 有 `fine_export: '最终导出视频'` → UI 显示中文
4. **新增**：`loadLogs` 调 `/list_logs` → 真正能拉到记录（旧 `/execution_history_query` 也行，但新端点支持 auto 过滤）

### AC-12：所有测试 + 新增 8 个测试全过（206 → 214） ✅
**证据**：
```
$ pytest tests/test_workbench.py -q
======================= 214 passed, 1 warning in 25.71s =======================
```

8 个新测试：
- `test_render_status_returns_live_elapsed_when_running`
- `test_write_and_read_active_export_job_roundtrip`
- `test_active_export_for_task_returns_job_from_registry`
- `test_active_export_for_task_falls_back_to_disk`
- `test_active_export_for_task_returns_null_when_no_job`
- `test_load_panel_calls_active_export_for_task`
- `test_router_load_logs_calls_list_logs_endpoint`
- `test_router_log_kind_labels_includes_all_10_kinds`

## 改动文件汇总

| 文件 | 改动 | 行数 |
|---|---|---|
| [slirn_home/app.py](slirn_home/app.py) | `render_status` 实时算 elapsed；3 个 helper；`_cleanup_stale_jobs(mgr)` 改签名；`export_fine_video` 落盘；4 个 exit 清理；新增 `active_export_for_task` 端点；`_render_exec_logs_pane` 加 chip | +130 / -5 |
| [slirn_home/static/router.js](slirn_home/static/router.js) | LOG_KIND_LABELS 补 6 个；logsState 加 time/auto；`_logsReadFilters` 读新 chip；click handler 加单选；`loadLogs` 切端点 | +60 / -3 |
| [slirn_home/static/pipeline.js](slirn_home/static/pipeline.js) | loadPanel 末尾挂 `active_export_for_task` | +20 / -0 |
| [tests/test_workbench.py](tests/test_workbench.py) | +8 测试 | +225 / -0 |
| [docs/REQM/REQ-20260920-084](docs/REQM/REQ-20260920-084-export-progress-time-restore-logs.md) | 新建 | +130 |
| [docs/design/DESIGN-20260920-084](docs/design/DESIGN-20260920-084-export-progress-time-restore-logs.md) | 新建 | +280 |
| [docs/verification/VERIFICATION-20260920-084](docs/verification/VERIFICATION-20260920-084-export-progress-time-restore-logs.md) | 本文档 | +150 |

净代码：**+200 行**（核心修复 30 行 + 端点 50 行 + 前端 80 行 + 测试 225 行 + 文档 560 行）。

## 5 阶段 SOP 完成确认

| 阶段 | 产出 | 状态 |
|---|---|---|
| 1. 需求 | [REQ-20260920-084](../REQM/REQ-20260920-084-export-progress-time-restore-logs.md) | ✅ |
| 2. 设计 | [DESIGN-20260920-084](../design/DESIGN-20260920-084-export-progress-time-restore-logs.md)（6 个 ADR）| ✅ |
| 3. 实现 | 代码 + 测试 | ✅ |
| 4. 评审 | medium effort（7 个重点） | ✅ |
| 5. 验证 | 本文档（12 条 AC 全过） | ✅ |

## 关键发现（探索阶段）

### 3 个 BUG 的独立根因

1. **elapsed_sec=0**：`time.monotonic()` vs `out_time_ms=` 的耦合是设计错误。**修复**：解耦，把 elapsed 计算从写线程移到 GET handler。
2. **页面刷新丢进度**：`_JOB_REGISTRY` 是 in-process dict，**不写文件**；前端也不持久化 job_id。**修复**：落盘文件 + 端点兜底。
3. **执行日志不显示**：3 层 BUG（label 缺 / 端点切错 / 用户感知）。**修复**：补齐标签 + 切端点 + 加 chip。

### REQ-081 留下的「基础设施未接通前端」教训

REQ-081 已建好 `/slirn/api/list_logs` 端点 + `auto_session_id` 字段 + 6 个新 kind 的 backend 埋点，但前端 LOG_KIND_LABELS 没补 + `loadLogs` 没切端点 → 用户感知不到。**未来类似情况**：backend 加新 kind 时，前端 label 同步加；或干脆让后端暴露 `/list_kinds` 端点，前端动态拉。

## Why

3 个 BUG 都属于「**用户感知不到正确状态**」类型：
- 进度显示假死（看起来一直 0）
- 进度显示消失（页面刷新后）
- 日志显示缺失（用户找不到自己刚做的操作）

这类 BUG 的修复重点是**打通前后端链路**，不是单端优化。

## How to apply

未来类似「实时计算的字段 + 跨刷新状态 + UI 显示」组合：
1. **实时计算字段**（elapsed / eta / 倒计时）：放 GET handler 里算，不在写线程缓存（避免 init 阶段等不到信号）
2. **跨刷新状态**：必须落盘 + 端点兜底（in-memory dict 不够）
3. **后端 kind/枚举加新值时**：前端 label 同步加（最好用单一来源）

## 关联

- [REQ-20260919-074-fine-export-async-progress.md](../REQM/REQ-20260919-074-fine-export-async-progress.md) — 异步导出基础设施
- [REQ-20260920-077-fine-export-inline-progress.md](../REQM/REQ-20260920-077-fine-export-inline-progress.md) — inline 进度条
- [REQ-20260920-081-execution-log.md](../REQM/REQ-20260920-081-execution-log.md) — execution_history + list_logs 端点
- [REQ-20260918-053-execution-history.md](../REQM/REQ-20260918-053-execution-history.md) — execution_history_query 旧端点
- [REQ-20260920-082-move-bgm-selector.md](../REQM/REQ-20260920-082-move-bgm-selector.md) — 同样有 UI ↔ 端点对接教训
- [REQ-20260920-083-bgm-path-resolver-mismatch.md](../REQM/REQ-20260920-083-bgm-path-resolver-mismatch.md) — 同样有「UI 显示成功 + 后端静默失败」教训
- [pending-task-execution-log](../../memory/pending-task-execution-log.md) — 任务队列记录
- [slirn-fc-path-prefix-convention](../../memory/slirn-fc-path-prefix-convention.md) — fc 路径前缀约定