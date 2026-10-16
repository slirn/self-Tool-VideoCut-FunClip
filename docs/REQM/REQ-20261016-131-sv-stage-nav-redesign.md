# REQ-20261016-131 — 阶段导航重设计：去 ✓/·、状态用卡片背景

> 状态：✅ 已完成（2026-10-16）
> 范围：短视频项目左侧 7 阶段导航卡（长视频工作台不动）

## 1. 需求（用户原话）

> 在每个阶段前面已经有了数字，所以在文字区域不要显示数字加阶段名，直接显示阶段名，然后已经执行成功过的用绿色，然后阶段的显示数字不要被遮挡，执行成功后只显示绿色，不要逗号，不要对号。当前正在执行的用一个蓝色背景表示，也不要遮盖数字。未执行的按现在方式显示灰色背景和数字即可

## 2. 改动要点

| 维度 | 旧 | 新 |
|---|---|---|
| Mark (左圆) 内容 | 完成 `✓` / 失败 `!` / 否则数字 | **永远数字**（不被任何符号遮挡） |
| Text 区域 | `1 · 选择源视频`（数字 + · + 标题） | **`选择源视频`**（只剩标题；数字在 mark 里） |
| Done 态 | 只 mark 圆变绿 | **整个卡片浅绿**（`#d1fae5`）+ 边框 `#6ee7b7` |
| Current 态 | 只 mark 圆变蓝 | **整个卡片浅蓝**（`#dbeafe`）+ 边框 `#93c5fd` |
| Failed 态 | 只 mark 圆变红 | **整个卡片浅红**（`#fee2e2`）+ 边框 `#fca5a5` |
| Pending 态 | 透明卡 + 灰 mark | **不变**（按用户原话"按现在方式"） |
| Active 态 | 蓝边框 + 灰底 | **inset 3px 蓝条**（避免与 current 蓝卡重色） |

## 3. 关键文件

| 文件 | 改动 |
|---|---|
| [short_video_ui.py:1730-1745](short_video_ui.py#L1730) | `mark = number` 永远数字；`<strong>{title}</strong>` 去前缀 |
| [home.css:5454-5482](static/home.css) | active 改 box-shadow inset；新增 done/current/failed 卡片背景规则 |
| [tests/test_short_video_nav_and_refresh.py](tests/test_short_video_nav_and_refresh.py) | +3 测试（mark 数字 / 无 · / 三态卡片背景不同 / active inset 蓝条） |

## 4. 测试

- `test_short_video_stage_navigation_shows_one_pane`（既有）保持绿
- `test_short_video_stage_nav_no_checkmark_no_dot_in_text`（新）—— mark 是数字 "1"、strong 只有 "选择源视频"、无 `·`
- `test_short_video_stage_nav_state_card_bg`（新）—— done/current/failed 卡片 background 三色互不相同
- `test_short_video_stage_nav_active_no_blue_border`（新）—— active 用 box-shadow inset（不再用 border-color 蓝）

全量 1316 → 1319 passed（既有 + 新 3）。

## 5. 验收

- [x] 1319/1319 pytest 全过
- [ ] 7861 重启加载新代码（CSS 改动，按 [slirn-css-launch-injection-restart](slirn-css-launch-injection-restart.md)）
- [ ] 真实项目：左侧 7 阶段卡片
  - 完成阶段：浅绿卡 + 圆内显示数字（无 ✓）
  - 正在执行：浅蓝卡 + 圆内显示数字
  - 未执行：透明卡 + 灰圆
  - 标题区：只有阶段名（无 `1 · ` 前缀）

## 6. 不做的事

- ❌ 不动长视频工作台（`_wb_stage_states` / 长视频 .slirn-wb-* 样式）
- ❌ 不改 mark 圆大小（仍 24px）
- ❌ 不改 nav_items 列表结构 / 状态值（done/current/failed/pending）
- ❌ 不改 hover 行为（仍 `var(--bg-hover)` 8% 灰）
- ❌ 不改 active 的内容（仅改视觉为 inset 蓝条）

## 7. 关联

- [[req-098-six-stage-pipeline]] — 6 阶段管线建立（后扩 7 阶段）
- [[slirn-css-launch-injection-restart]] — CSS 必须重启 7861
