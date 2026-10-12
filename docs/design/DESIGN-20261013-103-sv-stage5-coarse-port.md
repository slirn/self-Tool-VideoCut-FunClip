# DESIGN-20261013-103 — 短视频 Stage 5 粗剪按长视频 compose_rough 整套搬

> 父任务：[REQ-20261013-102 §2.3 C2](../REQM/REQ-20261013-102-sv-coarse-refine-finalize-port.md)
> 目标：把 Stage 5（粗剪合成）按长视频 `compose_rough` + `compose_rough_status` + `compose_rough_delete` + `compose_rough_preview_subs` 四件套搬过来；UI 改造 `_stage5_coarse` 加状态区 + 复制/下载 SRT。
> 优先级：3 子任务之首（用户 2026-10-13 串行指令：C2 → D1 → C1）

## 1. 现状

短视频 Stage 5：

- 端点：`POST /slirn/api/short_video_stage4_run`（`app.py:10303`），内部 `short_video_coarse.run_stage4` 同步阻塞执行
- 服务模块：`slirn_home/short_video_coarse.py:194 run_stage4`
  - 读 `stage3/highlights.json` + `stage2/raw.srt` → 按 `subtitle_lines[].src_index` 反查时间戳
  - 用 moviepy 按多段拼接切出每条 highlight 独立 mp4 + 占位 srt
  - 落 `stage4/coarse_NN.mp4` + `coarse_NN.srt`（**stage4 目录是历史遗留命名**，未随重编号改 —— 服务层实际写 `pipeline.stage5_coarse`）
  - 状态字段：`project.json → pipeline.stage5_coarse`（`status` / `count` / `highlights[]` / `logs`）
- UI：`short_video_ui.py:771 _stage5_coarse` —— 只有"每条 highlight 一个 `<video>` 卡片 + 下载按钮"，**缺**状态区 + 复制/下载 SRT 按钮 + 删除按钮 + 全片预览入口

长视频 compose_rough 四件套：

| 端点 | 行号 | 功能 |
|---|---|---|
| `POST /slirn/api/compose_rough` | `app.py:9217` | 启动合成（后台线程；用 `compose_service.start_compose`） |
| `POST /slirn/api/compose_rough_status` | `app.py:9282` | 进度轮询（内存 `_JOBS[tid]`；服务重启兜底"产物在即视为完成"） |
| `POST /slirn/api/compose_rough_delete` | `app.py:9298` | 同步删除 mp4 + srt 副产物 + 写执行历史 |
| `POST /slirn/api/compose_rough_preview_subs` | `app.py:9331` | 返回连续时间轴 SRT 字符串（`compose_service.format_srt`） |

调用链路：compose_rough 端点 → `compose_service.start_compose` 后台线程 → `compose_rough_cut` 用上游 `VideoClipper.video_clip` 拼接 → 落 `rough_compose.mp4` + 副产物 `.srt` → 进度回调 `_FrameProgressLogger` 通过 `_JOBS[tid]["progress"]` 更新。

## 2. 关键差异（短视频 vs 长视频）

| 维度 | 长视频 | 短视频 | 决策 |
|---|---|---|---|
| 源 | 整段任务视频 | 短视频项目选源后**单段**视频（`base_material_id`） | 同：都是单段 |
| 字幕源 | `revision.json` + `fine_revision.json`（keep/delete 决策） | `stage3/highlights.json`（每条 hl 独立 + 字幕行） | 不同：短视频没有 keep/delete 决策流 |
| 保留区间 | `cutlist_service.effective_keep_units` → `[(start_ms, end_ms)]` | `subtitle_lines[].src_index` 反查 `stage2/raw.srt` → `[(start_ms, end_ms)]` | 不同：短视频按字幕行多窗拼接，长视频按决策区间单段 |
| 合成 | 上游 VideoClipper（moviepy） | moviepy.subclip + concatenate | **不**直接用 VideoClipper：保留行概念不同；短视频沿用 moviepy.subclip |
| 产物 | `rough_compose.mp4` + `rough_compose.srt`（**单段**整段） | `stage4/coarse_NN.mp4` + `coarse_NN.srt`（**N 段**每条 highlight 独立） | 不同：短视频是 N 个独立 mp4；**不**合并成单段 |
| 异步 | 内存 `_JOBS[tid]` + 进度回调 | 当前**同步**阻塞 | **改异步**：仿 `compose_service.start_compose` 模式 |
| 删除 | `compose_rough_delete` 同步清 mp4+srt | **没有** | **新增** |
| SRT 预览 | `compose_rough_preview_subs` 调 `compose_service.format_srt`（整段连续） | **没有** | **新增**：跨所有 coarse_NN.srt 拼成连续时间轴 SRT |
| 执行历史 | `KIND_ROUGH_COMPOSE` / `KIND_ROUGH_COMPOSE_DELETE` | **没有**（只用 KIND_SHORT_VIDEO_AI） | **新增** `KIND_SHORT_VIDEO_COARSE` / `KIND_SHORT_VIDEO_COARSE_DELETE` |

