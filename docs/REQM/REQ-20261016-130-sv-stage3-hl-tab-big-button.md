# REQ-20261016-130 — Stage 3 高亮 tab 做大按钮 + 完整标题

> 状态：✅ 已完成（2026-10-16，commit 待 push）
> 范围：**仅 Stage 3 高亮 tab bar**（不涉及 Stage 4；CSS 共享，Stage 4 视觉同步但 Python 截断未改）
> 改动：1 文件 Python + 1 文件 CSS，0 新测试（既有 3 个测试保持绿）

## 1. 需求（用户原话）

> 把每个短视频片段对应的标题展现完整，做成一个大按钮，这样可以看见完整的标题信息，点击之后下面显示当前短视频片段对应的字幕信息

用户 3 项回答：
- 位置：**Stage 3 高亮列表**（不是 Stage 4、不是重排 tab 栏）
- 标题可编辑：**保留 inline 编辑**（按钮 = input 形态可输入可点击）
- 默认行为：**只展开当前激活那条**（互斥）

## 2. 现状与改动

### 2.1 改动点（2 处）

| 文件 | 旧 | 新 |
|---|---|---|
| [short_video_ui.py:600-605](short_video_ui.py#L600) | 标题截到 10 字 + `…`、纯文本 `#N ` 前缀 | 完整标题 + 3 个 span 包裹（no/title/arrow） |
| [home.css:5718-5775](static/home.css) | 12px 字体、6px padding、透明 tab 样式 | 14px 字体、10px padding、可见边框 + 背景卡片样式 |

### 2.2 视觉/行为变化

- **完整标题可见**：tab 标签直接显示标题全文（wrapping to 多行），不再 `…` 截断
- **大按钮**：font-size 12→14px，padding 6/12→10/14px，min-width 240px / max-width 360px，可见 1px 边框 + 白色背景
- **箭头 affordance**：`▾` 箭头在 active 态旋转 180°，暗示"展开/折叠"
- **行为不变**：点击 tab 仍调 `sv-stage3-hl-tab` → `switchHlTab` 互斥切换 pane（active 单选）

### 2.3 Stage 4 影响

按用户回答"只改 Stage 3"原则：仅 Python 改 Stage 3，CSS 共享导致 Stage 4 视觉**也变大了**（大按钮样式）。Stage 4 Python 截断未改（标题仍可能 `…`）。如需 Stage 4 标题也完整，把 [short_video_ui.py:986](short_video_ui.py#L986) 同样改 spans 即可。

## 3. 测试

既有 3 测试保持绿（1316/1316 全过）：
- `test_stage3_ui_tabs_per_hl`（首签 active、`#1`/`#2` 出现）
- `test_stage3_ui_tab_label_truncation`（现反而验证完整标题在 title 属性 + input 里）
- `test_stage3_hl_tabs_wiring`（CSS 选择器 / JS 分发 / 横滚保留）

> 注：原 `test_stage3_ui_tab_label_truncation` 名字保留但语义反转（从"必须截断" → "必须不截断"），用户原意改。

## 4. 验收

- [x] 1316/1316 pytest 全过
- [ ] 7861 重启（CSS 改动 → 必须重启，按 [slirn-css-launch-injection-restart](slirn-css-launch-injection-restart.md)）
- [ ] 真实项目：8+ 高亮的 Stage 3 tab 全部显示完整标题；点击切换互斥

## 5. 不做的事

- ❌ 不动 Stage 4 Python（按用户回答"只 Stage 3"）
- ❌ 不加新端点 / 不加新 JS action（复用 sv-stage3-hl-tab）
- ❌ 不重做 tab 互斥逻辑（已由 switchHlTab 实现）

## 6. 关联

- [[req-099-stage3-content-parse]] — Stage 3 整体设计 + tab 互斥实现
- [[req-109-sv-stage4-hl-tabs]] — Stage 4 tab 视觉（CSS 共享）
- [[git-partial-staging-mixed-reqs]] — 本次 hunk-split 拆 REQ-130 vs REQ-116/117
