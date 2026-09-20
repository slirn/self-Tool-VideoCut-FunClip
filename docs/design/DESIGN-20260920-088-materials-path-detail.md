# DESIGN-20260920-088 — 精剪素材路径详情

## Context

[REQ-20260920-088](../REQM/REQ-20260920-088-materials-path-detail.md) 要求精剪视频·素材生成器的 6 个素材卡片：
1. 底部加「🔍 详情」按钮
2. 点击弹窗显示完整路径 + 来源色块 + 文件元数据
3. 区分 3 种 source：auto（上游产物）/ upload（用户上传）/ default_bgm（系统 BGM 复制品）

## 决策

| 维度 | 决策 | 理由 |
|---|---|---|
| 触发方式 | **按钮 + 模态框**（用户选） | 详情字段多（路径 + size + mtime + source + type），inline 会撑爆布局；模态框集中展示 |
| 路径展示 | **绝对路径 + 相对 repo_root 路径双展示** | 绝对路径便于排查（直接去文件系统看），相对路径便于理解层级 |
| 文件元数据来源 | **后端 `/slirn/api/material_info` 端点实时 stat** | 前端不能 stat 文件；后端 `_resolve_mat_abs` 已有 3 候选路径兜底，直接复用 |
| 来源色块 | **3 种 CSS class**：`.mat-source-auto` (蓝) / `.mat-source-upload` (绿) / `.mat-source-default` (紫) | 视觉区分清晰；复用现有 badge 组件样式 |
| 路径缺失处理 | **模态框内给 warning + 提供「重新自动获取」按钮** | source=auto 但上游产物不存在是常见场景（用户删了 outputs/rough_compose.mp4） |
| 模态框样式 | **复用现有 `.slirn-modal` 通用样式** | home.css 已有 `.slirn-modal-overlay` / `.slirn-modal-content` / ESC 关闭 / 点击遮罩关闭 |
| 字段顺序 | **kind 中文名 → 来源色块 → 物理路径 → fc path → 大小 → mtime → source → type** | 重点信息（路径 + 来源）放最前 |

## 实现要点

### 1. 后端：新增 `/slirn/api/material_info` 端点

```python
@app.app.post("/slirn/api/material_info")
async def material_info(body: dict = Body(default_factory=dict)):
    """REQ-20260920-088：查指定素材的物理路径 + 文件元数据。
    
    Body: {task_id: str, kind: str}
    Return: {
      ok: True,
      kind: "video" | "subtitle" | ...,
      source: "auto" | "upload" | "default_bgm" | "" (none),
      source_label: "上游产物" | "用户上传" | "系统默认 BGM" | "",
      fc_path: "tasks/20260918-022/upload/video_xxx.mp4",  # fc.materials.kind.path（相对 repo_root）
      abs_path: "D:/.../tasks/20260918-022/upload/video_xxx.mp4",  # 实际物理路径
      exists: True | False,
      size_bytes: 1234567,
      mtime: "2026-09-20 10:30:45",  # ISO 格式
      type: "video" | "subtitle" | "image" | "audio",
      upstream_name: "rough_compose.mp4",  # 仅 source=auto 时填
      upstream_exists: True | False,  # 仅 source=auto 时填
    }
    """
    task_id = body.get("task_id", "")
    kind = body.get("kind", "")
    if not task_id or kind not in _FINE_MATERIAL_KINDS:
        return _err("参数错误")
    try:
        mgr.get(task_id)
    except Exception:
        return _err("任务不存在")
    fc = _get_fine_compose(mgr, task_id)
    mat = (fc.get("materials") or {}).get(kind) or {}
    fc_path = mat.get("path") or ""
    source = mat.get("source") or ""
    # 推 source_label
    if kind == "audio" and fc_path.startswith(f"tasks/{task_id}/materials/audio/"):
        source = "default_bgm"
        source_label = "系统默认 BGM"
    elif source == "auto":
        source_label = "上游产物"
    elif source == "upload":
        source_label = "用户上传"
    else:
        source_label = ""
    # 解析物理路径
    abs_path = None
    if fc_path:
        abs_path = _resolve_mat_abs(mgr, task_id, fc.get("materials", {}), kind)
    # stat
    size_bytes = 0
    mtime = ""
    exists = False
    if abs_path and abs_path.exists():
        exists = True
        st = abs_path.stat()
        size_bytes = st.st_size
        mtime = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    # 上游产物信息（仅 auto）
    upstream_name = _fine_upstream_label(task_id, kind, mgr) if kind in _FINE_AUTO_KINDS else ""
    upstream_exists = bool(_fine_upstream_path(task_id, kind, mgr)) if kind in _FINE_AUTO_KINDS else None
    type_str = {"video": "video", "subtitle": "subtitle", "audio": "audio"}.get(kind, "image")
    return _ok(
        kind=kind,
        source=source,
        source_label=source_label,
        fc_path=fc_path,
        abs_path=str(abs_path) if abs_path else "",
        exists=exists,
        size_bytes=size_bytes,
        mtime=mtime,
        type=type_str,
        upstream_name=upstream_name,
        upstream_exists=upstream_exists,
    )
```