## 3. 设计决策

### 3.1 异步执行（仿 `compose_service.start_compose` 模式）

- 新增内存 job 表 `slirn_home/short_video_coarse.py` 内：
  - `_JOBS: dict[tuple[tid, pid], dict]` —— key 是 (task_id, project_id) 元组
  - `_JOBS_LOCK = threading.Lock()`
  - 状态字段：`{state, progress, stage, error, started_at, finished_at, count, total_count}`
- 新增 `start_coarse_async(repo_root, tid, pid, *, progress_cb=None) -> bool`：
  - 已 running → False
  - 起 daemon 线程调 `run_stage4`（**重命名**为内部 `run_stage4_sync` + 加 progress_cb 参数；当前 `run_stage4` 名字保留，**接口签名不变**，`progress_cb` 设为关键字默认 None 兼容现有调用）
  - 线程内：先写状态 running → 调 `run_stage4` → 收尾 done/failed；写执行历史 `KIND_SHORT_VIDEO_COARSE`
- 新增 `coarse_job_status(tid, pid) -> dict | None`：内存 job 优先，service 重启兜底（产物在即视为 done）

### 3.2 端点设计（短 vs 长映射）

| 短视频新端点 | 长视频对应 | 行号（新） | 实现 |
|---|---|---|---|
| `POST /slirn/api/short_video_stage5_export` | `compose_rough` (9217) | app.py 新增（10303 之后） | 起 `start_coarse_async` 线程；返回 `{running, job, count}` |
| `POST /slirn/api/short_video_stage5_export_status` | `compose_rough_status` (9282) | app.py 新增 | 内存 job + 磁盘兜底 |
| `POST /slirn/api/short_video_stage5_delete` | `compose_rough_delete` (9298) | app.py 新增 | 同步清 stage4/coarse_NN.{mp4,srt} + 写历史 |
| `POST /slirn/api/short_video_stage5_preview_subs` | `compose_rough_preview_subs` (9331) | app.py 新增 | 跨 N 个 coarse_NN.srt 拼成连续时间轴 SRT（**用 `compose_service.format_srt`** 直接复用） |

### 3.3 `run_stage4` 内部改造（**不破坏现有签名**）

保留 `run_stage4(repo_root, task_id, project_id, *, start_ost_ms=0, end_ost_ms=100, progress_cb=None) -> dict` 签名不变（默认 `progress_cb=None` 走原行为），**新增**关键字参数让外部可注入进度回调。改动点：

- 每个 clip 完成时：若 `progress_cb` 不为空 → `progress_cb(f"stage4_clip_done", {"index": idx, "count": len(results), "duration_sec": res.duration_sec})` （参照 `run_stage6_batch` 已有 progress_cb 模式）
- 全部完成时：若 `progress_cb` 不为空 → `progress_cb("stage4_done" / "stage4_failed", {"count": len(results)})`

### 3.4 状态字段增强

`pipeline.stage5_coarse` 现有字段：
```
{status, started_at, finished_at, count, highlights[{index, coarse_mp4, coarse_srt, status, duration_sec, elapsed_sec, error}], logs}
```

**新增**字段（**不破坏**老字段，纯增量）：
```
{... 现有 ..., 
 total_duration_sec: float,    # 所有 coarse mp4 时长累加
 preview_subs_path: str,        # stage5/preview_subs.srt（compose_rough_preview_subs 缓存）
 last_action: str,              # "export" | "delete" | None
 last_action_at: ISO,
}
```

