# REQ-20260919-074 精剪·导出最终视频 → 异步后台任务 + 进度展示

## Context

精剪视频阶段「💾 导出最终视频」当前实现：

```python
# slirn_home/app.py:5111-5128
@app.app.post("/slirn/api/export_fine_video")
async def export_fine_video(body: dict):
    ...
    result = _run_fine_render(tid, mgr, out_path, duration=None)
    if not result.get("ok"):
        return result
    return _ok(url=f"/slirn/api/video/{tid}?src=fine_export...")
```

```python
# slirn_home/app.py:2046-2053
result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, ...)
except subprocess.TimeoutExpired:
    return {"ok": False, "error": "ffmpeg 渲染超时（>120s），请缩短视频或简化滤镜"}
```

**问题（用户反馈）**：

1. 实际最终视频**通常 1～3 小时**（不是 120 秒）
2. ffmpeg 渲染时间 = 视频时长 × 编码系数（libx264 fast preset 约 0.3-0.8×实时）
3. 当前 120 秒超时 → 1 小时视频渲染到 2 分钟就报错 `ffmpeg 渲染超时`
4. 即便去掉超时，`fetch()` 同步等 1-3 小时 → 浏览器卡死 / 用户没法知道进度 / 没法中途取消
5. 用户要求：**点击导出时显示执行时间**（实时计时 + 进度）

**根因**：渲染是 CPU 密集型长任务，放在 HTTP 同步响应里本质错误。

## 验收标准

- AC-1：「💾 导出最终视频」点击后**立即返回**（<1 秒），弹出进度模态框
- AC-2：进度模态框显示：
  - 状态：`排队中 / 渲染中 / 完成 / 失败 / 已取消`
  - 进度：`已渲染 MM:SS / 总时长 HH:MM:SS（XX%）`
  - 实时计时：`已用 HH:MM:SS`
  - 编码速度：`X.X×实时`（可选）
  - 预计剩余：`约 MM:SS 后完成`（按编码速度估算）
  - 操作按钮：`取消渲染`（仅在渲染中可见）
- AC-3：渲染成功后模态框显示 `✅ 已导出 outputs/fine_export.mp4` + `下载` 按钮 + 自动关闭按钮
- AC-4：渲染失败时显示错误信息（ffmpeg stderr 末尾 500 字符）
- AC-5：取消按钮调用 `POST /slirn/api/cancel_render`，ffmpeg 收到 SIGTERM 后清理
- AC-6：进度通过 `GET /slirn/api/render_status?job_id=X` 轮询（前端每 1.5 秒一次）
- AC-7：服务器侧 job 状态保留 5 分钟（完成后过期清理），便于前端最后一次查询拿到结果
- AC-8：**预览（≤30 秒）保持原同步行为**（120 秒足够，且用户能即时看到结果）—— 这次只改导出，预览不动
- AC-9：后端使用 ffmpeg `-progress pipe:1` 解析 `out_time_ms=...` / `total_size=...` / `progress=continue|end`
- AC-10：所有现有测试通过；新增 4 个测试覆盖 job 生命周期

## 方案：后台线程 + 轮询（推荐）

### 架构

```
前端                          FastAPI                    后台线程
  │                             │                          │
  │ POST /export_fine_video     │                          │
  │ ───────────────────────────>│                          │
  │                             │ 创建 job, 启线程          │
  │ <───────── {job_id} ────────│─────────────────────────>│
  │                             │                          │ ffmpeg -progress pipe:1
  │ GET /render_status?job=X    │                          │ ...编码中...
  │ ───────────────────────────>│ 读 job 状态               │
  │ <─ {state, pct, elap, ...} ─│<──── 更新 job.state ─────│
  │ (每 1.5 秒轮询)             │                          │
  │ GET /render_status?job=X    │                          │
  │ <─ {state: 'done', url} ────│<──── ffmpeg 退出 ────────│
  │ 显示下载按钮                  │                          │
```

### 1. Job 注册表（[slirn_home/app.py](slirn_home/app.py) 新增模块级单例）

```python
import threading
import time as _time
from dataclasses import dataclass, field

@dataclass
class _RenderJob:
    job_id: str
    task_id: str
    state: str = "queued"          # queued / running / done / failed / cancelled
    started_at: float = 0.0        # 线程启动时间
    finished_at: float = 0.0
    elapsed_sec: float = 0.0
    progress_pct: float = 0.0      # 0-100
    progress_time_ms: int = 0      # 当前已编码时间（来自 ffmpeg out_time_ms）
    total_duration_ms: int = 0     # 源视频总时长（启动时 ffprobe 拿）
    speed_x: float = 0.0           # ffmpeg speed=2.5x → 2.5
    eta_sec: float = -1.0          # 预计剩余秒数
    log_tail: list[str] = field(default_factory=list)
    error: str = ""
    output_url: str = ""
    proc: object = None            # subprocess.Popen, 用于 cancel

_JOB_REGISTRY: dict[str, _RenderJob] = {}
_JOB_LOCK = threading.Lock()
_JOB_TTL_SEC = 300  # 完成后保留 5 分钟，便于前端最后一次查询
```

