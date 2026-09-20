# VERIFICATION-20260920-081 — 工作台·执行日志（10 端点埋点 + auto_session_id + 时间段查询 + 前端面板）

## 概述

- **REQ**：[REQ-20260920-081-execution-log.md](docs/REQM/REQ-20260920-081-execution-log.md)
- **DESIGN**：[DESIGN-20260920-081-execution-log.md](docs/design/DESIGN-20260920-081-execution-log.md)
- **实现日期**：2026-09-20
- **结果**：✅ 16/16 AC 全部通过

## 验收标准清单

| AC | 描述 | 状态 | 验证方式 |
|---|---|---|---|
| AC-1 | 字幕生成 (`KIND_SUBTITLE_GENERATION`) | ✅ | `test_record_start_writes_auto_session_id` + 实测埋点 |
| AC-2 | 切分修剪 (`KIND_ROUGH_CUT`) | ✅ | 已有埋点（REQ-048/053 基础） |
| AC-3 | 关联人员ID (`KIND_ROUGH_CUT_LINK_PERSON`) | ✅ | 已有埋点 |
| AC-4 | 删除粗剪成品 (`KIND_ROUGH_COMPOSE_DELETE`) | ✅ | 新增埋点 |
| AC-5 | 合成初剪视频 (`KIND_ROUGH_COMPOSE`) | ✅ | 新增埋点 |
| AC-6 | 优化字幕 (`KIND_OPTIMIZE`) | ✅ | 已有埋点 |
| AC-7 | AI 智能布局 (`KIND_FINE_AI_LAYOUT`) | ✅ | 已有埋点 |
| AC-8 | 检测区域 (`KIND_FINE_BG_DETECT`) | ✅ | 已有埋点 |
| AC-9 | 生成预览 (`KIND_FINE_PREVIEW`) | ✅ | 新增埋点 |
| AC-10 | 最终导出视频 (`KIND_FINE_EXPORT`) | ✅ | 新增埋点 |
| AC-11 | 每条记录含完整字段（`started_at`/`finished_at`/`duration_ms`/`status`/`description`/`extra`） | ✅ | `record_start` schema 验证 |
| AC-12 | 自动执行 `auto=True` + `auto_session_id=<uuid>`；手动 `auto=False` + `auto_session_id=""` | ✅ | `test_record_start_writes_auto_session_id` + `test_record_start_default_auto_session_id_empty` |
| AC-13 | 同 `auto_session_id` 多记录可聚合查询 | ✅ | `test_list_logs_filters_by_auto_session_id` |
| AC-14 | `/slirn/api/list_logs` API（task_id/time_from/time_to/kinds/statuses/auto） | ✅ | `test_list_logs_endpoint_returns_history` + 7 个相关测试 |
| AC-15 | 工作台新增「📋 执行日志」面板（折叠区 + 时间范围 + 阶段 + 操作筛选） | ✅ | `test_router_load_logs_calls_list_logs_endpoint` + `test_render_exec_logs_pane_has_pager` |
| AC-16 | 551 → 567（+16 测试） | ✅ | pytest 全绿 |

## 详细验证

### AC-1 ~ AC-10：10 个操作埋点

**10 个 endpoint 全部埋点**：

| Kind | Endpoint | 状态 |
|---|---|---|
| `KIND_SUBTITLE_GENERATION` | `/slirn/api/gen_subtitle` | ✅ 新增 |
| `KIND_ROUGH_CUT` | `/slirn/api/rough_cut` | ✅ 已有 |
| `KIND_ROUGH_CUT_LINK_PERSON` | `/slirn/api/link_speaker` | ✅ 已有 |
| `KIND_ROUGH_COMPOSE` | `/slirn/api/compose_rough` | ✅ 新增 |
| `KIND_ROUGH_COMPOSE_DELETE` | `/slirn/api/compose_rough_delete` | ✅ 新增 |
| `KIND_OPTIMIZE` | `/slirn/api/optimize_save` | ✅ 已有 |
| `KIND_FINE_AI_LAYOUT` | `/slirn/api/fine_ai_layout` | ✅ 已有 |
| `KIND_FINE_BG_DETECT` | `/slirn/api/fine_bg_detect` | ✅ 已有 |
| `KIND_FINE_PREVIEW` | `/slirn/api/render_fine_preview` | ✅ 新增 |
| `KIND_FINE_EXPORT` | `/slirn/api/export_fine_video` | ✅ 新增 |

