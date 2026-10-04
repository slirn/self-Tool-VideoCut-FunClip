# REQ-20261003-098 - 单源 AI 短视频内容拆条工作台

- **日期**：2026-10-03
- **类型**：新功能（替换 REQ-20260930-094 多素材混剪）
- **状态**：需求澄清
- **替代关系**：本 REQ **废弃** REQ-094（多素材混剪）；保留 REQ-095/096/097 素材库作为底层存储复用
- **风险初判**：高。涉及 6 阶段状态机、外部 LLM 多次调用、ASR 复跑、跨 Skill 复用与隔离、UI 重构
- **2026-10-04 冒烟**：`work/_smoke_sv_pipeline.py` 跑完 Stage 1→6 全部 done，3 个 final mp4 带 h264+aac；1081 tests pass

## 1. 背景

现有 `slirn_home/short_video_*` 是"多素材混剪"模式（挑 B-roll + 转场 + BGM 组装 9:16）。本 REQ 替换该路径为"单源 AI 拆条"模式：从一段完整视频（长视频任务产物 / 上传）出发，由 AI 通过字幕分析+重组+重排序，产生 3-5 个独立的 1 分钟级短视频。

素材库（上传/预览/删除/卡片样式）保持不变；底层的 `project / materials / render_jobs / execution_history` 数据结构扩展以支持 6 阶段流水线，旧的多素材混剪项目不再创建。

## 2. 目标

用户上传 / 选择 1 个完整视频，经过 6 个可中断、可回退的阶段，产出 3-5 个独立的 1 分钟左右短视频。

```
源视频 → [1 选源] → [2 抽字幕] → [3 AI 拆条] → [4 粗剪] → [5 字幕优化] → [6 精简混编]
                ↓           ↓             ↓              ↓             ↓                ↓
            视频ID       raw.srt     highlights[]   highlights[]    refined.srt    final mp4[]
```

每个阶段：
- 独立保存状态，可单独重跑（不影响后续阶段）
- 产物落盘到 `project.dir/<stage>/`，可在 UI 中下载预览
- 第 2 / 第 5 阶段复用现有 Skill；第 3 阶段新增；第 4 / 第 6 阶段复用部分成熟调用

## 3. 首个可验收切片

给定一个新建的拆条项目 `P`：

1. 用户在 UI 中上传或选 1 个视频，落地为项目 `P` 的源视频（`source_material`）。
3. 点击「提取字幕」→ 调用 funasr，生成 `P/raw.srt`，UI 可预览前 30 行。
4. 点击「AI 拆条」→ 调用 LLM，按所选模板生成 3-5 条 highlights，每条 ~1 分钟，UI 列表展示每条的起止时间 / 字幕预览 / 摘要。
5. 点击「粗剪」→ 按 highlights 起止时间逐条剪辑，落地 N 个 `coarse_NN.mp4`，UI 列表展示。
6. 点击「优化字幕」→ 对每个粗剪 mp4 重新 ASR，生成 `refined_NN.srt`，UI 提供文本框让人工修改后保存。
7. 点击「精简混编」→ 对每个粗剪 mp4 + 优化后的 srt 调用 `video-subtitle-editing-pipeline` 步骤 3，落地 N 个 `final_NN.mp4` 与连续时间轴 srt。

## 4. 边界

### 本期包含

- 6 阶段状态机：每阶段独立状态、独立产物、可重跑
- 项目持久化：每个项目保存源视频、各阶段产物、LLM 输出、用户手动修正
- 第 3 阶段 3 套 prompt 模板 + 用户自带 prompt 覆盖
- 第 5 阶段 ASR 复跑 + 人工字幕修正 UI（文本编辑）
- 复用 `REQ-095/096/097` 素材库（保留存储层，UI 改用于源视频选择）
- 复用 `video-subtitle-extractor` Skill（第 2 阶段）
- 复用 `video-timestamp-cutter` Skill 思路（第 4 阶段实现）
- 复用 `video-subtitle-editing-pipeline` 步骤 3 `cut_by_srt.py`（第 6 阶段），通过子进程隔离调用，不污染长视频状态

### 本期不包含

- 抖音 / 视频号自动发布
- 跨项目 / 跨用户公共素材库（沿用 REQ-094 边界）
- 高级剪辑：转场、特效、人脸追踪、节拍卡点
- 自动生成的标题 / Hook / CTA 文案（用户后续可在导出时手动加）
- 多源视频融合（输入始终是 1 个完整视频；多源拼接仍属废弃的多素材混剪思路，本期不支持）

## 5. 已确认决策

