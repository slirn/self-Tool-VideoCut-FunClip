# DESIGN-20260914-001-A — 任务数据模型 + 存储（**子 REQ A 设计**）

| 字段 | 值 |
|---|---|
| 编号 | DESIGN-20260914-001-A |
| 日期 | 2026-09-14 |
| 对应 REQ | [REQ-20260914-001-A](../REQM/REQ-20260914-001-A-task-data-model.md) |
| 状态 | ✅ 设计完成（待用户 review） |
| 关联仓库 | slirn-standalone（独立仓库） |

---

## 0. 文档目的

把 REQ-20260914-001-A 里的需求**转化为可落地的技术决策**。每个关键决策说明：
- 选了什么
- 为什么选（vs 排除的方案）
- 怎么实现（代码骨架）

设计完成后，进入实现阶段（写代码 + 自测）。

---

## 1. 决策矩阵（4 个关键决策）

### D1：`Task` 字段类型策略 —— **内存 Path，JSON str，优先相对路径**

#### 选择

| 阶段 | 类型 |
|---|---|
| 内存（`Task` dataclass） | `pathlib.Path` |
| JSON 序列化（`metadata.json`） | `str`（相对仓库根，POSIX 风格 + 平台无关） |
| 读回时 | 自动 detect：相对路径 → 拼仓库根；绝对路径 → 直接用 |

#### 为什么

| 候选 | 取舍 |
|---|---|
| ❌ 全部存 `Path` 对象 | JSON 序列化时 `Path` 被 `str(p)`，但**类型信息丢失**（反序列化时是 str 不是 Path，需要额外还原） |
| ❌ 全部存 `str`（绝对路径） | 简单但跨机器/换盘符就失效 |
| ✅ **混合 + 相对优先** | IDE 类型提示好（`Path`）、跨机器可移植（相对路径）、反序列化简单（str → Path 一行） |

#### 实现骨架

```python
# tasklib/schema.py
from pathlib import Path

def task_to_dict(task: "Task", repo_root: Path) -> dict:
    """序列化：Path → 相对路径 str（相对仓库根）"""
    return {
        "schema_version": "1",
        "task_id": task.task_id,
        "name": task.name,
        "original_video": _to_repo_relative(task.original_video, repo_root),
        "segment": {
            "start": task.segment.start,
            "end": task.segment.end,
            "path": _to_repo_relative(task.segment.path, repo_root) if task.segment else None,
        } if task.segment else None,
        # ...
    }

def _to_repo_relative(p: Path, repo_root: Path) -> str:
    """优先相对路径，跨盘符或逃出仓库时回退到绝对路径"""
    try:
        return str(p.resolve().relative_to(repo_root.resolve())).replace("\\", "/")
    except ValueError:
        return str(p.resolve()).replace("\\", "/")
```

**注意**：JSON 里用 POSIX 风格（`/`），读回时 Windows `Path` 自动接受。

---

### D2：`TimeSegment` 类型 —— **`NamedTuple`**

#### 选择

```python
from typing import NamedTuple

class TimeSegment(NamedTuple):
    start: str    # "HH:MM:SS"
    end: str      # "HH:MM:SS"
```

#### 为什么

| 候选 | 取舍 |
|---|---|
| ❌ `@dataclass` | 默认可变，但时间片段天然不可变（"00:01:00 到 00:05:00"改了就成另一段） |
| ❌ 普通 `tuple` | 不可变但**无字段名**，`seg[0]` 难读 |
| ✅ **NamedTuple** | 不可变 + 字段名访问 + 支持解构 `a, b = seg` + 类型完整 |

#### 副作用

- NamedTuple **默认自带 `__eq__` 和 `__hash__`**，可作 dict key（将来可能用到）
- NamedTuple **不可继承**，但时间片段也不该继承

#### 附加决策：`start/end` 用 `str` 还是 `timedelta`？

**选 `str`**——理由：
- 用户在 UI 上看到的就是 `"00:01:00"`，直接传字符串最直观
- 跨语言读 JSON 时 str 无歧义
- 校验逻辑（"start < end"、"HH:MM:SS 格式"）独立写一个 `parse_time(s: str) -> timedelta` 工具函数

---

### D3：符号链接策略 —— **try os.symlink，失败回退到存路径 + warning**

#### 选择

