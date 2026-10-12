# DESIGN-20261012-099 — Stage 3「解析短视频内容」改版

对应需求：[REQ-20261012-099](../docs/REQM/REQ-20261012-099-stage3-content-parse-rework.md)

## 关键决策

### D1：不动 highlights.json 结构，只扩展元数据字段

Why：下游 Stage 4/5/6 与 `_inject_stage3_highlights` 全部依赖现结构；
改版语义只到 UI/输入侧。新增顶层字段 `prompt`（本次解析提示词）、
`image`（截图文件名，可空）做溯源，老项目缺字段按空处理。

### D2：用户提示词整体作为 system；JSON schema 约束放 user payload

Why：自定义提示词不会自带「输出 JSON」要求，若 schema 只在 system（现状），
自定义提示词下输出不可解析。把格式约束段固定拼在 user payload 尾部，
system 换成用户提示词也能稳定拿到结构化输出。模板路径（无自定义提示词）行为不变。

### D3：截图存 `stage3/input_image.<ext>`（覆盖式）+ state 镜像

Why：`short_video_file?kind=stage3` 已有安全路径校验（Path.name 比对 + is_relative_to），
零新代码即可让浏览器访问缩略图。state `stage3_analyze.image = {name, updated_at}`；
渲染时文件不存在则视为无图（防悬空引用）。

### D4：提示词两级存储放全局 `config/short_video_prompts.json`

Why：跟随 `llm_config` 的 `config/` 运行时目录惯例（已 gitignore、机器本地）。
「默认提示词」跨项目生效符合"默认"语义；历史列表同理。
结构：`{"default_prompt": {prompt, updated_at} | null, "history": [{prompt, updated_at}]}`，
history 最新在前、上限 10；save 语义见 REQ D 节。

### D5：非 vision 模型 + 需要发图 → 显式报错，不降级

Why：用户明确期望"AI 看了图"。静默丢图会让用户误判解析质量。
复用 `revision_service._is_vision_entry`（跨模块私有访问在 analyze 里已有先例 `_call_llm`）。

### D6：单片段重解析 = 独立 LLM 调用 + 原位替换

`rerun_stage3_one(repo_root, tid, pid, hl_index, prompt, use_image, entry)`：
- system = 用户提示词 + 追加「仅针对主题〈当前标题〉重新解析 1 条」定向指令
- user payload = SRT + 单条 JSON schema 约束
- `_validate_highlights` 复用（取校验后第 1 条）
- 替换 highlights.json 中 `index == hl_index` 的条目（title 为空时保留旧值），
  同步 `state.highlights` 镜像，`reset_downstream_stages("stage3_analyze")`
- LLM 原文落 `stage3/llm_raw_hl<N>.txt`（不覆盖整跑的 llm_raw.txt）

### D7：UI 面板重组（_stage3_analyze）

```
3 | 解析短视频内容  [badge][elapsed]
  ┌ 解析提示词 ─────────────────────────────┐
  │ textarea#slirn-stage3-prompt（初始=默认/模板生效文本）│
  │ 快速模板 radio ×3（点击 JS 填充文本域）            │
  │ [💾 设为默认][📜 存入列表][🧹 清空]               │
  │ 📜 历史提示词（≤10 条，点击载入，显示截断+时间）      │
  └──────────────────────────────────────┘
  截图：[file input][📤 上传] [缩略图][✖ 移除] [☑ 发送截图给 AI]
  片段数 [n]        [🔍 解析]
  片段卡片 ×N（新增 🔄 重新解析 按钮）
```

旧的 3 个 `<details>` 每模板编辑面板移除（被文本域体系取代）；
`sv-save/reset-custom-prompt` 前后端 API 保留（向后兼容），无 UI 入口。

## 数据流

- 渲染注入：`_inject_stage3_highlights` 追加 `prompt_settings = {default_prompt, history}`
  （读全局 config）+ `image`（读 state + 文件存在性）。
- 解析：JS 收集 `{prompt, n_clips, use_image}` → `short_video_stage3_run` →
  `run_stage3(prompt=..., image_path=stage3/input_image.*)` →
  有图走 `_call_llm_vision`，无图 `_call_llm`。
- 重解析：`{hl_index, prompt, use_image}` → `short_video_stage3_rerun_one`。

## 改动清单

| 文件 | 改动 |
|---|---|
| slirn_home/short_video_service.py | prompt settings CRUD + stage3 截图存取 + state 辅助 |
| slirn_home/short_video_analyze.py | run_stage3 扩展（prompt/image/vision/格式约束）、rerun_stage3_one |
| slirn_home/app.py | 5 个新端点 + stage3_run 扩展 + 注入扩展 |
| slirn_home/short_video_ui.py | _stage3_analyze 重构 + 每片段重解析按钮 |
| slirn_home/static/short_video.js | 新交互（填充/上传/两级保存确认/重解析） |
| slirn_home/static/home.css | 新样式 |
| tests/ | service prompt CRUD / analyze 新路径 / api 端点 |

## 风险

- 自定义提示词导致输出格式漂移 → D2 的 user 侧 schema 约束 + `_strip_json_block`
  已有容错（多对象合并/数组顶层/U+FFFD）。
- home.css 经 launch(css=) 注入 → 改完必须重启 7861（memory: slirn-css-launch-injection-restart）。
- `node --check` 验证 JS 语法（memory: js-edit-showtab-regression）。
