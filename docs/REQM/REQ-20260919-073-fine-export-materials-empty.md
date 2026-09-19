# REQ-20260919-073 精剪·导出参数统一不携带 materials

## Context

用户反馈：精剪面板「📤 导出参数」生成的 JSON 与「📥 引用参数（本地）」弹窗里
「📤 导出」生成的 JSON 结构不一致。

对比（导出当前任务 vs 导出全局模板）：

| 字段 | 任务级 `export_fine_params` | 全局模板 `export_fine_global_profile` |
|---|---|---|
| `_schema` | 3 | 3 |
| `materials` | `fc.materials`（含 path/source/type） | `{}` |
| `detected_region` | `fc.detected_region` | `None` |
| `layout/font/output/audio` | 有 | 有 |
| `_source_task_id` | `tid` | `prof.task_id_origin` |
| `_source_profile_id/_name` | 缺 | 有 |

**用户意图**：「导出参数里边存的信息，关于上传素材的是不需要的，这不是设置参数」。
materials（视频/字幕/封面/BGM/参考图的文件路径）不是设置参数 — 它是任务本地
上传的素材文件，跨任务/跨机器复用没意义。导入端也按"不动 materials"实现
（`import_fine_params` 跳 materials），所以导出端本就不该把 materials 写进文件。

## 验收标准

- AC-1：`export_fine_params` 写出的 JSON `materials = {}`（与全局模板同口径）
- AC-2：两个端点导出 JSON 的 5 个核心字段结构完全一致（layout/font/output/audio/detected_region + 溯源字段）
- AC-3：现有 import 行为不变（materials 始终不被覆盖）
- AC-4：现有测试 `test_export_fine_params_returns_full_compose_with_detected_region` 升级，加 `materials == {}` 断言
- AC-5：现有测试 `test_export_fine_global_profile_returns_full_params_json` 已有 `materials == {}` 断言 — 不动

## 方案

### 1. 任务级导出不再写 materials（[slirn_home/app.py:5131-5160](slirn_home/app.py#L5131)）

```python
@app.app.post("/slirn/api/export_fine_params")
async def export_fine_params(body: dict = Body(default_factory=dict)):
    """导出精剪参数为 JSON 字符串（不含素材文件本体，也不上传素材路径）。

    materials 是任务本地上传的素材（视频/字幕/封面/BGM/参考图的文件路径），
    跨任务/跨机器复用无意义。导入端始终不动 materials（见 import_fine_params），
    故导出端本就不该把 materials 写进文件 — 与全局模板 export 同口径。
    """
    tid = (body.get("task_id") or "").strip()
    if not tid:
        return _err("缺少 task_id")
    try:
        mgr.get(tid)
    except Exception as e:
        return _err(f"任务不存在: {e}")
    fc = _get_fine_compose(mgr, tid)
    payload = {
        "_schema": 3,
        "_exported_at": datetime.now().isoformat(timespec="seconds"),
        "_source_task_id": tid,
        "materials": {},  # REQ-20260919-073：素材路径不是设置参数，不导出
        "layout": fc.get("layout"),
        "font": fc.get("font"),
        "output": fc.get("output"),
        "audio": fc.get("audio"),
        "detected_region": fc.get("detected_region"),
    }
    filename = f"fine_params_{tid}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    try:
        content = json.dumps(payload, ensure_ascii=False, indent=2)
    except Exception as e:
        return _err(f"序列化失败: {e}")
    return _ok(filename=filename, content=content, mime="application/json")
```

注：`detected_region` 不动 — 它是检测算法的产物（用户主动点了"🔍 检测区域"按钮，
存到 fc 里），是设置参数（会被"✅ 填充到视频位置和裁剪"按钮应用到 video 布局），
跨任务复用有场景意义。

### 2. 测试升级（[tests/test_workbench.py:4771](tests/test_workbench.py#L4771)）

`test_export_fine_params_returns_full_compose_with_detected_region` 加一行：

```python
assert payload["materials"] == {}, \
    "任务级导出不应包含 materials（路径不是设置参数），与全局模板同口径"
```

### 3. 文件清单

| 文件 | 改动 |
|---|---|
| [slirn_home/app.py:5131-5160](slirn_home/app.py#L5131) | 任务级导出 materials = {} |
| [tests/test_workbench.py:4771](tests/test_workbench.py#L4771) | 升级 1 个测试 |

## 测试

```
pytest tests/ -q     # 523 通过（519 + 4 REQ-072，新增 REQ-073 升级断言）
```

## 不做的事

- ❌ 不改 `detected_region`（它是设置参数，跨任务复用有意义）
- ❌ 不改 `_schema`（保持 3，向后兼容 v2 导入）
- ❌ 不改 `import_fine_params`（materials 本来就不被覆盖）
- ❌ 不动 `_source_*` 溯源字段（任务级用 `_source_task_id`，模板级用 `_source_profile_*`）
- ❌ 不动文件名格式

## 真机验证

1. 重启 slirn → 进任务 A 精剪面板
2. 点「📤 导出参数」→ 下载 `fine_params_<tid>_<ts>.json`
3. 打开 JSON → `materials: {}`（之前是含 video/subtitle/bg 路径的字典）
4. 进「📥 引用参数（本地）」弹窗 → 点任一模板的「📤 导出」→ 下载 JSON
5. 对比两个 JSON：
   - `materials` 都是 `{}`
   - `layout/font/output/audio` 都是字典
   - `detected_region` 在任务级有值，模板级是 `None`（设计如此）
6. 把任务级 JSON 导入回任务 B → 验证 layout/font/output/audio/detected_region 被覆盖，materials 不动