```python
import logging
from pathlib import Path

log = logging.getLogger(__name__)

def _link_or_record(src: Path, dst: Path) -> Path:
    """尝试创建符号链接 src -> dst。失败时返回 src（用于 metadata 记录）。"""
    try:
        dst.symlink_to(src)
        return dst
    except (OSError, NotImplementedError) as e:
        log.warning(
            "无法创建符号链接 %s -> %s: %s。原视频路径将记录在 metadata 中。",
            dst, src, e,
        )
        return src
```

#### 为什么

| 候选 | 取舍 |
|---|---|
| ❌ 始终只用符号链接 | Windows 上 90% 用户没开开发者模式，失败率高 |
| ❌ 始终只用绝对路径 | 简单但违反 REQ-A.3.5（"符号链接为主"） |
| ❌ 始终尝试符号链接且不警告 | 用户不知道为什么"任务列表里没视频文件" |
| ✅ **try + 失败回退 + warning** | 对用户透明，跨平台行为一致（都能列出任务），失败时给明确提示 |

#### 跨机器共享策略

- 符号链接是相对路径格式 → 跨机器可移植（前提是相对路径解析后两边文件都在）
- 仅绝对路径 → 换机器失效
- metadata 里**两个字段都存**：
  - `original_video_symlink`: 符号链接路径（`raw_input/original.mp4`，相对仓库根）
  - `original_video_source`: 真实路径（用户原视频绝对路径）

读时优先看符号链接是否存在；不存在则用 source 路径。

---

### D4：`task_id` 生成规则 —— **UTC 日期 + 当日序号**（`20260914-001`）

#### 选择

```python
from datetime import datetime, timezone
from pathlib import Path

def next_task_id(tasks_dir: Path) -> str:
    """扫描 tasks/ 目录，取当日最大序号 +1"""
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    prefix = f"{today}-"
    
    max_seq = 0
    if tasks_dir.exists():
        for entry in tasks_dir.iterdir():
            if entry.is_dir() and entry.name.startswith(prefix):
                try:
                    seq = int(entry.name[len(prefix):])
                    max_seq = max(max_seq, seq)
                except ValueError:
                    continue
    
    return f"{prefix}{max_seq + 1:03d}"
```

#### 为什么

| 候选 | 取舍 |
|---|---|
| ❌ UTC 时间戳（`1726350600`） | 紧凑但**不可读**，看不出日期 |
| ❌ UUIDv4（`a3f2b9c1`） | 全局唯一但**不可读、不可排序** |
| ❌ 日期 + UUID 组合 | 既可读又唯一，但 ID 变长 |
| ✅ **日期 + 序号** | 可读（一眼看出哪天）、可排序（字符串排序 ≈ 时间排序）、够用（单日 < 1000 任务） |

#### 碰撞风险

- 单用户场景，无并发 → 序号唯一
- 单日 > 999 个任务时序号变 4 位（`:04d`），但不强制（保持 `:03d`，> 999 时变 `1000` 排序仍然正确）
- **跨日不会撞**（前缀不同）

---

### D5：JSON 字段顺序 —— **显式构造 dict，不用 `asdict`**

#### 选择

```python
def task_to_dict(task: "Task", repo_root: Path) -> dict:
    """显式构造 dict，字段顺序可控"""
    return {
        "schema_version": "1",
        "task_id": task.task_id,
        "name": task.name,
        "original_video_source": str(task.original_video_source),
        "original_video_symlink": str(task.original_video_symlink),
        "segment": (
            None if task.segment is None
            else {
                "start": task.segment.start,
                "end": task.segment.end,
                "path": str(task.segment.path),
            }
        ),
        "hotwords_path": str(task.hotwords_path),
        "status": task.status.value,    # 枚举值字符串，如 "DRAFT"
        "created_at": task.created_at.isoformat().replace("+00:00", "Z"),
        "updated_at": task.updated_at.isoformat().replace("+00:00", "Z"),
    }
```

#### 为什么

| 候选 | 取舍 |
|---|---|
| ❌ `dataclasses.asdict(task)` | 字段顺序 = dataclass 定义顺序，**不可控**（加字段、改顺序都要小心） |
| ❌ 用 cattrs / pydantic | 用户已选"纯 stdlib"，引入新依赖违规 |
| ✅ **显式构造** | 字段顺序显式可控、类型转换集中处理、反序列化对称 |

#### 反序列化对称