### 2. 前端：素材卡片底部加「🔍 详情」按钮 + 模态框

#### 2.1 HTML 修改（`_render_fine_cut_zone` line 2932-2950）

每个素材卡片 status_text 下方加：
```python
f'<button class="slirn-btn slirn-btn-xs" data-action="fine-mat-detail" '
f'data-kind="{kind}" data-task-id="{_esc(task_id)}" '
f'{"disabled" if not path else ""}>🔍 详情</button>'
```

#### 2.2 模态框 DOM（注入到 `_render_fine_cut_zone` 末尾）

```html
<div class="slirn-modal-overlay slirn-hidden" id="slirn-mat-detail-modal">
  <div class="slirn-modal-content slirn-mat-detail-modal">
    <div class="slirn-modal-header">
      <span class="slirn-modal-title">素材详情</span>
      <button class="slirn-modal-close" data-action="mat-detail-close">✕</button>
    </div>
    <div class="slirn-modal-body" id="slirn-mat-detail-body">
      <!-- 动态填充 -->
    </div>
  </div>
</div>
```

#### 2.3 router.js handler

```javascript
// REQ-20260920-088：素材详情按钮
document.addEventListener('click', function(e) {
  var btn = e.target.closest('[data-action="fine-mat-detail"]');
  if (!btn) return;
  var kind = btn.getAttribute('data-kind');
  var tid = btn.getAttribute('data-task-id');
  if (!kind || !tid) return;
  postJSON(SLIRN_API + '/material_info', {task_id: tid, kind: kind})
    .then(function(r) {
      if (!r || !r.ok) {
        toast('❌ 查询失败: ' + (r && r.error || '未知错误'));
        return;
      }
      _renderMatDetailModal(r);
      var modal = document.getElementById('slirn-mat-detail-modal');
      if (modal) modal.classList.remove('slirn-hidden');
    });
});

function _renderMatDetailModal(info) {
  var body = document.getElementById('slirn-mat-detail-body');
  if (!body) return;
  var sourceClass = info.source === 'auto' ? 'mat-source-auto'
    : info.source === 'upload' ? 'mat-source-upload'
    : info.source === 'default_bgm' ? 'mat-source-default'
    : 'mat-source-none';
  var sizeStr = info.exists ? (info.size_bytes > 1024 * 1024
    ? (info.size_bytes / 1024 / 1024).toFixed(2) + ' MB'
    : (info.size_bytes / 1024).toFixed(1) + ' KB') : '—';
  var html = '';
  html += '<div class="slirn-mat-detail-header">';
  html += '<span class="slirn-mat-detail-kind">' + _esc(info.kind) + '</span>';
  if (info.source_label) {
    html += '<span class="slirn-mat-detail-source ' + sourceClass + '">' + _esc(info.source_label) + '</span>';
  }
  html += '</div>';
  html += '<table class="slirn-mat-detail-table">';
  html += '<tr><th>📁 物理路径</th><td>' + (info.exists ? _esc(info.abs_path) : '<span class="slirn-warn">⚠️ 文件不存在</span>') + '</td></tr>';
  html += '<tr><th>🔗 fc.materials.path</th><td>' + _esc(info.fc_path || '—') + '</td></tr>';
  html += '<tr><th>📊 文件大小</th><td>' + sizeStr + '</td></tr>';
  html += '<tr><th>🕒 最后修改</th><td>' + (info.mtime || '—') + '</td></tr>';
  html += '<tr><th>🔖 source 字段</th><td>' + _esc(info.source || 'none') + '</td></tr>';
  html += '<tr><th>🎬 类型</th><td>' + _esc(info.type) + '</td></tr>';
  if (info.source === 'auto') {
    html += '<tr><th>📥 上游产物</th><td>' + _esc(info.upstream_name || '—') + ' ' +
      (info.upstream_exists ? '<span class="slirn-ok">✅ 存在</span>' : '<span class="slirn-warn">⚠️ 已不存在</span>') + '</td></tr>';
  }
  html += '</table>';
  if (info.source === 'auto' && !info.upstream_exists) {
    html += '<div class="slirn-mat-detail-actions">';
    html += '<button class="slirn-btn slirn-btn-primary" data-action="fine-source-auto" data-kind="' + _esc(info.kind) + '">📥 重新自动获取</button>';
    html += '</div>';
  }
  body.innerHTML = html;
}

// 模态框关闭（复用通用）
document.addEventListener('click', function(e) {
  if (e.target.closest('[data-action="mat-detail-close"]')) {
    var modal = document.getElementById('slirn-mat-detail-modal');
    if (modal) modal.classList.add('slirn-hidden');
  }
  // 点击遮罩关闭
  var overlay = e.target.classList && e.target.classList.contains('slirn-modal-overlay');
  if (overlay && overlay.id === 'slirn-mat-detail-modal') {
    overlay.classList.add('slirn-hidden');
  }
});

// ESC 关闭
document.addEventListener('keydown', function(e) {
  if (e.key === 'Escape') {
    var modal = document.getElementById('slirn-mat-detail-modal');
    if (modal && !modal.classList.contains('slirn-hidden')) {
      modal.classList.add('slirn-hidden');
    }
  }
});
```

