# REQ-20261001-096 · 短视频混剪：素材查看页内弹窗 + 素材删除

- **日期**：2026-10-01
- **来源**：用户需求（2 项）
  > 1. 「音频、视频图片等，在查看的时候弹出一个小窗口，不要弹出一个页签儿」
  > 2. 「上传的文件还需要有删除功能」
- **状态**：已实现（全量 1081 测试通过）

## 1. 需求与实现

### 1.1 素材「查看」→ 页内小窗（不再开新页签）

| 层 | 改动 |
|---|---|
| UI | 素材卡的 `查看` 从 `<a target="_blank">` 改为按钮 `data-action="sv-preview"`（携带 `data-url` / `data-kind` / `data-name`） |
| JS | `sv-preview` 打开页内模态（复用 `.slirn-modal-overlay` / `.slirn-modal-card` 体系）：视频 → `<video controls autoplay>`，音频 → `<audio controls>`，图片/背景图 → `<img>`；Esc / 点遮罩 / ✕ 关闭，关闭时暂停媒体释放声音 |
| CSS | `.slirn-sv-preview-card / -head / -title / -body`：限宽 `min(720px, 88vw)`、媒体限高 `min(64vh, 640px)`，保持「小窗口」观感 |
| 文件服务 | 素材预览 URL 改为 **按素材 id** 取文件（`kind=material` 新分支）：修复了自动导入的任务视频不在 assets 目录、旧 `kind=asset` 取不到文件的问题。路径限定在 `tasks/` 内（防穿越） |

### 1.2 素材删除

| 层 | 改动 |
|---|---|
| service | `remove_material(repo_root, task_id, project_id, material_id)`：上传素材（source=upload）连磁盘文件删（**仅限本项目 assets 目录内**，双保险）；任务自动导入素材（source=auto）**只移除引用不删源文件**（文件属于任务流水线）。同步清理引用：`base_material_id`、分镜 `segments` / `overlays`、`bgm_material_id`、`bg_material_id` |
| 端点 | `POST /slirn/api/short_video_remove_material`，成功返回新 `project` + 刷新后的 `html` |
| UI / JS | 素材卡新增红色「删」按钮（`sv-remove-material`），`confirm` 二次确认后调用端点并整页刷新 |

### 1.3 行为说明（非缺陷）

- 删除基础视频后 `base_material_id` 置空，重新「AI 生成三版分镜」时要求再选（与 REQ-095 必选逻辑一致）。
- 删除素材可能让某版本 segments 清空 → 该版本渲染时报「分镜没有视频片段」，需重新生成分镜或手动加片段；编辑器中清晰可见。

## 2. 验收标准与结果

| # | 验收 | 结果 |
|---|---|---|
| AC1 | 素材库点「查看」弹出页内小窗播放/显示，不开新页签 | ✅ `test_short_video_ui_contains_editor_and_materials`（无 `target="_blank"`）+ `test_short_video_preview_popup_and_remove_material` |
| AC2 | 视频 / 音频 / 图片 / 背景图分别用对应标签预览；Esc / 遮罩 / ✕ 可关 | ✅ JS 实现 + `node --check`（浏览器交互不进单测） |
| AC3 | 自动导入的任务视频也能预览（旧链路取不到文件） | ✅ `kind=material` 分支 + API 测试（auto 素材 200） |
| AC4 | 上传素材可删除：条目 + 磁盘文件 + 分镜引用一并清理 | ✅ `test_short_video_remove_material` |
| AC5 | 任务导入素材删除不删源文件 | ✅ 同上（断言源文件仍存在） |
| AC6 | 既有行为不回归 | ✅ 全量 1081 passed |

## 3. 影响面

- 修改：`short_video_service.py`（+`remove_material`）、`app.py`（+1 端点，`short_video_file` 加 `kind=material` 分支）、
  `short_video_ui.py`（素材卡两按钮）、`static/home.css`（弹窗样式）、`static/short_video.js`（弹窗 + 删除）
- 测试：`tests/test_short_video_service.py`（UI 断言扩展 + `test_short_video_remove_material`）、
  `tests/test_short_video_api.py`（`test_short_video_preview_popup_and_remove_material`）
- 兼容性：旧 `kind=asset` / `kind=output` 文件服务保留；旧项目无素材 id 变化，无需迁移
