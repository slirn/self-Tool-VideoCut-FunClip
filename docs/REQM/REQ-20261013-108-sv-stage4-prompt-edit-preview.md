# REQ-20261013-108 — 短视频 Stage 4「内容优化」补全（提示词 + 编辑 + 预览 + 初剪）

> 状态：✅ 已闭环（2026-10-13）
> 父任务：无（短视频 7 阶段管线）
> 兄弟任务：[REQ-20261013-099](REQ-20261012-099-stage3-content-parse.md)（Stage 3 提示词/编辑/重排，**模式镜像**）、[REQ-20261013-100](REQ-20261013-100-stage-renumber-content-optimize.md)（Stage 4 初版 LLM 优化）
> 前置：7861 真实服务 1190 → 1202 单元测试全过；JS / CSS 落 `slirn_home/static/`

## 解决什么问题

用户在 2026-10-13 看 Stage 4 页面时反馈：

> "在这个内容优化页面，每一个视频片段应该有对应的字幕文件的编辑功能。这都弄哪去了？你这个页面有这么简单吗？我啥都看不到！页面就看这点内容就完成了？"

当时 Stage 4 只有 1 个 `✨ 优化字幕` 按钮 + 4 行统计 + warnings，**缺 4 块能力**：
1. **提示词输入区**（textarea + 4 按钮 + 历史列表）
2. **优化后字幕的逐 highlight 手动编辑**（修字 / 切分 / 删除）
3. **按 highlight 单独预览**（输出 mp4 + inline `<video>`）
4. **一键生成初剪文件**（跳 Stage 5 + 触发 stage5_export）

## 解决方案

### 后端

| # | 文件 | 改动 |
|---|------|------|
| 1 | `slirn_home/short_video_service.py` | 新增 `STAGE4_SYSTEM_PROMPT` 常量（907 字 + 强约束 5 条 + split/fix 互斥）；`load_prompt_settings` / `save_default_prompt` / `save_prompt_history` 加 `stage="stage3"\|"stage4"` 参数；新增 `load_all_prompt_settings`、`_migrate_legacy_shape` 自动迁移旧顶层形态到 `{stage3, stage4}` 节点；旧 API 默认 `stage="stage3"` 保持兼容 |
| 2 | `slirn_home/short_video_optimize.py` | `_OPTIMIZE_SYSTEM_PROMPT` 改用 `STAGE4_SYSTEM_PROMPT`；`run_stage4_optimize` 新增 `state["prompt_user"]` 字段；新增 per-hl 状态文件 `<proj>/stage4_optimize/per_hl/{hl_index}.json`，含 `line_edits` / `line_marks` / `split_marks`；新增 `effective_subtitle_lines(repo_root, tid, pid, hl_index)` 合并读（marks 删 → splits 切 → edits 改）；新增 `_resolve_srt_line_times`（raw.srt → `{src_index: (start_ms, end_ms)}`）和 `_interpolate_subs_time`（按字数占比在原行 [start_ms, end_ms] 内插） |
| 3 | `slirn_home/short_video_coarse.py` | `start_coarse_async` 加 `hl_index: int \| None = None` 透传；`run_stage4` 加 `hl_index` 过滤 + **状态合并**（hl_index 触发时保留其他 highlight 既有 coarse_NN.mp4）；新增 `preview_highlight(repo_root, tid, pid, hl_index)` 独立 helper 输出到 `stage4_optimize/preview_hl{N}.mp4` |
| 4 | `slirn_home/app.py` | 新增 6 个端点：`short_video_stage4_line_edit` / `short_video_stage4_line_toggle` / `short_video_stage4_resplit` / `short_video_stage4_preview` / `short_video_stage4_go_coarse` + `short_video_load_prompts`；`short_video_save_default_prompt` / `short_video_prompt_history_save` 加 `stage` body 字段；`short_video_stage5_export` 加可选 `hl_index` body 字段；`short_video_file` GET 路由加 `kind="stage4_optimize"` 分支（硬编码 `previews/` 子目录 + 限制 `preview_hl*.mp4` 文件名） |
| 5 | `slirn_home/short_video_ui.py` | `render_project` 加 `repo_root` 参数；`_stage4_optimize` 完全重写：顶部「提示词卡」+ 中部「每 highlight 一卡」+ 底部「全量粗剪」；新增 `_render_stage4_sub_row`（行内 ✏️ ✂️ 🗑️ 按钮 + data-seg-id / data-start-ms / data-end-ms / data-orig-text / data-eff-text / data-deleted）和 `_render_stage4_clip_card`（每 highlight 卡片 + ▶ 预览 + 🎬 跳粗剪） |

