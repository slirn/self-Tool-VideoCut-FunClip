# REQ-20260915-009 — 修复：任务卡片最小宽度不足，底部按钮文字折行

| 字段 | 值 |
|---|---|
| 编号 | REQ-20260915-009 |
| 日期 | 2026-09-15 |
| 优先级 | P3（视觉缺陷，用户报告） |
| 状态 | ✅ 已完成（验证记录见 §4） |
| 关联 | REQ-20260915-001（任务列表卡片视图） |
| 改动范围 | 仅 `slirn_home/static/home.css`（3 处规则）；Python/JS 零改动 |

## 1. 缺陷与根因

- 现象：任务列表卡片较窄时，底部操作行（📄 详情 / ✏️ 编辑 / ✂️ 剪辑 / 🗑️ 删除）
  放不下，按钮折行/文字换行，卡片底部参差不齐。
- 根因：`.slirn-task-grid` 用 `repeat(auto-fill, minmax(280px, 1fr))` —
  宽视口下多列卡宽被压到 ~280-300px；按钮行实测需要 ~290px + 卡片左右
  padding 32px → 卡片至少 ~322px 才放得下一行。`.slirn-task-actions`
  未禁 flex wrap，也未给按钮加 nowrap 保护。

## 2. 修复方案

| ID | 修复 |
|---|---|
| REQ-9.1 | 网格最小列宽 280px → **340px**（覆盖按钮行 + padding，留余量） |
| REQ-9.2 | `.slirn-task-actions` 加 `flex-wrap: nowrap`；按钮 `white-space: nowrap` + `flex-shrink: 0`（双保险：即使字体/文案变化也不折行，宁可卡片整体变宽） |

## 3. 验收标准

- [x] AC-1 宽视口（多列布局）下每个卡片 ≥340px，4 个操作按钮同一行
  （CDP 实测 4 按钮 offsetTop 相同）
- [x] AC-2 按钮文字单行显示（按钮高度 = 单行行高，无内部折行）
- [x] AC-3 现有 pytest 不受影响（纯 CSS）；截图存档

## 4. 验证记录（2026-09-15，CDP headless Chrome 1400px 宽）

- 多列布局下任务卡 `.slirn-task-card` 实测宽 ≥340px；
  每张卡 4 个操作按钮 `offsetTop` 全相同（同一行），
  按钮文本（含「📄 详情」等）均为单行
- 截图：`work/REQ-20260915-009-taskcard-minwidth/_shot_taskcards.png`
- pytest 105 passed；ruff 0 错（与 REQ-20260915-008 同批验证）

## 5. 非目标

- ❌ 不改卡片内部信息（名称/视频名超长仍省略号截断）
- ❌ 不做窄屏（手机）特殊处理 — 窄屏下网格自然单列，卡宽自适应
