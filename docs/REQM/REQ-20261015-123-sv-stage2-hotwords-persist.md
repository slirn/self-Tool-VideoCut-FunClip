# REQ-20261015-123 — Stage 2 热词自动保存（落盘 + 回填）

> 状态：✅ 已完成（2026-10-15）
> 关联：[REQ-20261015-122](REQ-20261015-122-sv-stage2-remove-model-lang-and-hotwords-comma.md)
> （同一天把热词输入改半角逗号分隔）

## 1. 需求（用户原话）

「第二阶段，提取字幕功能中热词没有自动保存」

## 2. 根因

- `run_stage2` 只把 `model`/`lang` 写进 stage2 状态，**热词没落盘**；
- UI `_stage2_extract` 的热词 `<input>` 没有 `value` 回填；
- 提取完成 → `openProject` 整卡重渲染 → 输入框变空，用户下次提取要重敲。

## 3. 实现

| 文件 | 改动 |
|---|---|
| `slirn_home/short_video_extract.py` | `run_stage2` 状态新增 `state["hotwords"] = hotwords`（存用户原始输入；逗号→空格归一仍在 `_run_funasr_extract` 内做，不提前破坏原样回填） |
| `slirn_home/short_video_ui.py` | 热词 input 加 `value="{hotwords_v}"`（`esc()` 转义），重渲染/刷新后回填 |

保存时机 = 点「提取字幕」运行时（与 model/lang 同口径）；只输入不运行
不保存（无副作用写入）。

## 4. 测试（+1，功能级）

`test_stage2_hotwords_saved_and_rendered_back`：mock `_run_funasr_extract`
成功 → `run_stage2(hotwords="智能体,Agent，工作流")` → 状态存原始输入 →
`load_project` + `render_project` → HTML 含 `value="智能体,Agent，工作流"`。
