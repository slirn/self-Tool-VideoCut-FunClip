# REQ-20260914-001-A — 任务数据模型 + 存储（**子 REQ A**）

| 字段 | 值 |
|---|---|
| 编号 | REQ-20260914-001-A（子 REQ，**A 必须先做**） |
| 日期 | 2026-09-14 |
| 优先级 | P0（基础设施，B/C/D 依赖） |
| 状态 | 📝 草案（基于 Epic §5/§11 + 已确认决策） |
| 上位 Epic | [REQ-20260914-001](REQ-20260914-001-custom-home-task.md) |
| 关联仓库 | slirn-standalone（独立仓库，与 funclip-main 是 submodule 关系） |
| 代码归属 | `slirn-standalone/tasklib/` |
| 数据归属 | `slirn-standalone/tasks/<task_id>/` |
| 范围 | 任务元数据 schema、目录布局、JSON 序列化、TaskManager 类 |

---

## 0. 存储原则（**钉死的总原则**）

> **本项目不引入任何数据库**（MySQL / SQLite / MongoDB / Redis / 任何 ORM / 任何 KV 存储）。所有持久化数据 = **本地文件**（JSON / 纯文本 / 视频文件 / 符号链接）。

### 0.1 各种数据用什么文件

| 数据类型 | 存储形式 | 物理路径 |
|---|---|---|
| 任务元数据 | JSON 文件 | `tasks/<task_id>/metadata.json` |
| 任务级热词 | 纯文本 | `tasks/<task_id>/hotwords.txt` |
| 原视频 | **符号链接**（不复制） | `tasks/<task_id>/raw_input/original.<ext>` |
| 截取段视频 | 实际视频文件 | `tasks/<task_id>/raw_input/<stem>_segment_*.mp4` |
| 公共热词 | 纯文本 | `hotwords/public.txt` |
| 公共热词分类 | JSON 文件 | `hotwords/categories.json`（**REQ-D** 范围） |
| 后续剪辑产物 | 视频/SRT/JSON | `tasks/<task_id>/outputs/...`（**后续 REQ** 范围） |

### 0.2 为什么不用数据库

1. **Epic §6 非目标**：单机本地、单用户、无分布式
2. **数据量小**：估算任务数 < 100，单任务元数据 < 1 KB，全文件 grep/glob 够用
3. **备份简单**：`tar -czf backup.tar.gz tasks/ hotwords/` 一行命令
4. **版本控制友好**：JSON 是纯文本，diff 友好
5. **零运维**：不需要 DB server、不需要 schema migration 工具
6. **跨平台**：Windows/Mac/Linux 一致

### 0.3 已知 trade-off（**接受，不优化**）

| trade-off | 当前处理 |
|---|---|
| >1 万任务时 list() 慢 | 不优化（Epic 范围内用不到） |
| 无并发写保护 | 单用户，无需考虑 |
| 写文件半路失败可能留坏文件 | 用"写 `.tmp` 再 `os.replace()`"原子模式（**DESIGN-A 阶段定**） |
| 无事务 | 用"先备份原文件再覆盖"模式（**DESIGN-A 阶段定**） |

### 0.4 决策不可逆声明

如果将来要引入数据库，**整个 Epic 的存储层要重新设计**，成本极高。所以这条原则从一开始就要钉死，避免后续子 REQ 出现动摇。

---

---

## 1. 背景与目标

**Epic 大局**：为"自定义首页与任务管理"建立任务驱动工作流的基础数据层。

**子 REQ A 职责**：把"任务是什么、存在哪里、怎么读写"定义清楚，并提供一个可被 REQ-B（首页 UI）、REQ-C（视频+时间）、REQ-D（热词）三个子 REQ 共同调用的 Python 库。

**价值**：REQ-B/C/D 三个 UI 子模块可以直接 `from tasklib import TaskManager` 使用，不需要各自实现一遍"任务存在哪里、长啥样"。

---

## 2. 用户故事（来自 Epic §2，对应 A 的部分）

### US-A1：保存任务

- **角色**：项目使用者（或后续 REQ-B 的 UI）
- **场景**：用户已经决定开一个新剪辑任务（视频选了、热词选了），提交
- **操作**：调用 `TaskManager.create(...)`，传入必要信息
- **结果**：在 `tasks/<task_id>/` 下生成完整目录结构 + 写入 `metadata.json`

