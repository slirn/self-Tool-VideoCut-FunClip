# DESIGN-20260914-001-B — 首页切换 + 任务列表 UI（**设计**）

| 字段 | 值 |
|---|---|
| 编号 | DESIGN-20260914-001-B |
| 日期 | 2026-09-14 |
| 对应 REQ | [REQ-20260914-001-B](../REQM/REQ-20260914-001-B-home-and-list.md) |
| 状态 | ✅ 设计完成 |
| 关联仓库 | funclip-main（UI 代码） + slirn-standalone（tasklib 依赖） |

---

## 1. 决策矩阵（5 个关键决策）

### D1：运行时 tab 切换机制 —— **新标签页 + URL 链接**

#### 选择

slirn 首页顶部的 tab **不是 Gradio 内置 tab**，而是用 `gr.HTML` 或 `gr.Markdown` 渲染两个 `<a target="_blank">` 链接，分别指向：
- `#tasks`（任务列表区，本页）
- `<上游 Gradio URL>`（新标签页打开上游首页，通常 `http://localhost:7860/`）

#### 为什么

| 候选 | 取舍 |
|---|---|
| ❌ Gradio 内置 `gr.Tabs` | 不能跨 Gradio Blocks 切换；上游首页是独立的 Gradio 实例 |
| ❌ iframe 嵌入上游首页 | 跨域/端口冲突；上游首页需要单独启动 |
| ✅ **新标签页 + HTML 链接** | 简单、跨实例、用户可控（可关掉上游标签页） |

#### 实现

```python
gr.Markdown(
    "[📋 任务列表](#tasks) | "
    "[🚀 上游首页](http://localhost:7860/){target='_blank'}"
)
```

**注意**：上游 Gradio 默认启动在 7860 端口；如果 slirn 首页也启动在 7860，会冲突。**D6：slirn 首页启动在 7861，上游首页启动在 7860**（详见 D6）。

---

### D2：列表是否要手动刷新按钮 —— **要，手动刷新按钮**

#### 选择

任务列表渲染时拉取一次，提供「🔄 刷新」按钮重新拉取。

#### 为什么

| 候选 | 取舍 |
|---|---|
| ❌ 自动 polling（如每 5s） | 违反 Epic §6「不做实时监控」 |
| ❌ 不刷新（用户重启应用才看到新任务） | 不友好 |
| ✅ **手动刷新按钮** | 用户按需；不引入后台任务 |

#### 实现

```python
refresh_btn = gr.Button("🔄 刷新")
refresh_btn.click(fn=lambda: render_task_list(mgr), outputs=[task_list])
```

---

### D3：任务列表渲染 —— **gr.Dataframe（不可编辑）**

#### 选择

用 `gr.Dataframe(value=..., headers=["任务名", "视频", "状态", "创建", "修改"], interactive=False)`。

#### 为什么

| 候选 | 取舍 |
|---|---|
| ❌ `gr.Column` + `gr.Row` 自定义 | 工作量大、样式难统一 |
| ❌ `gr.Table` | Gradio 4.x 推荐用 Dataframe |
| ✅ **`gr.Dataframe(interactive=False)`** | Gradio 4.x 原生、样式统一、列固定 |

#### 实现细节

```python
def render_task_list(mgr: TaskManager) -> list[list]:
    """渲染为 Dataframe 接受的 list-of-lists 格式。"""
    rows = []
    for s in mgr.list():
        rows.append([
            s.name,
            s.original_video,
            TASK_STATUS_LABEL[s.status],
            s.created_at.strftime("%Y-%m-%d %H:%M"),
            s.updated_at.strftime("%Y-%m-%d %H:%M"),
        ])
    return rows
```

---

### D4：「查看详情」的模态框 —— **gr.Accordion（折叠面板）**

#### 选择

点击「查看详情」按钮 → 展开该任务下方的 `gr.Accordion`，显示完整元数据。

#### 为什么

| 候选 | 取舍 |
|---|---|
| ❌ Gradio `gr.Modal` | Gradio 4.x 无原生 Modal；可用 `gr.update(visible=...)` 但不优雅 |
| ❌ 弹窗（HTML/JS） | 跨 Gradio Blocks 复杂 |
| ✅ **`gr.Accordion`** | Gradio 原生、可折叠、不离开页面 |

#### 实现

