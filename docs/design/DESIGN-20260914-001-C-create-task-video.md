# DESIGN-20260914-001-C — 新建任务 - 视频 + 时间截取（**设计**）

| 字段 | 值 |
|---|---|
| 编号 | DESIGN-20260914-001-C |
| 日期 | 2026-09-14 |
| 对应 REQ | [REQ-20260914-001-C](../REQM/REQ-20260914-001-C-create-task-video.md) |
| 状态 | ✅ 设计完成 |

---

## 1. 决策矩阵（5 个关键决策）

### D1：视频时长获取 —— **ffprobe，可选降级**

#### 选择

`get_video_duration()` 调用 ffprobe；如 ffprobe 失败（不在 PATH / 文件损坏），返回 `None` 而非抛异常。UI 上不显示时长限制，让用户自己保证。

#### 为什么

| 候选 | 取舍 |
|---|---|
| ❌ 必须有时长，否则拒绝创建 | 太严格；很多边缘情况会卡 |
| ✅ **ffprobe，失败返回 None，UI 不强制** | 鲁棒 + 不阻塞用户 |

#### 实现

```python
def get_video_duration(path: Path) -> float | None:
    """调用 ffprobe 获取时长（秒）。失败返回 None。"""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            log.warning("ffprobe 失败: %s", result.stderr)
            return None
        return float(result.stdout.strip())
    except (FileNotFoundError, ValueError, subprocess.TimeoutExpired) as e:
        log.warning("ffprobe 异常: %s", e)
        return None
```

---

### D2：临时预览目录 —— **`.temp/cut_<uuid>.mp4`**

#### 选择

截取预览时，截取段先存到 `slirn-standalone/.temp/cut_<uuid>.mp4`；用户点「创建」时再 mv 到 `tasks/<new_task_id>/raw_input/`，并把临时文件删除。

#### 为什么

| 候选 | 取舍 |
|---|---|
| ❌ 直接截到 `tasks/<id>/raw_input/`，需要先创建 task 再截 | 必须先调 `mgr.create()`，但 task_id 是 create 时生成的，截取路径里就要 task_id——鸡生蛋 |
| ❌ 截到 `tasks/draft_<uuid>/` 临时任务目录 | 复杂；用户取消时还要清理 |
| ✅ **`.temp/cut_<uuid>.mp4`** | 简单、`.temp/` 已在 .gitignore、有 uuid 防冲突 |

#### 实现

```python
import uuid
def cut_to_temp(src: Path, start: str, end: str) -> Path:
    temp_dir = src.parent.parent / ".temp"   # slirn-standalone/.temp/
    temp_dir.mkdir(exist_ok=True)
    dst = temp_dir / f"cut_{uuid.uuid4().hex[:8]}_{src.stem}.mp4"
    cut_video(src, dst, start, end)
    return dst
```

创建任务时：
```python
mgr.create(...)
# 把临时截取段移到 task_dir
shutil.move(temp_cut, task_dir / "raw_input" / final_name)
```

---

### D3：「跳过截取」按钮 —— **不需要**

#### 选择

去掉「跳过截取」按钮，让两个时间文本框**可为空**（已满足 REQ-C.2.2），用户清空就等于跳过。

#### 为什么

冗余按钮 = UI 噪音。文档说明「文本框留空 = 不截取」即可。

---

### D4：起止时间输入 —— **两个文本框 + 格式提示**

#### 选择

两个独立 `gr.Textbox`（`start_time`、`end_time`），placeholder 写 `HH:MM:SS 或留空`。无滑块（滑块不适合精确时间戳）。

#### 为什么

| 候选 | 取舍 |
|---|---|
| ❌ 滑块 | 不适合精确时间戳（fps 级别） |
| ❌ 时间选择器（小时/分钟/秒三下拉） | Gradio 原生无；自造麻烦 |
| ✅ **两个文本框 + placeholder 提示** | 直接、易校验 |

---

### D5：截取后预览 —— **Gradio Video 组件 + 路径**

#### 选择

截取后把临时视频路径传给 `gr.Video(value=temp_path)`，Gradio 自动渲染播放控件。

#### 为什么

Gradio Video 组件接受文件路径直接渲染，无需额外处理。

---

## 2. 端到端示例

### 2.1 调用

```python
# tasklib/video.py 公共接口
from tasklib.video import get_video_duration, cut_video, probe_video
from pathlib import Path

src = Path("D:/videos/lecture.mp4")

# 获取时长（可选）
duration = get_video_duration(src)  # 3600.5 秒

# 截取
dst = Path("slirn-standalone/.temp/cut_abc12345_lecture.mp4")
cut_video(src, dst, "00:01:00", "00:05:00")
```

### 2.2 UI 流程