### US-A2：浏览历史

- **角色**：同上
- **场景**：用户进入自定义首页
- **操作**：调用 `TaskManager.list_tasks()` / `get_task(id)`
- **结果**：拿到所有任务的摘要列表，可用于渲染任务列表

### US-A3：删除任务

- **角色**：同上
- **场景**：用户决定放弃某个任务
- **操作**：调用 `TaskManager.delete(id)`
- **结果**：`tasks/<task_id>/` 整个目录被删除（含所有产物）

### US-A4：更新任务

- **角色**：同上
- **场景**：任务进度推进（如从 `素材就绪` → `字幕生成中`）
- **操作**：调用 `TaskManager.update_status(id, new_status)`
- **结果**：`metadata.json` 中的 status 字段被更新

---

## 3. 核心需求（**仅 A 的范围**，从 Epic §3 抽取）

### 3.1 任务元数据 schema

| ID | 需求 |
|---|---|
| REQ-A.1.1 | 任务有 **唯一 ID**（`task_id`），格式：`<YYYYMMDD>-<NNN>` 或 UUIDv4 短串 |
| REQ-A.1.2 | 任务有 **名字**（用户可改，默认从视频文件名生成） |
| REQ-A.1.3 | 任务记录 **原始视频**（绝对路径或相对仓库根的路径，不复制原文件） |
| REQ-A.1.4 | 任务记录 **截取段**（如有）：路径 + 起止时间戳 |
| REQ-A.1.5 | 任务记录 **任务级热词**（路径指向 `<task_dir>/hotwords.txt`） |
| REQ-A.1.6 | 任务记录 **状态**（见 §3.2 状态枚举） |
| REQ-A.1.7 | 任务记录 **创建时间**（ISO 8601 UTC） |
| REQ-A.1.8 | 任务记录 **最后修改时间**（ISO 8601 UTC） |
| REQ-A.1.9 | 任务记录 **schema 版本号**（`schema_version`，便于未来迁移） |

### 3.2 状态枚举（**全部定义，后续 REQ 用**）

| ID | 需求 |
|---|---|
| REQ-A.2.1 | 状态枚举 `TaskStatus` 包含 Epic §11.1 Q5 列出的全部取值 |
| REQ-A.2.2 | 状态机迁移规则**留 TODO**：本 REQ 只暴露枚举值，不实现迁移约束 |
| REQ-A.2.3 | 状态值用**英文 snake_case**（便于代码引用），UI 显示中文标签 |

**完整状态值**（来自 Epic §5 Q5 答案）：
```
DRAFT                    # 草稿
ASSETS_READY             # 已准备素材
SUBTITLE_GENERATED       # 已生成字幕
SUBTITLE_REVIEWED        # 已分析修改字幕
ROUGH_CUT_DONE           # 粗剪完成
FINE_SUBTITLE_DONE       # 精剪字幕
FINE_SUBTITLE_REVIEWED   # 精剪修改
FINE_CUT_DONE            # 精剪完成
MUXED                    # 视频字幕合成
```

### 3.3 目录布局

```
slirn-standalone/                      # 仓库根
├── tasks/                             # 所有任务根目录
│   ├── 20260914-001/                  # task_id
│   │   ├── metadata.json              # 任务元数据（必需）
│   │   ├── hotwords.txt               # 任务级热词（可空）
│   │   ├── raw_input/                 # 原始视频 + 截取段
│   │   │   ├── original.mp4           # 用户选的原视频的**符号链接**或**复制**（见 REQ-A.3.5）
│   │   │   └── original_segment_00-01-00_00-05-00.mp4   # 截取段
│   │   └── outputs/                   # 后续子 REQ 写入的产物（粗剪、精剪、合成等）
│   └── 20260914-002/
│       └── ...
└── tasklib/                           # 本 REQ 的代码
    ├── __init__.py
    ├── models.py                      # dataclass 定义
    ├── manager.py                     # TaskManager 类
    ├── schema.py                      # JSON 序列化 + 版本号
    └── exceptions.py
```

