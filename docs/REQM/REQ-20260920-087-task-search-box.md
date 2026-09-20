# REQ-20260920-087 — 任务列表搜索框无监听（输入被忽略）

## 背景

任务列表（首页 → 任务标签页）顶部有一个搜索框 `[app.py:215](slirn_home/app.py#L215)`：

```html
<input class="slirn-search-box" id="slirn-task-search" placeholder="搜索任务名 / ID / 视频..." />
```

**BUG**：搜索框是 UI 孤儿 — 没有任何 JS listener 处理。`router.js` 全文 `Grep "slirn-task-search"` 无任何匹配。用户输入"22"想找 task 20260918-022，结果：
- 输入框接受字符（光标可见）
- 任务卡片列表**完全没有变化**
- 用户以为搜索框坏了 / 找不到 task 22

## 根因

`_render_tasks_tab` 只输出搜索框 input 标签，没绑定任何 oninput / onkeyup / addEventListener。整个 router.js 没有任何代码读 `#slirn-task-search` 的值或隐藏/显示卡片。

## 用户原始反馈

> 这里根本没有执行过滤的按钮，输入22也没有用啊，Task 22怎么找

## 验收标准

### AC-1：输入"22"时 task 22 卡片可见，task 19/20/21/13/02 等不含"22"的隐藏 ✅ 待验证
### AC-2：清空搜索框，所有任务卡片重新可见 ✅ 待验证
### AC-3：搜索匹配 task.name OR task.task_id（不区分大小写） ✅ 待验证
### AC-4：搜索匹配 original_video_source 文件名（如 "v17-verify"） ✅ 待验证
### AC-5：搜索框有"无匹配"空态文案（输入有内容但无匹配时） ✅ 待验证
### AC-6：搜索状态不持久化（刷新页面后清空搜索框） ✅ 待验证
### AC-7：搜索不破坏现有 task 卡片其他功能（点剪辑按钮仍能进入工作台） ✅ 待验证
### AC-8：搜索 + 状态过滤（如 SUBTITLE_REVIEWED）协同工作 ✅ 待验证

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

- [REQ-20260918-016](REQ-20260918-016-task-list-ui.md) — 任务列表 UI（搜索框在这里引入但没接通交互）
