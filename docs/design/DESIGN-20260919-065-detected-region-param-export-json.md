# DESIGN-20260919-065 检测区域作为参数 + 参数 JSON 导出/导入

## Context

REQ-20260919-065：
1. 把 `bg_detect_cache` 提升为正式参数 `detected_region`（顶层 fc 字段）
2. 加「📤 导出参数」按钮 → 浏览器下载 fine_compose.json（不含素材二进制）
3. 加「📥 导入参数」按钮 → 用户上传之前下载的 JSON → 应用到当前任务（不动 materials）

## 现状

`fc.bg_detect_cache` 当前是 fc 下的一个字段，存检测结果：

```python
{
  "x", "y", "width", "height", "center_x", "center_y",
  "corners": {topleft, topright, bottomleft, bottomright},
  "pixel_count", "image_native_w", "image_native_h",
  "algorithm", "threshold", "detected_at",
  "detected_color"?, "color_tolerance"?,
}
```

问题：「cache」字眼让用户以为是临时缓存，可能被清；语义上它实际是「视频在 bg 图上的位置」参数。

操作栏行 2 当前按钮：`保存设置参数 | 引用参数`，没有导出/导入。

## 设计

### 1. 新增 `fc.detected_region` 字段

写入逻辑：`_save_bg_detect_cache` 同时写 `bg_detect_cache`（向后兼容）和 `detected_region`（新位置）：

```python
def _save_bg_detect_cache(mgr, task_id, result):
    fc = _get_fine_compose(mgr, task_id)
    cache = _build_cache_dict(result)         # 抽出公共构造
    fc["bg_detect_cache"] = cache             # 向后兼容
    fc["detected_region"] = cache             # 新正式位置
    _save_fine_compose(mgr, task_id, fc)
```

读取逻辑：渲染区读 `detected_region`（fallback 到 `bg_detect_cache`）：

```python
detected = fc.get("detected_region") or fc.get("bg_detect_cache") or {}
```

迁移：`_get_fine_compose` 增加 `fc.setdefault("detected_region", None)`，旧任务缺该字段也兼容。

### 2. 导出参数 endpoint

`POST /slirn/api/export_fine_params`：

- body: `{task_id}`
- 读 fc → 序列化 JSON
- 返回 `{ok, filename, content, mime}` 让前端用 Blob 触发下载

```python
@app.app.post("/slirn/api/export_fine_params")
async def export_fine_params(body: dict):
    tid = body.get("task_id")
    fc = _get_fine_compose(mgr, tid)
    payload = {
        "_schema": 3,
        "_exported_at": datetime.now().isoformat(),
        "_source_task_id": tid,
        "materials": fc.get("materials", {}),       # 仅 metadata（不含文件）
        "layout": fc.get("layout"),
        "font": fc.get("font"),
        "output": fc.get("output"),
        "audio": fc.get("audio"),
        "detected_region": fc.get("detected_region"),
    }
    filename = f"fine_params_{tid}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    content = json.dumps(payload, ensure_ascii=False, indent=2)
    return _ok(filename=filename, content=content, mime="application/json")
```

**为什么不让 FastAPI 直接返回文件下载**：返回 dict + 前端用 Blob 下载，可以让前端先做 toast 反馈、再触发下载，体验更顺。

### 3. 导入参数 endpoint

`POST /slirn/api/import_fine_params`：

- body: `{task_id, content}`（content 是 JSON 字符串，前端用 FileReader 读）
- 解析 → 校验 `_schema` + 4 个字段类型 → 写到当前 fc（**只覆盖** layout/font/output/audio/detected_region，不动 materials）
- 返回 `{ok, applied_fields}` 前端用 toast 反馈
- 验证失败返回 `{ok: false, error: '...'}`，前端 toast 显示

