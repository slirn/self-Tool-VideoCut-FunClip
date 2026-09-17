# REQ-20260917-030 — 精剪修订阶段改造：优化字幕（成片重识别 + 不明确字词提取替换）

| 字段 | 值 |
|---|---|
| 编号 | REQ-20260917-030 |
| 日期 | 2026-09-17 |
| 优先级 | P2（精剪修订阶段能力重构） |
| 状态 | ✅ 已完成（单元 + 真实浏览器 E2E 全过） |
| 关联 | REQ-20260916-017（原精剪修订·热词替换，本需求**替换**其交互流程）；REQ-20260915-001（字幕生成，识别链路复用）；REQ-20260916-016（粗剪合成，成片为本次输入） |
| 改动范围 | `slirn_home/`（新建 optimize_service + app 阶段面板/端点/JS + CSS）；tasklib 零改动（状态枚举不变）；funclip/ 零改动 |

---

## 0. 背景

现「精剪修订」阶段（REQ-20260916-017）只对**切分保留行的文本**按任务热词做替换，
输入是管线中间产物。用户要求把它改造为「优化字幕」：以**粗剪合成生成的成片**
为准重新识别字幕，再用大模型做语义分析，提取**不明确的字/词**（不限于热词表），
统计次数、定位位置，人工分辨后替换，并保存替换对应关系。

## 1. 核心需求

| ID | 需求 |
|---|---|
| REQ-1.1 | 阶段更名：工作台阶段「精剪修订」→「优化字幕」（TaskStatus 枚举值不变，仅展示文案） |
| REQ-1.2 | 本阶段输入 = **粗剪合成成片（rough_compose.mp4）+ 任务热词**：把成片和热词传给 ASR 模型，**只识别字幕信息**（不做说话人分离），得到成片时间基的字幕行集 |
| REQ-1.3 | 识别结果交大模型语义分析：提取**不明确的字/词**（疑似同音/近音误识别、语义不通、术语可疑、与热词表冲突的片段），每处给出替换建议与理由 |
| REQ-1.4 | 统计每个不明确字/词的**出现次数**，并**定位其使用位置**（所在字幕行 + 行内位置 + 成片时间点可跳播核对） |
| REQ-1.5 | 人工分辨与替换：逐处可**采纳/不采纳**，替换值**可人工编辑**（不局限于模型建议） |
| REQ-1.6 | 替换保存时落盘**替换对应关系**（原文片段 → 替换后文字，含所在行/位置/次数统计），并生成替换后的成片优化字幕（含 SRT 导出） |
| REQ-1.7 | 旧任务兼容：已有 fine_revision.json 的旧任务不受影响（粗剪面板的替换并入展示照旧）；旧阶段已推进的 FINE_SUBTITLE_REVIEWED 状态保持有效 |

## 2. 验收标准

- [x] AC-1 工作台阶段名显示「优化字幕」，说明文案与新流程一致；TaskStatus 枚举与既有任务状态不回归
- [x] AC-2 无 rough_compose.mp4 时阶段给出引导（先去粗剪合成）；有成片 + 已注册大模型即可开始，热词为空不阻塞（提示后仍可分析）
- [x] AC-3 点击开始后后台执行：ASR 识别成片（带任务热词、不带说话人）→ 大模型分批分析 → 落盘 optimize_subtitle.json；全程进度轮询可见，失败有明确错误
- [x] AC-4 结果面板：不明确字词按目标词分组统计「词 ×次数」，点词可过滤/定位到出现行；点行按成片时间跳播核对
- [x] AC-5 每处出现可单独采纳/不采纳；替换值可编辑；保存时只对「采纳且替换值有效」的条目生效
- [x] AC-6 保存后 optimize_subtitle.json 含：识别行集（含替换后文本）、逐处替换记录（before/after/位置/采纳态）、按词聚合统计；saved_at 置位 → 阶段完成（推进 FINE_SUBTITLE_REVIEWED）
- [x] AC-7 可下载替换后的成片优化字幕 SRT（时间基 = 粗剪成片）
- [x] AC-8 旧任务（有 fine_revision.json 无 optimize_subtitle.json）：粗剪面板字幕并入展示不回归；本阶段面板显示新流程（重新开始优化）或旧已完成状态
- [x] AC-9 既有 + 新增 pytest 全过，ruff 0 错
- [x] AC-10 真实浏览器 E2E：有粗剪成片的任务走通「开始 → 分析完成 → 采纳/编辑/不采纳 → 保存 → SRT 下载」全链路

