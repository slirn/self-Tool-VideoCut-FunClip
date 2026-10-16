# REQ-20261016-136 — Stage 6「提取短视频字幕」按钮改名 + ASR 结果 inline 显示

> 状态：🚧 进行中（2026-10-16）
> 父 REQ：[REQ-133 SV Stage6 Row Edit](REQ-20261016-133-sv-stage6-row-edit.md)（per-row 编辑）
> 父 REQ：[REQ-135 SV Stage6 Tabs + Video Preview](REQ-20261016-135-sv-stage6-tabs-and-video-preview.md)（tab 化）
> 父 REQ：[REQ-104 SV Stage6 Optimize Port](REQ-20261016-104-sv-stage6-optimize-port.md)（Stage 6 端点）
> 范围：Stage 6 面板 - 「重新 ASR」按钮改名 + 提取后 ASR SRT 立即 inline 显示

## 1. 需求（用户原话）

> 如图所示，把红框所框的重新ASR改成提取短视频字幕，提取之后，把字幕显示在按钮的下面，这样提取之后直接就能看到，并且可以在下面这个区域进行修改，修改的功能包括删除陈星切分、修改文字，这个功能跟内容优化中对字幕的操作一样

> 注：「陈星」是「陈星切分」口误 / 输入法误触，结合上下文应为「**行切分**」（切分当前 SRT 行为多段）。

### 拆解

| # | 需求 | 状态 |
|---|---|---|
| 1 | 把「重新 ASR」按钮改成「提取短视频字幕」 | ❌ 当前是「重新 ASR」，命名不直观 |
| 2 | 提取后把 SRT 显示在按钮下面（inline） | ❌ 当前 bug：老项目 `refined_srt` 字段为空时只显示「点「重新 ASR」生成字幕」占位 |
| 3 | 提取后直接能看见 SRT | ❌ 同上 |
| 4 | 在 SRT 区域可修改 | ✅ 已有（REQ-133 per-row 编辑） |
| 5 | 编辑功能：删除行 / 行切分 / 修改文字 | ✅ 已有（🗑️/✂️/✏️ 三按钮） |
| 6 | 编辑体验与「内容优化」（Stage 4）一致 | ✅ REQ-133 仿 Stage 4 风格 |

### 现状 bug 根因

