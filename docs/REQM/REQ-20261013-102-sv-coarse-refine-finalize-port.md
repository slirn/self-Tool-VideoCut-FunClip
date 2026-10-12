# REQ-20261013-102 — 短视频粗剪 + 字幕优化 + 精剪混编按长视频整套搬过来

> 状态：✅ 已闭环（2026-10-13）— 3 子任务全部串行完成
> 优先级：紧随 REQ-101 之后（用户串行指令：先 B 后 C+D，C+D 方向相同合并）

## 1. 解决什么问题

用户原始需求：

> 短视频最后阶段「精剪混编」参照长视频「精剪合成」整套功能搬过来

> 包括前面的短视频粗剪和字幕优化，也按照长视频的粗剪和字幕优化功能复制一套
> 搬过来。后台能用的、能复用的尽量复用，但不要相互影响。

当前短视频（Stage 5/6/7）能力清单：

| 阶段 | 现状 | 长视频对应 | 主要缺失 |
|---|---|---|---|
| Stage 5 粗剪 | 调 moviepy 按 highlights[].subtitle_lines[].src_index 多窗切 → `coarse_NN.mp4` + 占位 srt | `compose_rough` 上游 VideoClipper.video_clip（`rough_compose.mp4`） | 切完**没**"上游统一合成"一步（多段合并 + 字幕烧录 + 副产物 srt）；UI 无 status 区，无 cancel，无 re-run 进度轮询 |
| Stage 6 字幕优化 | 单条 coarse mp4 重 ASR → `refined_NN.srt`；前端 textarea 修正保存 | `optimize_subtitle`（ASR + LLM 局部替换 + 标记删除行 + 行内切分 + 批量替换 + 优化成片剪辑） | **完全缺**整段 keep/fix/split/delete LLM 优化、行内 accept/reject、整句替换、行内切分、批量搜索/替换、词频面板、cut state 自动调度 |
| Stage 7 精剪混编 | 调 `cut_by_srt.py` 子进程 → `final_NN.mp4` + `final_NN.srt`（每 highlight 独立） | `fine_cut`（6 素材合成器：video+subtitle+cover+bg+reference+audio；layout/font/output/audio；BGM；bg 检测；AI 布局；模板；预览/异步导出） | **完全缺**素材合成器六大卡、字体/位置/裁剪、BGM、preview 2-30s、异步 export job、全局模板 |

C+D 方向相同（都是"把长视频功能搬到短视频"），合并为一个 REQ，分 3 个子任务按序实现。

## 2. 设计决策

### 2.1 后台 helper 复用策略

**核心原则**：业务无耦合的 helper（ffprobe / SRT 解析 / 区间合并 / 原子写 / execution_history）**直接 import**；业务耦合的（cutlist_service / pipeline_service / 6 阶段 handler）**不动**。

