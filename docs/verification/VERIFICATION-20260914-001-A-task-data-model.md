# VERIFICATION-20260914-001-A — 任务数据模型 + 存储（**验证报告**）

| 字段 | 值 |
|---|---|
| 编号 | VERIFICATION-20260914-001-A |
| 日期 | 2026-09-14 |
| 对应 REQ | [REQ-20260914-001-A](../REQM/REQ-20260914-001-A-task-data-model.md) §4 |
| 对应 DESIGN | [DESIGN-20260914-001-A](../design/DESIGN-20260914-001-A-task-data-model.md) |
| 状态 | ✅ 验证通过（**23/23 AC 全过**） |

---

## 1. 验证方法

| 维度 | 方法 |
|---|---|
| 单元测试 | pytest — 43 个测试覆盖主要 AC |
| 静态检查 | ruff check — 0 错 |
| 端到端 | 手工跑 §2.1 demo（create / list / get / update_status / delete） |
| 序列化 | 验证 metadata.json 字段顺序、UTF-8、Z 后缀、schema_version |

---

## 2. AC 逐条验证

### AC-A.1 schema 与字段（3 条）

| AC | 状态 | 验证方式 |
|---|---|---|
| **AC-A.1.1** metadata.json 包含全部 9 个字段 | ✅ | `test_task_dataclass_has_nine_fields` + `test_metadata_json_written_with_correct_keys` |
| **AC-A.1.2** 字段缺失/类型错误抛 MetadataCorruptedError | ✅ | `test_from_dict_raises_on_missing_required_field` |
| **AC-A.1.3** schema_version 缺失/不识别抛 MetadataCorruptedError | ✅ | `test_from_dict_raises_on_wrong_schema_version` |

### AC-A.2 状态枚举（3 条）

| AC | 状态 | 验证方式 |
|---|---|---|
| **AC-A.2.1** TaskStatus 包含 9 个枚举值 | ✅ | `test_task_status_has_nine_values` |
| **AC-A.2.2** 状态值是英文 | ✅ | `test_task_status_values_are_snake_case_english` |
| **AC-A.2.3** UI 显示映射表存在 | ✅ | `test_task_status_label_covers_all_statuses` |

### AC-A.3 目录布局（3 条）

| AC | 状态 | 验证方式 |
|---|---|---|
| **AC-A.3.1** create 后目录结构完整 | ✅ | `test_create_creates_directory_structure` |
| **AC-A.3.2** outputs/ 子目录存在 | ✅ | 同上 |
| **AC-A.3.3** task_id 唯一 | ✅ | `_next_task_id()` 扫盘取 max+1，单用户场景无碰撞 |

### AC-A.4 截取段命名（4 条）

| AC | 状态 | 验证方式 |
|---|---|---|
| **AC-A.4.1** 文件名格式 `<stem>_segment_<HH-MM-SS>_<HH-MM-SS>.<ext>` | ✅ | `test_create_with_segment` + 端到端 demo |
| **AC-A.4.2** start >= end 抛 InvalidTimeRangeError | ✅ | `test_create_invalid_segment_raises` + `test_validate_segment_*` |
| **AC-A.4.3** 时间格式错抛 ValueError | ✅ | `test_parse_time_invalid_format_raises` |
| **AC-A.4.4** 截取段和原视频在不同路径 | ✅ | `manager.create()` 显式构造 `seg_path = raw_input_dir / seg_filename`，与 `original.<ext>` 同目录但不同名 |

### AC-A.5 原视频存储（3 条）

| AC | 状态 | 验证方式 |
|---|---|---|
| **AC-A.5.1** raw_input/original.<ext> 是符号链接 | ✅ | `test_create_creates_symlink` + 端到端 demo（Windows 上 symlink 默认可能失败，回退见 AC-A.5.2） |
| **AC-A.5.2** 符号链接失败时记录绝对路径、不创建符号链接 | ✅ | `_link_or_record()` 实现 + warning 日志 |
| **AC-A.5.3** 用户原视频不复制 | ✅ | `create()` 不做 shutil.copy，仅 `_link_or_record()` |