`preview_subs_path` 落 `stage5/preview_subs.srt`（**新增**目录 `stage5/` 实际已存在但为空目录，新增此文件兜底连续 SRT 预览产物）。`stage5` 与现有 `stage4/coarse_NN.*` 命名不一致 —— **不**做物理迁移，仅在 stage5_dir 内落预览文件。

### 3.5 UI 改造 `_stage5_coarse`

保留现有每条 highlight `<video>` 卡片 + 下载按钮。**新增**：

1. **顶部状态卡**（参考长视频 `_render_rough_compose_zone` L1078-1216 风格）：
   - 状态徽章（done/failed/running/pending）
   - 「⏱ 总时长 Xm Ys」+ 「📊 N 条」
   - 「🎬 重新合成粗剪」按钮（带 disabled 当 running）
   - 「🗑 删除粗剪成片」按钮（confirm）
   - 进度条（仅 running 状态可见，仿 `startFineExportInline` L4024 模式）
2. **底部操作行**（3 个新按钮）：
   - 「📋 复制连续 SRT」→ 调 `stage5_preview_subs` + clipboard
   - 「⬇ 下载连续 SRT」→ 调 `stage5_preview_subs` + `<a download>` 触发
   - 「📋 复制单条 SRT」+「⬇ 下载单条 SRT」放在每个 clip 卡片内（**保留**现有下载按钮，新增「复制 SRT」+「下载 SRT」）

### 3.6 router.js 接线

新增 `data-action` 派发（仿 `rcCompose` L2714 / `rcDelete` L2756 / `rcSubsCopy` L2772 / `rcSubsDownload` L2792）：

- `sv-stage5-export` → `svStage5Export(btn)` → POST + 启动 setInterval 轮询（**用 setInterval 不是 setTimeout 链**，REQ-093 教训）
- `sv-stage5-export-cancel`（**v1 不实现**：短视频单条 highlight 切 mp4 几秒内完成，cancel 暂不必要；stage6_batch 才需要 cancel 走 cancel_render 模式）
- `sv-stage5-delete` → `svStage5Delete(btn)` → confirm + POST
- `sv-stage5-subs-copy` → `svStage5SubsCopy(btn)` → fetch + clipboard
- `sv-stage5-subs-download` → `svStage5SubsDownload(btn)` → fetch + `<a download>`
- 行内 `sv-stage5-clip-subs-copy` / `sv-stage5-clip-subs-download` → 单条 SRT 操作

### 3.7 复用 helper 清单（C2 子任务执行时 import）

```python
from slirn_home.compose_service import format_srt, _probe_duration
from slirn_home.execution_history import record_start, record_finish, patch_fields
```

**删除**短视频自己 1 处重复实现（节省 ~18 行）：
- `short_video_finalize._ffprobe_duration` 保留（v1 不动，避免牵动 Stage 6 finalize）
- **本子任务不动** short_video_coarse 的 `_fmt_srt_time`（C1 再统一改 import）

### 3.8 执行历史

新增常量到 `slirn_home/execution_history.py`：

```python
KIND_SHORT_VIDEO_COARSE = "short_video_coarse"
KIND_SHORT_VIDEO_COARSE_DELETE = "short_video_coarse_delete"
KIND_LABELS 增 ("short_video_coarse", "粗剪合成（短）")
            ("short_video_coarse_delete", "删除粗剪成品（短）")
KIND_TO_STAGE 增 ("short_video_coarse", "stage5_coarse")
              ("short_video_coarse_delete", "stage5_coarse")
```

**不**写长视频 `tasks/<tid>/outputs/execution_history.json`；短视频写自己路径 `tasks/<tid>/short_video/<pid>/execution_history.json`（`execution_history` helper 接受 `outputs_dir` 参数，把短视频 stage5_dir 传进去即可）。

## 4. 数据流

### 4.1 启动异步导出

```
[UI: 🎬 重新合成粗剪] → router.js svStage5Export
  → POST /slirn/api/short_video_stage5_export {task_id, project_id}
    → short_video_coarse.start_coarse_async(repo_root, tid, pid, progress_cb=...)
      → _JOBS[(tid, pid)] = {state: running, ...}
      → daemon thread.start()
        → run_stage4(repo_root, tid, pid, progress_cb=inner_cb)
          → inner_cb → 更新 _JOBS[(tid, pid)]["progress"] / ["stage"]
        → thread 收尾：_JOBS[(tid, pid)].update({state: done/failed, finished_at: ...})
        → execution_history.record_finish(stage5_dir, exec_id, ...)
    → return {running: True, job: {state: running, count: N}}
```