[short_video_ui.py:1326-1338](slirn_home/short_video_ui.py#L1326) 只在 `refined_name` 不为空时读 SRT 文件并渲染 per-row 编辑器：

```python
if refined_name:
    full_path = srt_path / "..." / "stage5" / refined_name
    if full_path.is_file():
        txt = full_path.read_text(...)
        srt_rows_html = _render_stage6_srt_rows(txt, idx)
```

问题：老项目（或 ASR 中断、refined_srt 写入失败）的 `hl["refined_srt"]` 字段为空，但 `hl["asr_srt"]` 已经有值。**用户点击「重新 ASR」成功后，看到的只是占位提示，ASR 结果藏在「查看 ASR 字幕」弹窗里**，体验非常糟糕。

修复方向：per-row 编辑器**优先读 refined_srt，没有则 fallback 读 asr_srt**。两者都是 SRT 文件，结构相同（funasr 输出 + 默认 refined 等于 ASR）。

## 2. 关键设计决策

| 维度 | 选型 | 理由 |
|---|---|---|
| 按钮名 | 「重新 ASR」→「**提取短视频字幕**」 | 用户原话；动作更直白（从短视频里提取字幕）；避免「ASR」术语吓退非技术用户 |
| ASR SRT fallback | `refined_name` 为空时 fallback `asr_name` | 兼容老项目 / refined_srt 写入异常场景；funasr 输出直接可用 |
| 文件读取顺序 | `refined_srt` 优先 → `asr_srt` 兜底 | refined 是用户修正过的，更权威；ASR 是原始结果，refined 缺失时作为最佳替代 |
| 提示文案 | 全部统一为「点「提取短视频字幕」生成字幕」 | 按钮名和占位文案必须一致 |
| JS toast | 「重新 ASR 中…」→「**提取短视频字幕 中…**」 | 用户点击按钮后立刻看到的反馈，必须用新名 |
| 编辑器 | 沿用 REQ-133 per-row 编辑器 | 不重写；JS 端 `sv-stage6-line-edit/resplit/toggle` 三按钮即满足「修改/切分/删除」 |
| 隐藏 textarea | 保留，placeholder 同步改名 | `stage5Save` 端点从 textarea 读 SRT，逻辑不动 |
| 后端端点 | 不动 | `/short_video_stage5_run` 内部实现稳定，行为不变；只改 UI 文字 |

## 3. 关键文件改动

| 文件 | 改动 | 行数（估） |
|---|---|---|
| [slirn_home/short_video_ui.py:1303](slirn_home/short_video_ui.py#L1303) | `empty_srt6_div` 文案改 | +1 / -1 |
| [slirn_home/short_video_ui.py:1326-1338](slirn_home/short_video_ui.py#L1326) | `refined_name` 为空时 fallback `asr_name` | +8 / -1 |
| [slirn_home/short_video_ui.py:1386](slirn_home/short_video_ui.py#L1386) | 按钮文字「重新 ASR」→「提取短视频字幕」 | +1 / -1 |
| [slirn_home/short_video_ui.py:1403](slirn_home/short_video_ui.py#L1403) | textarea placeholder 改名 | +1 / -1 |
| [slirn_home/short_video_ui.py:1213](slirn_home/short_video_ui.py#L1213) | `_render_stage6_srt_rows` 空态文案 | +1 / -1 |
| [slirn_home/short_video_ui.py:1413](slirn_home/short_video_ui.py#L1413) | `empty_hint6_div` 文案 | +1 / -1 |
| [slirn_home/short_video_ui.py:1424](slirn_home/short_video_ui.py#L1424) | 阶段描述 | +1 / -1 |
| [slirn_home/static/short_video.js:1882](slirn_home/static/short_video.js#L1882) | toast「重新 ASR 中…」→「提取短视频字幕 中…」 | +1 / -1 |
| [slirn_home/static/short_video.js:1900](slirn_home/static/short_video.js#L1900) | toast 错误文案 | +1 / -1 |
| [tests/test_short_video_stage6_extract_subtitle.py](tests/test_short_video_stage6_extract_subtitle.py) | 新增 7 个测试 | +150 |

预计 1364 → 1371 pytest（+7 测试）。

## 4. 关键代码改动

### A. `slirn_home/short_video_ui.py`

**A.1** L1303 占位文案：

```python
# REQ-20261016-136：按钮名统一
empty_srt6_div = '<div class="slirn-sv-muted">点「提取短视频字幕」生成字幕</div>'
```

**A.2** L1213 `_render_stage6_srt_rows` 空态：

```python
return ('<div class="slirn-sv-muted" data-empty="1">'
        '（暂无字幕行 — 点「提取短视频字幕」生成）</div>')
```

**A.3** L1326-1338 SRT fallback（**核心修复**）：

```python
# REQ-20261016-136：refined 缺失时 fallback 读 ASR SRT
srt_inline = ""
srt_rows_html = ""
srt_source = refined_name or asr_name  # 优先 refined，没有用 asr
if srt_source:
    srt_path = Path(__file__).resolve().parent.parent
    full_path = srt_path / "tasks" / task_id / "short_video" / project_id / "stage5" / srt_source
    if full_path.is_file():
        try:
            txt = full_path.read_text(encoding="utf-8", errors="replace")
            srt_inline = esc(txt[:3000])
            srt_rows_html = _render_stage6_srt_rows(txt, idx)
        except Exception:
            srt_inline = ""
```

**A.4** L1386 按钮文字：

```python
f'<button class="slirn-btn-mini" data-action="sv-stage5-asr" data-clip-index="{idx}">提取短视频字幕</button>'
```

**A.5** L1403 textarea placeholder：

```python
f'style="display:none;" placeholder="点「提取短视频字幕」后再编辑">{srt_inline}</textarea>'
```

**A.6** L1413 `empty_hint6_div`：

```python
empty_hint6_div = (
    '<div class="slirn-sv-muted">尚无优化结果 — 每条 clip 依次「提取短视频字幕 → ✨ LLM 优化」</div>'
    if coarse_has_artifact else
    '<div class="slirn-sv-muted">先完成 Stage 5 粗剪</div>'
)
```

**A.7** L1424 阶段描述：

```html
<div class="slirn-sv-muted">每条独立处理：先提取短视频字幕 → LLM 优化 → 行级采纳/拒绝/编辑 → 保存</div>
```

### B. `slirn_home/static/short_video.js`

**B.1** L1882 `stage5Asr` toast：

```javascript
toast('提取短视频字幕 中…');
```

**B.2** L1900 `stage5Save` 错误 toast：

```javascript
if (!srt.trim()) return toast('字幕为空，请先编辑或提取字幕', 'error');
```

## 5. 测试覆盖（`tests/test_short_video_stage6_extract_subtitle.py`）

| 测试 | 验点 |
|---|---|
| `test_extract_subtitle_button_label` | 按钮文字 =「提取短视频字幕」（不再有「重新 ASR」） |
| `test_extract_subtitle_button_keeps_data_action` | 按钮的 `data-action="sv-stage5-asr"` 不变（避免回归） |
| `test_inline_srt_falls_back_to_asr_when_no_refined` | 老项目（只有 `asr_srt`，`refined_srt` 空）能渲染 per-row 编辑器 |
| `test_inline_srt_prefers_refined_over_asr` | 两者都有时用 refined（用户编辑过的最权威） |
| `test_inline_srt_handles_missing_files_gracefully` | 路径都指向不存在的文件时不报错，显示空态 |
| `test_empty_placeholder_mentions_extract_subtitle` | 占位文案含「提取短视频字幕」（不再有「重新 ASR」） |
| `test_textarea_placeholder_mentions_extract_subtitle` | 隐藏 textarea 的 placeholder 同步改名 |
| `test_js_toast_text_uses_extract_subtitle` | JS 端 toast「重新 ASR 中…」已改成「提取短视频字幕 中…」 |

预计 1364 → 1372 pytest（+8 测试）。

## 6. 验收

- [ ] 1372/1372 pytest 全过
- [ ] 7861 重启加载新文案
- [ ] 真实项目 `sv_20260930_234159_86a2fa` 验证：
  - 按钮文字变「提取短视频字幕」
  - 当前为占位（refined_srt 为空）的 clip，点按钮 → 看到 ASR SRT 立即 inline 显示
  - 已有 refined_srt 的 clip → 显示 refined SRT（行为不变）
  - per-row 编辑（🗑/✂️/✏️）仍可用
  - 批量替换 / LLM 优化 / 保存 / 视频预览 / tab 切换 全部不回归

## 7. 不做的事

- ❌ 不改后端 `/short_video_stage5_run` 端点（只改 UI 文字）
- ❌ 不动 per-row 编辑器 JS（`sv-stage6-line-edit/resplit/toggle` 已满足需求）
- ❌ 不动 hidden textarea 作为后端数据源（`stage5Save` 端点读 textarea 的逻辑稳定）
- ❌ 不改 Stage 3/4/5/7 任何文案
- ❌ 不动 tab 化 / 视频预览（REQ-135）
- ❌ 不动 _stage5_default_srt_path / _stage5_refined_srt_path 落点

## 8. 关联

- [[req-133-sv-stage6-row-edit]] — per-row 编辑器（前置）
- [[req-135-sv-stage6-tabs-and-video-preview]] — tab 化（前置）
- [[req-104-sv-stage6-optimize-port]] — Stage 6 端点
- [[req-090-combo-debug-panel]] — 调试面板（不受影响）
- [[slirn-css-launch-injection-restart]] — 文案改不需重启（只是字符串）；但 Python 改动需重启