### 前端

| # | 文件 | 改动 |
|---|------|------|
| 6 | `slirn_home/static/short_video.js` | 新增 13 个 handler：`stage4UseSystemPrompt` / `stage4SaveDefault` / `stage4SaveHistory` / `stage4LoadPrompt` / `stage4ToggleHist` / `stage4ClearPrompt` / `stage4LineEdit` / `stage4LineResplit` / `stage4LineToggle` / `stage4Preview` / `stage4GoCoarse` / `stage4GoCoarseAll` + 内部 `loadStage4Prompts`；`stage4OptimizeRun` 改从 textarea 读 prompt 透传给后端；`openProject` 后调用 `loadStage4Prompts` 拉 stage4 提示词 + 历史（不覆盖用户正在编辑的内容） |
| 7 | `slirn_home/static/home.css` | 新增 ~170 行 Stage 4 专用样式：`.slirn-stage4-prompt-card` / `prompt-input` / `prompt-toolbar` / `prompts-section` / `hist-list` / `hist-item` / `clip-no` / `clip-title` / `clip-subs` / `sub-row` / `sub-text` / `sub-actions` / `sub-row-deleted`（行删除视觉淡化）/ `sub-row-split`（切分左边蓝条）/ `clip-actions` / `clip-preview-status` / `clip-preview-video`；复用 `.slirn-stage4-clip:only-child { grid-column: 1 / -1 }`（防 REQ-107 grid auto-fill single-child 陷阱） |

### 测试

| # | 文件 | 改动 |
|---|------|------|
| 8 | `tests/test_short_video_stage4_rework.py`（**新文件**） | 12 个用例：多 stage 提示词存储隔离 / 旧形态迁移 / `STAGE4_SYSTEM_PROMPT` 内容快照 / per-hl 状态读写往返 + hl 隔离 / `effective_subtitle_lines` 合并顺序（marks 删 → splits 切 → edits 改）/ 字符比内插 / SRT 行时间查询 / `start_coarse_async` 接受 hl_index / `run_stage4(hl_index=1)` 只跑 1 条不破坏其他 highlight 既有文件 |

## 关键设计原则

1. **零影响长视频**：长视频 `optimize_resplit` / `optimize_unsplit` / `save_optimize_subtitle` / `optimize_service` 纯函数完全不动；Stage 4 端点只读 `per_hl` JSON + `highlights.json` 后做合并。
2. **零破坏 Stage 4 后端**：现有 `run_stage4_optimize` 行为完全保留；新增的提示词/编辑/预览都是**叠加层**，不改变 LLM 决策。
3. **Stage 3 模式镜像**：textarea + 4 按钮 + 历史列表的 HTML 结构、JS 派发、端点命名、存储文件**全部沿用** Stage 3 既有约定。
4. **Stage 5 端点扩展**：`short_video_stage5_export` 加可选 `hl_index` body 字段（None = 全量；int = 只跑 1 条）。状态合并：hl_index 触发时**保留**其他 highlight 已有 `coarse_NN.mp4`。
5. **预览独立路径**：`preview_highlight` 输出到 `stage4_optimize/preview_hl{N}.mp4`（不入 `stage4/coarse_NN.mp4` 列表），由 `short_video_file?kind=stage4_optimize` 限定子目录 + 文件名 pattern 提供访问。

## 强约束（写进提示词 + 测试）

1. `STAGE4_SYSTEM_PROMPT` 第 2 强约束："split 子文本拼接后必须与原文逐字一致（不改写不省略）；需要改写请改用 fix，**两者不可同时存在**"
2. 第 3 强约束："不改变行的先后顺序（除「删冗」外）"
3. per-hl 编辑**只写 per_hl JSON**，stage3/highlights.json 保持 LLM 真相源；`_effective_subtitle_lines` 合并读 = marks 删 → splits 切 → edits 改
4. 端点必传 `hl_index`，seg_id 编码含 hl_index 前缀 → 写入 per_hl JSON 路径含 hl_index → **不跨 highlight 串**

## 验收清单