| helper | 来源 | 复用方式 | 备注 |
|---|---|---|---|
| `compose_service._probe_duration` | 长 | 直接 import | 短视频当前有重复的 `_ffprobe_duration`，可删 |
| `compose_service._probe_av_durations` | 长 | 直接 import | 编码后 A/V 校验 |
| `compose_service.ffmpeg_bin()` | 长 | 直接 import | 选稳定 imageio_ffmpeg |
| `compose_service.parse_srt / format_srt / _ms_to_srt_time / _SRT_TIME_RE` | 长 | 直接 import | 删短视频自己的 `_fmt_srt_time` (ms) / `_fmt_ms` / 重复正则 |
| `compose_service.merge_intervals_ms / complement_intervals_ms` | 长 | 直接 import | 短视频 Stage 5 多窗拼接统一改用 |
| `asr_service.has_audio_track` | 长 | 直接 import | 防 ffmpeg 在无音频源上崩 |
| `asr_service.segments_to_srt` | 长 | 直接 import | segments → SRT（带 spk 标签） |
| `execution_history.record_start / record_finish / patch_extra / patch_fields / load_history / query_history` | 长 | 直接 import | 短视频已用 KIND_SHORT_VIDEO_AI / KIND_SHORT_VIDEO_RENDER，**新增 KIND_SHORT_VIDEO_COARSE / KIND_SHORT_VIDEO_OPTIMIZE / KIND_SHORT_VIDEO_FINE** 三个常量 |
| `cutlist_service.tokenize / join_tokens / align_tokens` | 长 | 暂不搬 | 短视频没有"切分修剪"决策流，tokenize 在内容优化阶段（Stage 4）已实现；本任务不引入新切分决策 |
| `pipeline_service.*` | 长 | **不搬** | 6 阶段硬编码长视频模型，引入会污染 |
| `compose_service.compose_rough_cut` | 长 | **不搬**（重写短视频版） | 上游 VideoClipper + moviepy + _COMPOSE_LOCK 耦合长视频；短视频用 moviepy.subclip + 多窗拼接（已有 Stage 5 实现） |
| `optimize_service.start_job / save_decisions` | 长 | **参考实现思路，短视频内部重写** | 短视频字幕源是 Stage 5 粗剪 mp4（每条独立），没有"行 / 父段 / 标记删除行"的复杂 keep/delete 决策流；但可借鉴 LLM 局部替换 + 接受/拒绝的行级决策模式 |
| `app.py:4379-5061 _render_fine_cut_zone` | 长 | **参考 UI 形态，短视频内部重写** | 7 阶段短视频面板用 inline HTML 渲染（与现有 Stage 1-7 一致），**不**强行套长视频 700 行 panel（避免引入 fine_compose.json 整套模型） |
| `fine_compose.json` + `fine_profiles` | 长 | **不直接复用** | 短视频用 `project.json` 存精剪参数（避免新加 _schema 迁移），但**借鉴** 6 组子字段（layout/font/output/audio/preview）落 project.json 新增的 `pipeline.stage7_finalize.params` 子键 |
| `compose_service.start_compose` job 表模式 | 长 | **借鉴** | 短视频 Stage 5/7 改异步 + 进度轮询模式（当前是同步阻塞，10 分钟级别要轮询） |

### 2.2 项目隔离策略（**不相互影响**）

- 路径：`tasks/<tid>/short_video/<pid>/stage{5,6,7}/...`（与现有短视频一致）
- 状态：每个项目自己 `project.json → pipeline.{stage5_coarse, stage6_refine, stage7_finalize}`（**不动**长视频 `tasks/<tid>/outputs/...`）
- helper 调用**不写**长视频 outputs 目录（短视频产物路径常量保留在 short_video_service.py）
- 重新跑 Stage 5/6/7 只作废短视频自己的 stage 6/7 状态（`reset_downstream_stages` 已有逻辑），**不**触长视频
- 长视频执行历史 `tasks/<tid>/outputs/execution_history.json` **不写**；短视频写自己的 `tasks/<tid>/short_video/<pid>/execution_history.json`（与现有短视频惯例一致）

### 2.3 三个子任务范围（按用户串行指令逐步闭环）

#### 子任务 C1：Stage 7 精剪混编按长视频精剪合成搬

最小可用集（**v1**）：

- 新增 endpoint `POST /slirn/api/short_video_stage7_export`（异步） + `GET /slirn/api/short_video_stage7_export_status`（轮询）
- 新增 endpoint `POST /slirn/api/short_video_stage7_save_params`（保存 6 组子字段到 `pipeline.stage7_finalize.params`）
- 新增 endpoint `POST /slirn/api/short_video_stage7_load_default`（默认值）
- 服务端模块 `slirn_home/short_video_fine.py`（新增）
  - `run_stage7_one(...)`：单 highlight 调 `cut_by_srt.py`（已有 short_video_finalize.run_stage6_one 改造 + 复用其 `_ffprobe_duration`/`_validate_stage6_isolation`）
  - `run_stage7_batch(...)`：批量（已有 short_video_finalize.run_stage6_batch 改造）
  - 复用 `compose_service.ffmpeg_bin` / `compose_service._probe_av_durations`
- 重新设计 Stage 7 UI 面板（在 short_video_ui.py `_stage7_finalize`）：
  - 顶部 6 素材卡（video / subtitle / cover / bg / reference / audio）—— **最小版**：每条 highlight 的 coarse mp4 作 video，refined srt 作 subtitle；cover/bg/reference/audio 可选，**初始为空允许"快速导出"模式**（仅 video + subtitle 走 cut_by_srt）
  - 字体设置 11 项（family/size/bold/align/offset/stroke_width/color/stroke_color/bg_enabled/bg_color/bg_opacity/bg_radius）
  - 输出设置（resolution 720p/1080p/source）
  - BGM（enabled / volume_db / fade_in / fade_out + 系统默认 BGM 选择，复用 `app.py:7400-7408 list_default_bgms/list_bgm_files/select_default_bgm` 三个端点；按 project_id 维度平移）
  - 「🎬 预览」（2-30 秒）+ 「💾 导出最终视频」（异步，复用 `compose_service.ffmpeg_bin` 跑 ffmpeg 烧录 + BGM 混合）
