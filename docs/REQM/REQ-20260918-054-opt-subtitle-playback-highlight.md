# REQ-20260918-054 — 优化字幕阶段播放时字幕行高亮

## 用户原话

> 优化字幕阶段，点击词频列表中的某一个词之后列出来了相关的 11 条字幕，这时点击一条字幕进行播放时，字幕应该进行高亮，这样方便跟踪正在播放字幕的位置

## 现状

- 优化字幕阶段已有独立视频播放器 `<video id="slirn-opt-player">` 与字幕列表 `#slirn-opt-list`（[slirn_home/app.py:1198-1227](slirn_home/app.py#L1198)）
- 已有交互 `playOptAt(tid, startMs)` [slirn_home/static/router.js:2276-2292](slirn_home/static/router.js#L2276)：点字幕行 → `currentTime = startMs/1000` + `play()`
- **但没有任何 timeupdate 跟随**，用户看不到当前播放到哪一行
- 字幕行 DOM 只有 `data-start-ms`、没有 `data-end-ms`（[slirn_home/app.py:1198-1199](slirn_home/app.py#L1198) 渲染漏写）
- 同款 timeupdate 高亮模式已在 [slirn_home/static/router.js:2392-2509](slirn_home/static/router.js#L2392) 字幕修订 (`bindRevPlayer`) 和切分修剪 (`bindCutPlayer`) 实现

## 验收标准

| # | 验收点 | 验证方式 |
|---|---|---|
| 1 | `_line_html` 渲染 `slirn-opt-row` 时输出 `data-end-ms` 属性，值 = `seg.end_ms` | E2E：DOM 检查 |
| 2 | 点击字幕行后，**当前播放行立即高亮**（不需等下一个 timeupdate） | E2E：点行后立即断言 active 数 = 1 且是目标行 |
| 3 | 视频播放过程中，高亮自动跟随 currentTime 移动 | E2E：`v.currentTime += 1` 后断言 active 行更新 |
| 4 | 段间缝隙（tms 越过末行尾但未进下一行）保持前一行高亮（不闪烁） | 与 cut/rev 一致，依赖 setActive 逻辑 |
| 5 | `.slirn-opt-row.active` 视觉比 `.slirn-opt-row-target` 更强（蓝色边压过 target 橙色） | E2E：计算样式断言 `border-left-color` 含 #2563eb |
| 6 | 暂停/视频结束时高亮保留在最后位置（不消失） | E2E：pause 后 active 仍在 |
| 7 | 不破坏现有 REQ-20260918-052 词 target 高亮（淡黄底 + 左色条） | 视觉/回归 |
| 8 | 不破坏现有 REQ-20260918-050 词频分页 | 回归 |
| 9 | 不引入新依赖、不动 funclip/ 上游 | 评审 |
| 10 | 与 `bindRevPlayer` / `bindCutPlayer` 视觉/行为一致（class 用 `.active`） | 评审 |

## 范围

**In**：
- [slirn_home/app.py](slirn_home/app.py) `_line_html` 补 `data-end-ms`
- [slirn_home/static/router.js](slirn_home/static/router.js) 新增 `bindOptPlayer` + `optPlayerHighlightNow`，`playOptAt` 调用
- [slirn_home/static/home.css](slirn_home/static/home.css) `.slirn-opt-row.active` 样式

**Out**：
- 切分/修订阶段的同类实现（已有）
- 视频源 / API 改动
- 新依赖

## 设计摘要

详见 [docs/design/DESIGN-20260918-054-opt-subtitle-playback-highlight.md](../design/DESIGN-20260918-054-opt-subtitle-playback-highlight.md)

核心逻辑：复用 `bindRevPlayer` / `bindCutPlayer` 的 timeupdate 高亮模式，找到 `tms ∈ [s0, e0)` 的行 → 加 `.active` class，段间缝隙保留前一行。点行后立即按 currentTime 重算（不等 timeupdate）。

## 风险

详见设计文档"风险与边界"一节。简版：
- 切任务 video 元素被替换 → `v.dataset.bound` 防重复绑
- 过滤态下 hidden 行被加 active（无视觉副作用）
- 段间缝隙保持 lastHit（与 cut/rev 行为一致）
