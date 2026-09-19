# DESIGN-20260920-076 — 全局参数模板导出包含 detected_region

## 1. 背景

接 [REQ-20260920-076](REQ-20260920-076-global-template-include-detected-region.md)。任务级和模板级「📤 导出参数」JSON 顶层字段不一致 —— 模板级少了 `detected_region`。

## 2. 设计决策

### 2.1 数据流（修复前 vs 修复后）

**修复前**：
```
用户手动保存 → fineSaveAll() → save_fine_layout/font/output/audio → fc[layout/font/output/audio]
                                                                       fc[detected_region] (不变)

用户点「📤 导出参数」→ export_fine_params → 含 layout/font/output/audio + detected_region  ✅
用户点模板「📤 导出」→ export_fine_global_profile → 含 layout/font/output/audio + None  ❌

原因：
  save_fine_global_profile → params = {layout, font, output, audio}   ← 漏 detected_region
  export_fine_global_profile → payload.detected_region = None          ← 写死
```

**修复后**：
```
save_fine_global_profile → params = {layout, font, output, audio, detected_region}   ✅
export_fine_global_profile → payload.detected_region = params.get("detected_region")  ✅
```

### 2.2 关键决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 保存模板时是否带 detected_region | **是** | 与导出 JSON 字段一致；导入端已经支持 5 字段 |
| 应用模板时是否覆盖 detected_region | **否** | detected_region 与具体任务背景图强相关（4 角点坐标、宽高）—— 跨任务复用没意义；保留 `apply_fine_global_profile` 当前行为（不动 detected_region）|
| 是否把 detected_region 加入 `PROFILE_PARAM_KEYS` | **否** | `PROFILE_PARAM_KEYS` 是 apply 时的白名单；我们只想让它**被保存**+**被导出**，不想让它被应用时覆盖 |
| 旧模板（无 detected_region）怎么办 | **导出时 None** | 与"FC 默认 detected_region=None"一致；导入时 None → 不修改（已有 AC7）|
| 是否改 schema 版本 | **不改** | 字段加进顶层不破坏向后兼容（_schema=3 文件加新字段合法）|

### 2.3 与 REQ-073 的对称关系

| REQ-073 | REQ-076 |
|---|---|
| materials **不**进导出（任务级+模板级一致） | detected_region **要**进导出（任务级+模板级一致）|

两个 REQ 一起确保两个导出端点顶层字段完全一致。

## 3. 实现方案

### 3.1 文件改动

| 文件 | 改动 |
|---|---|
| [slirn_home/app.py](slirn_home/app.py) | `save_fine_global_profile` 增 1 行；`export_fine_global_profile` 改 1 行 |
| [tests/test_workbench.py](tests/test_workbench.py) | 新增 2 个测试 |

### 3.2 代码改动

#### A. `save_fine_global_profile`（[slirn_home/app.py:5234-5240](slirn_home/app.py#L5234)）

**改前**：
```python
fc = _get_fine_compose(mgr, tid)
params = {
    "layout": fc.get("layout") or {},
    "font": fc.get("font") or {},
    "output": fc.get("output") or {},
    "audio": fc.get("audio") or {},
}
```

**改后**：
```python
fc = _get_fine_compose(mgr, tid)
params = {
    "layout": fc.get("layout") or {},
    "font": fc.get("font") or {},
    "output": fc.get("output") or {},
    "audio": fc.get("audio") or {},
    # REQ-20260920-076：把背景图检测结果也存进模板（与任务级 export 同口径），
    # 旧模板（无 detected_region）→ get() 返回 None，应用时不动目标任务的。
    "detected_region": fc.get("detected_region"),
}
```

#### B. `export_fine_global_profile`（[slirn_home/app.py:5327-5340](slirn_home/app.py#L5327)）

**改前**：
```python
params = prof.get("params") or {}
payload = {
    "_schema": 3,
    ...
    "layout": params.get("layout"),
    "font": params.get("font"),
    "output": params.get("output"),
    "audio": params.get("audio"),
    "detected_region": None,  # 全局模板不存 detected_region
}
```

