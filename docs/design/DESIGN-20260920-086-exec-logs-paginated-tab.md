# DESIGN-20260920-086 — 执行日志：折叠卡 → 分页 tab + 阶段分隔条

## 核心思路

3 处改动：

1. **删除工作台顶部 `📜 执行历史` 折叠卡** — `_render_workbench` 不再调 `_render_exec_history_card`；折叠卡函数本体保留以备复用
2. **`slirn-wb-pane-logs` 加分页** — 后端 `query_history` + `list_logs` 端点加 `offset`；前端 `loadLogs` 传 `page / page_size`，渲染页码导航
3. **rail 加视觉分隔** — 在 `_render_workbench` stage_items 末尾插入分隔条 `slirn-wb-rail-divider`（粗 1px 实线 + 图标 + 文字「📜 执行日志（不属于流水线阶段）」）；同时给 logs stage 加 `slirn-wb-stage-extra` 类

## 设计决策

### 决策 1：删除折叠卡 vs 隐藏折叠卡

| 选项 | 优点 | 缺点 |
|---|---|---|
| **A. 删除**（采用） | 简单；意图清晰；DOM 干净 | 函数本体仍占代码（~75 行） |
| B. CSS `display:none` | 保留函数 | DOM 里仍有元素；用户仍能看到折叠卡状态；潜在困惑 |
| C. 折叠卡移到日志 tab 顶部 | 仍显示"最近 10 条"摘要 | 与分页全量展示重叠 |

**理由**：A 最干净。函数 `_render_exec_history_card` 本体**保留**（不删）— 未来若需要"最近 10 条摘要"可零成本复用。

### 决策 2：每页大小

| 选项 | 优点 | 缺点 |
|---|---|---|
| **A. 20 条/页**（采用） | 一屏可见；翻页成本低 | 长任务历史要看多次翻页 |
| B. 50 条/页 | 翻页次数少 | 单页滚动长 |
| C. 10 条/页 | 与原折叠卡一致 | 翻页太多 |

**理由**：20 是 UI 分页通用值（GitHub PR 列表、邮箱列表）。后端 `list_logs` 保留 `limit` 上限 1000，调高 page_size 由前端控制。

### 决策 3：分页 UI 形态

| 选项 | 优点 | 缺点 |
|---|---|---|
| **A. 经典「首页 ‹ 上一页 N/M 下一页 › 末页」**（采用） | 通用；无歧义 | 略占空间 |
| B. 无限滚动 | 现代化；少点击 | 跳页难；DOM 累积 |
| C. 简单「‹ 下一页」按钮 | 极简 | 无法跳指定页 |

**理由**：执行日志场景里用户常要查特定记录（按时间倒序找某次失败），经典分页跳页更高效。

### 决策 4：分页与过滤的关系

**采用：过滤条件变化时 page 重置为 1**。

理由：用户改了 chip 后，新过滤的总条数变化；若还停在第 5 页可能超出范围 → 报"无数据"。`logsState` 监听 chip click handler 加 `if (changed) logsState.page = 1;`。

### 决策 5：分页与关键词搜索的关系

**采用：关键词搜索单独 trigger**（与现有 `data-action="logs-refresh"` 一致）。不自动重置 page——避免输入时实时触发重置。

### 决策 6：分隔条形态

| 选项 | 优点 | 缺点 |
|---|---|---|
| **A. 粗实线 + 📜 图标 + 「执行日志（不属于流水线阶段）」文字**（采用） | 信息明确；视觉冲击；可访问性 | 多占 30px 高度 |
| B. 仅粗实线 | 极简 | 不解释为什么分隔 |
| C. 图片/插画 | 美观 | 文件管理麻烦；本地化困难 |

**理由**：A 既分隔又解释。用户截图里他明确说「明确的分割条或者是图片之类的，把执行日志跟上面的所有阶段分隔开」— 文字 + 图标 + 实线三者组合最稳。

CSS：

