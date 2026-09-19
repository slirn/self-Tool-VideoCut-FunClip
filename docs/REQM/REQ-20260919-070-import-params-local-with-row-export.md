# REQ-20260919-070 引用参数（本地）+ 每行导出

## Context

精剪视频面板的「📥 引用参数」弹窗列出全局模板，每行只有「应用 / 改名 / 删除」
3 个按钮。用户希望：

1. 把按钮名改为「📥 引用参数（本地）」以明确"不会发到外部，本机保存的模板"
2. 每行模板新增「📤 导出」按钮 — 与精剪视频阶段「📤 导出参数」同口径：
   下载该模板参数为 JSON 文件（不依赖任务，可在外部备份/分享/再导入）

## 验收标准

- AC-1：操作栏按钮文案 = `📥 引用参数（本地）`（title 也同步更新）
- AC-2：弹窗列表每行包含 4 个按钮：`📥 应用` / `📤 导出` / `✏️ 改名` / `🗑 删除`
- AC-3：点 `📤 导出` → 后端取该模板 params → 返 JSON 文件（Blob 下载）
- AC-4：导出文件 `_schema = 3`（与任务级 export 同 schema；导入端可复用）
- AC-5：导出文件 `materials = {}`（全局模板按设计不含素材，标空避免误导）
- AC-6：导出文件包含溯源字段：`_source_profile_id` / `_source_profile_name` /
  `_source_task_id`（后者来自 profile.task_id_origin）
- AC-7：文件名格式 = `fine_params_profile_<cleaned_name>_<YYYYMMDD_HHMMSS>.json`，
  模板名里的 Windows 非法字符（`\\/:*?"<>|`）和空白替换为 `_`
- AC-8：不存在的 `profile_id` / 空 `profile_id` → `_err`（不下载）

## 方案

### 1. 按钮重命名（[slirn_home/app.py:2795-2798](slirn_home/app.py#L2795)）

```python
'<button class="slirn-btn" data-action="fine-import-show" '
f'data-task-id="{_esc(task_id)}" title="从本机已保存的全局参数模板中选择应用（不会发到外部）">'
f'📥 引用参数（本地）</button>'
```

### 2. 新端点 `POST /slirn/api/export_fine_global_profile`

[slirn_home/app.py:4977-5016](slirn_home/app.py#L4977) — 与 `export_fine_params` 同口径，
但取 `fine_profiles.get_profile(repo_root, profile_id)` 的 params。

```python
@app.app.post("/slirn/api/export_fine_global_profile")
async def export_fine_global_profile(body: dict):
    pid = (body.get("profile_id") or "").strip()
    if not pid: return _err("缺少 profile_id")
    prof = _fine_profiles.get_profile(repo_root, pid)
    if not prof: return _err(f"模板不存在或已删除: {pid}")
    params = prof.get("params") or {}
    payload = {
        "_schema": 3,
        "_exported_at": datetime.now().isoformat(timespec="seconds"),
        "_source_profile_id": pid,
        "_source_profile_name": prof.get("name", ""),
        "_source_task_id": prof.get("task_id_origin", ""),
        "materials": {},
        "layout": params.get("layout"),
        "font": params.get("font"),
        "output": params.get("output"),
        "audio": params.get("audio"),
        "detected_region": None,
    }
    safe_name = re.sub(r'[\\/:*?"<>|\s]+', "_", prof.get("name") or "profile")[:30]
    filename = f"fine_params_profile_{safe_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    content = json.dumps(payload, ensure_ascii=False, indent=2)
    return _ok(filename=filename, content=content, mime="application/json")
```

### 3. 前端 modal 渲染（[slirn_home/static/router.js:3414-3440](slirn_home/static/router.js#L3414)）

在「📥 应用」与「✏️ 改名」之间插入「📤 导出」按钮：

```javascript
'<button class="slirn-btn slirn-btn-xs" ' +
  'data-action="fine-import-export" data-profile-id="' + _fineEscapeHtml(p.id) + '" ' +
  'title="下载该模板的参数为 JSON 文件（与精剪阶段「📤 导出参数」同口径）">' +
  '📤 导出</button>' +
```

### 4. 委托处理（[slirn_home/static/router.js:5132-5163](slirn_home/static/router.js#L5132)）

新增 `action === 'fine-import-export'` 分支，与 `fine-export-params`（任务级导出）
逻辑一致：fetch → Blob + `<a download>` → toast。

## 关键文件

| 文件 | 改动 |
|---|---|
| [slirn_home/app.py:2738](slirn_home/app.py#L2738) | import_modal 注释更新 |
| [slirn_home/app.py:2742](slirn_home/app.py#L2742) | modal 标题文案（保留不动）+ 底部 hint 加"导出"说明 |
| [slirn_home/app.py:2795-2798](slirn_home/app.py#L2795) | 按钮重命名 + title 更新 |
| [slirn_home/app.py:4977-5016](slirn_home/app.py#L4977) | 新端点 `export_fine_global_profile` |
| [slirn_home/static/router.js:3414-3440](slirn_home/static/router.js#L3414) | modal 行加 📤 导出 按钮 |
| [slirn_home/static/router.js:5132-5163](slirn_home/static/router.js#L5132) | 委托处理 `fine-import-export` |

## 测试

| 测试 | 验证 |
|---|---|
| `test_export_fine_global_profile_returns_full_params_json` | 端点返 JSON 三件套 + payload 结构（AC-3/4/5/6） |
| `test_export_fine_global_profile_rejects_missing_id` | profile_id 缺失/空 → _err（AC-8） |
| `test_export_fine_global_profile_rejects_unknown_id` | 不存在 ID → _err（AC-8） |
| `test_export_fine_global_profile_sanitizes_filename` | 文件名不含 Win 非法字符（AC-7） |
| `test_render_workbench_button_renamed_to_local` | 按钮文案改为「引用参数（本地）」（AC-1） |
| `test_router_fine_import_row_has_export_button` | router.js 渲染 + 委托处理含 fine-import-export（AC-2/3） |

```
pytest tests/ -q     # 516 通过（510 + 6 新）
```

## 不做的事

- ❌ 不做"导出后从全局删除"（导出是只读操作，不改源）
- ❌ 不做"批量导出全部模板"（超出本需求范围）
- ❌ 不改 `_schema`（保持 3，与任务级 export 一致；导入端自动兼容）
- ❌ 不引入新依赖（仅复用 `re.sub` 清理文件名）

## 真机验证

1. 重启 slirn → 进任务 A 精剪视频面板
2. 点「📥 引用参数（本地）」→ 弹窗列表中每行应见 4 个按钮（应用 / 导出 / 改名 / 删除）
3. 点任一行的「📤 导出」→ 浏览器下载 `fine_params_profile_<name>_<ts>.json`
4. 打开 JSON → `_schema: 3` + `_source_profile_id` + `_source_profile_name` +
   `materials: {}` + 4 个 params 子字段
5. 验证弹窗按钮文案 = 「📥 引用参数（本地）」