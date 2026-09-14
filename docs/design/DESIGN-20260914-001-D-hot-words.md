# DESIGN-20260914-001-D — 公共热词库 + 选择 UI

| 字段 | 值 |
|---|---|
| REQ 编号 | REQ-20260914-001-D（子 REQ D） |
| 设计日期 | 2026-09-14 |
| 上游 REQ | [REQ-20260914-001-D](../REQM/REQ-20260914-001-D-hot-words.md) |

---

## 0. 设计目标

实现项目级公共热词库 + 维护 UI + 5×10 网格选择面板，纯文件存储 + Gradio 组件，无新依赖。

---

## 1. 关键设计决策（D1-D5）

### D1：分类扁平结构（一层）

**决定**：仅一层分类（`{category: [words]}`），无嵌套。

**理由**：
- 嵌套分类 UI 复杂（树形控件），Gradio 原生支持差
- 用户已确认"分类也可手动添加"——暗示扁平可扩展
- 减少 80% 的边界情况（拖拽、改父分类、循环引用）

**折中**：分类名支持任意字符串（"讲师"/"专业术语"/"产品名"），用户自行组织。

---

### D2：搜索过滤在客户端

**决定**：每次渲染只显示**当前页的 50 个词**，搜索触发**重新过滤 + 重置到第 1 页**。

**理由**：
- 公共库规模预期 < 1000 词（教学/课程场景）
- 服务端过滤需 round-trip，Gradio 异步回调有延迟
- 客户端过滤 = 内存中 `list comprehension`，毫秒级

**实现**：
- `HotwordLibrary.search(query)` 返回过滤后的 `list[str]`
- UI 用 `gr.Dataframe(value=...)` + `gr.State(filtered_list)` 缓存
- 搜索框 `change` 事件 → 重过滤 + 重渲网格

---

### D3：5×10 网格用 `gr.Dataframe` 而非 50 个 Checkbox

**决定**：网格用 `gr.Dataframe`（pandas-style），每行 5 列，每格显示词文本。
勾选用**点击行选中** + 行号标记高亮。

**理由对比**：

| 方案 | 优点 | 缺点 |
|---|---|---|
| 50 个 `gr.Checkbox` | 勾选状态直观 | 布局在 Gradio 6 中不稳定；50 个组件事件注册性能差 |
| `gr.Dataframe` + `gr.State` 维护选中集合 | 渲染快、布局固定 | 勾选状态需额外 State 同步 |

**最终**：用 `gr.Dataframe` + 选中集合用 `gr.State` 维护。点击行 → State 更新 + 视觉标记（Dataframe `value` 列加 ✓ 后缀）。

**注**：Gradio 6 的 `gr.Dataframe` 支持 `interactive=False` + 自定义 cell 渲染受限，因此用「列加 ✓」的方式做视觉标记而非 checkbox 单元格。

---

### D4：选择面板 → 任务 Textbox 数据流用「追加」

**决定**：选中词**追加**到任务热词 Textbox（不去重不覆盖）。

**理由**：
- 用户可能先手动输了几个词，再从库选 → 必须保留手动输入
- 「去重」交给底层 `TaskManager.create(hotwords=[...])`（已有去重逻辑）

**数据流**：
```
[手动输入 Textbox] + [从库选中 list]
       ↓                    ↓
   合并 list → 去重 → 写入 hotwords.txt
```

---

### D5：存储用 `hotwords/public.txt` + `hotwords/categories.json` 双文件

**决定**：
- `public.txt`：每行一个词（**含分类前缀** `分类:词`，便于人类阅读 + 单一文件）
- `categories.json`：可选索引（`{"默认": [...], "讲师": [...]}`），用于 `list_grouped()` 加速

**理由**：
- 双文件结构符合 KISS：单 txt 文件易读易编辑，json 文件给 API 用
- `public.txt` 是真实数据源，`categories.json` 可由其重建

**最终格式**（修订 D5）：
- **单一数据源**：`hotwords/public.txt`
  ```
  # 分类:词（注释行忽略）
  讲师:张老师
  讲师:达摩院
  专业术语:FunASR
  默认:行业黑话
  ```
- **API 实时解析**：`parse_public_txt() -> dict[str, list[str]]`
- 不维护 `categories.json` —— 避免双文件同步问题