```python
def task_from_dict(d: dict, repo_root: Path) -> "Task":
    seg_d = d.get("segment")
    segment = None if seg_d is None else TimeSegment(
        start=seg_d["start"], end=seg_d["end"], path=Path(seg_d["path"]),
    )
    return Task(
        task_id=d["task_id"],
        name=d["name"],
        original_video_source=Path(d["original_video_source"]),
        original_video_symlink=Path(d["original_video_symlink"]),
        segment=segment,
        hotwords_path=Path(d["hotwords_path"]),
        status=TaskStatus(d["status"]),
        created_at=datetime.fromisoformat(d["created_at"].replace("Z", "+00:00")),
        updated_at=datetime.fromisoformat(d["updated_at"].replace("Z", "+00:00")),
    )
```

---

## 2. 端到端示例

### 2.1 用户调用

```python
from pathlib import Path
from tasklib import TaskManager, TimeSegment

mgr = TaskManager(repo_root=Path("D:/Slirn/WorkSpaces/WaytoAGI/ALI/slirn-standalone"))

# 创建任务（无截取）
task = mgr.create(
    name="张老师讲座 - 第一讲",
    original_video=Path("D:/videos/lecture_01.mp4"),
    hotwords=["FunASR", "达摩院"],
)
print(task.task_id)    # "20260914-001"

# 创建任务（带截取）
task2 = mgr.create(
    name="张老师讲座 - 第二讲（剪片）",
    original_video=Path("D:/videos/lecture_02.mp4"),
    segment=TimeSegment(start="00:01:00", end="00:05:00"),
    hotwords=["张老师"],
)
print(task2.task_id)   # "20260914-002"
print(task2.segment.path.name)  # "lecture_02_segment_00-01-00_00-05-00.mp4"

# 列表
for summary in mgr.list():
    print(f"{summary.task_id}: {summary.name} [{summary.status.label}]")

# 更新状态
mgr.update_status("20260914-001", TaskStatus.ASSETS_READY)

# 删除
mgr.delete("20260914-002")
```

### 2.2 文件系统变化

```
slirn-standalone/tasks/
├── 20260914-001/
│   ├── metadata.json
│   ├── hotwords.txt
│   ├── raw_input/
│   │   └── original.mp4 -> D:/videos/lecture_01.mp4
│   └── outputs/
└── 20260914-002/
    ├── metadata.json
    ├── hotwords.txt
    ├── raw_input/
    │   ├── original.mp4 -> D:/videos/lecture_02.mp4
    │   └── lecture_02_segment_00-01-00_00-05-00.mp4
    └── outputs/
```

### 2.3 `metadata.json` 样例

```json
{
  "schema_version": "1",
  "task_id": "20260914-002",
  "name": "张老师讲座 - 第二讲（剪片）",
  "original_video_source": "D:/videos/lecture_02.mp4",
  "original_video_symlink": "tasks/20260914-002/raw_input/original.mp4",
  "segment": {
    "start": "00:01:00",
    "end": "00:05:00",
    "path": "tasks/20260914-002/raw_input/lecture_02_segment_00-01-00_00-05-00.mp4"
  },
  "hotwords_path": "tasks/20260914-002/hotwords.txt",
  "status": "DRAFT",
  "created_at": "2026-09-14T22:35:12Z",
  "updated_at": "2026-09-14T22:35:12Z"
}
```

---

## 3. 目录结构（最终）

```
slirn-standalone/                      # 仓库根
├── docs/
│   ├── 解析过程/                       # 已有
│   └── design/                        # 本 REQ 新建
│       └── DESIGN-20260914-001-A-task-data-model.md
├── tasklib/                           # 本 REQ 新建
│   ├── __init__.py                    # 导出 TaskManager / Task / TimeSegment / TaskStatus
│   ├── models.py                      # dataclass Task + NamedTuple TimeSegment + Enum TaskStatus
│   ├── manager.py                     # TaskManager 类（核心 API）
│   ├── schema.py                      # task_to_dict / task_from_dict
│   ├── time_utils.py                  # parse_time / format_time / validate_segment
│   └── exceptions.py                  # 异常体系
├── tests/                             # 本 REQ 新建
│   ├── __init__.py
│   ├── conftest.py                    # 共享 fixture（tmp_repo_root）
│   ├── test_models.py
│   ├── test_manager.py
│   ├── test_schema.py
│   ├── test_time_utils.py
│   └── test_exceptions.py
├── hotwords/                          # 后续 REQ-D 创建（先空）
├── tasks/                             # 数据目录（运行时生成，git 忽略）
├── material/                          # 已有
├── skill/                             # 已有
├── prompts/                           # 已有
├── .temp/                             # 已有
└── .gitignore                         # 已存在，需追加 tasks/ 和 hotwords/ 等
```

