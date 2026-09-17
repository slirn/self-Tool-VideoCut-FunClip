# REQ-20260917-030 — 精剪修订阶段改造：优化字幕（成片重识别 + 不明确字词提取替换）

| 字段 | 值 |
|---|---|
| 编号 | REQ-20260917-030 |
| 日期 | 2026-09-17 |
| 优先级 | P2（精剪修订阶段能力重构） |
| 状态 | ✅ 已实现（单元验证通过；真实浏览器 E2E 见 §5） |
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
- [ ] AC-10 真实浏览器 E2E：有粗剪成片的任务走通「开始 → 分析完成 → 采纳/编辑/不采纳 → 保存 → SRT 下载」全链路

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
- AC-10（真实浏览器 E2E）：**待办** — 重新部署 7861 后执行「开始 → 分析 → 采纳/编辑/不采纳 → 保存 → SRT 下载」全链路（真实 ASR + LLM）。