**简化**（**D5 最终**）：只一个 `public.txt` 文件，分类内联在词前，API 解析时按 `:` 拆分第一个。

---

## 2. 数据模型

### 2.1 文件格式

**`hotwords/public.txt`**（UTF-8，每行一条）：
```
# 注释行（以 # 开头，解析时跳过）
讲师:张老师
讲师:达摩院
专业术语:FunASR
专业术语:moviepy
默认:未分类词1
```

格式规则：
- 每行 `<分类>:<词>`，分类与词用 `:` 分隔
- 分类可省略，省略时归到 `默认` 分类
- 注释以 `#` 开头，跳过
- 空行跳过
- 重复词（任意分类下）自动去重

### 2.2 Python 类

```python
class HotwordLibrary:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.txt_path = repo_root / "hotwords" / "public.txt"
        # 不存在则自动创建空文件（含 # header）
        self._ensure_exists()

    def list_all(self) -> list[str]:
        """返回所有词（去重 + 保持插入顺序）。"""

    def list_grouped(self) -> dict[str, list[str]]:
        """返回 {分类: [词]}。"""

    def search(self, query: str) -> list[str]:
        """子串匹配（大小写不敏感）。"""

    def add(self, word: str, category: str = "默认") -> bool:
        """添加词。返回 True 表示新增，False 表示已存在。"""

    def remove(self, word: str) -> bool:
        """删除词。返回 True 表示删除成功，False 表示不存在。"""

    def add_category(self, name: str) -> None:
        """添加分类（实际上只是确保分类存在，无词也可）。"""

    def assign_category(self, word: str, new_category: str) -> bool:
        """修改词的分类。返回 True 表示修改成功。"""

    def _ensure_exists(self) -> None:
        """不存在时创建 hotwords/ 目录 + public.txt 空文件。"""

    def _read_lines(self) -> list[str]:
        """读取所有有效行（去注释、去空、去尾空白）。"""

    def _write_lines(self, lines: list[str]) -> None:
        """原子写入（tmp + replace）。"""
```

---

## 3. 模块结构

### 3.1 slirn-standalone 侧

```
slirn-standalone/tasklib/
├── hotword_lib.py       # NEW — HotwordLibrary 类 + 解析函数
└── __init__.py          # MOD — 导出 HotwordLibrary
```

### 3.2 funclip-main 侧

```
funclip-main/slirn_home/
├── hotword_ui.py        # NEW — 维护 UI + 选择 UI
├── app.py               # MOD — 添加「热词库管理」tab + 「从库选择」入口
└── create_task.py       # MOD — 「从库选择」按钮 + Accordion 面板
```

---

## 4. UI 设计

### 4.1 维护 tab（`build_hotword_library_tab(mgr, hwlib)`）

```
📚 热词库管理
├── 添加新词
│   ├── [词输入框]
│   ├── [分类下拉 / 输入框]
│   └── [➕ 添加] 按钮
├── 添加新分类
│   ├── [分类名输入框]
│   └── [➕ 添加分类] 按钮
└── 现有词（Accordion 按分类分组）
    ├── Accordion: 讲师（3）
    │   ├── 张老师  [分类▼: 讲师]  [🗑️]
    │   ├── 达摩院  [分类▼: 讲师]  [🗑️]
    │   └── ...
    ├── Accordion: 专业术语（5）
    │   └── ...
    └── Accordion: 默认（N）
        └── ...
```

### 4.2 选择面板（在 `create_task.py` 内嵌）

```
➕ 新建任务
├── [视频上传区] ...
├── [时间截取] ...
├── [任务名] ...
├── 🔥 热词
│   ├── [Textbox — 手动输入]
│   ├── [🔥 从公共库选择] 按钮
│   └── Accordion: 选择面板（点击展开）
│       ├── 🔍 [搜索框]
│       ├── [Dataframe 5×10 网格 — 第 1 页]
│       ├── [⬅️ 上一页]  [页码 1/3]  [➡️ 下一页]
│       ├── 状态: 已选 3 个
│       └── [✅ 确认选择] [🗑️ 清空选择]
└── [✅ 创建任务] [❌ 取消]
```

---

