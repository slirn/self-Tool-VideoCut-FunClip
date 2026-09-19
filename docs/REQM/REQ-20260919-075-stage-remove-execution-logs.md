# REQ-20260919-075 精简工作台阶段（去字幕合成）+ 完善执行日志

## 背景

工作台目前 8 个阶段，最后一阶段「字幕合成 / MUXED」无独立功能入口（已被精剪视频阶段的「最终导出」覆盖）。同时执行日志只覆盖了 5 种自动操作（生成字幕/字幕修订/切分修剪/粗剪合成/优化字幕），粒度太粗：用户看不到具体动作（如"删除粗剪成品"是粗剪合成阶段里一个独立的可逆操作），也无法区分手动触发 vs. 流程自动执行。

## 需求

### 1. 移除工作台第 8 阶段（字幕合成/MUXED）

- 从 `_WB_STAGES` 移除 `("mux", "MUXED", "字幕合成", "🎞️", ...)`
- 任务管线终点改为 `FINE_CUT_DONE`（精剪视频阶段完成 = 全部完成）
- dashboard "已完成" 统计仍以 `MUXED` 为口径，但同时兼容 `FINE_CUT_DONE`（已无 MUXED 的任务也算已完成）

### 2. 完善执行日志

每条日志字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `kind` | str | 操作类型枚举（见 §3） |
| `stage` | str | 所属阶段 key（对应 `_WB_STAGES` 的 key） |
| `started_at` / `finished_at` | float | 时间戳 |
| `started_at_iso` / `finished_at_iso` | str | ISO8601 字符串 |
| `duration_ms` | int | 时长（毫秒） |
| `status` | str | running / success / failed |
| `description` | str | 操作描述（含操作主体 + 动作） |
| `auto` | bool | 是否自动执行（流程配置触发） |
| `error` | str | 失败原因 |
| `extra` | dict | 摘要（segments_count / speaker_count / output_duration 等） |

### 3. 需要记录的操作清单

| 阶段 | 操作 | kind 枚举 | 中文标签 |
|---|---|---|---|
| 字幕生成 | 生成字幕 | `subtitle_generation`（已有） | 生成字幕 |
| 切分修剪 | 执行切分修剪 | `rough_cut`（已有） | 执行切分修剪 |
| 切分修剪 | 关联人员ID | `rough_cut_link_person`（新） | 关联人员ID |
| 粗剪合成 | 删除粗剪成品 | `rough_compose_delete`（新） | 删除粗剪成品 |
| 粗剪合成 | 合成初剪视频 | `rough_compose`（已有） | 合成初剪视频 |
| 优化字幕 | 确认保存 | `optimize`（已有） | 确认保存 |
| 精剪视频 | AI智能布局 | `fine_ai_layout`（新） | AI智能布局 |
| 精剪视频 | 检测区域 | `fine_bg_detect`（新） | 检测区域 |
| 精剪视频 | 生成预览 | `fine_preview`（新） | 生成预览 |
| 精剪视频 | 最终导出视频 | `fine_export`（新） | 最终导出视频 |

### 4. description 生成规则

每种操作有默认模板；调用 record_start 时可覆盖：

| 操作 | 默认 description 模板 |
|---|---|
| 生成字幕 | `FunASR seaco-paraformer 识别原始视频，识别 {n} 段，{m} 个说话人`（完成后回填） |
| 关联人员ID | `切分 {n} 段关联到人员ID；更新 {m} 个发言人标签` |
| 执行切分修剪 | `按切分决策生成 {n} 段粗剪片段`（完成后回填） |
| 删除粗剪成品 | `删除 {n} 段粗剪视频文件` |
| 合成初剪视频 | `ffmpeg 拼接 {n} 段片段，输出初剪视频 ({duration}s)` |
| 确认保存 | `优化字幕保存：保留 {n} 段，替换 {m} 处` |
| AI智能布局 | `LLM 分析视频画面，生成布局建议 (cover_intro={n}s)` |
| 检测区域 | `YOLO 检测视频主体区域，分辨率 {w}x{h}` |
| 生成预览 | `生成 {n} 段预览切片，时长 {duration}s` |
| 最终导出视频 | `ffmpeg 渲染精剪视频，分辨率 {w}x{h}，时长 {duration}s` |

### 5. 自动执行标识

- `pipeline_service.run_pipeline` 内部通过 HTTP 调用现有 `/slirn/api/*` 端点（urllib）
- 这些端点内部不知道调用方是 pipeline 还是手动 — 需要在 HTTP header / body 加 `auto=true` 透传
- 现有 endpoints 不改 body schema；用 `headers={"X-Slirn-Auto": "1"}` 透传
- 服务层函数（如 `_do_generate_subtitle`、`_do_optimize_save`）接 `auto` 参数，调用 `record_start(..., auto=auto)`
- 前端展示时：`auto=true` 显示「⚙️ 自动」徽章；`auto=false` 不显示

### 6. 任务范围（只显示当前任务）

`execution_history.json` 本身已按任务分文件（`tasks/<tid>/outputs/execution_history.json`），天然隔离。无需改查询端点。

### 7. UI 展示

工作台「执行日志」面板（已存在，app.py:3342 附近）：
- 每条记录显示：阶段 + 操作 + 开始时间 + 截止时间 + 时长 + description + auto 徽章
- 失败记录高亮（红色），并在底部显示 error 前 200 字符

## 验收标准

| # | 标准 |
|---|---|
| AC-1 | 工作台阶段只剩 7 个（无"字幕合成"） |
| AC-2 | 任务状态达到 `FINE_CUT_DONE` 后，dashboard"已完成"统计 +1 |
| AC-3 | `execution_history.json` schema 升级：新增 `description` 和 `auto` 字段（向后兼容：旧记录 `description=""`、`auto=false`） |
| AC-4 | 10 种操作全部能在执行后产生 history 条目 |
| AC-5 | 自动执行的日志 `auto=true`，手动为 `auto=false` |
| AC-6 | UI 中自动日志显示「⚙️ 自动」徽章，手动无徽章 |
| AC-7 | 不同任务的执行日志互不可见（per-task 文件隔离） |
| AC-8 | `pytest tests/ -q` 全过；新增 ≥ 12 测试 |

## 不做的事

- ❌ 不改 `TaskStatus.MUXED` 枚举本身（保持向后兼容；只在统计/前端判断时兼容）
- ❌ 不做执行日志的导出/分享
- ❌ 不做历史日志的搜索/筛选 UI（仅 query_history 后端能力已就绪）
- ❌ 不做按时间范围的日志聚合
- ❌ 不持久化「当前任务最近一次失败原因」（仅本次展示）