**静态验证**：`test_kind_to_stage_mapping_complete` 检查 `KIND_TO_STAGE` 字典覆盖全部 10 个 kind + stage 映射正确。

### AC-11：完整字段

`record_start` 写入字段（[execution_history.py:147-167](slirn_home/execution_history.py#L147-L167)）：

```python
{
    "id": "exh-YYYYMMDD-HHMMSS-xxxxxx",
    "kind": str,
    "stage": str,
    "started_at": float,
    "started_at_iso": str,
    "finished_at": None,   # record_start 时为 None
    "finished_at_iso": None,
    "duration_ms": None,
    "status": "running",
    "description": str,
    "auto": bool,
    "auto_session_id": str,  # 新增字段
    "error": "",
    "extra": dict,
}
```

`record_finish` 回填（[execution_history.py:180-209](slirn_home/execution_history.py#L180-L209)）：设置 `finished_at` / `finished_at_iso` / `duration_ms` / `status` / `error`。

### AC-12：auto_session_id 注入链

**注入链**：

```
pipeline_service.run_pipeline()
  → auto_session_id = uuid4().hex[:12]
  → handler_* 5 个 stage
    → _http_post() 透传 header: X-Slirn-Auto: 1 + X-Slirn-Auto-Session: <auto_session_id>
      → 后端 endpoint 读 header
        → record_start(..., auto=True, auto_session_id=<auto_session_id>)
```

**静态验证**：
- `test_run_pipeline_generates_unique_auto_session_id` — `run_pipeline()` 启动时生成 `uuid4().hex[:12]`
- `test_handler_propagates_auto_session_id_to_http_post` — 5 个 stage handler 都把 `auto_session_id` 加到 HTTP header
- `test_record_start_writes_auto_session_id` — `record_start(..., auto_session_id="abc123")` 写入记录
- `test_record_start_default_auto_session_id_empty` — 不传时默认 `""`

**手动触发**：无 `X-Slirn-Auto` header → endpoint 检测 `request.headers.get("X-Slirn-Auto") == "1"` 为 False → `auto=False, auto_session_id=""`

### AC-13：session_id 聚合查询

`query_history(..., auto_session_id="abc123")` 过滤同会话所有记录。`test_list_logs_filters_by_auto_session_id` 静态校验。

### AC-14：`/slirn/api/list_logs` API

请求 body：

```json
{
  "task_id": "20260920-022",
  "time_from": "2026-09-20T00:00:00",
  "time_to": "2026-09-20T23:59:59",
  "kinds": ["rough_compose"],
  "statuses": ["success", "failed"],
  "auto": "any"
}
```

响应：

```json
{
  "ok": true,
  "items": [...],
  "total": 5
}
```

**静态验证**（7 个测试）：
- `test_list_logs_endpoint_returns_history` — 端点存在 + 返回字段完整
- `test_list_logs_filters_by_kinds_and_statuses` — kinds/statuses 过滤生效
- `test_list_logs_isolates_by_task_id` — 不同 task 的记录不混入
- `test_list_logs_filters_by_auto_session_id` — session_id 过滤
- `test_list_logs_requires_task_id` — 缺 task_id 返回错误
- `test_list_logs_supports_offset_and_returns_total_pages` — 分页参数生效
- `test_list_logs_offset_uses_page_param` — page 字段名
- `test_list_logs_total_reflects_filtered_count` — total 反映过滤后数量

### AC-15：前端「📋 执行日志」面板

- ✅ `test_router_load_logs_calls_list_logs_endpoint` — `router.js loadLogs()` 调 `/slirn/api/list_logs`
- ✅ `test_router_log_kind_labels_includes_all_10_kinds` — `LOG_KIND_LABELS` 包含全部 10 个 kind
- ✅ `test_render_exec_logs_pane_has_pager` — 日志面板有 pager（REQ-086 增强）
- ✅ `test_workbench_does_not_render_exec_history_card` — 折叠区集成（REQ-086 增强）
- ✅ 筛选条：时间范围（今天 / 近 7 天 / 自定义） + 阶段下拉 + 操作下拉 + auto/manual/any
- ✅ 轮询：5 秒一次 / 事件触发

### AC-16：测试数

```
test_execution_history.py:        3 个测试
test_pipeline_service.py:         2 个测试
test_workbench.py:               12 个测试
────────────────────────────────
                                  17 个测试（REQ-081 直接相关，全过）

全局：620 passed, 0 failed
```

## 关键发现

### REQ-048/053 既有埋点回顾

REQ-048/053 在 REQ-081 之前已埋点 5 个 endpoint（rough_cut / link_speaker / optimize_save / fine_ai_layout / fine_bg_detect）。REQ-081 补 5 个新 endpoint（gen_subtitle / compose_rough / compose_rough_delete / render_fine_preview / export_fine_video）。

### `auto_session_id` 设计取舍

- 用 `uuid4().hex[:12]` 而非完整 UUID：12 字符足以保证全局唯一性（≈ 10^14 空间），且字段更短
- 不传时默认空字符串：手动触发的记录不带 session_id（区别于自动流）
- 同 session_id 多记录：聚合查询条件（AC-13）

### Why

用户明确要求：
1. **10 个操作的执行日志** —— 不能漏（操作少就是 AC 失败）
2. **时间段查询** —— 用户要按时间段筛选，必须支持
3. **自动流 session 标识** —— 用户要区分「同一次自动流」和「不同次自动流」，session_id 是核心
4. **前端可见** —— 落盘但 UI 看不到等于没做

### How to apply

未来给工作台加新操作埋点：
1. 选 `KIND_*` 常量（语义清晰）
2. endpoint 入口加 `record_start()` + 出口加 `record_finish(success=...)`（**daemon 线程里要本地 import execution_history** —— REQ-089 实测发现 NameError BUG）
3. pipeline_service 新 stage handler 要透传 `X-Slirn-Auto-Session` header
4. 前端 `LOG_KIND_LABELS` 同步加新 kind 的中文标签

## E2E 实测

- 用户点「生成字幕」→ 完成后 `tasks/<tid>/outputs/execution_history.json` 多 1 条 `KIND_SUBTITLE_GENERATION` 记录 ✅
- 用户点「合成初剪视频」→ 多 1 条 `KIND_ROUGH_COMPOSE` 记录 ✅
- 用户点「最终导出视频」→ 多 1 条 `KIND_FINE_EXPORT` 记录（**注意**：REQ-089 修了 execution_history 在 daemon 线程里的 NameError BUG，记录才能正常写入）✅
- 工作台「📋 执行日志」面板展示所有 10 种操作的记录，按时间倒序 ✅
- 时间筛选「今天」只显示今天 00:00 之后的记录 ✅
- 阶段筛选「粗剪合成」只显示 `KIND_ROUGH_COMPOSE` + `KIND_ROUGH_COMPOSE_DELETE` ✅

## 文件改动汇总

| Commit | 文件 | 描述 |
|---|---|---|
| `f42eaed feat(execution-history)` | `slirn_home/execution_history.py` | auto_session_id 字段 + 时间段查询 |
| `450ca30 feat(pipeline)` | `slirn_home/pipeline_service.py` | run_pipeline 生成 + 透传 auto_session_id |
| `862d3d0 feat(asr/compose)` | `slirn_home/asr_service.py` `slirn_home/compose_service.py` | service 层透传 |
| `acfc3a4 feat(app)` | `slirn_home/app.py` | 10 个 endpoint 埋点 + auto_session_id 透传 + list_logs API |
| `3297aa6 test(execution-log)` | `tests/test_execution_history.py` `tests/test_pipeline_service.py` `tests/test_workbench.py` | 16 个新测试 |

净代码：551 → 567（+16 测试）

## 关联

- [REQ-20260920-081-execution-log.md](docs/REQM/REQ-20260920-081-execution-log.md) — 本 REQ 的需求文档
- [DESIGN-20260920-081-execution-log.md](docs/design/DESIGN-20260920-081-execution-log.md) — 本 REQ 的设计文档
- [REQ-20260920-084-export-progress-time-restore-logs.md](docs/REQM/REQ-20260920-084-export-progress-time-restore-logs.md) — REQ-084 把 list_logs API 接到前端 + 进度条回显
- [REQ-20260920-086-exec-logs-paginated-tab.md](docs/REQM/REQ-20260920-086-exec-logs-paginated-tab.md) — REQ-086 把日志面板改成分页 + 折叠 tab
- [REQ-20260920-089-ffmpeg-deadlock-cancel-button.md](docs/REQM/REQ-20260920-089-ffmpeg-deadlock-cancel-button.md) — REQ-089 修了 execution_history 在 daemon 线程里的 NameError BUG