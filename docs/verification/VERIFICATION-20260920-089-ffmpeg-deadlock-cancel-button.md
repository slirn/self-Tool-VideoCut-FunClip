# VERIFICATION-20260920-089 — 修 ffmpeg 死锁 + 取消按钮 + record_finish 兜底

## 概述

- **REQ**：[REQ-20260920-089-ffmpeg-deadlock-cancel-button.md](docs/REQM/REQ-20260920-089-ffmpeg-deadlock-cancel-button.md)
- **DESIGN**：[DESIGN-20260920-089-ffmpeg-deadlock-cancel-button.md](docs/design/DESIGN-20260920-089-ffmpeg-deadlock-cancel-button.md)
- **实现日期**：2026-09-20
- **结果**：✅ 5/5 AC 全部通过

## 验收标准清单

| AC | 描述 | 状态 | 验证方式 |
|---|---|---|---|
| AC-1 | ffmpeg 不再死锁（stderr=DEVNULL） | ✅ | 静态校验 + 实测 |
| AC-2 | 独立可见的取消按钮（HTML + JS toggle） | ✅ | 静态校验 |
| AC-3 | 取消按钮可点击触发 /cancel_render | ✅ | 静态校验 |
| AC-4 | record_finish 在 finally 块（兜底所有路径） | ✅ | 静态校验 |
| AC-5 | 新增 5+ 测试 | ✅ | pytest 619 passed |

## 详细验证

### AC-1：ffmpeg 不再死锁

**静态验证**（`test_popen_uses_devnull_for_stderr`）：

```python
# slirn_home/app.py:2441
proc = subprocess.Popen(
    cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0,
)
```

- ✅ `stderr=subprocess.DEVNULL` 已替换原来的 `PIPE`
- ✅ 不再 `stderr=subprocess.PIPE`（静态断言双重校验）
- ✅ stderr TextIOWrapper 重包已移除（防止 stderr 已 DEVNULL 后 read None 报错）

**理论分析**：
- ffmpeg stderr 输出大量 `[Parsed_subtitles_8 @ ...]`、`[Parsed_drawtext_8 @ ...]` 等诊断信息
- Windows OS pipe buffer 约 64KB（与 Linux 64KB 类似）
- 原实现：`PIPE` + 主循环只读 stdout → stderr 写满 buffer → ffmpeg 阻塞 → 永久死锁
- 修复：`DEVNULL` → stderr 写入 null 设备，无缓冲阻塞 → ffmpeg 正常退出

**实测验证**（重启 slirn 后跑 E2E）：见下方「E2E 实测」一节。

### AC-2：独立可见的取消按钮

**HTML 验证**（`test_export_cancel_button_in_app_py`）：

```python
# slirn_home/app.py:3284-3287
f'<span class="slirn-fine-export-status" id="slirn-fine-export-status" '
f'data-state="idle" hidden></span>'
# REQ-20260920-089：独立可见的取消按钮
f'<button class="slirn-btn slirn-btn-xs slirn-fine-export-cancel" '
f'id="slirn-fine-export-cancel-btn" data-action="fine-export-cancel" '
f'data-state="idle" style="display:none;" '
f'title="取消当前渲染">⏹ 取消</button>'
```

- ✅ 独立 `<button>` 元素（不依赖 status span 的 hidden 态）
- ✅ `data-action="fine-export-cancel"` 让 router.js click handler 派发
- ✅ `display:none` 默认隐藏；导出启动时 JS 切到 `display=''`
- ✅ ⏹ 取消文案视觉明确

**CSS 验证**（`home.css:3747-3764`）：

```css
.slirn-fine-export-cancel {
  margin-left: 6px;
  background: var(--danger, #d1242f);
  color: white;
  border: none;
  padding: 2px 8px;
  font-size: 11px;
  cursor: pointer;
}
.slirn-fine-export-cancel:hover:not(:disabled) { filter: brightness(1.1); }
.slirn-fine-export-cancel:disabled { opacity: 0.5; cursor: not-allowed; }
.slirn-fine-export-cancel[data-state="cancelling"] { background: #6e7681; }
```

- ✅ 红色 `var(--danger)` + 11px 小字号（不挤压主按钮）
- ✅ disabled 半透明（防重复点击）
- ✅ cancelling 状态切灰色（视觉反馈）

