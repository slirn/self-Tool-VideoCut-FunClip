# REQ-20260920-076 — 全局参数模板导出需包含 detected_region

## 1. 背景

精剪视频面板有两个「📤 导出参数」入口：

| 入口 | 后端 endpoint | 触发位置 |
|---|---|---|
| **任务级「📤 导出参数」** | `POST /slirn/api/export_fine_params` | 精剪面板顶部操作栏（手动保存按钮右侧） |
| **模板级「📤 导出」** | `POST /slirn/api/export_fine_global_profile` | 「📥 引用参数（本地）」modal 里每个模板行的导出按钮 |

两个端点都把当前任务的 fine_compose 导出为 JSON 文件，schema 同为 `_schema: 3`。

## 2. 问题（用户反馈）

用户实测发现：两个导出的 JSON 内容**不一致** —— 任务级导出含 `detected_region`，模板级导出不包含。

> 「我看到现象就是这两个地方导出的数据结果不一致，具体什么原因，还得你来查」

## 3. 现状（根因分析）

[slirn_home/app.py:5327-5340](slirn_home/app.py#L5327) `export_fine_global_profile`：
```python
payload = {
    "_schema": 3,
    ...
    "materials": {},
    "layout": params.get("layout"),
    "font": params.get("font"),
    "output": params.get("output"),
    "audio": params.get("audio"),
    "detected_region": None,  # ← 这里注释说「全局模板不存 detected_region」
}
```

[slirn_home/app.py:5534-5545](slirn_home/app.py#L5534) `export_fine_params`（任务级）：
```python
payload = {
    "_schema": 3,
    ...
    "materials": {},
    "layout": fc.get("layout"),
    "font": fc.get("font"),
    "output": fc.get("output"),
    "audio": fc.get("audio"),
    "detected_region": fc.get("detected_region"),  # ← 任务级正常导出
}
```

不一致的来源是 `save_fine_global_profile` 在保存模板时**就没存 detected_region** —— 看 [slirn_home/app.py:5235-5240](slirn_home/app.py#L5235)：
```python
params = {
    "layout": fc.get("layout") or {},
    "font": fc.get("font") or {},
    "output": fc.get("output") or {},
    "audio": fc.get("audio") or {},
    # ← 没有 detected_region
}
```

而 [slirn_home/app.py:5583-5592](slirn_home/app.py#L5583) `import_fine_params` 明确支持 `detected_region` 字段（白名单 5 字段之一）：
```python
for key in ("layout", "font", "output", "audio", "detected_region"):
    if key in payload:
        fc[key] = payload[key]
        applied.append(key)
```

所以**导入端支持 5 字段，导出端只导 4 字段** —— 这是设计漏洞。

## 4. 用户故事

> **作为** 精剪视频创作者，
> **我希望** 任务级「📤 导出参数」和模板级「📤 导出」导出的 JSON 字段一致，
> **以便** 拿任意一个 JSON 都能正确导入到别的任务，且不会丢背景图检测结果。

## 5. 验收标准

### 5.1 字段一致性（核心）
- [ ] **AC1**：`save_fine_global_profile` 保存模板时，模板 `params` 包含 `detected_region` 字段（即使为 None）
- [ ] **AC2**：`export_fine_global_profile` 导出的 JSON `detected_region` 字段值 = 模板中存储的值（不再是写死 `None`）
- [ ] **AC3**：`export_fine_params`（任务级）和 `export_fine_global_profile`（模板级）导出的 JSON schema 完全一致（顶层字段列表相同）

### 5.2 兼容旧模板
- [ ] **AC4**：旧版本保存的模板（无 `detected_region` 字段）→ 重新导出时 `detected_region` 字段为 `None`（不报错）
- [ ] **AC5**：旧版本保存的模板（无 `detected_region` 字段）→ 应用到任务时不影响目标任务的 `detected_region`（保持兼容行为）

### 5.3 导入端不破坏
- [ ] **AC6**：`import_fine_params` 继续支持 5 字段白名单（不动它）
- [ ] **AC7**：导入的 JSON 中 `detected_region` 为 `null`/缺省 → 不修改目标任务的 detected_region（与现状一致）

### 5.4 UI/UX
- [ ] **AC8**：前端无需改任何 JS（因为导出端只返回 content，前端 Blob 触发下载）
- [ ] **AC9**：测试两种导出方式 → 顶层字段集合一致

## 6. 不做的事

- ❌ 不修改 `apply_fine_global_profile`（应用模板时不动 detected_region）—— 保留原语义
- ❌ 不改 `_schema` 版本号（不破坏现有文件）
- ❌ 不改 `import_fine_params`（已经支持 5 字段）
- ❌ 不把 `detected_region` 加到 `fine_profiles.PROFILE_PARAM_KEYS`（应用时不覆盖）
- ❌ 不为旧模板做迁移（_schema=3 数据足够新，无需 backfill）

## 7. 关联

- REQ-20260919-065：detected_region 升级为正式参数（同一份内容写到 fc["detected_region"] + bg_detect_cache 缓存）
- REQ-20260919-070：「📤 导出」按钮加到模板行（这个 endpoint 当时没把 detected_region 一起导出）
- REQ-20260919-073：materials 不进导出（REQ-076 是它的镜像版本：detected_region 必须进导出）
- import_fine_params 早就在白名单里支持 detected_region（5 字段），所以导入端无破坏
