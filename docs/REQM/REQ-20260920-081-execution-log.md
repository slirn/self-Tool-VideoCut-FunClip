# REQ-20260920-081 工作台·执行日志 — 补齐 10 个操作埋点 + auto_session_id + 时间段查询 + 前端面板

## 背景

工作台目前已有 REQ-20260918-048 / 053 实现的执行历史（`execution_history.py`）：落盘 `tasks/<tid>/outputs/execution_history.json`，含 `id/kind/stage/started_at/finished_at/duration_ms/status/description/auto/error/extra`。

但用户实测发现：
1. **10 个列出的操作只埋了 5 个** —— 还有 5 个未埋点：
   - ❌ 字幕生成阶段的生成字幕 (`KIND_SUBTITLE_GENERATION` → `/slirn/api/gen_subtitle`)
   - ❌ 粗剪合成阶段的合成初剪视频 (`KIND_ROUGH_COMPOSE` → `/slirn/api/compose_rough`)
   - ❌ 粗剪合成阶段的删除粗剪成品 (`KIND_ROUGH_COMPOSE_DELETE` → `/slirn/api/compose_rough_delete`)
   - ❌ 精简视频阶段的生成预览 (`KIND_FINE_PREVIEW` → `/slirn/api/render_fine_preview`)
   - ❌ 精简视频阶段的最终导出视频 (`KIND_FINE_EXPORT` → `/slirn/api/export_fine_video`)
2. **缺 `auto_session_id`** —— 用户明确要求自动执行的日志要带会话 ID，但目前只有 `auto=True` 布尔
3. **缺时间段查询** —— 用户要求「按时间段和剪辑的处理阶段查询」，当前 `query_history` 只支持 kinds + statuses + keyword
4. **缺前端日志面板** —— 历史记录已落盘但没在工作台展示

## 用户影响

| 场景 | 表现 |
|---|---|
| 用户点「生成字幕」 | 进度显示但不写入历史 → 工作台看不到 |
| 用户点「删除粗剪成品」 | 同上 |
| 用户点「合成初剪视频」 | 同上 |
| 用户点「生成预览」 | 同上 |
| 用户点「导出最终视频」 | 同上 |
| 流程配置启动自动管线 | 5 个 stage handler 的记录无法区分「同一次自动流」和「不同次自动流」 |
| 用户想看某段时间的执行日志 | 必须翻整个 history 文件，无时间段筛选 |
| 用户切到工作台面板 | 看不到任何历史（虽然落盘了） |

## 用户明确要求（直接引用）

> 执行日志功能需要对主要的操作添加执行日志信息，日志信息主要是正在执行的是什么阶段的什么操作，执行的开始时间和截止时间以及时长，还有具体执行情况，这个具体执行情况，你看着相关的操作需要记录什么，你就记什么。然后，并不是所有的操作都需要记录，下面我列出来需要记录执行日志的操作：
> - 字幕生成阶段的生成字幕功能；
> - 切分修剪阶段的执行。切分修剪和关联人员ID；
> - 粗剪合成阶段的删除粗剪成品和合成初剪视频；
> - 优化字幕阶段的确认保存；
> - 精简视频阶段的AI智能布局、检测区域、生成预览、最终导出视频这些功能。
> 这些执行日志是针对一个任务的相关操作。在查看执行日志时，只看当前任务的，不要把其他任务的日志混在一起。另外还有流程配置中自动执行的时候，每一阶段相关的操作也都要记日志。对于自动执行的日志，要有自动执行的标识信息，以区分手工操作的日志。按照这个要求添加执行日志。

## 验收标准

