# DESIGN-20260920-081 工作台·执行日志

## Context

[REQ-20260920-081](../REQM/REQ-20260920-081-execution-log.md) 描述：用户希望工作台能记录 10 个核心操作的执行日志，支持按 task_id 隔离、按时间段 + 阶段查询，区分手动 / 自动执行。

**现状**（已有基础设施）：
- `execution_history.py` 已存在（REQ-20260918-048/053 + REQ-20260919-075）
- `KIND_*` 11 个常量、KIND_LABELS 中文标签、KIND_TO_STAGE 阶段映射都已定义
- `record_start` / `record_finish` / `query_history` / `load_history` / `patch_extra` API 已实现
- 5 个 endpoint 已埋点（detect_bg_white_area / parse_reference_layout / build_cutlist / cut_speaker_link / save_optimize_subtitle）
- `X-Slirn-Auto: 1` header 透传已实现
- `auto=True` 字段已记录

**缺口**：
- 5 个 endpoint 未埋点（gen_subtitle / compose_rough / compose_rough_delete / render_fine_preview / export_fine_video）
- `auto_session_id` 字段不存在
- pipeline_service 不生成 / 透传 session_id
- 无时间段查询 API
- 无前端日志面板

## 关键决策

| 维度 | 决策 | 理由 |
|---|---|---|
| 存储格式 | 复用现有 `execution_history.json` + JSON Lines 追加 | 不破坏现有数据；零迁移成本 |
| `auto_session_id` 生成 | `uuid4().hex[:12]`（12 字符 / 48 bit，足够唯一） | 与现有 `exh-<时间戳>-<6hex>` 风格一致 |
| `auto_session_id` 注入链 | pipeline_service.run_pipeline() 生成 → handler 透传 → HTTP header `X-Slirn-Auto-Session` → endpoint 读 header → record_start() | 与现有 `X-Slirn-Auto` 透传链完全对称；0 改动 5 个 stage handler 的核心逻辑 |
| 手动端点 | 无 X-Slirn-Auto header → `auto=False, auto_session_id=""` | 手动不参与 session 聚合；空字符串区分 |
| 时间段过滤 | query_history 新增 `time_from_ts` / `time_to_ts` 参数（epoch 秒） | ISO 8601 在 API 层解析，模块层只接 float；避免 datetime tz 处理复杂 |
| task_id 隔离 | 通过 outputs_dir 自然隔离（每个 task 目录独立 history 文件） | 现有架构已是如此 |
| 新 API | 新增 `POST /slirn/api/list_logs`，复用 `query_history` | 不破坏现有 `query_history` 调用者 |
| 前端面板 | 挂在 workbench 区域底部新增折叠区 `<details><summary>📋 执行日志</summary>` | 不影响其他面板；可折叠；与现有 design 一致 |
| 轮询策略 | 5 秒一次轮询 `list_logs` | 不引入 WebSocket；简单可维护 |
| `extra` 字段 | 各操作按需记录：gen_subtitle 记 model/segments；compose_rough 记 output_size；render_fine_preview 记 clip 时长 + 预缩次数；export_fine_video 记 output_path + resolution | 用户原话「具体执行情况，你看着相关的操作需要记录什么，你就记什么」 |
| 错误处理 | record_start/finish 内部 try/except → log.warning + 返回空 id（不阻塞主流程） | 现有实现已如此；保持一致 |
| 文件上限 | 现有 `_HARD_LIMIT = 5000` → 同次任务够用；不动 | 防止单文件 OOM |
| 权限白名单 | `execution_history` 模块已经在 SETTINGS 白名单 | 不需新权限 |

## 关键文件改动

