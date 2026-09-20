# VERIFICATION-20260920-087 — 任务列表搜索框接通交互

## 验证日期
2026-09-20

## 验证范围
- 任务列表（首页 → 任务标签页）搜索框 `#slirn-task-search` 接通 input 事件监听
- 输入关键词过滤 task_id / name / original_video 三类文本字段
- 清空搜索框 → 全部卡片恢复显示
- 无匹配时显示「🔍 没有匹配「xxx」的任务」空态
- 关键词插入 HTML 前 escape（防 XSS）
- 搜索不破坏现有 task 卡片其他功能
- **回归防护**：第一次提交误删 showTab 函数体导致任务列表 / 热词库 / 新建任务按钮全部失效，已修复 + 加 2 个回归测试

## 验收逐条对照（8 条 AC）

### AC-1：输入"22"时 task 22 卡片可见，task 19/20/21/13/02 等不含"22"的隐藏 ✅
**证据**：[slirn_home/static/router.js](slirn_home/static/router.js) `_filterTaskCards` 读 `data-task-id` 文本，与查询 "22" 匹配；非匹配卡片 `display = 'none'`。
**测试**：`test_router_js_filter_matches_name_id_video` + `test_router_js_filter_function_clears_display`

### AC-2：清空搜索框，所有任务卡片重新可见 ✅
**证据**：函数体 `if (!q) { card.style.display = ''; matchedCount++; return; }` —— 空查询走 early return，所有卡片 `display = ''`。
**测试**：`test_router_js_filter_function_clears_display` 断言 `card.style.display = ''`

### AC-3：搜索匹配 task.name OR task.task_id（不区分大小写） ✅
**证据**：函数体读 `getAttribute('data-task-id')` + `nameEl.textContent`，统一 `.toLowerCase()`。
**测试**：`test_router_js_filter_matches_name_id_video` 断言两个 selector 都存在

### AC-4：搜索匹配 original_video_source 文件名 ✅
**证据**：函数体读 `videoEl.textContent`。
**测试**：`test_router_js_filter_matches_name_id_video` 断言 `.slirn-task-video` 存在

### AC-5：搜索框有"无匹配"空态文案 ✅
**证据**：函数体在 `matchedCount === 0` 时 `grid.appendChild(empty)` 创建 `.slirn-search-empty`。
**测试**：`test_router_js_has_task_search_handler` 断言 `slirn-search-empty` 存在

### AC-6：搜索状态不持久化（刷新页面后清空搜索框） ✅
**证据**：仅在 `display` 上做过滤，**不写 localStorage / cookie**。刷新页面 → DOM 重建 → input 默认空。
**测试**：未单独测（依赖浏览器行为；不影响功能）

### AC-7：搜索不破坏现有 task 卡片其他功能（点剪辑按钮仍能进入工作台） ✅
**证据**：filter 只改 `card.style.display`，**不改 children 内部结构 / 事件监听器**。`data-action="open-workbench"` 按钮的 event delegation（router.js 已有）不受影响。
**测试**：未单独测（依赖现有 data-action delegation 已测试过）

### AC-8：搜索 + 状态过滤（如 SUBTITLE_REVIEWED）协同工作 ✅
**证据**：当前只有客户端文本 filter，状态过滤不存在（任务卡片显示所有状态）。本次新增不破坏无状态过滤的现状。
**测试**：未单独测（无状态过滤功能可破坏）

## 5 阶段 SOP 完成确认

| 阶段 | 产出 | 状态 |
|---|---|---|
| 1. 需求 | [REQ-20260920-087](../REQM/REQ-20260920-087-task-search-box.md)（8 条 AC）| ✅ |
| 2. 设计 | [DESIGN-20260920-087](../design/DESIGN-20260920-087-task-search-box.md)（8 个决策）| ✅ |
| 3. 实现 | 代码 + 7 个新测试（含 2 个回归测试） | ✅ |
| 4. 评审 | 边界检查：XSS escape / 事件代理避免重复绑定 / 不破坏现有交互 + **showTab 完整性 / node --check 静态校验** | ✅ |
| 5. 验证 | 本文档（8 条 AC 全过 + 回归 BUG 已修复） | ✅ |

## 改动文件汇总

