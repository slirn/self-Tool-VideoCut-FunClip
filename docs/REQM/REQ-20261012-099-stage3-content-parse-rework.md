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

## 修订五（2026-10-12 验收反馈五：Stage 3 接入上游「大模型智能剪辑」+ 拖拽排序 + 截取拼接）

用户验收三件事（含图 4.png）：

1. **调用后台功能**：点「解析」复用上游首页「大模型智能剪辑」对应后台链路
   （`funclip/launch.py` `llm_inference` 同款）——system =「你是一个视频srt
   字幕分析剪辑器…输出需严格按照如下格式：1. [开始时间-结束时间] 文本」，
   user =「这是待裁剪的视频srt字幕：」+ raw SRT + 编号列表格式约束
   （约束拼在 user 尾部，含「忽略上文的其他输出格式要求（包括 JSON）」，
   防 system 被自定义覆盖后解析器失配）。上游的「不低于 50% 时长」备注
   **刻意不加**——与拆条（n 条片段）目标冲突。解析按出现顺序**自动重编号**
   （LLM 自报序号不可信），subtitle_lines 由时间重叠映射回 raw.srt 行，
   highlights.json schema 不变 → Stage 3.5/4/5/6 零改动。模型仍按旧
   JSON 输出时走兜底解析（raw 含 "highlights" → 旧 `_strip_json_block`
   路径）；用户自定义提示词仍最优先。
2. **拖拽调整顺序（编号不变）**：行首 ⇕ 把柄按下才开启行级 draggable
   （默认不可拖，避免与标题输入 / 行内字幕行拖拽冲突；事件全走 document
   代理，重渲染免重绑）。drop 后 DOM 顺序 POST 持久化为 `splice_order`，
   片段 `index` 永不重编——编号随行移动，原始顺序号天然保持。
   重排后已拼接结果标记 stale（「拼接顺序已调整，请重新拼接」）。
3. **片段截取与拼接**：`run_stage3_splice` 按 splice_order 从源视频逐段
   `subclip`（start +0ms / end +100ms 防切词尾，同 Stage 4 默认）+
   `concatenate_videoclips`，产物 `stage3/splice.mp4` + 重建的
   `splice.srt`（按新顺序重编号 + 时间轴按段重定位）；拼接卡显示
   「拼接顺序：#a → #b → #c」+ 播放 / 字幕预览 + 时长片段数。新管线状态
   `stage3_splice` 接入三个重置路径（重跑解析 / 单片段重解析 / 拖拽重排）。

附带：导航入口「短视频列表」强制回列表视图（`showTab(target, {forceShortVideoList})`，
不恢复上次编辑页记忆）。

涉及：`short_video_analyze.py`（上游提示词常量 + `_parse_llm_clip_segments`
解析器 + `reorder_splice_order`）/ `short_video_coarse.py`
（`build_splice_plan` + `_render_splice_video` + `run_stage3_splice`）/
`short_video_service.py`（`stage3_splice` 状态注册）/ `app.py`
（reorder_highlights / splice 两端点）/ `short_video_ui.py`（⇕ 把柄 +
拼接卡）/ `short_video.js`（把柄门控行拖拽 + stage3Splice）/
`static/router.js`（forceShortVideoList）/ `home.css`；
新测试 `tests/test_short_video_stage3_llm_clip.py`（17 例：
上游提示词保真 / 解析器变体 / run_stage3 链路 / 拖拽重排 / 拼接含
SRT 重建 / 端点 / UI 渲染 / JS 接线 + node --check）。

已知无关失败：`test_short_video_render_smoke_9x16` 全量套件下 ffmpeg
「Cannot allocate memory」（干净 HEAD 同样失败，本机内存压力；单独运行
通过）——与本次改动无关。

## 修订六（2026-10-12 验收反馈六：分组短视频解析 + OCR 参考方向 + 还原顺序 + 逐组拼接）

用户验收四件事（含图 5.png）：

