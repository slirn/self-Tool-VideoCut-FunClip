# DESIGN-20260920-082 — 精剪·「系统默认 BGM」下拉从参数区移到素材上传区

## Context

REQ-20260920-078 在「🎵 背景音乐」参数块（`audio_html`）底部追加了一个 `<div class="slirn-fine-default-bgm-row">`，包含 `<select id="slirn-fine-default-bgm">`。这个下拉的功能是「一键选 5 个内置 lo-fi mp3 之一并写入 `fc.materials.audio.path`」，本质上是**素材选择**，但放在了**参数区**。

用户反馈（2026-09-20）：
> 「系统提供的背景音乐列表的选择应该放在上传素材的里边，而不是应该放在参数里边」

### 现状分析

`audio_html` 当前结构（[app.py:3023-3045](slirn_home/app.py#L3023-L3045)）：

```
┌─ .slirn-fine-audio-block（参数块）
│   ├─ 🎵 背景音乐 标题
│   ├─ ☑️ 启用背景音乐 checkbox (data-key="audio")
│   ├─ 音量 / 淡入 / 淡出 3 个滑块（音量是 audio 的「参数」）
│   └─ .slirn-fine-default-bgm-row  ← 越界：这里是「素材选择」
│       └─ 📦 系统默认 BGM <select>
└─
```

素材上传区结构（[app.py:2811-2864](slirn_home/app.py#L2811-L2864)）：

```
┌─ .slirn-fine-uploads（6 张上传卡，video/subtitle/cover/bg/reference/audio）
│   ├─ .slirn-fine-upload-card data-kind="audio"   ← audio 上传卡
│   │   ├─ 🎵 背景音乐 标题
│   │   ├─ 提示 "mp3/wav/m4a"
│   │   ├─ <input type="file"> + 📤 上传文件 + 👁️ 预览
│   │   └─ .slirn-fine-upload-status  ← 当前上传文件名 / 未上传
│   ...
└─
```

### 目标结构

audio 上传卡内部，在 status 行下方追加 `.slirn-fine-default-bgm-row`：

```
┌─ .slirn-fine-uploads
│   ├─ .slirn-fine-upload-card data-kind="audio"
│   │   ├─ 🎵 背景音乐 标题
│   │   ├─ 提示 "mp3/wav/m4a"
│   │   ├─ <input type="file"> + 📤 上传文件 + 👁️ 预览
│   │   ├─ .slirn-fine-upload-status
│   │   └─ .slirn-fine-default-bgm-row     ← 新位置
│   │       └─ 📦 系统默认 BGM <select>
│   ...
│   ├─ .slirn-fine-upload-card data-kind="bg"
│   ...
└─
```

参数区变成纯参数：

```
┌─ .slirn-fine-audio-block（参数块）
│   ├─ 🎵 背景音乐 标题
│   ├─ ☑️ 启用背景音乐 checkbox
│   └─ 音量 / 淡入 / 淡出 3 个滑块
└─
```

---

## ADR-082-1：迁移到 audio 上传卡（而不是独立新卡片）

**决策**：把 BGM 下拉放进 `data-kind="audio"` 的上传卡内部（不是新建独立卡片）。

**考虑过的方案**：

| 方案 | 优劣 |
|---|---|
| **A. 嵌进 audio 上传卡内**（采纳） | 视觉上 BGM 与「🎵 背景音乐」上传卡天然绑定；不需要新增 card；与「上传/自动获取」是同一类「素材来源选项」 |
| B. 在 audio 上传卡右侧加一栏 | 6 列变 6 列不对称，破坏 grid；不必要 |
| C. 独立卡片 `.slirn-fine-default-bgm-card` | 过度结构化；用户视角看，BGM 本就是 audio 素材的另一个来源 |
| D. 嵌进 audio 参数块顶部（移走但不挪远） | 仍在参数区，与用户诉求不符 |

**理由**：方案 A 最贴合用户认知「BGM 是 audio 的来源选项」。同时 CSS class `.slirn-fine-default-bgm-row` 已有（REQ-078），复用即可，零额外样式风险。

---

## ADR-082-2：保持 class 名和 select id 不变（router.js 零改动）

**决策**：迁移 HTML 时不改 `.slirn-fine-default-bgm-row` / `.slirn-fine-default-bgm-select` class 名，不改 `<select id="slirn-fine-default-bgm">` 的 id。

**理由**：
1. router.js 中所有 BGM 相关逻辑都用 id / class 选取（`getElementById('slirn-fine-default-bgm')`、`e.target.id === 'slirn-fine-default-bgm'`），改名 = 全链路改。
2. 位置变了但 DOM 节点属性不变 → router.js / CSS 都不用改。
3. 符合「最小改动」原则；测试只需验证 HTML 字符串，不验证 JS 内部 state。

**附带收益**：REQ-078 遗漏的 `fineDefaultBgmLoad()` 未调用问题，在本 REQ 范围内一并修复（移到上传卡后，用户点上传区折叠展开触发更明显），见 ADR-082-3。

---

## ADR-082-3：修 REQ-078 遗留 bug —— 让 `fineDefaultBgmLoad()` 真正被调用

**决策**：在 router.js 的 fine-cut 面板初始化路径里（panel render 完成时）调用一次 `fineDefaultBgmLoad()`。

**现状**：
- `fineDefaultBgmLoad()` 函数定义于 [router.js:3865](slirn_home/static/router.js#L3865)，逻辑完整（fetch `/slirn/api/list_default_bgms` → 填 options）。
- 但**整个 router.js 中没有任何调用点**（grep `fineDefaultBgmLoad` 只返回函数定义）。
- 用户实际体验：下拉只有「— 不选（清空选择）—」一个 option，**选了什么都不出来**。

**修复方案**：在 panel 加载完成时（loadPanel 回调里），调一次：

```javascript
// REQ-20260920-082：触发默认 BGM 列表加载（修复 REQ-078 遗漏）
if (typeof fineDefaultBgmLoad === 'function') {
  try { fineDefaultBgmLoad(); } catch (e) { console.warn('[bgm-load]', e); }
}
```

调用点选取：
- **方案 α**：在 `slirnPipelineMount` / `loadPanel` 末尾调用（panel render 完成时）
- **方案 β**：在 `document.addEventListener('DOMContentLoaded', ...)` 末尾调用
- **方案 γ**：暴露一个 `window.slirnFineDefaultBgmLoad` 全局函数，由 Python 模板里 `<script>` 调用

**采纳方案 α**：复用现有 panel 渲染完成路径，避免新增 template script（template script 在 JSDOM 测试里需要单独 mock）。在 `pipeline.js` 的 `loadPanel` 末尾或 router.js 现有的「panel 内容加载完成」钩子处调用。

**新增测试**（覆盖此修复）：
- `test_fineDefaultBgmLoad_called_on_panel_load`：mock fetch → 触发 loadPanel(tid) → 验证 `list_default_bgms` 被调过一次

---

## 实施步骤（5 阶段 SOP）

### Phase 1 — REQ 文档 ✓
产出：[docs/REQM/REQ-20260920-082-move-bgm-selector.md](docs/REQM/REQ-20260920-082-move-bgm-selector.md)

### Phase 2 — DESIGN 文档（本文件）

### Phase 3 — 实现

**Step 1：HTML 迁移**（[slirn_home/app.py](slirn_home/app.py)）

a) 删除 `audio_html` 末尾的 `.slirn-fine-default-bgm-row` 块（[app.py:3037-3043](slirn_home/app.py#L3037-L3043)）：
```python
# 删除这段
# REQ-20260920-078：系统默认 BGM 下拉（一键选 5 个 lo-fi mp3 之一）
f'<div class="slirn-fine-default-bgm-row">'
f'<span class="slirn-fine-actions-label">📦 系统默认 BGM</span>'
f'<select id="slirn-fine-default-bgm" class="slirn-fine-default-bgm-select">'
f'<option value="">— 不选（清空选择）—</option>'
f'</select>'
f'</div>'
```

b) 在上传卡循环里（[app.py:2846-2863](slirn_home/app.py#L2846-L2863)），对 `kind == "audio"` 的卡片，append 一个 `.slirn-fine-default-bgm-row` 块（保留 `data-task-id` 用于 router.js change 委托）：

```python
# REQ-20260920-082：把「系统默认 BGM」下拉嵌进 audio 上传卡（从参数区迁移）
default_bgm_html = ""
if kind == "audio":
    default_bgm_html = (
        f'<div class="slirn-fine-default-bgm-row" data-task-id="{_esc(task_id)}">'
        f'<span class="slirn-fine-actions-label">📦 系统默认 BGM</span>'
        f'<select id="slirn-fine-default-bgm" class="slirn-fine-default-bgm-select">'
        f'<option value="">— 不选（清空选择）—</option>'
        f'</select>'
        f'</div>'
    )

upload_cards.append(
    f'<div class="slirn-fine-upload-card{has}" data-kind="{kind}" data-source="{source or "none"}">'
    f'<div class="slirn-fine-upload-label">{icon} {label}{source_badge}</div>'
    f'<div class="slirn-fine-upload-hint">{accept}</div>'
    f'<input type="file" class="slirn-fine-file" id="slirn-fine-file-{kind}" '
    f'accept=".{",".join(accept.split("/"))}" data-kind="{kind}">'
    f'<button class="slirn-btn slirn-btn-xs" data-action="fine-upload" data-kind="{kind}">📤 上传文件</button>'
    f'<button class="slirn-btn slirn-btn-xs" data-action="fine-mat-preview" data-kind="{kind}" '
    f'data-task-id="{_esc(task_id)}" '
    f'{"disabled" if not has else ""} '
    f'title="{_esc("请先上传或自动获取素材") if not has else _esc("打开预览窗口（可缩放）")}">'
    f'👁️ 预览</button>'
    f'{auto_btn_html}'
    f'<div class="slirn-fine-upload-status" data-status-kind="{kind}">{status_text}</div>'
    f'{default_bgm_html}'  # ← 新增：仅 audio 卡
    f'</div>'
)
```

**Step 2：router.js 修复 `fineDefaultBgmLoad` 未调用 bug**

在 `pipeline.js` 的 `loadPanel` 函数末尾（[pipeline.js:631](slirn_home/static/pipeline.js#L631) 之后），追加：

```javascript
// REQ-20260920-082：触发默认 BGM 列表加载（修复 REQ-078 遗漏）
if (typeof window.fineDefaultBgmLoad === 'function') {
  try { window.fineDefaultBgmLoad(); } catch (e) { console.warn('[bgm-load]', e); }
}
```

同时把 router.js 里的 `fineDefaultBgmLoad` 暴露到 window（如果目前只是局部函数）：

```javascript
// REQ-20260920-082：暴露给 pipeline.js loadPanel 末尾调用
window.fineDefaultBgmLoad = fineDefaultBgmLoad;
```

或合并：`window.fineDefaultBgmLoad = fineDefaultBgmLoad;` 在 router.js 顶部定义。

**Step 3：CSS 适配**

`.slirn-fine-default-bgm-row` 现有样式（[home.css:3668-3675](slirn_home/static/home.css#L3668-L3675)）用 flex 布局 + 虚线分隔，**已经够用**，不需要新增样式。

但需要确认：audio 上传卡内的 status 行 + BGM row 是否会被 grid 错位。查看现有 `.slirn-fine-upload-card` 样式（如果用 grid），可能需要让 `.slirn-fine-default-bgm-row` 占整宽。

→ 测试时会验证；如果视觉错位，CSS 补丁 1-3 行：
```css
.slirn-fine-upload-card .slirn-fine-default-bgm-row {
  grid-column: 1 / -1;  /* span all columns */
}
```

**Step 4：测试**（[tests/test_workbench.py](tests/test_workbench.py)）

3 个新测试：

```python
def test_render_fine_cut_zone_bgm_select_moved_to_audio_upload_card(tmp_path):
    """REQ-20260920-082：BGM 下拉不再在音频参数块，而在 audio 上传卡内。"""
    from slirn_home.app import _render_workbench

    m, video = _make_mgr(tmp_path)
    t = m.create(name="bgm-relocate", original_video=video)
    html = _render_workbench(t.task_id, m)
    # 1. BGM select 仍然存在
    assert 'id="slirn-fine-default-bgm"' in html
    assert 'class="slirn-fine-default-bgm-select"' in html
    # 2. BGM select 在 audio 上传卡内（不在 audio 参数块）
    audio_card_pos = html.find('data-kind="audio"')
    bgm_pos = html.find('id="slirn-fine-default-bgm"')
    audio_block_pos = html.find('class="slirn-fine-audio-block"')
    assert audio_card_pos > 0
    assert bgm_pos > 0
    assert audio_block_pos > 0
    # BGM 在 audio card 之前 → 不在 audio block 内
    assert bgm_pos < audio_block_pos, (
        f"BGM select ({bgm_pos}) 必须在 audio 参数块 ({audio_block_pos}) 之前"
    )
    # BGM 在 audio card 之后（说明嵌进了 audio card）
    assert bgm_pos > audio_card_pos


def test_render_fine_cut_zone_bgm_row_carries_task_id(tmp_path):
    """REQ-20260920-082：BGM 下拉带 data-task-id，router.js change 委托靠它取 tid。"""
    from slirn_home.app import _render_workbench

    m, video = _make_mgr(tmp_path)
    t = m.create(name="bgm-tid", original_video=video)
    html = _render_workbench(t.task_id, m)
    # audio 上传卡内的 BGM row 必须有 data-task-id
    # 取 audio card 范围
    audio_start = html.find('data-kind="audio"')
    audio_end = html.find('</div>', audio_start)  # 简化截断
    # 更安全：直接 grep 紧邻上下文
    assert f'data-task-id="{t.task_id}"' in html
    # 单独验证 BGM row 块
    import re
    m_bgm = re.search(
        r'<div class="slirn-fine-default-bgm-row" data-task-id="([^"]+)">.*?slirn-fine-default-bgm',
        html, re.DOTALL,
    )
    assert m_bgm, "BGM row 缺少 data-task-id 或位置错"
    assert m_bgm.group(1) == t.task_id


def test_render_fine_cut_zone_audio_block_no_bgm_select(tmp_path):
    """REQ-20260920-082：音频参数块（audio_html）不再含 BGM select。"""
    from slirn_home.app import _render_workbench

    m, video = _make_mgr(tmp_path)
    t = m.create(name="bgm-block-clean", original_video=video)
    html = _render_workbench(t.task_id, m)
    # 取 audio 参数块范围
    audio_block_start = html.find('class="slirn-fine-audio-block"')
    audio_block_end = html.find('</div>', audio_block_start + 100)  # 跳过内部 div
    # 实际 audio block 是嵌套的 div，需找匹配的 </div>（简单方式：到下个 slirn-fine-* 块前）
    next_block_pos = html.find('slirn-fine-layout-block', audio_block_start)
    if next_block_pos == -1:
        next_block_pos = html.find('slirn-fine-font-block', audio_block_start)
    audio_block_html = html[audio_block_start:next_block_pos]
    assert 'slirn-fine-default-bgm' not in audio_block_html, (
        f"audio 参数块不应再含 BGM select，但发现：\n{audio_block_html[:500]}"
    )
```

1 个 router.js 加载修复测试（pipeline.js mock 路径复杂，可能跳过；用最小 mock 验证调用存在）：

```python
def test_fine_default_bgm_load_called_on_pipeline_panel_load():
    """REQ-20260920-082：修复 REQ-078 遗留 bug —— loadPanel 完成时触发 fineDefaultBgmLoad。"""
    # 检查 pipeline.js / router.js 中 loadPanel 完成路径有 fineDefaultBgmLoad 调用
    import pathlib
    pipeline_js = (pathlib.Path(__file__).parent.parent / "slirn_home" / "static" / "pipeline.js").read_text(encoding="utf-8")
    assert "fineDefaultBgmLoad" in pipeline_js, (
        "pipeline.js 必须引用 fineDefaultBgmLoad（loadPanel 末尾调用）"
    )
    # router.js 中应暴露 window.fineDefaultBgmLoad
    router_js = (pathlib.Path(__file__).parent.parent / "slirn_home" / "static" / "router.js").read_text(encoding="utf-8")
    assert "window.fineDefaultBgmLoad" in router_js, (
        "router.js 必须把 fineDefaultBgmLoad 挂到 window，供 pipeline.js 调用"
    )
```

### Phase 4 — Review

按 [docs/sop/04-review.md](docs/sop/04-review.md) 走 medium effort：
- 重点 1：HTML 迁移时 audio_block 与 upload card 顺序：app.py 中 audio_block 在 upload_html **之后**定义（line 3023-3045 vs line 2811-2864），所以 `audio_html` 里删 BGM 不会影响 upload_html；新位置插到 upload card 循环里（line 2846-2863）也是独立 append，**不会有重复 id**。
- 重点 2：`grep -c 'id="slirn-fine-default-bgm"' slirn_home/app.py` 应该 = 1（迁移后唯一来源）。
- 重点 3：`grep -c 'class="slirn-fine-default-bgm-row"' slirn_home/app.py` 应该 = 1。
- 重点 4：router.js / pipeline.js 改完后 `node --check` 语法 OK。
- 重点 5：现有 567 测试全过 + 新增 4 测试全过 = 571 通过。

### Phase 5 — 验证

1. `pytest tests/ -q` → 571 passed
2. 重启 slirn（`./.venv/Scripts/python.exe funclip/launch.py --home slirn`）
3. 浏览器访问 `/home`，打开精剪面板，展开「📁 上传素材」
4. 验证：
   - 「🎵 背景音乐」上传卡下方出现「📦 系统默认 BGM」下拉
   - 下拉框列出 5 个 lo-fi mp3 选项（REQ-078 + 本次修复的 bug 让列表真正加载）
   - 「🎵 背景音乐」参数块（折叠区外）**没有**「系统默认 BGM」字样
5. 选中某个 BGM → 验证 audio 上传卡 status 行变化、自动勾选 audio 参数 checkbox
6. 提交 4 个 commit（按 SOP）：
   - `feat(fine-ui): REQ-20260920-082 BGM 下拉迁移到 audio 上传卡`
   - `fix(router): REQ-20260920-082 修复 fineDefaultBgmLoad 未调用 bug`
   - `test(bgm-relocate): REQ-20260920-082 4 个新测试（567 → 571）`
   - `docs(reqm): REQ-20260920-082 REQ + DESIGN + VERIFICATION 文档`

---

## 关键文件改动汇总

| 文件 | 位置 | 改动 | 估算行数 |
|---|---|---|---|
| [slirn_home/app.py](slirn_home/app.py) | [L3037-3043](slirn_home/app.py#L3037-L3043) | 删除 audio_html 末尾 BGM row | -7 |
| [slirn_home/app.py](slirn_home/app.py) | [L2846-2863](slirn_home/app.py#L2846-L2863) | 在 audio card append BGM row | +9 |
| [slirn_home/static/router.js](slirn_home/static/router.js) | 3865 附近 | 暴露 `window.fineDefaultBgmLoad` | +1 |
| [slirn_home/static/pipeline.js](slirn_home/static/pipeline.js) | loadPanel 末尾 | 调 `window.fineDefaultBgmLoad()` | +3 |
| [slirn_home/static/home.css](slirn_home/static/home.css) | 3668 附近 | 可选：grid-column: 1/-1 | +3 |
| [tests/test_workbench.py](tests/test_workbench.py) | 末尾 | 4 个新测试 | +80 |
| [docs/REQM/](docs/REQM/) | 新建 | REQ | +90 |
| [docs/design/](docs/design/) | 新建 | DESIGN | +200（本文件）|
| [docs/verification/](docs/verification/) | 新建 | VERIFICATION | +80 |

净代码量约 **+10 行**（HTML 迁移 +9 / router.js +1 / pipeline.js +3 / CSS +3）。

---

## 风险与边界

| 风险 | 处理 |
|---|---|
| 迁移后 audio 上传卡 grid 错位 | CSS 补丁：`.slirn-fine-upload-card .slirn-fine-default-bgm-row { grid-column: 1 / -1; }` |
| `fineDefaultBgmLoad()` 调用时机太早（DOM 未渲染） | 在 loadPanel 回调里调用（panel innerHTML 已写入），确保 `getElementById` 找得到 select |
| 旧 `_slirnFineAudioPath` 缓存 key 不变 | router.js 完全不动（ADR-082-2） |
| audio 折叠区默认折叠 → 用户看不到 BGM 下拉 | 用户点开上传折叠区就看到；与现有 6 个上传卡行为一致；不扩散 |
| 6 列 grid 在窄屏（<800px）换行 → BGM row 跨列错位 | 复用现有 mobile 适配（不需要改） |
| `select_default_bgm` 后端接口不变 | ADR-082-2 已约束：零后端改动 |
| 多个 task 同时打开（不同 panel）→ fineDefaultBgmLoad 全局缓存冲突 | 当前 `_defaultBgmsCache` 是模块作用域；多个 panel 共用 OK；不扩散 |

---

## 复用现有基础设施

- CSS `.slirn-fine-default-bgm-row` / `.slirn-fine-default-bgm-select`（[home.css:3668-3689](slirn_home/static/home.css#L3668-L3689)）
- 后端 `/slirn/api/list_default_bgms` / `/slirn/api/select_default_bgm`（REQ-078 已实现）
- router.js 中 `fineDefaultBgmLoad` / `fineDefaultBgmSyncFromFc` / `fineDefaultBgmSelect` / change 委托
- `_render_fine_cut_zone` 中 `_FINE_MATERIAL_KINDS` 循环（[app.py:2813](slirn_home/app.py#L2813)）

---

## 关联

- [REQ-20260920-078-system-default-bgm.md](docs/REQM/REQ-20260920-078-system-default-bgm.md) — 本次调整的目标 feature
- [REQ-20260920-080-fix-bgm-filter-chain-label.md](docs/REQM/REQ-20260920-080-fix-bgm-filter-chain-label.md) — BGM 相关前置修复
- [REQ-20260919-061-fine-cut-4-materials.md](docs/REQM/REQ-20260919-061-fine-cut-4-materials.md) — 精剪视频面板原始 4 素材架构
- [REQ-20260919-067-fine-uploads-4-cols.md](docs/REQM/REQ-20260919-067-fine-uploads-4-cols.md) — 上传区 6 列 grid 布局