### 3.4 截取段命名规则（来自 Epic §11.1 Q8）

| ID | 需求 |
|---|---|
| REQ-A.4.1 | 截取段文件名：`<原视频 stem>_segment_<HH-MM-SS>_<HH-MM-SS>.<ext>` |
| REQ-A.4.2 | 时间格式：**HH-MM-SS**（用 `-` 不用 `:`，避免 Windows 文件名兼容性问题） |
| REQ-A.4.3 | 起始时间早于结束时间，否则抛 `InvalidTimeRangeError` |
| REQ-A.4.4 | 截取段**单独存储**于 `<task_dir>/raw_input/`（与原视频在不同子目录） |
| REQ-A.4.5 | 不存在"跳过截取"逻辑：是否截取由 REQ-C UI 决定，A 只负责"给定一段 → 命名 + 入库" |

### 3.5 原视频的存储策略

| ID | 需求 |
|---|---|
| REQ-A.5.1 | 用户选的原视频**不复制**到任务目录（节省磁盘） |
| REQ-A.5.2 | 任务目录 `raw_input/` 下生成 `original.<ext>` 符号链接指向用户原视频 |
| REQ-A.5.3 | Windows 上 symlink 受限，**回退方案**：记录原视频绝对路径在 metadata.json，不创建符号链接 |
| REQ-A.5.4 | 读取符号链接失败时抛明确异常（含原视频路径 + 当前路径） |

### 3.6 TaskManager 接口

```python
class TaskManager:
    def __init__(self, repo_root: Path):
        """repo_root = slirn-standalone/ 仓库根"""

    def create(
        self,
        name: str,
        original_video: Path,
        segment: TimeSegment | None = None,
        hotwords: list[str] | None = None,
        status: TaskStatus = TaskStatus.DRAFT,
    ) -> Task:
        """新建任务。生成 task_id、目录、metadata.json、符号链接/路径记录。"""

    def get(self, task_id: str) -> Task:
        """按 ID 读取任务。不存在时抛 TaskNotFoundError。"""

    def list(self) -> list[TaskSummary]:
        """列出所有任务，按最后修改时间倒序。每个返回摘要（不含完整热词列表）。"""

    def delete(self, task_id: str) -> None:
        """删除整个任务目录。不存在时抛 TaskNotFoundError。"""

    def update_status(self, task_id: str, new_status: TaskStatus) -> None:
        """更新状态。**不做迁移约束**（REQ-A.2.2 留 TODO）。"""

    def update_hotwords(self, task_id: str, hotwords: list[str]) -> None:
        """更新任务级热词文件 + metadata.json 引用。"""

    def exists(self, task_id: str) -> bool: ...
```

### 3.7 JSON 序列化

| ID | 需求 |
|---|---|
| REQ-A.7.1 | `metadata.json` 用 UTF-8 编码，2 空格缩进，`ensure_ascii=False`（中文友好） |
| REQ-A.7.2 | 字段顺序：固定（schema_version → task_id → name → ...），便于 git diff 阅读 |
| REQ-A.7.3 | 时间戳统一 ISO 8601 UTC 字符串（如 `2026-09-14T22:30:00Z`） |
| REQ-A.7.4 | 枚举值序列化用**英文值**（如 `"DRAFT"`），不是中文 |
| REQ-A.7.5 | schema_version 当前固定为 `"1"`，未来 schema 变更时升到 `"2"` 并写迁移脚本 |

### 3.8 异常体系

```python
class TaskError(Exception): pass
class TaskNotFoundError(TaskError): pass
class DuplicateTaskError(TaskError): pass
class InvalidTimeRangeError(TaskError): pass
class MetadataCorruptedError(TaskError): pass
```

---

## 4. 验收标准

### AC-A.1 schema 与字段

- [ ] **AC-A.1.1** `metadata.json` 包含全部 §3.1 列出的 9 个字段
- [ ] **AC-A.1.2** 字段缺失/类型错误时 `TaskManager.get()` 抛 `MetadataCorruptedError`
- [ ] **AC-A.1.3** `schema_version` 缺失时抛 `MetadataCorruptedError`

### AC-A.2 状态枚举

