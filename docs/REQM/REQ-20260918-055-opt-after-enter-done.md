# REQ-20260918-055 — 优化字幕·输入框回车确认完成

## 用户原话

> 在优化字幕阶段，对于已经修正的词，在输入框中如果按回车键的话，则自动设置当前这次更正确认为已完成

## 现状

- 优化字幕阶段的"替换值"输入框 `.slirn-opt-after` [slirn_home/app.py:1186](slirn_home/app.py#L1186) 渲染在 `.slirn-opt-occ` 内
- 当前"完成"语义：`data-reviewed="1"`（已处理，计入词行 x/y 进度），由以下路径触发：
  - 点 ✕/✓ 采纳按钮 → `optOccToggle` → `optOccMarkReviewed` [slirn_home/static/router.js:2113-2126](slirn_home/static/router.js#L2113)
  - 编辑 input → input 事件 → `optOccMarkReviewed` [slirn_home/static/router.js:791-794](slirn_home/static/router.js#L791)
- **目前没有任何 Enter 键处理**（cut/rev keydown 监听对 INPUT 焦点显式让位）

## 验收标准

| # | 验收点 | 验证方式 |
|---|---|---|
| 1 | 焦点在 `.slirn-opt-after` 输入框 → 按 Enter → 当前 occ `data-applied="1"` | E2E |
| 2 | 同上 → 当前 occ `data-reviewed="1"` | E2E |
| 3 | 同上 → 所属词行 `.slirn-opt-word-prog` x/y +1 | E2E |
| 4 | 同上 → 视觉上有 `.slirn-opt-occ-just-done` 反馈（600ms 内） | E2E + CSS 视觉 |
| 5 | 输入框 value 为空 → 按 Enter → toast 警告"替换值为空"，无其他副作用 | E2E |
| 6 | 焦点不在 `.slirn-opt-after`（其他 input/textarea）→ 按 Enter 不被拦截 | E2E |
| 7 | 修饰键（Ctrl/Alt/Meta/Shift）+ Enter 不被拦截 | E2E |
| 8 | 已 reviewed=1 重复按 Enter → 无副作用（幂等） | E2E |
| 9 | 不破坏 cut/rev 自定义快捷键（行 3142 INPUT 让位） | 评审 + E2E |
| 10 | 不引入新依赖、不动 funclip/ 上游 | 评审 |

## 范围

**In**：
- [slirn_home/static/router.js](slirn_home/static/router.js) 新增 `optOccEnterConfirm` + document keydown 委托
- [slirn_home/static/home.css](slirn_home/static/home.css) 新增 `.slirn-opt-occ-just-done` 动画

**Out**：
- 不自动跳到下一条 occ 输入框（用户原话未提；如需后续 REQ）
- 不改 `optSave` 落盘逻辑
- 不改 `optOccToggle` 按钮行为
- 不在 input 上挂 keyup

## 设计摘要

详见 [docs/design/DESIGN-20260918-055-opt-after-enter-done.md](../design/DESIGN-20260918-055-opt-after-enter-done.md)（在 plan 文件内）

## 风险

详见 plan "风险与边界"。简版：
- document 级委托误吞其他 Enter → 严格过滤（key='Enter' + class 匹配）
- 与 cut/rev 快捷键冲突 → 已让位（行 3142）
- wb 重渲染后元素替换 → document 级委托不受影响
