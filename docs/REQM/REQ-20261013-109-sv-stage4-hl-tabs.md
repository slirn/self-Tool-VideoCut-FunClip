# REQ-20261013-109 — 短视频 Stage 4 每 highlight 改用 tab 页签切换

> 父 REQ：[REQ-20261013-108](REQ-20261013-108-sv-stage4-prompt-edit-preview.md)（提示词 + 编辑 + 预览 + 初剪）
> 兄弟 REQ：[REQ-20261012-099](REQ-20261012-099-stage3-content-parse.md)（**模式镜像**：Stage 3 tab 切换）
> 状态：✅ 验收通过（2026-10-13）

## Context

**用户反馈**（2026-10-13 配图）：在 Stage 4「内容优化」页面，当 8 条短视频都跑完 Stage 4 后，下方"逐条编辑"区直接把 8 张编辑卡全堆出来，每张卡 20+ 行的字幕编辑列表 + 预览/初剪按钮，**整页非常长**。用户原话：

> "把每一个短视频区域用 tab 页签展现"

**目标**：每条 highlight 占用一个 tab 页签，**一次只显示一条**（用户确认），切换 tab 切换编辑区。

**预期成果**：Stage 4 详情页纵向高度从「N 张卡全堆」降为「tab 栏 + 1 张卡」，视觉与 Stage 3 风格一致。

## 验收清单（实现完成后逐条验证）

- [x] `_stage4_optimize` 渲染：N 条 hl → N 个 tab + N 个 pane
- [x] 第 1 个 tab / pane 有 `active` 类，其余没有
- [x] 每个 pane 内含原 `.slirn-stage4-clip`（REQ-108 编辑能力保留）
- [x] CSS `.slirn-stage4-hl-tab` / `.slirn-stage4-hl-pane` 选择器存在
- [x] JS `switchHlTab` 选择器扩展为 stage3 ∪ stage4
- [x] JS action 路由含 `sv-stage4-hl-tab`
- [x] 状态记忆（`window._slirnSvHlTab`）跨 openProject 恢复
- [x] hl_index 已删时回退到第 1 条
- [x] Stage 3 tab 切换 0 破坏（回归）
- [x] Stage 4 编辑/预览/初剪按钮在 pane 内仍工作
- [x] pytest 1202 + 4 新增 = **1206 全过**
- [x] **7861 服务已重启**（PID 32140，11:56 启动）— 旧的 PID 2704 在 10:57 启动时还没有 tab 代码，用户截图即来自旧服务
- [x] `test_stage3_hl_tabs_wiring` 已同步更新为「逗号并列选择器」断言
- [x] 冒烟：实测渲染 2 条 hl → 2 个 tab 按钮 + 2 个 pane 容器 + 第 1 个 active

## 改动文件

| 文件 | 改动 |
|---|---|
| `slirn_home/short_video_ui.py` | L824-866：flat 列表 → tab bar + N 个 pane 容器（保留 `_render_stage4_clip_card` 0 改动） |
| `slirn_home/static/home.css` | L5604-5651：6 条 `.slirn-stage3-hl-*` 规则改为「stage3 ∪ stage4」逗号并列 |
| `slirn_home/static/short_video.js` | L186-212：扩展 `switchHlTab` / `_restoreHlTab` 选择器；L2035 区域加 `sv-stage4-hl-tab` action 路由 |
| `tests/test_short_video_stage4_rework.py` | + 4 个 tab 用例（渲染 / 接线 / 状态恢复 / 失效回退） |
| `tests/test_short_video_stage3_llm_clip.py` | `test_stage3_hl_tabs_wiring` 同步适配新选择器格式（逗号并列） |

## 关键设计决策

- **CSS 共享**：stage3 和 stage4 tab 用逗号并列选择器（一份 CSS 服务两套 class）—— 后续 stage5/stage6 改 tab 时只需加 class，无需复制 CSS
- **JS 选择器扩展**：`switchHlTab` / `_restoreHlTab` 同时 query 两套 class，互不破坏
- **状态记忆共享**：`window._slirnSvHlTab` 在 stage3 和 stage4 之间共用；项目 key + hl_index 维度足够（编号巧合一致就是好事）
- **per-hl 0 改动**：`per_hl` JSON 仍按 hl_index 独立存储；tab 切换纯前端 `display` 切换，不触发任何后端请求

## 数字

- pytest 1202 → **1206**（+ 4 新 tab 用例；0 回归）
- CSS 6 条规则 → 6 条共用规则（行数不变）
- JS 0 新增函数，仅扩展选择器 + 1 行 action 路由
- 7861 重启：从 PID 2704（旧代码）→ PID 32140（新代码）

## 风险 / 教训

- **后台服务不会自动 reload 代码**（已有 memory 警示 `slirn-css-launch-injection-restart`）—— 截图时 7861 还是上次启动的进程，UI 代码改了但浏览器看到的还是旧产物。下次改 Python + UI 一定要重启。
- **共享 CSS 选择器会破坏「断言单个 class 字面量」的老测试**——本次踩坑：`test_stage3_hl_tabs_wiring` 之前断言 `.slirn-stage3-hl-tab.active {` 字面量；改共享后必须更新测试为 `.slirn-stage3-hl-tab.active,` 形式（注意逗号位置）。

## 关联

- [[req-099-stage3-content-parse]] — **模式镜像**（JS 切换 + CSS 视觉 + 状态记忆）
- [[req-108-sv-stage4-prompt-edit-preview]] — 父任务（提供 `_render_stage4_clip_card` / per_hl 等基础）
- [[req-107-sv-stage7-actions-bar-wrap]] — grid auto-fill 教训（tab 化后非 grid，规则保留无害）
- [[slirn-css-launch-injection-restart]] — 改 CSS/UI 必须重启 7861
- [[slirn-dynamic-script-load-race]] — 改 JS 不需重启
