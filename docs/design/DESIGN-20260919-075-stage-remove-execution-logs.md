# DESIGN-20260919-075 阶段简化 + 执行日志完善

## 决策摘要

| 问题 | 决策 |
|---|---|
| MUXED 阶段去留 | 删除 UI 阶段 + 删除 WB 显示；TaskStatus 枚举保留（向后兼容） |
| 终态判断 | 改为 `FINE_CUT_DONE` 视为"已完成"；但代码同时兼容 `MUXED`（旧任务） |
| 历史记录粒度 | 按操作拆（10 种），每条独立 kind |
| 字段扩展 | 新增 `description` + `auto` 字段；旧记录补默认 |
| auto 透传方式 | HTTP header `X-Slirn-Auto: 1`；urllib.Request 加 header；FastAPI endpoint 读 header 传入 service |
| pipeline 自动执行标记 | run_pipeline 调所有子操作时统一带 `X-Slirn-Auto=1` |
| 任务隔离 | 已天然 per-task（`tasks/<tid>/outputs/execution_history.json`），不动 |
| UI 改动 | 工作台「执行日志」面板扩展每行：阶段 + 操作 + 时间 + 时长 + 描述 + auto 徽章 |

## 架构改动

### 1. `_WB_STAGES` 缩减

```python
# app.py
_WB_STAGES = [
    ("assets",          "ASSETS_READY",         "素材准备", "📦", ...),
    ("subtitle",        "SUBTITLE_GENERATED",   "字幕生成", "🎙", ...),
    ("subtitle_review", "SUBTITLE_REVIEWED",    "字幕修订", "📝", ...),
    ("rough_cut",       "ROUGH_CUT_DONE",       "切分修剪", "✂️", ...),
    ("rough_compose",  "FINE_SUBTITLE_DONE",   "粗剪合成", "🎥", ...),
    ("fine_review",     "FINE_SUBTITLE_REVIEWED", "优化字幕", "✨", ...),
    ("fine_cut",        "FINE_CUT_DONE",        "精剪视频", "🎬", ...),
]
```

7 个阶段（原 8）。

### 2. 任务完成判断

```python
# app.py
def _stats(mgr):
    ...
    if s.status in (TaskStatus.MUXED, TaskStatus.FINE_CUT_DONE):
        done += 1   # 同时兼容旧 MUXED 与新 FINE_CUT_DONE
```

### 3. execution_history.py 扩展

#### 新 kind 常量

```python
KIND_FINE_AI_LAYOUT = "fine_ai_layout"
KIND_FINE_BG_DETECT = "fine_bg_detect"
KIND_FINE_PREVIEW = "fine_preview"
KIND_FINE_EXPORT = "fine_export"
KIND_ROUGH_CUT_DELETE = "rough_compose_delete"   # 操作：删除粗剪成品
KIND_ROUGH_CUT_LINK_PERSON = "rough_cut_link_person"
```

注：`rough_cut_delete` 实际属于"粗剪合成"阶段（操作是删除上一轮粗剪成品），但 kind 命名为 `rough_compose_delete` 以贴合其归属。

#### 新字段

`record_start(..., description: str = "", auto: bool = False)`

每条记录初始结构：

```python
{
    "id": "exh-...",
    "kind": "fine_export",
    "stage": "fine_cut",           # 新增（从 kind → stage 的映射查 KIND_TO_STAGE）
    "started_at": ...,
    "started_at_iso": ...,
    "finished_at": None,
    "finished_at_iso": None,
    "duration_ms": None,
    "status": "running",
    "description": "ffmpeg 渲染精剪视频，...",   # 新增
    "auto": False,                                # 新增
    "error": "",
    "extra": {...},
}
```

#### 新辅助