1. **N 组 = N 条独立短视频**（修正修订五「合成一条」的误读）：解析产出即
   最终要剪出的 N 条短视频；每条围绕一个标题展开、全部字幕时长合计约
   70 秒（后续还会删字，留余量）；组内字幕顺序按该标题的叙事需要排列，
   **可以与 srt 原顺序不同**。片段数 N（3~5，上游页面同款参数）从 Stage 3
   「片段数」控件注入提示词。输出格式 = `# 组序号 | 短视频标题` 组头行 +
   `行号. [HH:MM:SS,mmm-HH:MM:SS,mmm] 文本` 编号行（约束仍拼 user 尾部，
   含「忽略上文的其他输出格式要求（包括 JSON）」）。
2. **截图先 OCR 提文字**：解析前先用多模态模型专用调用提取「智能纪要课程
   重点」截图里的全部文字（`stage3/ocr_text.txt` 落档；同一张图缓存复用，
   换图重提取），提取文字以「【课程重点参考……仅作为选题与叙事方向的参考，
   不要求逐字使用】」段头拼进 user payload 头部；**主解析调用始终纯文本**。
   OCR 失败 → stage3 failed（错误含「截图文字提取失败」）；提取为空 → 告警
   并跳过参考段。UI 显示「✅ 截图文字已提取（N 字）」+ 查看提取文字入口。
3. **行唯一编号 + 还原顺序**：组内字幕行按 LLM 输出顺序编 `no` 1..M，
   用户拖拽重排后 no 随行移动**永不改变**；每行显示编号 + 时间戳
   （mm:ss → mm:ss）+ 文本。行头「↩️ 还原顺序」按钮按 no 升序稳定排序
   还原大模型最初顺序（当前已是原序时禁用）；还原写回 highlights.json +
   state 镜像 + `reset_downstream_stages`（含 stage3_splice 作废）。
4. **逐组截取拼接**：`run_stage3_splice` 重写——每组按**当前 subtitle_lines
   行序**逐行切源视频（行自带时间戳优先，缺失查 raw.srt 源行；end +100ms
   防切词尾）拼接成独立 `stage3/clip_<N>.mp4` + 重编号/时间轴重定位的
   `clip_<N>.srt`；状态 `stage3_splice.clips` 列出各组产物（index/title/
   mp4/srt/total_sec），UI 逐条显示「#N 标题 · 时长 [🎬 播放][📖 字幕]」。
   旧产物（修订五 splice.* + 上轮 clip_*）自动清理。

实现要点：
- 解析器 `_parse_llm_clip_groups`（替代修订五 `_parse_llm_clip_segments`）：
  组号按出现顺序重编（LLM 组号不可信）；每行与 raw.srt 做**单行最佳重叠**
  匹配（时间/文本以源行为准，未匹配上保留 LLM 时间戳并告警）；无组头行时
  扁平兜底（每行各自成组，修订五格式兼容）；组数超 N 截断告警；组内合计
  <45s / >110s 告警（目标 70s）。
- 修订五拼接顺序机制**整体移除**：`splice_order` / `reorder_splice_order` /
  `build_splice_plan` / 行拖拽 ⇕ 把柄 / `short_video_stage3_reorder_highlights`
  端点全部删除（N 条独立视频语义下组间顺序无意义）；新端点
  `POST /slirn/api/short_video_stage3_restore_order`。
- JSON 兜底路径（raw 含 "highlights"）保留，`_annotate_subtitle_lines`
  补 no/start_ms/end_ms，旧模型输出不致整跑失败。
- 单片段重解析 `rerun_stage3_one` 同步升级：同一套分组格式（n=1）+
  OCR-first 参考方向。
- highlights.json 顶层加 `flow: "llm_groups"` 溯源；`splice_order` 不再写。

