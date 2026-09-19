# REQ-20260920-078 精剪视频·系统默认 BGM 备选列表

## Context

精剪视频阶段当前允许用户上传 mp3/wav/m4a 作为背景音乐（materials.audio.path），但用户每次都要从本机找文件，且无法预览。用户希望：
- 系统预先内置 5 个备选 BGM（lo-fi 风格 mp3，来自 `D:\tmp\tttttt\`）
- 在精剪面板「🎵 背景音乐」块加「📦 系统默认 BGM」下拉
- 一键选中 → 自动复制到任务目录 + 写进 `materials.audio.path` + 启用音频
- 保留上传自定义 BGM 能力（两条路径并存）

## 文件清单（5 个系统默认 BGM）

源目录：`D:\tmp\tttttt\`（**绝对路径硬编码进后端**，不动态扫描 — 更稳，避免目录丢失/移位）

| ID | 显示名 | 源文件 | 大小 |
|---|---|---|---|
| lofi_beat_1 | Pretty John — Lo-Fi Beat | `prettyjohn1-lo-fi-beat-580021.mp3` | 1.65 MB |
| lofi_love_loop | Sonican — Sentimental Jazzy Love Loop | `sonican-lo-fi-music-loop-sentimental-jazzy-love-473154.mp3` | 3.07 MB |
| the_mountain | The Mountain — Lo-Fi Beat | `the_mountain-lo-fi-beat-567432.mp3` | 5.22 MB |
| zephira_lofi | Zephira Music — Lo-Fi | `zephiramusic-lo-fi-581502.mp3` | 4.36 MB |
| zephira_relax | Zephira Music — Relaxing Lo-Fi | `zephiramusic-relaxing-lo-fi-587547.mp3` | 5.49 MB |

总计 ~19.8 MB。

## 验收标准

- AC-1：精剪面板「🎵 背景音乐」块下方新增「📦 系统默认 BGM」下拉框，列出 5 项 + 「— 不选（清空）—」
- AC-2：下拉默认显示「— 不选（清空）—」；当前任务的 `materials.audio.path` 非空时显示对应文件名
- AC-3：选某项 BGM → 自动调后端端点 → 复制到任务 `tasks/{tid}/materials/audio/<id>.mp3` → 写进 `fc["materials"]["audio"] = {"path": "materials/audio/<id>.mp3"}` → 自动勾选「启用背景音乐」 → toast「✅ 已选 BGM: xxx」
- AC-4：后端硬编码的 5 个 ID 必须真实存在（启动时校验；缺失 → 启动告警 + 该项从下拉隐藏）
- AC-5：下拉框变更时立即生效（无需点保存按钮），但参数仍按现有 `fineSaveAll` 流程走（防中途刷新丢失）
- AC-6：保留现有的「📤 上传自定义 BGM」路径（materials.upload），用户上传的 BGM 优先级高于系统默认（下拉显示用户文件名）
- AC-7：复用现有 ffmpeg filter 逻辑（[slirn_home/app.py:1992-2000](slirn_home/app.py#L1992)），无需改 `_run_fine_render_async` / `_assemble_fine_filter`
- AC-8：新增 4 个测试覆盖：列表端点 / 默认 BGM 选择 / 缺失文件处理 / 不影响现有 audio 字段
- AC-9：所有现有 542 个测试通过
- AC-10：5 个源 mp3 文件不复制到仓库（绝对路径外引；不进 git，不进 .venv）

## 方案

### 架构

```
精剪面板「🎵 背景音乐」块（[app.py:2823-2835](slirn_home/app.py#L2823)）
  ├─ 原 checkbox + 3 个滑块（保留不动）
  └─ 新增 <select id="slirn-fine-default-bgm">  ← REQ-078
        ├─ POST /slirn/api/list_default_bgms（启动渲染时 fetch，缓存到 JS）
        └─ POST /slirn/api/select_default_bgm  ← 选某项时调
              ├─ 复制 D:\tmp\tttttt\<file>.mp3 → tasks/{tid}/materials/audio/<id>.mp3
              ├─ 写 fc["materials"]["audio"] = {"path": "materials/audio/<id>.mp3"}
              ├─ 启用 fc["audio"]["enabled"] = True
              └─ 返回 {ok, audio_url, message}
```

### 1. 后端常量（[slirn_home/app.py](slirn_home/app.py) 顶部 _FINE_AUDIO_DEFAULTS 附近）

```python
# REQ-20260920-078：系统默认 BGM 备选列表（绝对路径硬编码，不动态扫描）
_DEFAULT_BGMS_DIR = Path(r"D:\tmp\tttttt")
_DEFAULT_BGMS = [
    {"id": "lofi_beat_1",    "name": "Pretty John — Lo-Fi Beat",
     "filename": "prettyjohn1-lo-fi-beat-580021.mp3"},
    {"id": "lofi_love_loop", "name": "Sonican — Sentimental Jazzy Love",
     "filename": "sonican-lo-fi-music-loop-sentimental-jazzy-love-473154.mp3"},
    {"id": "the_mountain",   "name": "The Mountain — Lo-Fi Beat",
     "filename": "the_mountain-lo-fi-beat-567432.mp3"},
    {"id": "zephira_lofi",   "name": "Zephira Music — Lo-Fi",
     "filename": "zephiramusic-lo-fi-581502.mp3"},
    {"id": "zephira_relax",  "name": "Zephira Music — Relaxing Lo-Fi",
     "filename": "zephiramusic-relaxing-lo-fi-587547.mp3"},
]

def _get_default_bgms() -> list[dict]:
    """返回实际可用的系统默认 BGM（启动时校验存在性）。"""
    out = []
    for bgm in _DEFAULT_BGMS:
        p = _DEFAULT_BGMS_DIR / bgm["filename"]
        out.append({**bgm, "available": p.exists(),
                    "size_bytes": p.stat().st_size if p.exists() else 0})
    return out
```

### 2. 两个新端点（[slirn_home/app.py](slirn_home/app.py) 紧邻 `/save_fine_audio` 端点 line 5198）

```python
@app.app.post("/slirn/api/list_default_bgms")
async def list_default_bgms(body: dict = Body(default_factory=dict)):
    """REQ-20260920-078：列出系统默认 BGM（带 available 标记）。"""
    return _ok(bgms=_get_default_bgms())

@app.app.post("/slirn/api/select_default_bgm")
async def select_default_bgm(body: dict = Body(default_factory=dict)):
    """REQ-20260920-078：选某个系统默认 BGM → 复制到任务目录 + 写 fc.materials.audio。"""
    tid = (body.get("task_id") or "").strip()
    bgm_id = (body.get("bgm_id") or "").strip()
    if not tid or not bgm_id:
        return _err("缺少 task_id 或 bgm_id")
    bgm = next((b for b in _DEFAULT_BGMS if b["id"] == bgm_id), None)
    if not bgm:
        return _err(f"未知的 bgm_id: {bgm_id}")
    src = _DEFAULT_BGMS_DIR / bgm["filename"]
    if not src.exists():
        return _err(f"系统默认 BGM 文件不存在: {src}")
    # 目标路径：tasks/{tid}/materials/audio/<id>.mp3
    mat_dir = mgr.tasks_dir / tid / "materials" / "audio"
    mat_dir.mkdir(parents=True, exist_ok=True)
    dst = mat_dir / f"{bgm_id}.mp3"
    shutil.copy2(src, dst)
    # 写 fc
    fc = _get_fine_compose(mgr, tid)
    fc.setdefault("materials", {})["audio"] = {"path": f"materials/audio/{bgm_id}.mp3"}
    fc.setdefault("audio", {})["enabled"] = True
    _save_fine_compose(mgr, tid, fc)
    audio_url = f"/slirn/api/video/{tid}?src=mat&kind=audio&t={int(time.time())}"
    return _ok(audio_url=audio_url, name=bgm["name"], toast=f"✅ 已选 BGM: {bgm['name']}")
```

### 3. 前端 HTML（[slirn_home/app.py:2835](slirn_home/app.py#L2835) 之后插入）

在 `</div>` 关闭 `.slirn-fine-audio-block` 之前：

```python
default_bgm_html = (
    f'<div class="slirn-fine-default-bgm-row">'
    f'<span class="slirn-fine-actions-label">📦 系统默认 BGM</span>'
    f'<select id="slirn-fine-default-bgm" class="slirn-fine-default-bgm-select">'
    f'<option value="">— 不选（清空）—</option>'
    f'</select>'
    f'</div>'
)
audio_html = audio_html.replace("</div>", default_bgm_html + "</div>", 1)
```

> **当前 audio 路径**：用字符串替换在 `</div>` 前插入，避免改整个块结构。

### 4. 前端 JS（[slirn_home/static/router.js](slirn_home/static/router.js)）

新增：

```javascript
// REQ-20260920-078：系统默认 BGM 列表 + 选择
async function fineDefaultBgmLoad() {
  var sel = document.getElementById('slirn-fine-default-bgm');
  if (!sel || sel.dataset.loaded === '1') return;
  try {
    var r = await fetch('/slirn/api/list_default_bgms', {method: 'POST', headers: {'Content-Type':'application/json'}, body: '{}'});
    var j = await r.json();
    if (!j.ok) return;
    // 清空（保留第一个「不选」）
    while (sel.options.length > 1) sel.remove(1);
    (j.bgms || []).forEach(function(b) {
      var opt = document.createElement('option');
      opt.value = b.id;
      opt.textContent = b.available
        ? '🎵 ' + b.name + '（' + (b.size_bytes/1048576).toFixed(1) + ' MB）'
        : '⚠️ ' + b.name + '（文件缺失）';
      opt.disabled = !b.available;
      sel.appendChild(opt);
    });
    sel.dataset.loaded = '1';
  } catch(e) { console.warn('[list_default_bgms]', e); }
}

async function fineDefaultBgmSelect(bgmId, tid) {
  if (!bgmId) {
    // 选「不选」→ 清空 materials.audio
    // 暂不动 fc（保留已上传的 BGM）；只刷新 UI
    return;
  }
  var r = await fetch('/slirn/api/select_default_bgm', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({task_id: tid, bgm_id: bgmId})
  });
  var j = await r.json();
  if (!j.ok) { toast('❌ ' + (j.error || '选择失败')); return; }
  toast(j.toast || '✅ 已选');
  // 启用 checkbox
  var cb = document.querySelector('.slirn-fine-enabled[data-key="audio"]');
  if (cb && !cb.checked) { cb.checked = true; cb.dispatchEvent(new Event('change', {bubbles: true})); }
  // 触发 fineSaveAll 自动保存
  if (typeof fineSaveAll === 'function') await fineSaveAll(false, false);
}
```

### 5. CSS（[slirn_home/static/home.css](slirn_home/static/home.css) 末尾）

```css
/* REQ-20260920-078：系统默认 BGM 下拉 */
.slirn-fine-default-bgm-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px dashed var(--border, rgba(128,128,128,0.2));
}
.slirn-fine-default-bgm-select {
  flex: 1;
  padding: 5px 8px;
  font-size: 12px;
  border: 1px solid var(--border, rgba(128,128,128,0.3));
  border-radius: 6px;
  background: var(--input-bg, #fff);
  color: var(--text, #1e293b);
}
```

### 6. 测试（[tests/test_workbench.py](tests/test_workbench.py)）

| 测试 | 验证 |
|---|---|
| `test_list_default_bgms_returns_five` | POST 返 5 项 + available=true（假设源文件存在） |
| `test_select_default_bgm_copies_to_task` | 选 bgm_id → 检查 `tasks/{tid}/materials/audio/<id>.mp3` 存在 + fc.materials.audio.path 设置 + audio.enabled=True |
| `test_select_default_bgm_unknown_id_errors` | bgm_id='xxx' → 返 error |
| `test_render_fine_cut_zone_has_default_bgm_select` | HTML 含 `id="slirn-fine-default-bgm"` |

## 不做的事

- ❌ 不做音频在线预览（用户已上传的文件可预览 mp3，但默认 BGM 列表只展示文件名 + 大小，不做播放）
- ❌ 不做「上传更多 BGM 到 D:\tmp\tttttt」的管理界面（用户手动放文件即可）
- ❌ 不改现有 ffmpeg filter 逻辑
- ❌ 不持久化「当前任务用的哪个默认 BGM」（fc.materials.audio.path 已存，足够）
- ❌ 不做 BGM 标签/分类（5 个都是 lo-fi，统一展示）
- ❌ 不引入外部音频处理库（ffmpeg 自带 amix 足够）

## 风险与边界

| 风险 | 处理 |
|---|---|
| D:\tmp\tttttt 目录被删/移位 | 启动校验 `available` 标记 + 前端灰显 + 错误 toast |
| 5 个 BGM 风格同质（都是 lo-fi） | 用户可继续上传自定义 BGM；先满足「能一键选」 |
| 复制 20MB BGM 到任务目录 | 一次性复制；任务级缓存，不影响 git（materials/ 已在 gitignore） |
| fc.materials.audio 路径格式与现有上传不兼容 | 完全复用 `{path: "materials/audio/xxx.mp3"}` 格式，与上传路径一致 |
| 用户选「不选（清空）」但已有 materials.audio | 当前实现不动 fc（保留上传的文件）；UI 仅取消选中，不删文件 |
