# REQ-20260914-001-C — 新建任务 - 视频 + 时间截取（**子 REQ C**）

| 字段 | 值 |
|---|---|
| 编号 | REQ-20260914-001-C（子 REQ） |
| 日期 | 2026-09-14 |
| 优先级 | P1（核心流程，依赖 REQ-A） |
| 状态 | 📝 草案 |
| 上位 Epic | [REQ-20260914-001](../REQM/REQ-20260914-001-custom-home-task.md) |
| 上游依赖 | [REQ-20260914-001-A](../REQM/REQ-20260914-001-A-task-data-model.md)（已完成） |
| 关联仓库 | slirn-standalone（视频截取 ffmpeg 调用） + funclip-main（UI） |
| 代码归属 | `slirn-standalone/tasklib/video.py`（截取逻辑） + `funclip-main/slirn_home/create_task.py`（UI） |
| 范围 | 文件选择、拖拽、时间截取（ffmpeg 调用）、新建任务 UI 入口 |

---

## 0. 与上游 REQ 的关系

- REQ-A 已实现 `TaskManager.create()`，含 `segment: TimeSegment` 参数（**但截取段文件实际生成逻辑未实现**——REQ-A 只生成 metadata，截取段文件由 REQ-C 创建）
- REQ-B 已实现 UI 框架，**新建任务按钮是占位**，本 REQ 实现真实逻辑
- 本 REQ 通过 ffmpeg 调用实现真实视频截取，调用结果落盘到 `tasks/<id>/raw_input/` 后再调 `TaskManager.create()`

---

## 1. 背景与目标

**当前状态**：自定义首页有"➕ 新建任务"按钮，但点击只弹 toast（REQ-B 占位）。

**目标**：实现真实的新建任务流程：
1. 用户选/拖拽视频文件
2. （可选）设置起止时间 → 截取视频
3. （REQ-C 不做）从公共热词库选择热词（**REQ-D 范围**）→ 本 REQ 支持手动输入
4. 点击"创建" → 任务入库

**价值**：补齐"任务驱动工作流"的最核心入口。

---

## 2. 用户故事

### US-C1：选文件 + 直接创建

- **角色**：项目使用者
- **场景**：想用整段视频做剪辑
- **操作**：点"新建任务" → 选视频 → 输入任务名 → 点"创建"
- **结果**：任务入库（无截取段）

### US-C2：选文件 + 截取后创建

- **角色**：同上
- **场景**：长视频只想用其中一段（00:01:00 - 00:05:00）
- **操作**：选视频 → 设起止时间 → 点"截取预览" → 点"创建"
- **结果**：截取段生成在 `tasks/<id>/raw_input/`，任务入库指向截取段

### US-C3：拖拽文件

- **角色**：同上
- **场景**：不想用文件选择器
- **操作**：拖拽视频到上传区
- **结果**：文件被识别（同 US-C1 流程）

---

## 3. 核心需求

### 3.1 视频文件选择

| ID | 需求 |
|---|---|
| REQ-C.1.1 | 提供"打开文件"按钮，点击弹系统文件选择器 |
| REQ-C.1.2 | 支持拖拽上传（Gradio `gr.File(file_count="single")`） |
| REQ-C.1.3 | 接受格式：mp4/avi/mkv/mov/webm/ts/mpeg（与 funclip 一致） |
| REQ-C.1.4 | 文件选择后显示文件名 + 文件大小（**视频时长需要 ffprobe，留 D1 决定**） |
| REQ-C.1.5 | **不限制**文件大小/时长（Epic §11.3 Q10 答案） |

### 3.2 时间截取 UI

| ID | 需求 |
|---|---|
| REQ-C.2.1 | 两个文本框：`start_time`、`end_time`，格式 HH:MM:SS 或 HH:MM:SS.mmm |
| REQ-C.2.2 | 两个文本框**可为空**：为空表示不截取，使用完整视频 |
| REQ-C.2.3 | 「截取预览」按钮：调用 ffmpeg 截取到临时位置，在 UI 上预览 |
| REQ-C.2.4 | 「跳过截取」按钮：清空两个文本框 |
| REQ-C.2.5 | 校验：start < end、end ≤ 视频总时长（**总时长检查留 D1**） |
| REQ-C.2.6 | 截取段命名遵循 REQ-A.4.1：`<stem>_segment_<HH-MM-SS>_<HH-MM-SS>.<ext>` |
| REQ-C.2.7 | 截取完成后展示截取段路径 + 大小 + 时长 |

