# REQ-20260920-090 合成元素组合测试面板（debug）

## 背景

用户调试 BGM 路径时发现：单独合成「视频 + BGM」已可（REQ-20260920-089 修复后 1.8 秒完成，status=success）。但**当勾选更多元素（字幕 / 封面 / 背景图片）时，BGM 是否仍存在**不确定——尤其当元素组合变化时，`inputs_count` 改变导致 `audio_idx` 重新计算，可能引入新的回归。

REQ-080/083/085/089 这一系列 BGM 修复只验证了「单独视频 + BGM」场景。当用户启用更多元素时：
- `inputs_count` 改变 → `audio_idx = inputs_count - 1` 重算（audio 始终最后）
- bg/cover 元素加了 overlay/subtitles filter → 可能影响 filter_complex chain 顺序
- 字幕 force_style 解析可能因 bg/cover 的设置不同而异常

用户要一个**调试面板**：5 个勾选框（视频 / 字幕 / 封面 / 背景 / BGM）+ 一键测试按钮，输出「是否包含 BGM」的诊断报告。手动勾不同组合测试，能直观对比合成路径差异。

## 用户影响

| 场景 | 表现 |
|---|---|
| 用户怀疑「加了字幕 + 封面 + 背景，BGM 是不是没了？」 | 没有工具能验证 → 只能下载 output 文件听 |
| 用户调 `_assemble_fine_filter` 后担心 BGM 回归 | 没办法快速试 5 种组合 |
| 用户新加合成元素（watermark/sticker/transition） | 不知道 BGM 是否仍存在 |

## 用户明确要求（直接引用）

> 单独合成视频和背景音乐时确实可以了。那你比较一下，单独合成这两个和同时合成视频字幕、封面、背景图片和这个有什么差别？甚至你可以试一下单独加一个测试方法。这个测试方法可以让我来勾选只合成视频和背景音乐，还是勾选视频字幕和背景音乐，甚至可以勾选视频字幕、封面图片、背景音乐这样的组合方式，一步步来测试为什么合成时没有背景音乐。

## 用户决策（已确认）

| 维度 | 决策 |
|---|---|
| 测试面板目标 | **调试面板（临时用）** —— 5 个勾选 + 一键测试 + 输出 BGM 检测报告 |
| 面板位置 | **工作台顶部独立 panel** —— 跨任务可用（位于 `_render_fine_cut_zone` 顶部） |
| 异步能力 | **复用现有 `export_fine_video` 异步基础设施** —— 进度条 + ⏹ 取消按钮现成 |

## 验收标准