## 3. 非目标

- ❌ 不改 `funclip/` 上游源码、不改 tasklib 状态机（沿用 FINE_SUBTITLE_REVIEWED）
- ❌ 不删除 fine_service.py 与旧 fine_revision.json 的读取兼容（旧任务数据保留可读）
- ❌ 不做替换关系的跨阶段回写（不反灌切分行/原字幕；优化字幕是成片时间基的独立产物）
- ❌ 不做自动替换（一切替换必须经人工确认）
- ❌ fine_cut（精剪视频）阶段仍是规划中，不在本需求内

## 4. 口径说明

- **不明确字/词**：ASR 结果中疑似误识别的片段 —— 含但不限于：与热词表冲突、
  同音/近音可疑、语义不通、术语可疑。判定由大模型给出（附理由），**最终由人工分辨**，
  模型只提议不决策。
- **词频口径**：按**替换目标词**（after）分组计数；同一目标词的多种错误写法
  （「神精网络」「神经网洛」→「神经网络」）计为同一词的多次出现。
- **一句话/一行**：成片 ASR 断句后的一个字幕段。
- **替换对应关系**：每处出现一条记录（行号、行内位置、原文片段、替换后文字、
  是否采纳），按词聚合统计随文件保存。

## 5. 验证记录

**2026-09-17（实现 + 单元验证）**

- 质量：`ruff check slirn_home/ tests/` 0 错；`pytest tests/ -q` **235 passed**（新增 `tests/test_optimize_subtitle.py` 19 例）。
- AC-1：`test_render_workbench_layout`（阶段名「✨ 优化字幕」+ 说明）、`test_wb_stage_states_optimize_done`（枚举序不回归；旧 fine_revision.json 兼容 done）。
- AC-2：`test_render_zone_guide_without_artifact`（无成片引导去粗剪合成）、`test_render_zone_start_state`（成片+模型 → 开始态，热词回显）。
- AC-3：`test_start_job_writes_artifact`（ASR→LLM→落盘全流程，monkeypatch 假实现；sd=False）、`test_start_job_llm_all_fail_still_writes`（LLM 全败不阻塞，识别结果+空出现项仍落盘）。进度轮询走 job 状态端点（同既有 ASR/合成阶段模式）。
- AC-4/AC-5/AC-6：`test_render_zone_result_state`（词频 chips `词<b>×N</b>`、行内 widget、data-words/data-start-ms、未确认 SRT disabled）、`test_save_decisions_roundtrip`（全量口径：未列出=不采纳）、`test_save_decisions_edited_after`（人工编辑值生效；改回原文/空值兜底不采纳；new_text/words/mapping/stats/saved_at 重算）。
- AC-7：`test_build_srt_prefers_new_text`（new_text 优先、未采纳保留原文、空行分隔）；端点须 saved_at（渲染断言 disabled 态）。
- AC-8：fine_service.py 原样保留（`test_fine_revision.py` 全过）；旧 fine_revision.json 有 saved_at → 阶段 done（`test_wb_stage_states_optimize_done`）。
- 设计约束复核：optimize_service 只调 `asr_service._run_recognition/segments_from_sentences`（不触 `start_job`、不写 subtitle.json）；funclip/ 零改动；tasklib 仅展示文案（枚举值不变）。
- AC-10（真实浏览器 E2E）：见下方 2026-09-17 E2E 记录。

**2026-09-17（E2E 排查中发现的两个产品缺陷，已修复）**

