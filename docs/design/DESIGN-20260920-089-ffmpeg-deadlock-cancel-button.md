# DESIGN-20260920-089 — 修 ffmpeg 死锁 + 取消按钮 + record_finish 兜底

## Context

用户在「💾 导出最终视频」后看到：

1. **进度卡 0% 长达 6 分多钟** —— 实际 ffmpeg 进程已死锁
2. **没有可见的取消按钮** —— 取消依赖 hidden status 元素 click

实测验证（[REQ-20260920-089](docs/REQM/REQ-20260920-089-ffmpeg-deadlock-cancel-button.md) 现象表）：

- ffmpeg 进程 PID 30152 启于 15:57:53，已跑 412s，**累计 CPU 时间 0.1 秒**（实质空闲）
- Threads=1（应该 >1）、`progress_pct=0.0` 永远不动、`fine_export.mp4` size=0
- execution_history 显示 exh-...20078f 永远 `running`，无 `finished_at`

### 根因（已确认）

| # | 现象 | 根因 |
|---|------|------|
| 1 | ffmpeg 卡死 | `_run_fine_render_async` Popen 同时 PIPE stdout+stderr，但主循环只读 stdout。ffmpeg stderr 写满 OS pipe buffer (~64KB Windows) → ffmpeg 阻塞等 stderr 排空 → 永久死锁 |
| 2 | 进度永远 0% | ffmpeg stdout 写到 `out_time_ms=` 行就被死锁卡住，主循环永不读到后续行 |
| 3 | 无独立取消按钮 | UI 设计是「点 status 元素本身」取消（[router.js:3639](slirn_home/static/router.js#L3639)），但 status 元素初始 `hidden`（[app.py:3284](slirn_home/app.py#L3284)），用户感知不到 |
| 4 | `record_finish` 漏写 | 外部 kill（force stop process）后 daemon 线程已退出 → 主循环不执行到任何 record_finish 分支 → execution_history 永远 running |

## 设计决策

| 维度 | 决策 | 理由 |
|---|---|---|
| 修死锁 | **`stderr=subprocess.DEVNULL`**（不再 PIPE） | ffmpeg stderr 90% 是 `[Parsed_xxx] Shaper: ...` 这类诊断信息；前端只用 `stderr_tail` 末尾 500 字符做错误展示（用户感知差）。砍掉 stderr 消费 = 砍掉死锁 |
| 取消按钮 | **独立 `<button>`** 而不是隐藏的 `<span>` 依赖 click handler | 视觉明确；带 `data-action="fine-export-cancel"`；不依赖前端 init 顺序 |
| record_finish 兜底 | **daemon 线程用 try/finally 包裹 record_finish**（失败/取消/外部 kill 都走 finally） | finally 一定会跑；保证 execution_history 永远有终态 |
| 后端兼容 | `/render_status` 返回字段不变（state/elapsed/progress_pct），UI 仅修前端入口 | 前端 status 元素保留（兜底） |

## 实施步骤

### Step 1：修 ffmpeg 死锁（核心修复）

修改 [slirn_home/app.py:2434](slirn_home/app.py#L2434) `_run_fine_render_async` Popen 调用：

```python
# 旧：
proc = subprocess.Popen(
    cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
)

# 新（REQ-20260920-089：解决 stderr PIPE 死锁）：
proc = subprocess.Popen(
    cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0,
)
```

同步移除 [line 2474-2478](slirn_home/app.py#L2474-L2478) 的 stderr TextIOWrapper 重包（stderr 已经是 DEVNULL）。

保留 [line 2586](slirn_home/app.py#L2586) `proc.stderr.read()` 的兜底前需加 `if proc.stderr is not None` —— DEVNULL 不会出错，但保险起见加 None check。

理由：
- ffmpeg stderr 是诊断信息（`[Parsed_xxx] ...`），前端只用 `stderr_tail` 末尾 500 字符做错误展示（用户感知差）
- 改成 DEVNULL → 进度 100% 实时推，UI 不再假死
- 单行改动 + 风险低

### Step 2：加独立取消按钮

修改 [slirn_home/app.py:3283](slirn_home/app.py#L3283) 的 status 元素区域：

旧：
```python
f'<span class="slirn-fine-export-status" id="slirn-fine-export-status" '
f'data-state="idle" hidden></span>'
```

新：保留 `<span>`（不破坏现有 JS），紧跟其后加独立按钮：
```python
f'<button class="slirn-btn slirn-btn-xs slirn-fine-export-cancel" '
f'id="slirn-fine-export-cancel-btn" data-action="fine-export-cancel" '
f'data-state="idle" style="display:none;" '
f'title="取消当前渲染">⏹ 取消</button>'
```

按钮 `id` = `slirn-fine-export-cancel-btn`，便于 JS 显示/隐藏。

### Step 3：前端点击取消按钮 + 显隐控制

修改 [slirn_home/static/router.js:3584](slirn_home/static/router.js#L3584) `startFineExportInline`：

- 在 `setExportBtnState(btnEl, 'running')` 之后，`cancelBtn.style.display = ''` 显示按钮 + `data-state='running'` + `data-job-id={jobId}` + `disabled=false`
- 在 done / failed / cancelled 三个分支各加 `_hideCancelBtn()` 隐藏按钮

加新 action dispatch 分支（在 router.js click handler 里）：
```javascript
else if (action === 'fine-export-cancel') {
  if (!confirm('确认取消当前渲染？已生成的片段会被丢弃。')) return;
  var _ceBtn = document.getElementById('slirn-fine-export-cancel-btn');
  if (_ceBtn) { _ceBtn.disabled = true; _ceBtn.setAttribute('data-state', 'cancelling'); }
  // 从 status 元素或取消按钮的 data-job-id 读 job_id
  var _statusEl = document.getElementById('slirn-fine-export-status');
  var _jobId = (_ceBtn && _ceBtn.getAttribute('data-job-id')) ||
               (_statusEl && _statusEl.getAttribute('data-job-id')) || '';
  if (!_jobId) {
    toast('❌ 找不到当前 job_id（可能已完成或已取消）');
    if (_ceBtn) { _ceBtn.disabled = false; _ceBtn.setAttribute('data-state', 'idle'); }
    return;
  }
  fetch('/slirn/api/cancel_render', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ job_id: _jobId })
  })
    .then(function(r) { return r.json(); })
    .then(function(j) {
      if (!j.ok) {
        toast('❌ ' + (j.error || '取消失败'));
        if (_ceBtn) { _ceBtn.disabled = false; _ceBtn.setAttribute('data-state', 'running'); }
      } else {
        toast('⏹ 已发送取消信号');
      }
    })
    .catch(function(e) {
      toast('❌ 网络错误: ' + e.message);
      if (_ceBtn) { _ceBtn.disabled = false; _ceBtn.setAttribute('data-state', 'running'); }
    });
}
```

### Step 4：record_finish 兜底（finally 块）

修改 [slirn_home/app.py:2426](slirn_home/app.py#L2426) `_run_fine_render_async`：

把 record_finish 调用统一挪到 `finally` 块，根据 `job.state` 决定 success/error：

```python
try:
    # ... 主循环 ...
finally:
    # REQ-20260920-089：finally 块统一处理所有终态（cancel / done / failed /
    # 外部 kill / 异常），保证 record_finish 一定被调用，不再漏写。
    # 1) 兜底 kill 未退出的 ffmpeg（cancel / 外部 kill）
    if proc.poll() is None:
        _kill_proc_with_grace(proc)

    # 2) 等 ffmpeg 真正退出 + 算 elapsed（如果还没设）
    try:
        proc.wait(timeout=10)
    except Exception:
        pass
    if not job.finished_at:
        job.finished_at = time.monotonic()
        job.wall_finished_at = time.time()
    job.elapsed_sec = job.finished_at - job.started_at

    # 3) 清理临时文件
    if sub_input_tmp:
        try: sub_input_tmp.unlink(missing_ok=True)
        except Exception: pass
    for _p in image_tmp_paths:
        try: _p.unlink(missing_ok=True)
        except Exception: pass

    # 4) 推算最终 state（主循环因 readline 返回空自然退出但 state 还是 running）
    if job.state == "running":
        if proc.returncode == 0:
            job.state = "done"
            job.progress_pct = 100.0
            job.output_url = (
                f"/slirn/api/video/{tid}?src=fine_export&t={int(time.time())}"
            )
        else:
            job.state = "failed"
            if not job.error:
                job.error = f"ffmpeg 进程异常终止（exit code {proc.returncode}）"

    # 5) 统一 record_finish（所有路径都走这里）
    if exec_id and outputs_dir is not None:
        try:
            if job.state == "done":
                execution_history.patch_extra(
                    outputs_dir, exec_id,
                    {"output_path": str(out_path),
                     "duration_sec": round((job.finished_at - job.started_at), 1),
                     "resolution": (asm.get("output_resolution") or "1080p")},
                )
                execution_history.record_finish(
                    outputs_dir, exec_id, success=True, error="",
                )
            elif job.state == "cancelled":
                execution_history.record_finish(
                    outputs_dir, exec_id, success=False,
                    error="用户取消渲染",
                )
            else:
                # failed / 外部 kill / 异常
                err_msg = job.error or "ffmpeg 进程异常终止（可能被外部 kill）"
                execution_history.record_finish(
                    outputs_dir, exec_id, success=False,
                    error=str(err_msg)[:500],
                )
        except Exception:
            pass

    # 6) 清理落盘文件（无论哪种终态都删）
    try: _delete_active_export_job(mgr, tid)
    except Exception: pass
```

**同步删除** cancel 分支和 failed 分支的 record_finish + _delete_active_export_job（统一到 finally）。

注意：`returncode == 0` 的成功判断要在 finally 之前；state 设置要在 finally 之前。

### Step 5：测试

新增 5 个测试（`tests/test_workbench.py`）：

1. `test_popen_uses_devnull_for_stderr` — 静态校验 `_run_fine_render_async` 源码 `stderr=subprocess.DEVNULL`（不再用 PIPE）
2. `test_export_cancel_button_in_app_py` — app.py 必须含 `id="slirn-fine-export-cancel-btn"` + `data-action="fine-export-cancel"`
3. `test_router_js_fine_export_cancel_action` — router.js 必须有 `fine-export-cancel` action 分支
4. `test_router_js_cancel_button_toggle` — router.js `startFineExportInline` 必须在 done/failed/cancelled 时隐藏取消按钮
5. `test_render_async_finally_records_finish` — 抓 `_run_fine_render_async` 函数体，断言 finally 块含 record_finish + _delete_active_export_job + _kill_proc_with_grace

预期：614 + 5 = 619 passed。

**同步更新** `test_render_async_uses_line_buffered_stdout` 的 stderr 断言：从「应引用 proc.stderr」改为「不应引用 proc.stderr」（stderr 已 DEVNULL，不再 PIPE 也不再 read）。

### Step 6：UI CSS（home.css）

加 `.slirn-fine-export-cancel` 样式（红色小按钮 + disabled 半透明 + cancelling 灰色）。

## 关键文件改动汇总

| 文件 | 改动 | 行数 |
|---|---|---|
| [slirn_home/app.py](slirn_home/app.py) | Popen stderr=DEVNULL + record_finish finally 块 + 取消按钮 HTML | +35 / -25 |
| [slirn_home/static/router.js](slirn_home/static/router.js) | 取消按钮 click handler + 显隐控制 | +30 / -12 |
| [slirn_home/static/home.css](slirn_home/static/home.css) | 取消按钮样式 | +15 / -0 |
| [tests/test_workbench.py](tests/test_workbench.py) | 5 个新测试 + 1 个 REQ-077 测试更新 | +150 / -5 |
| `docs/REQM/...089...md` | REQ | +90 / -0 |
| `docs/verification/...089...md` | VERIFICATION | +80 / -0 |

净代码 +95 / -25 = +70 行。

## 风险与边界

| 风险 | 处理 |
|---|---|
| `DEVNULL` 改动后失去 stderr 错误信息 | record_finish 用 `proc.returncode` + `job.error` 兜底；用户可从 ffmpeg 输出文件路径手验 |
| finally 块的 record_finish 与现有 cancel/failed 分支的 record_finish 重复 | 删掉现有 2 处，统一在 finally |
| `_delete_active_export_job` 也挪 finally | 现有 2 处删除，统一在 finally |
| 测试 mock subprocess 难 | 用静态源码扫描 + 现有 mock 模式 |
| 取消按钮加在「💾 导出最终视频」按钮右侧可能挤压 | 用 `slirn-btn-xs` 小尺寸 + margin-left: 6px |

## 复用现有基础设施

- `_kill_proc_with_grace`（[app.py:2331](slirn_home/app.py#L2331)）—— 已有，finally 块复用
- `_delete_active_export_job`（REQ-084 已实现）—— finally 块复用
- `execution_history.record_finish` / `patch_extra`（[execution_history.py](slirn_home/execution_history.py)）—— 已有
- `/slirn/api/cancel_render`（[app.py:6137](slirn_home/app.py#L6137)）—— 不改，复用
- `_fmtSec` / `_stateLabel`（[router.js](slirn_home/static/router.js)）—— 已有

## Why

**subprocess PIPE 死锁是经典 BUG**：ffmpeg 输出大量 stderr 诊断信息，OS pipe buffer ~64KB 写满后 ffmpeg 阻塞，主循环只读 stdout → 永久死锁。用户在 UI 上看到「卡死 + 没按钮」，因为：
- 前端依赖 status 元素的 click → 但 status 元素初始 `hidden`，UI 上一开始啥都没显示
- 死锁时主循环不更新任何 job 字段 → UI 显示 stale 状态

修复核心：**砍 stderr PIPE（用 DEVNULL）+ finally record_finish + 独立可见的取消按钮**。3 个改动都很小，但用户感知差异巨大。

## How to apply

未来启动任何 subprocess 处理 ffmpeg / imagemagick / 其他多输出流进程：
1. **`stderr=DEVNULL`** 是默认选择（除非真的需要实时日志）
2. **必须并发读 stdout+stderr**（用线程），否则死锁
3. **`record_finish` 在 finally** —— 任何后台任务的落盘历史都要 try/finally 兜底
4. **独立可见的取消按钮** —— 别依赖「点 status 元素本身」的隐藏交互，用户感知差

## 关联

- [REQ-20260919-074-fine-export-async-progress.md](docs/REQM/REQ-20260919-074-fine-export-async-progress.md) — 异步导出 + `_JOB_REGISTRY` 基础设施
- [REQ-20260920-077-fine-export-inline-progress.md](docs/REQM/REQ-20260920-077-fine-export-inline-progress.md) — inline 进度条
- [REQ-20260920-084-export-progress-time-restore-logs.md](docs/REQM/REQ-20260920-084-export-progress-time-restore-logs.md) — REQ-084 修了 elapsed/落盘/label，但没修 stderr 死锁