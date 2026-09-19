# REQ-20260918-059 — colEnhance · 同一 pane 内 hint 合并为一个折叠

## 用户原话

> 好像每次保存都会生成一个说明。这个不断在撑高页面

## 现状

[slirn_home/static/router.js:1088-1099](slirn_home/static/router.js#L1088) `colEnhance` 当前只把**相邻**的 `.slirn-form-hint` 并成一组：

```js
Array.prototype.forEach.call(scope.querySelectorAll('.slirn-wb-pane .slirn-form-hint'), function(h) {
  if (h.dataset.slirnCol !== undefined) return;
  colWrap(h, colPaneKey(h, 'hint'), 'ℹ️ 说明');
  var wrapH = h.parentNode;
  var nxt = wrapH.nextElementSibling;
  while (nxt && ...slirn-form-hint... && ...undefined...) {
    nxt.dataset.slirnCol = '1';
    wrapH.appendChild(nxt);
    ...
  }
});
```

**问题**：wb HTML 中同一个 `.slirn-wb-pane` 内如果 2 个 hint 被 `opt-words` / `opt-list` 等元素隔开——colEnhance 不会合并——产生**多个独立 "📋 说明" 折叠块**。

实测 wb HTML（task 20260915-001）hint 分布：

| pane | hint 数 |
|---|---|
| assets | 0 |
| subtitle | 1 |
| subtitle_review | 0 |
| rough_cut | 3（连续 → 已合并为 1） |
| rough_compose | 1 |
| fine_review | 2（被 opt-words/opt-list 隔开 → **2 个独立折叠**） |
| fine_cut | 1 |
| mux | 1 |
| **总计独立折叠块** | **6 个** |

但用户截图看到 **18+ 个独立"📋 说明"折叠**——说明实际 wb 中某些 pane 内有更多 hint 隔开。

## 验收标准

| # | 验收点 | 验证方式 |
|---|---|---|
| 1 | 同一 `.slirn-wb-pane` 内所有 `.slirn-form-hint` 合并为单个折叠 | E2E |
| 2 | 不同 pane 仍是独立折叠（互不影响） | E2E |
| 3 | 折叠头文字 "📋 说明" 保留 | E2E |
| 4 | 折叠内 hint 显示顺序与 wb HTML DOM 顺序一致 | E2E |
| 5 | wb 重渲染后行为稳定（不累积 wrap） | 评审 |
| 6 | 与现有 `slirn-sub-meta` 等其他 col 折叠正交 | 评审 |
| 7 | 无后端改动 | 评审 |

## 方案

修改 `colEnhance` 的 hint 处理逻辑：**第一遍扫所有 hint，按 pane 分组；第二遍每组只 wrap 第一个**，把整组 hint 都塞进第一个的 wrap。

```js
// 替代原 while 循环逻辑
var hints = Array.prototype.slice.call(scope.querySelectorAll('.slirn-wb-pane .slirn-form-hint'));
var paneMap = {};  // pane_id → [hint1, hint2, ...]
hints.forEach(function(h) {
  if (h.dataset.slirnCol !== undefined) return;
  var pane = h.closest('.slirn-wb-pane');
  var pid = pane ? (pane.id || 'x') : 'x';
  (paneMap[pid] = paneMap[pid] || []).push(h);
});
Object.keys(paneMap).forEach(function(pid) {
  var group = paneMap[pid];
  if (group.length === 0) return;
  var first = group[0];
  colWrap(first, colPaneKey(first, 'hint'), 'ℹ️ 说明');
  var wrap = first.parentNode;  // slirn-col wrap
  for (var i = 1; i < group.length; i++) {
    group[i].dataset.slirnCol = '1';
    wrap.appendChild(group[i]);  // 把后续 hint 挪进同一 wrap
  }
});
```

## 范围

**In**：
- [slirn_home/static/router.js:1088-1099](slirn_home/static/router.js#L1088) `colEnhance` hint 处理段

**Out**：
- ❌ 不改 `.slirn-sub-meta` / 词频 chips 等其他 col 折叠逻辑
- ❌ 不改 wb HTML（不动 app.py）
- ❌ 不动 hint 的视觉/CSS

## 风险

- 跨 pane 错误合并 → 用 `pane.closest('.slirn-wb-pane')` 限定范围
- 顺序错乱 → 用 Array 收集保持 DOM 顺序
- 旧 hint 残留 → 仍走 dataset.slirnCol 标记
- 与 REQ-057B 文字过滤、REQ-058 input wrap 正交 → 互不影响