- [ ] **AC-A.2.1** `TaskStatus` 包含 9 个枚举值（与 §3.2 一致）
- [ ] **AC-A.2.2** 状态值是英文 snake_case（`DRAFT`、`ASSETS_READY` ...）
- [ ] **AC-A.2.3** UI 显示映射表存在（`TASK_STATUS_LABEL: dict[TaskStatus, str]`）

### AC-A.3 目录布局

- [ ] **AC-A.3.1** `TaskManager.create()` 后 `tasks/<id>/{metadata.json, raw_input/, hotwords.txt}` 全部存在
- [ ] **AC-A.3.2** `tasks/<id>/outputs/` 子目录也存在（即使为空）
- [ ] **AC-A.3.3** task_id 唯一；重复创建抛 `DuplicateTaskError`

### AC-A.4 截取段命名

- [ ] **AC-A.4.1** `create(segment=TimeSegment(start="00:01:00", end="00:05:00"), ...)` 生成 `<stem>_segment_00-01-00_00-05-00.<ext>`
- [ ] **AC-A.4.2** `start >= end` 抛 `InvalidTimeRangeError`
- [ ] **AC-A.4.3** 时间格式错（不是 HH:MM:SS）抛 `ValueError`
- [ ] **AC-A.4.4** 截取段**不会**和原视频在同一路径（原视频在 `raw_input/original.<ext>`，截取段在 `raw_input/<stem>_segment_*.mp4`）

### AC-A.5 原视频存储

- [ ] **AC-A.5.1** `raw_input/original.<ext>` 是符号链接，指向用户提供的视频绝对路径
- [ ] **AC-A.5.2** 符号链接创建失败（如权限不足）时，记录原视频绝对路径到 `metadata.json.original_video` 字段，不创建符号链接
- [ ] **AC-A.5.3** 用户原视频**不**被复制到 `tasks/<id>/`

### AC-A.6 TaskManager 接口

- [ ] **AC-A.6.1** `create` 返回的 `Task` 对象可重新 `get(id)` 出来（往返一致）
- [ ] **AC-A.6.2** `list()` 按 `updated_at` 倒序
- [ ] **AC-A.6.3** `delete` 后 `get(id)` 抛 `TaskNotFoundError`
- [ ] **AC-A.6.4** `update_status` 写入新状态，`updated_at` 自动刷新
- [ ] **AC-A.6.5** `update_hotwords` 同步更新 `hotwords.txt` 与 `metadata.json.hotwords_path`

### AC-A.7 序列化

- [ ] **AC-A.7.1** 中文任务名（`"剪辑 - 张老师讲座"`）在 JSON 中保持原字符（不被转义为 `\u...`）
- [ ] **AC-A.7.2** 两次 `create` 生成的 JSON 字段顺序完全一致（git diff 友好）
- [ ] **AC-A.7.3** 时间戳以 `Z` 结尾（UTC）

### AC-A.8 单元测试

- [ ] **AC-A.8.1** `tests/test_models.py` 覆盖 dataclass 字段定义
- [ ] **AC-A.8.2** `tests/test_manager.py` 覆盖 create/get/list/delete/update_status/update_hotwords 全流程
- [ ] **AC-A.8.3** `tests/test_schema.py` 覆盖 JSON 序列化往返（写入 → 读取 → 比较）
- [ ] **AC-A.8.4** 异常路径全覆盖（重复创建、ID 不存在、metadata 损坏、时间格式错）

---

## 5. 非目标（**本 REQ 不做**）

- ❌ 状态机迁移规则（REQ-A.2.2）
- ❌ 任务进度实时监控（Epic §6）
- ❌ 视频转码/截取（REQ-C 范围）
- ❌ 热词库读取/写入（REQ-D 范围）
- ❌ 任何 Gradio UI（REQ-B 范围）
- ❌ 数据备份/恢复（Epic §6 非目标）

---

## 6. 技术约束