**改后**：
```python
params = prof.get("params") or {}
payload = {
    "_schema": 3,
    ...
    "layout": params.get("layout"),
    "font": params.get("font"),
    "output": params.get("output"),
    "audio": params.get("audio"),
    # REQ-20260920-076：与任务级 export_fine_params 口径一致；旧模板
    # （无 detected_region 字段）→ params.get("detected_region") = None
    "detected_region": params.get("detected_region"),
}
```

#### C. 测试（[tests/test_workbench.py](tests/test_workbench.py)）

```python
def test_export_fine_global_profile_includes_detected_region(monkeypatch, tmp_path):
    """REQ-20260920-076：模板级导出 JSON 应包含 detected_region（与任务级同口径）。"""
    # 建任务 → 触发检测 → save_fine_global_profile → export_fine_global_profile
    # 校验：导出 JSON 的 detected_region 等于任务里检测的结果

def test_export_fine_global_profile_old_template_no_detected_region(monkeypatch, tmp_path):
    """REQ-20260920-076：旧模板（无 detected_region 字段）→ 导出时为 None，不报错。"""
    # 直接写一个旧版 profile JSON（无 detected_region）→ 触发导出 → 校验 None
```

### 3.3 不改的地方

- ❌ `fine_profiles.PROFILE_PARAM_KEYS`：仍只 `(layout, font, output, audio)` —— apply 时不覆盖
- ❌ `apply_fine_global_profile`：保持当前"不动 detected_region"的语义
- ❌ `import_fine_params`：本来就在 5 字段白名单里
- ❌ 前端 router.js：导出端只返 content，前端 Blob 触发下载，零改动

## 4. 验收

### 4.1 单元测试

```bash
./.venv/Scripts/python.exe -m pytest tests/test_workbench.py -v -k "global_profile"
```

预期：新增 2 个测试通过；原有所有测试通过。

### 4.2 手动验证（真机手测）

1. 重启 slirn → 进任务 A（已上传 5 素材 + 已检测过背景图 → detected_region 有值）
2. 调好参数 → 点「💾 保存设置参数」+ 填模板名「测试模板」→ 保存
3. 进任务 B（不同任务）→ 进精剪面板 → 点「📥 引用参数」→ 看到「测试模板」行 → 点「📤 导出」
4. 打开下载的 JSON：`detected_region` 字段 = 任务 A 检测的 4 角点 + 宽高
5. 对比任务级「📤 导出参数」→ JSON 顶层字段集合完全一致
6. 把下载的 JSON 导入任务 C → 任务 C 的 detected_region 被设置为任务 A 的值（导入端已支持）
7. 旧版模板（用 git show 看 pre-076 的 _global_fine_profiles.json）→ 重新导出 → `detected_region: null` 不报错

### 4.3 5 阶段 checklist

- [x] Phase 1: REQ 文档（docs/REQM/REQ-20260920-076）
- [x] Phase 2: DESIGN 文档（本文档）
- [ ] Phase 3: 实现 + 单测
- [ ] Phase 4: Review（code-review Skill）
- [ ] Phase 5: 手动验证 + 提交

## 5. 风险与边界

| 风险 | 处理 |
|---|---|
| 旧模板（无 detected_region）兼容性 | 已在 AC4 验证；导出时 `.get("detected_region")` 返回 None（不报错）|
| 应用模板时是否覆盖 detected_region | 显式不覆盖（PROFILE_PARAM_KEYS 不变）；旧行为保留 |
| 导出 JSON 体积膨胀 | detected_region 是 dict（4 角点 + 宽高）≈ 100 字节，可忽略 |
| 测试覆盖 | 2 个新单测（一个正常路径，一个旧模板兼容路径）|
| 多任务并发 | save_fine_global_profile 本身已用 atomic write（fine_profiles.py）|

## 6. 关联

- REQ-20260919-065：detected_region 升级为正式参数位置
- REQ-20260919-070：模板行加「📤 导出」按钮（漏掉了 detected_region）
- REQ-20260919-073：materials 不进导出（REQ-076 是镜像修复）
- import_fine_params：白名单支持 5 字段（无需改）