| 编号 | 决策 |
| --- | --- |
| D1 | 废弃 REQ-094 多素材混剪；新工作台仅支持单源 AI 拆条 |
| D2 | 第 4 阶段粗剪产物：每个 highlight 一个独立 mp4（共 N 个），不拼接成总集 |
| D3 | 第 3 阶段 prompt：提供 3 套模板（Hook 优先 / 主题聚类 / 起承转合），跑通后用户挑 1 套作为默认 |
| D4 | 第 3 阶段允许用户在 UI 输入自定义 prompt 覆盖默认模板 |
| D5 | 第 5 阶段字幕修正走纯文本编辑 UI；不引入时间轴拖拽 |
| D6 | 第 6 阶段走子进程调用 `cut_by_srt.py`，产物落到 `project.dir/stage6/`，与长视频的产物目录完全隔离 |
| D7 | 第 3 阶段 N = 3-5，由用户在第 3 阶段 UI 选（默认 4） |
| D8 | 每个 highlight 时长目标 60 秒；第 3 阶段 prompt 要求 ±20 秒容差 |
| D9 | 第 3 阶段允许用户为每条 highlight 自定义标题（覆盖 LLM 默认） |

## 6. 阶段详细规格

### 阶段 1：选源视频
- 输入：项目新建（无源视频）
- 操作：复用 REQ-095 上传 UI / 复用 REQ-096 预览弹窗；从任务产物 / 上传文件二选一
- 产物：`P/source.mp4`、`P/source_material.json`
- 状态：`source_video: pending → extracted → done`
- AC：选择后 5 秒内 UI 显示视频时长 / 分辨率

### 阶段 2：提取字幕
- 输入：`P/source.mp4`
- 操作：调用 `slirn/skill/video-subtitle-extractor/scripts/extract_subtitle.py` 子进程；funasr 模型沿用上游默认（paraformer-zh / SenseVoiceSmall）
- 产物：`P/raw.srt`、`P/raw.json`（含 timestamp）
- 状态：`extract_subtitle: pending → running → done / failed`
- AC：2 分钟视频 ≤ 30 秒完成；UI 可点开预览

### 阶段 3：AI 拆条
- 输入：`P/raw.srt`
- 操作：调用 LLM（沿用上游 `openai_call` / `call_qwen_model` / `g4f_openai_call`），按所选模板生成 3-5 条 highlights；每条含起止毫秒、字幕行重组序列、自定义标题
- 关键要求：**字幕分段重组**（不是直接复制连续片段）+ **重新排序**（不按源时间顺序）
- 产物：`P/highlights.json`（结构见 §8 schema）、`P/llm_raw.txt`
- 状态：`content_analysis: pending → running → done / failed`
- AC：用户选模板后 60 秒内出结果；UI 列表可重排、编辑标题、删除单条

### 阶段 4：粗剪合成
- 输入：`P/highlights.json`、`P/source.mp4`
- 操作：对每条 highlight 调用 `moviepy.editor.VideoFileClip.subclip(start, end)` 导出 mp4
- 产物：`P/stage4/coarse_NN.mp4`、`P/stage4/coarse_NN.srt`（LLM 给出的该条字幕）
- 状态：`coarse_cut: pending → running → done_per_clip / failed`
- AC：每条 mp4 ≤ 10 秒；音频流为 aac；UI 可逐条播放

### 阶段 5：优化字幕
- 输入：`P/stage4/coarse_NN.mp4`（每条独立处理）
- 操作：对每条粗剪 mp4 重新 ASR（funasr，**模型与第 2 阶段可不同**），覆盖 LLM 给出的字幕；UI 提供文本框让人工修改后保存
- 产物：`P/stage5/refined_NN.srt`（人工修正版，覆盖 ASR 默认）
- 状态：`subtitle_refine: pending → running_asr → user_editing → confirmed`
- AC：用户逐条进入、修改完成；空提交等于使用 ASR 原始结果

### 阶段 6：精简混编
- 输入：`P/stage4/coarse_NN.mp4` + `P/stage5/refined_NN.srt`（逐条）
- 操作：对每条调用子进程 `cut_by_srt.py`（来自 `video-subtitle-editing-pipeline` 步骤 3）→ 输出 `final_NN.mp4` + `final_NN.continuous.srt`
- 产物：`P/stage6/final_NN.mp4`、`P/stage6/final_NN.continuous.srt`
- 状态：`finalize: pending → running → done / failed`
- AC：每个 final mp4 ≤ 90 秒；字幕与视频时长对齐；不向长视频项目目录写任何文件

## 7. Prompt 模板选项（待挑）

第 3 阶段 LLM 输出 JSON 格式固定为：

```json
{
  "highlights": [
    {
      "id": "h1",
      "title": "钩子 + 5s 转折",
      "start_ms": 0,         // 在源视频中的源起止毫秒
      "end_ms": 60000,
      "subtitle_lines": [    // 字幕重组：每行从 raw.srt 引用
        {"src_index": 12, "text": "原句 1（可微调）"},
        {"src_index": 5,  "text": "原句 2（重组）"}
      ]
    },
    ...
  ]
}
```

