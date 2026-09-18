# REQ-20260918-048 — 执行历史记录（生成字幕 / 生成粗剪视频）

## 1. 背景与目标

用户在两个阶段开始操作时希望追踪历史：

- **生成字幕**（`POST /slirn/api/gen_subtitle`）
- **生成粗剪视频**（`POST /slirn/api/compose_rough`）

现状：

- `_JOBS` 字典里已有 `started_at` / `finished_at` 字段，状态机内可读（`job_status` 返回）
- 但**仅内存有效**：服务重启后丢失；用户切走页面后无途径回看历史；多次执行同接口的对比也只能靠 toast

目标：每次执行都落盘一条记录（不论成功或失败），用户可在工作台面板查看执行历史（次数、最近若干次的起止时间、时长、状态、错误信息）。

## 2. 设计要点

### 2.1 落盘位置

- 单文件：`tasks/<tid>/outputs/execution_history.json`
- 不污染上游 tasklib schema（tasklib 不感知 history 文件）
- 每条记录字段：

```json
{
  "id": "exh-20260918-001",
  "kind": "subtitle_generation" | "rough_compose",
  "started_at": 1758154231.234,
  "started_at_iso": "2026-09-18T12:30:41+08:00",
  "finished_at": 1758154270.789,
  "finished_at_iso": "2026-09-18T12:31:20+08:00",
  "duration_ms": 39555,
  "status": "success" | "failed" | "running",
  "error": "" | "no audio track",
  "extra": {"sd": true, "segments": 14, "video_ms": 120000}
}
```

### 2.2 写入时机

在 `asr_service.start_job` / `compose_service.start_compose` 内**完成分支**写盘：

- 成功：`status="success"`，`finished_at` 取 `time.time()`
- 失败（异常分支）：`status="failed"`，`error=str(e)`
- 启动即 running：插入 `status="running"`，让用户立刻看到「正在执行」

实现：抽公共 `_record_exec(tid, kind, started_at, **extra)` 写入 `execution_history.json`（列表追加、单写锁防并发），落到 `outputs/execution_history.json`。

### 2.3 展示

复用工作台面板顶部已有「阶段说明 / 统计」折叠壳（REQ-044 `.slirn-col`），新增一个折叠区：

- 标题：「📜 执行历史」
- 内容：倒序列出最近 10 条；超过 10 条提示「共 N 次，最近 10 次」
- 每行：
  - `字幕生成 #3 · 2026-09-18 12:30:41 → 12:31:20 · 39.5s · ✅ 成功`（红 ❌ 失败、灰 ⏳ 运行中）
  - 失败行附错误信息（折叠展开）
- 默认折叠（首次进工作台不打扰用户）

### 2.4 服务端接口

- `POST /slirn/api/execution_history` → `{task_id}` → `{ok, items}`（数组按 started_at 倒序）

实现：服务端渲染时把 `items[:10]` 注入 `slirn-wb-pane` 的折叠壳 HTML，JS 只负责折叠 + 不做轮询（history 已是落盘数据）。每次 `gen_subtitle` / `compose_rough` 完成后下一次进入工作台自然刷新。

### 2.5 不做的事

- 不做实时刷新（无 WebSocket；用户主动离开/刷新即可）
- 不清理旧记录（任务删除时整体删除 outputs/ 即可）
- 不做按时间筛选 / 导出 CSV（用户提的是「查看执行了多少次」够用即可）

## 3. 验收标准

| # | 标准 |
|---|---|
| 1 | 字幕生成成功 → history 多一条 success 记录（started_at / finished_at / duration_ms 正确） |
| 2 | 字幕生成失败 → history 多一条 failed 记录（error 含原始错误信息） |
| 3 | 粗剪合成成功 → history 多一条 success 记录 |
| 4 | 粗剪合成失败 → history 多一条 failed 记录 |
| 5 | 工作台面板渲染时显示「执行历史」折叠区 + 倒序最近 10 条 |
| 6 | 服务重启后再查询 → 历史仍存在（落盘而非内存） |
| 7 | 并发执行两个任务不冲突（单写锁 + 追加） |
| 8 | history 单文件 ≤ 1MB（远低于现实预期） |

## 4. 涉及文件

- `slirn_home/execution_history.py`（新文件）：`record_start/record_finish/load_history` + 锁
- `slirn_home/asr_service.py`：在 `_run()` 异常/完成处调 `record_finish`
- `slirn_home/compose_service.py`：同上
- `slirn_home/app.py`：
  - `gen_subtitle` / `compose_rough` 启动时调 `record_start`（status=running）
  - 新接口 `POST /slirn/api/execution_history`
  - `_render_workbench` 注入执行历史折叠区 HTML
- `slirn_home/static/router.js`：折叠壳复用 REQ-044 `.slirn-col`（自动 wrap，无需新逻辑）
- `slirn_home/static/home.css`：复用 REQ-044 样式
- `tests/test_execution_history.py`（新文件）：5-8 个单元用例（启动/完成/失败/加载/落盘往返）
- `docs/REQM/REQ-20260918-048-execution-history.md`（本文件）