1. **路由 JS 全站失效（Gradio 6.17.3 `head=` 反斜杠解码）**：E2E 首跑发现页面所有 `data-action` 按钮无响应。逐层排查（CDP 探针 ×12）定位：`gr.HTML(head=...)` 的传输链路会把脚本文本里的反斜杠转义解码（`\n`→换行、`\x20`→空格、`\\`→`\`、孤立 `\` 被删除）——~115KB 路由脚本里 22 个 `\` 被吃掉 → SyntaxError → 事件委托监听从未注册。headless 与真实 Chrome 均复现（产品级缺陷，非测试环境问题）。
   **修复**：路由 JS 迁出为静态文件 `slirn_home/static/router.js`（`GET /slirn/static/router.js`，no-cache），`_build_head()` 只注入无反斜杠的 `<script src>` + theme 脚本；`router.js` 加幂等绑定保护；顺带删除 `_register_slirn_api` 重复调用。验证：CDP 下 `document` click 监听 2 个（gradio + 路由）、refresh-tasks 点击发出真实请求。
   > 排查要点：`document.querySelectorAll('script')` 会匹配到 265KB 的 `window.gradio_config` 脚本（内含所有 props 的转义副本），须排除否则 diff/re-exec 全在查错副本。
2. **提取提示词过弱（LLM 对杂乱口语返回空）**：真实跑批 MiniMax-M3 对含明显可疑片段的闲聊转写（「八四」「视频视频」「happy house」）返回 `[]`。强化 `OPT_SYSTEM`：列重点信号（近音误写/突兀数字字母/语义不通/与热词矛盾/叠字重复）、明确「宁多提议」「至少评估出最可疑几处」、after 只写替换文字。同转写复测：7 处出现项（八四→把试、happy house→Happy Hour、视频视频→视频 等），解析/重叠剔除全通过。

**2026-09-17（AC-10 真实浏览器 E2E + 复检兜底）**

- **AC-10 ✅**：CDP 真实点击全链路（任务 20260917-009，真实 ASR + MiniMax-M3）：
  开始 → 分析完成（识别 31 行 · 不明确 4 处 4 词）→ 词筛选（隐藏 28 行/复显）→
  行点击跳播（row@4550ms，|Δ|<3s）→ 第 1 处 toggle 不采纳 + 第 2 处编辑为
  「84（改）」→ 保存（已确认 · 生效 3 处 · 未采纳 1 处 · SRT 解禁 · 阶段 done）→
  SRT 下载按钮 + 页内 fetch（31 行、`-->`、空行分块）→ 磁盘校验
  （saved_at / occs[0].applied=False / occs[1].after 含「（改）」/ new_text /
  words / mapping / TaskStatus.FINE_SUBTITLE_REVIEWED）全一致。
- **零发现复检兜底**：E2E 第 5 跑发现 MiniMax 抽样波动下可能整段返回 `[]`
  （复测 3/3 次为 6-8 处，但偶发 0）→ start_job 增加首轮全批零发现且 ≥5 行时
  严格复检一轮（OPT_STRICT_SUFFIX：降门槛 + 至少 3 处候选）；复检后仍零视为
  干净。单测 `test_start_job_zero_found_strict_retry`。
- **E2E 环境坑（记录备查）**：① 页内 fetch 不能用 `window.SLIRN_API`（IIFE 内部
  变量，不在 window）→ 404 JSON 致断言失败，用字面量 `/slirn/api/...`；
  ② 7861 启动瞬间 Gradio 自检失败走 recovery rebuild 时，早开页面可能路由未绑
  → 刷新重试前先整页重载。
- 质量门：`ruff check` 0 错；`pytest tests/ -q` **241 passed**
  （新增零发现复检 1 例 + 时区显示 5 例，见下）。

**2026-09-17（附带修复：任务时间显示差 8 小时）**

- 用户反馈「新建任务都显示 8 小时前」。根因：tasklib 存**带时区 UTC**
  （规范），`_time_ago`/`_stats`/工作台「创建时间」直接剥 `tzinfo` 把 UTC 当
  本地用，再与本地 `now()` 相减 → UTC+8 下刚建即「8 小时前」。
  修复：新增 `_to_local_naive`（aware → `astimezone()` 转本地），三处显示统一
  走它；`_fmt_local` 输出本地 `YYYY-MM-DD HH:MM:SS`。部署后实测任务列表显示
  「7 分钟前 / 20 分钟前」正确。单测 5 例（`test_slirn_home.py`）。