```
[新建任务 tab]
  ┌─────────────────────────────────────┐
  │ 📁 视频文件：                        │
  │   [选择文件]  lecture.mp4 (50.2 MB) │
  │   或拖拽到此处                        │
  │                                      │
  │ ⏱ 时间截取（留空 = 不截取）：        │
  │   开始：[00:00:00      ]             │
  │   结束：[00:00:00      ]             │
  │   [🎬 截取预览]                      │
  │                                      │
  │ 🎥 预览（截取后）：                  │
  │   [ video player ]                   │
  │                                      │
  │ 📝 任务名：[默认从文件名生成]        │
  │ 🔥 任务级热词（手动输入）：          │
  │   [textarea]                         │
  │                                      │
  │ [✅ 创建]  [❌ 取消]                 │
  └─────────────────────────────────────┘
```

### 2.3 文件系统变化

创建成功后：
```
slirn-standalone/
├── .temp/                          # 临时预览（创建后删除）
│   └── cut_abc12345_lecture.mp4    # 创建后被 move 到 task_dir，删除
├── tasks/
│   └── 20260914-003/               # 新任务
│       ├── metadata.json           # segment.path 指向截取段
│       ├── hotwords.txt
│       ├── raw_input/
│       │   ├── original.mp4 -> 用户原视频
│       │   └── lecture_segment_00-01-00_00-05-00.mp4  ← 从 .temp/ 移过来
│       └── outputs/
```

---

## 3. 目录结构

```
slirn-standalone/
└── tasklib/
    ├── __init__.py             # 加 re-export VideoProcessingError
    ├── exceptions.py           # 加 VideoProcessingError
    ├── video.py                # 新增：get_video_duration / cut_video / probe_video
    └── ... (其他已有)

funclip-main/
└── slirn_home/
    ├── __init__.py
    ├── app.py                  # 加「新建任务」tab 入口
    └── create_task.py          # 新增：UI 构建 + 回调
```

---

## 4. 与 REQ-A/B 的耦合

| REQ-A/B 接口 | REQ-C 在哪用 |
|---|---|
| `TaskManager.create(...)` | `create_task.py:on_create()` 调 |
| `TimeSegment` | 传入 `mgr.create(segment=...)` |
| REQ-B 「新建任务」占位 | REQ-C 改为 `gr.Tabs.select(fn=switch_to_create_tab)` |

---

## 5. 风险与边界

| 风险 | 缓解 |
|---|---|
| ffmpeg/ffprobe 不在 PATH | 启动时检测；缺失时弹明确错误 |
| 截取段文件很大（几百 MB） | stream copy 不重新编码，速度快 |
| 用户取消创建 | 临时文件保留在 .temp/，下次启动可清理 |
| 临时文件泄漏 | `.temp/` 加 .gitignore（已存在）；不清理（用户重启后磁盘可回收） |
| 路径含中文 | ffmpeg 接受，subprocess 用 list 形式（不走 shell） |
| 视频文件被外部删除 | `mgr.create()` 已检查 `original_video.exists()` |

---

## 6. 实现阶段 checklist

按 [docs/sop/03-implementation.md](../sop/03-implementation.md) 走：

**slirn-standalone/tasklib/**：
- [ ] `tasklib/exceptions.py` 加 `VideoProcessingError(TaskError)`
- [ ] `tasklib/video.py`：`get_video_duration()` / `cut_video()` / `probe_video()`
- [ ] `tasklib/__init__.py` re-export
- [ ] `tests/test_video.py` 测试（mock subprocess.run）

**funclip-main/slirn_home/**：
- [ ] `slirn_home/create_task.py`：`build_create_task_tab(mgr)` → gr.Column
- [ ] `slirn_home/app.py`：加「新建任务」tab + 按钮绑定
- [ ] `tests/test_create_task.py` 测试渲染函数

---

## 7. 验证阶段 checklist

按 [docs/sop/05-verification.md](../sop/05-verification.md) 走，逐条验证 REQ-C §4 AC-C.1 ~ AC-C.6 共 30 条。

**预计验证方式**：
- 单元测试：mock subprocess.run 测试 video.py 函数
- 集成测试：用真实小 mp4（项目自带的 sample）测 cut_video 端到端
- UI 测试：build_create_task_tab() 不启动服务器，构造成功即可
- ruff + pytest 双通过

---

## 8. 设计完成声明

- [x] D1-D5 共 5 个决策有"为什么"
- [x] §2 端到端示例覆盖 cut + create + UI
- [x] §3 目录结构明确
- [x] §4 与 REQ-A/B 耦合清晰
- [x] §5 风险已识别
- [x] §6 实现 checklist 可执行
- [x] §7 验证 checklist 对齐 AC

**可以进入实现阶段。**
