# REQ-20261013-101 — 快捷键入口提到顶栏「热词库」前统一设置

## 解决什么问题

用户反馈：
> 还有一个功能要重新提取出来，就是关于快捷键：要把快捷键的配置放到热词库
> 的前面，在这里统一设置，而不在长视频的阶段里去设置。

现在快捷键配置埋在「字幕修订」「优化字幕」面板底部的 `slirn-revkeys-open`
「⚙ 自定义」里（router.js:6738 弹窗）。问题：

1. 跨阶段生效（修订 / 优化字幕 / 精剪都要看当前键位），配置入口却在某个阶段
   面板里 —— 用户切到非该阶段页面就找不到了。
2. 顶栏已有「📚 热词库」「大模型设置」「主题切换」「上游首页」全局按钮，
   快捷键属于同一类全局设置。

## 设计决策

1. **顶栏新增「⌨ 快捷键」按钮**，紧贴「📚 热词库」**前面**（用户指定位置）。
   复用现成弹窗 `revKeysOpenModal()` —— 0 新逻辑，纯入口提升。
2. **保留面板内「⚙ 自定义」入口** —— 不破坏现有 UX（有的用户已习惯从面板
   底部点；删了等于把功能藏深一层）。两处入口都映射到同一个 `data-action
   ="revkeys-open"`，路由分发已存在。
3. **权限/可见性**：快捷键是浏览器 localStorage 的 `slirnRevKeys`（按设备、
   不按用户）—— 与登录态无关，顶栏按钮始终显示。

## 不做的事

- ❌ 不改快捷键底层逻辑（键位定义、弹窗渲染、冲突检测、提示条同步），
  REQ-20260916-005 已有完整方案且稳定。
- ❌ 不把键位搬到服务端（用户明确说在浏览器里能改就行）。
- ❌ 不动「字幕修订」「优化字幕」面板里的「⚙ 自定义」入口。

## 验收标准

1. 顶栏顺序：⌨ 快捷键 → 📚 热词库 → 大模型设置 → 🌓 主题 → 🚀 上游首页。 ✅
2. 点「⌨ 快捷键」弹出与面板内「⚙ 自定义」**同一**弹窗（同一 DOM id
   `slirn-revkeys-modal`、同一渲染函数 `revKeysOpenModal`），键位列表 +
   视频键冲突表 + 录制态完整一致。 ✅ 证据：复用 router.js:7106 派发、
   0 新增逻辑；测试 `test_topbar_hotkeys_button_uses_reuse_dispatch` 校验
   router.js 内 `if (action === 'revkeys-open')` + `revKeysOpenModal();`
   同时存在。
3. 改键后：提示条（`.slirn-rev-kbhint` / `.slirn-opt-kbhint`）跟随刷新；
   关闭弹窗状态不丢；localStorage `slirnRevKeys` 已写。 ✅
   证据：`applyRevKeysState` + `applyOptKeysState` 已由 router.js 在
   保存键位后调用（既有方案，不在本任务范围）。
4. 面板内的「⚙ 自定义」按钮**仍然存在**（双入口）。 ✅ 证据：
   `test_panel_revkeys_open_still_present` 校验 app.py 内
   `data-action="revkeys-open"` 出现 ≥ 2 次（顶栏 + 至少一处面板）。
5. ruff 零新增 / node --check 通过 / 全量 pytest 通过 / 7861 顶栏已渲染新按钮。 ✅
   证据：ruff `app.py` 工作树 99 == HEAD 99 错误（零新增）；`node --check
   router.js` 通过；全量 pytest 1186 passed（基础 1182 + 新增 4）；7861
   已重启 PID 24004，`_build_topbar` 输出含 `revkeys-open` 在 `goto-hotwords`
   之前（顺序测试 `test_topbar_hotkeys_button_before_hotwords` 通过）。

## 闭环记录

- 提交：2a2d148，pushed to main（ref-update `73f6e24..2a2d148`）。
- 7861 后台任务：b3dh9r95p（live PID 24004）。
- 后续任务：Task C+D（短视频精剪/粗剪/字幕优化按长视频搬运，方向相同
  合并侦察；后台能复用的尽量复用、短/长互不影响），按用户串行指令
  排在 B 之后。
