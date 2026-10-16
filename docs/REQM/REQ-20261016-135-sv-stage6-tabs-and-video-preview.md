# REQ-20261016-135 — Stage 6 字幕优化 UI 重排（Tab 化 + 视频预览浮窗）

> 状态：✅ 已完成（2026-10-16）
> 父 REQ：[REQ-134 SV Stage5 Title + Stage6 Cards](REQ-20261016-134-sv-stage5-title-and-stage6-cards.md)
> 父 REQ：[REQ-132 SV Stage5 Video Popup](REQ-20261016-132-sv-stage5-video-popup.md)
> 父 REQ：[REQ-109 SV Stage4 HL Tabs](REQ-20261016-109-sv-stage4-hl-tabs.md)
> 父 REQ：[REQ-130 SV Stage3 HL Tab Big Button](REQ-20261016-130-sv-stage3-hl-tab-big-button.md)
> 范围：Stage 6 面板 tab 化 + 每 pane 加视频预览按钮

## 1. 需求（用户原话）

> 在字幕优化阶段，要把对应着前一阶段所有的视频的标题显示上，显示方式参考解析短视频内容阶段的形式，在这里主要看每个短视频对应着的字幕，在展现字幕的同时可以查看对应的短视频视频信息，短视频展现时用弹出窗口，并且可以调整大小，这样是为了能够对应视频跟文字信息看看效果。

## 2. 关键设计决策

| 维度 | 选型 | 理由 |
|---|---|---|
| Tab 结构 | 复用 Stage 3 大按钮模式（`#N + 完整标题 + ▾`） | 用户原话「参考解析短视频内容阶段的形式」 |
| Tab/pane 类名 | 新增 `slirn-stage6-hl-tab` + `slirn-stage6-hl-pane` | 与 stage3/stage4 类同级；CSS 复制 stage3 选择器组 |
| Tab 切换 JS | 复用 `switchHlTab(idx)` | 已有 stage3/stage4 支持；只需扩选择器 |
| 视频预览 | 复用 `ensureSvVideoPopup` + `svStage5Preview`（REQ-132） | 已支持拖动 + 调大小 + 同 URL 二次关闭；只读 `data-url` + `data-clip-name` |
| Action 名 | 按钮写 `data-action="sv-stage5-preview"` | 路由表已绑；不新增 `sv-stage6-preview` |
| 视频路径来源 | `coarse_by_idx` 字典从 `stage5_coarse.highlights` 拉 `coarse_mp4` | 现状合成占位漏带 coarse_mp4，预览按钮无 URL |
| URL 拼接 | `_file_url(task_id, project_id, "stage4", name)` | mp4 真实落点 `stage4/`（_stage5_coarse L1125 同款 kind） |
| 无 URL 兜底 | `<span class="slirn-sv-muted">未生成</span>` | 与 Stage 5 预览按钮无 URL 时一致 |
| 状态记忆 | 复用 `window._slirnSvHlTab` | 跨 stage 共享，刷新项目后自动回到上次的 tab |
| Pane 内结构 | 整张 `.slirn-stage5-clip` 卡片**保留不动**，外面套 `<div class="slirn-stage6-hl-pane">` | 不破 REQ-133 per-row 编辑、REQ-134 标题置顶 |
| 标题来源 | `stage6.hl.title` → `coarse.hl.title` → 「片段 #N」 | 多级 fallback，新老项目数据兼容 |
| 全集策略 | 以 `coarse.highlights` 为全集，stage6.highlights 叠加上去；缺失补 status=pending 占位 | REQ-134 仅在 stage6 完全空时合成；当 stage6 只有部分条目时未处理的 clip 就消失，违反「对应前一阶段所有视频」的诉求 |

## 3. 关键文件改动

