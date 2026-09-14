# REQ-20260914-001-B — 首页切换 + 任务列表 UI（**子 REQ B**）

| 字段 | 值 |
|---|---|
| 编号 | REQ-20260914-001-B（子 REQ） |
| 日期 | 2026-09-14 |
| 优先级 | P1（核心 UI，依赖 REQ-A） |
| 状态 | 📝 草案 |
| 上位 Epic | [REQ-20260914-001](../REQM/REQ-20260914-001-custom-home-task.md) |
| 上游依赖 | [REQ-20260914-001-A](../REQM/REQ-20260914-001-A-task-data-model.md)（已完成） |
| 关联仓库 | funclip-main（UI 集成在 funclip-main 这边） |
| 代码归属 | `funclip-main/slirn_home/`（新建模块） |
| 范围 | 自定义首页框架、上游/自定义切换、任务列表展示 |

---

## 0. 与 REQ-A 的关系

REQ-A 已实现 `tasklib.TaskManager`（`slirn-standalone/tasklib/`）。
REQ-B 直接调用 TaskManager 渲染任务列表，**不重新实现**任务持久化。

UI 层代码在 **funclip-main**（不开新仓库），因为它要跟上游 `funclip/launch.py` 集成。

---

## 1. 背景与目标

**当前状态**：FunClip fork 启动后直接进入上游 Gradio 界面（`funclip/launch.py`）。

**目标**：在 funclip-main 这边加一个**可切换的首页**：
- 启动时用 CLI 参数选 slirn 自定义首页 或上游 Gradio 首页
- 启动后，slirn 首页上有 tab 切换到上游首页

**价值**：让用户能体验 slirn 任务驱动工作流，同时保留上游 Gradio 入口作为过渡。

---

## 2. 用户故事

### US-B1：切换首页（启动时）

- **角色**：项目使用者
- **场景**：决定今天用 slirn 首页
- **操作**：CLI 加参数 `--home slirn` 或 `--home original`
- **结果**：启动后进入对应的首页

### US-B2：切换首页（运行时）

- **角色**：同上
- **场景**：已经在 slirn 首页，想临时切回上游 Gradio 试一下
- **操作**：点击 slirn 首页顶部的 tab "上游首页"
- **结果**：在同一个浏览器标签页内切换到上游 Gradio（**不刷新页面**？或新标签？**待澄清 D1**）

### US-B3：查看任务列表

- **角色**：同上
- **场景**：已经有几个任务了
- **操作**：打开 slirn 首页
- **结果**：看到任务列表（任务名、原始视频、状态、创建时间、最后修改）

### US-B4：操作任务

- **角色**：同上
- **场景**：想删除一个失败的任务
- **操作**：在任务列表点击"删除"按钮 → 确认
- **结果**：任务从列表消失

### US-B5：操作任务（其他）

按 Epic §11.2 Q2 答案，列表操作包括：
- 查看详情（**本 REQ 提供入口**，详情页留 REQ-C/D）
- 删除
- 开始剪辑
- 暂停剪辑
- 停止剪辑
- 编辑任务信息

**本 REQ 范围**：删除、查看详情入口（按钮 + 占位提示）、其他操作的 UI 占位（按钮存在，行为留 TODO → 后续 REQ）

---

## 3. 核心需求

### 3.1 首页切换（CLI 启动参数）

| ID | 需求 |
|---|---|
| REQ-B.1.1 | CLI 接受 `--home slirn\|original` 参数，默认 `original`（向后兼容） |
| REQ-B.1.2 | `--home slirn` 时启动 slirn 自定义首页 |
| REQ-B.1.3 | `--home original` 时启动上游 Gradio 首页（行为与今天一致） |
| REQ-B.1.4 | 无 `--home` 参数时，行为与今天完全一致（启动上游首页） |

### 3.2 首页切换（运行时 tab）

| ID | 需求 |
|---|---|
| REQ-B.2.1 | slirn 自定义首页顶部有 tab/导航栏，至少两个选项：「任务列表」「上游首页」 |
| REQ-B.2.2 | 点击「上游首页」tab 时，浏览器**新标签页**打开 `http://localhost:<port>/`（上游 launch.py 默认 7860） |
| REQ-B.2.3 | 上游首页 Gradio 仍可独立启动（用 `--home original`），不需要 slirn 首页在运行 |
| REQ-B.2.4 | slirn 首页的 tab 是**只读 UI 控件**，不调用任何后端逻辑 |

### 3.3 任务列表展示