```python
KIND_TO_STAGE: dict[str, str] = {
    KIND_SUBTITLE_GENERATION: "subtitle",
    KIND_SUBTITLE_REVIEW: "subtitle_review",
    KIND_ROUGH_CUT: "rough_cut",
    KIND_ROUGH_CUT_LINK_PERSON: "rough_cut",
    KIND_ROUGH_COMPOSE: "rough_compose",
    KIND_ROUGH_CUT_DELETE: "rough_compose",
    KIND_OPTIMIZE: "fine_review",
    KIND_FINE_AI_LAYOUT: "fine_cut",
    KIND_FINE_BG_DETECT: "fine_cut",
    KIND_FINE_PREVIEW: "fine_cut",
    KIND_FINE_EXPORT: "fine_cut",
}

DEFAULT_DESCRIPTIONS: dict[str, str] = {
    KIND_SUBTITLE_GENERATION: "FunASR seaco-paraformer 识别原始视频",
    KIND_ROUGH_CUT: "按切分决策生成粗剪片段",
    KIND_ROUGH_CUT_LINK_PERSON: "切分片段关联到人员ID",
    KIND_ROUGH_CUT_DELETE: "删除上一轮粗剪成品",
    KIND_ROUGH_COMPOSE: "ffmpeg 拼接片段，输出初剪视频",
    KIND_OPTIMIZE: "优化字幕保存",
    KIND_FINE_AI_LAYOUT: "LLM 分析视频画面，生成布局建议",
    KIND_FINE_BG_DETECT: "YOLO 检测视频主体区域",
    KIND_FINE_PREVIEW: "生成精剪预览切片",
    KIND_FINE_EXPORT: "ffmpeg 渲染精剪视频",
    KIND_SUBTITLE_REVIEW: "字幕修订保存",
}
```

`record_start` 接收 `description=""` 时，按 kind 从 `DEFAULT_DESCRIPTIONS` 取默认；`auto=False` 默认。

`patch_extra` 不变；record_finish 不变。`query_history` 过滤时把 `auto` 也作为可选条件（不破坏现有签名）。

#### 向后兼容

读旧文件时（缺 `description` / `auto` / `stage` 字段），`_read` 后**不做迁移**——前端展示时给默认值：

```javascript
const desc = item.description || DEFAULT_DESCS[item.kind] || '';
const isAuto = !!item.auto;
const stage = item.stage || KIND_TO_STAGE[item.kind] || '';
```

### 4. auto 透传机制

#### 4.1 pipeline_service.py → urllib 加 header

```python
# pipeline_service.py:_do_call
def _do_call(url: str, body: dict, auto: bool = True) -> dict:
    headers = {"Content-Type": "application/json"}
    if auto:
        headers["X-Slirn-Auto"] = "1"
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers=headers, method="POST",
    )
    with urllib.request.urlopen(req, timeout=...) as resp:
        return json.loads(resp.read().decode("utf-8"))
```

所有 STAGE_HANDLERS 调 `_do_call(..., auto=True)`。

#### 4.2 FastAPI endpoint 读 header

```python
# app.py (pattern 通用)
async def some_endpoint(request: Request, ...):
    auto = request.headers.get("X-Slirn-Auto") == "1"
    # 调 service 层时传 auto=auto
    ...
```

#### 4.3 service 函数签名扩展

```python
# asr_service.py / compose_service.py / optimize_service.py / etc.
def generate_subtitle(..., auto: bool = False) -> str:
    exec_id = record_start(..., auto=auto, description=DEFAULT_DESCRIPTIONS[KIND_SUBTITLE_GENERATION])
    ...
```

#### 4.4 前端手动调用

前端 router.js 现有 fetch 调用**不动**（不带 header → 默认 auto=false）。仅 pipeline_service 发请求时带 header。

### 5. UI 改动

工作台「执行日志」面板（app.py:_render_workbench 或 router.js 渲染函数）：

每行 HTML：

```html
<div class="slirn-exec-row {status_class}">
  <div class="slirn-exec-header">
    <span class="slirn-exec-stage">精剪视频</span>
    <span class="slirn-exec-op">最终导出视频</span>
    {is_auto ? '<span class="slirn-exec-auto-badge">⚙️ 自动</span>' : ''}
    <span class="slirn-exec-duration">12m34s</span>
  </div>
  <div class="slirn-exec-desc">ffmpeg 渲染精剪视频，分辨率 1920x1080，时长 7234s</div>
  {error ? `<div class="slirn-exec-error">${error}</div>` : ''}
</div>
```

CSS 新增：

```css
.slirn-exec-stage { font-weight: 600; color: var(--accent-solid); }
.slirn-exec-op { color: var(--text-secondary); }
.slirn-exec-auto-badge {
  background: rgba(79, 124, 255, 0.12);
  color: #4f7cff;
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 11px;
}
.slirn-exec-row.failed { border-left: 3px solid #ef4444; }
.slirn-exec-error { color: #ef4444; font-size: 12px; margin-top: 4px; }
```

## 关键文件改动