| 文件 | 改动 | 行数 |
|---|---|---|
| [slirn_home/short_video_ui.py:1248-1392](slirn_home/short_video_ui.py#L1248) | `_stage6_refine` 改 tab+pane 渲染 + coarse_mp4 透传 + 全集策略 | +82 / -24 |
| [slirn_home/static/home.css:5806-5877](slirn_home/static/home.css#L5806) | 新增 stage6 tab/pane 样式（仿 stage3） | +72 / 0 |
| [slirn_home/static/short_video.js:278-301,2685](slirn_home/static/short_video.js#L278) | 扩 3 处选择器 + 1 行路由 | +5 / 0 |
| [tests/test_short_video_stage6_hl_tabs.py](tests/test_short_video_stage6_hl_tabs.py) | 6 个新测试（tab 渲染 / title+arrow / pane 内卡片 / 默认 active / fallback / **全集策略**） | +200 |
| [tests/test_short_video_stage6_video_preview.py](tests/test_short_video_stage6_video_preview.py) | 5 个新测试（按钮 + URL kind / 无 URL 兜底 / 合成占位 / clip_name） | +150 |
| [tests/test_short_video_stage6_js_route.py](tests/test_short_video_stage6_js_route.py) | 2 个新测试（switchHlTab 选择器 + 路由派发） | +30 |
| [tests/test_short_video_stage6_hl_tabs_css.py](tests/test_short_video_stage6_hl_tabs_css.py) | 2 个新测试（CSS 样式 + active display） | +30 |

## 4. 测试覆盖

- `test_stage6_renders_hl_tabs_per_clip` — 3 张 clip → 3 tab + 3 pane + 1 active
- `test_stage6_hl_tab_includes_title_and_arrow` — tab 含 #N + title + ▾ 箭头
- `test_stage6_pane_contains_clip_card` — pane 内有 `.slirn-stage5-clip`（REQ-133 per-row 编辑不破）
- `test_stage6_first_pane_active_by_default` — 第 1 个默认 active
- `test_stage6_falls_back_to_index_when_no_title` — 无 title → 「片段 #N」
- `test_stage6_shows_all_clips_when_stage6_partial` — **stage6 只有 1 条时，仍显示 3 tab**（全集策略）
- `test_stage6_has_preview_button_when_coarse_mp4_present` — 3 个预览按钮
- `test_stage6_preview_button_uses_stage4_kind` — URL kind=stage4
- `test_stage6_no_preview_when_no_coarse_mp4` — 无 mp4 → 「未生成」
- `test_stage6_preview_synthesized_from_coarse` — 合成占位也带 mp4
- `test_stage6_preview_includes_clip_name_attr` — `data-clip-name="#N 预览"`
- `test_js_switches_stage6_hl_tabs` — switchHlTab 选择器
- `test_js_dispatches_stage6_hl_tab_action` — 路由表含 sv-stage6-hl-tab
- `test_css_stage6_hl_tab_style_defined` — CSS active 态 + 箭头旋转
- `test_css_stage6_hl_pane_display_none_by_default` — pane 默认 hidden

1363 → **1364 pytest** 全过。

## 5. 验收

- [x] 1364/1364 pytest 全过
- [x] 7861 重启加载 CSS + Python
- [x] 真实项目验证（`sv_20260930_234159_86a2fa` = 「8课3短视频」）：
  - 3 个 tab（#1 / #2 / #3），完整标题显示（fallback「片段 #N」）
  - 第 1 个 tab/pane 默认 active
  - 3 个「▶ 预览」按钮，URL 全部 `kind=stage4` + `coarse_0N.mp4`
  - 切 tab → pane 切换（前端 JS 接管，不需后端）
  - 点「▶ 预览」→ REQ-132 浮窗（拖动 + 调大小）
- [x] REQ-133 per-row 编辑（✏️/✂️/🗑️）仍可用（pane 内 `.slirn-stage5-clip` 卡片保留不动）
- [x] 上一轮 REQ-134 followup 3 个按钮端点不回归（重新 ASR / LLM 优化 / 保存修正）

## 6. 不做的事

- ❌ 不改 Stage 3 / 4 / 5 / 7 任何 UI（沿用它们的 tab 模式但不重组）
- ❌ 不新增视频弹窗（直接复用 REQ-132 弹窗 + 拖动逻辑）
- ❌ 不持久化 tab 状态（沿用 `window._slirnSvHlTab` 内存）
- ❌ 不改后端端点（无新接口、无修改）
- ❌ 不动 batch 替换 / 行级采纳（顶部工具条保留原位）

## 7. 关联

- [[req-134-sv-stage5-title-and-stage6-cards]] — 父 REQ
- [[req-132-sv-stage5-video-popup]] — 视频弹窗模式
- [[req-109-sv-stage4-hl-tabs]] — 单 pane tab 模式
- [[req-130-sv-stage3-hl-tab-big-button]] — 大按钮完整标题
- [[req-133-sv-stage6-row-edit]] — per-row 编辑（前置于本 REQ，保留不动）
- [[slirn-css-launch-injection-restart]] — CSS 改动必须重启 7861