提供 3 套模板，**用户挑 1 套为默认**：

### 模板 A：Hook-First（钩子前置）
> 用视频中"最抓人"的一句话作为第 1 条的开头（甚至可以把视频开头非精彩部分剪掉）；剩下 2-4 条按"情绪强度"递减或递增排序；适合推广 / 拉新类短视频

**样例输出**（4 条）：
```
[1] "你不知道的 X"  → 00:01:20 - 00:02:25  钩子段
[2] "X 是怎么工作的" → 00:08:40 - 00:09:45  解释段
[3] "常见误解"        → 00:23:10 - 00:24:20  痛点段
[4] "现在就试"        → 00:45:00 - 00:46:05  CTA 段
```

### 模板 B：Topic-Cluster（主题聚类）
> 把字幕按主题聚成 3-5 个簇，每簇选最具代表性、最连贯的句段；每条都是相对独立的"知识小卡"；适合教程 / 知识类短视频

**样例输出**（4 条）：
```
[1] "X 的三大特点"      → 簇 1（句子从 00:01, 00:15, 00:23 重组）
[2] "为什么 X 会失败"   → 簇 2（句子从 00:35, 00:42, 00:48 重组）
[3] "X vs Y 的对比"     → 簇 3
[4] "上手 X 的第一步"   → 簇 4
```

### 模板 C：Story-Arc（起承转合）
> 每条都是完整的"起承转合"小故事（4 个小段落）；从视频中挑 4 个具备完整叙事弧的段；适合故事 / 人物访谈类短视频

**样例输出**（4 条）：
```
[1] "困境 - 觉醒 - 行动" → 00:01 - 00:03 段
[2] "失败 - 反思 - 转机" → 00:18 - 00:21 段
[3] "尝试 - 受挫 - 突破" → 00:32 - 00:35 段
[4] "成果 - 感恩 - 展望" → 00:48 - 00:52 段
```

**默认行为**：UI 提供 3 个单选按钮 + 1 个"自定义 prompt" textarea。用户选模板后，预览 LLM 输出，可手动改每条的 title / 重排顺序 / 删除任意条。

## 8. 数据模型（highlights.json schema）

```json
{
  "project_id": "P-...",
  "source_srt": "raw.srt",
  "model": "deepseek-chat",
  "template": "A|B|C|custom",
  "custom_prompt": "（用户覆盖时填写）",
  "highlights": [
    {
      "id": "h1",
      "title": "用户可编辑的标题",
      "start_ms": 0,
      "end_ms": 60000,
      "subtitle_lines": [
        {"src_index": 12, "text": "..."},
        {"src_index": 5,  "text": "..."}
      ]
    }
  ],
  "created_at": "2026-10-03T..."
}
```

## 9. 验收标准（首版可量化）

| 编号 | 验收标准 | 验证方式 |
| --- | --- | --- |
| AC-1 | UI 6 阶段状态机可独立查看与重跑 | 浏览器 E2E + 状态查询 API |
| AC-2 | 第 3 阶段对 2 分钟 zh 视频 LLM 响应 ≤ 60 秒 | API 测试 |
| AC-3 | 3 套 prompt 模板各跑通 1 个样本，可挑默认 | 人工对比 + 用户确认 |
| AC-4 | 第 4 阶段每条粗剪 mp4 有 aac 音频，时长与 LLM 起止 ±1 秒 | ffprobe + pytest |
| AC-5 | 第 5 阶段人工修正后保存的 srt 可被第 6 阶段消费 | 端到端冒烟 |
| AC-6 | 第 6 阶段产物全部在 `P/stage6/` 下，长视频项目目录无任何写入 | 文件系统检查 |
| AC-7 | 长视频 6 阶段流水线（`video-subtitle-editing-pipeline`）行为不回归 | 全量 pytest |
| AC-8 | 旧 REQ-094 多素材混剪入口不可见；旧项目仍可查看但不渲染 | 手动 + UI 截图 |

## 10. 风险

- LLM 输出的 `subtitle_lines` 可能引用不存在的 `src_index`；需校验 + 兜底
- 第 5 阶段重新 ASR 在短片段上耗时 / 准确度不稳定；提供"跳过重跑、使用第 3 阶段 LLM 字幕"选项
- `cut_by_srt.py` 走子进程需捕获 stderr；失败时给出原始错误
- 6 阶段状态机引入更多错误码，需要靠滚动日志而非单点错误
- LLM 重组 + 重排序对 prompt 极敏感；3 套模板效果差异可能很大，需要用户对比

## 11. 后续动作

1. 用户评审本 REQ；如确认 → 进 DESIGN-098（确定数据迁移、UI 状态机、prompt 模板最终选型、状态机错误码表）
2. DESIGN-098 通过后 → 实现
3. 实现分阶段提交，每阶段对应一个 PR
4. 实现完跑通 AC 表