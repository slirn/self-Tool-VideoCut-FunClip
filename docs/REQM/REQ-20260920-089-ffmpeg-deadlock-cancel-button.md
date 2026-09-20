# REQ-20260920-089 — 修 ffmpeg 死锁 + 加独立取消按钮 + record_finish 兜底

## 背景

用户点击「💾 导出最终视频」后，UI 反馈异常：
- 「执行 6 分多钟，进度始终 0%」（实际是 ffmpeg 进程卡死）
- 「页面上没有取消按钮」（取消按钮依赖 hidden 的 status 元素 click 触发）

## 现象（实测 2026-09-20）

| 项 | 实测 |
|---|---|
| ffmpeg 进程 PID | 30152（启于 15:57:53）|
| 已运行时长 | 412 秒（6:52）|
| 累计 CPU 时间 | **0.1 秒**（实质空闲）|
| Threads | 1（应该 >1）|
| `progress_pct` | 0.0 永远不动 |
| `fine_export.mp4` size | 0（ffmpeg 没写出第一帧）|
| execution_history | exh-...20078f 永远 `running`，无 finished_at |

## 根因分析

| # | 现象 | 根因 |
|---|------|------|
| 1 | ffmpeg 卡死 6:52 | [`_run_fine_render_async`](slirn_home/app.py#L2434) `subprocess.Popen(..., stdout=PIPE, stderr=PIPE, ...)`，主循环 [line 2484](slirn_home/app.py#L2484) 只读 stdout；ffmpeg stderr 写满 OS pipe buffer (~64KB Windows) → ffmpeg 阻塞 → 死锁 |
| 2 | progress_pct 永远 0 | ffmpeg 死锁后 stdout 也不再有新行（ffmpeg 在等 stderr 排空）；主循环 `readline()` 阻塞 |
| 3 | 无取消按钮 | UI 设计是「点 status 元素本身」（[router.js:3639](slirn_home/static/router.js#L3639)），但元素初始 `hidden`（[app.py:3284](slirn_home/app.py#L3284)），用户看不到 |
| 4 | record_finish 漏写 | 外部 kill 后 daemon 线程退出，主循环不执行到 `record_finish` 分支 |

## 验收标准

### AC-1：ffmpeg 不再死锁 ✅ 待验证
`_run_fine_render_async` Popen 调用 `stderr=subprocess.DEVNULL`（不再 PIPE）。
进度 1.5 秒内开始更新（`progress_pct > 0`），CPU > 5%。

### AC-2：独立可见的取消按钮 ✅ 待验证
HTML 在 `_render_fine_cut_zone` 输出 `<button id="slirn-fine-export-cancel-btn" data-action="fine-export-cancel">⏹ 取消</button>`，默认 `display:none`。
导出进行时按钮显示；done/failed/cancelled 时按钮自动隐藏。

### AC-3：取消按钮可点击触发 /slirn/api/cancel_render ✅ 待验证
前端 click handler 调 `cancel_render` 端点 → 后端 SIGTERM → ffmpeg 退出 → 主循环退出 → record_finish 写历史。

### AC-4：record_finish 在 finally 块（兜底所有路径） ✅ 待验证
无论正常退出 / cancel / 外部 kill / 异常，`finally` 块保证 `execution_history.record_finish` 一定被调用。

### AC-5：新增 5+ 测试 ✅ 待验证
- Popen stderr=DEVNULL 静态校验
- 取消按钮 HTML 存在
- 取消 action dispatch 分支
- done/failed/cancelled 状态隐藏按钮
- record_finish finally 兜底

## Why

subprocess PIPE 死锁是经典 BUG：ffmpeg 输出大量 stderr 诊断信息，OS pipe buffer 写满后 ffmpeg 阻塞，主循环只读 stdout → 永久死锁。UI 上看不到取消按钮是 design BUG（依赖 hidden 元素的 click）。

## How to apply

未来启动任何 subprocess 处理 ffmpeg / imagemagick / 其他多输出流进程：
1. **`stderr=DEVNULL`** 是默认选择（除非真的需要实时日志）
2. **必须并发读 stdout+stderr**（用线程），否则死锁
3. **`record_finish` 在 finally** —— 任何后台任务的落盘历史都要 try/finally 兜底
4. **独立可见的取消按钮** —— 别依赖「点 status 元素本身」的隐藏交互

## 关联

- [REQ-20260919-074-fine-export-async-progress.md](docs/REQM/REQ-20260919-074-fine-export-async-progress.md) — 异步导出基础设施
- [REQ-20260920-077-fine-export-inline-progress.md](docs/REQM/REQ-20260920-077-fine-export-inline-progress.md) — inline 进度条
- [REQ-20260920-084-export-progress-time-restore-logs.md](docs/REQM/REQ-20260920-084-export-progress-time-restore-logs.md) — REQ-084 修了 elapsed/落盘，但没修死锁
