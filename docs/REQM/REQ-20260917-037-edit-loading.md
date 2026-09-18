# REQ-20260917-037 — 编辑任务加载等待效果

| 字段 | 值 |
|---|---|
| 编号 | REQ-20260917-037 |
| 日期 | 2026-09-18 |
| 优先级 | P3（交互反馈） |
| 状态 | ✅ 完成（2026-09-18） |
| 关联 | REQ-20260915-003（编辑任务） |
| 改动范围 | `slirn_home/`（router.js edit-task 分支 + home.css spinner）；零后端改动 |

---

## 0. 背景

任务列表点「✏️ 编辑」→ `/slirn/api/edit_task` 服务端要读视频信息
（ffprobe 大视频慢）+ 热词库渲染编辑页，工作多的任务点击后长时间无任何反馈，
像没点上。

## 1. 核心需求

| ID | 需求 |
|---|---|
| REQ-1.1 | 点击「编辑」**立即**出现加载态：按钮置「⏳ 加载中…」并禁用 |
| REQ-1.2 | 同时切到编辑页位置并显示 spinner 占位（说明在等什么） |
| REQ-1.3 | 响应到达后替换为编辑页（原有行为）；失败/网络错误恢复按钮与原 tab |

## 2. 设计要点

- 同步 DOM 改写放在 click 分支内（promise 回调前的同一个 JS task），
  保证「点了就有反馈」不受网络快慢影响。
- 失败恢复：服务端 error 或网络拒绝 → 按钮还原 + 切回任务列表 tab + toast。

## 3. 验收标准

- [x] AC-1 点击后立刻（响应前）按钮为 ⏳ 禁用态 + spinner 占位可见
- [x] AC-2 加载完成后正常渲染编辑页（预填行为不变）
- [x] AC-3 失败路径按钮/页面恢复且给出 toast
- [x] AC-4 ruff 0 错；全量 pytest 通过；真实浏览器 E2E 验证同步加载态

## 4. 验证记录（2026-09-18）

| 项 | 结果 |
|---|---|
| `node --check router.js` | ✅ JS OK |
| `ruff check slirn_home/ tests/` | ✅ All checks passed |
| 全量 pytest | ✅ 258 passed |
| 真实浏览器 E2E（CDP，Chrome headless） | ✅ 8/8 通过 |

E2E 细节（`work/REQ-20260917-037/_e2e_037_038.py`，截图 `_shot_037_loading.png`）：

- **AC-1 同步加载态**：在**同一个** `Runtime.evaluate` 里 `btn.click()` 后立即读取 —
  按钮已变「⏳ 加载中…」+ `disabled=true`、`.slirn-loading-box .slirn-spinner`
  占位含「正在加载任务（读取视频信息与热词…）」、create tab 已切出。
  同一 eval 内断言 = fetch 尚未返回时就已生效（同步 DOM 改写，不受网络快慢影响）。
- **AC-2 正常路径**：编辑页渲染后 spinner 消失、按钮还原「✏️ 编辑」。
- **AC-3 失败路径**：打桩 `window.fetch` 对 `/edit_task` reject → 按钮还原可点、
  切回任务列表 tab、toast「❌ TypeError: net down」。
  （注：postJSON 的 catch 把网络异常转成 `{error}`，走 handler 的 `r.error` 分支
  `restoreTabsE()` — 行为与网络拒绝回调等价，恢复逻辑两条路都通。）

改动点：`router.js` edit-task 分支（同步置 ⏳/禁用 + spinner 占位 + ALL_TABS 切换，
`restoreTabsE` 统一恢复）、`home.css` `.slirn-loading-box` / `.slirn-spinner` + 旋转动画。