```python
with gr.Accordion("任务详情", visible=False) as detail_acc:
    detail_md = gr.Markdown()
view_btn.click(fn=show_detail, inputs=[task_id_state], outputs=[detail_acc, detail_md])
```

---

### D5：删除确认 —— **gr.Button 双步确认（按钮文字变化）**

#### 选择

点「删除」按钮 → 按钮文字变成「⚠️ 确认删除？」+ 出现「取消」按钮 → 再点「确认删除」才执行。

#### 为什么

| 候选 | 取舍 |
|---|---|
| ❌ 弹窗（HTML/JS） | 复杂 |
| ❌ `gr.Confirm`（Gradio 3.x 风格） | Gradio 4.x 不再推荐 |
| ✅ **按钮文字变化 + 二次点击** | 纯 Gradio 原生、用户明显感知 |

#### 实现

```python
delete_btn.click(fn=arm_delete, outputs=[delete_btn, cancel_btn])
@gr.on(triggers=[delete_btn.click], inputs=[task_id_state])
def confirm_delete(task_id):
    mgr.delete(task_id)
    return "已删除"
```

---

### D6：端口策略 —— **slirn 首页 7861，上游首页 7860**

#### 选择

- `python -m funclip.launch --home slirn` → 启动在 7861
- `python -m funclip.launch --home original` → 启动在 7860（默认行为，不变）

#### 为什么

| 候选 | 取舍 |
|---|---|
| ❌ 都用 7860 | 端口冲突；同一时间只能跑一个 |
| ✅ **分离端口** | 可同时跑（用户能同时体验两个），D1 链接指向明确 |

#### 启动脚本

```python
# funclip/launch.py 新增分支
if args.home == "slirn":
    from slirn_home import build_app
    app = build_app(repo_root)
    app.launch(server_port=7861, server_name="127.0.0.1")
else:  # original
    # ... 上游 launch() 原有逻辑
    launch(...)  # 默认 7860
```

---

## 2. 端到端示例

### 2.1 用户调用

```bash
# 启动 slirn 自定义首页（端口 7861）
python -m funclip.launch --home slirn

# 另开终端启动上游 Gradio（端口 7860，可选）
python -m funclip.launch --home original

# 浏览器打开 http://127.0.0.1:7861 → 看 slirn 首页
# 点顶部「🚀 上游首页」链接 → 新标签页打开 http://127.0.0.1:7860
```

### 2.2 UI 截图描述

```
┌──────────────────────────────────────────────────────────────┐
│  📋 任务列表  |  🚀 上游首页                            🔄 │  ← 顶部 tab + 刷新
├──────────────────────────────────────────────────────────────┤
│  任务名        | 视频     | 状态     | 创建       | 修改     │
│  张老师讲座-01 | lec1.mp4 | 已准备素材 | 09-14 22:30 | 09-14 23:00│
│  张老师讲座-02 | lec2.mp4 | 草稿      | 09-14 23:00 | 09-14 23:00│
├──────────────────────────────────────────────────────────────┤
│  操作：[详情] [删除] [开始剪辑*] [暂停剪辑*] [停止剪辑*] [编辑*] │
├──────────────────────────────────────────────────────────────┤
│  ➕ 新建任务（占位，REQ-C 实现）                              │
└──────────────────────────────────────────────────────────────┘
* = 占位按钮，点击弹 toast
```

### 2.3 文件系统变化

```
funclip-main/
├── slirn_home/                       # 新建模块
│   ├── __init__.py                   # 暴露 build_app
│   ├── app.py                        # Gradio Blocks 构造
│   ├── task_list.py                  # 任务列表渲染 + 操作回调
│   └── paths.py                      # repo_root / slirn-standalone 路径解析
├── funclip/
│   └── launch.py                     # 修改：新增 --home 分支
└── tests/
    └── test_slirn_home.py            # 单元测试（mock TaskManager）
```

---

## 3. 目录结构（最终）

```
funclip-main/                          # 仓库根
├── funclip/                           # 上游源码（最小改动：仅 launch.py 加分支）
├── slirn_home/                        # 本 REQ 新建
│   ├── __init__.py
│   ├── app.py                         # build_app(repo_root) → gr.Blocks
│   ├── task_list.py                   # 列表渲染 + 6 个操作回调
│   └── paths.py                       # 找 slirn-standalone / tasklib 路径
├── docs/
│   ├── REQM/
│   │   ├── REQ-20260914-001-custom-home-task.md    (Epic)
│   │   ├── REQ-20260914-001-A-task-data-model.md   (REQ-A)
│   │   └── REQ-20260914-001-B-home-and-list.md     (本 REQ)
│   └── design/
│       └── DESIGN-20260914-001-B-home-and-list.md  (本文档)
└── tests/
    └── test_slirn_home.py             # 单元测试
```