### 2. 后台渲染函数（替换 `_run_fine_render` 的 export 路径）

新增 `_run_fine_render_async(job, tid, mgr, out_path)`：

```python
def _run_fine_render_async(job: _RenderJob, tid: str, mgr, out_path: Path):
    """后台线程执行 — 用 Popen + -progress pipe:1 实时解析。"""
    job.state = "running"
    job.started_at = _time.time()

    # 1. 组装 filter_complex + cmd（复用 _run_fine_render 的逻辑，但拆出来）
    filter_complex, input_args, sub_input_tmp = _assemble_fine_filter(tid, mgr, None, 0.0)
    if not filter_complex.get("ok"):
        job.state = "failed"
        job.error = filter_complex["error"]
        job.finished_at = _time.time()
        return

    # 2. ffprobe 拿总时长（用于计算百分比）
    job.total_duration_ms = _probe_video_duration_ms(mgr, tid) or 0

    # 3. ffmpeg Popen + -progress pipe:1
    cmd = ["ffmpeg", "-y", *input_args, "-filter_complex", filter_complex["filter_complex"],
           "-map", "[vout]", "-map", "[aout]", "-c:v", "libx264", "-preset", "fast", "-crf", "23",
           "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart",
           "-progress", "pipe:1", "-nostats", str(out_path)]

    job.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", bufsize=1)

    # 4. 解析 -progress 输出（key=value 格式，每段以 'progress=continue' 结束）
    last_log_flush = 0.0
    while True:
        line = job.proc.stdout.readline()
        if not line and job.proc.poll() is not None:
            break
        line = line.strip()
        if not line:
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        if key == "out_time_ms":
            try:
                job.progress_time_ms = int(val)
                if job.total_duration_ms > 0:
                    job.progress_pct = min(100.0,
                        job.progress_time_ms / job.total_duration_ms * 100)
            except ValueError:
                pass
        elif key == "speed":
            # "2.5x" → 2.5
            try:
                job.speed_x = float(val.rstrip("x"))
            except ValueError:
                pass
        elif key == "progress":
            pass  # continue / end 标记

        # 计算 elapsed + ETA（每 0.5 秒更新一次）
        now = _time.time()
        if now - last_log_flush > 0.5:
            last_log_flush = now
            job.elapsed_sec = now - job.started_at
            if job.speed_x > 0:
                remaining_ms = max(0, job.total_duration_ms - job.progress_time_ms)
                # speed_x 是「源时长 / 墙钟时长」；剩余墙钟 = 剩余源时长 / speed
                job.eta_sec = remaining_ms / 1000.0 / job.speed_x

    # 5. 等待退出 + 收集结果
    job.proc.wait()
    job.finished_at = _time.time()
    job.elapsed_sec = job.finished_at - job.started_at

    if job.proc.returncode == 0:
        job.state = "done"
        job.progress_pct = 100.0
        job.output_url = f"/slirn/api/video/{tid}?src=fine_export&t={int(_time.time())}"
    else:
        if job.state != "cancelled":
            job.state = "failed"
            job.error = (job.proc.stderr.read() or "")[-500:] if job.proc.stderr else "未知错误"

    # 6. 清理
    if sub_input_tmp:
        try: sub_input_tmp.unlink(missing_ok=True)
        except Exception: pass
```

### 3. 三个新端点（[slirn_home/app.py:5111](slirn_home/app.py#L5111) 附近）

