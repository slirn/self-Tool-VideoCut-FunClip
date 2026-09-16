# REQ-20260916-007 — 分析严谨性新增「自定义」档（默认提示词可编辑 + 一键恢复默认）

## 需求原话

> 在执行字幕修订时，选择分析严谨性级别时，添加一个自定义严谨性，并提供默认的提示词，用户可以在这个提示词的基础之上进行修改，并为这个自定义提示词提供一个恢复默认提示词的功能

## 设计

### 服务端（slirn_home/revision_service.py）

- `CUSTOM_RIGOR_KEY = "custom"`：自定义档的 key，与 `high / medium / low` 并列。
- `default_custom_prompt()`：默认提示词底稿 = `build_system_prompt("high")`。
  高档「严格打磨」的判定标准最完整（含内容更正 fix 判定与 JSON 输出要求），作为可编辑底稿信息量最大；用户在其上做减法比在低档上做加法更可控。
- `resolve_system_prompt(rigor, custom_prompt)`：
  - `custom` → 返回 `custom_prompt.strip()`（**原样注入，不与底稿拼接**）；空白/None 回退默认底稿（与服务端、前端「恢复默认」同源）。
  - 非 custom 且不在 `RIGOR_LEVELS` → `ValueError("未知严谨性级别: …（可选 高 / 中 / 低 / 自定义）")`。
- `start_job(..., custom_prompt=None)`：经 `resolve_system_prompt` 解析系统提示词；meta 留痕
  `"custom_prompt": system_prompt if rigor == CUSTOM_RIGOR_KEY else None`（全文入 meta，分析可复现）。

### 前端（slirn_home/app.py）

- `_render_rigor_picker()` 第 4 张单选卡：`value="custom"`，文案「自 · 自定义严谨性」；卡片网格改 `auto-fit`（home.css）自适应 4 张。
- 编辑区 `#slirn-rigor-custom`：
  - 服务端 `display:none` 下发；`data-default-prompt` 属性携带底稿全文（`_esc` 转义，换行在属性中原样保留，`getAttribute` 取回无损）；textarea 服务端置空，由 JS 预填。
  - `syncRigorCustomUI()`：仅选中 custom 时展开；首次展开预填草稿（localStorage `slirnRevCustomPrompt`），无草稿用底稿；`ta.dataset.slirnFilled` 标记防切走再切回时覆盖已编辑内容。
  - `input` 事件实时存草稿（不依赖提交）；`change` 事件（radio）触发同步。
  - 「↩️ 恢复默认提示词」按钮（`data-action="rigor-prompt-reset"`）：textarea ← `data-default-prompt`，草稿同步，toast 提示。
- 提交（revise-sub）：选中 custom 时 `payload.custom_prompt = textarea.value`；空白由服务端回退底稿。
- 端点校验：`rigor != custom 且 rigor not in RIGOR_LEVELS` → 报错「请先选择分析严谨性级别（高 / 中 / 低 / 自定义）」。
- 统计行：`rigor == "custom"` → 显示「 · 严谨性 自定义」。
- 严谨性回填 `applyRevRigorState` 枚举扩为 `['high','medium','low','custom']`（localStorage `slirnRevRigor` 可含 custom）。

### 样式（slirn_home/static/home.css）

- `.slirn-rigor-cards`：`grid-template-columns: repeat(auto-fit, minmax(180px, 1fr))`（3→4 张自适应）。
- `.slirn-rigor-custom`：虚线边框（--accent-soft）+ 浅底；`-head` flex 标题/按钮两端对齐可换行；`-text` 等宽字体（Consolas/Menlo）12.5px、min-height 150px、宽 100%；`-tip` 11.5px 灰字提示（保留 JSON 格式部分 / fix 判定建议保留 / 草稿仅本机）。

## 验收标准

1. 严谨性选择器出现第 4 张「自 · 自定义严谨性」卡，无预选语义与 REQ-003 保持一致。✅
2. 选中自定义 → 展开编辑区，textarea 预填默认底稿（= 高档系统提示词，含 fix 判定与 JSON 输出要求）。✅
3. 用户可自由修改提示词；草稿实时存 localStorage，重开工作台后恢复。✅
4. 「↩️ 恢复默认提示词」一键还原底稿（textarea + 草稿同步）。✅
5. 提交分析时自定义提示词**原样**作为系统提示词发送；空白提交回退默认底稿。✅
6. meta 留痕 custom_prompt 全文（rigor=custom 时）。✅
7. 切走（高/中/低）编辑区收起，切回内容保留不覆盖。✅
8. 统计行显示「严谨性 自定义」。✅

## 验证记录

- `ruff check` clean；`pytest tests/ -v` → **144 passed**（新增 `test_resolve_system_prompt_custom`：底稿=高档、custom 原样/空白回退/None 回退/未知级别 ValueError；`test_start_job_custom_rigor`：mock LLM 捕获 system==自定义原文、meta 留痕、空白回退；渲染断言 4 卡 + 编辑区结构 + 统计「自定义」）。
- 浏览器实测（CDP headless，任务 20260915-002 只读）7 步全绿：
  1. 4 卡无预选、编辑区隐藏 textarea 空
  2. 选自定义 → 展开预填底稿（含 fix/输出要求，len>200）
  3. 追加自有规则 → 草稿实时落 localStorage
  4. 切高收起 / 切回内容保留
  5. 恢复默认 → textarea/草稿均回底稿
  6. 重开工作台 → 草稿恢复到 textarea
  7. radio/localStorage 复原；`performance` 计零 `revise_subtitle`/`save_revision` 请求（**用户数据零改动、零 LLM 消耗**）
- 视觉复核（截图 + 视觉模型）：4 卡一行并排整齐、自定义卡蓝紫描边选中态明确、编辑区结构齐全（标题/恢复默认按钮/等宽 textarea 预填底稿/底部灰字 tip）、无重叠溢出、无排版缺陷 → 通过。

## 非目标

- 跨设备同步自定义提示词（仅存本机浏览器 localStorage，与快捷键设置一致）。
- 提示词模板变量/占位符系统（当前为纯文本原样注入）。
- 多份自定义提示词命名保存（当前单草稿）。
- 用自定义提示词直改历史 meta（仅新分析留痕）。

## 关联

- 前置：REQ-20260916-003（严谨性必选）、REQ-20260916-006（fix 内容更正，底稿含其判定标准）
- 提交：`feat(home): 分析严谨性级别新增自定义档+默认提示词可编辑 (REQ-20260916-007)`