### 3.3 任务元数据输入

| ID | 需求 |
|---|---|
| REQ-C.3.1 | 任务名文本框（默认从视频文件名生成，去扩展名） |
| REQ-C.3.2 | 任务级热词文本框（手动输入，多个用空格或换行分隔） |
| REQ-C.3.3 | 「创建」按钮：提交，调 `TaskManager.create()` |
| REQ-C.3.4 | 「取消」按钮：清空所有输入，返回任务列表 |

### 3.4 创建后行为

| ID | 需求 |
|---|---|
| REQ-C.4.1 | 创建成功后切换回"任务列表" tab，自动刷新 |
| REQ-C.4.2 | 显示成功 toast：「✅ 已创建任务 <task_id>」 |
| REQ-C.4.3 | 创建失败显示错误 toast（FFmpeg 调用失败 / 校验失败 / 文件不存在） |

### 3.5 视频处理底层（tasklib/video.py）

| ID | 需求 |
|---|---|
| REQ-C.5.1 | `video.py:get_video_duration(path: Path) -> float \| None` 用 ffprobe（**或 ffmpeg**）获取时长（秒） |
| REQ-C.5.2 | `video.py:cut_video(src: Path, dst: Path, start: str, end: str) -> None` 用 ffmpeg 截取（**不重新编码，stream copy**） |
| REQ-C.5.3 | `video.py:probe_video(path: Path) -> VideoInfo` 返回 `{duration, codec, size}`（**或仅 duration，留 D1**） |
| REQ-C.5.4 | ffmpeg/ffprobe 调用走 `subprocess.run`，**捕获 stderr** 用于错误提示 |
| REQ-C.5.5 | 失败时抛 `VideoProcessingError`（继承 `TaskError`） |

---

## 4. 验收标准

### AC-C.1 文件选择

- [ ] **AC-C.1.1** "打开文件" 按钮点击后弹出系统文件选择对话框
- [ ] **AC-C.1.2** 拖拽 mp4 文件到上传区，松手后文件名被识别
- [ ] **AC-C.1.3** 选择/拖拽后 UI 显示文件名 + 文件大小
- [ ] **AC-C.1.4** 不接受格式（如 .txt）被拒绝，弹错误提示
- [ ] **AC-C.1.5** 文件不存在/无权限时弹错误提示

### AC-C.2 时间截取

- [ ] **AC-C.2.1** 输入 `00:01:00` 和 `00:05:00`，点「截取预览」生成 `<stem>_segment_00-01-00_00-05-00.mp4`
- [ ] **AC-C.2.2** 截取段位于临时目录（`slirn-standalone/.temp/`），创建任务时才移到 `<task_dir>/raw_input/`
- [ ] **AC-C.2.3** start ≥ end 弹错误，不执行截取
- [ ] **AC-C.2.4** start/end 都为空 = 不截取，使用完整视频
- [ ] **AC-C.2.5** 截取后预览区显示截取段（Gradio Video 组件）
- [ ] **AC-C.2.6** 「跳过截取」按钮清空起止时间文本框

### AC-C.3 任务元数据

- [ ] **AC-C.3.1** 任务名默认 = 视频文件名 stem
- [ ] **AC-C.3.2** 任务名可手动修改
- [ ] **AC-C.3.3** 任务级热词输入框接受多行输入
- [ ] **AC-C.3.4** 「创建」按钮触发 `TaskManager.create(...)`
- [ ] **AC-C.3.5** 「取消」按钮清空所有输入并切回任务列表 tab

### AC-C.4 创建后行为

- [ ] **AC-C.4.1** 创建成功后自动切回任务列表 tab
- [ ] **AC-C.4.2** 创建成功后任务列表自动刷新（含新任务）
- [ ] **AC-C.4.3** 创建成功显示 toast「✅ 已创建任务 <task_id>」
- [ ] **AC-C.4.4** 创建失败显示错误 toast（含原因）

### AC-C.5 视频处理底层