| # | 描述 |
|---|---|
| AC-1 | 字幕生成阶段的生成字幕功能 → 操作历史有 1 条 `kind=KIND_SUBTITLE_GENERATION` 记录 |
| AC-2 | 切分修剪阶段的「执行切分修剪」→ 1 条 `kind=KIND_ROUGH_CUT` |
| AC-3 | 切分修剪阶段的「关联人员ID」→ 1 条 `kind=KIND_ROUGH_CUT_LINK_PERSON` |
| AC-4 | 粗剪合成阶段的「删除粗剪成品」→ 1 条 `kind=KIND_ROUGH_COMPOSE_DELETE` |
| AC-5 | 粗剪合成阶段的「合成初剪视频」→ 1 条 `kind=KIND_ROUGH_COMPOSE` |
| AC-6 | 优化字幕阶段的「确认保存」→ 1 条 `kind=KIND_OPTIMIZE` |
| AC-7 | 精简视频阶段的「AI智能布局」→ 1 条 `kind=KIND_FINE_AI_LAYOUT` |
| AC-8 | 精简视频阶段的「检测区域」→ 1 条 `kind=KIND_FINE_BG_DETECT` |
| AC-9 | 精简视频阶段的「生成预览」→ 1 条 `kind=KIND_FINE_PREVIEW` |
| AC-10 | 精简视频阶段的「最终导出视频」→ 1 条 `kind=KIND_FINE_EXPORT` |
| AC-11 | 每条记录含 `started_at` / `finished_at` / `duration_ms` / `status` / `description` / `extra` 字段 |
| AC-12 | 自动执行时（`X-Slirn-Auto: 1` header）记录带 `auto=True` + `auto_session_id=<uuid>`；手动触发 `auto=False` + `auto_session_id=""` |
| AC-13 | 同一 `auto_session_id` 下的所有记录可通过 session_id 聚合查询 |
| AC-14 | 新增 `/slirn/api/list_logs` 查询 API：`body={task_id, time_from?, time_to?, kinds?, statuses?, auto?}` → 返回当前任务的过滤后日志列表 |
| AC-15 | 工作台面板新增「📋 执行日志」折叠区，展示当前任务的日志列表，支持时间范围 + 阶段 + 操作筛选 |
| AC-16 | 所有现有测试 + 新增 ≥6 个测试全过（551 → 557+） |

## 范围

### 在范围内（必须做）

1. **补 5 个端点的埋点**：在 `/slirn/api/gen_subtitle`、`compose_rough`、`compose_rough_delete`、`render_fine_preview`、`export_fine_video` 入口加 `record_start` + 出口加 `record_finish`
2. **`execution_history.py` 加 `auto_session_id` 字段**：`record_start` 新增 `auto_session_id` 参数；记录条目加 `auto_session_id` 字段；不传时默认空字符串
3. **5 个 endpoint 检测 `X-Slirn-Auto` + `X-Slirn-Auto-Session` header**：
   - 已有 `X-Slirn-Auto` 检测（`req.headers.get("X-Slirn-Auto") == "1"` → `auto=True`）—— 复用
   - 新增 `X-Slirn-Auto-Session` header 透传到 `record_start`
4. **`pipeline_service.py` 在 `run_pipeline()` 启动时生成 `auto_session_id`**（`uuid4().hex[:12]`），传给 5 个 stage handler，handler 透传到 HTTP 请求的 `X-Slirn-Auto-Session` header
5. **新增 `/slirn/api/list_logs` 查询 API**：
   - body: `{task_id, time_from?, time_to?, kinds?, statuses?, auto?}`
   - 用 `tasklib` 解析 task_id → outputs_dir，调 `query_history()`
   - time_from/time_to 转为 started_at 时间戳，filter
6. **前端工作台面板新增「📋 执行日志」折叠区**：挂在任意一个 stage tab 底部或单独 panel
   - 展示字段：开始时间（相对或绝对）/ 阶段 / 操作 / 时长 / 状态 / 详情摘要
   - 筛选条：时间范围（今天 / 近 7 天 / 自定义） + 阶段下拉（多选） + 操作下拉（多选） + 模式（全部 / 手动 / 自动）
   - 轮询：5 秒一次 / 或事件触发刷新
7. **每个操作按需记录 detail**（用户原话：「具体执行情况，你看着相关的操作需要记录什么，你就记什么」）：
   - `gen_subtitle` → extra: `{model_name, segments_count, audio_duration_sec, asr_cost_ms}`
   - `compose_rough` / `compose_rough_delete` → extra: `{output_size_bytes, output_path, ffmpeg_cmd_preview}`
   - `render_fine_preview` → extra: `{output_path, clip_start, clip_end, duration_sec, prescale_count}`
   - `export_fine_video` → extra: `{output_path, resolution, ffmpeg_progress_last}`
   - 其他沿用现有（已有就够）

