# REQ-20261013-105 — 短视频 Stage 7 精剪 v1 完整版（C1）

> 父任务：[REQ-20261013-102 §2.3 C1](REQ-20261013-102-sv-coarse-refine-finalize-port.md)
> 状态：✅ 已闭环（2026-10-13）

## 解决什么问题

短视频 Stage 7 精剪当前只有「调 cut_by_srt.py 子进程按条输出 final mp4 + continuous srt」，与长视频精剪合成（6 素材卡 + 字体 + 输出 + BGM + 预览 + 异步 + 模板 + bg 检测 + AI 布局）能力不对齐。

按用户 2026-10-13 指令「v1 完整版塞全部（6 素材 + 字体 + BGM + 预览 + 异步 + 模板 + bg 检测 + AI 布局）」，把长视频精剪合成能力整套搬过来。

## v1 范围

### 6 素材卡（必选 + 可选）
- video（必选）：每条 highlight 的粗剪 mp4
- subtitle（必选）：Stage 6 优化后的 refined_NN.srt
- cover：可选，封面图（默认空）
- bg：可选，背景图（默认空）
- reference：可选，参考图（AI 智能布局用）
- audio：可选，附加音频轨（默认空，BGM 用独立字段）

### 字体设置 11 项
- family / size / bold / align / offset / stroke_width / color / stroke_color / bg_enabled / bg_color / bg_opacity / bg_radius

### 输出设置
- resolution：720p / 1080p / source

### BGM
- enabled / volume_db / fade_in / fade_out
- 系统默认 BGM 选择（复用 long video `list_default_bgms` / `list_bgm_files` / `select_default_bgm` 三个端点，按 project_id 维度平移到短视频端点）

### 预览 + 异步导出
- 「🎬 预览」：2-30s（默认 5s），调 ffmpeg 快速切段
- 「💾 导出最终视频」：异步 + 进度轮询（仿 `compose_service.start_compose`），复用 `compose_service.ffmpeg_bin`

### 全局参数模板（v1 完整版）
- 6 端点：list_fine_global_profiles / save / apply / delete / rename / export
- 落 `tasks/_global_short_video_fine_profiles.json`（**不污染**长视频 `tasks/_global_fine_profiles.json`）

### bg 检测（v1 完整版）
- `detect_bg_white_area` 端点 + UI「🔍 检测区域」按钮 + 落 `pipeline.stage7_finalize.params.detected_region` + 「✅ 填充到视频位置和裁剪」按钮

### AI 智能布局（v1 完整版）
- `parse_reference_layout` 端点 + UI「🤖 AI 智能识别布局」按钮 + 解析结果填充到 layout.{video,subtitle,cover,bg}

## 端点（v1 完整版，~12 个）

| 路径 | 功能 |
|---|---|
| POST `/slirn/api/short_video_stage7_export` | 异步导出（仿 compose_service.start_compose） |
| POST `/slirn/api/short_video_stage7_export_status` | 进度轮询 |
| POST `/slirn/api/short_video_stage7_save_params` | 保存 6 组子字段到 pipeline.stage7_finalize.params |
| POST `/slirn/api/short_video_stage7_load_default` | 默认值 |
| POST `/slirn/api/short_video_stage7_preview` | 2-30s 快速预览 |
| POST `/slirn/api/short_video_stage7_detect_bg` | bg 检测 |
| POST `/slirn/api/short_video_stage7_parse_layout` | AI 智能布局 |
| POST `/slirn/api/short_video_stage7_list_bgm` / `select_bgm` | 系统 BGM 选择 |
| POST `/slirn/api/short_video_stage7_profiles_list` / `save` / `apply` / `delete` / `rename` / `export` | 6 个模板管理端点 |

## 验收标准

1. 端点齐：~12 端点全部 200 + 正常响应
2. 单 highlight 异步导出 job 可轮询进度
3. UI 顶部 6 素材卡 + 字体 11 项 + 输出 + BGM + 预览 2-30s + 异步导出全部可见
4. bg 检测 + 「✅ 填充」按钮可工作
5. AI 智能布局可工作（mock LLM 跑通链路）
6. 模板 6 端点：list/save/apply/delete/rename/export 全可用
7. 短/长隔离：短视频模板落 `tasks/_global_short_video_fine_profiles.json`，**不**写长视频 `tasks/_global_fine_profiles.json`
8. 质量门：ruff 零新增、node --check 通过、pytest ≥ 1211 + C1 新增 ≥ 8 测试
9. 7861 重启

## 验收（2026-10-13）

1. ✅ 端点齐：注册 16 端点（11 v1 + 5 模板）；冒烟全 200 + ok=true
2. ✅ 异步导出 job：_JOBS dict + _RENDER_GATE 锁，状态机 running/success/failed/idle
3. ✅ UI：6 素材卡（video/subtitle/cover/bg/reference/audio）+ 字体 11 项 + output resolution + BGM + preview duration + 模板选择 + bg 检测 + AI 布局
4. ✅ bg 检测：`_compute_content_bbox` 全白/居中/单点测试通过 + apply 落 `detected_region`
5. ✅ AI 智能布局：mock _call_llm 返 JSON → apply 写 `pipeline.stage7_finalize.params.layout`；LLM 失败/垃圾输出 fallback 默认布局
6. ✅ 模板 6 端点：list/save/apply/delete/rename/export 全过；重名自动 `(2)` 后缀
7. ✅ 短/长隔离：`save_profile` 落 `tasks/_global_short_video_fine_profiles.json`；长视频 `tasks/_global_fine_profiles.json` 内容/大小零变化
8. ✅ 质量门：ruff 4 新文件零 lint 错；node --check 3 JS 全过；pytest 17/17 + 全量 1228 passed 零回归
9. ✅ 7861 重启：PID bnu0blza2 / bpqp9lewd 跑通

## 闭环记录

| 项 | 值 |
|---|---|
| REQ 文档 | REQ-20261013-105-sv-stage7-fine-port.md（本文件） |
| 提交号 | `7ac8819`（feat(slirn_home) 短视频 Stage 7 精剪 v1 完整版） |
| push ref-update | `e838689..7ac8819 main -> main` |
| 7861 PID | bnu0blza2（初）/ bpqp9lewd（select_bgm try/except 修复后） |
| 新增测试 | 17（test_save_params_merges_six_groups / profile_lifecycle / profile_unique_name_on_duplicate / profile_storage_does_not_touch_long_video_file / fine_job_idempotent_and_copy / compute_content_bbox 全白/居中/单点 / ai_default_layout_4_regions / ai_normalize_region_clamps_out_of_range / ai_normalize_region_handles_garbage / ai_parse_with_mocked_llm / ai_parse_llm_failure_falls_back_to_default / ai_parse_garbage_falls_back_to_default / preview_duration_out_of_range_raises / save_params_does_not_touch_long_video_outputs） |
| 全量 pytest | 1228 passed (83.43s)，零回归 |
| ruff | 4 新文件 All checks passed |
| node --check | 3 JS（pipeline/router/short_video）全过 |
| 端到端冒烟 | select_bgm 用 `20261003-001/sv_20261004_072937_572eb5` + bgm_id=lofi_beat_1 → ok=true；project.json 验证 `params.audio.bgm_path` 已落 |

## 与已闭环任务的关系

- 父任务：REQ-20261013-102（599cf47）
- 前置：C2（59001e3）✅ + D1（e838689）✅
- 下一串行任务：完成 → 用户验收 → 关单