```python
# REQ-20260919-074：导出异步化（1-3 小时视频不再超时）
@app.app.post("/slirn/api/export_fine_video")
async def export_fine_video(body: dict = Body(default_factory=dict)):
    tid = (body.get("task_id") or "").strip()
    if not tid:
        return _err("缺少 task_id")
    try:
        mgr.get(tid)
    except Exception as e:
        return _err(f"任务不存在: {e}")

    # 同一任务已有运行中的 job → 拒绝重启（避免并发写同一文件）
    for existing in _JOB_REGISTRY.values():
        if existing.task_id == tid and existing.state in ("queued", "running"):
            return _err(f"该任务已有运行中的渲染（job_id={existing.job_id}）")

    job_id = f"job_{int(_time.time() * 1000)}_{os.getpid()}"
    out_path = mgr.tasks_dir / tid / "outputs" / "fine_export.mp4"
    job = _RenderJob(job_id=job_id, task_id=tid)
    with _JOB_LOCK:
        _JOB_REGISTRY[job_id] = job
        # 清理过期的旧 job（>5 分钟）
        now = _time.time()
        for jid in list(_JOB_REGISTRY.keys()):
            j = _JOB_REGISTRY[jid]
            if j.finished_at and now - j.finished_at > _JOB_TTL_SEC:
                del _JOB_REGISTRY[jid]

    # 启动后台线程
    t = threading.Thread(
        target=_run_fine_render_async, args=(job, tid, mgr, out_path),
        daemon=True, name=f"fine-render-{job_id}",
    )
    t.start()

    return _ok(job_id=job_id, toast=f"🎬 导出已启动（{tid[:8]}），可在本卡片下方看进度")


@app.app.get("/slirn/api/render_status")
async def render_status(job_id: str):
    with _JOB_LOCK:
        job = _JOB_REGISTRY.get(job_id)
    if not job:
        return _err(f"job 不存在或已过期（>5 分钟）: {job_id}")
    return _ok(
        job_id=job.job_id,
        task_id=job.task_id,
        state=job.state,
        elapsed_sec=round(job.elapsed_sec, 1),
        progress_pct=round(job.progress_pct, 1),
        progress_time_ms=job.progress_time_ms,
        total_duration_ms=job.total_duration_ms,
        speed_x=round(job.speed_x, 2),
        eta_sec=round(job.eta_sec, 1) if job.eta_sec >= 0 else None,
        error=job.error,
        output_url=job.output_url,
    )


@app.app.post("/slirn/api/cancel_render")
async def cancel_render(body: dict = Body(default_factory=dict)):
    job_id = (body.get("job_id") or "").strip()
    if not job_id:
        return _err("缺少 job_id")
    with _JOB_LOCK:
        job = _JOB_REGISTRY.get(job_id)
    if not job:
        return _err(f"job 不存在: {job_id}")
    if job.state not in ("queued", "running"):
        return _err(f"job 已处于终态（{job.state}），无法取消")
    if job.proc:
        try:
            job.proc.terminate()  # SIGTERM
            job.state = "cancelled"
            job.finished_at = _time.time()
        except Exception as e:
            return _err(f"取消失败: {e}")
    return _ok(toast="⏹ 已发送取消信号")
```

### 4. 前端（[slirn_home/static/router.js:5325](slirn_home/static/router.js#L5325)）

替换 `action === 'fine-export'` 分支：