```css
.slirn-wb-rail-divider {
  display: flex; align-items: center; gap: 8px;
  margin: 14px 0 8px;
  padding-top: 12px;
  border-top: 2px solid var(--accent-solid);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.4px;
  color: var(--accent-solid);
  text-transform: uppercase;
}
.slirn-wb-rail-divider::before { content: '——————'; color: var(--border); }
.slirn-wb-rail-divider::after  { content: '——————'; color: var(--border); flex: 1; }
```

### 决策 7：是否加 `slirn-wb-stage-extra` 类

**采用：加**。

理由：`router.js` 的 `wbAutoNextMaybe`（[router.js:759](slirn_home/static/router.js#L759)）用 `:not(.slirn-wb-stage-extra)` 排除非流水线阶段。但当前 `app.py:3577` 渲染 logs stage 用的是 `slirn-wb-stage-logs` 类 — **不匹配 `extra` 选择器**。当前没出 bug 是因为 logs 是 rail 末尾（line 769 `idx + 1 >= stages.length` 兜底）。但**语义错乱**。

修复：给 logs stage 同时加 `slirn-wb-stage-extra slirn-wb-stage-logs` 两个类 — 让两者语义都对。

### 决策 8：后端 list_logs 协议变更

**变更**：

| 字段 | 旧 | 新 |
|---|---|---|
| 输入 | `task_id, kinds, statuses, keyword, time_from, time_to, auto, limit` | `+ offset, page, page_size` |
| 输出 | `{ok, items, total}` | `{ok, items, total, page, page_size, total_pages}` |

`total` = **过滤后总数**（不是 `len(items)`），用于前端算总页数。

后端 `query_history` 加 `offset: int = 0` 参数 — 在过滤完后 `items[offset:offset+limit]` 切片返回。

**兼容性**：旧调用方（不带 offset）继续工作 — 默认 offset=0。

### 决策 9：分页组件位置

**采用：放在 `_render_exec_logs_pane` HTML 内的 `.slirn-logs-list` 下方**（独立 `.slirn-logs-pager` 块）。

理由：与列表 DOM 关联；过滤 chip 在上方不影响；翻页时只重渲染列表不重渲整个面板。

## 实施步骤

### Phase 1 — REQ 文档 ✅

产出：[REQ-20260920-086](../REQM/REQ-20260920-086-exec-logs-paginated-tab.md)

### Phase 2 — DESIGN 文档（本文件） ✅

### Phase 3 — 实现

#### Step 1：删除工作台顶部折叠卡（5 处改动）

[slirn_home/app.py:3638](slirn_home/app.py#L3638) 移除：

```python
{_render_exec_history_card(task_id, mgr)}  ← 删
```

#### Step 2：rail 加分隔条 + logs stage 加 `slirn-wb-stage-extra` 类

[slirn_home/app.py:3574-3583](slirn_home/app.py#L3574-L3583) 改：

```python
# REQ-20260920-086：rail 末尾加明显分隔条（执行日志不属于流水线阶段）
stage_items += (
    f'<div class="slirn-wb-rail-divider" title="执行日志是历史视图，不属于流水线阶段">'
    f'📜 执行日志（不属于流水线阶段）'
    f'</div>'
)
stage_items += (
    f'<div class="slirn-wb-stage slirn-wb-stage-logs slirn-wb-stage-extra pending" '
    f'data-action="wb-stage" data-pane="logs" '
    f'title="查看该任务所有阶段的执行历史（含耗时、错误信息）">'
    f'<span class="slirn-wb-stage-mark">📜</span>'
    f'<div class="slirn-wb-stage-body"><div class="slirn-wb-stage-title">📜 执行日志</div>'
    f'<div class="slirn-wb-stage-desc">查看所有阶段执行历史（生成/修订/切分/合成/优化）</div></div></div>'
)
```

#### Step 3：日志面板加分页 UI

[slirn_home/app.py:3714-3717](slirn_home/app.py#L3714-L3717) 改 `.slirn-logs-list` 下方加：

```python
f'  <div class="slirn-logs-list" id="slirn-logs-list" data-task-id="{_esc(task_id)}">'
f'    <div class="slirn-form-hint slirn-logs-empty">尚未查询。点击「🔄 刷新」或切换过滤条件自动加载。</div>'
f'  </div>'
# REQ-20260920-086：分页导航（首次渲染时 total=0 → 显示空）
f'  <div class="slirn-logs-pager" id="slirn-logs-pager" data-page="1" data-page-size="20" data-total="0">'
f'    <button type="button" class="slirn-pager-btn" data-pager="first" disabled title="首页">«</button>'
f'    <button type="button" class="slirn-pager-btn" data-pager="prev" disabled title="上一页">‹</button>'
f'    <span class="slirn-pager-info">第 <span data-bind="page">1</span> / <span data-bind="total-pages">1</span> 页 · 共 <span data-bind="total">0</span> 条</span>'
f'    <button type="button" class="slirn-pager-btn" data-pager="next" disabled title="下一页">›</button>'
f'    <button type="button" class="slirn-pager-btn" data-pager="last" disabled title="末页">»</button>'
f'    <select class="slirn-pager-size" data-pager="size" title="每页条数">'
f'      <option value="10">10 条/页</option>'
f'      <option value="20" selected>20 条/页</option>'
f'      <option value="50">50 条/页</option>'
f'      <option value="100">100 条/页</option>'
f'      <input type="hidden" data-bind="total" value="0">'
f'    </select>'
f'  </div>'
```

#### Step 4：后端 query_history + list_logs 支持 offset/page

[slirn_home/execution_history.py:264-271](slirn_home/execution_history.py#L264-L271)：

```python
def query_history(outputs_dir: Path, *,
                  kinds: list[str] | None = None,
                  statuses: list[str] | None = None,
                  keyword: str = "",
                  limit: int = 200,
                  offset: int = 0,        # REQ-20260920-086：分页 offset
                  time_from_ts: float | None = None,
                  time_to_ts: float | None = None,
                  auto: str = "any") -> list[dict]:
```

实现：先按旧逻辑收集全部 `out`（不 break 在 limit 处），收集完后 `total = len(out)`，再切片 `out[offset:offset+limit]` 返回 items。

但旧逻辑 `if len(out) >= limit: break` 在 limit=200 时只返回前 200 条 — **过滤后总数大于 limit 时不准**。修：

```python
matched: list[dict] = []
for it in items:
    # ... 过滤条件 ...
    matched.append(it)
# REQ-20260920-086：返回 matched + total = len(matched)
return matched[offset:offset+limit], len(matched)
```

**重要**：函数签名从 `list[dict]` 改为 `tuple[list[dict], int]`（items, total）— 改返回类型。

`list_logs` 端点：

```python
try:
    limit = int(body.get("limit") or body.get("page_size") or 20)
except (TypeError, ValueError):
    limit = 20
limit = max(1, min(limit, 1000))
try:
    offset = int(body.get("offset") or 0)
except (TypeError, ValueError):
    offset = 0
offset = max(0, offset)

# ... 调用 query_history 拿 (items, total)
items, total = execution_history.query_history(
    outputs_dir,
    kinds=...,
    statuses=...,
    keyword=keyword,
    limit=limit,
    offset=offset,
    time_from_ts=time_from_ts,
    time_to_ts=time_to_ts,
    auto=auto_mode,
)
page = offset // max(1, limit) + 1
total_pages = max(1, (total + limit - 1) // limit)
return _ok("", items=items, total=total, page=page, page_size=limit, total_pages=total_pages)
```

#### Step 5：前端 loadLogs + 分页控件

[slirn_home/static/router.js](slirn_home/static/router.js)：

```javascript
var logsState = {
  kinds: [],
  statuses: [],
  keyword: '',
  timeFrom: '',
  timeTo: '',
  auto: 'any',
  page: 1,           // REQ-20260920-086：当前页
  pageSize: 20,      // REQ-20260920-086：每页条数
};

function loadLogs() {
  // ...
  var payload = {
    task_id: tid,
    kinds: logsState.kinds,
    statuses: logsState.statuses,
    keyword: logsState.keyword,
    time_from: logsState.timeFrom || '',
    time_to: logsState.timeTo || '',
    auto: logsState.auto || 'any',
    offset: (logsState.page - 1) * logsState.pageSize,
    limit: logsState.pageSize,
  };
  // ...
  postJSON(SLIRN_API + '/list_logs', payload).then(function(r) {
    if (!r || !r.ok) { /* ... */ return; }
    _renderLogsList(list, r.items || []);
    _renderLogsPager(r);
    if (countEl) countEl.textContent = (r.total || 0) + ' 条';
  });
}

function _renderLogsPager(r) {
  var pager = document.getElementById('slirn-logs-pager');
  if (!pager) return;
  var total = r.total || 0;
  var pageSize = r.page_size || logsState.pageSize;
  var totalPages = r.total_pages || Math.max(1, Math.ceil(total / pageSize));
  var page = r.page || 1;
  pager.setAttribute('data-page', String(page));
  pager.setAttribute('data-page-size', String(pageSize));
  pager.setAttribute('data-total', String(total));
  pager.querySelector('[data-bind="page"]').textContent = String(page);
  pager.querySelector('[data-bind="total-pages"]').textContent = String(totalPages);
  pager.querySelector('[data-bind="total"]').textContent = String(total);
  var btns = pager.querySelectorAll('.slirn-pager-btn');
  btns.forEach(function(b) {
    var act = b.getAttribute('data-pager');
    var disabled = (act === 'first' || act === 'prev') ? page <= 1 : page >= totalPages;
    b.disabled = disabled;
  });
}

// chip click handler 改：过滤变化时 page 重置
if (t.classList.contains('slirn-chip') && (t.hasAttribute('data-log-kind') || t.hasAttribute('data-log-status'))) {
  t.classList.toggle('active');
  logsState.page = 1;  // REQ-20260920-086：过滤变化重置 page
  loadLogs();
  return;
}
// 同样处理 data-log-auto / data-log-time click handler

// 新增：pager button click handler
document.addEventListener('click', function(e) {
  var t = e.target;
  if (!t || !t.classList) return;
  if (t.classList.contains('slirn-pager-btn')) {
    var act = t.getAttribute('data-pager');
    var pager = document.getElementById('slirn-logs-pager');
    if (!pager) return;
    var cur = parseInt(pager.getAttribute('data-page') || '1', 10);
    var totalPages = parseInt(pager.querySelector('[data-bind="total-pages"]').textContent || '1', 10);
    if (act === 'first') logsState.page = 1;
    else if (act === 'prev') logsState.page = Math.max(1, cur - 1);
    else if (act === 'next') logsState.page = Math.min(totalPages, cur + 1);
    else if (act === 'last') logsState.page = totalPages;
    loadLogs();
    return;
  }
  // page size change
  if (t.classList.contains('slirn-pager-size')) {
    // 用 change 事件另写
  }
});
document.addEventListener('change', function(e) {
  var t = e.target;
  if (t && t.classList && t.classList.contains('slirn-pager-size')) {
    logsState.pageSize = parseInt(t.value || '20', 10);
    logsState.page = 1;
    loadLogs();
  }
});
```

#### Step 6：CSS 加分隔条 + 分页按钮样式

[slirn_home/static/home.css](slirn_home/static/home.css)：

```css
/* REQ-20260920-086：rail 分隔条（执行日志非流水线阶段） */
.slirn-wb-rail-divider {
  display: flex; align-items: center; gap: 8px;
  margin: 14px 0 8px;
  padding-top: 12px;
  border-top: 2px solid var(--accent-solid);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.4px;
  color: var(--accent-solid);
}
.slirn-wb-rail-divider::after {
  content: '';
  flex: 1;
  border-top: 2px dashed var(--accent-solid);
  opacity: 0.4;
  margin-left: 4px;
}

/* REQ-20260920-086：分页导航 */
.slirn-logs-pager {
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
  padding: 10px 12px;
  margin-top: 6px;
  border-top: 1px dashed var(--border);
  font-size: 12px;
  color: var(--text-secondary);
}
.slirn-pager-btn {
  padding: 4px 10px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--card-bg);
  cursor: pointer;
  font-size: 12px;
  min-width: 28px;
}
.slirn-pager-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
.slirn-pager-btn:hover:not(:disabled) {
  background: var(--accent-soft);
  border-color: var(--accent-solid);
}
.slirn-pager-info {
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  margin: 0 4px;
}
.slirn-pager-size {
  padding: 4px 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--card-bg);
  font-size: 11.5px;
  margin-left: auto;
}
```

#### Step 7：测试

5 个新测试：

```python
def test_list_logs_supports_offset_and_total(tmp_path):
    """REQ-086：list_logs 端点支持 offset + 返回 total / total_pages / page。"""
    # 写 25 条 execution_history 记录
    # 调 list_logs offset=10 limit=10 → 应返回 10 条 + total=25 + page=2 + total_pages=3
    ...

def test_list_logs_total_reflects_filtered_count_not_page_size(tmp_path):
    """REQ-086：total = 过滤后总数，不受 limit 影响。"""
    # 写 25 条但只 5 条匹配 status=failed
    # limit=10 → 应返回 5 条 + total=5 + total_pages=1
    ...

def test_workbench_does_not_render_exec_history_card(tmp_path):
    """REQ-086：工作台 HTML 不再含 .slirn-exec-card。"""
    # 调 _render_workbench → 不应找到 slirn-exec-card class
    ...

def test_workbench_rail_has_divider_before_logs(tmp_path):
    """REQ-086：rail 在 logs stage 之前有 .slirn-wb-rail-divider。"""
    # 调 _render_workbench → 找 .slirn-wb-rail-divider
    # 它的位置在 logs stage 之前
    ...

def test_workbench_logs_stage_has_extra_class(tmp_path):
    """REQ-086：rail 上 logs stage 含 .slirn-wb-stage-extra 类（修潜在 BUG）。"""
    # 调 _render_workbench → logs stage 的 class 含 'slirn-wb-stage-extra'
    ...

def test_router_logs_state_has_page_field():
    """REQ-086：router.js logsState 加 page / pageSize 字段。"""
    ...
```

预期 218 + 6 = **224 passed**。

### Phase 4 — 评审

中 effort（≤20 分钟）：重点审
- [ ] list_logs offset 边界（负数 / 超出范围）
- [ ] 后端 query_history 改返回类型是否影响旧调用方
- [ ] 前端 pager button disabled 状态正确性
- [ ] 过滤变化时 page 是否真的重置（防止 click handler 漏改）
- [ ] `_render_exec_history_card` 函数本体保留（仅调用方删除）

### Phase 5 — 验证

详见 [VERIFICATION-20260920-086](../verification/VERIFICATION-20260920-086-exec-logs-paginated-tab.md)。

## 关键文件改动汇总

| 文件 | 改动 |
|---|---|
| [slirn_home/app.py](slirn_home/app.py) | `_render_workbench` 移除 `_render_exec_history_card` 调用；rail 加分隔条；logs stage 加 `slirn-wb-stage-extra` 类；`_render_exec_logs_pane` 加分页 HTML；`list_logs` 端点加 offset/page/total_pages |
| [slirn_home/execution_history.py](slirn_home/execution_history.py) | `query_history` 加 `offset` 参数，返回类型 `(list, int)` |
| [slirn_home/static/router.js](slirn_home/static/router.js) | `logsState.page / pageSize`；`loadLogs` 传 offset/limit；新增 `_renderLogsPager`；pager button click handler；过滤变化时重置 page |
| [slirn_home/static/home.css](slirn_home/static/home.css) | `.slirn-wb-rail-divider` 样式；`.slirn-logs-pager / .slirn-pager-btn / .slirn-pager-info / .slirn-pager-size` |
| [tests/test_workbench.py](tests/test_workbench.py) | +6 测试 |

净代码：**+180 行**（核心 ~80 行 + CSS ~40 行 + 前端 ~50 行 + 测试 ~120 行 + 文档 ~250 行）。

## 风险与边界

| 风险 | 处理 |
|---|---|
| `query_history` 返回类型变更影响旧调用方 | 全项目 grep `query_history` 调用方 → 改 2 处（list_logs 端点 + 其他） |
| 后端 `offset` 超过 total | 服务端切片 `matched[offset:]` → 返回空 items + total 不变 |
| 翻页时过滤被改动 → page 跳回 1 | click handler 内重置 logsState.page = 1 |
| 分页 UI 在窄屏挤 | `flex-wrap: wrap` 已加 |
| rail 分隔条遮挡小屏 | `overflow: auto` 已在 `.slirn-wb-stages` 上 |
| 折叠卡函数 `_render_exec_history_card` 删除后调用报错 | **不删函数**，仅移除调用方 |
| 历史里查询 `_render_exec_history_card` 相关代码注释（如「REQ-048」）需要保留以备追溯 | 函数本体留着 + 注释完整 |

## 复用现有基础设施

- `_render_exec_logs_pane`（[app.py:3662](slirn_home/app.py#L3662)）— 仅追加分页 HTML
- `list_logs` 端点（[app.py:6596](slirn_home/app.py#L6596)）— 加 offset/limit
- `query_history`（[execution_history.py:264](slirn_home/execution_history.py#L264)）— 加 offset 参数
- `loadLogs` / `logsState`（[router.js:885+](slirn_home/static/router.js#L885)）— 加 page/pageSize + pager 渲染
- `_WB_STAGES` / `wbAutoNextMaybe`（[router.js:755](slirn_home/static/router.js#L755)）— 修语义错乱（logs stage 加 extra 类）

## 验证步骤

1. `pytest tests/test_workbench.py -q` → **224 passed**（218 + 6 新）
2. 重启 slirn → 创建 task + 上传视频 + 跑几次粗剪合成
3. 进入工作台 → **验证 1**：工作台顶部**没有**「📜 执行历史」折叠卡
4. 看 rail → **验证 2**：精剪视频阶段下方有明显分隔条（粗线 + 「📜 执行日志（不属于流水线阶段）」）
5. 点 rail 「📜 执行日志」 → **验证 3**：右侧显示日志面板
6. **验证 4**：默认显示 20 条 + 分页条「第 1 / 3 页 · 共 50 条」 + 翻页按钮可点
7. 点「下一页」 → **验证 5**：列表更新到第 2 页 + 页码变 2
8. 点末页 `»` → **验证 6**：跳到第 3 页 + 「下一页」变 disabled
9. 切换阶段 chip「粗剪合成」 → **验证 7**：page 重置为 1 + 只显示粗剪合成记录
10. 改每页 50 条 → **验证 8**：列表更新 + total_pages 重算
11. 输关键词搜索 → **验证 9**：过滤生效 + 不重置 page（仅触发刷新）

## Why

历史双轨 UI（顶部折叠卡 + rail tab）是早期迭代残留 — REQ-053 建了 tab 后没清理折叠卡。视觉混淆来自 logs stage 长得和其他流水线阶段一样。本次修复：

- 删除冗余 UI
- 让 logs tab 真正能查全量历史（分页）
- 用强视觉信号声明「logs 不是流水线阶段」

## How to apply

未来 UI 引入新「**非流水线视图**」（如「任务统计」「评论」「导出历史」）：

1. 在 rail 末尾用同样的分隔条 + 命名类（`slirn-wb-stage-extra` + 自定义 class）
2. **复用 `wbAutoNextMaybe` 的 `:not(.slirn-wb-stage-extra)` 语义** — 确保自动跳转不进入非流水线视图
3. **不要**让非流水线视图参与 `_WB_STAGES`（避免状态判断 `done/current/pending` 出错）
4. 大列表/历史用分页（避免一次性渲染几千条 DOM）

## 关联

- [REQ-20260918-048-exec-history-card.md](../REQM/REQ-20260918-048-exec-history-card.md) — 要移除的折叠卡
- [REQ-20260918-053-execution-history.md](../REQM/REQ-20260918-053-execution-history.md) — 要增强的日志面板
- [REQ-20260920-081-execution-log.md](../REQM/REQ-20260920-081-execution-log.md) — list_logs 端点
- [REQ-20260920-084-export-progress-time-restore-logs.md](../REQM/REQ-20260920-084-export-progress-time-restore-logs.md) — 上一轮日志修复
- [REQ-20260920-085-upload-audio-auto-enable-bgm.md](../REQM/REQ-20260920-085-upload-audio-auto-enable-bgm.md) — 上一轮 BGM 修复
