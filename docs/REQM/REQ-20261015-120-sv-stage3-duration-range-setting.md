# REQ-20261015-120 — Stage 3 蓝色框区新增「每条时长区间」设置

> 状态：✅ 已完成（commit `8b435cd`，2026-10-15）
> 关联：[REQ-20261015-119](REQ-20261015-119-sv-stage3-duration-hard-constraint.md)（硬约束本体；本需求把
> 写死的 60-90 参数化为用户可配置）

## 1. 需求

> 「在蓝色框区域添加每个片段的时长区间设置信息，这样可以保证用户根据需要
> 调整时长，并且在程序中的后台不用再计算，而是直接取这值即可，可以把这块
> 的设置后拼到提示词中，并在输出时要求判断」

三个要点：① UI 可设区间 ② 后端直接取值（不自行推算）③ 拼进提示词 +
输出按此核查。

## 2. 实现

### 2.1 UI（`short_video_ui.py::_stage3_analyze` 蓝色框设置区）

form-grid 从 1 列扩 3 列：

```
片段数 [4]   时长下限(秒) [60]   时长上限(秒) [90]
```

- `#slirn-stage3-dmin` / `#slirn-stage3-dmax`（number，step=5，
  min/max 属性与后端钳制一致）；
- 初值 = `s3.duration_range`（上次运行的生效区间）回填，缺省 60/90；
- hl meta 行新增越界标记：`字幕合计 58s ⚠️ 未达 60s` /
  `字幕合计 95s ⚠️ 超 90s`（区间口径 = 上次运行）。

### 2.2 JS（`short_video.js`）

- `_stage3DurationRange()`：读两输入 → parseFloat → NaN/缺元素返
  `[null, null]` → 倒挂互换（完整钳制由后端兜底）；
- `stage3Run` / `stage3RerunOne` POST body 增加
  `duration_min_sec: dur[0], duration_max_sec: dur[1]`。

### 2.3 后端（端点 → 归一化 → 全链路同区间）

- `app.py` 两个端点（run / rerun_one）`body.get("duration_min_sec")`
  透传；
- `_norm_duration_range()`：非数/NaN/非正值 → 默认 60-90；下限钳
  [10,600]；上限钳 [下限+5, 900]（倒挂时上限抬到下限+5）；
- 提示词占位符 `{dmin}`/`{dmax}`/`{dmin2}`/`{dmax2}`（`_duration_prompt_ctx`）
  —— system / 输出约束 / rerun 定向指令 / 反馈重试全部同区间渲染；
- 解析阈值（未达/超过判定）、`duration_feedback`、终态告警同区间；
- `rerun_stage3_one` 缺省入参 → 回退 doc 里上次整跑的 `duration_range`；
- `duration_range`（生效区间）+ `hl_duration_sec` 落盘 doc+state。

## 3. 测试（`tests/test_short_video_stage3_llm_clip.py` REQ-120 段）

- `_norm_duration_range`：默认/0 视为未填/透传/离谱钳制/倒挂/NaN；
- 自定义区间 40-80 端到端流：提示词出现「40–80 秒」「20–40 行」、
  不残留 60-90、state/doc `duration_range=[40,80]`、50s 组零重试；
- 同一 10s 结果：区间 [5,15] 一次过 vs 默认 60-90 触发重试（阈值跟
  区间走）；
- rerun 缺省回退 doc 区间（40-80 进 payload）；
- UI 渲染：输入框默认值/回填/⚠ 标记三态；
- 接线守卫：端点 `body.get` ×2、JS helper + POST 字段 ×2。

## 4. E2E

真实项目整跑（区间 60-90）验证参数链路 + UI 渲染：输入框 value=60/90
（回填自 state）、4 条 meta 无 ⚠（全部落区间内）。自定义区间路径由
单测覆盖（真实 LLM 只跑了默认区间一次，避免烧 token）。

## 5. 设计取舍

- **倒挂（min>max）**：JS 互换、后端抬上限到下限+5，双保险但结果一致
  （互换后 [小,大] 与抬升后 [max, max+5] 不同——以 JS 互换为先，
  后端仅兜底直连 API 的调用者）；
- **0/空/非数 → 默认区间**而非报错：设置区是可选增强，不设即旧行为；
- **上限 ≥ 下限+5**：保证区间有意义（5 秒宽的区间没有可操作性）。