- [ ] **AC-C.5.1** `cut_video()` 调用 ffmpeg stream copy 模式（不重新编码）
- [ ] **AC-C.5.2** `cut_video()` 成功生成目标文件
- [ ] **AC-C.5.3** `cut_video()` 失败抛 `VideoProcessingError`，错误信息含 ffmpeg stderr
- [ ] **AC-C.5.4** `get_video_duration()` 调用 ffprobe 或 ffmpeg，返回秒数（float）
- [ ] **AC-C.5.5** `get_video_duration()` 对损坏文件返回 None 或抛 VideoProcessingError
- [ ] **AC-C.5.6** tasklib 不引入 ffmpeg-python 等新库（**仅 stdlib + subprocess**）

### AC-C.6 UI 集成

- [ ] **AC-C.6.1** REQ-B 的「➕ 新建任务」按钮切换到「新建任务」tab
- [ ] **AC-C.6.2** 新建任务 tab 包含文件选择、时间、任务名、热词、按钮
- [ ] **AC-C.6.3** UI 代码在 `funclip-main/slirn_home/create_task.py`
- [ ] **AC-C.6.4** 截取逻辑在 `slirn-standalone/tasklib/video.py`
- [ ] **AC-C.6.5** UI 通过 `from tasklib.video import cut_video` 引用（sys.path 已有）

---

## 5. 非目标（**本 REQ 不做**）

- ❌ 公共热词库选择（REQ-D 范围）
- ❌ 视频转码/压缩（Epic §6）
- ❌ 多任务并发创建（单用户场景无并发）
- ❌ 视频缩略图生成（可选增强，留后续）
- ❌ 上传进度条（subprocess.run 不易做进度，留后续）
- ❌ 已存在任务的「编辑任务信息」（Epic §3.2.4 留 TODO，本 REQ 不做）

---

## 6. 技术约束

- **底层**：ffmpeg（已确认在 PATH）+ stdlib subprocess
- **不引入**：ffmpeg-python、moviepy（避免新依赖；tasklib 保持纯 stdlib 风格）
- **截取模式**：stream copy（`-c copy`），不重新编码（速度 + 画质无损）
- **代码组织**：
  - 截取逻辑 `slirn-standalone/tasklib/video.py`（**注意**：tasklib 当前只有 models/manager/schema/time_utils，video.py 是新文件，但属于 tasklib 命名空间下）
  - UI `funclip-main/slirn_home/create_task.py`
- **Gradio 组件**：`gr.File`、`gr.Video`、`gr.Textbox`、`gr.Button`

---

## 7. 反例（**不该做的设计**）

- ❌ 引入 ffmpeg-python 库（违反「仅 stdlib + subprocess」）
- ❌ 截取后立即复制到 task_dir（应该先临时预览，确认后再移动）
- ❌ 截取段和原视频路径混淆（必须明确区分）
- ❌ 视频时长检查过于严格（ffmpeg 允许 end 略超出，用 `<=` 而非 `<`）
- ❌ 不捕获 ffmpeg stderr（错误信息丢失）
- ❌ 创建按钮无二次校验（应该预先验证所有输入）

---

## 8. 已确认的 Epic 级决策（**沿用**）

| 决策点 | 来源 | 值 |
|---|---|---|
| 视频大小/时长限制 | Epic §11.3 Q10 | 不限制 |
| 截取段命名 | REQ-A.4.1 | `<stem>_segment_<HH-MM-SS>_<HH-MM-SS>.<ext>` |
| 时间格式 | REQ-A.3.4 | HH:MM:SS 或 HH:MM:SS.mmm |
| 任务数据存储 | REQ-A | metadata.json + hotwords.txt |
| 任务状态初始值 | REQ-A | DRAFT |

---

## 9. 下一阶段

进入 [DESIGN-20260914-001-C-create-task-video.md](../../design/DESIGN-20260914-001-C-create-task-video.md) 系统设计。

**关键设计问题**：
- D1：视频时长是必填还是可选？ffprobe 调用失败时如何降级？
- D2：临时预览目录路径（`.temp/cut_<uuid>.mp4` vs `tasks/<new_task_id>/raw_input/...`）
- D3：「跳过截取」按钮是否真的需要，还是直接让文本框为空即可
- D4：起止时间是否支持手动输入（毫秒精度）+ 滑块辅助
- D5：截取后预览的实现（Gradio Video 组件 + 文件路径 vs 流式）

---

## 10. 出口条件

- [ ] §3 每条需求都有可验证的验收标准（§4）
- [ ] §5 非目标明确
- [ ] §6 技术约束与 Epic §11 答案一致
- [ ] §7 反例列举 ≥ 3 条
