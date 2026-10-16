# REQ-20261016-138 — 移除 Stage 6「📝 切换源」按钮

> 状态：✅ 已完成（2026-10-16）
> 父 REQ：[REQ-133 SV Stage6 Row Edit](REQ-20261016-133-sv-stage6-row-edit.md)（按钮原始引入）
> 父 REQ：[REQ-137 SV Popup First Open Positioning](REQ-20261016-137-sv-popup-first-open-positioning.md)（同会话前置）
> 父 REQ：[REQ-136 SV Stage6 Extract Subtitle Inline](REQ-20261016-136-sv-stage6-extract-subtitle-inline.md)
> 范围：删除按钮 + JS 函数 + 路由；保留隐藏 textarea（后端数据源）

## 1. 需求（用户原话）

> 这个按钮的名称也没有变啊，并且这里面有个切换源是啥意思？为什么要切换源呢？
> 你先说明白有哪些源？第一个短视频不就一个源吗
> 字幕直接显示在下面，为什么要切换？

**直接诉求**：删除「📝 切换源」按钮。

## 2. 背景说明（用户问的「切换源」是什么）

「📝 切换源」按钮来自 [REQ-133](REQ-20261016-133-sv-stage6-row-edit.md)（Stage 6 per-row 编辑），**不是切换数据源**（短视频确实只有 1 个输入视频），而是**切换视图**：

| 视图 | 默认 | 内容 |
|---|---|---|
| **行编辑视图**（per-row）| ✅ 显示 | 每行 SRT 一个卡片 + 🗑 / ✂️ / ✏️ 三按钮 |
| **SRT 源文视图**（textarea）| ❌ 隐藏 | 完整 SRT 文本，可直接编辑任意位置 |

实现：[short_video.js:2015-2030](slirn_home/static/short_video.js#L2015)（已删除）只做 `wrap.style.display = 'none'` ↔ `ta.style.display = ''` 切换。

**但用户说得对**：命名「切换源」字面意思是「切换数据源」，与实际语义（切换视图）不符。per-row 编辑器已覆盖 99% 场景，源文视图极少用到。

## 3. 关键设计决策

| 维度 | 选型 | 理由 |
|---|---|---|
| 按钮 | 删除 | 用户原话「字幕直接显示在下面，为什么要切换？」 |
| 隐藏 textarea | 保留 | `stage5Save` 后端端点从 textarea 读 SRT；per-row DOM 通过 `stage6RebuildSrt` 回写 |
| Per-row 编辑器 | 保留 | REQ-133 主功能，行级 🗑/✂️/✏️ 不破 |
| JS 函数 `stage6ToggleSource` | 删除 | 死代码，避免回归 |
| JS 路由 `sv-stage6-toggle-source` | 删除 | 同上 |
| 其他 actions | 不动 | `sv-stage5-asr` / `sv-stage5-save` / `sv-stage6-optimize` / `sv-stage5-preview` 全部保留 |

## 4. 关键文件改动

| 文件 | 改动 | 行数 |
|---|---|---|
| [slirn_home/short_video_ui.py:1397-1398](slirn_home/short_video_ui.py#L1397) | 删除 2 行「📝 切换源」按钮 | -2 / 0 |
| [slirn_home/static/short_video.js:2014](slirn_home/static/short_video.js#L2014) | 删除 `stage6ToggleSource` 函数 | -17 / 0 |
| [slirn_home/static/short_video.js:2710](slirn_home/static/short_video.js#L2710) | 删除 `sv-stage6-toggle-source` 路由 | -1 / 0 |
| [tests/test_short_video_stage6_remove_toggle_source.py](tests/test_short_video_stage6_remove_toggle_source.py) | 9 个新测试 | +200 |
| [tests/test_short_video_stage6_row_edit.py](tests/test_short_video_stage6_row_edit.py) | 4 个旧测试断言反转 | +5 / -5 |
| [tests/test_short_video_stage6_hl_tabs.py](tests/test_short_video_stage6_hl_tabs.py) | 1 个旧测试断言反转 | +2 / -1 |

1380 → 1389 pytest 全过（+9）。

## 5. 验收

- [x] 1389/1389 pytest 全过
- [x] 7861 重启加载新 Python 代码
- [x] 静态分析：HTML 不再含 `data-action="sv-stage6-toggle-source"`
- [x] 静态分析：HTML 不再含「📝 切换源」文字
- [x] 静态分析：JS 不再有 `function stage6ToggleSource`
- [x] 静态分析：JS 路由表不再含 `action === 'sv-stage6-toggle-source'`
- [x] 隐藏 textarea（`slirn-stage5-srt`）保留 — 后端数据源不破
- [x] Per-row 编辑器（`slirn-stage6-srt-rows-wrap`）保留 — REQ-133 不破
- [x] 其他 actions（提取/保存/查看/预览/LLM 优化）保留

## 6. 用户操作

按 [memory:slirn-dynamic-script-load-race](slirn-dynamic-script-load-race.md)，**JS 改动不需重启 7861**，但 **Python 改动需重启**（7861 PID 19692 已重启）。用户刷新页面即可看到：
- Stage 6 面板 actions 栏只有 5 个按钮：提取短视频字幕 / 保存修正 / 查看 ASR 字幕 / 预览 / LLM 优化
- 字幕行直接显示在按钮下方，per-row 编辑器（🗑/✂️/✏️）可见
- 隐藏 textarea 仍在（保存时数据源）

## 7. 关键教训

**「界面文案歧义是 UX bug」** —— 「源」字歧义：可以是「数据源」也可以是「视图源」。本应叫「📝 高级编辑」或「📝 切到源文」更直白；但用户选择直接删除（per-row 编辑器已够用），最简方案。

**「per-row 编辑器覆盖 99% 场景，隐藏 textarea 留作后端数据源」** 是 Stage 6 编辑功能的最佳实践 — 用户只看到有用的 UI，后端拿完整 SRT 用于保存。

## 8. 关联

- [[req-133-sv-stage6-row-edit]] — 按钮原始引入
- [[req-136-sv-stage6-extract-subtitle-inline]] — 同会话前置
- [[req-137-sv-popup-first-open-positioning]] — 同会话前置
- [[slirn-dynamic-script-load-race]] — JS 改动不需重启
