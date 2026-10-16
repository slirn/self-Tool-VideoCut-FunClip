# REQ-20261016-132 — Stage 5 视频预览改成可拖动浮窗

> 状态：✅ 已完成（2026-10-16）
> 范围：仅 Stage 5 粗剪成品的视频预览（长视频工作台不动；Stage 6/7 也不动）

## 1. 需求（用户原话）

> 在粗剪合成后，显示区域用列表展现，然后要通过点击预览的方式弹出视频播放窗口，并且视频窗口可以拖动，不要遮住父级页面，并且窗口大小可以调整

三件事：
1. **列表展现** — 已在 [REQ-117](REQ-20261013-117-sv-stage5-empty-hint.md) 完成（每条 clip 1 张卡）
2. **点击预览弹窗** — 本次实现（点击「▶ 预览」按钮 → 弹浮窗）
3. **浮窗可拖动 + 不遮父页 + 调大小** — 本次实现

## 2. 关键设计决策

| 维度 | 选型 | 理由 |
|---|---|---|
| 弹窗类型 | 单一浮窗（`position: fixed`） | 不用 modal overlay — 用户需继续操作父页面 |
| Z-index | 9500 | 低于 modal(10000) 但高于 stage 卡（不遮 modal） |
| 拖动 | 标题栏 `mousedown/mousemove/mouseup` | 与现有 CSS 风格一致；不依赖第三方 |
| 调大小 | CSS `resize: both` + 右下三角视觉 | 浏览器原生支持，零依赖 |
| 视频源 | 同源 `<video controls>` + `preload="metadata"` | 自动播放 + 用户可控 |
| 二次点击同按钮 | 关闭 | UX 友好（不用去找 × 按钮） |
| ESC 关闭 | 支持 | 习惯 |
| Lazy 创建 | `ensureSvVideoPopup()` 首次点按钮时插到 `document.body` | 不污染 HTML 模板，避免 7861 启动期提前加载 |

## 3. 关键文件改动

| 文件 | 改动 | 行数 |
|---|---|---|
| [short_video_ui.py:1127-1140](slirn_home/short_video_ui.py#L1127) | inline `<video>` → 「▶ 预览」按钮（带 data-url / data-clip-index / data-clip-name） | +10 / -1 |
| [home.css:6426-6515](slirn_home/static/home.css#L6426) | 新增 `.slirn-sv-video-popup` + head/body/resize/close 样式块 | +90 |
| [short_video.js:1310-1408](slirn_home/static/short_video.js#L1310) | `ensureSvVideoPopup` + `bindSvPopupDrag` + `svStage5Preview` | +98 |
| [short_video.js:2553-2554](slirn_home/static/short_video.js#L2553) | 路由派发 `action === 'sv-stage5-preview'` | +2 |
| [tests/test_short_video_stage5_video_popup.py](tests/test_short_video_stage5_video_popup.py) | 6 个新测试 | +133 |

## 4. 测试

- `test_stage5_clip_no_inline_video` — Stage 5 面板无 `<video controls`
- `test_stage5_preview_button_has_required_attrs` — data-url/data-clip-name 全齐
- `test_stage5_no_url_shows_placeholder_not_button` — 无 URL → 「未生成」占位
- `test_short_video_js_dispatches_preview_action` — 路由 + 函数
- `test_short_video_js_defines_video_popup_factory` — `ensureSvVideoPopup` + `bindSvPopupDrag`
- `test_css_video_popup_is_floating_draggable_resizable` — CSS fixed + resize both + cursor move + z-index 9500

全量 1319 → 1325 passed。

## 5. 验收

- [x] 1325/1325 pytest 全过
- [x] 7861 重启加载 CSS
- [ ] 真实项目：Stage 5 列表点「▶ 预览」→ 浮窗右上角弹出
- [ ] 拖动标题栏 → 浮窗跟着鼠标走（边界不超 viewport）
- [ ] 拖浮窗右下角三角 → 视频区跟着拉宽/拉高
- [ ] 点 × 或按 ESC → 关闭，父页面继续可用

## 6. 不做的事

- ❌ 不动 Stage 4 / 6 / 7 视频（Stage 6/7 当前没有视频播放器）
- ❌ 不动长视频工作台（slirn-cut-player-wrap 等）
- ❌ 不改 `_file_url` 端点（预览按钮直接复用现有 video URL）
- ❌ 不加全屏 overlay（用户原话「不要遮住父级页面」）
- ❌ 不加触摸事件（暂只支持鼠标；移动端用原生 controls 自带的全屏按钮）

## 7. 关联

- [[req-117-sv-stage5-empty-hint]] — Stage 5 列表卡结构基础
- [[slirn-css-launch-injection-restart]] — CSS 改动必须重启 7861
- [[slirn-dynamic-script-load-race]] — 改 JS 不用重启 7861（但本次顺带重启保险）
