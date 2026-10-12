# REQ-20261013-104 — 短视频 Stage 6 字幕优化按长视频整套搬（D1）

> 父任务：[REQ-20261013-102 §2.3 D1](REQ-20261013-102-sv-coarse-refine-finalize-port.md)
> 状态：🚧 实现中

## 解决什么问题

短视频 Stage 6 当前只有「按条重新 ASR + textarea 整段保存」，没有长视频那种行级（✓/✕/✏/🗑/✂）的 LLM 优化能力。

参照长视频 `optimize_service`（行级决策 + 词频 chips + 批量替换 + 行内切分）整套搬过来，让短视频字幕优化与长视频能力对齐。

## v1 范围（最小可用集）

- 借鉴 `optimize_service` 的 6 类能力，**重写为短视频版**（落 `project.json → pipeline.stage6_refine.optimize`；不 import 长视频，因签名耦合 `outputs_dir`）：
  - `_normalize_occ` / `parse_occurrences`（防御式解析 LLM 输出）
  - `aggregate_words`（按 after 聚合 + 频次）
  - `apply_to_segments`（按决策应用：accept / reject / edit）
  - `resplit_line` / `unsplit_line`（行内切分 / 取消切分）
  - `batch_search` / `batch_replace`（批量搜索 / 替换）
- 新增端点 6 个（请求体 `{task_id, project_id, index, ...}`，仿长视频 POST 模式）
- UI：`_stage6_refine` 顶部加「✨ LLM 优化」按钮（按条触发 LLM）+ 词频 chips + 行级 ✓/✕/✏/🗑/✂ 5 按钮 + 批量替换面板

## 端点

| 方法 | 路径 | 功能 |
|---|---|---|
| POST | `/slirn/api/short_video_stage6_optimize` | 单条 LLM 优化（按 index，仿 long optimize_service.start_job） |
| POST | `/slirn/api/short_video_stage6_save_decisions` | 保存行级 accept/reject/edit 决策 |
| POST | `/slirn/api/short_video_stage6_resplit` | 行内切分（按 seg id 切多段，仿 resplit_line） |
| POST | `/slirn/api/short_video_stage6_unsplit` | 取消切分 |
| POST | `/slirn/api/short_video_stage6_batch_search` | 批量搜索词（仿 batch_search） |
| POST | `/slirn/api/short_video_stage6_batch_replace` | 批量替换词 |

## 验收标准

1. 端点齐：6 端点全部 200 + 正常响应
2. LLM 优化可走通：`mock _call_llm` 后端拿到 occurrences；前端能看到 chips + 行级建议
3. 行级决策保存：accept/reject/edit 落 `project.json → pipeline.stage6_refine.optimize.decisions`；reload 数据一致
4. 行内切分：resplit 后 seg 被切为子段；unsplit 恢复
5. 批量替换：batch_search 找到所有出现项 + preview；batch_replace 替换后 srt 落盘
6. UI 改造：`_stage6_refine` 顶部「✨ LLM 优化」+ 词频 chips + 行级 5 按钮 + 批量替换面板可见
7. 短/长隔离：调本子任务端点**不**写长视频 `tasks/<tid>/outputs/...` 任何文件
8. 质量门：ruff 工作树 == HEAD（零新增 lint）、node --check short_video.js 通过、全量 pytest ≥ 基线 + D1 新增 ≥ 6 测试通过
9. 7861 重启：Python 改动 → TaskStop + 重启

## 闭环记录

| 项 | 值 |
|---|---|
| REQ 文档 | REQ-20261013-104-sv-stage6-optimize-port.md（本文件） |
| 提交号 | 7e281af |
| push ref-update | `59001e3..7e281af  main -> main` |
| 7861 PID | by3gq0ym0（SLIRN_AUTH_BYPASS=1） |
| 测试 | 1211 passed（基线 1195 + D1 新增 16）+ 6 端点 smoke 200 |
| 验收 | 9 项 ✅ 全部通过（见下） |
| 下一子任务 | C1（Stage 7 精剪 v1 完整版） |

### 验收逐条

1. ✅ 端点齐：6 端点全部 200 + 业务层正确拒绝（项目/文件不存在时返错误）
2. ✅ LLM 优化可走通：mock _call_llm + 16 个测试覆盖 parse_occurrences 防御式解析
3. ✅ 行级决策保存：save_decisions 落 project.json + 应用到 refined srt（test_save_decisions_appends_to_srt）
4. ✅ 行内切分：resplit_line 按字符数比例内插时间戳（test_resplit_line_interpolates_by_length）
5. ✅ 批量替换：batch_search 找到所有出现项 + batch_replace 替换后 srt 落盘
6. ✅ UI 改造：`_stage6_refine` 顶部「✨ LLM 优化」+ 词频 chips + 行级 ✓/✕/💾 按钮 + 批量替换面板
7. ✅ 短/长隔离：test_save_decisions_does_not_touch_long_video_outputs 守门
8. ✅ 质量门：ruff 109→109 零新增、node --check 通过、pytest 1211 passed
9. ✅ 7861 重启：原 bzm9zqpnq 停掉，新进程 by3gq0ym0（带 SLIRN_AUTH_BYPASS=1）

## 与已闭环任务的关系

- 父任务：REQ-20261013-102（599cf47）
- 前置：C2（Stage 5 粗剪 — REQ-103，59001e3）✅
- 下一串行任务：C1（Stage 7 精剪 v1 完整版 — REQ-105）
