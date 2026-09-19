# REQ-20260920-084 — 精剪·导出进度时间显示 0 + 页面回显 + 执行日志显示

## 背景

用户反馈「导出最终视频」3 个关联问题：

1. **进度时间始终 0小时0分0秒** —— 用户看到 `00:00:00`（HH:MM:SS）一直不动
2. **页面回显缺失** —— 重新进入页面（刷新 / 重启服务）时，如果导出最终视频仍在执行，UI 完全丢失进度（按钮变 idle，但后台 ffmpeg 还在烧 CPU）
3. **执行日志无记录 / 不显示** —— 用户点了导出最终视频，但「执行日志」面板找不到；即使有，也显示原始 key `fine_export` 而不是「最终导出视频」

### 根因（探索阶段已确认）

| # | 现象 | 根因 |
|---|------|------|
| 1 | elapsed_sec=0 一直显示 | [`_run_fine_render_async`](slirn_home/app.py#L2460-L2468) 只在**读到 ffmpeg `out_time_ms=` 行**时 + 0.5s 节流后才更新 `job.elapsed_sec`。ffmpeg init 阶段 / 长间隔没输出时，`elapsed_sec` 始终 0；`render_status` 返回 `0` → `_fmtSec(0)` = `"00:00:00"` |
| 2 | 页面刷新丢失进度 | `_JOB_REGISTRY` 是**进程内内存 dict**（[app.py:2259](slirn_home/app.py#L2259)），**不写文件**；前端也**不持久化 job_id 到 localStorage**。F5 / 重启服务 → job_id 丢失 → `render_status` 报 "job 不存在或已过期" |
| 3 | 执行日志没记录 / 不显示 | 三层 BUG：(a) 后端 `record_start` 已写 `kind=fine_export` 到 `execution_history.json`（[app.py:5826](slirn_home/app.py#L5826)），**写盘 OK**；(b) 前端 `LOG_KIND_LABELS`（[router.js:935-942](slirn_home/static/router.js#L935-L942)）缺 6 个 kind → 显示原始 key 用户认不出；(c) `loadLogs()` 调旧端点 `/execution_history_query`（[router.js:921](slirn_home/static/router.js#L921)），**没用 REQ-081 的新端点** `/slirn/api/list_logs`（[app.py:6447](slirn_home/app.py#L6447)）→ `auto` 过滤 / 时间段过滤能力空跑 |

## 目标

1. 修 elapsed_sec 一直 0（ffmpeg init 阶段也要显示真实时间）
2. 页面刷新后能回显 in-flight 导出进度（job_id 落盘 + 端点兜底）
3. 执行日志面板能正确显示「最终导出视频」记录（含时间 / 阶段 / 模式过滤）

## 影响范围

| 文件 | 改动 | 原因 |
|---|---|---|
| [slirn_home/app.py](slirn_home/app.py) | `render_status` 实时算 elapsed；新增 3 个 helper；`export_fine_video` 落盘；4 个 exit 清理；新增 `active_export_for_task` 端点；`_cleanup_stale_jobs` 同步删文件 | 进度实时 + job_id 持久化 |
| [slirn_home/static/router.js](slirn_home/static/router.js) | `LOG_KIND_LABELS` 补 6 个；`loadLogs` 改调 `/list_logs`；新增 time/auto 过滤状态 | 日志显示 |
| [slirn_home/static/pipeline.js](slirn_home/static/pipeline.js) | `loadPanel` 末尾挂 `active_export_for_task` → `startFineExportInline` | 页面回显 |
| [slirn_home/static/home.css](slirn_home/static/home.css) | 时间段 chip / auto chip 样式 | UI |
| [tests/test_workbench.py](tests/test_workbench.py) | +5 测试 | 回归保护 |
| [docs/REQM/](docs/REQM/) | 新建 | 5 阶段 SOP 第 1 阶段 |
| [docs/design/](docs/design/) | 新建 | 第 2 阶段 |
| [docs/verification/](docs/verification/) | 新建 | 第 5 阶段 |

## 验收标准（12 条）

- **AC-1**：`/slirn/api/render_status` 返回的 `elapsed_sec` 与 `time.monotonic() - job.started_at` 一致（即使 ffmpeg 还没输出 `out_time_ms=`）
- **AC-2**：ffmpeg init 阶段（0% 进度，前 5 秒）调 `render_status` 返回 `elapsed_sec > 0`
- **AC-3**：调 `export_fine_video` 后，`tasks/<tid>/outputs/.export_job.json` 文件存在，内容含 `job_id` + `state` + `started_at`
- **AC-4**：job 进入终态（done / failed / cancelled）后，`.export_job.json` 被清理（5 分钟后或前端最后一次轮询拿到结果后）
- **AC-5**：`/slirn/api/active_export_for_task?task_id=X` 返回 `{ok:true, job:{job_id, state, source:"registry"}}`（in-memory 命中时）
- **AC-6**：服务重启后，`.export_job.json` 仍在 → `/slirn/api/active_export_for_task` 返回 `{ok:true, job:{source:"disk", warning:"内存无此 job（服务可能已重启）；ffmpeg 状态未知"}}`
- **AC-7**：页面刷新（F5）后，前端调 `active_export_for_task` → 自动重新挂 `startFineExportInline` → 进度条继续更新
- **AC-8**：前端 `LOG_KIND_LABELS` 含 10 个 kind（subtitle_generation / subtitle_review / rough_cut / rough_cut_link_person / rough_compose / rough_compose_delete / optimize / fine_ai_layout / fine_bg_detect / fine_preview / fine_export）
- **AC-9**：`loadLogs()` 调 `/slirn/api/list_logs`（不是 `/execution_history_query`）
- **AC-10**：UI 时间段 chip（今天 / 近 7 天 / 近 30 天 / 全部）+ 模式 chip（全部 / 手动 / 自动）切换 → 列表刷新
- **AC-11**：手动点「导出最终视频」→ 完成后 → 「📜 执行日志」面板有 1 条 `kind=最终导出视频, status=success` 记录
- **AC-12**：所有现有测试 + 新增 5 个测试全过（206 → 211）

## 不做的事

- ❌ 不改 `KIND_*` 常量命名（沿用 REQ-081 的 10 个）
- ❌ 不删 `execution_history_query` 旧端点（pipeline.js 等可能在用）
- ❌ 不做服务端推送（SSE / WebSocket）；前端轮询即可
- ❌ 不持久化 `_JOB_REGISTRY` 全部（仅 task 级 active job 文件）
- ❌ 不解决 pipeline auto 链路里 `fine_export` 不被自动调（STAGE_ORDER 也不含；不在本 REQ scope）
- ❌ 不改 `_run_fine_render_async` 主循环结构（最小侵入）

## 命名约定

| 项 | 值 |
|---|---|
| REQ 文档 | `REQ-20260920-084-export-progress-time-restore-logs.md` |
| DESIGN 文档 | `DESIGN-20260920-084-export-progress-time-restore-logs.md` |
| VERIFICATION 文档 | `VERIFICATION-20260920-084-export-progress-time-restore-logs.md` |
| 持久化文件 | `tasks/<tid>/outputs/.export_job.json`（点开头 + 隐藏） |
| 新端点 | `GET /slirn/api/active_export_for_task?task_id=X` |
| 时间显示 | 沿用 `HH:MM:SS` 格式（`_fmtSec`） |

## Why

REQ-081 留了基础设施（`/slirn/api/list_logs` + `record_start` 写 `kind=fine_export`）但没接通前端 → 用户感知不到已记录的日志；`_JOB_REGISTRY` 不持久化 + `elapsed_sec` 计算时机错 → 进度假死。这是用户最敏感的两个 UI 反馈（进度 + 日志），必须打通。

## How to apply

未来类似的「进程内状态 + 前端轮询」模式（如字幕生成进度 / 修订进度）：
- **实时计算字段**（elapsed / eta）放 GET handler 里算，不在写线程里缓存
- **跨刷新的状态**必须落盘 + endpoint 兜底
- **后端 KIND_* 加完时，前端 LOG_KIND_LABELS 同步加**（最好用单一来源 / 后端暴露 `/list_kinds` 端点）

## 上下文关联

[funclip-sop] [REQ-20260919-074-fine-export-async-progress] [REQ-20260920-077-fine-export-inline-progress]
[REQ-20260920-081-execution-log] [REQ-20260918-053-execution-history]
[REQ-20260920-082-move-bgm-selector] [REQ-20260920-083-bgm-path-resolver-mismatch]