```javascript
else if (action === 'fine-export') {
  // REQ-20260919-074：异步导出（1-3 小时不再超时）
  var _b = target;
  if (_b.disabled) return;
  var _tid = _b.getAttribute('data-task-id') || '';
  _b.disabled = true;
  _b.textContent = '💾 启动导出...';

  fetch('/slirn/api/export_fine_video', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ task_id: _tid })
  })
    .then(r => r.json())
    .then(function(j) {
      _b.disabled = false; _b.textContent = '💾 导出最终视频';
      if (!j.ok) { toast(j.error || '启动失败', 'err'); return; }
      var _jobId = j.job_id;
      _openFineExportProgressModal(_tid, _jobId);
    })
    .catch(function(e) {
      _b.disabled = false; _b.textContent = '💾 导出最终视频';
      toast('启动导出失败: ' + e, 'err');
    });
}

function _openFineExportProgressModal(tid, jobId) {
  // 显示进度模态框 + 启动轮询
  var modal = document.getElementById('slirn-fine-export-progress');
  if (!modal) {
    // 首次创建
    modal = document.createElement('div');
    modal.id = 'slirn-fine-export-progress';
    modal.className = 'slirn-modal-overlay';
    modal.innerHTML = '<div class="slirn-modal-card slirn-fine-progress-card">' +
      '<div class="slirn-modal-title">🎬 导出精剪视频</div>' +
      '<div class="slirn-fine-progress-body">' +
        '<div class="slirn-fine-progress-row">' +
          '<span class="slirn-fine-progress-label">状态</span>' +
          '<span class="slirn-fine-progress-val" id="slirn-fine-progress-state">排队中...</span>' +
        '</div>' +
        '<div class="slirn-fine-progress-bar"><div class="slirn-fine-progress-fill" id="slirn-fine-progress-fill"></div></div>' +
        '<div class="slirn-fine-progress-row">' +
          '<span class="slirn-fine-progress-label">已渲染</span>' +
          '<span class="slirn-fine-progress-val" id="slirn-fine-progress-time">— / —</span>' +
        '</div>' +
        '<div class="slirn-fine-progress-row">' +
          '<span class="slirn-fine-progress-label">已用时</span>' +
          '<span class="slirn-fine-progress-val" id="slirn-fine-progress-elapsed">00:00</span>' +
        '</div>' +
        '<div class="slirn-fine-progress-row">' +
          '<span class="slirn-fine-progress-label">编码速度</span>' +
          '<span class="slirn-fine-progress-val" id="slirn-fine-progress-speed">—</span>' +
        '</div>' +
        '<div class="slirn-fine-progress-row">' +
          '<span class="slirn-fine-progress-label">预计剩余</span>' +
          '<span class="slirn-fine-progress-val" id="slirn-fine-progress-eta">—</span>' +
        '</div>' +
      '</div>' +
      '<div class="slirn-fine-progress-actions">' +
        '<button class="slirn-btn" data-action="fine-export-cancel" id="slirn-fine-export-cancel-btn">⏹ 取消渲染</button>' +
        '<button class="slirn-btn slirn-btn-primary" data-action="fine-export-close" id="slirn-fine-export-close-btn" hidden>关闭</button>' +
      '</div>' +
      '</div>';
    document.body.appendChild(modal);
  }
  modal.hidden = false;
  var _poll = function() {
    fetch('/slirn/api/render_status?job_id=' + encodeURIComponent(jobId))
      .then(r => r.json())
      .then(function(s) {
        if (!s.ok) { toast(s.error || '查询失败', 'err'); return; }
        var _state = s.state;
        document.getElementById('slirn-fine-progress-state').textContent =
          ({queued:'排队中', running:'渲染中', done:'已完成', failed:'失败', cancelled:'已取消'})[_state] || _state;
        document.getElementById('slirn-fine-progress-fill').style.width = s.progress_pct + '%';
        document.getElementById('slirn-fine-progress-time').textContent =
          _fmtMs(s.progress_time_ms) + ' / ' + _fmtMs(s.total_duration_ms) +
          ' (' + s.progress_pct.toFixed(1) + '%)';
        document.getElementById('slirn-fine-progress-elapsed').textContent = _fmtSec(s.elapsed_sec);
        document.getElementById('slirn-fine-progress-speed').textContent =
          s.speed_x > 0 ? s.speed_x.toFixed(2) + '×' : '—';
        document.getElementById('slirn-fine-progress-eta').textContent =
          s.eta_sec != null && s.eta_sec >= 0 ? '约 ' + _fmtSec(s.eta_sec) : '—';

        var _cancelBtn = document.getElementById('slirn-fine-export-cancel-btn');
        var _closeBtn = document.getElementById('slirn-fine-export-close-btn');
        if (_state === 'done') {
          _cancelBtn.hidden = true;
          _closeBtn.hidden = false;
          _closeBtn.textContent = '⬇️ 下载 + 关闭';
          _closeBtn.onclick = function() {
            window.open(s.output_url, '_blank');
            modal.hidden = true;
          };
          clearInterval(_timer);
        } else if (_state === 'failed') {
          _cancelBtn.hidden = true;
          _closeBtn.hidden = false;
          _closeBtn.textContent = '关闭';
          _closeBtn.onclick = function() { modal.hidden = true; };
          toast('❌ 渲染失败: ' + (s.error || '未知'), 'err');
          clearInterval(_timer);
        } else if (_state === 'cancelled') {
          _cancelBtn.hidden = true; _closeBtn.hidden = false;
          _closeBtn.textContent = '关闭';
          _closeBtn.onclick = function() { modal.hidden = true; };
          clearInterval(_timer);
        }
      });
  };
  _poll();
  var _timer = setInterval(_poll, 1500);

  // 取消按钮
  document.getElementById('slirn-fine-export-cancel-btn').onclick = function() {
    if (!confirm('确认取消当前渲染？')) return;
    fetch('/slirn/api/cancel_render', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ job_id: jobId })
    })
      .then(r => r.json())
      .then(function(j) {
        if (!j.ok) toast(j.error || '取消失败', 'err');
      });
  };
}

function _fmtMs(ms) {
  if (!ms || ms <= 0) return '00:00:00';
  var s = Math.floor(ms / 1000);
  var h = Math.floor(s / 3600);
  var m = Math.floor((s % 3600) / 60);
  var ss = s % 60;
  return (h < 10 ? '0' : '') + h + ':' + (m < 10 ? '0' : '') + m + ':' + (ss < 10 ? '0' : '') + ss;
}
function _fmtSec(sec) {
  if (!sec || sec < 0) return '00:00:00';
  var s = Math.floor(sec);
  var h = Math.floor(s / 3600);
  var m = Math.floor((s % 3600) / 60);
  var ss = s % 60;
  return (h < 10 ? '0' : '') + h + ':' + (m < 10 ? '0' : '') + m + ':' + (ss < 10 ? '0' : '') + ss;
}
```