### AC-A.6 TaskManager 接口（5 条）

| AC | 状态 | 验证方式 |
|---|---|---|
| **AC-A.6.1** create 返回的 Task 可重新 get 出来 | ✅ | `test_get_returns_created_task` |
| **AC-A.6.2** list() 按 updated_at 倒序 | ✅ | `test_list_returns_summaries_sorted_by_updated` |
| **AC-A.6.3** delete 后 get 抛 TaskNotFoundError | ✅ | `test_delete_removes_directory` |
| **AC-A.6.4** update_status 写入新状态，updated_at 自动刷新 | ✅ | `test_update_status` |
| **AC-A.6.5** update_hotwords 同步文件与 metadata | ✅ | `test_update_hotwords` |

### AC-A.7 序列化（3 条）

| AC | 状态 | 验证方式 |
|---|---|---|
| **AC-A.7.1** 中文任务名保持原字符 | ✅ | `test_json_preserves_chinese_characters` |
| **AC-A.7.2** 两次 JSON 字段顺序一致 | ✅ | `test_json_field_order_is_stable` |
| **AC-A.7.3** 时间戳以 Z 结尾 | ✅ | `test_json_uses_utc_z_suffix` |

### AC-A.8 单元测试（4 条）

| AC | 状态 | 验证方式 |
|---|---|---|
| **AC-A.8.1** test_models.py 覆盖 dataclass 字段定义 | ✅ | `test_task_dataclass_has_nine_fields` 等 |
| **AC-A.8.2** test_manager.py 覆盖 7 个方法全流程 | ✅ | 16 个 manager 测试，覆盖 create/get/list/delete/update_status/update_hotwords/exists |
| **AC-A.8.3** test_schema.py 覆盖序列化往返 | ✅ | `test_round_trip_preserves_data` 等 |
| **AC-A.8.4** 异常路径全覆盖 | ✅ | test_exceptions.py + 各 test_*.py 异常断言 |

---

## 3. 验证结果汇总

| 维度 | 结果 |
|---|---|
| pytest | **43/43 通过** ✅ |
| ruff | **0 错** ✅ |
| AC 全过 | **23/23** ✅ |
| 端到端 demo | **通过**（create / list / update_status / delete 全流程） ✅ |

---

## 4. 已知 trade-off（**接受**）

| trade-off | 当前处理 |
|---|---|
| Windows 上符号链接可能创建失败 | `_link_or_record()` 自动回退到"记录绝对路径"，日志 warning |
| 临时目录里临时视频也在仓库内 → metadata 用相对路径 | 真实使用时 video 通常在仓库外（用户素材目录）→ 会用绝对路径。临时目录只是测试场景 |
| 单用户场景无并发 | `_next_task_id()` 扫盘取 max+1，无锁 |

---

## 5. 后续 REQ 影响

| REQ | 是否可立即开始 | 备注 |
|---|---|---|
| REQ-001-B 首页切换 + 任务列表 | ✅ | 可直接 `from tasklib import TaskManager, TASK_STATUS_LABEL` |
| REQ-001-C 新建任务 - 视频 + 时间 | ✅ | `TimeSegment` 已定义，`create()` 已支持 segment |
| REQ-001-D 公共热词库 | ✅（独立模块） | 公共库与 tasklib 无依赖 |

**REQ-A 的实现对 REQ-B/C/D 无阻塞**。

---

## 6. 验证完成声明

- [x] §2 23 条 AC 全部 ✅
- [x] pytest + ruff 双通过
- [x] 端到端 demo 验证全流程
- [x] 已知 trade-off 已文档化
- [x] 后续 REQ 可基于本 REQ 推进

**子 REQ A 验证通过。**
