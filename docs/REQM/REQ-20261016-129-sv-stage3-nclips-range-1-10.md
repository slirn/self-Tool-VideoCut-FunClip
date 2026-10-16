# REQ-20261016-129 — Stage 3 片段数区间从 3-5 放宽到 1-10

> 状态：✅ 已完成（2026-10-16）
> 范围：**仅 Stage 3 解析短视频内容**；不影响 Stage 1/2/4/5/6/7
> 改动：3 处（UI 输入框 + 服务端 clamp + docstring），加 1 个单元测试

## 1. 需求（用户原话）

> 把第三阶段的片段数参数范围改成 1~10

## 2. 现状与改动

### 2.1 改动点（3 处）

| 文件 | 旧值 | 新值 |
|---|---|---|
| [short_video_ui.py:781](short_video_ui.py#L781) | `min="3" max="5"`、`title="...（3-5）"` | `min="1" max="10"`、`title="...（1-10）"` |
| [short_video_analyze.py:1159](short_video_analyze.py#L1159) | docstring `n_clips: ...（3-5）` | `n_clips: ...（1-10，REQ-20261016-129 放宽）` |
| [short_video_analyze.py:1183](short_video_analyze.py#L1183) | `n_clips = max(3, min(5, int(n_clips or 4)))` | `n_clips = max(1, min(10, int(n_clips or 4)))` + 注释 |

### 2.2 边界值语义

- `int(n_clips or 4)`：0/None/空字符串都走默认 4（falsy 短路）
- 1-10 区间内的值原样保留
- > 10 夹到 10
- < 1 夹到 1
- 与 3-5 旧版相比：**下限从 3 降到 1**、**上限从 5 升到 10**

### 2.3 不影响的同类函数（已确认无副作用）

| 函数 | 行为 | 是否需要改 |
|---|---|---|
| `_group_srt_into_clips` L927 `cap = max(1, int(n_groups or 4))` | floor only，无上限 | 不改（用户没要求） |
| `build_stage3_prompt` L1065 `n=max(1, int(n_clips or 4))` | floor only，无上限 | 不改（同一处约束靠 run_stage3 的 clamp 兜底） |

## 3. 测试

### 3.1 新增 [tests/test_short_video_stage3_llm_clip.py::test_stage3_n_clips_range_1_to_10](../tests/test_short_video_stage3_llm_clip.py)

- (1) UI 源码含 `min="1" max="10"` 和 `（1-10）`
- (2) `run_stage3` 源码含 `max(1, min(10,` 且**不含** `max(3, min(5,`
- (3) 抽出 clamp 表达式单验：1/5/10 原样、11→10、None/0 走默认 4

### 3.2 验证结果

```
================ 1316 passed, 12 warnings in 89.93s (0:01:29) ================
```

## 4. 验收

- [x] 1316/1316 pytest 全过（1315 旧 + 1 新）
- [ ] 7861 重启加载新 Python（仅 `short_video_ui.py` 和 `short_video_analyze.py` 改动，endpoints 没变；按 [slirn-dynamic-script-load-race](slirn-dynamic-script-load-race.md) 不需重启 JS；**Python 必须重启**）
- [ ] 真实项目 UI 验收：Stage 3「片段数」输入框可输入 1 / 10，HTML5 校验生效；填 12 提交后实际生效是 10
- [ ] 真实解析：1 条短切片、10 条短切片分别走通

## 5. 不做的事

- ❌ 不改 Stage 4/5/6/7 的同类参数
- ❌ 不改 `_group_srt_into_clips` 的 `cap`（floor only）
- ❌ 不动默认 4（保留向后兼容）
- ❌ 不加 CSS / 不加新端点

## 6. 关联

- [[req-099-stage3-content-parse]] — Stage 3 整体设计
- [[req-127-sv-stage3-prompt-timespan-summation]] — 上一轮 Stage 3 改版
- [[git-partial-staging-mixed-reqs]] — 本次 hunk-split 拆 REQ-129 vs REQ-116/117
