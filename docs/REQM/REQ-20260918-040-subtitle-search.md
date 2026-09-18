# REQ-20260918-040 — 字幕内容搜索定位

## 背景与目标

字幕修订 / 切分修剪两阶段行数动辄上百，靠肉眼滚动找内容费时。本需求新增按内容片段搜索定位的能力。

## 验收

| # | 标准 | 状态 |
|---|---|---|
| 1 | 字幕修订面板渲染搜索条（输入框 + 计数 + 上/下一条按钮） | ✅ |
| 2 | 输入即显匹配数（如「人工智能」→ 2 处） | ✅ |
| 3 | 回车 → 定位第 1 条；再回车 → 第 2 条；末条后再回车 → 循环回第 1 条 | ✅ |
| 4 | Shift+回车 → 上一条 | ✅ |
| 5 | 零匹配 → 计数显示「0 处」+ toast 报错 | ✅ |
| 6 | 空词 → toast 提示输入 | ✅ |
| 7 | 切分面板：整段行按显示文本 + 子段行按子段文本；data-orig 兜底原文 | ✅ |
| 8 | 服务端行属性 data-text / data-orig 由 _esc 安全转义 | ✅ |

## 涉及文件

- `slirn_home/app.py` — 两阶段顶部搜索条 HTML + 行 data-text/data-orig 属性
- `slirn_home/static/router.js` — searchTextOf / searchQuery / searchCountSync / searchNavTo / revMarkSel / cutMarkSel
- `slirn_home/static/home.css` — `.slirn-search-q` / `.slirn-search-count` 样式
- `tests/test_batch_status.py` — REQ-040 服务端渲染断言

## E2E

`work/REQ-20260918-040/_e2e_040_046.py` 040 段：11/11 通过。