涉及：`short_video_analyze.py`（分组提示词常量 + OCR 常量 + 解析器 +
`_ocr_stage3_image` + `restore_hl_order` + run/rerun 重构）/
`short_video_coarse.py`（`run_stage3_splice` 逐组重写 + `_hl_line_window`）/
`app.py`（restore_order 端点替换 reorder_highlights；splice toast）/
`short_video_ui.py`（行 no+时间戳渲染 + 还原按钮 + 逐条 clip 列表 +
OCR 提示）/ `static/short_video.js`（stage3RestoreOrder + 删行拖拽块）/
`home.css`（hl-no chip / clip 列表 / ocr 提示；删 dragseg 规则）；
测试 `tests/test_short_video_stage3_llm_clip.py` 全量重写（20 例）+
`tests/test_short_video_stage3_rework.py` 视觉测试改 OCR-first（38 例全过）。

验证：全量 pytest 1165 passed / node --check OK / ruff HEAD-vs-worktree
无新增违规 / 7861 重启后实测（新 JS 已上、旧拖拽 JS 已清、新 CSS 类注入）。

## 修订七（2026-10-13 验收反馈）：行格式/文字颜色 + 只显示一组 bug + 预览窗口拖拽调宽

用户验收三件事：

1. **行格式 + 文字颜色**：每行 `编号. [hh:mm:ss,ms → hh:mm:ss,ms] 字幕文本`
   （`_ts` 从 mm:ss 改完整 SRT 式毫秒，区间带方括号）；字幕文本此前在深色
   预览底上"看不到文字"。
   - 根因（CDP 实测定位）：Gradio 主题样式表含
     `.gradio-container-6-17-3 .prose * { color: var(--body-text-color) }` ——
     gr.HTML 输出被包在 `.prose` 里，**所有后代被直接声明 color**（直接声明
     压过继承，且其在 Tailwind @layer 内、非 layer 的自有规则可赢）。
     `.slirn-stage3-hl-sub`/chips 自带显式色不受影响；`.slirn-stage3-hl-text`
     无任何 color 规则 → 被强制成 `#1f2937` 深字落在 `#1e293b` 深底上。
   - 修复：`.slirn-stage3-hl-preview .slirn-stage3-hl-text { color: #f1f5f9 }`
     （双类特异度 + 非 layer 稳赢）；`.slirn-srt-pre` 固定浅底配固定深字
     `#0f172a`（暗色主题原先同样被强制成浅字 → 白底白字）。
2. **只显示一组（如只剩 3 号）**：显示层 bug，生成没问题（磁盘 doc 5 组完整）。
   - 根因链：`rerun_stage3_one` 在 state 镜像为空时 append 出**单组镜像**落盘 →
     之后整跑 `run_stage3` 重写 doc 为全新 N 组但**不清镜像** → GET 层
     `_inject_stage3_highlights` 用 `if not s3.get("highlights")` 跳过注入 →
     页面渲染过期单组（37 行旧内容）。
   - 修复（多入口状态对称）：注入改**按 doc 整表覆盖**（doc=source of truth，
     GET 不落盘只影响渲染）；`rerun_one`/`restore_hl_order` 镜像同步改
     `state["highlights"] = hls` 整表；`run_stage3` done 块 pop 过期
     `highlights`/`srt_lines` 镜像。用户下次打开页面即自愈，无需迁移。
3. **预览视频窗口可拖动 + 可调整大小**：预览 modal（🎬 播放/📖 字幕）原本
   固定居中——新助手 `_enableWinDrag`（标题栏 mousedown → 卡片切绝对定位
   跟手移动，钳制在视口内）+ `_enableWinResize`（右下角 ◢ 把柄拉宽，解除
   max-width 上限，视频随 max-width:100% 跟随）；Stage 3 悬浮播放器原本已
   可拖、补 ◢ 调宽 + 尺寸记忆 `slirn.sv.fp.size`（位置本就记忆
   `slirn.sv.fp.pos`）。

