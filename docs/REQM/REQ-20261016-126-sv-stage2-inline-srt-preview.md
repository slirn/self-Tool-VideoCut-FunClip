# REQ-20261016-126 — Stage 2 提取字幕完成后 SRT 全文内联预览

> 状态：✅ 已完成（2026-10-16）
> 关联：[REQ-20261016-125](REQ-20261016-125-sv-stage2-progress-and-oom-fix.md)
> 关联：[REQ-20261013-110](REQ-20261013-110-sv-stage4-reorder-time-srtno.md)
> （4 字段 SRT 块视觉对齐来源）

## 1. 需求（用户原话）

> 「提取的字幕信息直接显在页面的下面，不要再点击按钮弹出」

截图 24.png 显示 Stage 2 卡片已就绪（127142 字、状态卡、日志可折叠），
但**红框标注的卡片下方大片空白**——字幕没有直接显示，用户还得点
「查看 raw.srt」按钮弹窗/新页。

## 2. 实现

| 位置 | 改动 |
|---|---|
| `slirn_home/short_video_ui.py` | 新增 `_fmt_srt_time_ms` / `_parse_srt_entries` / `_render_srt_preview`；`_stage2_extract` 在 `status=done` 时直接读 SRT 解析为 4 字段块，渲染到卡片下方；「查看 raw.srt」按钮改为 `<a href target=_blank>`（原文件下载兜底）；移除 data-action 弹窗 |
| `slirn_home/static/home.css` | 新增 `.slirn-stage2-srt-preview` / `.slirn-stage2-srt-rows`（max-height 500px + overflow-y auto）/ `.slirn-stage2-srt-row`（grid 三列：`#N` / `00:00:00,000 → 00:00:00,000` / 文字）/ `.slirn-stage2-srt-no/time/text` |
| `slirn_home/static/short_video.js` | 删除 `previewStage2Srt` 函数体 + 路由器中 `sv-stage2-preview-srt` 派发（死代码） |

**设计选择**（用户确认）：**全部条目一次性内联渲染**。1.7h 视频的
2574 行 SRT ≈ 80KB HTML——浏览器原生滚动，5xx 容器够用，无需
「加载更多」懒加载。

**视觉与 Stage 3/4 一致**：4 字段只读（`#N` / `00:00:00,000 → 00:00:00,000` /
文字），但**没有编辑/重排按钮**（Stage 2 不可写）。`@SRT#K` 是 Stage 4
编辑后的口径（原始 vs effective），Stage 2 是 raw 不需要这个字段，
所以只 3 字段。

**「查看 raw.srt」保留**作为下载原文件兜底（用户可右键另存为
/换行符处理），改为 `<a target=_blank>` 避免再走弹窗路由。

## 3. 测试（+3，全量 1304 passed）

- `test_parse_srt_entries_basic_and_fallback` — 标准 SRT 解析、坏块不抛
- `test_stage2_inline_srt_preview_after_extract` — done 状态 HTML 含预览面板
  + 4 字段行 + 行数摘要 + `data-action="sv-stage2-preview-srt"` 已移除
- `test_stage2_no_preview_when_not_done` — 未完成时无预览面板（无空壳）

## 4. E2E

7861 重启后用户真实项目（1.7h 立体声视频，2574 SRT 块）刷新页面
即可看见字幕全文内联在 Stage 2 卡片下方，500px 滚动容器，
首行 `#1 00:00:00,830 → 00:00:04,475 那我们有请 CY 老师给我们上课`。

## 5. 已知边界

- SRT 单条多行（某些 ASR 把一行文字拆多行）— `white-space: pre-wrap`
  保留换行；汇总字数只计纯文本（不重时空格）。
- SRT 文件 > 几 MB（极长视频）— 当前一次性 inline；如未来有 5+ 万行
  SRT 场景再考虑切换「全部 vs 折叠」开关（`details`/懒加载）。
- 预览面板在每次 openProject 都会重新读 SRT 解析；SRT < 1MB
  场景开销可忽略。