### 不在范围内（不做）

- ❌ 不记录视频/音频上传（用户未列）
- ❌ 不记录模板保存/应用（用户未列）
- ❌ 不记录人员统计/任务列表刷新（用户未列）
- ❌ 不持久化到云（本地 JSON 即可）
- ❌ 不做实时推送（前端轮询即可）
- ❌ 不重构 `execution_history.py` 现有 API
- ❌ 不改 `KIND_*` 常量命名
- ❌ 不改 `KIND_LABELS` 中文标签
- ❌ 不重写 5 个已埋点端点的代码（只追加缺失的）

## 字段设计

### 落盘条目 schema

```json
{
  "id": "exh-20260920-153045-a1b2c3",
  "kind": "rough_compose",
  "stage": "rough_compose",
  "started_at": 1726231845.123,
  "started_at_iso": "2026-09-20T15:30:45+08:00",
  "finished_at": 1726231852.456,
  "finished_at_iso": "2026-09-20T15:30:52+08:00",
  "duration_ms": 7333,
  "status": "success",       // running / success / failed
  "description": "ffmpeg 拼接片段，输出初剪视频（含随片字幕）",
  "auto": true,              // 流程配置自动执行标识
  "auto_session_id": "abc123def456",  // 同一次自动流的多个操作共享
  "error": "",
  "extra": {                  // 操作特定细节
    "output_path": "tasks/20260920-022/outputs/rough/rough.mp4",
    "output_size_bytes": 12345678,
    "ffmpeg_cmd_preview": "ffmpeg -i cut.mp4 -i cut.srt -c:v libx264 ..."
  }
}
```

### API body 协议（`/slirn/api/list_logs`）

请求：
```json
{
  "task_id": "20260920-022",
  "time_from": "2026-09-20T00:00:00",   // ISO 8601, 可选
  "time_to": "2026-09-20T23:59:59",     // ISO 8601, 可选
  "kinds": ["rough_compose", "rough_compose_delete"],   // 可选
  "statuses": ["success", "failed"],      // 可选
  "auto": "any"                           // "manual" | "auto" | "any"
}
```

响应：
```json
{
  "ok": true,
  "items": [ /* history entries */ ],
  "total": 5
}
```

## 设计草图（待 Design 阶段细化）

- **`auto_session_id` 注入链**：
  ```
  pipeline_service.run_pipeline()
    → 生成 auto_session_id = uuid4().hex[:12]
    → handler_subtitle_generation / handler_subtitle_review / handler_rough_cut / handler_rough_compose / handler_optimize
      → _http_post() 透传 X-Slirn-Auto: 1 + X-Slirn-Auto-Session: <auto_session_id>
        → 后端 endpoint 读 header → 调 record_start(..., auto=True, auto_session_id=...)
  ```
- **手动端点**：无 X-Slirn-Auto header → `auto=False, auto_session_id=""`
- **查询 API**：tasklib 解 task_id → outputs_dir → query_history + time range filter
- **前端面板**：挂在 workbench 区域底部，折叠区；轮询 5 秒；表格展示

## 关联

- 上游：[REQ-20260918-048 execution_history](docs/REQM/)（落盘基础设施）
- 上游：[REQ-20260918-053 query_history](docs/REQM/)（按 kinds/statuses 查询）
- 上游：[REQ-20260919-075 X-Slirn-Auto header](docs/REQM/)（auto 布尔）
- 上游：[REQ-20260919-052 pipeline_service](docs/REQM/)（自动管线 5 阶段）

## 失败案例

| 字段 | 值 |
|---|---|
| 用户实测 | 工作台切到精剪 → 点 AI 智能布局 → 完成后切到粗剪 → 历史只剩粗剪阶段的，看不到刚才精剪的执行记录 |
| 失败信号 | 历史里只有 5 个 kind 的条目（BG_DETECT / AI_LAYOUT / ROUGH_CUT / LINK_PERSON / OPTIMIZE），缺其他 5 个 |
| 期望 | 历史应能列出该任务的全部 10 个操作的执行记录，按时间倒序排列 |