涉及：`short_video_ui.py`（`_ts` 完整毫秒 + `[...]` 区间）/ `home.css`
（hl-text 显式色 / srt-pre 深字 / ◢ 把柄 + 标题栏 move 光标）/
`app.py`（注入整表覆盖）/ `short_video_analyze.py`（三处镜像对称）/
`static/short_video.js`（拖动/调宽助手 + modal/悬浮窗接线）；测试新增
5 例（陈旧镜像自愈 / 整跑清镜像 / rerun+restore 整表对称 / 颜色守卫 /
拖拽调宽接线）+ 时间戳断言更新，文件内 29 例。

验证：全量 pytest **1171 passed** / node --check OK / ruff HEAD-vs-worktree
零新增 / 7861 重启后 CDP 实测：hl-text 计算色 rgb(241,245,249)（修复前
rgb(31,41,55) 不可见）、时间戳 `[00:34:38,090 → 00:34:39,450]`、srt-pre
深字浅底、served JS 含拖拽调宽接线。

## 修订八（2026-10-13 验收反馈）：预览逐段播放 + 删除确认 + 重解析提示词按主题重组

用户验收三件事：

1. **预览片段按字幕时间戳逐段播放**：原实现只 seek 到组起点然后连播到组
   终点（把未选中的空隙也播了，观感=「从第一个字幕顺序播放」）。改为预览
   按钮 `data-segments` 直接携带逐段切割窗（**当前行序**、行时间戳优先、
   缺失查源行、end+100ms 词尾——与 `run_stage3_splice` 完全一致），悬浮窗
   逐段 seek：播完一段跳下一段起点，最后一段结束暂停；用户手动拖进度条
   （seeking 跳出现段）自动退出逐段推进，不跟手动操作抢。无 segments 数据
   时回退旧的组起点连播。
2. **删除按钮**：文案「删」→「删除」；原先**点了没任何反应**——UI 有按钮
   但 JS 无 handler、后端无端点（本轮补齐 `stage3RemoveHl` 前端 confirm →
   `POST /slirn/api/short_video_stage3_remove_hl` → `remove_stage3_hl`）。
   语义：前端弹 confirm（含标题回显），确认后真删；其余条目 **index 不重编**
   （编号是 rerun/restore/clip 命名的稳定标识，留空洞是刻意行为，重跑解析
   才整体重编）；doc+镜像整表同步、下游/拼接作废、删光回「解析后将列出」态。
3. **重新解析提示词重组**：
   - OCR 参考不再给全量截图文字——`_filter_ocr_for_title` 按标题 2-gram
     （CJK 滑窗，标点跨界 gram 丢弃）过滤只给相关行，12 行上限，无命中
     给空串（宁缺毋滥）；节选声明用重解析专用模板（「与主题「X」相关的
     参考方向（…节选…）」）。
   - user payload 主题声明前置：「本次任务：只解析主题「{title}」这 1 条
     短视频」；结尾约束加「选中的字幕行要按叙事逻辑有条理地组织」。
   - system 定向指令（`_rerun_one_suffix`）升级：只选与该主题直接相关的
     字幕行、不引入其他主题；**有条理地组织**成有起点/展开/收束的连贯短视频。

涉及：`short_video_ui.py`（data-segments 渲染 + 删除文案）/
`short_video_analyze.py`（`_filter_ocr_for_title` + `_RERUN_OCR_REFERENCE_TMPL`
+ `_rerun_one_suffix` 重写 + rerun user_payload 重组 + `remove_stage3_hl`）/
`app.py`（remove_hl 端点）/ `static/short_video.js`（逐段播放引擎 + 手动
seek 退出 + `stage3RemoveHl` confirm 分发）；测试新增 6 例（OCR 过滤单测 /
rerun 提示词捕获 / 删除全流程含删光 / segments UI 断言 / JS 接线守卫），
文件内 34 例。

验证：全量 pytest **1176 passed** / node --check OK / ruff HEAD-vs-worktree
零新增 / 7861 重启后 served JS 含逐段引擎与 confirm 接线（11 处匹配）。
