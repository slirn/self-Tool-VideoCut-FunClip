# REQ-20260917-038 — 不明确字词频次列表 + 处理完成标识 + 按标识过滤

| 字段 | 值 |
|---|---|
| 编号 | REQ-20260917-038 |
| 日期 | 2026-09-18 |
| 优先级 | P2（优化字幕阶段核心工作台） |
| 状态 | ✅ 完成（2026-09-18） |
| 关联 | REQ-20260917-030（优化字幕首版：词频 chips + 逐处采纳）；REQ-20260917-032（替换栏右列） |
| 改动范围 | `slirn_home/`（app.py 优化区渲染 + optimize_service.py reviewed 字段 + router.js + CSS）；tasklib/funclip 零改动 |

---

## 0. 背景

优化字幕分析完成后，不明确字词只有一行 chips（word×count），没有整体
处理进度视角：哪些词都过完了、哪些还剩几处没看，全靠记忆。人工逐词
核对的工作需要一个**清单**来驱动。

## 1. 核心需求

| ID | 需求 |
|---|---|
| REQ-1.1 | 不明确字词频次以**列表**展现：每行 = 词 + 频次 + 处理进度 x/y + 处理完成标识 |
| REQ-1.2 | 「处理完成」= 该词全部出现处都已**明确处理**（切换过 ✓/✕ 或编辑过替换值）；标识 ✅/⬜ |
| REQ-1.3 | 列表顶部按钮按标识过滤：全部 / ⬜ 未完成 / ✅ 已完成 |
| REQ-1.4 | 处理进度**实时**刷新（前端切换/编辑即更新词行标识，无需保存） |
| REQ-1.5 | 进度随「确认替换并保存」**落盘**（occ.reviewed），重进任务不丢 |
| REQ-1.6 | 保留原有点词筛选出现行、只看有不明确行等行为 |

## 2. 设计要点

- **口径**：occ 级新增 `reviewed`（默认 False，分析落盘时写入）；词行分组与
  旧 chips 一致按替换目标词（after 落盘值，词身份在分析时固定）；词行 total =
  该词全部出现处（**含未采纳** — 旧 chips 只计生效，进度视角需要全量）。
- **不动的**：`data["words"]/aggregate_words` 形状与统计口径不变（其余消费方零影响），
  词列表由 occurrences 在渲染端现算。
- **前端实时**：occ chip 带 `data-word/data-reviewed`；切换 ✓/✕ 或编辑替换值
  （change 委托）→ `optWordRowRefresh(word)` 重算该词行 x/y + 徽章 + data-done。
- **持久化**：`save_decisions` decisions 增 `reviewed`（全量口径，未列 = 未处理）。

## 3. 验收标准

- [x] AC-1 分析完成后词频以列表展现（词/频次/进度/标识），排序 次数降序、词升序
- [x] AC-2 处理完某词全部出现处 → 该词行实时变 ✅ 已完成（无需保存）
- [x] AC-3 过滤按钮三种模式只显示对应词行
- [x] AC-4 保存后重进任务：处理进度与标识保持（reviewed 落盘）
- [x] AC-5 重新优化 → 进度重置（新分析的 reviewed 全 False）
- [x] AC-6 旧数据（无 reviewed 字段）零回归 — 视为未处理
- [x] AC-7 ruff 0 错；全量 pytest 通过；真实浏览器 E2E：列表渲染 + 实时翻标识 + 过滤 + 落盘

## 4. 验证记录（2026-09-18）

| 项 | 结果 |
|---|---|
| `node --check router.js` | ✅ JS OK |
| `ruff check slirn_home/ tests/` | ✅ All checks passed |
| 全量 pytest | ✅ 258 passed（含新增 2 用例） |
| 单元测试 | ✅ `test_save_decisions_persists_reviewed`（reviewed 落盘往返，未列 = False）<br>✅ `test_render_zone_word_list_done_flags`（词行 data-done/进度/标识/过滤按钮/occ data-word+data-reviewed） |
| 真实浏览器 E2E（CDP） | ✅ 10/10 通过 |

E2E 细节（`work/REQ-20260917-037/_e2e_037_038.py`，截图 `_shot_038_words/_shot_038_alldone/_shot_038_reload.png`）：

- 种子任务 4 处 occ / 3 词：神经网络（1/2 已核 1）、AI（1/1 已核）、大模型（0/1）。
- **AC-1** 词行渲染 `神经网络~0~1/2~⬜ 未完成 ‖ AI~1~1/1~✅ 已完成 ‖ 大模型~0~0/1~⬜ 未完成`
  （次数降序、词升序排序正确）。
- **AC-3** 点「⬜ 未完成」→ AI 行隐藏；点「✅ 已完成」→ 只剩 AI 行；「全部」恢复。
- **AC-2 切换路径**：点 occ 2 的 ✓/✕ 切换 → 神经网络行**无刷新**变 `data-done=1`、
  `2/2`、`✅ 已完成`，occ `data-reviewed=1`。
- **AC-2 编辑路径**：occ 3 替换值输入框 dispatch `change` → 大模型行实时 `1/1 ✅`。
- **AC-4 落盘**：点「💾 确认替换并保存」→ 磁盘 `optimize_subtitle.json` 4 处
  `reviewed` 全 True、`saved_at` 已置；重开任务服务端渲染 `神经网络~1~2/2 ‖ AI~1~1/1 ‖
  大模型~1~1/1`、meta 显示「✅ 已确认」、todo 过滤为空。
- **AC-5** 代码审查验证：`start_job` 落盘 occurrences 显式写 `"reviewed": False`
  （重新优化即重置）。
- **AC-6** 真实任务验证：20260916-004（253 处旧 occ，无 reviewed 字段）经
  `/slirn/api/workbench` 渲染 — 253 处全部 `data-reviewed`、244 词行全 ⬜ 0/N，
  旧数据零回归。

### 修订记录

- 2026-09-18：`test_render_zone_word_list_done_flags` 首跑断言 `2/2` 与 fixture 不符
  （AI 组仅 1 处，正确进度 1/1）— 修测试断言而非实现。