```python
@app.app.post("/slirn/api/import_fine_params")
async def import_fine_params(body: dict):
    tid = body.get("task_id")
    raw = body.get("content")
    if not isinstance(raw, str):
        return _err("导入内容必须为 JSON 字符串")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        return _err(f"JSON 解析失败：{e}")
    schema = payload.get("_schema")
    if schema not in (2, 3):
        return _err(f"参数文件 _schema 不兼容（当前仅支持 2/3，文件为 {schema}）")

    # 校验 4 个字段类型
    for key in ("layout", "font", "output", "audio", "detected_region"):
        v = payload.get(key)
        if v is not None and not isinstance(v, dict):
            return _err(f"{key} 必须是 dict 或 null")

    fc = _get_fine_compose(mgr, tid)
    applied = []
    for key in ("layout", "font", "output", "audio", "detected_region"):
        if key in payload:
            fc[key] = payload[key]
            applied.append(key)
    _save_fine_compose(mgr, tid, fc)
    return _ok(applied_fields=applied)
```

**为什么 schema 兼容 2 和 3**：2 是当前生产 schema（无 detected_region）；3 是新 schema（含 detected_region）。导入 v2 文件 → detected_region 留 None（兼容旧文件）。

**为什么不动 materials**：视频/封面/BGM 是当前任务的实体文件，跨任务导入无意义；保持当前任务的 materials 不变。

### 4. 前端按钮 + 交互

操作栏行 2 新增 2 个按钮（放在「保存设置参数 | 引用参数」之后）：

```html
<button class="slirn-btn" data-action="fine-export-params" 
        data-task-id="{tid}" title="下载所有参数（布局/字体/输出/音频/检测区域）为 JSON 文件">
  📤 导出参数
</button>
<button class="slirn-btn" data-action="fine-import-params" 
        data-task-id="{tid}" title="从 JSON 文件导入参数（覆盖当前参数；不动素材文件）">
  📥 导入参数
</button>
```

**导入按钮的细节**：点击时：
1. 动态创建 `<input type="file" accept=".json,application/json">`
2. 触发点击 → 用户选文件
3. FileReader 读为字符串 → POST 到后端
4. 后端成功 → toast + 调 `renderWorkbench()` 刷新整个精剪面板
5. 后端失败 → toast 显示错误

为什么不用 `<form>`：避免 form submit 默认行为；FileReader 比 FormData 更轻（不上传 multipart 大文件）。

### 5. router.js handler

```javascript
else if (action === 'fine-export-params') {
  var _b = target;
  fetch('/slirn/api/export_fine_params', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({task_id: _b.getAttribute('data-task-id')})
  })
  .then(r => r.json())
  .then(j => {
    if (!j.ok) { toast('❌ ' + (j.error || '导出失败')); return; }
    var blob = new Blob([j.content], {type: j.mime || 'application/json'});
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url; a.download = j.filename;
    document.body.appendChild(a); a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    toast('✅ 参数已导出到 ' + j.filename);
  });
}

else if (action === 'fine-import-params') {
  var _b = target;
  var tid = _b.getAttribute('data-task-id');
  var input = document.createElement('input');
  input.type = 'file';
  input.accept = '.json,application/json';
  input.style.display = 'none';
  input.addEventListener('change', function(e) {
    var file = e.target.files[0];
    if (!file) return;
    if (file.size > 1024 * 64) {  // 64KB 上限（远高于实际参数体积）
      toast('❌ 文件过大（>64KB），拒绝导入');
      return;
    }
    var reader = new FileReader();
    reader.onload = function(ev) {
      fetch('/slirn/api/import_fine_params', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({task_id: tid, content: ev.target.result})
      })
      .then(r => r.json())
      .then(j => {
        if (!j.ok) { toast('❌ ' + (j.error || '导入失败')); return; }
        toast('✅ 已从导入文件应用参数（' + (j.applied_fields || []).length + ' 个字段）');
        // 刷新整个精剪面板
        if (typeof renderWorkbench === 'function') renderWorkbench();
      });
    };
    reader.readAsText(file, 'utf-8');
  });
  document.body.appendChild(input);
  input.click();
  document.body.removeChild(input);
}
```

