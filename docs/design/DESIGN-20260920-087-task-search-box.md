# DESIGN-20260920-087 — 任务列表搜索框接通交互

## Context

[REQ-20260920-087-task-search-box.md](../REQM/REQ-20260920-087-task-search-box.md) 描述：首页 → 任务标签页 顶部搜索框 `[app.py:215](slirn_home/app.py#L215)` 是 UI 孤儿 — 输入字符被忽略，列表不过滤。

**根因**：`_render_task_list` 只输出 `<input>` 标签，`router.js` 全文 `Grep "slirn-task-search"` 无匹配。没有任何 listener 读输入值。

## 决策

| 维度 | 决策 | 理由 |
|---|---|---|
| 过滤位置 | **客户端**（不重新调 `/refresh_tasks`） | 任务列表通常 < 100 条；客户端过滤 0 延迟；不增加服务端压力 |
| 匹配字段 | `task_id` + `name` + `original_video` 三个文本字段（小写包含匹配） | 覆盖用户搜「22」(找 task_id 含 22)、「训练营」(找 name)、「v17-verify」(找 video 文件名) 三类典型场景 |
| 大小写 | 不敏感（统一 toLowerCase） | 用户输入大小写习惯不同 |
| 空态 | 单独 div 元素 `.slirn-search-empty`（grid-column: 1/-1 跨整行） | 视觉对齐现有 `.slirn-empty`；区分「无任务」与「搜索无匹配」两种空态 |
| XSS 防护 | 关键词插入空态文案前用 `replace(/[<>&"']/g, ...)` 转义 | innerHTML 拼接必须 escape；防用户输入 `<script>` 注入 |
| 事件绑定 | **document 级别 `addEventListener('input', ...)` 事件代理** | `_render_task_list` 可能在刷新时被替换（如任务刷新后），事件代理避免重复绑定 |
| 状态持久化 | **不持久化**（刷新页面后清空） | 搜索是临时查询，不该跨页面延续（避免误导） |
| CSS | 仅加 `.slirn-search-empty` 样式（padding / 居中 / muted 颜色）；其他复用现有 `.slirn-empty` | 最小改动 |

## 实现要点

### 1. router.js 加 `_filterTaskCards` 函数 + 事件代理

```javascript
function _filterTaskCards(query) {
  var q = (query || '').trim().toLowerCase();
  var grid = document.querySelector('.slirn-task-grid');
  if (!grid) return;
  var cards = grid.querySelectorAll('.slirn-task-card');
  var matchedCount = 0;
  cards.forEach(function(card) {
    if (!q) {
      card.style.display = '';
      matchedCount++;
      return;
    }
    var tid = (card.getAttribute('data-task-id') || '').toLowerCase();
    var nameEl = card.querySelector('.slirn-task-name');
    var videoEl = card.querySelector('.slirn-task-video');
    var name = nameEl ? (nameEl.textContent || '').toLowerCase() : '';
    var video = videoEl ? (videoEl.textContent || '').toLowerCase() : '';
    var hay = tid + ' ' + name + ' ' + video;
    var match = hay.indexOf(q) >= 0;
    card.style.display = match ? '' : 'none';
    if (match) matchedCount++;
  });
  // 处理「无匹配」空态
  var existing = grid.querySelector('.slirn-search-empty');
  if (existing) existing.remove();
  if (q && matchedCount === 0) {
    var empty = document.createElement('div');
    empty.className = 'slirn-empty slirn-search-empty';
    empty.style.gridColumn = '1/-1';
    empty.innerHTML = '<div class="slirn-empty-icon">🔍</div>' +
      '<div class="slirn-empty-text">没有匹配「' + q.replace(/[<>&"']/g, function(c) {
        return ({'<':'<','>':'>','&':'&','"':'"',"'":'&#39;'})[c];
      }) + '」的任务</div>';
    grid.appendChild(empty);
  }
}

document.addEventListener('input', function(e) {
  if (e.target && e.target.id === 'slirn-task-search') {
    _filterTaskCards(e.target.value);
  }
});
```

### 2. home.css 加 `.slirn-search-empty`

```css
.slirn-search-empty {
  padding: 48px 16px !important;
  text-align: center;
  color: var(--muted, #666);
}
```

## 关键文件改动

| 文件 | 改动 | 行数 |
|---|---|---|
| [slirn_home/static/router.js](slirn_home/static/router.js) | 加 `_filterTaskCards` 函数 + `addEventListener('input', ...)` 事件代理 | +50 / -0 |
| [slirn_home/static/home.css](slirn_home/static/home.css) | 加 `.slirn-search-empty` 样式 | +5 / -0 |
| [tests/test_workbench.py](tests/test_workbench.py) | 加 5 个测试 | +85 / -0 |

## 验证步骤

1. `pytest tests/ -q` → 599 passed（594 + 5 new）
2. 启动 slirn → 首页 → 任务标签 → 在搜索框输入「22」→ 仅显示 task_id / name / video 含「22」的卡片（截图里 task 22 命中「22」）
3. 清空搜索框 → 所有任务卡片重新显示
4. 输入「不存在」→ 显示「🔍 没有匹配「不存在」的任务」空态
5. 输入 `<script>alert(1)</script>` → 空态文案正确转义（不弹 alert）
6. 在搜索结果中点「剪辑」按钮 → 仍能进入工作台（filter 不破坏交互）

## Why

UI 渲染 vs 交互逻辑是两条链，渲染完没接通交互是常见 BUG。本 REQ 一次性补齐：
- 输入框 → JS listener
- listener → filter 函数
- filter 函数 → DOM 显隐

## How to apply

未来加任何「输入控件 + 数据列表」场景：
1. **renderer 输出 input** 时，**必须同时写 listener**（不能只渲染占位）
2. **listener 实现可观察行为**（DOM 显隐 / 网络请求 / toast），不能用 `console.log` 凑数
3. **验收测试要覆盖「输入后列表变化」**（不能只看 input 渲染存在）

## 关联

- [REQ-20260918-016](../REQM/REQ-20260918-016-task-list-ui.md) — 任务列表 UI（搜索框在这里引入但没接通交互）
