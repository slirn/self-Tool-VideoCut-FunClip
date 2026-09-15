# DESIGN-20260915-001 — 字幕生成服务 + 详情页处理区

## 1. 架构决策

### D1: ASR 入口复用上游 `VideoClipper.video_recog`，不复制识别逻辑
- `funclip/videoclipper.py:229` `video_recog(video_filename, sd_switch, hotwords, output_dir)`
  → `(res_text, res_srt, state)`，`state['sentences']` 每项 `{text, timestamp: [[ms,ms],...]}`
- 模型构建照抄本项目 skill `video-subtitle-extractor/scripts/extract_subtitle.py` 的
  paraformer 分支（seaco-paraformer + fsmn-vad + punc，全部已离线缓存于
  `~/.cache/modelscope`）。**seaco 版是热词优化版**，`generate(hotword="空格分隔")` 原生支持。
- 理由：与 skill 管线同源，行为一致；不碰上游代码（CLAUDE.md 红线）。

### D2: 服务放 `slirn_home/asr_service.py`（app 层），不进 tasklib
- 理由：依赖 funasr/moviepy/librosa 重栈 + funclip sys.path 处理 + 后台线程 +
  Gradio 缓存联动，全是 home 应用编排；tasklib 保持零重依赖（REQ-2.4.3 先例）。

### D3: 后台线程 + 内存 job 表 + 轮询（不上 Celery/队列）
- 模块级 `_JOBS: dict[task_id, JobState]`，`threading.Thread(daemon=True)` 执行；
- 前端 `setInterval` 2s 轮询 `/slirn/api/subtitle_status`；
- 进度为**阶段级**（加载模型/提取音频/识别中/保存）——funasr generate 无细粒度回调，
  不伪造百分比。
- 单任务单飞：同 task 重复 POST 直接返回"已在生成中"。

### D4: 结果双写 `tasks/<id>/outputs/`
- `subtitle.srt`：utf-8-sig，下游 skill 可直接吃；
- `subtitle.json`：`{version, model, source(segment|original), video_path, video_url_name, base_offset_ms, created_at, segments:[{i,start_ms,end_ms,text}]}`；
- 时间基准 = 被识别视频自身时间轴（截取段从 0 起）；`base_offset_ms` 记录截取段在
  原视频中的起点，供后续"映射回原视频"阶段用。

### D5: 视频预览走自建 Range 流式端点 `/slirn/api/video/{task_id}`
- 任务视频在 `slirn-standalone/tasks/`（D 盘），`/gradio_api/file=` 只白名单
  `%TEMP%/gradio/`（C 盘）——复制 415MB 到 C 盘太浪费；
- 自建原始 ASGI 路由（同 upload_video 模式，绕 Gradio 6 rebuild 的 FastAPI 注入 bug）：
  手工解析 `Range` 头 → 206 + Content-Range 分块流式，支持进度条拖动；
- 视频解析逻辑单一来源 `_resolve_task_video()`：截取段优先、原视频兜底，
  渲染层 / 播放端点 / 生成端点三处共用。

### D6: UI 挂在任务详情页（既有 `slirn-tab-detail` cell）
- 处理区：无字幕 → 「🎙 生成字幕」+ 状态行；有字幕 → 播放器 + 字幕列表 + 「🔄 重新生成」；
- 字幕行点击：`data-start-ms` → `v.currentTime = ms/1000` + `play()`；
- `timeupdate` → 线性扫描 `window.slirnSubs` 找当前段 → 高亮 + 自动滚动；
- 生成完成 → 重新 `view_task` 整块重渲染（服务端渲染 HTML，符合既有 refreshCell 模式）。

## 2. 关键复用

| 复用点 | 位置 |
|---|---|
| `VideoClipper.video_recog` | funclip/videoclipper.py:229 |
| `Text2SRT`（text 拼接 + ms→SRT 时间） | funclip/utils/subtitle_utils.py:29 |
| `build_model` paraformer 分支 | skill/video-subtitle-extractor/scripts/extract_subtitle.py:60 |
| `mgr.update_status(SUBTITLE_GENERATED)` | tasklib/manager.py:152 |
| `postJSON / handleResp / refreshCell / _esc` | slirn_home/app.py 既有 JS + _ok/_err |

## 3. 风险

| 风险 | 缓解 |
|---|---|
| 2h41m 原视频 CPU 识别耗时长 | 阶段进度 + 已耗时显示；UI 提示长视频耗时长；建议先截取 |
| `video.audio is None` 上游直接 `sys.exit(1)` | 起线程前 ffprobe 预检音轨；线程内兜底 catch SystemExit |
| funasr 首次联网下载模型 | 本机已全部离线缓存；job error 原样回显 |
| 重新生成时正在播放 | 完成后整块重渲染会重建 video 元素（可接受） |
