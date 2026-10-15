# REQ-20261015-121 — Stage 3 系统提示词参数化 + 参数行三列布局与可见边框

> 状态：✅ 已完成（commit `925ea40`，2026-10-15）
> 关联：[REQ-20261015-119](REQ-20261015-119-sv-stage3-duration-hard-constraint.md) /
> [REQ-20261015-120](REQ-20261015-120-sv-stage3-duration-range-setting.md)（区间可配置本体；本需求把「系统默认提示词」也接入参数）

## 1. 需求（用户原话要点）

1. 系统默认提示词里的**时长参数与片段数**要根据参数动态加入到提示词上；
2. 页面要明确**时长下限/上限是每个片段**的口径；
3. 三个参数（片段数/下限/上限）**放同一行**；
4. 数值 input 框**边框要显示出来**（现在看不到边框，一片白）。

## 2. 根因 / 现状

| 问题 | 根因 |
|---|---|
| 提示词写死 | `STAGE3_SYSTEM_PROMPT` 全文硬编码「60-90 秒」「若干条」；用户改了区间再「引用系统提示词」，引用来的文本反而覆盖用户设置 |
| 第三个参数换行 | `.slirn-sv-form-grid` 是 `repeat(2, ...)` 两列网格，3 个字段必然换行 |
| 边框不可见 | 输入框边框色取 `--border: rgba(148,163,184,0.2)`——20% 透明度在白底上肉眼不可见 |

## 3. 实现

### 3.1 提示词参数化（service + JS 双端渲染）

- 常量改为模板 `_STAGE3_SYSTEM_PROMPT_TMPL`，占位符
  `{n}/{dmin}/{dmax}/{dmin2}/{dmax2}/{ex_sec}/{ex_lines}`（文本无字面花括号，
  `.format` 安全，测试守卫）；
- `stage3_system_prompt(n_clips, dmin, dmax)` builder：条数（「若干条（条数以
  任务要求为准）」→「{n} 条」）、硬约束区间、行数指引（区间半值）、口径示例
  （区间中值 / 每行 2.2s）全部按参数渲染；非正值回默认 60-90、倒挂互换；
- `STAGE3_SYSTEM_PROMPT = stage3_system_prompt()` 默认渲染（4 条 / 60-90），
  既有引用方（app.py load_prompts、UI data-prompt、测试）零影响；
- UI「🧭 引用系统提示词」按钮新增 `data-prompt-tmpl`（模板原文）；JS
  `_stage3RenderSystemPrompt()` 在**点击时**按当前三个输入框的值替换——用户
  先改参数再引用，文本即与设置一致；模板缺失回退 `data-prompt`；
- Python/JS 同公式（dmin2/dmax2=半值、ex_sec=中值、ex_lines=ex_sec/2.2），
  node DOM 桩验证两端输出一致。

### 3.2 参数行 UI

- 标签：「时长下限(秒)」→「**每条时长下限(秒)**」，上限同理，tooltip 同步
  明确「每个片段的总时长」；
- 三列一行：`.slirn-sv-form-grid--3col { grid-template-columns:
  repeat(3, minmax(0,1fr)) }`，≤900px 折叠单列（加入既有 media 断点）；
- 边框：`--3col input[type="number"] { border: 1px solid #94a3b8 }`——
  实色，明暗主题都可见。

## 4. 测试（+7，全量 1290 passed）

builder 动态参数（3 条/40-80/20-40 行/≈60 秒，无 60-90 残留）、兜底与倒挂
互换、常量=默认渲染、模板 token 完整性（无多余花括号）、UI 标签+同容器+
模板属性、CSS 三列+实色边框+窄屏折叠、JS 渲染接线。

## 5. E2E

- 真实项目页面（7861 重启后）：三参数同容器、标签、`data-prompt-tmpl`
  含 token、`data-prompt` 兜底齐全；
- 注入 CSS 含 `--3col` 规则 + `#94a3b8`（launch 注入需重启，已重启）；
- node DOM 桩：`_stage3RenderSystemPrompt` 输出「3 条（40-80 秒）需要
  20-40 行，选 27 行 → ≈60 秒，落在 [40, 80]」与 Python builder 一致。

## 6. 已知边界

- 用户把渲染后的系统提示词「💾 设为默认」后，再改区间不会自动改已存默认
  （已存文本是快照）——但解析端的核查/重试仍按实际参数执行（REQ-119/120），
  仅提示词文案可能滞后；需要新文案时重新「引用」即可。
- push 时本机代理（127.0.0.1:21081）宕机，用 `git -c http.proxy= -
  -c https.proxy= push` 直连成功。
