# DESIGN-20260918-059 — colEnhance 同一 pane hint 合并

## Context

[REQ-20260918-059](../REQM/REQ-20260918-059-col-enhance-group-hints-per-pane.md)

wb HTML 中同一 `.slirn-wb-pane` 内如果有 2+ 个 `.slirn-form-hint`，当前 colEnhance 只合并**相邻**的——被其他元素（opt-words / opt-list 等）隔开时各自成组，产生多个 "📋 说明" 折叠块，撑高页面。

## 现状代码

[slirn_home/static/router.js:1088-1099](slirn_home/static/router.js#L1088)

```js
Array.prototype.forEach.call(scope.querySelectorAll('.slirn-wb-pane .slirn-form-hint'), function(h) {
  if (h.dataset.slirnCol !== undefined) return;
  colWrap(h, colPaneKey(h, 'hint'), 'ℹ️ 说明');
  var wrapH = h.parentNode;
  var nxt = wrapH.nextElementSibling;
  while (nxt && nxt.classList && nxt.classList.contains('slirn-form-hint')
         && nxt.dataset.slirnCol === undefined) {
    nxt.dataset.slirnCol = '1';
    wrapH.appendChild(nxt);
    nxt = wrapH.nextElementSibling;
  }
});
```

## 设计

**核心**：第一遍按 pane 分组 hint；第二遍每组 wrap 第一个，把全组挪进第一个的 wrap。

```js
// 第一遍：扫所有 hint，按 pane 分组
var paneMap = Object.create(null);
Array.prototype.forEach.call(
  scope.querySelectorAll('.slirn-wb-pane .slirn-form-hint'),
  function(h) {
    if (h.dataset.slirnCol !== undefined) return;
    var pane = (h.closest && h.closest('.slirn-wb-pane')) || null;
    var pid = pane && pane.id ? pane.id : 'x';
    (paneMap[pid] = paneMap[pid] || []).push(h);
  }
);
// 第二遍：每组 wrap 第一个，后续挪进同一 wrap
Object.keys(paneMap).forEach(function(pid) {
  var group = paneMap[pid];
  if (!group.length) return;
  var first = group[0];
  colWrap(first, colPaneKey(first, 'hint'), 'ℹ️ 说明');
  var wrap = first.parentNode;
  for (var i = 1; i < group.length; i++) {
    group[i].dataset.slirnCol = '1';
    wrap.appendChild(group[i]);
  }
});
```

**关键**：
1. paneMap 用 `Object.create(null)` 避免原型污染
2. `closest('.slirn-wb-pane')` 限定范围
3. 保留 `colPaneKey(first, 'hint')` 作为 colState key（按 pane 区分折叠状态）

## 实施步骤

1. **改 colEnhance**：替换 hint 处理段为新分组逻辑
2. **回归验证**：
   - `node --check router.js`
   - pytest 全部通过
   - 单元测试：构造 wb DOM（每个 pane 含 2 个被隔开的 hint），验证只生成 1 个 wrap per pane

## 关键文件

| 文件 | 改动 |
|---|---|
| [slirn_home/static/router.js:1088-1099](slirn_home/static/router.js#L1088) | 替换 hint 处理段为 pane 分组合并 |

## 验证

### 静态
- `node --check router.js`
- pytest 全过

### 单元测试（`work/_archive/req059_col_enhance_unit.js`）
- 构造 8 个 pane，每 pane 0-3 个 hint（部分 hint 被隔开元素分开）
- 跑 colEnhance
- 断言：每个 pane 内只生成 1 个 `.slirn-col` wrap
- 断言：所有 hint 都迁移到对应 wrap 内

### 真机手测
- 重启 slirn
- 进 wb → 数"📋 说明"折叠块数
- 多次保存 → 折叠块数应稳定不变

## 风险

| 风险 | 处理 |
|---|---|
| 跨 pane 误合并 | `closest('.slirn-wb-pane')` 限定 |
| DOM 顺序错乱 | Array 保留遍历顺序 |
| 与 sub-meta 折叠冲突 | sub-meta 走独立 singles 列表 |
| wb 重渲染累积 | dataset.slirnCol 防重 wrap |