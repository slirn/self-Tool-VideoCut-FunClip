# DESIGN-20260919-074 精剪·导出最终视频 → 异步后台任务 + 进度展示

## 决策摘要

| 问题 | 决策 |
|---|---|
| 渲染放哪？ | 后台 daemon thread（in-process） |
| 进度怎么推？ | 前端轮询 GET /slirn/api/render_status（每 1.5 秒） |
| 用 ffmpeg `-progress` 解析 | `pipe:1` stdout，每 0.4 秒一行 `key=value` |
| 进度百分比怎么算？ | `out_time_ms / ffprobe_duration * 100` |
| ETA 怎么算？ | `remaining_ms / 1000 / speed_x` |
| 取消怎么传？ | `POST /cancel_render` → `proc.terminate()`（SIGTERM） |
| 完成后保留多久？ | 5 分钟（够前端最后一次查询拿到结果；之后清理） |
| 预览怎么办？ | 不动（render_fine_preview ≤30 秒，120 秒足够） |
| 进程重启 job 丢失？ | 接受（用户重试即可；持久化需 RQ/Celery，超出本需求） |

## 架构图

```
前端 (router.js)                   FastAPI (app.py)                   后台 daemon thread
  │                                  │                                       │
  │ POST /export_fine_video          │                                       │
  │ ────────────────────────────────>│                                       │
  │                                  │ 创建 _RenderJob → 启 thread            │
  │ <───── {job_id, toast: 启动成功} ─│<──────────────────────────────────────│
  │                                  │                                       │ _run_fine_render_async
  │                                  │                                       │   ├─ ffprobe 拿总时长
  │                                  │                                       │   ├─ subprocess.Popen
  │                                  │                                       │   │    ffmpeg -progress pipe:1
  │                                  │                                       │   └─ 循环读 stdout
  │                                  │                                       │       parse out_time_ms=
  │ GET /render_status?job_id=X      │                                       │       parse speed=2.5x
  │ ────────────────────────────────>│ 读 _JOB_REGISTRY[job_id]              │       update job.progress_*
  │ <───── {state, pct, elapsed,     │<─────── 更新 job.elapsed_sec ─────────│       update job.eta_sec
  │        speed, eta, output_url} ───│                                       │
  │  (每 1.5 秒轮询)                 │                                       │
  │                                  │                                       │ ffmpeg 退出
  │ GET /render_status               │                                       │   ├─ returncode == 0
  │ <──── {state: 'done', url} ──────│<──── job.state = 'done' ───────────────│   └─ job.output_url = ...
  │ 关闭模态框 + 打开下载             │                                       │
  │                                  │                                       │ (5 分钟后清理 job)
```

## 数据结构

```python
@dataclass
class _RenderJob:
    job_id: str                              # job_<ts_ms>_<pid>
    task_id: str                             # 来源任务
    state: str = "queued"                    # queued/running/done/failed/cancelled
    started_at: float = 0.0                  # 线程启动时间 (time.time())
    finished_at: float = 0.0                 # 线程退出时间
    elapsed_sec: float = 0.0                 # 已用秒数
    progress_pct: float = 0.0                # 0-100
    progress_time_ms: int = 0                # 已编码毫秒（来自 ffmpeg out_time_ms）
    total_duration_ms: int = 0               # 源视频总毫秒（启动时 ffprobe）
    speed_x: float = 0.0                     # ffmpeg speed=2.5x
    eta_sec: float = -1.0                    # 预计剩余秒数
    error: str = ""                          # 失败时 stderr 末尾 500 字符
    output_url: str = ""                     # 成功时的下载链接
    proc: subprocess.Popen | None = None     # 用于 cancel

_JOB_REGISTRY: dict[str, _RenderJob] = {}   # job_id → job
_JOB_LOCK = threading.Lock()                # 保护 _JOB_REGISTRY 读写
_JOB_TTL_SEC = 300                          # 完成/失败/取消后保留 5 分钟
```

## 后端实现要点

### 1. `_run_fine_render_async(job, tid, mgr, out_path)`

与同步版的关键差异：
- `subprocess.Popen`（不是 run）+ `stdout=PIPE, stderr=PIPE, text=True, bufsize=1`
- 加 `-progress pipe:1 -nostats` 标志
- 主循环读 stdout，parse `out_time_ms=` / `speed=` / `progress=`
- 每 0.5 秒刷新 elapsed + ETA（不每行刷新 — 避免频繁写共享 dict）
- 退出后 `proc.wait()` 拿 returncode

### 2. filter_complex 组装需独立化

当前 `_run_fine_render` 是 filter_complex + subprocess.run 混在一起。要拆：