### 4.2 进度轮询

```
[UI: setInterval 1.5s] → POST /slirn/api/short_video_stage5_export_status
  → coarse_job_status(tid, pid)
    → 内存 job 优先：_JOBS.get((tid, pid)) → dict
    → 兜底：stage5/highlights 全部 done? → {state: done, progress: 100}
           都没有 → 404-like (前端按 idle 处理)
  → return {job: {...}}
```

### 4.3 删除

```
[UI: 🗑 删除粗剪成片] → router.js svStage5Delete
  → confirm "确认删除 N 条粗剪 mp4 + N 条占位 srt？"
  → POST /slirn/api/short_video_stage5_delete
    → 枚举 stage4/coarse_*.{mp4,srt} 全部 unlink
    → execution_history.record_start(KIND_SHORT_VIDEO_COARSE_DELETE, ...)
    → execution_history.record_finish(success=True)
    → svc.reset_downstream_stages(project, "stage5_coarse")  # 作废 stage 6/7
  → return {deleted: bool, removed: [...], remaining: [...]}
```

### 4.4 连续 SRT 预览

```
[UI: 📋 复制连续 SRT] → POST /slirn/api/short_video_stage5_preview_subs
  → 读 stage4/coarse_NN.srt 全部 → merge_intervals_ms(每条 coarse 时间区间)
  → 拼接 + compose_service.format_srt(units, text_overrides=None)
  → 写 stage5/preview_subs.srt（缓存，第二次取直接读）
  → return {srt: "..."}
```

## 5. 验收标准（C2 完整闭环）

1. 端点齐：stage5_export / stage5_export_status / stage5_delete / stage5_preview_subs 全部 200 + 正常响应
2. 异步进度可轮询：启动后 setInterval 1.5s 拿到 progress 0-100；done 时返回 stage5_coarse 完整 state
3. UI 改造：`_stage5_coarse` 顶部状态卡 + 3 个新按钮（复制/下载连续 SRT + 删除）可见
4. 单条 SRT 操作：每个 clip 卡片内「📋 复制 SRT」+「⬇ 下载 SRT」可工作
5. 删除：清 stage4/coarse_NN.{mp4,srt} + 写历史 + 推进 reset_downstream_stages
6. 短/长隔离：调本子任务端点**不**写长视频 `tasks/<tid>/outputs/...` 任何文件（用 ffprobe 探测产物目录验证）
7. 质量门：ruff 工作树 == HEAD（零新增）、node --check router.js 通过、全量 pytest ≥ 现有基线 + C2 新增 ≥ 5 测试通过
8. 7861 重启：Python 改动 → `TaskStop b3dh9r95p` + 重启 PID（保留后台任务 b3dh9r95p 模式）

## 6. 风险与备选

- **风险 1**：短视频每条 highlight 独立 mp4 不合并成"粗剪成片"（与长视频 single mp4 概念不同）。**缓解**：UI 状态卡明示「N 条独立 mp4」+ 提供「连续 SRT」近似代替长视频的「随片 srt」概念
- **风险 2**：moviepy 全片 decode + 逐段 subclip 对大源视频内存压力（短片源一般 1-2 分钟 OK；源大时需切到 ffmpeg direct）。**缓解**：v1 沿用现有 moviepy 路径（沿用至今稳定）；源超 500MB 时记录 warning 不阻塞
- **风险 3**：执行历史写 `stage5_dir` 而非 `outputs_dir`，与长视频 `record_start(outputs_dir, ...)` 默认路径不一致。**缓解**：所有 `record_start/stage5_dir, ...` 调用统一传短视频路径；不写长视频 outputs 兜底逻辑保持

## 7. 闭环记录（C2 完成后回填）

| 项 | 值 |
|---|---|
| REQ 文档 | REQ-20261013-103-sv-stage5-coarse-port.md |
| DESIGN 文档 | DESIGN-20261013-103-sv-stage5-coarse-port.md（本文件） |
| 提交号 | TBD |
| push ref-update | TBD |
| 7861 PID | TBD |
| 测试 | TBD passed |
| 下一子任务 | D1（Stage 6 字幕优化按长视频搬） |
