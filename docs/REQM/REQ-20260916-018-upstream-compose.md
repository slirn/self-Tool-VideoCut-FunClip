# REQ-20260916-018 — 粗剪合成：改用上游 VideoClipper 合成方法

## 需求原话

> 粗剪合成，你是自己单独写的算法吗？这个算法很失败，你应该看一下当前项目的主项目，它的首页上有一个合成的页面，那里边会有相关的参数和调用方法。你看一下它的调用过程是什么样的，如果缺原始视频相关参数的话，你需要分析一下，这个原始视频应该怎么传递给他原先后台的参数。因为他之前是存过，所以，可能没传这个视频地址或者是文件的参数。你把它这个调用过程研究清楚，然后用它这个过程去合成视频文件，这个合成过程，主要就是用咱们前面生成的处理之后的字幕和原文件就能够合成，再加上大模型。你看一下它的处理办法，然后重新写粗剪合成阶段的合成方法。

## 背景

REQ-20260916-016 自研了 ffmpeg concat + libx264 重编码合成路线。被用户判定「这个算法很失败」，本 REQ 改为**复刻上游 FunClip 主项目首页合成页的调用过程**（funclip/launch.py `AI_clip` 按钮 → funclip/videoclipper.py `VideoClipper.video_clip` 的 `timestamp_list` 直传分支）—— 首页的 AI 裁剪用 LLM 选段得 timestamp_list；我们的合成阶段是「切分决策 + 字幕已过大模型修订/热词替换 → 直接传区间」，语义等价。

## 实现

### 上游调用链研究（funclip/launch.py + funclip/videoclipper.py）

**首页合成页 Gradio 绑定（funclip/launch.py:256-266 / 274-290）：**

```python
timestamp_list = extract_timestamps(LLM_res)  # [[start_ms, end_ms], ...]
clip_video_file, message, clip_srt = audio_clipper.video_clip(
    dest_text, start_ost, end_ost, video_state,
    dest_spk=..., output_dir=..., timestamp_list=timestamp_list, add_sub=False)
```