---

## 4. 与 REQ-A 的代码耦合

| REQ-A 提供的 API | REQ-B 在哪用 |
|---|---|
| `TaskManager(repo_root)` | `app.py` 构造时初始化一次 |
| `mgr.list()` | `task_list.py:render_task_list()` |
| `mgr.get(task_id)` | `task_list.py:show_detail()` |
| `mgr.delete(task_id)` | `task_list.py:confirm_delete()` |
| `TASK_STATUS_LABEL` | `task_list.py:render_task_list()` 显示中文 |
| `TaskStatus` enum | （仅类型提示，不直接用） |

**不引入新的数据层**——REQ-B 是纯 UI 包装层。

---

## 5. launch.py 修改策略

**最小改动原则**：只改 `funclip/launch.py` 的 argparse 和入口分支，**不动 videoclipper.py**。

```python
# funclip/launch.py 伪代码改动
import argparse
parser = argparse.ArgumentParser()
# ... 现有参数
parser.add_argument("--home", choices=["slirn", "original"], default="original")
args = parser.parse_args()

if args.home == "slirn":
    from slirn_home import build_app
    app = build_app(repo_root=Path(__file__).parent.parent / "slirn-standalone")
    app.launch(server_port=7861, server_name="127.0.0.1")
else:
    # ... 上游原有 launch() 逻辑
```

---

## 6. 边界与风险

### 6.1 已识别风险

| 风险 | 缓解 |
|---|---|
| 上游首页未启动时「上游首页」tab 链接 404 | 用户知道这是新标签页；打开后看到上游 Gradio 默认页或 connection error 即可 |
| `slirn-standalone` 路径找不到 | `paths.py` 提供启发式查找（向上找 sibling dir） |
| 端口被占用 | launch.py 检测端口占用并报错 |
| 跨平台路径分隔符 | 全部用 `pathlib.Path` |

### 6.2 不在 REQ-B 范围

- 「开始剪辑」「暂停剪辑」「停止剪辑」真实行为（按钮占位）
- 「编辑任务信息」真实行为（按钮占位）
- 新建任务流程（REQ-C）
- 公共热词库 UI（REQ-D）

---

## 7. 实现阶段 checklist

按 [docs/sop/03-implementation.md](../sop/03-implementation.md) 走：

- [ ] `slirn_home/paths.py` — `find_slirn_standalone_root()` + `find_repo_root()`
- [ ] `slirn_home/task_list.py` — `render_task_list()` + 6 个回调（refresh/show_detail/arm_delete/confirm_delete/placeholder_toast）
- [ ] `slirn_home/app.py` — `build_app(repo_root) -> gr.Blocks`
- [ ] `slirn_home/__init__.py` — re-export `build_app`
- [ ] `funclip/launch.py` — 加 `--home` 参数 + slirn 分支
- [ ] `tests/test_slirn_home.py` — mock TaskManager 测渲染函数
- [ ] 单元测试通过（pytest）
- [ ] ruff check 0 错

---

## 8. 验证阶段 checklist

按 [docs/sop/05-verification.md](../sop/05-verification.md) 走，逐条验证 REQ-B §4 AC-B.1 ~ AC-B.6 共 30 条。

**预计验证方式**：
- CLI 切换：手工跑 4 种参数组合
- UI 渲染：浏览器打开 7861 检查布局
- 操作按钮：mock TaskManager 测回调函数（不启动 Gradio 服务器）
- ruff + pytest 双通过

---

## 9. 设计完成声明

- [x] D1-D6 共 6 个决策有"为什么"
- [x] §2 端到端示例覆盖 CLI 启动 + UI 布局
- [x] §3 目录结构与代码组织明确
- [x] §4 与 REQ-A 的耦合清晰
- [x] §5 launch.py 改动最小化
- [x] §6 风险已识别
- [x] §7 实现阶段 checklist 可直接执行
- [x] §8 验证阶段 checklist 对齐 REQ AC

**可以进入实现阶段。**