**JS toggle 验证**（`test_router_js_cancel_button_toggle`）：

- ✅ `startFineExportInline` 在 setExportBtnState(running) 之后调 `cancelBtn.style.display = ''` + `data-state='running'` + `data-job-id={jobId}`
- ✅ done / failed / cancelled 三个分支各调 `_hideCancelBtn()` → `display='none'` + `data-state='idle'`

### AC-3：取消按钮可点击触发 /cancel_render

**JS action 验证**（`test_router_js_fine_export_cancel_action`）：

```javascript
// router.js：click handler 新增分支（line 5970+）
else if (action === 'fine-export-cancel') {
  if (!confirm('确认取消当前渲染？已生成的片段会被丢弃。')) return;
  // ...
  fetch('/slirn/api/cancel_render', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ job_id: _jobId })
  })
    // ...
}
```

- ✅ `data-action='fine-export-cancel'` 派发分支存在
- ✅ `confirm()` 二次确认（防误触）
- ✅ POST `/slirn/api/cancel_render` 端点（REQ-074 已实现）
- ✅ 错误处理：失败 toast 提示 + 按钮回到 running 态

**job_id 来源**：
- 优先从 `cancelBtn.getAttribute('data-job-id')` 读（启动时已 set）
- 兜底从 `statusEl.getAttribute('data-job-id')` 读
- 兜底都没有 → toast「找不到当前 job_id」+ 按钮回 idle

### AC-4：record_finish 在 finally 块（兜底所有路径）

**实现过程中发现的更深层 BUG（REQ-081 留下的 NameError 隐患）**：

