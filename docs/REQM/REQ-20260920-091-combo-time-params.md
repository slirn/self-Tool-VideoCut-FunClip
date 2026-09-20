# REQ-20260920-091 合成元素组合测试面板 — 加时间参数（开始时间 + 时长）

## 背景

REQ-20260920-090 实现了 5-checkbox 调试面板，让用户逐步验证 BGM 在不同元素组合下是否仍存在。但有个**致命的可用性问题**：

合成是**跑完整视频**（1-3 小时）。每次「🚀 一键测试合成」要等几分钟到几小时才能拿到结果，**没法快速迭代验证 BGM 路径**。

用户实测发现：
- 测「视频 + 字幕 + BGM」：要等 5-15 分钟（完整视频 + 字幕 + BGM 混音）
- 测 5 种组合：1-3 小时 × 5 = **半天**
- 想验 BGM 是否存在——根本不必跑完整视频，30 秒片段就够听

`_assemble_fine_filter` 接受两个时间参数（REQ-20260919-064/066）：
- `preview_start: float = 0.0` —— ffmpeg `-ss` 跳到几秒
- `duration: float | None` —— ffmpeg `-t` 限制输出时长

但当前 `export_fine_video` endpoint 内部 hardcode 了 `duration=None, preview_start=0.0`（[app.py:2484](slirn_home/app.py#L2484)），**完全导完整视频**。

测试面板缺这两个参数。

## 用户影响

| 场景 | 表现 | 用户感受 |
|---|---|---|
| 想测「视频 + 字幕 + BGM」 | 等 10 分钟 | 😩 太慢 |
| 想测 5 种组合 | 等 1-3 小时 | 😡 半天没了 |
| 想验 BGM 是否存在 | 30 秒片段足够 | ❌ 跑完整视频浪费 |
| 想测 BGM 在「视频 60 秒处」是否仍存在 | 完全没法测 | ❌ |

## 用户明确要求（直接引用）

> 这个合成元素组成测试没有时间上的参数吗？你前面都查到了两个参数，开始时间和时长，加这两个就可以

## 用户决策（已确认）

| 维度 | 决策 |
|---|---|
| 参数 1 | **预览开始时间**（时:分:秒，三段输入复用 REQ-066 模式） |
| 参数 2 | **预览时长**（秒，2-30 范围复用 REQ-064 模式） |
| 默认值 | start = 00:00:00，duration = 10 秒（短片段快速测试） |
| 范围 | start ≥ 0；duration ∈ [2, 30]（与现有预览面板一致） |
| 后端 | `export_fine_video` 接受 body.preview_start + body.duration，透传给 `_run_fine_render_async` |
| 输出文件名 | 加 `_t{start}_d{duration}.mp4` 后缀防覆盖完整视频 |

## 验收标准

| AC | 描述 |
|---|---|
| AC-1 | 工作台「🧪 合成元素组合测试」面板新增 2 个 input：「⏱ 开始时间（时:分:秒）」+「⏳ 时长（秒，2–30）」 |
| AC-2 | 默认值：start=00:00:00，duration=10 秒 |
| AC-3 | 复用 REQ-064/066 的 HTML input class（`slirn-fine-num` + `slirn-fine-preview-time`） |
| AC-4 | `/slirn/api/export_fine_video` 接受 body.preview_start（float 秒）+ body.duration（float 秒） |
| AC-5 | 缺省时回退到 preview_start=0.0 / duration=None（导出完整视频，保持向后兼容） |
| AC-6 | `_run_fine_render_async` 接受 `preview_start` + `duration` 参数，传入 `_assemble_fine_filter` |
| AC-7 | 输出文件名：默认 `outputs/fine_export.mp4`；有时间参数时改为 `outputs/fine_export_t{start}_d{duration}.mp4` |
| AC-8 | combo-test 在 fetch /export_fine_video 时把 time 参数放进 body |
| AC-9 | combo-test 完成后的 probe_output_audio 自动探测**对应**输出文件（不是默认 final.mp4） |
| AC-10 | 4 个新测试：HTML 含 2 个 time input / export_fine_video 接受 time 参数 / output_path 时间后缀 / combo-test 传 time 参数 |

## 范围外

- 不改 `_assemble_fine_filter` 主逻辑（它已接受这 2 个参数）
- 不改 REQ-089 已修的 Popen / finally record_finish
- 不动现有「💾 导出最终视频」按钮（它仍导出完整视频）
- 不持久化 time 参数到 fc（每次用 UI 输入）

## 风险

| 风险 | 处理 |
|---|---|
| 用户输入 start > 视频时长 | 后端钳到 [0, video_duration]（复用现有 `_clamp_video_to_viewport` 模式） |
| 用户输入 duration > 视频剩余时长 | 后端钳到剩余时长（自动截断） |
| 用户输入 start/duration 为负数 | 后端钳到 ≥ 0 |
| combo-test 输出文件覆盖之前的完整视频 | 文件名加 `_t{d}_d{n}.mp4` 后缀（不覆盖） |
| probe_output_audio 找不到新文件 | combo-test 传 `output_path` 给 probe_output_audio |

## 关联

- [REQ-20260920-090](docs/REQM/REQ-20260920-090-combo-test-panel.md) — 5-checkbox 调试面板（基础）
- [REQ-20260919-064](docs/REQM/REQ-20260919-064-preview-start-time.md) — 预览开始时间参数（preview_start）
- [REQ-20260919-066](docs/REQM/REQ-20260919-066-time-hms-input.md) — 时:分:秒 三段输入
- [REQ-20260920-089](docs/REQM/REQ-20260920-089-ffmpeg-deadlock-cancel-button.md) — 修了 BGM 路径的 ffmpeg 死锁 + finally record_finish
- [REQ-20260920-074](docs/REQM/REQ-20260919-074-fine-export-async-progress.md) — export_fine_video 异步基础设施

## Why

**测试速度 = 调试效率**。当前调试面板只能跑完整视频，5 种组合 = 半天。补上开始时间 + 时长后，每种组合 10 秒就够，5 种 = 50 秒。调试效率提升 **~360 倍**。

## How to apply

未来加任何「导出片段」类功能（不只是 BGM 调试）：
1. 复用 `_assemble_fine_filter` 的 `preview_start` + `duration` 参数
2. 输出文件名带时间后缀（不覆盖完整版本）
3. probe 类端点接受 `output_path`（不止默认 `outputs/final.mp4`）