### 5. CSS（[slirn_home/static/home.css](slirn_home/static/home.css) 新增）

```css
.slirn-fine-progress-card { min-width: 460px; }
.slirn-fine-progress-body { display: flex; flex-direction: column; gap: 10px; }
.slirn-fine-progress-row { display: flex; justify-content: space-between; align-items: center; font-size: 13px; }
.slirn-fine-progress-label { color: var(--text-muted); }
.slirn-fine-progress-val { color: var(--text-primary); font-weight: 600; font-variant-numeric: tabular-nums; }
.slirn-fine-progress-bar { height: 8px; background: rgba(128,128,128,0.15); border-radius: 4px; overflow: hidden; }
.slirn-fine-progress-fill { height: 100%; background: var(--accent-solid); width: 0; transition: width 0.4s ease; }
.slirn-fine-progress-actions { display: flex; gap: 10px; justify-content: center; margin-top: 14px; }
```

### 6. 关键文件

| 文件 | 改动 |
|---|---|
| [slirn_home/app.py](slirn_home/app.py) | 新增 `_RenderJob` / `_JOB_REGISTRY` / `_run_fine_render_async`；3 个新端点 |
| [slirn_home/static/router.js:5325](slirn_home/static/router.js#L5325) | 替换 fine-export handler 为异步 + 进度模态框 |
| [slirn_home/static/home.css](slirn_home/static/home.css) | 新增 `.slirn-fine-progress-*` 样式 |
| [tests/test_workbench.py](tests/test_workbench.py) | 4 个新测试 |

### 7. 测试

| 测试 | 验证 |
|---|---|
| `test_export_fine_video_returns_job_id_immediately` | POST 立即返回 job_id，不阻塞（<1s） |
| `test_render_status_returns_progress` | GET render_status 返 state + progress_pct + elapsed_sec |
| `test_cancel_render_sends_sigterm` | POST cancel_render → proc.terminate 被调用 |
| `test_render_status_404_after_ttl` | 完成 5 分钟后查询 → 404（清理逻辑） |

### 8. 不做的事

- ❌ 不改预览（render_fine_preview 仍走同步，30 秒内必完成）
- ❌ 不持久化 job 状态（进程重启 = job 丢失，符合预期；用户重试即可）
- ❌ 不做 WebSocket（轮询 1.5 秒足够；实现简单）
- ❌ 不做多 worker 队列（单进程 daemon thread 足够；如需扩展用 RQ/Celery）
- ❌ 不改 ffmpeg 参数（沿用 `-preset fast -crf 23`，已是 speed/quality 平衡点）
- ❌ 不动 `_run_fine_render` 同步路径（预览仍用），只新增 `_run_fine_render_async`

### 9. 风险与边界

| 风险 | 处理 |
|---|---|
| 进程重启时 job 丢失 | 接受（用户重试即可；持久化需 joblib 之类外部存储） |
| 同一任务并发启动 2 个 job | 启动时检查并拒绝 |
| ffmpeg 输出很大（-progress 1 秒 1 段） | 只读不存磁盘，内存累积即可 |
| SIGTERM 后 ffmpeg 不立即退出 | 加 5 秒后 SIGKILL 兜底（daemon thread 内） |
| `-progress pipe:1` 与 stderr 顺序乱 | ffmpeg 保证 stdout 全是 progress key=value；stderr 留给失败回看 |
| 用户关浏览器后 job 继续跑 | daemon thread + 注册表 5 分钟 TTL，job 自然跑完或被新任务覆盖 |

### 10. 真机验证

1. 重启 slirn → 进任务 A（1 小时视频）→ 精剪面板 → 点「💾 导出最终视频」
2. 立即看到进度模态框：`状态: 渲染中 / 已渲染 00:00:00 / 总 01:00:00 (0%) / 已用时 00:01 / 编码速度 0.85× / 预计剩余 约 01:10:00`
3. 进度条按 ffmpeg out_time_ms 增长
4. 跑约 30 秒后点「⏹ 取消渲染」→ 模态框变 `已取消`
5. 不取消 → 约 1 小时后模态框变 `已完成` + `⬇️ 下载` 按钮可点
6. 验证文件：`outputs/fine_export.mp4` 存在 + 长度 ≈ 源视频
