# REQ-20260930-093 · 精剪模板套用：六组参数全量回填

- **日期**：2026-09-30
- **来源**：用户确认（精剪合成参数结构文档审阅后）
  > 「是的，这6组都要回填」（指 layout / font / output / audio / detected_region / preview）
- **状态**：已实现（全量测试通过）

## 1. 需求

精剪全局模板保存了六组参数（SAVE_PARAM_KEYS），但套用时只回填
`layout / font / output / audio` 四组，`detected_region / preview` 存而不用。
用户要求套用模板时六组**全部**回填到目标任务 fine_compose.json。

## 2. 设计

### 2.1 决策变更

推翻 `DESIGN-20260920-076` 的「apply 不覆盖 detected_region」决策
（当时理由：检测区是任务背景图强相关坐标，跨任务复用没意义；
用户实际工作流会用同款背景图，希望检测结果随模板回来）。

### 2.2 实现（改动极小）

| 文件 | 改动 |
|---|---|
| `slirn_home/fine_profiles.py` | `PROFILE_PARAM_KEYS` 4 组 → 6 组；`SAVE_PARAM_KEYS = PROFILE_PARAM_KEYS`（保存/套用同口径对称） |
| `slirn_home/app.py` apply_fine_global_profile | 循环沿用 `PROFILE_PARAM_KEYS` → 自动覆盖 6 组；docstring 更新 |

- `params_source="template:<id>"` 的流水线通道也走同一端点
  （pipeline_service 先 POST apply 再 export），无需另改
- materials 依旧永不动（任务素材与模板无关）

### 2.3 兼容性

- 旧模板（REQ-076 时代）params 里**没有** detected_region/preview 键 →
  apply 循环 `if key in params` 天然跳过，不动目标任务这两组，**无需数据迁移**
- 套用 detected_region 后渲染路径优先读 detected_region、缺失才 fallback
  bg_detect_cache（L4899），不产生缓存不一致

## 3. 验收标准与结果

| # | 验收 | 结果 |
|---|---|---|
| AC1 | 套用模板后六组全部写入任务 fc（含 detected_region / preview） | ✅ `test_apply_fine_global_profile_backfills_all_six`（端点级） |
| AC2 | 旧模板缺键不误伤目标任务对应字段 | ✅ `test_apply_fine_global_profile_old_template_skips_missing_keys` |
| AC3 | materials 永不被模板覆盖 | ✅ 既有 `test_fine_profiles_apply...` + 本需求未触碰 materials 逻辑 |
| AC4 | 全量测试无回归 | ✅ 1062 passed |

## 4. 影响面

- 修改：`slirn_home/fine_profiles.py`（常量 + 注释）、`slirn_home/app.py`（apply 端点 docstring/注释）
- 新增：2 个端点级测试（tests/test_workbench.py）
- 文档：DESIGN-20260920-076 加「已被本需求推翻」注记