- `video_clip` 的 `timestamp_list` 直传分支（[videoclipper.py:307-308](funclip/videoclipper.py#L307-L308)）：`all_ts = [[i[0]*16.0, i[1]*16.0] for i in timestamp_list]` → `start = ts/16000` 秒。所以 **timestamp_list 元素单位 = 毫秒**（不是秒）。
- state 必需 keys：`recog_res_raw` / `timestamp` / `sentences` / `video` / `clip_video_file` / `video_filename`。runner stage 2 最小 state 构造范式（[videoclipper.py:494-525](funclip/videoclipper.py#L494-L525)）：`state['video'] = mpy.VideoFileClip(file); state['video_filename'] = file; state['clip_video_file'] = output_file`。
- 输出文件固定带 `_no{GLOBAL_COUNT}` 后缀（[videoclipper.py:355-364](funclip/videoclipper.py#L355-L364)），按返回值 rename 成我们的标准产物名。
- `sentences` 格式 = ASR sentence_info：`[{'text', 'timestamp': [[token_start_ms, token_end_ms], ...]}]`（毫秒级 token 时间戳）；`generate_srt_clip`（funclip/utils/subtitle_utils.py）按 time_acc_ost 平移字幕时间戳，输出按 `subs=[(时间, 文本), …]` 给 add_sub=True 烧录（add_sub=False 时不触碰 TextClip/font 路径，仅返回 clip_srt）。

### 改动

| 改动 | 说明 |
|---|---|
| `slirn_home/compose_service.py`（重写） | 改用上游 `VideoClipper(None).video_clip(..., add_sub=False, timestamp_list=ms)`：保留 job 层/进度轮询/`rough_compose_path`/线程 daemon 不变；`build_sentences(lines)`（整行单 token，毫秒时间戳）作 upstream sentences 输入；`merge_intervals_ms(intervals_ms)` 合并相邻区间（<1ms）；end +1ms 容差规避 upstream `int(end*1000)` 浮点精度丢 1ms 触发的 `generate_srt_clip` CASE2 空 `_ts` 崩溃；自带 `_FrameProgressLogger(proglog.ProgressBarLogger)` 钩入 moviepy 的 `bars_callback(bar, attr, value, old_value)` 实时编码进度，并消除 tqdm 进度条；产物 `_COMPOSE_LOCK` 串行（VideoClipper.GLOBAL_COUNT 与 write_videofile 补丁非并发安全）；副产物 `rough_compose.srt` |
| 行集口径 | `effective_keep_units(build_cutlist(...))` — 与切分面板/精剪修订完全同源；行文本取热词替换（REQ-20260916-017）未撤销行的 new_text |
| `slirn_home/app.py` 端点 | `compose_rough` 改为传 `intervals_ms`（整数毫秒），构造 `lines` 合并热词替换；面板 `_render_rough_compose_zone` docstring/stale 判定（`max(saved_at, rev_at, fine_at)`）更新；阶段条描述改为「按切分保留内容用上游 VideoClipper 合成粗剪视频（含随片字幕）」；面板 hint 注明处理之后的字幕 + 上游方法 |
| `tests/test_compose_service.py`（新建 12 用例） | 纯函数与签名：merge_intervals_ms / build_sentences / merge_intervals / rough_compose_path / 三个守卫 / 重入防护 / job_status 缺如 |

### 关键对接说明

- 上游 `VideoClipper(None)` — clip 阶段不加载 ASR 模型（funclip CLI stage 2 同款）
- `timestamp_list` 单位毫秒（funclip `extract_timestamps` 同口径）— 切分清单即毫秒，直传
- end +1ms 是**唯一**必要的兼容性补丁：浮点 `int(258.02*1000) = 258019`，单 token 行的 `ts[-1][1] = 258020 > 258019` 误成立，upstream CASE2 else 分支（[subtitle_utils.py:99-110](funclip/utils/subtitle_utils.py#L99-L110)）走 `_text[0:0]=空` 触发 IndexError
- 性能：200 区间实测 127.2s（含 200 段 subclip+write）；1681 段全量预计 ~12 分钟

## 验证

- `pytest tests/ -q` → **187 passed**（新增 12 个：merge 边界/乱序/空、build_sentences 全行单 token/empty、守卫链、重入、job_status）
- `ruff check slirn_home/ tests/` → clean
- **小样本离线**（`work/.../_service_test.py`，6 行任务 001 真实数据）：17.12s 精确匹配（实测 17.12 vs 期望 17.12）；mp4 + aac；随片 srt；进度回调 8 次；后台 job done
- **子集 200 行**（`work/.../_service_full_test.py`，SUBSET=200）：274.16s vs 期望 274.0s（偏差 <1ms/段）；h264+aac；随片 srt；耗时 127.2s
- **服务重启**（netstat 7862 PID → taskkill → detached）→ HTTP 200
- **E2E 只读**（CDP headless，chrome_cdp_036/9353）七项全过：阶段条描述更新（含「上游 VideoClipper」）、面板 hint 说明上游方法 + 11 分钟预估、compose_rough 守卫（缺 tid）、compose_rough_status 产物兜底、精剪修订/切分面板不受扰、截图、**零写请求**

## 边界说明

- **产物路径**：`tasks/<tid>/outputs/rough_compose.mp4`（沿用 REQ-20260916-016 标准名）+ `rough_compose.srt`（随片字幕副产物，与 mp4 时间轴对齐）
- 不改 funclip/ 上游源码（仅 `from videoclipper import VideoClipPer` + 注入 funclip/ 到 sys.path）
- 任务无字幕生成产物 → 端点返回错误；任务字幕修订未决策 → 端点返回错误（守卫链与原版一致）

## 关联

- 前置：REQ-20260916-016（粗剪合成可选阶段——产物/阶段位置/守卫链接口）、REQ-20260916-017（行文本=热词替换后的 new_text）
- 提交：`refactor(home): 粗剪合成改用上游 VideoClipper 合成方法 (REQ-20260916-018)`