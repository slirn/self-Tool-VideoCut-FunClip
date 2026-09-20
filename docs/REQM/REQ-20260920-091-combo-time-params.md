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

---

## v2 修订（2026-09-20 晚）— combo-test UX 反馈修复

### 用户原始反馈（直接引用）

> 刚才我直接点一键测试合成，也没有任何反馈，起码它能生成一个视频，然后弹出窗口啊，这个反馈都没有

### 根因分析

combo-test 完成后只有两个反馈路径：
1. `_appendOutput(html)` 写文本到 `#slirn-combo-test-output-{tid}`
2. `_setBusy(false)` 把按钮 `disabled = false`

但 `outputEl` 在 `<details>` 内（折叠时不可见），`_setBusy` 只是把按钮变灰**不改文字** → 用户看不到任何反馈，以为按钮卡死。

后端 E2E 实测**完全正常**（curl 1.8 秒完成 + probe 报告正确）—— **不是 BUG，是 UX 缺失**。

### 用户还问了什么

> 一键测试合成前面还有一个应用勾选写F、C, 这是什么意思？每次我都点了这个按钮

→ 问 combo-apply 的用途。答案是：combo-test 内部**已经**调 `_applyComboToFC`（[router.js:3818](slirn_home/static/router.js#L3818)），combo-apply 是冗余的（用户每次都点两次）。

### 意外发现的 BUG

每次 combo-apply 后再 combo-test，会调两次 `_snapshot()`：
- combo-apply：snapshot 原状态 → 写入 `window._comboSnapshot[tid]`
- combo-test：snapshot 入口又调一次 `_snapshot()` → **覆盖**为「测试状态」（不是原始 fc）

→ 用户点「↩️ 还原」按钮还原不到原始 fc，只能还原到「测试状态」。

### 修复（v2）

| 维度 | 决策 |
|---|---|
| combo-test 点击反馈 | **toast 立刻通知 + 按钮文字变化**（参考 REQ-077 `setExportBtnState` 模式） |
| running 状态 | 按钮文字 → `⏳ 测试中…` |
| done 状态 | 按钮文字 → `✅ 完成 · 查看视频` + onclick → `window.open(outputUrl, '_blank')` 自动弹视频 |
| failed 状态 | 按钮文字 → `❌ 失败 · 重试` |
| 超时状态 | 按钮文字 → `🚀 一键测试合成`（恢复原状） |
| time-suffix 文件 | 提示用户去本地 outputs 目录查看（**不**自动弹 video 端点，因只支持 src=fine_export） |
| combo-apply | **保留**按钮（不删）—— 部分用户想手动写 fc 但不立即测试 |
| _snapshot() 早期 return | 加 `if (window._comboSnapshot[tid] !== undefined) return;` 防覆盖 |

### 验收标准（v2 新增）

| AC | 描述 |
|---|---|
| AC-11 | combo-test 点击立刻 toast `🚀 一键测试合成已启动（{dur}）` |
| AC-12 | combo-test 进入时按钮文字变 `⏳ 测试中…` |
| AC-13 | combo-test done 时按钮文字变 `✅ 完成 · 查看视频` + 自动 `window.open` |
| AC-14 | combo-test fail 时按钮文字变 `❌ 失败 · 重试` |
| AC-15 | 自动 `window.open` 仅当 `outPath === 'outputs/fine_export.mp4'`（video 端点支持） |
| AC-16 | time-suffix 文件不自动弹 video 端点，提示用户去本地查看 |
| AC-17 | `_snapshot()` 加 early-return——已在 snapshot 时不覆盖 |
| AC-18 | 2 个新测试（button_state_changes + only_autoopens_for_default_output） |

### v2 测试

```
tests\test_workbench.py ...                                              [100%]
3 passed, 260 deselected in 0.57s
```

- `test_combo_test_button_state_changes` —— AC-11/12/13/14
- `test_combo_test_only_autoopens_for_default_output` —— AC-15/16
- `test_combo_snapshot_not_overwritten_by_combo_test` —— AC-17

### 完整测试套件

```
631 passed, 3 warnings in 46.18s
```
（629 from v1 + 2 new from v2）

### v2 commit

- `feat(combo-test): REQ-20260920-091 v2 combo-test UX 反馈修复（toast + 按钮状态 + 自动弹视频）`

### Why v2

**没有反馈 = 用户不知道点成功没有**。即使后端 E2E 跑通，前端看不到任何变化 → 用户以为按钮卡死再点几下 → 重复触发 + 资源浪费。

按钮状态机（idle/running/done/failed）是 Gradio 上「💾 导出最终视频」按钮的标准 UX（参考 REQ-077），调试面板必须复用相同模式，否则用户会困惑「为什么点了没反应」。

