# REQ-20261015-119 — Stage 3 解析结果时长不达标的提示词根治

> 状态：✅ 已完成（commit `8b435cd`，2026-10-15）
> 关联：[REQ-20261015-120](REQ-20261015-120-sv-stage3-duration-range-setting.md)（本需求的区间可配置化扩展）

## 1. 需求

> 「在第三阶段，点击解析之后，返回的短视频长度多数都没有达到 60~90 秒的要求，
> 看一下如何再优化一下提示词，能达到时长的要求」

## 2. 根因（真实项目测量，非猜测）

对 `20260924-001 / sv_20260930_234159_86a2fa`（2593 行字幕）实测：

| 维度 | 值 |
|---|---|
| 每行实际时长均值 | **2.07s** |
| 旧版 3 条 hls 实际 Σ 时长 | 41.2 / 32.5 / 41.4 s（全部 < 60） |
| 字符数公式估算 (chars+1)/4 | 57-64 s（**高估约一倍**） |

结论：旧提示词给 LLM 的时长信息全部是**不可执行的**——

1. 「约 70 秒」是形容词，LLM 没有任何手段知道已选了多少秒；
2. `STAGE3_SYSTEM_PROMPT` 里的字符数公式对快语速课程高估 ~2 倍，
   LLM 以为选够了实际只有一半；
3. 没有「不达标怎么办」的核查环节。

## 3. 方案（三侧合力，缺一不可）

### 3.1 输入侧：给 LLM 可求和的硬数据

- `_annotate_srt_seconds(srt_text)`：发给 LLM 的每行字幕文本末尾追加
  真实行时长标注「（2.1s）」——全角/半角括号都识别（`_DUR_TAG_RE`）。
- `strip_duration_tag(text)`：解析 LLM 输出时剥掉标注（防抄进结果）。

### 3.2 提示词侧：计算口径可执行

system（`_UPSTREAM_CLIP_SYSTEM_PROMPT`）+ 输出约束
（`_GROUPS_OUTPUT_CONSTRAINT`）+ 引用系统提示词（`svc.STAGE3_SYSTEM_PROMPT`）
统一换成：

- **「每条总时长 = 组内全部选中行末尾（X.Xs）标注之和，必须落在 60-90 秒
  （含端点）」**——硬区间替换「约 70 秒」；
- 明示「不要用字符数估算」「不要把行末秒数标注抄进输出」；
- 行数量级指引 = 区间半值（60/90 → 30-45 行，每行约 2s）。

### 3.3 执行侧：核查 + 一轮反馈重试

- 解析后每组算 `hl_lines_duration_sec`（Σ 行 end-start）；越界组生成
  `duration_feedback`（「低于 60s 下限，还差 X.Xs：约需再选 N 行」）。
- 不达标 → 自动带反馈重试一轮（retry payload = 原载荷 + 反馈段）；
  重试原文追加落 `llm_raw.txt`（`===== 时长反馈重试 =====` 分隔），
  便于排查模型行为；成功后**过滤丢弃轮的时长告警**（只留真终态）。
- 重试后仍不达标 → 如实终态警告（「可在 Stage 4 编辑或单条重新解析」）。
- `state.duration_retry` / `hl_duration_sec` 落盘（doc+state 镜像）。

### 3.4 E2E 暴露的隐藏炸弹：max_tokens 截断

硬约束让输出从 ~20 行/组涨到 30-45 行/组 × 4 组，**首次 E2E 直接
`stop_reason=max_tokens` 截断失败**——Anthropic 协议 body 里写死的
`max_tokens: 4096` 顶爆了。

修复：`revision_service._chat_completion` / `_call_llm` 增加 `max_tokens`
透传参数（默认仍 4096，存量调用零影响）；Stage 3 收口点
`_call_highlights_llm` 提额到 **8192**（4 组约 5-6K tokens，留余量）。

## 4. 测试

- `tests/test_short_video_stage3_llm_clip.py` REQ-119 段：标注/剥标注、
  提示词硬口径 + 字符公式移除、反馈/告警文案、重试修复短组（10s→64s、
  恰好 2 次调用、重试 payload 含反馈段、丢弃轮告警被过滤）、达标不重试、
  rerun 同口径。
- `tests/test_short_video_stage3_rework.py`：短 SRT 必触发一轮重试
  （vision=1, llm=2）。
- `tests/test_revision.py`：`test_anthropic_max_tokens_passthrough`
  （8192 透传 / 默认 4096）。
- 全量 1283 passed。

## 5. E2E（真实项目 + 真实 LLM）

`POST /slirn/api/short_video_stage3_run`（n=4，区间 60-90）：

```
hl_duration_sec: {'1': 85.4, '2': 76.9, '3': 72.5, '4': 78.6}   ← 全部 ∈ [60,90]
duration_retry: True        ← 第一轮不达标，反馈重试真实生效并修复
lines: 33 / 39 / 41 / 35
llm_raw.txt 含「===== 时长反馈重试 =====」标记
doc 与 state 的 duration_range / hl_duration_sec 镜像一致
```

对比改造前：41.2 / 32.5 / 41.4 s 全部不达标。

## 6. 遗留说明

- 重试只做一轮（第二轮仍不达标则如实告警）——避免无界循环烧 token；
  用户可改区间重跑或 Stage 4 手工增删行。
- `duration_retry=True` 时总耗时约 ×2（两次多模态调用）。