| ID | 需求 |
|---|---|
| REQ-B.3.1 | slirn 首页主体区域显示任务列表 |
| REQ-B.3.2 | 列表字段：任务名、原始视频文件名、状态（中文标签）、创建时间、最后修改时间 |
| REQ-B.3.3 | 列表按 `updated_at` 倒序（与 REQ-A `TaskManager.list()` 一致） |
| REQ-B.3.4 | 列表为空时，显示「暂无任务」占位 + 「新建任务」按钮 |
| REQ-B.3.5 | 列表刷新：页面打开时拉取一次（**手动刷新按钮**待 D2 决定是否加入） |
| REQ-B.3.6 | 列表只读展示，**不直接修改元数据**（点击按钮才触发操作） |

### 3.4 任务列表操作按钮

| ID | 需求 |
|---|---|
| REQ-B.4.1 | 每个任务右侧有操作按钮组 |
| REQ-B.4.2 | 「查看详情」按钮：点击打开模态框/折叠面板，显示完整元数据（**只读**，任务名/创建时间/路径/状态等） |
| REQ-B.4.3 | 「删除」按钮：点击弹确认对话框；用户确认后调 `TaskManager.delete()` |
| REQ-B.4.4 | 「开始剪辑」「暂停剪辑」「停止剪辑」「编辑任务信息」按钮：**UI 占位**（点击弹 toast「该功能在后续 REQ 实现」） |
| REQ-B.4.5 | 删除是**不可逆操作**，必须二次确认（按钮 + 确认对话框） |

### 3.5 UI 技术栈

| ID | 需求 |
|---|---|
| REQ-B.5.1 | 沿用 Gradio 4.x（与上游一致） |
| REQ-B.5.2 | 不引入新前端框架（无 React/Vue/Svelte） |
| REQ-B.5.3 | 任务列表用 Gradio `Dataframe` 或自定义 `gr.Column` + `gr.Row`（**待 D3 设计决定**） |

---

## 4. 验收标准

### AC-B.1 CLI 切换

- [ ] **AC-B.1.1** `python funclip/launch.py --home slirn` 启动后浏览器打开 slirn 首页
- [ ] **AC-B.1.2** `python funclip/launch.py --home original` 启动后浏览器打开上游 Gradio 首页
- [ ] **AC-B.1.3** `python funclip/launch.py`（无参数）行为与 `--home original` 一致
- [ ] **AC-B.1.4** `--home` 参数值非法（如 `--home foo`）时启动失败并给出错误提示

### AC-B.2 运行时切换

- [ ] **AC-B.2.1** slirn 首页顶部有 tab「任务列表」「上游首页」
- [ ] **AC-B.2.2** 点击「上游首页」tab 在新标签页打开上游 Gradio（假设上游也在运行）
- [ ] **AC-B.2.3** 上游首页可独立启动并正常工作（不依赖 slirn 首页在运行）
- [ ] **AC-B.2.4** tab 切换不调用任何后端逻辑（Gradio UI 状态隔离）

### AC-B.3 任务列表展示

- [ ] **AC-B.3.1** 已有 ≥1 个任务时，首页显示列表
- [ ] **AC-B.3.2** 列表字段完整：任务名、原始视频文件名、状态中文标签、创建时间、最后修改时间
- [ ] **AC-B.3.3** 列表按 updated_at 倒序
- [ ] **AC-B.3.4** 列表为空时显示「暂无任务」+ 「新建任务」按钮
- [ ] **AC-B.3.5** 「新建任务」按钮占位（点击弹 toast「该功能在 REQ-C 实现」），本 REQ 不实现新建流程
- [ ] **AC-B.3.6** 状态显示使用 `TASK_STATUS_LABEL` 中文映射（与 REQ-A 一致）

### AC-B.4 任务列表操作

- [ ] **AC-B.4.1** 每个任务有「查看详情」按钮
- [ ] **AC-B.4.2** 「查看详情」打开模态框/折叠面板，显示完整元数据（任务名、ID、原始视频路径、segment、热词路径、状态、创建/更新时间）
- [ ] **AC-B.4.3** 「查看详情」是只读
- [ ] **AC-B.4.4** 每个任务有「删除」按钮
- [ ] **AC-B.4.5** 点击「删除」弹确认对话框
- [ ] **AC-B.4.6** 用户确认后调 `TaskManager.delete(task_id)`，任务从列表消失
- [ ] **AC-B.4.7** 列表有「开始剪辑」「暂停剪辑」「停止剪辑」「编辑任务信息」按钮（占位）
- [ ] **AC-B.4.8** 占位按钮点击后弹 toast「该功能在后续 REQ 实现」

### AC-B.5 UI 技术栈

- [ ] **AC-B.5.1** 沿用 Gradio 4.x，无新前端框架依赖
- [ ] **AC-B.5.2** `requirements.txt` 无新增依赖

### AC-B.6 代码组织

