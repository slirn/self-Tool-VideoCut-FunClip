# REQ-20261016-134 — Stage 5 标题置顶 + Stage 6 卡片可见（修复 0 按钮问题）

> 状态：✅ 已完成（2026-10-16）
> commit：`d9ff2f0`（与 REQ-116/117 leftovers 合并提交）
> 范围：Stage 5 卡片标题显示 + Stage 6 渲染逻辑 + Stage 5 coarse title 透传

## 1. 需求（用户原话）

### 1.1 Stage 5 粗剪成品列表

> 红框所选的区域，这个3个短视频相关信息以列表形式展现，先显示标题，
> 然后是其他的信息及按钮放在后面

### 1.2 Stage 6 字幕优化（BUG 修复）

> 第六阶段，这个详情区域根本没有可操作的按钮，要如何执行字幕的优化呀？

## 2. 关键设计决策

### 2.1 Stage 5 标题

| 维度 | 选型 | 理由 |
|---|---|---|
| 标题位置 | 卡片顶部（`.slirn-stage4-clip-title-top`） | 用户原话「先显示标题」 |
| 标题来源 | `stage3 highlights.title` 透传到 `stage5_coarse.highlights[i].title` | 与上游 stage3 一致；无 title 时 UI 用「片段 #N」占位 |
| CSS | 14px / 600 半粗 / 2 行截断 / `word-break: break-word` | 比 head 信息更显眼（12px），但不抢按钮焦点 |
| class 名 | `slirn-stage4-clip-title-top`（避免与已有 `slirn-stage4-clip-title` 冲突） | L6329 的 `slirn-stage4-clip-title` 是 head 内的 flex 子项，nowrap 截断 |

### 2.2 Stage 6 卡片可见性

| 维度 | 选型 | 理由 |
|---|---|---|
| 触发条件 | `stage6_refine.highlights` 空 AND `stage5_coarse.highlights` 有产物 | 用户首次进 Stage 6 的真实场景 |
| 合成策略 | 内存合成占位条目（status=pending, asr_srt="", refined_srt=""），不写盘 | 与 REQ-102/103 长/短工作台结构对齐；按 index 沿用 stage5 |
| 用户感知 | 立刻看到所有 clip 卡片 + 按钮，无需「初始化」动作 | 简化 UX，避免「点哪个才能开始优化」的疑问 |
| 后端 | 不动 | `run_stage5_one` / `save_stage6_refined` / `short_video_stage6_optimize` 已支持按 index 操作 |

## 3. 关键文件改动

