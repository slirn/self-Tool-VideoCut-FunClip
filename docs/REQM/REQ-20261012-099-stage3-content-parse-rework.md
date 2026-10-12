# REQ-20261012-099 — Stage 3 改版：解析短视频内容（提示词 + 截图 + 单片段重解析）

## 解决什么问题

[已确认] Stage 3「AI 拆条」升级为「解析短视频内容」：用户可自主录入解析提示词、
上传截图一起发给 AI；产出的每个片段可单独重新解析。提示词支持「默认提示词」
与「最新 10 条历史列表」两级复用。

## 需求清单

### A. 改名（P0）

- [已确认] Stage 3 阶段标题「AI 拆条」→「解析短视频内容」；执行按钮文案改为「解析」。
- [可自主决定] 内部 state key `stage3_analyze`、产物文件名（highlights.json 等）不变（向后兼容）。

### B. 提示词录入（P0）

- [已确认] Stage 3 提供「解析提示词」文本域，用户录入提取短视频内容所用的提示词。
- [已确认] 「解析」按钮把 Stage 2 字幕（raw.srt）+ 提示词 + 截图（可选）发给 AI，
  AI 产出内容信息：字幕时间戳时间段 + 对应字幕内容（即现有 highlights 结构）。
- [已确认] 产出片段数由 Stage 3 的「片段数」参数决定。
- [建议] 原 3 个模板（钩子优先/主题聚类/起承转合）保留为「快速模板」，点击填充文本域；
  每模板的用户自定义覆盖继续生效（填充时优先用自定义版）。

### C. 上传图片（P0）

- [已确认] Stage 3 可上传一张截图；解析时随字幕+提示词一起发给多模态 AI。
- [已确认] 已上传截图可移除；重新上传覆盖旧图。
- [已确认] 上传了图且勾选「发送截图」但当前模型不支持视觉 → 解析直接报错
  （提示到 ⚙️ 大模型设置切换多模态模型），不静默降级。
- [可自主决定] 截图存 `stage3/` 目录（复用 `short_video_file?kind=stage3` 访问），大小限 10MB（沿用 `_call_llm_vision` 限制）。

### D. 提示词两级存储（P0）

- [已确认] 「💾 设为默认」：当前文本域内容保存为默认提示词（全局，跨项目），
  新打开项目时文本域初始填它。
- [已确认] 「📜 存入列表」：保存到提示词历史列表，**最多 10 条最新的**；
  已满 10 条且新提示词不在列表中 → 返回待确认状态，前端弹窗由用户决定是否覆盖
  （覆盖 = 丢弃最旧一条，保留最新 10 条）。
- [已确认] 列表内已存在的相同提示词 → 更新时间戳并置顶，不算新增、无需确认。
- [已确认] 历史列表在 Stage 3 UI 展示，点击载入文本域。
- [可自主决定] 存储位置 `<repo_root>/config/short_video_prompts.json`（跟随 llm_config 的 config/ 运行时目录惯例，已 gitignore）。

### E. 单片段重新解析（P0）

- [已确认] 每个已生成片段卡片提供「🔄 重新解析」按钮：针对该片段的标题/主题，
  用当前提示词 + 截图重新调 AI，替换该片段的时间段与字幕内容。
- [已确认] 重解析后该片段 index 不变；用户已改的标题若 AI 未给出新标题则保留。
- [已确认] 重解析与整体解析一样触发下游 Stage 4/5/6 重置。

## 不做的事（anti-examples）

- ❌ 不改 highlights.json 数据结构语义（下游 Stage 4-6 零改动）。
- ❌ 不做多图上传（本次单张截图）。
- ❌ 不做提示词历史条目的手动删除/重命名（仅覆盖最旧 + 置顶去重）。
- ❌ 不迁移/删除上周的每模板 custom_prompts.json 机制（API 保留，UI 面板移除）。

## 验收标准

1. 打开任一 split 项目，Stage 3 标题显示「解析短视频内容」，按钮显示「解析」。 ✅
2. 文本域初始值 = 全局默认提示词（未设置时 = 当前模板生效文本）。 ✅
3. 点击「快速模板」radio → 文本域被填充该模板生效文本（自定义覆盖优先）。 ✅
4. 上传 png → 显示缩略图；「移除」后缩略图消失且解析不带图。 ✅
5. 上传图 + 勾选发送 + 当前模型非 vision → 点「解析」返回错误信息含「多模态」。 ✅
6. `POST short_video_prompt_history_save`：第 11 条新提示词 → `status=need_confirm`；
   `confirm=true` 重发 → `status=saved` 且列表长度 == 10、最旧一条被丢弃。 ✅
7. 相同提示词重复保存 → 置顶 + 更新时间戳，长度不变，无 need_confirm。 ✅
8. 片段卡片点「🔄 重新解析」→ 该片段 subtitle_lines/start/end 更新，其余片段不变，
   下游 stage4/5/6 状态回 pending。 ✅
9. `pytest tests/test_short_video_service.py tests/test_short_video_api.py` 全绿。 ✅
10. `ruff check` 0 错。 ✅

## 技术约束

- Python 3.10、现有依赖（httpx/fastapi/gradio）；多模态走 `revision_service._call_llm_vision`
  （openai/anthropic 双协议已支持）。