- **语言**：Python 3.10+（与 funclip 一致）
- **依赖**：**仅 stdlib**（`dataclasses`、`json`、`pathlib`、`enum`、`datetime`、`uuid`）
- **代码组织**：放在 `slirn-standalone/tasklib/`，**不创建 Python 包**（仓库没有 setup.py）
- **导入方式**：调用方 `sys.path.insert(0, REPO_ROOT)` 后 `from tasklib import TaskManager`
- **路径处理**：所有路径用 `pathlib.Path`，跨平台（Windows/Unix）
- **时区**：所有时间用 UTC，存 ISO 8601 字符串
- **与 funclip-main 的关系**：funclip-main 不引入 tasklib（它是 slirn-standalone 内部模块）；Gradio UI 在 funclip-main 这边启动，但通过 subprocess 调 slirn-standalone 的脚本（**这层在 REQ-B 设计时定**）

---

## 7. 反例（**不该做的设计**）

- ❌ 把任务存数据库（Epic §8 反例）
- ❌ 用 pickle 序列化（不可读、版本不友好）
- ❌ 引入 Pydantic（违反用户选的"纯 stdlib"）
- ❌ 把原视频**复制**到任务目录（违反 §3.5，浪费磁盘）
- ❌ 截取段和原视频放同一目录（违反 Epic §8 反例）
- ❌ 状态机迁移规则在本 REQ 实现（留给后续 REQ，避免范围爆炸）
- ❌ 把 TaskManager 写成 Gradio 依赖（违反"分层"，UI 在 REQ-B）
- ❌ 在 funclip-main 仓库根复制一份 tasklib（违反"代码归属在 slirn-standalone"）

---

## 8. 已确认的 Epic 级决策（**沿用，不在本 REQ 重新决定**）

| 决策点 | 来源 | 值 |
|---|---|---|
| 实现语言/框架 | 用户答 | 纯 Python + dataclass + JSON |
| 代码归属 | 用户答 | slirn-standalone |
| 状态枚举范围 | 用户答 | 全 9 个状态先定义，后续 REQ 用 |
| 路径前缀 | 用户答 | 去掉 `slirn/`，用仓库根相对 |
| 数据存储 | Epic §11.4 Q9 | JSON 元数据 + 视频文件 + 热词 txt |
| 截取段命名 | Epic §11.1 Q8 | `<原视频名>_segment_<HH-MM-SS>_<HH-MM-SS>.<ext>` |

---

## 9. 与上游 funclip 的关系

| 上游概念 | 在 A 里的处理 |
|---|---|
| FunASR 输出 SRT | **不读** SRT（这是 skill video-subtitle-extractor 的事） |
| moviepy / ffmpeg | **不调用**（REQ-C 范围） |
| 上游 Gradio launch.py | **不修改**（REQ-B 范围） |
| 上游的 `funasr` 模型依赖 | **不引入**（保持 tasklib 零依赖） |

---

## 10. 下一阶段（**子 REQ A 内部**）

进入 [DESIGN-20260914-001-A-task-data-model.md](../design/DESIGN-20260914-001-A-task-data-model.md) 系统设计。

**关键设计问题**（提前透露，**共 4 个**，文档说"5 个"是笔误）：
- `Task` dataclass 的具体字段类型（`Path` vs `str`？相对/绝对？）
- `TimeSegment` 是 dataclass 还是 NamedTuple？
- 符号链接 vs 绝对路径记录的判断逻辑
- `task_id` 生成规则（用 UTC 时间 + 序号 vs UUID）
- `metadata.json` 字段顺序在 Python dict 中的实现方式（`dict[str, Any]` + 显式构造 vs `dataclasses.asdict` + 重排）

---

## 11. 出口条件（**REQM 阶段通过标准**）

- [ ] §3 每条需求都有可验证的验收标准（§4）
- [ ] §5 非目标明确，无歧义
- [ ] §6 技术约束与用户已确认决策一致
- [ ] §7 反例列举 ≥ 3 条
- [ ] 用户 review 通过（或显式说"用草案"）

---

## 12. 子 REQ A 启动检查清单（来自 Epic §12）

- [x] REQ-A 涉及的 Q（Q5、Q6、Q7、Q8）已回答（见 §8）
- [x] REQ-A 无依赖（A 是基础）
- [x] REQ-A 文档已创建（本文）
- [x] REQ-A 文档列出**该子 REQ 范围内的需求**（§3，共 27 条编号需求）
- [x] REQ-A 文档的验收标准**可独立验证**（§4，AC-A.1 ~ AC-A.8 共 23 条）
