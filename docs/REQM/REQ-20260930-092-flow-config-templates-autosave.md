# REQ-20260930-092 · 流程配置面板：命名模板 + 自动保存

- **日期**：2026-09-30
- **来源**：用户需求（截图红框圈选流程配置面板①–⑥全部区段）
  > 「图片中红框所选区域内所有的相关参数，包括复选按钮、选中、不选中都需要作为参数设置保存」
  > 澄清答复：「前两种情况都需要既要成为可复用的命名模板，也要自动保存到本地任务」
- **状态**：已实现（1060 测试通过）

## 1. 需求

流程配置面板（红框 = 整个面板：六个阶段区段的每个复选框选中/不选中、严谨性
单选卡片、跳过类别、说话人删除、默认决策、导出方式单选、起点/时长、参数源、
顶层运行模式 + 停止阶段）的全部参数：

1. **命名模板**：整套配置可存为命名模板，跨任务复用（模板下拉直接套用）；
2. **自动保存**：面板内任何改动无需点「💾 保存配置」即写入本任务 pipeline.json。

## 2. 设计

### 2.1 存储（镜像 fine_profiles 模式）

`<repo_root>/tasks/_global_flow_profiles.json`：

```json
{"_schema": 1, "profiles": [
  {"id": "fp_20260930_153000_a1b2c3", "name": "课程全自动",
   "saved_at": "2026-09-30T15:30:00", "task_id_origin": "20260924-001",
   "config": {"subtitle_generation": {...}, ..., "run_mode": "...", "stop_after": "..."}}
], "updated_at": "..."}
```

- config 经 `pipeline_service.validate_config` 规范化 → 与任务 pipeline.json 的
  config 完全同构（套用 = 直接喂 `renderPanel`）
- **显式 False 原样保留**（复选框「不勾」是有效配置，不被 2026-09-30 全选默认覆盖）
- atomic write（.tmp + os.replace）；损坏回退空注册表；重名加 `(2)` 后缀

### 2.2 端点（app.py，紧跟 pipeline_save 之后）

| 端点 | 入参 | 行为 |
|---|---|---|
| `POST /slirn/api/list_flow_profiles` | — | 全部模板（saved_at 倒序，含完整 config） |
| `POST /slirn/api/save_flow_profile` | task_id, name, config? | 存新模板；config 缺省兜底取任务 pipeline.json |
| `POST /slirn/api/delete_flow_profile` | profile_id | 删除；返回是否真删 |

校验：task 必须存在；name 必填且 ≤30 字。

### 2.3 前端（pipeline.js）

- 模板下拉新增固定项 `💾 将当前配置存为模板…`（`fp:_save_as`）→ prompt 命名
  → save_flow_profile → 选中新建模板
- 用户模板以 `fp:<id>` 动态插入下拉（`_populateFlowTemplates`，renderPanel 尾部
  调用，覆盖 loadPanel / 套用 / 重置三条重渲染路径）；选中用户模板时显示 🗑
  删除按钮（`pipe-flow-tpl-del`，click 委托，confirm 后删除）
- **回显**：`_matchTemplateValue` = 套用标记 `_tplAppliedValue`（确定性事实，
  用户手改任何控件即清 null 回「自定义」）> 用户模板深子集匹配 > 内置子集匹配
  > custom。子集而非全等：内置模板 config 是部分字段（duration:null 等），
  readCurrentConfig() 重建完整形态
- **套用即落盘**：内置 / 用户模板套用后立即 `_autoSaveNow()` 写入本任务
- **自动保存**：document 级 `change`（面板内 input/select/textarea，模板下拉
  分支已全部 return 不会误触）+ `input`（文本类，不等失焦）→ 800ms 防抖
  `_autoSaveNow()` → `pipeline_save`；成功静默更新「最近保存：<ts>（自动）」，
  失败仅 console.warn；切任务（loadPanel）重置防抖与套用标记，防串任务
- 程序性 DOM 更新不触发事件 → 无自触发循环

### 2.4 顺带修复（本需求触碰路径上的既有 bug）

1. `saveConfig` 读 `r.updated_at` 但后端返回 `saved_at` → 「最近保存」时间戳
   从不更新 → 改为 `r.saved_at || r.updated_at`
2. 模板下拉从不回显已保存配置（静态 options 无 selected 同步，刷新后一律显示
   内置：人工全审）→ `_populateFlowTemplates` 渲染后按配置匹配回显

## 3. 验收标准与结果

| # | 验收 | 结果 |
|---|---|---|
| AC1 | 面板全部参数（含复选框不勾）存为命名模板，重开浏览器仍在 | ✅ `test_flow_profiles_save_and_list` / `_sanitize_preserves_explicit_false` |
| AC2 | 模板在任意任务的下拉可见、可套用、套用后面板逐字段一致 | ✅ 前端套用直接喂 renderPanel（config 同构）+ E2E |
| AC3 | 删除模板有确认，删后下拉移除，已保存任务不受影响 | ✅ `test_flow_profiles_delete` + 端点测试 |
| AC4 | 改任何控件（勾/不勾/单选/文本/下拉）后不点保存，刷新页面配置仍在 | ✅ 自动保存 change+input 双监听（E2E 验证 pipeline.json 落盘） |
| AC5 | 重名自动加后缀；空名兜底「未命名」；损坏文件回退不崩 | ✅ 3 个专项测试 |
| AC6 | 既有 1045 测试不回归 | ✅ 全量 1060 passed |

## 4. 影响面

- 新增：`slirn_home/flow_profiles.py`、`tests/test_flow_profiles.py`（15 用例）、本 REQM
- 修改：`slirn_home/app.py`（+3 端点）、`slirn_home/static/pipeline.js`
  （模板 UI + 回显 + 自动保存 + 2 处既有 bug 修复）
- 存储新增 `tasks/_global_flow_profiles.json`（运行时生成，不入库）
