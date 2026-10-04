# REQ-20261001-095 · 短视频混剪：基础视频必选 + 背景图片类型 + 原始文件名

- **日期**：2026-10-01
- **来源**：用户需求（3 项）
  > 1. 「在AI生成三版分镜之前，需要选择一个视频作为生成分镜的基础视频，而不是由AI自动选」
  > 2. 「上传素材的类型要添加一个背景图片」
  > 3. 「在上传文件的时候显示的要显示用户所选文件的文件名。不要自己生成个一串没有意义的字符串」
- **状态**：已实现（全量 1079 测试通过，含真实 ffmpeg 背景图渲染 smoke）

## 1. 需求与实现

### 1.1 基础视频必选（不再 AI 自动挑）

| 层 | 改动 |
|---|---|
| service | `project.base_material_id` 新字段（持久化）；`generate_storyboard` 增加 `base_material_id` 参数，缺失/非法/非视频素材 → `ShortVideoError("请先选择基础视频…")`；LLM 提示词声明 segments 只能用基础视频，违规引用在规范化前统一替换；本地兜底分镜全部从基础视频取段（起点在视频时长内均匀分布） |
| 端点 | `short_video_ai_storyboard` 透传 `base_material_id`（先落库再生成）；`short_video_save`/`update_project` 支持保存选择 |
| UI | 创作 Brief 区块新增「基础视频」下拉（`#slirn-sv-base-video`，placeholder「（请选择基础视频）」，选中值回显）；JS 未选时前端拦截提示 |

### 1.2 背景图片类型（bg_image）

| 层 | 改动 |
|---|---|
| service | kind 白名单 + `bg_image`（仅接受图片扩展名，否则报错）；`_normalize_variant` 校验 `variant.bg_material_id` 只接受 bg_image 素材（普通 image 是 B-roll，自动清空） |
| UI | 上传类型下拉新增「背景图片」；每个版本表单新增「背景图（画布底图）」下拉（空 = 视频模糊填充）；kind 显示中文化（视频/图片/音频/背景图片） |
| 渲染 | 有背景图：单图片输入 `-loop 1` + `split=N` 分发各段（cover 铺满画布）替代默认 gblur 模糊填充分支；无背景图走原链路不变。**关键坑**：① 每段各开图片输入会 OOM（多段解码缓冲翻倍），必须单输入 + split；② 音频流引用从「段号=输入索引」改为记录每段真实视频输入索引 |

### 1.3 上传显示原始文件名

根因：上传端点先把文件落系统临时文件（随机名），素材 `name` 取自落盘名 → `asset_<时间戳>_<hex>___.png` 之类的无意义串。

修复：`add_material_path` 新增 `display_name` 参数 —— 素材 `name` = 用户所选原始文件名（中文原样）；磁盘文件名仍走 ASCII 安全化 + 唯一前缀（防穿越/防覆盖）。上传端点传 `file.filename`。素材卡、各下拉、B-roll 选择均显示原始名。

## 2. 验收标准与结果

| # | 验收 | 结果 |
|---|---|---|
| AC1 | 不选基础视频点「AI 生成三版分镜」→ 明确报错；选后生成的主片段全部来自该视频 | ✅ `test_short_video_storyboard_requires_base_video` + API 测试（前后端双侧拦截） |
| AC2 | 上传类型可选「背景图片」；背景图作为画布底图渲染；普通图片仍可作 B-roll | ✅ `test_short_video_bg_image_kind_validation` / `_normalize_variant_bg_material_id` / `_render_command_bg_image_background` + 真实 ffmpeg smoke |
| AC3 | 上传后素材名显示用户原始文件名（含中文），非服务端生成串 | ✅ `test_short_video_upload_preserves_original_filename` + API 中文名测试 |
| AC4 | 既有行为不回归 | ✅ 全量 1079 passed |

## 3. 影响面

- 修改：`short_video_service.py`、`short_video_render.py`、`short_video_ui.py`、
  `static/short_video.js`、`app.py`（2 端点小改）
- 测试：`tests/test_short_video_service.py`（4 个既有用例适配必选基础视频 + 5 个新增）、
  `tests/test_short_video_api.py`（1 个新增 + 导入顺序修复使本文件可独立运行）
- 兼容性：旧项目 `base_material_id` 缺省为 `""`（重新生成时要求选择）；旧分镜/渲染不受影响