```python
def _assemble_fine_filter(tid, mgr, duration, preview_start):
    """返回 (filter_complex, input_args, sub_input_tmp, _err_or_none)"""
    # 复用 _run_fine_render 前半部分（filter_complex + 输入 args）的所有逻辑
    ...
    return {"ok": True, "filter_complex": ..., "input_args": ..., "sub_input_tmp": ...}
    # 或失败时 {"ok": False, "error": ...}
```

`_run_fine_render` 改为先调 `_assemble_fine_filter`，再 `subprocess.run`（同步路径）。
`_run_fine_render_async` 同样先调 `_assemble_fine_filter`，再 Popen。

### 3. ffmpeg 命令差异

| 项 | 同步 `_run_fine_render` | 异步 `_run_fine_render_async` |
|---|---|---|
| 调用方式 | `subprocess.run(..., timeout=120)` | `subprocess.Popen(..., stdout=PIPE)` |
| timeout | 120 | None（由用户取消） |
| `-progress pipe:1` | 无 | 有 |
| `-nostats` | 无 | 有（让 stderr 不刷进度噪音） |
| 取消 | `subprocess.TimeoutExpired` 自动 | `proc.terminate()` (SIGTERM) + 5s 后 SIGKILL |

### 4. 三个新端点

| 端点 | 方法 | 用途 |
|---|---|---|
| `POST /slirn/api/export_fine_video` | 启动 job | 立即返回 `{job_id, toast}` |
| `GET /slirn/api/render_status?job_id=X` | 查进度 | 返 `{state, pct, elapsed, speed, eta, output_url, error}` |
| `POST /slirn/api/cancel_render` | 取消 | body `{job_id}`，发 SIGTERM |

### 5. SIGTERM → SIGKILL 兜底

后台线程内：

```python
def _kill_with_grace(proc, grace_sec=5.0):
    proc.terminate()
    try:
        proc.wait(timeout=grace_sec)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
```

## 前端实现要点

### 1. router.js 替换 fine-export 分支

详见 REQ-20260919-074 第 4 节。核心：
- fetch → `_openFineExportProgressModal(tid, jobId)`
- 模态框显示 5 行信息（状态/进度/已用/速度/ETA）+ 进度条
- `setInterval(_poll, 1500)` 轮询
- 终态 → 清 interval + 切换按钮（取消 → 下载/关闭）

### 2. CSS

新增 `.slirn-fine-progress-*` 类（6 个）：
- `.slirn-fine-progress-card` — 卡片宽度 460px
- `.slirn-fine-progress-body` — flex column gap 10
- `.slirn-fine-progress-row` — flex space-between
- `.slirn-fine-progress-label` / `.slirn-fine-progress-val` — 文案样式
- `.slirn-fine-progress-bar` / `.slirn-fine-progress-fill` — 8px 高 + accent 色

## 关键文件

| 文件 | 改动 |
|---|---|
| [slirn_home/app.py](slirn_home/app.py) | + ~180 行：`_RenderJob` / `_JOB_REGISTRY` / `_assemble_fine_filter` / `_run_fine_render_async` / 3 个新端点 |
| [slirn_home/static/router.js:5325](slirn_home/static/router.js#L5325) | + ~150 行：替换 fine-export handler + 进度模态框函数 + 工具函数 `_fmtMs` / `_fmtSec` |
| [slirn_home/static/home.css](slirn_home/static/home.css) | + ~20 行：`.slirn-fine-progress-*` |
| [tests/test_workbench.py](tests/test_workbench.py) | + ~80 行：4 个新测试 |

## 测试

| 测试 | 验证 |
|---|---|
| `test_export_fine_video_returns_job_id_immediately` | POST 后 <0.5s 返回 job_id，不阻塞 |
| `test_render_status_reports_progress` | GET render_status 在 job 完成后返 state=done + output_url |
| `test_cancel_render_sends_sigterm` | POST cancel_render 后 proc.poll() 非 None |
| `test_render_status_returns_404_for_unknown_job` | GET unknown job_id → _err |

## 风险与边界

| 风险 | 缓解 |
|---|---|
| 进程重启 job 丢失 | 用户重试；持久化不在本需求范围 |
| 多 worker 部署下 registry 不共享 | 当前是单进程 Gradio/FastAPI；多 worker 需外置 Redis |
| ffmpeg `-progress` 输出未 flush | `bufsize=1` + Python `text=True` 行缓冲 |
| `out_time_ms` 可能比 `total_duration_ms` 大（编码器预读） | `min(100, ...)` 钳住 |
| 用户关浏览器后 job 仍跑 | daemon thread + 5 分钟 TTL，新任务可覆盖旧任务 |
| SIGTERM 后 ffmpeg 残留 .mp4 半成品 | 后台线程在 exit 时清理（输出文件不存在时补 unlink） |
| `time.time()` 在 Windows 精度差 | 用 `time.monotonic()` 算 elapsed；`time.time()` 仅用于 timestamp 显示 |