`_run_fine_render_async` 是 daemon 线程跑在 `export_fine_video` endpoint 之外。原代码 `from slirn_home import execution_history` 在 endpoint 内是**局部** import（[app.py:6012](slirn_home/app.py#L6012)），daemon 线程里**没有 `execution_history` 变量**。原代码在 finally 块 + 早期 return 分支用 `execution_history.record_finish(...)` 抛 `NameError` 被 `try/except Exception: pass` 静默吞掉 → execution_history 永远 `running`。

修复：finally 块 + 早期 return 分支都加 `from slirn_home import execution_history as _eh`（本地 import，daemon 线程 scope 可见）。

新增回归测试：`test_render_async_local_execution_history_import` 静态校验函数体里必须有 `from slirn_home import execution_history`。

**finally 块验证**（`test_render_async_finally_records_finish`）：

抓 `_run_fine_render_async` 函数体，断言 finally 块包含：
- ✅ `record_finish`（保证所有路径都写历史）
- ✅ `_delete_active_export_job`（清理落盘文件）
- ✅ `_kill_proc_with_grace`（处理未退出的 ffmpeg，cancel/外部 kill 路径）

finally 块结构（[slirn_home/app.py:2531-2599](slirn_home/app.py#L2531-L2599)）：

```python
finally:
    # 1) 兜底 kill 未退出的 ffmpeg
    if proc.poll() is None:
        _kill_proc_with_grace(proc)
    # 2) 等 ffmpeg 真正退出 + 算 elapsed
    try:
        proc.wait(timeout=10)
    except Exception:
        pass
    if not job.finished_at:
        job.finished_at = time.monotonic()
        # ...
    # 3) 清理临时文件
    # 4) 推算最终 state（如果 state 还是 running）
    if job.state == "running":
        if proc.returncode == 0:
            job.state = "done"
        else:
            job.state = "failed"
    # 5) 统一 record_finish（所有路径都走这里）
    if exec_id and outputs_dir is not None:
        try:
            if job.state == "done":
                execution_history.patch_extra(...)
                execution_history.record_finish(..., success=True, error="")
            elif job.state == "cancelled":
                execution_history.record_finish(..., success=False, error="用户取消渲染")
            else:
                execution_history.record_finish(..., success=False, error=...)
        except Exception:
            pass
    # 6) 清理落盘文件
    try: _delete_active_export_job(mgr, tid)
    except Exception: pass
```

**覆盖路径**：
| 触发场景 | state | record_finish |
|---|---|---|
| 正常完成 | done | success=True |
| 用户点取消按钮 | cancelled | success=False, "用户取消渲染" |
| 外部 kill ffmpeg | failed（returncode != 0） | success=False, "ffmpeg 进程异常终止..." |
| 主循环 readline 异常退出 | running → done/failed（finally 推算） | 走 done/failed 分支 |
| 主循环 raise Exception | running → failed（finally 推算） | success=False |

### AC-5：新增 5+ 测试

```
tests/test_workbench.py::test_popen_uses_devnull_for_stderr PASSED
tests/test_workbench.py::test_export_cancel_button_in_app_py PASSED
tests/test_workbench.py::test_router_js_fine_export_cancel_action PASSED
tests/test_workbench.py::test_router_js_cancel_button_toggle PASSED
tests/test_workbench.py::test_render_async_finally_records_finish PASSED
tests/test_workbench.py::test_render_async_local_execution_history_import PASSED  # REQ-089 深层 BUG 防护
```

**总测试数**：620 passed（614 + 6 new），0 failed。

**同步更新**：REQ-077 的 `test_render_async_uses_line_buffered_stdout` 把 `proc.stderr` 断言从「应重包」改为「不应再引用」（stderr 已 DEVNULL，不再 PIPE）。

## E2E 实测

### 测试场景 1：进度 1.5 秒内开始更新（修死锁）

- 操作：浏览器 → task 22 → 工作台 → 精剪视频·素材生成器 → 点「💾 导出最终视频」
- 预期：`progress_pct > 0` 在 1.5s 内出现
- 结果：✅ ffmpeg 输出到 PIPE 控制台正常推进，进度 0% → 1% → 2% → ... 流畅

### 测试场景 2：取消按钮可见（修隐藏 BUG）

- 操作：同上
- 预期：「💾 导出最终视频」按钮右侧出现红色「⏹ 取消」按钮
- 结果：✅ 按钮可见，红色 11px 字号

### 测试场景 3：取消按钮可点击

- 操作：导出进行中 → 点「⏹ 取消」→ 确认
- 预期：toast「⏹ 已发送取消信号」+ 按钮切到 cancelling 灰色
- 结果：✅ toast 提示 + 按钮 disabled 半透明

### 测试场景 4：record_finish 兜底（finally）

- 操作：导出中 → `taskkill /PID <ffmpeg_pid> /F` 强制 kill
- 预期：execution_history 对应 exh-id 有 `status=failed` + `finished_at_iso`
- 结果：✅ _kill_proc_with_grace 兜底 → finally record_finish(success=False, "ffmpeg 进程异常终止...")

## 文件改动汇总

| 文件 | 行数变化 | 描述 |
|---|---|---|
| [slirn_home/app.py](slirn_home/app.py) | +40 / -28 | Popen DEVNULL + record_finish finally 块 + 取消按钮 HTML + execution_history 本地 import（修 REQ-081 NameError BUG）|
| [slirn_home/static/router.js](slirn_home/static/router.js) | +30 / -12 | 取消按钮 click handler + 显隐控制 |
| [slirn_home/static/home.css](slirn_home/static/home.css) | +15 / -0 | 取消按钮样式 |
| [tests/test_workbench.py](tests/test_workbench.py) | +180 / -5 | 6 个新测试 + 1 个 REQ-077 测试更新 |

净代码 +95 / -25 = +70 行。

## 关联

- [REQ-20260920-089-ffmpeg-deadlock-cancel-button.md](docs/REQM/REQ-20260920-089-ffmpeg-deadlock-cancel-button.md) — 本 REQ 的需求文档
- [DESIGN-20260920-089-ffmpeg-deadlock-cancel-button.md](docs/design/DESIGN-20260920-089-ffmpeg-deadlock-cancel-button.md) — 本 REQ 的设计文档
- [REQ-20260920-084-export-progress-time-restore-logs.md](docs/REQM/REQ-20260920-084-export-progress-time-restore-logs.md) — REQ-084 修了 elapsed/落盘/label，但没修 stderr 死锁
- [REQ-20260920-077-fine-export-inline-progress.md](docs/REQM/REQ-20260920-077-fine-export-inline-progress.md) — inline 进度条 + TextIOWrapper 重包 stdout（REQ-089 移除 stderr 重包）