| AC | 描述 |
|---|---|
| AC-1 | 工作台顶部出现「🧪 合成元素组合测试」独立 details 折叠块（默认展开） |
| AC-2 | details 内有 5 个 `data-combo-kind` checkbox：video / subtitle / cover / bg / audio |
| AC-3 | 顶部红色提示「⚠️ 这会修改 fc 当前勾选状态；测试后会提示还原」 |
| AC-4 | 4 个按钮：「📝 应用勾选（写 fc）」「🚀 一键测试合成」「🔍 仅诊断」「↩️ 还原上次配置」（最后一个默认隐藏） |
| AC-5 | `/slirn/api/diagnose_bgm` 端点：body `{task_id}` → 返回 `_predict_audio_path` 结果（含 elements / inputs_count / audio_idx / predicted_has_bgm / predicted_audio_filters / why_no_bgm） |
| AC-6 | 「🔍 仅诊断」按钮：调 diagnose_bgm → 渲染结构化报告到 `#slirn-combo-test-output-{tid}`（含每个元素 will_render 状态 + BGM 预测 + why） |
| AC-7 | 「📝 应用勾选」按钮：把 5 个 checkbox 的勾选状态写入 fc（调 save_fine_layout 4 个 + save_fine_audio 1 个） |
| AC-8 | 「🚀 一键测试合成」按钮：先 apply → 调 export_fine_video → 轮询 render_status → 显示完成结果 |
| AC-9 | 「🚀 一键测试合成」完成后调 `/slirn/api/probe_output_audio` 用 ffprobe volumedetect 探测 output 音频 + 对比 BGM 源 mean_volume，显示匹配度报告 |
| AC-10 | 「↩️ 还原上次配置」按钮：apply 前 snapshot 原 5 个 enabled → 点 restore 写回（前端 state，刷新页面后失效） |
| AC-11 | router.js 包含 3 个新 action dispatch 分支：`combo-apply` / `combo-test` / `combo-diagnose`（combo-restore 不需后端 API） |
| AC-12 | 测试面板 5 个 checkbox 默认勾选状态：video ✅ / subtitle ❌ / cover ❌ / bg ❌ / audio ✅（匹配当前 fc 默认） |
| AC-13 | 「🚀 一键测试」异步跑时复用 REQ-089 的 export_fine_video / render_status / cancel_render 基础设施（不另起一套） |
| AC-14 | CSS：新增 `.slirn-combo-test-block` / `.slirn-combo-test-row` / `.slirn-combo-test-output` 样式（紧凑布局 + checkbox 横向排列） |
| AC-15 | 4 个新测试：HTML 含 5 个 checkbox / diagnose_bgm endpoint 存在 / router.js 有 3 个 action / combo-apply 调正确的 endpoints |
| AC-16 | 620 → 624 测试（+4），pytest 全绿 |

## 范围外

- 不改 `_assemble_fine_filter` 主逻辑
- 不改 REQ-089 已修的 Popen / finally record_finish
- 不新增 LLM 调用
- 不修改其他 fc 字段（只动 layout.enabled + audio.enabled）
- 不持久化 snapshot（仅前端 state，刷新失效）

## 风险

| 风险 | 处理 |
|---|---|
| `combo-apply` 写 fc 会覆盖用户当前配置 | 顶部红色提示 + 「↩️ 还原」按钮 |
| `combo-test` 每次跑 ffmpeg 可能 1-10 秒 | 异步跑，轮询 + 取消按钮复用 |
| 勾选没对应素材（勾字幕但没 SRT） | diagnose 检测 → 报告里提示「素材缺失，filter 不会渲染」 |
| diagnose 报告太技术化 | 加 emoji + 简洁描述 |
| 用户连续点 combo-test 启动多个 job | 启动时禁用按钮，job 完成后恢复 |

## 关联

- [REQ-20260920-089](docs/REQM/REQ-20260920-089-ffmpeg-deadlock-cancel-button.md) — 修了 BGM 路径的 ffmpeg 死锁 + finally record_finish
- [REQ-20260920-080](docs/REQM/REQ-20260920-080-fix-bgm-filter-chain-label.md) — 修了 `[bgm]` label 不能 `","` 拼接的 BUG
- [REQ-20260920-083](docs/REQM/REQ-20260920-083-bgm-path-resolver-mismatch.md) — 修了 select_default_bgm 路径解析 BUG
- [REQ-20260920-085](docs/REQ-20260920-085-upload-audio-auto-enable-bgm.md) — 修了上传音频自动启用 BGM

## Why

**组合测试 = BGM 路径回归防护**。当前 BGM 修复只验证了「单独视频 + BGM」场景，缺一个工具能逐步验证元素组合变化时 BGM 仍存在。调试面板让用户能**逐个组合验证**，是防止 BGM 回归的最后一道保险。

## How to apply

未来添加新合成元素（watermark / sticker / transition）时：
1. 加进 `_FINE_LAYOUT_DEFAULTS` + 在 `_assemble_fine_filter` 处理 gate
2. 在测试面板的勾选列表里加一项
3. diagnose 报告里加对应元素的状态字段

调试面板可长期保留作为「合成元素组合回归防护」——每次改 filter assembly 都要跑一遍。