### 3. CSS（home.css）

```css
/* REQ-20260920-088：素材详情模态框 */
.slirn-mat-detail-modal {
  max-width: 640px;
  width: 90%;
}
.slirn-mat-detail-header {
  display: flex;
  gap: 12px;
  align-items: center;
  margin-bottom: 16px;
}
.slirn-mat-detail-kind {
  font-size: 18px;
  font-weight: 600;
}
.slirn-mat-detail-source {
  padding: 4px 12px;
  border-radius: 4px;
  color: white;
  font-size: 12px;
}
.mat-source-auto { background: #3b82f6; }      /* 蓝 — 上游产物 */
.mat-source-upload { background: #22c55e; }    /* 绿 — 用户上传 */
.mat-source-default { background: #a855f7; }   /* 紫 — 系统 BGM */
.mat-source-none { background: #6b7280; }      /* 灰 — 未配置 */
.slirn-mat-detail-table {
  width: 100%;
  border-collapse: collapse;
}
.slirn-mat-detail-table th {
  text-align: left;
  padding: 8px 12px 8px 0;
  color: var(--muted, #666);
  font-weight: 500;
  vertical-align: top;
  width: 140px;
}
.slirn-mat-detail-table td {
  padding: 8px 0;
  word-break: break-all;
  font-family: monospace;
  font-size: 13px;
}
.slirn-mat-detail-actions {
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid var(--border, #eee);
}
.slirn-warn { color: #f59e0b; }
.slirn-ok { color: #22c55e; }
```

## 关键文件改动

| 文件 | 改动 | 行数 |
|---|---|---|
| [slirn_home/app.py](slirn_home/app.py) | 加 `/material_info` 端点 + `_render_fine_cut_zone` 加「🔍 详情」按钮 + 模态框 DOM | +120 / -0 |
| [slirn_home/static/router.js](slirn_home/static/router.js) | 详情按钮 click handler + 模态框渲染 + 关闭逻辑 | +70 / -0 |
| [slirn_home/static/home.css](slirn_home/static/home.css) | 模态框样式 + 来源色块 + 表格样式 | +50 / -0 |
| [tests/test_workbench.py](tests/test_workbench.py) | 8 个新测试 | +200 / -0 |

## 验证步骤

1. `pytest tests/ -q` → 609 passed（601 + 8 new）
2. 启动 slirn → 选 task 22 → 工作台 → 精剪视频·素材生成器
3. **验证 1**：6 个素材卡片底部有「🔍 详情」按钮（无素材时按钮 disabled）
4. **验证 2**：点 video 卡片「🔍 详情」→ 弹窗显示：
   - 来源色块：「🟦 上游产物」（若 source=auto）或「🟩 用户上传」（若 source=upload）
   - 物理路径：完整绝对路径
   - fc path：相对 repo_root 路径
   - 文件大小：XX KB
   - 最后修改：YYYY-MM-DD HH:MM:SS
5. **验证 3**：audio 卡片显示「🟪 系统默认 BGM」（若来自 select_default_bgm）
6. **验证 4**：source=auto 但上游产物已删除 → 弹窗显示「⚠️ 上游产物已不存在」+ 提供「📥 重新自动获取」按钮
7. **验证 5**：按 ESC 关闭模态框
8. **验证 6**：点击遮罩关闭模态框

## Why

精剪素材的「路径 + 来源」是排查 BGM、静音、白框、字幕错位等问题的关键线索。详情弹窗让用户**自己核对实际路径**，无需我帮忙 grep。

## How to apply

未来任何「素材/文件/资源」类 UI：
1. **必须暴露完整路径**（不止文件名）
2. **区分来源类型**（系统生成 / 用户上传 / 第三方复制）
3. **弹窗详情优于 inline 展示**（详情多、用得少）

## 关联

- [REQ-20260919-061](../REQM/REQ-20260919-061-fine-cut-5-materials.md) — 精剪视频 5 素材上传卡（原版）
- [REQ-20260920-082](../REQM/REQ-20260920-082-move-bgm-selector.md) — 系统默认 BGM 嵌入 audio 卡
- [REQ-20260920-085](../REQM/REQ-20260920-085-upload-audio-auto-enable-bgm.md) — 上传音频自动启用 BGM