- [ ] **AC-B.6.1** 自定义首页代码在 `funclip-main/slirn_home/` 模块
- [ ] **AC-B.6.2** 与上游 `funclip/launch.py` 解耦（不直接 import 上游内部模块）
- [ ] **AC-B.6.3** 通过 `sys.path.insert(0, slirn-standalone)` 引入 `tasklib`
- [ ] **AC-B.6.4** `slirn_home/__init__.py` 暴露 `build_app(repo_root: Path) -> gr.Blocks`
- [ ] **AC-B.6.5** `funclip/launch.py` 接受 `--home` 参数，参数为 `slirn` 时调用 `slirn_home.build_app()`

---

## 5. 非目标（**本 REQ 不做**）

- ❌ 新建任务流程（REQ-C 范围）
- ❌ 公共热词库 UI（REQ-D 范围）
- ❌ 「开始剪辑」「暂停剪辑」「停止剪辑」「编辑任务信息」的真实行为（按钮占位即可）
- ❌ 任务进度的实时监控（Epic §6 非目标）
- ❌ 自定义首页的移动端适配（先用桌面浏览器跑通）
- ❌ 暗色主题（沿用 Gradio 默认）

---

## 6. 技术约束

- **前端**：Gradio 4.x（与上游一致）
- **后端**：与 funclip 同一进程（不拆微服务）
- **代码位置**：`funclip-main/slirn_home/`
- **依赖**：复用 funclip + slirn-standalone 已有依赖；**不引入新库**
- **与上游 launch.py 的关系**：通过修改 `funclip/launch.py` 添加 `--home` 分支（**仅修改 launch.py 入口，不动 videoclipper.py 等核心**）

---

## 7. 反例（**不该做的设计**）

- ❌ 把 slirn 首页做成独立 Gradio app 占不同端口 → 违反「同一进程、同一浏览器」
- ❌ 改 `funclip/videoclipper.py` 加 slirn 首页逻辑 → 违反「不直接改上游核心」
- ❌ 引入 React/Vue 等新前端框架 → 违反「不引入新依赖」
- ❌ 任务列表用 HTML 表格（绕过 Gradio）→ 破坏 Gradio UI 一致性
- ❌ 删除按钮不做二次确认 → 误点丢失任务数据
- ❌ 列表实时刷新（polling）→ 违反 Epic §6「不做实时监控」
- ❌ 把 tasklib 拷贝到 funclip-main 仓库 → 违反「tasklib 是 slirn-standalone 模块」

---

## 8. 已确认的 Epic 级决策（**沿用**）

| 决策点 | 来源 | 值 |
|---|---|---|
| 切换 UI 形式 | Epic §11.2 Q1 | CLI 参数 + 启动后 tab |
| 任务列表操作 | Epic §11.2 Q2 | 查看详情、删除、开始/暂停/停止剪辑、编辑信息 |
| 状态枚举 | REQ-A | 9 个状态 + TASK_STATUS_LABEL 中文映射 |
| TaskManager | REQ-A | `from tasklib import TaskManager` |

---

## 9. 与上游 funclip 的关系

| 上游概念 | 在 B 里的处理 |
|---|---|
| `funclip/launch.py` | **修改入口**加 `--home` 分支；不改内部逻辑 |
| `funclip/videoclipper.py` | **不改** |
| `funclip/llm/` | **不改** |
| `funclip/utils/` | **不改** |
| 上游 Gradio Blocks | 通过 `python -m funclip.launch --home original` 启动，端口 7860 |
| slirn 首页 Gradio Blocks | 通过 `python -m funclip.launch --home slirn` 启动，端口 7860（同一端口，因为是分支） |

---

## 10. 下一阶段（**子 REQ B 内部**）

进入 [DESIGN-20260914-001-B-home-and-list.md](../../design/DESIGN-20260914-001-B-home-and-list.md) 系统设计。

**关键设计问题**：
- D1：运行时 tab 切换的机制（新标签页 vs iframe vs Gradio 内置 tab）
- D2：列表是否要手动刷新按钮
- D3：任务列表用 Gradio `Dataframe` 还是 `gr.Column` + `gr.Row`
- D4：「查看详情」的模态框实现方式（Gradio `gr.Modal` 还是 `gr.Accordion`）
- D5：删除确认对话框的实现（Gradio 原生支持 vs 自定义）

---

## 11. 出口条件（**REQM 阶段通过标准**）

- [ ] §3 每条需求都有可验证的验收标准（§4）
- [ ] §5 非目标明确，无歧义
- [ ] §6 技术约束与 Epic §11.2 答案一致
- [ ] §7 反例列举 ≥ 3 条
- [ ] 用户 review 通过（或显式说"用草案"）