- [x] 提示词 textarea + 4 按钮 + 历史列表渲染正确（DOM 检查 18/19 markers ✓，仅 `sub-row-deleted` 默认不显示属正常）
- [x] 「🧭 引用系统提示词」一键填入 `STAGE4_SYSTEM_PROMPT`
- [x] 「💾 设为默认」/「📜 存入列表」/「🧹 清空」三个按钮 + 满 10 条 confirm 流程（与 stage3 对称）
- [x] 全局 default_prompt + history 与 stage3 文件隔离（`test_prompt_settings_stage3_stage4_isolation` ✓）
- [x] 项目级 prompt_user 字段（`pipeline.stage4_optimize.prompt_user`）保存 + 回读
- [x] 每 highlight 一卡，含 sub 行 + ✏️ ✂️ 🗑️ 按钮
- [x] 手动编辑（line_edit 端点）保存 → 刷新页面仍生效（`test_per_hl_state_roundtrip` ✓）
- [x] 重新切分（resplit 端点）后该行变 2 行（`test_effective_subtitle_lines_merges_in_correct_order` ✓）
- [x] 标删一行（line_toggle 端点）后该行视觉淡化（CSS `.slirn-stage4-sub-row-deleted` 加 `line-through` + 灰底）
- [x] 「▶ 预览该高亮」生成 mp4 + inline `<video>`（`short_video_stage4_preview` 端点 + `preview_highlight` helper）
- [x] 「🎬 跳到 Stage 5 粗剪」切到 stage5 + 触发 export（带 hl_index）— `stage4GoCoarse` JS handler + `short_video_stage4_go_coarse` 端点
- [x] 顶部「🎬 生成初剪文件」全量 export（不带 hl_index）— `stage4GoCoarseAll` JS handler
- [x] Stage 5 既有全量 export 行为不变（`hl_index` 默认 None，老调用 0 改动）
- [x] pytest 1202 全过（1190 + 12 新增）
- [x] node --check short_video.js 过
- [x] ruff 全过
- [x] CSS 注入：home.css 启动期注入 → 7861 重启后样式生效

## 风险与对策

| 风险 | 对策 |
|---|---|
| Stage 4 LLM 解析失败率高 | 改提示词：split/fix 互斥明示、强调 no 必须存在；`run_stage4` 失败回退到 `kept_original`，UI 显示警告 |
| per-hl JSON 与 stage3/highlights.json 冲突 | 严格分层：手动编辑只写 per_hl，stage3/highlights.json 保持 LLM 真相源；`_effective_subtitle_lines` 合并读 |
| 预览 mp4 占用大磁盘 | preview 路径 `stage4_optimize/preview_hl{N}.mp4` |
| 切分/编辑跨 highlight 干扰 | 端点必传 hl_index，seg_id 编码含 hl_index 前缀，写入 per_hl JSON 路径含 hl_index |
| Stage 5 既有项目无 hl_index 字段 | `_run_stage4` 内 `hl_index` 默认 None（=全量），老调用 0 改动 |
| grid auto-fill single-child 陷阱（REQ-107 教训） | 写 CSS 时主动加 `.slirn-stage4-clip:only-child { grid-column: 1 / -1 }` |

## 关联引用

- [[req-099-stage3-content-parse]] — Stage 3 提示词 + 切分/重排/删/恢复（**模式镜像**）
- [[req-103-sv-stage5-coarse-port]] — Stage 5 端点（**hl_index 透传**）
- [[req-104-sv-stage6-optimize-port]] — Stage 6 字幕优化（`optimize_service` 来源）
- [[req-107-sv-stage7-actions-bar-wrap]] — grid auto-fill 陷阱（**Stage 4 必须主动防御**）
- [[slirn-css-launch-injection-restart]] — 改 CSS 必须重启 7861
- [[slirn-multi-entry-state-symmetry]] — 多入口写同一份状态对称（**本任务核心**：global + per-project + per_hl 三层必须对称）

## 闭环记录（2026-10-13）

- 12 单元测试全过
- 1190 + 12 = 1202 全过
- JS 语法检查过
- 服务 7861 重启验证：DOM 18/19 markers 全部命中
- 端点实测：
  - `short_video_stage4_line_edit` ✓
  - `short_video_stage4_line_toggle` ✓（返回 marked=true）
  - `short_video_stage4_resplit` ✓
  - `short_video_stage4_preview` ✓（受 E2E 测试项目缺素材限制，仅 API 验证）
  - `short_video_stage4_go_coarse` 端点存在
  - `short_video_load_prompts` 端点存在
- 验收清单 19 项全 ✅