---

## 4. `.gitignore` 更新

`slirn-standalone/.gitignore` 当前缺少任务数据目录的忽略规则。在实现阶段会追加：

```gitignore
# 任务运行时数据（git 忽略）
tasks/
hotwords/

# Python 测试产物
.pytest_cache/
*.pyc
**/__pycache__/

# 编辑器
.idea/
.vscode/
```

---

## 5. 边界与风险

### 5.1 已识别风险

| 风险 | 缓解 |
|---|---|
| 用户原视频被外部删除，符号链接失效 | metadata 里同时存 `original_video_source`，UI 读时优先符号链接，失败则提示"原视频可能已移动" |
| `metadata.json` 写一半断电导致损坏 | 写策略：先写 `metadata.json.tmp`，再 `os.replace()` 原子替换 |
| 跨盘符创建符号链接失败 | D3 已处理：失败时回退 + warning |
| Windows 路径大小写不敏感导致 ID 冲突 | REQ-A.1.1 task_id 用日期 + 数字，天然无大小写问题 |
| 多进程并发 create 撞序号 | 单用户场景无并发，D4 不处理；如未来需要，加 file lock |

### 5.2 不在 REQ-A 范围

- 状态机迁移规则（REQ-A.2.2 留 TODO，**实现里 status 字段不做迁移校验**）
- 任务级热词的"热词去重 / 排序"（简单按行存，不处理）
- 公共热词库（REQ-D）
- UI（REQ-B）
- 视频截取调用（REQ-C）

---

## 6. 实现阶段 checklist

按 [docs/sop/03-implementation.md](../sop/03-implementation.md) 走：

- [ ] `tasklib/exceptions.py` — 5 个异常类
- [ ] `tasklib/time_utils.py` — `parse_time` / `format_time` / `validate_segment`
- [ ] `tasklib/models.py` — `TaskStatus` enum（9 个值）+ `TASK_STATUS_LABEL` 映射 + `TimeSegment` NamedTuple + `Task` dataclass
- [ ] `tasklib/schema.py` — `task_to_dict` / `task_from_dict` + `_to_repo_relative` / `_from_repo_relative`
- [ ] `tasklib/manager.py` — `TaskManager` 类（7 个方法 + 私有 `_atomic_write_json` / `_link_or_record`）
- [ ] `tasklib/__init__.py` — re-export
- [ ] `tests/conftest.py` — `tmp_repo_root` fixture
- [ ] `tests/test_models.py` — dataclass 字段定义 + 枚举完整性
- [ ] `tests/test_manager.py` — 7 个方法的 happy path + 异常路径
- [ ] `tests/test_schema.py` — JSON 序列化往返（**重点测字段顺序**）
- [ ] `tests/test_time_utils.py` — 时间格式校验
- [ ] `tests/test_exceptions.py` — 异常类型继承关系
- [ ] `.gitignore` 追加 `tasks/` / `hotwords/` / `.pytest_cache/` 等
- [ ] `slirn-standalone/` 仓库 commit + push
- [ ] 回 `funclip-main` 更新 submodule 引用 + push

---

## 7. 验证阶段 checklist

按 [docs/sop/05-verification.md](../sop/05-verification.md) 走，逐条验证 REQ-A §4 的 AC-A.1 ~ AC-A.8 共 23 条。

**预计验证方式**：
- 单元测试（pytest）覆盖大部分 AC
- 手工跑一次 §2.1 的端到端示例，验证文件系统变化符合 §2.2
- 检查生成的 `metadata.json` 字段顺序和格式符合 §2.3

---

## 8. 设计完成声明

- [x] D1 ~ D5 共 5 个决策有"为什么"
- [x] §2 端到端示例覆盖 create / list / update_status / delete 全部主流程
- [x] §3 目录结构与代码组织明确
- [x] §5 风险已识别，缓解策略已写
- [x] §6 实现阶段 checklist 可直接执行
- [x] §7 验证阶段 checklist 对齐 REQ AC

**可以进入实现阶段。**