| 文件 | 改动 | 行数 |
|---|---|---|
| [slirn_home/short_video_ui.py:1130-1134](slirn_home/short_video_ui.py#L1130) | _stage5_coarse：提取 title_text + 渲染 `.slirn-stage4-clip-title-top` | +8 |
| [slirn_home/short_video_ui.py:1196-1214](slirn_home/short_video_ui.py#L1196) | _stage6_refine：合成占位 highlights（s5_highlights synthesis） | +15 |
| [slirn_home/short_video_coarse.py:472-475](slirn_home/short_video_coarse.py#L472) | new_highlights 加 title 字段（hl_by_idx O(1) 查找） | +5 |
| [slirn_home/static/home.css:6412-6427](slirn_home/static/home.css#L6412) | `.slirn-stage4-clip-title-top` 样式 | +15 |
| [tests/test_short_video_stage5_clip_title.py](tests/test_short_video_stage5_clip_title.py) | 5 个新测试（title 显示 / 顺序 / fallback / CSS） | +157 |
| [tests/test_short_video_stage6_auto_cards.py](tests/test_short_video_stage6_auto_cards.py) | 3 个新测试（合成卡片 / 优先 stage6 / 无 coarse 兜底） | +119 |
| [tests/test_short_video_coarse_title.py](tests/test_short_video_coarse_title.py) | 2 个新测试（coarse 透传 title） | +28 |

## 4. 测试

- `test_stage5_clip_title_appears_before_head` — 3 张卡片 3 个 title，title 在 head 之前
- `test_stage5_clip_title_falls_back_to_index` — 无 title → 「片段 #N」占位
- `test_stage5_clip_head_contains_status_and_buttons` — head 含 #1 / 73.8s / 下载视频 / 复制 SRT / 下载 SRT
- `test_css_title_style_defined` — CSS 含 `slirn-stage4-clip-title-top` 样式 + 14px / 600 / line-clamp: 2
- `test_stage5_clip_layout_order_is_title_head` — 卡片内 title → head → preview-btn 顺序
- `test_stage6_renders_cards_from_coarse_when_stage6_empty` — coarse 3 条 → Stage 6 3 张卡片 + 3 个 ASR + 3 个优化按钮
- `test_stage6_uses_existing_highlights_when_present` — 真有 stage6 时优先用它
- `test_stage6_empty_when_no_coarse` — coarse 空时「先完成 Stage 5 粗剪」提示
- `test_coarse_new_highlights_includes_title_field` — coarse 字典含 `"title":`
- `test_coarse_title_lookup_uses_index` — 用 hl_by_idx O(1) 查找

修改：
- `test_short_video_nav_and_refresh.py::test_stage6_empty_hint_reflects_stage5_artifacts` —
  新行为：coarse 有产物时 Stage 6 不再显示「尚无优化结果」空提示，而是渲染可操作卡片

全量 1335 → 1345 passed。

## 5. 验收

- [x] 1345/1345 pytest 全过
- [x] 7861 重启加载 CSS（CSS 改动 + Stage 6 合成逻辑都在 Python）
- [x] 真实项目验证 Stage 5 卡片显示标题（项目 sv_20260930_234159_86a2fa，3 张卡片均有 `.slirn-stage4-clip-title-top` + 预览/下载按钮）
- [x] 真实项目验证 Stage 6 卡片可见（项目同上，3 张卡片 + 3 个「重新 ASR」 + 3 个「✨ LLM 优化」）

## 5.1 E2E 端点验证（用户原话「点一下试试」）

按用户要求**真实点击**所有 Stage 6 按钮（项目 sv_20260930_234159_86a2fa = 「8课3短视频」）：

| 按钮 | 端点 | 结果 |
|---|---|---|
| 重新 ASR #1 | POST `/short_video_stage5_run` index=1 | ✅ ok=True, status=user_editing, asr_chars=1598 |
| ✨ LLM 优化 #1 | POST `/short_video_stage6_optimize` index=1 | ✅ ok=True, 12 occurrences + 12 words + 33 lines |
| 保存修正 #1 | POST `/short_video_stage5_save` index=1 | ✅ ok=True, status=confirmed |
| 行级决策 | POST `/short_video_stage6_save_decisions` index=1 | ✅ ok=True |

**额外发现 3 个 pre-existing bug 并修复**（与 REQ-134 独立 commit）：

| # | bug | 根因 | 修复 |
|---|---|---|---|
| 1 | `NameError: _sv_coarse` | REQ-103 提交时漏删旧 `stage5_run` 实现，FastAPI 取首个 | 删 19 行旧实现 ([app.py:11056-11073](slirn_home/app.py)) |
| 2 | `AttributeError: stage6_refined_srt_path` | rename 漏改 extract.py（其他文件都改对了） | 改回 `stage5_refined_srt_path` ([short_video_extract.py:367,423](slirn_home/short_video_extract.py)) |
| 3 | `TypeError: _call_llm() got unexpected 'timeout'` | `_call_llm` 不接受 timeout，但 opt_review.py 传了 | 删 `timeout=` kwarg ([short_video_opt_review.py:425](slirn_home/short_video_opt_review.py)) |

**测试加固**：[tests/test_short_video_stage6_button_endpoints.py](tests/test_short_video_stage6_button_endpoints.py) 新增 4 个回归测试（grep 静态检查错误符号），1345 → 1349 pytest 全过。

## 6. 不做的事

- ❌ 不动 Stage 4（per_hl 机制独立）
- ❌ 不动 ASR/LLM 流程
- ❌ 不持久化合成条目（只渲染期合成，节省磁盘 IO）
- ❌ 不重排卡片字段顺序（保留 head 行的「#N/状态/时长/按钮」原结构）
- ❌ 不做触摸事件支持

## 7. 关联

- [[req-105-sv-stage7-fine-port]] — Stage 7 精剪 UI 风格（per-block 卡片可参考）
- [[req-106-sv-stage7-ui-relayout]] — 长/短视频 UI 重排思路
- [[req-133-sv-stage6-row-edit]] — Stage 6 per-row 编辑（前置 REQ）
- [[req-132-sv-stage5-video-popup]] — Stage 5 视频预览浮窗（前置 REQ）
- [[req-102-sv-coarse-refine-finalize-port]] — 短/长工作台结构对齐