| 文件 | 改动 |
|---|---|
| [slirn_home/app.py](slirn_home/app.py) | `_WB_STAGES` 删 mux；`_stats` 终态判断；新增 auto header 读取 + 透传 service |
| [slirn_home/execution_history.py](slirn_home/execution_history.py) | 新 kind 常量；`record_start` 加 `description`/`auto` 参数；`KIND_TO_STAGE` / `DEFAULT_DESCRIPTIONS` 映射；`ALL_KINDS` 扩展 |
| [slirn_home/asr_service.py](slirn_home/asr_service.py) | `record_start` 传 description + auto；`patch_extra` 回填 segments/speakers 数 |
| [slirn_home/compose_service.py](slirn_home/compose_service.py) | 同上 + 补 rough_compose_delete 入口 |
| [slirn_home/optimize_service.py](slirn_home/optimize_service.py) | 同上 |
| [slirn_home/revision_service.py](slirn_home/revision_service.py) | `link_speaker_to_person` 新增 logging（rough_cut_link_person） |
| [slirn_home/cutlist_service.py](slirn_home/cutlist_service.py) | `execute_cut` logging 已存在，确认 description 传入 |
| 新文件 / 改动 [slirn_home/fine_ai_layout.py](slirn_home/fine_ai_layout.py) (如有) | 加 logging |
| 新文件 / 改动 [slirn_home/fine_bg_detect.py](slirn_home/fine_bg_detect.py) (如有) | 加 logging |
| [slirn_home/app.py](slirn_home/app.py) 精剪 endpoints | `render_fine_preview` + `export_fine_video` 加 logging |
| [slirn_home/pipeline_service.py](slirn_home/pipeline_service.py) | `_do_call` 加 `X-Slirn-Auto` header |
| [slirn_home/static/router.js](slirn_home/static/router.js) | 执行日志渲染：description + auto 徽章 |
| [slirn_home/static/home.css](slirn_home/static/home.css) | `.slirn-exec-*` 样式 |
| [tests/test_workbench.py](tests/test_workbench.py) | ≥ 12 个新测试 |

## 测试

| 测试 | 验证 |
|---|---|
| `test_wb_stages_no_mux` | `_WB_STAGES` 不含 ("mux", "MUXED")；长度=7 |
| `test_stats_counts_fine_cut_done_as_done` | FINE_CUT_DONE 状态任务计入 done |
| `test_stats_counts_muxed_as_done` | MUXED 状态任务也计入 done（兼容） |
| `test_history_record_with_description` | record_start(description="X") 后 load 读到 description="X" |
| `test_history_record_with_auto` | record_start(auto=True) 后 load 读到 auto=True |
| `test_history_default_description_by_kind` | 不传 description 时按 kind 查默认 |
| `test_history_kind_to_stage_mapping` | 所有 kind 都能映射到 stage |
| `test_history_all_kinds_include_new_ones` | ALL_KINDS 含 6 个新 kind |
| `test_pipeline_auto_header_added` | mock urllib，验证 Request 带 X-Slirn-Auto=1 |
| `test_asr_service_passes_auto_to_record` | patch record_start，验证 auto=True 时传入 True |
| `test_compose_service_delete_logs_history` | 调删除粗剪成品 API，验证 history 多一条 rough_compose_delete |
| `test_fine_export_logs_history` | 调 export_fine_video，验证 history 多一条 fine_export |

## 风险与边界

| 风险 | 缓解 |
|---|---|
| 旧 execution_history.json 缺字段 | 前端用默认值兜底；后端查询兼容 |
| `X-Slirn-Auto` header 被反向代理吞 | 当前是 Gradio 内嵌 FastAPI，无中间件 |
| 同一操作既可手动又可自动，日志难区分 | auto 字段语义清晰；前端徽章 |
| 删除粗剪成品若并发触发会多次记录 | 每次操作独立 record；并发保护靠各自 service 内部锁 |
| `_WB_STAGES` 长度变化导致 stage 索引移位 | 旧 URL / 状态里的 stage key 仍可映射（key=string，不靠索引） |
| `TaskStatus.MUXED` 还在枚举中（保留兼容） | dashboard 统计同时认 MUXED + FINE_CUT_DONE；不再有 UI 阶段引导到 MUXED |
| 自动执行时 record_start 失败 | record_start 已有 try/except 兜底（return ""），不影响主流程 |
| `description` 含敏感数据（如 LLM 输入） | 调用方负责脱敏；记录器只截断长度 |
| `pipeline_service` 调用链中 auto 透传丢失 | 在 `_do_call` 集中加 header，所有 stage handler 必走此函数 |