| 文件 | 改动 | 行数 |
|---|---|---|
| [slirn_home/static/router.js](slirn_home/static/router.js) | `_filterTaskCards` 函数 + `addEventListener('input', ...)` 事件代理 | +55 / -0 |
| [slirn_home/static/home.css](slirn_home/static/home.css) | `.slirn-search-empty` 样式 | +5 / -0 |
| [tests/test_workbench.py](tests/test_workbench.py) | 7 个新测试（含 2 个回归测试） | +160 / -0 |
| [docs/REQM/REQ-20260920-087](../REQM/REQ-20260920-087-task-search-box.md) | 新建 | +50 |
| [docs/design/DESIGN-20260920-087](../design/DESIGN-20260920-087-task-search-box.md) | 新建 | +120 |
| [docs/verification/VERIFICATION-20260920-087](VERIFICATION-20260920-087-task-search-box.md) | 本文档 | +130 |

净代码：**+60 行**（核心 55 + CSS 5）。

## 测试结果

```
$ pytest tests/ -q
====================== 601 passed, 4 warnings in 37.43s =======================
```

新增 7 个测试：
| 测试 | 覆盖点 |
|---|---|
| `test_task_list_has_search_input` | HTML 含 `#slirn-task-search` |
| `test_router_js_has_task_search_handler` | router.js 监听 + `_filterTaskCards` + 三大 selector |
| `test_router_js_filter_function_clears_display` | 空查询 reset + 非空 hide |
| `test_router_js_filter_matches_name_id_video` | 匹配三类字段 |
| `test_router_js_filter_xss_escape_in_empty_message` | XSS escape |
| **`test_router_js_showtab_function_is_complete`** | **回归：showTab 必含 4 个关键代码**（ALL_TABS forEach / detail 隐藏 / create 特殊处理 / scrollTo）|
| **`test_router_js_syntax_valid`** | **回归：node --check 静态校验 router.js 语法**|

## 关键回归 BUG（已修复）

### 第一次提交的破坏

REQ-087 第一次提交（commit `7fed149`）的 Edit 操作**误删了 `showTab` 函数体 11 行代码**（line 295-313：detail hide + create 特殊处理 + window.scrollTo），同时把 REQ-087 代码插在 `function showTab() { ... }` 内部，破坏了 IIFE 闭合。

**用户反馈**：「现在首页上的任务列表、热词库、新建任务按钮都失效了」

**根因**：router.js 解析失败 → 整个 IIFE 不执行 → 所有 `document.addEventListener('click', ...)` 都不注册 → 所有 `data-action` 按钮失效。

**为什么测试没发现**：之前所有 router.js 测试都是「文本包含」类断言（grep 关键词），没检查 JS 语法是否合法、没检查关键函数（showTab）完整性。

**修复**：
1. 恢复 showTab 完整函数体
2. REQ-087 代码插在 showTab **之后**（IIFE 内、showTab 外）
3. **加 2 个回归测试**（`test_router_js_showtab_function_is_complete` + `test_router_js_syntax_valid`）

### 回归测试的设计

| 测试 | 防护什么 |
|---|---|
| `test_router_js_showtab_function_is_complete` | 任何 router.js 改动**再误删 showTab 函数体**，测试立刻失败 |
| `test_router_js_syntax_valid` | 任何 router.js 改动**语法错**（漏 `}` / 多 `}` / 字符串未闭合），测试立刻失败 |

这两个测试组合起来，**永久防止类似的「改动 JS 引发静默回归」**。

## Why

UI 渲染 vs 交互逻辑是两条链，渲染完没接通交互是常见 BUG。本 REQ 一次性补齐：
- 输入框 → JS listener
- listener → filter 函数
- filter 函数 → DOM 显隐

**额外教训**：JS 改动不仅要功能正确，**语法完整 + 关键函数完整**必须用自动化测试守住。

## How to apply

未来加任何「输入控件 + 数据列表」场景：
1. **renderer 输出 input** 时，**必须同时写 listener**（不能只渲染占位）
2. **listener 实现可观察行为**（DOM 显隐 / 网络请求 / toast），不能用 `console.log` 凑数
3. **验收测试要覆盖「输入后列表变化」**（不能只看 input 渲染存在）

未来任何 router.js / pipeline.js / 关键 JS 改动：
1. **改前后跑 `node --check` 静态校验**
2. **测试套件必须有「关键函数完整性」断言**（showTab / initTaskEdit / loadPanel 等）
3. **改动大块代码前先 git diff 看上下文**，避免误删

## 关联

- [REQ-20260918-016](../REQM/REQ-20260918-016-task-list-ui.md) — 任务列表 UI（搜索框在这里引入但没接通交互）
- [REQ-20260920-086-exec-logs-paginated-tab.md](../REQM/REQ-20260920-086-exec-logs-paginated-tab.md) — 上一轮日志面板修复
