# REQ-20261016-127 — Stage 3 提示词改用时间戳加总口径，禁用「行数 × 平均 2s」捷径

> 状态：✅ 已完成（2026-10-16）
> 关联：[REQ-20261015-119](REQ-20261015-119-sv-stage3-duration-hard-constraint.md)（60-90s 硬约束来源）
> 关联：[REQ-20261015-124](REQ-20261015-124-sv-stage3-prompt-independence-perclip.md)（系统默认提示词 + 独立逐条时长）

## 1. 需求（用户原话）

> 「关于解析短视频内容时的提示词里边，不要引导 AI 用选的行数跟平均时长进行计算，应该直接把选的行数对应的时间戳的时间段累计计算即可」

## 2. 背景与问题

### 2.1 旧提示词里残留的「行数 × 平均 2s」启发式

REQ-119 落地 60-90s 硬约束时，提示词里同时给了两套口径：

1. **直接口径**（正确）：每行末尾已标注 `(X.Xs)`，总时长 = 选中行标注之和
2. **折算口径**（启发式）：按常见语速（每行约 2s）估算行数量级

旧版 system + user prompt 既有第 1 段（"总时长 = 选中行标注之和"），又同时给了
"按每行约 2s 一般需 30-45 行" 这样的反推。LLM 在边界判定时倾向走捷径（"差不多就
用行数 × 2 秒算"），结果：

- 课程语速方差大（实测 1.0s~4.5s 都有），平均估算法几乎一定偏离 1-3 秒
- 与"行末秒数标注为唯一可信数据"的硬约束相互矛盾（提示词自己打架）
- 用户反馈：希望直接用时间戳加总，不允许走捷径

### 2.2 旧实现残留的 4 个占位符

`_duration_prompt_ctx()` 返回 4 个键：`dmin` / `dmax` / `dmin2` / `dmax2`（其中
`dmin2 = dmin / 2`、`dmax2 = dmax / 2`）。`stage3_system_prompt()` 还多算
`ex_sec`（区间中值）和 `ex_lines`（按 2.2s/行折算）。

## 3. 实现

### 3.1 删除的旧占位符

| 占位符 | 旧值 | 现状 |
|---|---|---|
| `{dmin2}` | `dmin / 2` | 删除（行数估算） |
| `{dmax2}` | `dmax / 2` | 删除（行数估算） |
| `{ex_sec}` | `(dmin + dmax) / 2`（中值秒数） | 删除（与"按行加总"重复） |
| `{ex_lines}` | `ex_sec / 2.2`（折算行数） | 删除（"行数 × 平均"启发式） |

### 3.2 system prompt 新口径

`_STAGE3_SYSTEM_PROMPT_TMPL` 旧例（`{ex_lines} 行、平均 2.2s → 总时长 ≈ {ex_sec} 秒`）
替换为：

> 例：一条短视频选了 3 行，标注分别是「（2.5s）」「（2.2s）」「（2.8s）」→ 总时长 = 2.5 + 2.2 + 2.8 = 7.5 秒，落在 {dmin}-{dmax} 区间 ✓。

并加显式禁止：

> 绝对不要用「行数 × 平均 2 秒」之类的捷径估算——课程语速差异大（每行 1.0s~4.5s 都常见），平均估算几乎一定会偏离 1-3 秒。

`_UPSTREAM_CLIP_SYSTEM_PROMPT`（`short_video_analyze.py:534`）同步改写：
去掉"按常见语速（每行约 2 秒）估算"句子，替换为"直接把这些标注的秒数相加"。

### 3.3 user prompt 新口径

`run_stage3`（L1268）和 `rerun_stage3_one`（L1536）原句：

> 按每行约 2s 一般需 {dmin2:.0f}–{dmax2:.0f} 行

替换为：

> 把这些标注秒数直接相加，不要用「行数 × 平均 2 秒」估算

### 3.4 占位符收口

