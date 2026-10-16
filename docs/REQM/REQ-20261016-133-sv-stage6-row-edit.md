# REQ-20261016-133 — Stage 6 per-row 编辑（仿 Stage 4 风格）

> 状态：✅ 已完成（2026-10-16）
> 范围：仅 Stage 6 字幕区域（Stage 5 / 7 / 长视频工作台不动）

## 1. 需求（用户原话）

> 第六阶段，字幕优化，页面没有功能按钮，无法执行优化，优化的过程是把前面生成的多段短视频针对每一个分别生成新的字幕，然后对字幕可以进行删除、重新切分、修改内容等，操作方法跟前面的类似

三件事：
1. **针对每个 clip 重新生成字幕** — 已有「重新 ASR」按钮
2. **对字幕可删/切/改** — 本次实现（per-row UI）
3. **操作方法跟前面类似** — 仿 Stage 4 风格（✏️/✂️/🗑️ 3 按钮）

## 2. 关键设计决策

| 维度 | 选型 | 理由 |
|---|---|---|
| UI 形式 | per-row 列表（每行 4 字段 + 3 按钮） | 仿 Stage 4；视觉对齐；操作直接 |
| 数据源 | 隐藏 textarea 保存 SRT 源 | 复用现有 `/short_video_stage5_save` 端点（无需新后端） |
| 保存路径 | 客户端先 `stage6RebuildSrt()` 把行状态写回 textarea，再 POST 现有端点 | 行操作（edit/toggle/resplit）只改 DOM，提交时统一回写 |
| 切分算法 | (end-start)/N 等分（最后段用原 end） | 简单可预测；不引第三方 |
| 切换视图 | 「📝 切换源」按钮 | 高级用户仍可编辑 raw SRT |
| 源文回填 | 切分后行号用 `segNo*1000+i` 占位 | 保存时 stage6RebuildSrt 按出现顺序重编为连续 1..N |

## 3. 关键文件改动

| 文件 | 改动 | 行数 |
|---|---|---|
| [short_video_ui.py:1201-1253](slirn_home/short_video_ui.py#L1201) | 新增 `_render_stage6_srt_rows` + 替换 textarea 为 row 列表 + 「切换源」按钮 | +21 / -1 |
| [home.css:6520-6582](slirn_home/static/home.css#L6520) | 新增 `.slirn-stage6-srt-rows-wrap` / `row` / `row-deleted` 样式 | +58 |
| [short_video.js:1891-1909](slirn_home/static/short_video.js#L1891) | stage5Save 入口先 `stage6RebuildSrt(index)` | +3 |
| [short_video.js:1906-2060](slirn_home/static/short_video.js#L1906) | 新增 5 个函数（Edit/Toggle/Resplit/ToggleSource/RebuildSrt） + `_fmtSrtTime` | +154 |
| [short_video.js:2694-2700](slirn_home/static/short_video.js#L2694) | 路由派发 4 个新 action | +5 |
| [tests/test_short_video_stage6_row_edit.py](tests/test_short_video_stage6_row_edit.py) | 10 个新测试 | +164 |

## 4. 测试

- `test_render_stage6_srt_rows_returns_3_rows_for_3_entries` — 解析 3 条 SRT → 3 行
- `test_render_stage6_srt_rows_has_3_buttons_per_row` — 每行 3 按钮（edit/resplit/toggle）
- `test_render_stage6_srt_rows_empty_shows_placeholder` — 空 SRT → 「暂无字幕行」占位
- `test_render_stage6_srt_rows_preserves_text_and_time` — 文字 + 时间段保留
- `test_stage6_panel_contains_per_row_editor_and_hidden_textarea` — Stage 6 面板含 wrap + 隐藏 textarea
- `test_stage6_panel_empty_highlights_shows_placeholder` — 无 SRT 文件 → 「点「重新 ASR」生成字幕」
- `test_short_video_js_dispatches_stage6_row_actions` — 4 个路由派发
- `test_short_video_js_defines_stage6_row_helpers` — 5 个函数存在
- `test_short_video_js_save_uses_rebuild` — stage5Save 必须先 RebuildSrt
- `test_css_stage6_row_styles_defined` — CSS 含 row 样式 + deleted 红删线

全量 1325 → 1335 passed。

## 5. 验收

- [x] 1335/1335 pytest 全过
- [x] 7861 重启加载 CSS
- [ ] 真实项目：Stage 6 字幕区显示 per-row 列表（替代 textarea）
- [ ] 每行 ✏️ → prompt 输入新文 → 行文本就地更新
- [ ] 每行 ✂️ → prompt「前半|后半」→ 切为 2 行
- [ ] 每行 🗑️ → 行变红 + line-through（再点恢复）
- [ ] 「📝 切换源」→ 显示 textarea，再点回 row 视图（行状态保留）
- [ ] 「保存修正」→ 调 RebuildSrt 把行状态写回 textarea → POST 端点成功

## 6. 不做的事

- ❌ 不动 Stage 4（per_hl 机制独立）
- ❌ 不新增后端端点（复用 `/short_video_stage5_save` 写 refined_srt）
- ❌ 不动 ASR/LLM 流程（用户原话只要求 UI 完善）
- ❌ 不做触摸事件（暂只支持鼠标 prompt）
- ❌ 不做撤销栈（用户没要求）
- ❌ 不做实时自动保存（保留显式「保存修正」按钮）

## 7. 关联

- [[req-104-sv-stage6-optimize-port]] — Stage 6 整体 UI 基础
- [[req-102-sv-coarse-refine-finalize-port]] — 短/长工作台结构对齐
- [[slirn-css-launch-injection-restart]] — CSS 改动必须重启 7861
- [[slirn-dynamic-script-load-race]] — 改 JS 不用重启 7861（但本次顺带重启保险）
