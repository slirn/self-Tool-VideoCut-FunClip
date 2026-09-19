# REQ-20260919-061a — 精剪视频·顶部操作栏与预览时长（REQ-061 增量）

> 父需求：[REQ-20260919-061 精剪视频·四素材合成器](REQ-20260919-061-fine-cut-material-compositor.md)
> 本文件追加 **3 个 UI 交互改进**，不改变 REQ-061 的核心数据模型。

## 用户原话（本轮）

> 1. 把「保存设置参数」按钮放在设置参数区域的上边
> 2. 保存的时候，如果没有起名的话，需要填写一个名称
> 3. 在旁边再添加一个「引用参数」按钮，可以选择已经保存的参数列表名称
> 4. 生成预览时时长也添加一个控制参数，可以填写 2~30 秒之内的数值

## 范围（本轮增量）

| # | 子功能 | 影响文件 |
|---|---|---|
| A | 顶部 actions bar：模板名输入 + 保存按钮 + 引用按钮 + 状态 | `slirn_home/app.py` `_render_fine_cut_zone`；`slirn_home/static/home.css`；`slirn_home/static/router.js` |
| B | 保存时若模板名为空 → `window.prompt` 弹窗要求填写 | `slirn_home/static/router.js` `fineSaveAll(asTemplate=true)` |
| C | 引用参数模态：列出已保存模板，可应用/重命名/删除 | `slirn_home/static/home.css`；`slirn_home/static/router.js` `fineImportShow/Apply/Delete/Rename` |
| D | 预览时长控制：数字输入 + ▲▼ 步进，范围 [2, 30] 秒 | `slirn_home/app.py` `render_fine_preview` 端点；`router.js` `fine-preview` action handler；`home.css` `.slirn-fine-step-btn` |

## 关键决策

| 决策 | 选择 | 理由 |
|---|---|---|
| 保存按钮位置 | **顶部 actions bar**（设置参数区域上方） | 用户明确要求；不再放卡片标题里 |
| 模板名缺省处理 | **`window.prompt` 弹窗**（非自定义 modal） | 简单阻塞式输入足够；与现有「📥 引用」模态不混用 |
| 引用模板列表呈现 | **模态弹窗**（`.slirn-fine-import-overlay`） | 用户原话"旁边再加一个引用参数的按钮"暗示独立入口；模态比底部 inline 列表更紧凑 |
| 模板应用前确认 | **不弹窗，直接应用** | 用户没要求；模板结构简单（仅参数，不含素材），误点可随时再点保存覆盖 |
| 预览时长控件 | **number input + ▲▼ 步进**，无关联 slider | 单一数值字段不需要滑块；步进与小滑块视觉一致 |
| 时长默认值 | **10 秒**（历史默认值） | 不改变既有行为；如需调短直接改 |
| 时长范围 | **[2, 30] 秒**（用户原话） | 后端 `max(2.0, min(30.0, d))` 兜底 |
| 旧底部 profile 块 | **删除** | 被顶部 actions bar + 引用模态替代 |

## 验收标准

| # | 验收点 | 验证方式 |
|---|---|---|
| A1 | 精剪视频面板顶部有 actions bar，含「模板名」输入 + 「💾 保存设置参数」+ 「📥 引用参数」 | E2E |
| A2 | actions bar 内含 save_status（保存中/成功/失败 3 态） | 评审 |
| B1 | 用户在模板名为空时点保存 → 弹窗要求填写 | E2E |
| B2 | 用户在弹窗点取消 → 仅保存当前参数，不另存模板 | E2E |
| B3 | 用户在弹窗填写后保存 → 模板列表新增一项 | E2E |
| C1 | 点「📥 引用参数」→ 弹模态列出全部模板（含 saved_at） | E2E |
| C2 | 模板可「📥 应用」到当前任务（覆盖 layout/font/output/audio；不动 materials） | E2E + 单元测试 |
| C3 | 模板可「✏️ 重命名」和「🗑 删除」 | E2E |
| D1 | 预览时长输入框 + ▲▼ 步进，min=2 max=30 step=1 value=10 | 评审 |
| D2 | 改值后点「🎬 生成预览」→ 实际生成时长 = 输入值 | E2E + 后端 clamp 测试 |
| D3 | 输入越界（>30 或 <2）→ 后端 clamp 至 [2, 30] | 后端测试 |
| E1 | 旧的底部 `slirn-fine-profile-block` 已移除（被 actions bar + 模态替代） | 评审 |
| F1 | 全量测试 393+ 通过 | pytest |