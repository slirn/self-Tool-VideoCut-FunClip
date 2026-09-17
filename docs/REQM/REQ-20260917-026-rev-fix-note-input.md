# REQ-20260917-026 — 内容更正行输入框 + 切分修剪自动取用更正值

## 需求原话

> 当决策状态改为内容更正时，也要把切分修改内容输入框显示出来，用于填写
> 修正的内容，并在下一阶段切分修剪时，自动把内容修正的值从输入框中取出
> 来，或者是自动识别修正后的内容提出来

## 设计

此前 fix（内容更正）行的生效文本只取模型建议 `keep_text`，行内输入框
语义也只服务切分（「切分修剪后内容」且仅 split 行预填）。本次把 fix 行
纳入同一条「输入框 = 生效内容」链路：

### 1. 修订面板（app.py + revision_service.py）

- **预填**：fix 行输入框预填模型更正文本（与 split 同口径：手填
  `user_note` 优先 → `keep_text` 作起点）。渲染层兜底存量任务（未重分析
  也能看到/微调）；分析落盘层（revision_service）新任务直接写 `user_note`
- **语义区分**：fix 行 placeholder=「更正后内容（可微调）」，split 行仍为
  「切分修剪后内容（可空）」
- **决策切换即备好**（JS change 处理器）：决策下拉改为 `fix`/`split` →
  展开详情块、placeholder 跟随决策、输入框为空时从详情块模型建议文本
  （✏️ 更正后 / ✂️ 建议保留）预填、光标聚焦行尾——直接可微调；
  keep/delete/pending/accept 不弹（不需要内容）
- 「保存修订决策」原有收集逻辑不动（输入框值即 `user_note` 落盘）

### 2. 切分修剪（cutlist_service.py）

- 新增 `_fix_target(entry)`：`user_note`（输入框微调值）优先 → `keep_text`
  回退，与 `_split_target` 同构
- `build_cutlist` fix 条目文本改走 `_fix_target`（原文兜底保留）——
  **下一阶段自动取输入框值**，无需额外操作；下游（切分清单 → 粗剪合成
  SRT 预览 → 热词替换基底 → 精剪/成片）经 `effective_keep_units` 全链路
  自动继承更正后文本

## 验证

- `pytest tests/ -q` → **204 passed**（新增
  `test_build_cutlist_fix_user_note_priority` 三级优先级用例；更新两处
  「fix 不预填」旧断言为「预填」——本 REQ 即该行为变更）；ruff clean
- 服务重启后线上核验：workbench API 渲染 fix 行新 placeholder ×53 + 预填
  value ✅；根页面 4 个内联脚本 `node --check` 全过 ✅
- 浏览器走查待用户验证：修订面板把某行决策改「内容更正」→ 详情展开、
  输入框预填并聚焦 → 微调 → 保存 → 切分修剪预览该行文本 = 输入框值

## 关联

- 提交：`feat(rev): 内容更正行输入框与切分修剪自动取用 (REQ-20260917-026)`
- 前置：REQ-20260916-006（fix 类别与「✏️ 更正后」详情块）、
  REQ-20260916-010（split 输入框预填链路，本 REQ 对 fix 补齐）
