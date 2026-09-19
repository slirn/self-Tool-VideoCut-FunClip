# DESIGN-20260918-050 — 优化字幕 · 不明确字词频次列表分页

## Context

REQ-20260918-050 — 「优化字幕」页的词频列表（#slirn-opt-words）项数可达 70+，全部堆在一个 `flex-wrap` 容器里难以浏览。

## 设计

### 客户端分页（不动后端）

理由：
1. 词频数小（< 200），HTML 体积可接受
3. 与过滤（全部 / 未完成 / ✅ 已完成）天然耦合 — 分页必须在过滤之后跑（按可见项分页）

### 改动一览

| 文件 | 改动 |
|---|---|
| [home.css](slirn_home/static/home.css) | `#slirn-opt-words` 改为 grid 2 列；新增 `.slirn-opt-word-hidden` 隐藏类；新增 `.slirn-opt-words-pager` 分页控件样式 |
| [router.js](slirn_home/static/router.js) | 新增 `setupOptWordsPagination()` / `paginateOptWords(resetPage)`；`optWordFilterBtn()` 末尾调 paginate |

### CSS

```css
/* 词频列表固定 2 列网格（REQ-20260918-050：每行 2 条） */
#slirn-opt-words {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 4px 12px;
}
#slirn-opt-words .slirn-opt-word-hidden { display: none; }

/* 分页控件 */
.slirn-opt-words-pager {
  display: flex; align-items: center; justify-content: center; gap: 12px;
  margin-top: 10px; padding: 6px 10px;
  background: var(--card-bg-2, rgba(0,0,0,0.02));
  border-radius: 6px; font-size: 12px; color: var(--text-secondary);
}
.slirn-opt-words-pager button {
  border: 1px solid var(--border);
  background: var(--input-bg, #fff);
  padding: 3px 10px; border-radius: 4px;
  cursor: pointer; font-size: 12px;
}
.slirn-opt-words-pager button:disabled {
  opacity: 0.4; cursor: not-allowed;
}
.slirn-opt-words-pager .slirn-opt-page-info {
  font-weight: 600; color: var(--text-primary);
}
```

### JS

```javascript
// REQ-20260918-050：词频列表分页（每页 20 条 = 每行 2 条 × 10 行）
var OPT_WORDS_PAGE_SIZE = 20;
var optWordsCurrentPage = 1;

function setupOptWordsPagination() {
  var box = document.getElementById('slirn-opt-words');
  if (!box) return;
  // 在 box 之后插入分页控件
  if (!box.nextElementSibling || !box.nextElementSibling.classList.contains('slirn-opt-words-pager')) {
    var pager = document.createElement('div');
    pager.className = 'slirn-opt-words-pager';
    pager.setAttribute('data-opt-pager', '');
    box.parentNode.insertBefore(pager, box.nextSibling);
  }
  paginateOptWords(true);
}

function paginateOptWords(resetPage) {
  var box = document.getElementById('slirn-opt-words');
  if (!box) return;
  var all = Array.prototype.slice.call(box.querySelectorAll('.slirn-opt-word'));
  // 过滤当前可见（与过滤按钮同步；过滤由 display 控制）
  var visible = all.filter(function(el) {
    return el.style.display !== 'none' && !el.classList.contains('slirn-opt-word-hidden');
  });
  var total = visible.length;
  var pages = Math.max(1, Math.ceil(total / OPT_WORDS_PAGE_SIZE));
  if (resetPage) optWordsCurrentPage = 1;
  if (optWordsCurrentPage > pages) optWordsCurrentPage = pages;

  // 隐藏非当前页
  visible.forEach(function(el, i) {
    var pageIdx = Math.floor(i / OPT_WORDS_PAGE_SIZE);
    if (pageIdx === optWordsCurrentPage - 1) {
      el.classList.remove('slirn-opt-word-hidden');
    } else {
      el.classList.add('slirn-opt-word-hidden');
    }
  });

  // 更新分页控件
  var pager = box.nextElementSibling;
  if (!pager || !pager.classList.contains('slirn-opt-words-pager')) return;
  if (total <= OPT_WORDS_PAGE_SIZE) {
    pager.style.display = 'none';
    pager.innerHTML = '';
    return;
  }
  pager.style.display = '';
  pager.innerHTML =
    '<button data-action="opt-page-prev"' + (optWordsCurrentPage <= 1 ? ' disabled' : '') + '>‹ 上一页</button>'
    + '<span class="slirn-opt-page-info">第 ' + optWordsCurrentPage + ' / ' + pages + ' 页 · 共 ' + total + ' 个词</span>'
    + '<button data-action="opt-page-next"' + (optWordsCurrentPage >= pages ? ' disabled' : '') + '>下一页 ›</button>';
}

// 接到 click 委托
// data-action="opt-page-prev" → if currentPage > 1: currentPage--; paginateOptWords(false)
// data-action="opt-page-next" → if currentPage < pages: currentPage++; paginateOptWords(false)
```

### 与现有过滤的协作

`optWordFilterBtn()` 末尾追加：
```javascript
optWordCurrentPage = 1;  // ← 重置
paginateOptWords(true);   // 按过滤后可见项重新分页
```

`optWordCurrentPage` 变量已存在（同命名空间）。

### 初始化时机

- 页面首次渲染完成 → 调 `setupOptWordsPagination()`
- 监听 `slirn:task-loaded` 事件（已有的 workbench 加载事件），再次 setup（兜底重渲染场景）

## 测试

| 检查 | 方法 |
|---|---|
| CSS 2-col | DevTools 截图：词项 2 列对齐 |
| 总数 ≤ 20 不显示分页 | 手动挑一个短任务验证 |
| 总数 > 20 显示分页 | 当前 09-13 任务 (~70 个词) 验证 |
| 翻页 | 点 ‹ › 按钮，验证可见项切换 |
| 过滤联动 | 切到「未完成」/「✅ 已完成」，分页重置到第 1 页 + 总数变化 |
| 行为兼容 | 点词 chip 仍能筛选出现行 |

## 不做的事

- 不改后端（前端分页，避免分页/过滤双重维度下的 server round-trip）
- 不改词频聚合逻辑（app.py:1140-1161）
- 不改 `slirn-opt-word` 单项内部布局
- 不持久化分页状态（每次进入页面都从第 1 页开始）