| 文件 | 改动 | 估算行数 |
|---|---|---|
| [slirn_home/execution_history.py](slirn_home/execution_history.py) | `record_start` 加 `auto_session_id` 参数；条目加 `auto_session_id` 字段；`query_history` 加 `time_from_ts` / `time_to_ts` 参数；`format_duration` 不变 | +30 |
| [slirn_home/app.py](slirn_home/app.py) | 5 个 endpoint 入口埋 record_start + 出口埋 record_finish（[6078, 6597, 6674, 5606, 5668](slirn_home/app.py#L5606)）；新增 `/slirn/api/list_logs`；3 个 endpoint 已有 `record_start` 的位置 + 新增 `auto_session_id` 透传；其他 2 个（detect / parse）也加 session 透传 | +200 |
| [slirn_home/pipeline_service.py](slirn_home/pipeline_service.py) | `run_pipeline()` 启动时生成 `auto_session_id`；5 个 handler 透传给 `_http_post` 的 headers；`_http_post` 加 `X-Slirn-Auto-Session` | +50 |
| [slirn_home/static/router.js](slirn_home/static/router.js) | 新增 `initExecutionLogPanel()` + `renderLogList()` + `pollLogs()`；展开折叠区时启动轮询 | +150 |
| [slirn_home/static/home.css](slirn_home/static/home.css) | `.slirn-log-panel` / `.slirn-log-row` / `.slirn-log-filters` 样式 | +60 |
| [tests/test_workbench.py](tests/test_workbench.py) | 6 个新测试 | +200 |
| [docs/REQM/](docs/REQM/) | REQ 文档 | +180 |
| [docs/design/](docs/design/) | DESIGN 文档 | +200 |
| [docs/verification/](docs/verification/) | VERIFICATION 文档 | +100 |

净代码量约 **+450 行**（核心 + test + UI + 文档）。

## 数据流

### 自动执行（流程配置启动 pipeline）

```
pipeline_service.run_pipeline(task_id, config)
  ↓
生成 auto_session_id = uuid4().hex[:12]
  ↓
handler_subtitle_generation(handler_args)
  ↓
_http_post(url, body, headers={"X-Slirn-Auto": "1", "X-Slirn-Auto-Session": auto_session_id})
  ↓
后端 endpoint: req.headers.get("X-Slirn-Auto-Session") == auto_session_id
  ↓
record_start(outputs_dir, kind, auto=True, auto_session_id=auto_session_id)
  ↓
→ execution_history.json: {..., "auto": true, "auto_session_id": "abc123def456"}
```

### 手动执行（用户在 UI 点按钮）

```
用户点「生成字幕」→ 前端 router.js fetch("/slirn/api/gen_subtitle", {body})
  ↓ 无 X-Slirn-Auto header
后端 endpoint: req.headers.get("X-Slirn-Auto") → None
  ↓
record_start(outputs_dir, kind, auto=False, auto_session_id="")
  ↓
→ execution_history.json: {..., "auto": false, "auto_session_id": ""}
```

### 查询流程

```
用户在工作台展开「📋 执行日志」折叠区
  ↓
router.js 每 5 秒调一次 fetch("/slirn/api/list_logs", {body: {task_id}})
  ↓
后端:
  tasklib.TaskManager → task_dir / outputs / execution_history.json
  ↓
query_history(outputs_dir, kinds=?, statuses=?, time_from_ts=?, time_to_ts=?, auto=?)
  ↓
排序（倒序）+ 过滤
  ↓
返回 JSON {ok: true, items: [...], total: N}
  ↓
前端 renderLogList() 渲染表格
```

## 边界与错误处理

| 场景 | 行为 |
|---|---|
| record_start 抛异常（落盘失败） | log.warning + 返回空 id；endpoint 继续执行 |
| X-Slirn-Auto-Session header 缺失 | auto_session_id=""（区分手动） |
| pipeline_service 中途崩溃 | 已有记录 status 永远 running → 加一个查询 API 的「卡死检测」逻辑（启动时间 > 1 小时 → 显示「可能卡死」） |
| list_logs 传入未知 task_id | 返回 `{ok: false, error: "task not found"}` 404 |
| time_from > time_to | 返回空列表（不抛异常） |
| 同一 task 多用户同时操作 | execution_history.py 已有模块级 `_WRITE_LOCK`，并发安全 |
| 前端轮询时 endpoint 报错 | 显示错误提示，不影响其他功能 |

## 复用现有基础设施

- `execution_history.py` 全部 API：`record_start` / `record_finish` / `patch_extra` / `load_history` / `query_history` / `format_duration`
- `KIND_*` 11 个常量（已有全部 10 个用户列出 + 1 个 SUBTITLE_REVIEW）
- `KIND_LABELS` 中文标签
- `KIND_TO_STAGE` 阶段映射
- `DEFAULT_DESCRIPTIONS` 默认描述
- `X-Slirn-Auto: 1` header 检测模式
- `tasklib.TaskManager` 任务目录解析
- 前端 `toast()` / `fetch()` 工具函数

## 不做的事

- ❌ 不重构 execution_history.py 现有 API 签名
- ❌ 不改 KIND_* 常量命名
- ❌ 不改现有 5 个 endpoint 的代码（只追加 auto_session_id 透传）
- ❌ 不做实时推送（WebSocket）—— 5 秒轮询足够
- ❌ 不做日志导出 CSV —— 不在用户列出的需求里
- ❌ 不持久化到云 —— 本地 JSON 即可
- ❌ 不加操作热力图 / 统计图表 —— 不在用户列出的需求里

## 命名约定

| 项 | 命名 |
|---|---|
| REQ 文档 | `REQ-20260920-081-execution-log.md` |
| DESIGN 文档 | `DESIGN-20260920-081-execution-log.md` |
| VERIFICATION 文档 | `VERIFICATION-20260920-081-execution-log.md` |
| HTTP header | `X-Slirn-Auto-Session` |
| 字段 | `auto_session_id` |
| API 端点 | `/slirn/api/list_logs` |
| 前端函数 | `initExecutionLogPanel()` / `renderLogList()` / `pollLogs()` |
| CSS class | `.slirn-log-panel` / `.slirn-log-row` / `.slirn-log-filters` |