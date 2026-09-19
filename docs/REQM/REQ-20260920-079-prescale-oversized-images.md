# REQ-20260920-079 精剪·导出最终视频 → bg/cover 超大图自动缩放（防 ffmpeg OOM 卡死）

## 背景

用户实测任务 `20260918-022`「导出最终视频」卡 4.5 分钟仍 0% 进度。后端 `render_status` 暴露：

- `elapsed_sec=270.9`、`progress_pct=0.0`、`speed_x=0.0`、`state=running`
- 49 线程 ffmpeg 进程，CPU 仅 0.375s / 270s 墙钟（几乎不动）
- stderr 末尾仅显示 `Stream mapping`（卡在 filter_complex init）

根因：任务 bg / cover 用了两张 **8000×4500 RGBA PNG**（每帧 ~144MB）。ffmpeg 必须先把它们完整解码到内存，再 scale 到 1920×1080 输出。两张 144MB/frame PNG 同时进入 filter_complex，与 crop + overlay + subtitles + amix 链叠加 → 49 线程全在内存分配/拷贝 thrash → 几乎不动。

**REQ-077 修复的 stdout TextIOWrapper 缓冲对此无效** —— ffmpeg 没在跑编码，在卡 init。

## 用户诉求

| 诉求 | 处理 |
|---|---|
| 渲染时把超大图片自动缩放 | ✅ 跟随 `fc.output.resolution`（720p→1280×720 / 1080p→1920×1080 / source→1920×1080） |
| 不修改用户上传的原图 | ✅ 源图保存在 `tasks/<tid>/upload/`，原图 mtime / size 永远不变；渲染时 PIL 写隐藏临时文件 |
| 上传时提醒 | ✅ 检测 > 4096px 长边的图片，响应体加 `warning` 字段；前端 toast「💡 自动缩放」 |

## 验收标准

| # | 描述 |
|---|---|
| AC-1 | 上传 8000×4500 PNG 作为 bg → 前端 toast 显示「💡 合成视频时此图将自动缩放至 1920×1080；源文件保留原图」 |
| AC-2 | 原图保存在 `tasks/<tid>/upload/` 不被改动（mtime / size / 内容字节不变） |
| AC-3 | 点「💾 导出最终视频」→ 后端检测 8000×4500 → PIL 缩放至 1920×1080 → 写临时文件 → input 替换 |
| AC-4 | 临时文件路径 `.<label>_req079_<stem>_<W>x<H><.ext>` 与源同目录（隐藏文件，不进 fc 持久化） |
| AC-5 | ffmpeg 在 5-15 秒内输出 `out_time_ms=`（PIL 解码比 ffmpeg 内置 png_decoder 快 5-10 倍） |
| AC-6 | 渲染成功后临时文件被 unlink（finally 块） |
| AC-7 | 渲染失败 / 用户取消 → 临时文件被 unlink（finally 块兜底） |
| AC-8 | PIL 抛异常 → log.warning + 用原图继续渲染（不抛 500） |
| AC-9 | `fc.output.resolution="720p"` 缩放到 1280×720；`"1080p"` 1920×1080；`"source"` 1920×1080 |
| AC-10 | 所有现有测试 + 新增 4 个测试全过（546 → 550 passed） |

## 不做的事

- ❌ 不改 fc 持久化格式（materials bg/cover/reference path 仍指向原图）
- ❌ 不改上传接口主流程（只加可选 warning 字段，不强制、不拒绝）
- ❌ 不预缩 video 流（video 已过 crop+scale，不需要预缩；且 video 解码器效率高）
- ❌ 不解决 `"source"` silently → 1080p 的隐性 bug（[DESIGN §已知未修问题](#) 已记入未来 REQ-080）
- ❌ 不支持视频源的预缩（如有 8K video 需求单开 REQ）

## 关联

- 上游 REQ：[REQ-20260919-074 异步渲染 + Job 注册表](docs/REQM/)（沿用 `_run_fine_render_async` + `_JOB_REGISTRY` + cleanup pattern）
- 上游 REQ：[REQ-20260920-077 inline 进度状态元素](docs/REQM/REQ-20260920-077-fine-export-inline-progress.md)（本次失败在 inline 状态机里表现为 ❌ 失败；修复后变 ⏳ 渲染中 → ✅ 已完成）
- 上游 REQ：[REQ-20260919-063 RGBA 透明区黑底合成](docs/REQM/REQ-20260919-063.md)（缩放后透明区行为对齐：保留 RGBA mode + 透明 canvas）

## 失败案例（已记录于用户实测）

| 字段 | 值 |
|---|---|
| 任务 ID | 20260918-022 |
| 视频时长 | 1430.6 秒（23.8 分钟） |
| job_id | job_1789838680040_33776 |
| ffmpeg PID | 28800（49 线程，CPU 0.375s / 270s 墙钟） |
| bg 图尺寸 | 8000×4500 RGBA PNG（2.6 MB 压缩） |
| cover 图尺寸 | 8000×4500 RGBA PNG（5.0 MB 压缩） |
| 错误信号 | `state="failed"` `error="Stream mapping: ..."`（filter_complex init 卡死） |