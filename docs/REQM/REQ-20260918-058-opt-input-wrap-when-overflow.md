# REQ-20260918-058 — 优化字幕·替换输入框溢出自动换行

## 用户原话

> 如果要替换的输入框中的内容很多，在输入框的当前宽度下无法显示，则把输入框调整到下一行，使输入框中的文字能够完全显示出来

## 现状

[slirn_home/static/home.css:2107-2112](slirn_home/static/home.css#L2107)

```css
.slirn-opt-occ input.slirn-opt-after {
  flex: 1 1 auto; min-width: 0;
  ...
}
```

当前 `.slirn-opt-occ` 是 `inline-flex`，input 撑不开就压缩到 0 — 文字溢出但视觉上看不到。
用户选择方案：**保留 input，但 flex-wrap 换到下一行**（见 plan 时的 AskUserQuestion 答复）。

## 验收标准

| # | 验收点 | 验证方式 |
|---|---|---|
| 1 | 替换文字可在容器内显示（如 "神经网络" 完整显示） | 真机手测 |
| 2 | input 内容超长时，自动 wrap 到下一行（占整行 100% 宽度） | 真机手测 |
| 3 | 短替换词不强制换行（保持原来的横排布局） | 真机手测 |
| 4 | 行高/对齐不破坏（toggle 按钮位置正常） | 真机手测 |
| 5 | wb 重渲染后行为正常（重测时仍生效） | 评审 |
| 6 | 无后端改动 | 评审 |

## 方案

**前端方案**：CSS `flex-wrap: wrap` + JS 检测 `input.scrollWidth > input.clientWidth` 时给 `.slirn-opt-occ` 加 `wrapped` 类，让 input 的 `flex-basis` 强制变 100%。短词正常横排，长词自动下一行。

```css
.slirn-opt-occ { flex-wrap: wrap; }
.slirn-opt-occ.wrapped { /* 触发条件后 */ }
.slirn-opt-occ.wrapped input.slirn-opt-after {
  flex-basis: 100%;  /* 整行 */
  order: 1;
}
```

JS 监听 `input` 输入事件 + `wb 重渲染后` 初始化检测。

## 范围

**In**：
- [slirn_home/static/home.css:2098-2114](slirn_home/static/home.css#L2098) `.slirn-opt-occ` 加 `flex-wrap: wrap`；新增 `.slirn-opt-occ.wrapped input` 样式
- [slirn_home/static/router.js](slirn_home/static/router.js) 新增 `optInputOverflowCheck()` + `bindOptInputOverflow()`；wb 重渲后调用

**Out**：
- ❌ 不改 `<input>` 为 `<textarea>`（用户选 flex-wrap 方案）
- ❌ 不做横向滚动
- ❌ 不改 .slirn-opt-occ 其它样式

## 风险

- wrap 后 toggle 按钮位置变化 → `.slirn-opt-occ` 本来就是水平排列，wrap 不影响按钮位置
- input.value 变化时重新检测 → 防抖 100ms
- 测量 scrollWidth 在 display:none 状态可能不准确 → wb 渲染完后做一次检测即可
- 与 REQ-052 点词上下文模式正交 → 仅作用于 input 自身