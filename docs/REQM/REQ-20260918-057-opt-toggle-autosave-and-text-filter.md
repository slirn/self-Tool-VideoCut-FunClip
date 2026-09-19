# REQ-20260918-057 — 优化字幕·toggle 自动保存 + 词频文字过滤

## 用户原话

> 点击对号后变为叉时也要进行保存，并且在过滤区域要添加文字输入的过滤方式，方便找指定的词

## 现状

- `optOccToggle` [slirn_home/static/router.js:2148-2156](slirn_home/static/router.js#L2148) 切 ✕/✓ 只改 DOM + 调 `optOccMarkReviewed`，**未触发保存**
- 词频过滤 chips [slirn_home/app.py:1162-1167](slirn_home/app.py#L1162) 只有 3 个状态按钮（全部/未完成/已完成），**无文字输入过滤**

## 验收标准

### REQ-057A · toggle 自动保存

| # | 验收点 | 验证方式 |
|---|---|---|
| 1 | 点 ✕/✓ 切换后调 `optAutoSave(tid)` 走自动保存 | E2E |
| 2 | 磁盘 `optimize_subtitle.json` 落盘 `applied`/`reviewed` 正确 | E2E + 文件读 |
| 3 | 复用 REQ-056 并发锁（连续点击不并发） | E2E |
| 4 | 与回车自动保存走同一条路径 | 评审 |

### REQ-057B · 词频文字过滤

| # | 验收点 | 验证方式 |
|---|---|---|
| 1 | `#slirn-opt-word-text` 输入框存在 + placeholder | E2E |
| 2 | 输入文本 → 250ms 防抖 → 词频区只显示 `data-word` 含该文本的行 | E2E |
| 3 | 与状态过滤 AND 组合 | E2E |
| 4 | 大小写不敏感 | E2E |
| 5 | 分页重置到第 1 页 | E2E |
| 6 | ✕ 清空按钮（非空时显示）恢复全部 | E2E |
| 7 | 与 REQ-052 点词上下文模式正交 | E2E |
| 8 | wb 重渲染后 input 事件重新绑定（幂等） | 评审 |
| 9 | 无后端改动 | 评审 |

## 范围

**In**：
- [slirn_home/app.py:1162-1167](slirn_home/app.py#L1162) `word_filter_html` 追加 input + 清空按钮
- [slirn_home/static/router.js](slirn_home/static/router.js) `optOccToggle` 末尾加 `optAutoSave`；新增 `optWordTextFilter` + `bindOptWordTextFilter`；`setupOptWordsPagination` + `optWordFilterBtn` 末尾调用；新增 `data-action="opt-word-text-clear"` 委托
- [slirn_home/static/home.css](slirn_home/static/home.css) `.slirn-opt-word-text-filter` 样式

**Out**：
- 不做拼音/同义词/正则匹配
- 不做匹配片段高亮
- 不持久化文字过滤值
- 不改 REQ-052 点词上下文模式

## 设计摘要

详见 plan 文件 [tingly-dancing-kahan.md](tingly-dancing-kahan.md)

核心：
- **A**：复用 `optAutoSave`（REQ-056 已实现含并发锁），在 `optOccToggle` 末尾调一次
- **B**：纯前端 DOM 过滤；250ms 防抖；与状态过滤 AND 组合；分页联动

## 风险

- toggle 频繁点击 → 复用并发锁
- 文字过滤与上下文模式正交（DOM 区域不同）
- 防抖 250ms 故意丢弃中间态（性能优先）
- 关键词 XSS → `_esc(w)` 已转义；input.value 不参与 innerHTML
