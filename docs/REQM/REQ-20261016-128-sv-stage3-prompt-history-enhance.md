# REQ-20261016-128 — Stage 3 提示词 UI 三件套：括号注释 + 默认 ⭐ 修复 + 每条删除

> 状态：✅ 已完成（2026-10-16）
> 关联：[REQ-20261012-099](REQ-20261012-099-stage3-content-parse-rework.md)（提示词两级存储）
> 关联：[REQ-20261016-127](REQ-20261016-127-sv-stage3-prompt-timespan-summation.md)（上一轮 Stage 3 改版）
> 范围：**仅 Stage 3**，Stage 4 提示词不同不动

## 1. 需求（用户原话）

> 在引用系统提示词按钮的后面加个括号，里边写上就是可以带入片段数和时长限制。

> 在历史提示词里边默认的星没有打上，不知道哪个是当前默认的。

> 在每一条历史提示词后面再加一个删除

## 2. 背景与根因

### 2.1 ⭐ 默认不显示的真凶

[short_video_service.py:1296-1318](short_video_service.py#L1296) `save_default_prompt` 内部调用
`save_prompt_history(root, text, stage=s)` 但**忽略返回的 need_confirm 状态**。
当历史已满 10 条且新默认不在其中时：

- `save_prompt_history` 不写入（返回 `need_confirm`）
- `default_prompt` 字段照常落盘
- 前端 `render_prompt_history_list` 用 `p.strip() == default_prompt.strip()` 找匹配 → 永远为 false
- ⭐ 标记永不显示

修复：`save_default_prompt` 改用 `confirm=True` 强制覆盖最旧，保证默认提示词一定进历史 → ⭐ 可显示。

### 2.2 删除按钮缺失

之前 `render_prompt_history_list` 每条只有「载入」按钮（[short_video_ui.py:166-177](short_video_ui.py#L166)）。
用户无法清理过期提示词。

## 3. 实现

### 3.1 「引用系统提示词」按钮文字

按钮内追加：

```html
🧭 引用系统提示词<span class="slirn-sv-muted">（可带入片段数和时长限制）</span>
```

`slirn-sv-muted` 类已存在（用于副文），略淡显示括号注释不抢主按钮权重。

### 3.2 ⭐ 默认修复（[short_video_service.py:1296-1322](short_video_service.py#L1296)）

```python
def save_default_prompt(repo_root, prompt, *, stage="stage3"):
    ...
    full[s][_PROMPT_DEFAULT_KEY] = entry
    _write_json_atomic(prompt_settings_path(root), full)
    # REQ-20261016-128：默认必须能进历史（否则前端 ⭐ 永不显示），
    # 列表满时强制覆盖最旧（confirm=True），不让默认丢失。
    save_prompt_history(root, text, confirm=True, stage=s)
    return entry
```

### 3.3 删除按钮（[short_video_ui.py:166-185](short_video_ui.py#L166)）

```python
items.append(
    f'<div class="slirn-stage3-hist-item">'
    f'<div class="slirn-stage3-hist-head">'
    ...
    f'<button ... data-action="sv-stage3-load-prompt" ...>载入</button>'
    # REQ-20261016-128：每条历史后加 🗑 删除
    f'<button type="button" class="slirn-btn-mini slirn-btn-mini-danger" '
    f'data-action="sv-stage3-delete-prompt" data-stage="stage3" '
    f'data-prompt="{esc(p)}" '
    f'title="从历史列表删除这一条（若是默认会同时清除默认标记）">🗑 删除</button>'
    ...
)
```

### 3.4 新增 `delete_prompt_history` 服务函数

[short_video_service.py:1321-1349](short_video_service.py#L1321)：

```python
def delete_prompt_history(repo_root, prompt, *, stage="stage3") -> dict:
    """从历史列表移除指定 prompt；若是默认则同步清空 default_prompt。"""
    ...
    return {"removed_default": bool, "history": [...]}
```

### 3.5 新增端点

[app.py:10153-10174](app.py#L10153)：

```python
@app.app.post("/slirn/api/short_video_prompt_history_delete")
async def short_video_prompt_history_delete(body: dict = Body(default_factory=dict)):
    """REQ-20261016-128：从历史列表删除一条；若是默认则同步清空默认。"""
    ...
```

### 3.6 JS 派发器（[short_video.js:696-714](static/short_video.js#L696)）

```javascript
function stage3DeletePrompt(btn) {
  if (!btn) return;
  var prompt = btn.getAttribute('data-prompt') || '';
  var stage = btn.getAttribute('data-stage') || 'stage3';
  if (!prompt) return toast('该条目无 prompt 文本', 'error');
  if (!window.confirm('从历史列表删除这条提示词？\n（若是默认将同时清除默认标记）')) return;
  postJSON(API + '/short_video_prompt_history_delete', {
    prompt: prompt, stage: stage
  }).then(function(r) {
    if (!r || !r.ok) return toast((r && r.error) || '删除失败', 'error');
    _refreshStage3History(r.history_html);
    toast(r.toast || '已删除');
  });
}
```

路由 1 行：`if (action === 'sv-stage3-delete-prompt') return stage3DeletePrompt(btn);`

## 4. 改动清单

| 文件 | 改动 |
|---|---|
| `slirn_home/short_video_service.py` | `save_default_prompt` 改 + 新增 `delete_prompt_history` 函数 |
| `slirn_home/app.py` | 新增 `/slirn/api/short_video_prompt_history_delete` 端点 |
| `slirn_home/short_video_ui.py` | 「引用系统提示词」按钮文字 + `render_prompt_history_list` 加删除按钮 |
| `slirn_home/static/short_video.js` | `stage3DeletePrompt` 函数 + 路由 1 行 |
| `tests/test_short_video_stage3_prompt_history_delete.py` | +9 新测试（5 服务层 + 4 UI 层） |
| `tests/test_short_video_stage3_rework.py` | 1 旧测试改：列表已满时默认强制入列（旧行为「同步跳过」→ 新行为「强制覆盖最旧」） |

## 5. 测试（+9 / 改 1，全量 1315 passed）

### 新增

- `test_delete_prompt_history_removes_entry` — 删非默认 → history 少 1，default 不变
- `test_delete_prompt_history_clears_default_when_match` — 删默认 → history 少 1 + `default_prompt = None`
- `test_delete_prompt_history_noop_when_not_in_history` — 删不存在的 → 列表不变
- `test_delete_prompt_history_empty_prompt_raises` — 空 prompt → ShortVideoError
- `test_save_default_prompt_inserts_into_full_history` — 列表满 10 + 新默认不在 → 强制覆盖最旧，新默认入列
- `test_render_prompt_history_list_includes_delete_button` — 每条历史有删除按钮
- `test_render_prompt_history_list_star_matches_default` — ⭐ 默认 仍按文本相等标出
- `test_render_prompt_history_list_empty_state` — 无历史 → 占位文案，无删除按钮
- `test_stage3_analyze_prompt_use_system_button_has_parenthetical` — 按钮内含「（可带入片段数和时长限制）」

### 改

- `test_short_video_stage3_rework.py::test_prompt_settings_default_and_history` —
  旧断言「列表已满时设默认为新提示词 → 同步跳过」改为「强制覆盖最旧」

## 6. 验收

### 端到端（真实项目 20261003-001 / sv_20261004_072937_572eb5）

| 步骤 | 结果 |
|---|---|
| 点「设为默认」（填新提示词） | 新条目入列置顶 + ⭐ 默认标出 |
| 列表已满 10 条时再设默认 | 强制覆盖最旧（不再静默跳过） |
| 点某条「🗑 删除」+ confirm | 列表原地刷新 + 若是默认则 default_prompt = null |
| 「🧭 引用系统提示词（可带入片段数和时长限制）」可见 | 括号文字略淡（slirn-sv-muted 灰显） |

### 7861

- 重启加载新 Python 端点；CSS 未改无需 restart
- 1315/1315 pytest 全过

## 7. 已知边界

- **删除是物理删除，无法恢复**（用户原话「删就删」；如要可后续加「最近删除」二级回收站）
- **保存默认强制覆盖最旧**：用户已显式操作「设为默认」可接受（无 confirm 对话框）；如需更严的提示可后续在保存默认前弹「当前历史已满 10 条，将覆盖最旧一条」
- **Stage 4 提示词不同**（内容优化），用户明确「不要把它俩混在一起」——本期不动