- **v1 完整版**（用户 2026-10-13 指示「模板/检测/AI 布局也塞进 v1」）：6 素材 + 字体 + 输出 + BGM + 预览 + 异步导出 + **全局参数模板**（fine_profiles 套到短视频） + **bg 检测**（视频白底自动取景） + **AI 智能布局**（多模态 LLM 解析参考图）。不预留 v2。
- **执行策略**：C1 子任务分多次 commit（每 1-2 个子特性一个 commit 便于 review），不强行一次提交（30-40 文件单 commit 过大）。每个子特性单独 test + restart + commit + push，最后一次写 REQM 闭环记录。

#### 子任务 C2：Stage 5 粗剪按长视频粗剪合成搬

- 现有 `_coarse_one`（`short_video_coarse.py:88-191`）+ `run_stage4`（:194-351）已能多窗拼接出每条 highlight 独立 mp4/srt
- 新增 endpoint `POST /slirn/api/short_video_stage5_export`（异步） + 进度轮询（仿 `compose_service.start_compose`）
- 新增 endpoint `POST /slirn/api/short_video_stage5_delete`（清空粗剪产物）
- 服务端模块**沿用** `short_video_coarse.py`（不新增文件），仅把 `run_stage4` 改异步
- UI 改造 `_stage5_coarse`：
  - 顶部状态卡：粗剪成片总时长、clip 数、首次/重新合成按钮、删除按钮
  - 每条 clip 卡片保留现有 `<video>` 预览 + 下载
  - 加 🎬 全片「粗剪预览」(整个任务 N 条 mp4 顺序拼接预览，**v1 简化**：只展示 N 条 mp4 列表；不真合并)
  - 加 📋 复制 SRT（连续时间轴合成，调用 `compose_service.format_srt` 跨 clip 拼接）
  - 加 ⬇ 下载 SRT 压缩包（zip of N srt，**v1 简化**：只下载当前选中一条）

#### 子任务 D1：Stage 6 字幕优化按长视频优化字幕搬

最小可用集（**v1**）：

- 现有 `run_stage5_one`（`short_video_extract.py:290-364`）已有 funasr 重 ASR → `refined_NN_asr.srt` + `save_stage6_refined`（:365-415）保存
- 新增：LLM 行级局部替换（参考 `optimize_service.parse_occurrences` + `_normalize_occ` + `apply_replacements`）
- 新增 endpoint `POST /slirn/api/short_video_stage6_optimize`（单条 LLM 优化）+ 轮询
- 新增 endpoint `POST /slirn/api/short_video_stage6_save_decisions`（保存接受/拒绝）
- 新增 endpoint `POST /slirn/api/short_video_stage6_resplit` + `/unsplit`（行内切分/取消切分）
- 新增 endpoint `POST /slirn/api/short_video_stage6_batch_search` + `/batch_replace`（批量搜索/替换）
- 服务端模块 `slirn_home/short_video_opt_review.py`（新增）—— 借鉴 `optimize_service` 思路，**不**直接 import（避免 stage key 错位）
- UI 改造 `_stage6_refine`：
  - 顶部「✨ LLM 优化」（单条按钮 + 进度）
  - 词频面板（`aggregate_words + build_mapping` 简版，**v1 简化**：仅按出现频次排序 chips）
  - 行级 render：✓ 采纳 / ✕ 不采纳 / ✏️ 整句替换 / 🗑️ 标记删除行 / ✂️ 行内切分（每条 refined_NN.srt 单独面板）
  - 批量替换面板：🔍 搜索 → 预览 5 行 → ✅ 全部替换
  - 「💾 保存决定」 → 推进 stage6_refine.status=done
- **v1 不做**：切分行整句重切（`resplit_segment` 那种 token 级）；优化成片剪辑（`start_optimize_cut` 那种单趟 ffmpeg 硬剪）；自动化 auto_session；KIND_OPTIMIZE_CUT 调度。理由：短视频 Stage 6 的"删除行"在 Stage 7 cut_by_srt 阶段已经处理（cut_by_srt 只保留 srt 中含文字的区间），不再需要单独的优化成片。