## 实施步骤

| 文件 | 改动 |
|---|---|
| [slirn_home/app.py:_save_bg_detect_cache](slirn_home/app.py) | 同时写 bg_detect_cache + detected_region |
| [slirn_home/app.py:_get_fine_compose](slirn_home/app.py) | setdefault detected_region |
| [slirn_home/app.py](slirn_home/app.py) | 新增 export_fine_params + import_fine_params endpoints |
| [slirn_home/app.py:2665-2683](slirn_home/app.py) | 操作栏行 2 新增 2 个按钮 |
| [slirn_home/static/router.js](slirn_home/static/router.js) | 2 个新 handler（fine-export-params + fine-import-params） |
| [tests/test_workbench.py](tests/test_workbench.py) | 4 个新测试 |

测试：

| 测试 | 验证 |
|---|---|
| `test_save_bg_detect_cache_writes_both_cache_and_detected_region` | 调 `_save_bg_detect_cache` 后 fc 同时有 bg_detect_cache 和 detected_region |
| `test_export_fine_params_returns_full_compose_with_detected_region` | endpoint 返回 JSON 含 detected_region + _schema=3 |
| `test_import_fine_params_overwrites_fields_keeps_materials` | 导入后 layout/font/output/audio/detected_region 被覆盖，materials 不动 |
| `test_import_fine_params_rejects_bad_schema` | _schema=99 → 返回 error，不修改 fc |
| `test_render_fine_cut_zone_has_export_and_import_buttons` | HTML 含 data-action="fine-export-params" 和 "fine-import-params" |

## 风险与边界

| 风险 | 处理 |
|---|---|
| 导出 JSON 包含敏感信息（API key 等）| fc 不含敏感字段；materials 仅 metadata（不含文件） |
| 用户编辑 JSON 后导入损坏 | 后端 schema 校验 + 字段类型校验，错误时 toast 不修改 |
| 文件名冲突 | 时间戳精确到秒 |
| 检测数据很大（含原图坐标）| 最多几 KB；JSON 序列化无压力 |
| 前端 Blob 下载被浏览器拦截 | 用 `<a download>` + 程序点击，浏览器支持 OK |
| `bg_detect_cache` 还在被其他代码读 | 保留字段，向后兼容 |
| `detected_region` 与 `bg_detect_cache` 同步失败 | 用同一个 cache dict 引用，保证强一致 |
| 导入大文件 DoS | 64KB 上限（远高于实际参数体积） |
| 导入覆盖用户当前精挑细选的参数 | toast 提示 + 导入前可导出备份（用户自决） |
| 导入跨 schema（v2→v3）| detected_region 缺失时保持 None（不强制存在） |

## 不做的事

- ❌ 不做导入时的素材迁移（materials 保持当前任务的；用户在新机器上要手动上传视频/BGM）
- ❌ 不做导入合并（只覆盖，不做 diff）
- ❌ 不做版本对比 / 迁移工具
- ❌ 不把 detected_region 自动写入 layout.video（保持参数独立性）
- ❌ 不压缩/编码 JSON（用户可能人工编辑，可读性优先）

## 文件清单

| 文件 | 改动 |
|---|---|
| [slirn_home/app.py:_save_bg_detect_cache](slirn_home/app.py) | 双写 |
| [slirn_home/app.py:_get_fine_compose](slirn_home/app.py) | setdefault detected_region |
| [slirn_home/app.py:2665-2683](slirn_home/app.py) | 行 2 新 2 个按钮 |
| [slirn_home/app.py](slirn_home/app.py) | 新 export_fine_params + import_fine_params endpoint |
| [slirn_home/static/router.js](slirn_home/static/router.js) | 2 个新 handler |
| [tests/test_workbench.py](tests/test_workbench.py) | 4 个新测试 |