- vision 判定用 `revision_service._is_vision_entry`（用户显式声明优先，启发式兜底）。

## 修订（2026-10-12 验收反馈）

1. **移除快速模板 UI**（radio 组 + 「↺ 重置模板」按钮）；模板仅留作提示词
   初始填充兜底，后端 template 参数与优先级链不变。
2. **历史条目可查看完整内容**：条目改卡片结构（头部行 + `<pre hidden>`），
   点击头部展开/收起；「载入」独立按钮负责填入文本域。
3. **默认提示词在历史中可见**：`save_default_prompt` 尽力同步进历史
   （已存在→置顶；满且不存在→跳过不强占）；渲染时按文本相等标「⭐ 默认」。
4. **截图复选框前加 label**「智能纪要课程重点截图」。
5. **存入列表/设为默认后历史下拉自动刷新**：两端点返回 `history_html`
   片段，前端原地替换 `.slirn-stage3-hist-list` 并自动展开 details
   （不重渲染整页，文本域编辑内容不丢）。

涉及：`short_video_service.py` / `short_video_ui.py`（新增
`render_prompt_history_list`）/ `app.py` / `static/short_video.js` /
`static/home.css`；测试同步更新（`test_short_video_service.py` 的模板 radio
断言反转为「不存在」）。

## 修订二（2026-10-12 验收反馈续）

1. **刷新保持当前页面**：router.js `showTab` 记录 `slirn.lastTab`，刷新后
   `_restoreLastTab`（DOMContentLoaded 时机，等 defer 的 short_video.js 就绪）
   恢复 5 个主导航 tab 之一；short_video.js 以 `slirn.sv.view` 记忆打开的
   项目，进入短视频 tab 时直接重进项目编辑页。返回列表 / 项目删除 /
   打开失败均清空记忆。detail/workbench 依赖导航上下文，不在恢复范围。
2. **导航改名**：「📋 任务列表」→「📋 长视频任务列表」；「短视频混剪」
   （顶栏 + hero）→「短视频任务列表」；tab 骨架加载文案同步。
3. **截图区块标题**改为「智能纪要课程重点截图：」（上一版加在复选框前的
   重复 label 移除，避免同行出现两遍相同文案）。

## 修订三（2026-10-12 验收反馈三）

1. **顶栏「短视频列表」按钮删除**（红框；入口保留在 hero）。
2. **热词库移入顶栏**：位于「大模型设置」按钮左边；hero 不再保留。
3. **设置图标按钮（⚙️）改为文字按钮「大模型设置」**；app.py /
   short_video_analyze.py 中所有用户可见的「顶栏 ⚙️」指引文案同步改为
   「顶栏「大模型设置」」（装饰性 toast 里的 ⚙️ 不动）。
4. **hero 按钮重排**：➕ 新建任务（primary）→ 🎞 短视频列表 → 📋 长视频列表。
5. **大模型设置弹窗宽度加倍**（有效 420→840px）：`.slirn-modal-card.slirn-llm-card`
   双类选择器提高特异度——否则排在后面的通用 `.slirn-modal-card{width:380px}`
   （home.css 后半段）会把它压回去；`max-width: calc(100vw - 48px)` 小屏兜底。

涉及：`app.py` / `short_video_analyze.py` / `static/home.css`；
守卫测试新增 `test_topbar_and_hero_layout` /
`test_llm_settings_modal_width_doubled` /
`test_llm_hint_texts_follow_button_rename`
（`tests/test_short_video_nav_and_refresh.py`）。

## 修订四（2026-10-12 验收反馈四：短视频列表对齐长视频列表）

1. **「➕ 新建任务」从 hero 挪入短视频列表**：列表头部新增 primary 按钮
   「➕ 新建短视频任务」（sv-new：滚动到创建表单 + 聚焦项目名）；原「创建项目」
   区块标题同步改为「新建短视频任务」。hero 只留 短视频列表 / 长视频列表
   两个入口（新建长视频任务的入口仍在长视频列表工具栏）。
2. **scope 过滤（我的任务 / 全部任务）**：radio 仅 admin 渲染（默认 all 保持
   老行为）；普通用户固定 mine — 传 scope=all 也在端点 + `_visible_short_video_tasks`
   双重钳回（与长视频列表语义一致）。radio name 用 `slirn-sv-scope`，
   与长视频的 `slirn-task-scope` 区分，避免 router.js 的 change 分发串台。
3. **搜索**：`#slirn-sv-search` client-side 过滤 `.slirn-sv-card`
   （匹配 项目名 / 任务 ID / 项目 ID），事件代理挂监听（列表重渲染安全），
   无匹配时显示空态 — 完整对齐 `#slirn-task-search` 模式。卡片补 data-task-id。

涉及：`app.py`（hero / `_visible_short_video_tasks(scope)` / 列表端点）/
`short_video_ui.py`（`render_project_list` 加 scope/is_admin 参数）/
`static/short_video.js`（loadList(scope) + change/input 监听 + sv-new）/
`static/home.css`（`.slirn-sv-header-actions`）；
新测试 `tests/test_short_video_list_filters.py`（8 例，含非 admin 钳制端点测试）。