### 2.4 实施顺序与提交粒度

按用户 2026-10-13 串行指令，3 个子任务**调整顺序**逐个完整闭环（实现 + 测试 + 重启 7861 + commit + push + doc + memory + 回复）：

1. **C2（Stage 5 粗剪）** — 先做，10-15 个改动，2-3 次 commit
2. **D1（Stage 6 字幕优化）** — 中等，25-30 个改动，3-4 次 commit
3. **C1（Stage 7 精剪混编 v1 完整版）** — 最大，30-40 个改动 + 模板 + 检测 + AI 布局，4-6 次 commit

每个子任务的 REQM 文档独立（`REQ-20261013-103-sv-stage5-coarse-port.md` / `104-sv-stage6-optimize-port.md` / `105-sv-stage7-fine-port.md`），本文档作为总览 + 短长隔离策略。

### 2.5 不做的事

- ❌ 不动长视频 `tasks/<tid>/outputs/...` 任何文件
- ❌ 不引入 `fine_compose.json` 新 schema（短视频把精剪参数落 `project.json → pipeline.stage7_finalize.params`）
- ❌ 不引入 `pipeline_service.STAGE_ORDER` 短视频阶段（保持现状 `short_video_service._STAGE_STATE_KEYS` 8 键自管）
- ❌ 不做短视频自动流程（pipeline_service.run_pipeline 不接短视频）
- ❌ 不做精剪全局模板 `fine_profiles`（v1 仅任务级 + 复制长视频 6 组字段；模板 v2 再说）
- ❌ 不做精剪 bg 检测（依赖 OpenCV/PIL 引入新依赖；用户没要求；v1 跳过）
- ❌ 不做精剪 AI 智能布局（多模态 LLM 调用；v1 跳过）

## 3. 关键共享 helper 复用清单

执行时直接 import 不重写：

```python
# 短视频可原样 import 的长视频 helper
from slirn_home.compose_service import (
    ffmpeg_bin, _probe_duration, _probe_av_durations,
    parse_srt, format_srt, _ms_to_srt_time, _SRT_TIME_RE,
    merge_intervals_ms, complement_intervals_ms,
)
from slirn_home.asr_service import has_audio_track, segments_to_srt
from slirn_home.execution_history import (
    record_start, record_finish, patch_extra, patch_fields,
    load_history, query_history, format_duration,
)
# 新增常量：
KIND_SHORT_VIDEO_COARSE   = "short_video_coarse"   # execution_history.py
KIND_SHORT_VIDEO_OPTIMIZE = "short_video_optimize"
KIND_SHORT_VIDEO_FINE     = "short_video_fine"
```

短视频**待删**的重复实现（统一改 import 长视频版）：

| 短视频当前 | 长视频等价 | 行数 |
|---|---|---|
| `short_video_finalize._ffprobe_duration` (54) | `compose_service._probe_duration` | 18 |
| `short_video_finalize._SRT_TIME_RE` (223) | `compose_service._SRT_TIME_RE` | 1 |
| `short_video_coarse._fmt_srt_time(ms)` (80) | `compose_service._ms_to_srt_time` | 6 |
| `short_video_analyze._fmt_ms` (1418) | `compose_service._ms_to_srt_time` | 6 |

合计 ~31 行可删 + 改 import。

## 4. 验收标准（总览，每子任务独立 REQM 内细化）

### 共同
1. 短视频项目**不影响**长视频项目（同一 task_id 下长视频 outputs 目录零写入；新文件路径前缀 `short_video/<pid>/...`）
2. 7861 端点全部新加 `app.py` 内 `@app.app.post`，**不**改长视频端点
3. ruff 工作树 == HEAD（零新增 lint 错误）
4. node --check router.js / short_video.js 通过
5. 全量 pytest ≥ 现有基线 + 新增子任务测试通过