## 5. 事件流

### 5.1 维护 tab

```
[添加词] button.click → add_word_handler → Toast "已添加 张老师"
                        ↓
                     hwlib.add()
                        ↓
                     refresh_library_list() → 重新渲染 Accordion
```

### 5.2 选择面板

```
[从库选择] button.click → Accordion 展开

[搜索框] change → search_handler → 过滤 + 重渲网格 + 重置到第 1 页

[网格 row 点击] click → toggle_select_handler → 更新 selected_state + 重渲网格（✓ 标记）

[上一页/下一页] click → page_change_handler → 重渲网格

[确认选择] click → confirm_selection_handler → 合并手动+选中 → 写回 Textbox + 关闭面板
```

---

## 6. 错误处理

| 场景 | 行为 |
|---|---|
| 词为空 | Toast "⚠️ 请输入词" |
| 分类名为空 | 归为"默认" |
| 词已存在 | Toast "⚠️ 张老师 已存在"（不重复添加） |
| 删除不存在的词 | Toast "⚠️ 不存在: foo" |
| 文件 I/O 失败 | Toast "❌ 写文件失败: ..."（异常信息） |
| 文件夹不存在 | 自动创建 `hotwords/` |

---

## 7. 测试策略

### 7.1 slirn-standalone（tasklib/hotword_lib.py）

| 测试 | 覆盖 |
|---|---|
| `test_list_all_empty` | 空文件 |
| `test_list_all_with_words` | 多行解析 |
| `test_list_grouped` | 按分类聚合 |
| `test_search_case_insensitive` | 大小写不敏感 |
| `test_search_no_match` | 无结果 |
| `test_add_new_word` | 新增 |
| `test_add_duplicate_word` | 去重 |
| `test_remove_existing` | 删除成功 |
| `test_remove_nonexistent` | 删除失败 |
| `test_assign_category` | 改分类 |
| `test_persistence_across_instances` | 实例间数据持久 |
| `test_ensure_creates_files` | 自动创建目录/文件 |
| `test_skip_comments_and_blank` | 注释/空行处理 |

### 7.2 funclip-main（slirn_home/hotword_ui.py + create_task.py 修改）

| 测试 | 覆盖 |
|---|---|
| `test_build_hotword_library_tab` | 组件 dict 含必要 key |
| `test_on_add_word_success` | 添加成功 |
| `test_on_add_word_duplicate` | 添加重复 |
| `test_on_remove_word_success` | 删除成功 |
| `test_on_assign_category` | 改分类 |
| `test_on_search_words` | 搜索过滤 |
| `test_on_confirm_selection_appends_to_task` | 追加到 Textbox |
| `test_on_clear_selection` | 清空 |

---

## 8. 数据迁移

本 REQ 是新增功能（`hotwords/` 目录初始不存在），无迁移负担。
若未来要改 `public.txt` 格式：
- 新格式引入 `schema_version` 字段
- `parse_public_txt()` 检测版本并迁移

---

## 9. 设计决策汇总

| ID | 决策 | 一句话 |
|---|---|---|
| D1 | 分类扁平 | 一层 `{分类: [词]}`，无嵌套 |
| D2 | 客户端搜索 | 内存过滤，无 round-trip |
| D3 | Dataframe 网格 | 而非 50 个 Checkbox |
| D4 | 追加数据流 | 不覆盖手动输入，去重在底层 |
| D5 | 单一 public.txt | 分类内联在词前 |

---

## 10. 风险

| 风险 | 缓解 |
|---|---|
| `hotwords/public.txt` 编码错误 | 强制 UTF-8，写入时显式 `encoding="utf-8"` |
| 词中含 `:` 字符 | 解析时 `split(":", 1)`，只拆第一个 |
| 分类名含 `:` 字符 | 不允许（用户约束），或 split 后重组 |
| 大量词时 Dataframe 渲染慢 | 分页每页 50 个 |
| 文件并发写 | tasklib 已有 `_atomic_write_json`，同样模式用 `.tmp` + `os.replace` |

---

## 11. 下一阶段

进入实现 + 评审 + 验证（[VERIFICATION-20260914-001-D-hot-words.md](../../verification/VERIFICATION-20260914-001-D-hot-words.md)）。