`_duration_prompt_ctx()` 返回值收窄为 `{"dmin": dmin, "dmax": dmax}`。
`stage3_system_prompt()` 移除 `ex_sec` / `ex_lines` / `dmin2` / `dmax2` 计算。

`_STAGE3_SYSTEM_PROMPT_TMPL` 注释同步更新：明确"REQ-20261016-127 去掉
行数 × 平均 2s 折算类占位符"。

### 3.5 附带的 REQ-126 渲染收口

发现 `short_video_ui.py` 上一轮 REQ-126（commit 6f852c2）漏写了
1 行 f-string 模板渲染（`{srt_preview_html}` 变量已计算但未插入）——
REQ-126 当时靠 3 个单元测试通过（测试只验证 `_render_srt_preview` 返回
字符串，不验证模板插入），但实际页面未生效。

本 commit 顺带把这一行加回去（5 个 hunk 不重叠，逻辑相邻）。
补回后 `_render_srt_preview` 的结果才会出现在 Stage 2 卡片下方。

## 4. 改动清单

| 文件 | 改动 |
|---|---|
| `slirn_home/short_video_service.py` | `_STAGE3_SYSTEM_PROMPT_TMPL` 文案改写 + 注释更新；`stage3_system_prompt()` 移除 4 个占位符计算 |
| `slirn_home/short_video_analyze.py` | `_UPSTREAM_CLIP_SYSTEM_PROMPT` 改写（去"每行约 2 秒"启发式）；`_duration_prompt_ctx()` 收窄为 2 键；`run_stage3` / `rerun_stage3_one` user prompt 改写 |
| `slirn_home/short_video_ui.py` | 修复 REQ-126 漏写 1 行 f-string 渲染（`{srt_preview_html}`） |
| `tests/test_short_video_stage3_llm_clip.py` | `test_upstream_prompt_and_constraint_hard_duration` 改断言；`test_stage3_system_prompt_builder_dynamic_params` 改断言；`test_stage3_system_prompt_template_has_tokens` 改断言；新增 `test_stage3_prompt_no_row_count_avg_shortcut` + `test_stage3_run_prompts_drop_dmin2_dmax2` |

## 5. 测试（+2，全量 1306 passed）

- `test_upstream_prompt_and_constraint_hard_duration` 改 — 旧"30–45 行"行数指引
  改为新口径"直接把这些标注的秒数相加 + 不要用「行数 × 平均 2 秒」"
- `test_stage3_system_prompt_builder_dynamic_params` 改 — 旧"20-40 行" +
  "总时长 ≈ 60 秒" 改为新"2.5 + 2.2 + 2.8 = 7.5 秒"
- `test_stage3_system_prompt_template_has_tokens` 改 — 模板只剩 `{n}` / `{dmin}` /
  `{dmax}` 3 个占位符；显式断言 `{dmin2}` / `{dmax2}` / `{ex_sec}` / `{ex_lines}`
  不在模板里
- `test_stage3_prompt_no_row_count_avg_shortcut` 新 — system prompt 同时含新
  正向例 + 显式禁止旧捷径
- `test_stage3_run_prompts_drop_dmin2_dmax2` 新 — `inspect.getsource(run_stage3)`
  和 `rerun_stage3_one` 函数源码内不再出现 `dmin2` / `dmax2` 占位符

## 6. 验收

- 1306/1306 pytest 全过
- 真实项目（待用户验证）：点 Stage 3「解析」→ 4 个 highlights 总时长全部
  落在 60-90s 区间内（不再有"行数 × 2 秒"的边界偏离）

## 7. 已知边界

- 提示词层面禁用了，但 LLM 仍可能用其他等价短语（如"平均每段约 2 秒"）。
  REQ-124 的逐条时长约束 + 反馈重试（duration_retry）兜底——硬超界会
  触发第二轮重试。
- `_annotate_srt_seconds()` 注尾"X.Xs"标注保留（Stage 2 raw.srt 仍带此标注），
  LLM 用此标注加总即可，不再需要行数估算。