### C1（v1 完整版 — 精剪混编）
1. Stage 7 端点齐：stage7_export / stage7_export_status / stage7_save_params / stage7_load_default 全部可调
2. 单 highlight 异步导出 job 可轮询进度（done 时返回产物 url）
3. UI 顶部 6 素材卡（video / subtitle / cover / bg / reference / audio）+ 字体 11 项 + 输出 + BGM + 预览 2-30s + 异步导出全部可见
4. **bg 检测**：`detect_bg_white_area` 端点 + UI「🔍 检测区域」按钮 + `detected_region` 落 `pipeline.stage7_finalize.params.detected_region` + 「✅ 填充到视频位置和裁剪」按钮可一键同步
5. **AI 智能布局**：`parse_reference_layout` 端点 + UI「🤖 AI 智能识别布局」按钮 + 解析结果填充到 layout.{video,subtitle,cover,bg}
6. **全局参数模板**：`list_fine_global_profiles / save / apply / delete / rename / export` 6 个端点（**项目级路径平移**到 `tasks/_global_short_video_fine_profiles.json`，避免污染长视频 `tasks/_global_fine_profiles.json`）
7. 短/长隔离：短视频模板文件 `tasks/_global_short_video_fine_profiles.json` 与长视频 `tasks/_global_fine_profiles.json` 互不读写

### C2
1. Stage 5 端点齐：stage5_export / stage5_export_status / stage5_delete
2. 异步进度轮询可工作（仿 `compose_service.start_compose`）
3. UI 顶部状态卡 + 每条 clip 视频预览 + 复制/下载 SRT
4. 短/长项目隔离：删除粗剪**不**影响长视频 rough_compose.mp4

### D1
1. Stage 6 端点齐：stage6_optimize / stage6_save_decisions / stage6_resplit / stage6_unsplit / stage6_batch_search / stage6_batch_replace
2. 单条 LLM 优化可成功（mock _call_llm 跑通链路）
3. 行级 render 完整（采纳/拒绝/整句/删除/切分/子段 mark）
4. 批量替换保存后 reload 数据一致
5. 短/长项目隔离：Stage 6 保存不污染长视频 outputs

## 5. 风险与备选

- **风险 1**：长视频精剪面板 ~700 行；v1 缩简为"快速导出"模式可能让用户觉得"搬了不如不搬"。**缓解**：v1 UI 内显著标注「最小可用集 / 6 素材 + 字体 + BGM + 预览/导出 + 异步」+ 「v2 计划：模板/bg 检测/AI 布局」，与用户对齐期望。
- **风险 2**：短视频 Stage 6 的"标记删除行"在 Stage 7 cut_by_srt 中已经隐式处理（srt 里没文字的区间会被丢弃）。**缓解**：v1 不做"优化成片剪辑"（`start_optimize_cut` 那种），Stage 6 的标记删除仅影响词频统计与 UI 视觉；Stage 7 仍按 srt 实际条目切。
- **风险 3**：短视频每条 highlight 独立 mp4 异步导出 5-10 分钟（cut_by_srt 重编码），多 highlight 并发会撞 ffmpeg。**缓解**：仿 `app.py:_FINE_RENDER_GATE` 跨项目串行锁（`threading.Lock`，短视频模块内单例 `_SHORT_VIDEO_FINE_GATE`）。

## 6. 闭环记录（子任务完成后回填）

按用户 2026-10-13 串行指令顺序：**C2（粗剪）→ D1（字幕优化）→ C1（精剪 v1 完整版）**

| 子任务 | REQ 文档 | 提交号 | 7861 PID | 备注 |
|---|---|---|---|---|
| C2 | REQ-20261013-103 | `7ae40d8` / `59001e3` | bnu0blza2 | Stage 5 粗剪按长视频搬；先做 ✅ |
| D1 | REQ-20261013-104 | `7e281af` / `e838689` | bnu0blza2 | Stage 6 字幕优化按长视频搬；中间做 ✅ |
| C1 | REQ-20261013-105 | `7ac8819` / `9f32afe` | bnu0blza2 / bpqp9lewd | Stage 7 精剪 v1 完整版（6 素材+字体+BGM+预览+异步+模板+检测+AI 布局）；最后做 ✅ |

## 7. 与已闭环任务的关系

- 紧跟 [[REQ-20261013-100]]（Stage 4 内容优化）+ [[REQ-20261013-101]]（快捷键顶栏）之后
- 上一串行任务：REQ-20261013-101（已闭环，2a2d148 + 0327771）
- 下一串行任务：本文档 3 子任务全部完成，等用户验收 / 进一步指令
