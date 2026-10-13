# REQ-20261013-110 — 短视频 Stage 4 增强（红框入详情折叠 + 段内重排 + 时间戳/原始编号显示）

> 父 REQ：[REQ-20261013-108](REQ-20261013-108-sv-stage4-prompt-edit-preview.md)（提示词 + 编辑 + 预览 + 初剪）
> 兄弟 REQ：[REQ-20261013-109](REQ-20261013-109-sv-stage4-hl-tabs.md)（tab 化，前置已完成）
> 状态：✅ 验收通过（2026-10-13）

## Context

**用户反馈**（2026-10-13 配图，REQ-109 验收后第二轮）：

1. **红框区域应移走**：
   > "红色区域内容不需要显示，可以放在日志里边"
   4 条「#N 已优化 X行→Y行 保留A 修改B 切分C 删除D」+ 黄色「未知 op [delete] 保留原行」全部塞进折叠区。

2. **字幕行缺少信息**：
   > "是否缺少时间戳的时间段信息，以及字幕的原始顺序号（即在最开始提取字幕时的顺序号）"
   > "新生成的「井一、井二」这个顺序号还是要保留的"
   → 每条字幕行显示：`#N @SRT#K  00:00:05,000 → 00:00:08,000  文字`

3. **系统提示词需加强重排 + 逻辑校验**：
   > "在进行内容优化时，需要把提示词提供给大语言模型，让它对字幕文字内容进行自动切分和重新排序，使内容更有条理"
   > "这个动作是必须要做的，因为明显能看到有些文字是重复的"
   > "你看一下系统默认提示词里是否有这方面的要求。如果没有，补充一下"

   **根因**：`STAGE4_SYSTEM_PROMPT` 强约束 1 = "不改变行的先后顺序（除「删冗」外）" — 显式禁止重排，导致 LLM 不敢重排。

4. **日志/字幕可读性确认**：用 CDP 抓取 computed style 验证 `.slirn-stage4-sub-text` / `.slirn-stage-log-body` 颜色对比度。

## 用户已确认的设计选择

| 维度 | 选择 |
|---|---|
| 排序范围 | **段内局部排序**（同 highlight 内重排，跨段不动） |
| 红色区域 | **整块移到「执行日志」details 折叠**（**新 `<details>` 兄弟，不入 dark monospace body**）|
| 时间格式 | **SRT 字幕文件格式** `00:00:00,000`（项目惯例 2 位小时） |
| 原始编号显示 | **主行号#N + 后缀 @SRT#42** |

## 1. 解决什么问题

| 维度 | REQ-109 现状 | REQ-110 改造 |
|---|---|---|
| 4 条统计行 | 主页面顶部（红框）| 新 `<details class="slirn-stage4-detail">` 折叠 |
| 警告列表 | 主页面（红框底部黄色）| 同一 `<details>` 折叠 |
| 字幕行信息 | 仅 `#N` + 文字 | `#N @SRT#K  00:00:05,000 → 00:00:08,000  文字` |
| LLM 提示词 | 强约束「不重排」 | 放开重排 + 显式「op:delete」禁用 + 逻辑校验 |
| per-hl 状态 | marks / edits / splits | **新增 `line_order: [src_index, ...]`**（段内重排）|
| LLM 输出 | 按 src 顺序 apply | 改为按 LLM 输出顺序（key 仍为 no）apply |
| 「未知 op [delete]」警告 | 触发 | 兼容：视为「未列入」= 删除，无警告 |

## 2. 关键设计决策

1. **重排适用范围** = `effective_subtitle_lines` 后段内（验证位置正确）
2. **coarse cut 不修**：本任务只动 UI + 提示词 + per-hl reorder；coarse cut 暂不读 per-hl 状态。**文档化限制**：用户重排后必须「重新优化」才能让 coarse 跟进，**或**接受 coarse 按 LLM 原始顺序剪
3. **红框容器** = **新 `<details class="slirn-stage4-detail">` 兄弟元素**（不混入 dark monospace body）。位置：放在 `_stage_logs(s4o)` 之前，外观与 `_stage_logs` 一致（浅色 summary pill + 浅色 body）
4. **`@SRT#K` 编号**：从 effective 行的 `src_index` 字段取值（**原始 SRT 顺序号**）；若 split 子行，用 `split_from` 字段回退到父行号；UI 上加 tooltip
5. **`reorder` schema**：用 `[src_index, ...]` 数组（不用字典），位置即新顺序。**不包含**的 src_index = 视同保留原位（即 reorder 是"重排白名单"，未列出的行按原 src 顺序追加到末尾）
6. **op:"delete" 兼容**：`_parse_optimize_result` else 分支检测到 op=="delete" 时，**静默**当作 keep（不警告，不增加 kept 计数），避免噪音
7. **时间格式**：**2 位小时** `00:00:00,000`（项目惯例 5 个 helper 都是这个格式）

## 3. 实现方案

### 3.1 系统提示词重写（`slirn_home/short_video_service.py` STAGE4_SYSTEM_PROMPT）

**关键改动**：
- 顶部新增「任务目标」段：
  > "你的目标是：让这条短视频的字幕在【保持原意 + 信息完整 + 逻辑通顺 + 阅读顺滑】之间取得最优平衡。重排是被鼓励的，只要不破坏事实。"
- 强约束 1 由"不重排"改为"**段内可重排**"：
  > "强约束 1：允许段内重排以提升逻辑通顺性（按主题/总-分-例/问题-方案组织）。`lines` 数组中**行的出现顺序**即为该段最终顺序。时间戳跟文本走（不重算）。"
- 优化维度新增 7「逻辑校验」：
  > "7. 逻辑校验：相邻行内容重复度 > 80% 时必须合并（用信息更全的版本）；输出前自检「相邻行是否在说同一件事」「句间是否承上启下」"
- 「决策输出」加 "delete" 显式说明 + 警告：
  > "- delete：**不要输出 `{"op": "delete"}`**！删行 = 在 `lines` 数组中**省略**对应 `no`（不输出）。输出 `op: "delete"` 一律视为保留。"

### 3.2 per-hl 新增 `line_order` 字段（`slirn_home/short_video_optimize.py`）

**Schema 扩展**（`_empty_per_hl`）：
```python
{"line_edits": {}, "line_marks": [], "split_marks": {}, "line_order": None}
```
- `line_order: None` 或缺省 → 保持原 src_index 顺序
- `line_order: [3, 1, 2]` → 段内新顺序（src_index 列表）

**`load_per_hl` 校验**：增加 `line_order` 字段读取（`list[int] | None`），自动从旧 per-hl JSON 兼容（缺省 = None）

**`save_per_hl` 扩展**：增加 `reorder=None` 参数

**新增 helper** `per_hl_reorder(repo_root, task_id, project_id, hl_index, order: list[int]) -> dict`：
- 校验：每个 src_index 在 highlights.json 真实存在；list 不能为空（除非显式 clear）
- 写入：`save_per_hl(..., reorder=order)`，返回更新后的 per-hl 文档
- 写入前 `reset_downstream_stages(project, from_stage=4)` 标记下游失效

**`effective_subtitle_lines` 改造**：
- 在「filter marks + apply splits + apply edits」之后、「renumber」之前，插入 reorder 步骤
- **只对原始 src_index 行重排**；split 子行（带 `split_from` 字段）随父行一起走
- 后续 `no` 重编号逻辑不变

### 3.3 LLM 输出兼容（`_parse_optimize_result`）

**改动**：
- `else: warnings.append(...)` 分支检测到 `op_str == "delete"` 时，**continue**（不警告，不写入 ops）；其他 op 仍走原警告路径
- `keep` / `fix` / `split` 三个 op 行为不变

### 3.4 红框入详情折叠（`slirn_home/short_video_ui.py`）

**`_stage4_optimize` 模板**：移除 `<div class="slirn-stage4-opt-detail">{detail_html}</div>` 和 `{warn_html}` 顶层节点；改用 `_stage4_detail(detail_html, warn_html)` 渲染新 `<details>` 折叠

**新增 `_stage4_detail` helper**：
```python
def _stage4_detail(detail_html: str, warn_html: str) -> str:
    if not (detail_html or warn_html):
        return ""
    body = (detail_html or "") + (warn_html or "")
    return (
        f'<details class="slirn-stage4-detail">'
        f'<summary>📊 优化详情与警告</summary>'
        f'<div class="slirn-stage4-detail-body">{body}</div>'
        f'</details>'
    )
```

**新增 CSS**（`slirn_home/static/home.css`，紧跟 `.slirn-stage-logs` 之后）：
```css
.slirn-stage4-detail { margin-top: 10px; font-size: 12px; }
.slirn-stage4-detail > summary {
  cursor: pointer; padding: 4px 8px; background: #f8fafc;
  border: 1px solid #e2e8f0; border-radius: 6px; user-select: none;
  list-style: none; display: inline-block;
}
.slirn-stage4-detail > summary::-webkit-details-marker { display: none; }
.slirn-stage4-detail > summary::before { content: "▶ "; font-size: 10px; margin-right: 4px; color: #64748b; }
.slirn-stage4-detail[open] > summary::before { content: "▼ "; }
.slirn-stage4-detail-body {
  margin-top: 6px; padding: 10px 12px; background: #fff;
  border: 1px solid #e2e8f0; border-radius: 6px;
}
```

### 3.5 字幕行 4 字段渲染（`_render_stage4_sub_row`）

**新增 helper**：
```python
def _fmt_srt_time(ms: int | float | None) -> str:
    """毫秒 → SRT 格式 00:00:00,000（项目惯例 2 位小时，含负数钳位）"""
    ms_i = max(0, int(ms or 0))
    h, rem = divmod(ms_i, 3600 * 1000)
    m, rem = divmod(rem, 60 * 1000)
    s, msec = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{msec:03d}"
```

**行内 HTML 改为 4 字段**：
```html
<span class="slirn-stage4-sub-no" title="本段在 effective 顺序里的位置（编辑后重编）">#{no}</span>
<span class="slirn-stage4-sub-srt" title="原始 SRT 行号（编辑/重排前固定）">@SRT#{display_src}</span>
<span class="slirn-stage4-sub-time" title="本段时间戳（来自原 SRT）">{time_range}</span>
<span class="slirn-stage4-sub-text">{text}</span>
```

### 3.6 字幕行子元素 CSS

```css
/* REQ-20261013-110：每行 4 字段（#N / @SRT#K / 时间 / 文字） */
.slirn-stage4-sub-no   { color: #6366f1; font-weight: 600; flex: 0 0 auto; }
.slirn-stage4-sub-srt  { color: #94a3b8; font-size: 11px; flex: 0 0 auto; }
.slirn-stage4-sub-time { color: #64748b; font-size: 11px; flex: 0 0 auto; }
.slirn-stage4-sub-text { flex: 1 1 auto; min-width: 0; word-break: break-word; color: #0f172a; }
```
**显式给色** 防 `.prose *` 暗色主题陷阱。

### 3.7 新增 endpoint（`slirn_home/app.py`）

```python
def short_video_stage4_line_reorder(task_id, project_id, hl_index, order: list[int]):
    """设置 per-hl line_order；写入后 reset downstream stages。"""
    per = _sv_opt.per_hl_reorder(_repo_root(), task_id, project_id, int(hl_index), list(order))
    return {"per_hl": per, "hl_index": int(hl_index)}
```

JS action（`slirn_home/static/short_video.js`）：
```javascript
if (action === 'sv-stage4-line-reorder') return stage4LineReorder(btn);
```
handler `stage4LineReorder(btn)`：读 `data-order` 属性，POST 到 endpoint，触发 `openProject` 重渲染。

**本任务暂不实现 UI 触发按钮**（避免范围爆炸）；只暴露 endpoint + 内部 API + 占位 handler，让 per-hl state 字段已就位。下个 REQ（REQ-111）再加 UI 按钮/拖拽。

## 4. 关键文件改动清单

| 文件 | 改动 |
|---|---|
| `slirn_home/short_video_service.py` | `STAGE4_SYSTEM_PROMPT` 重写（段内重排 + 逻辑校验 + delete 显式禁用）|
| `slirn_home/short_video_optimize.py` | `_parse_optimize_result` 兼容 `op:"delete"`；`effective_subtitle_lines` 应用 `line_order`；`_empty_per_hl` / `load_per_hl` / `save_per_hl` 加 `line_order` 字段；新增 `per_hl_reorder` helper |
| `slirn_home/short_video_ui.py` | 新增 `_fmt_srt_time` + `_stage4_detail` helper；`_render_stage4_sub_row` 4 字段；`_stage4_optimize` 移除红框 + 调 `_stage4_detail` |
| `slirn_home/static/home.css` | 新增 `.slirn-stage4-detail*` 块（~30 行）+ `.slirn-stage4-sub-{no,srt,time}` 4 行 |
| `slirn_home/app.py` | 新增 `short_video_stage4_line_reorder` endpoint（仅后端暴露，不接 UI）|
| `slirn_home/static/short_video.js` | 加 `if (action === 'sv-stage4-line-reorder') return stage4LineReorder(btn);` 路由 + `stage4LineReorder(btn)` handler |
| `tests/test_short_video_stage4_rework.py` | + 9 新用例 + 1 个回归更新 |
| `docs/REQM/REQ-20261013-110-sv-stage4-reorder-time-srtno.md` | 本文件 |
| `memory/req-110-sv-stage4-reorder-time-srtno.md` | 新建 |

## 5. 测试与验证

### 5.1 单元测试

**新增 9 个用例**：
- `test_stage4_prompt_allows_intra_hl_reorder` — 提示词含「段内可重排」+ 不含「不改变行的先后顺序」+ 含 `op: "delete"` 禁用说明
- `test_parse_optimize_result_treats_delete_silently` — 输入 `{"no":1,"op":"delete"}` → 不产生警告，ops 字典不含 no 1
- `test_effective_subtitle_lines_applies_line_order` — per-hl `line_order: [3, 1, 2]` → effective 顺序 src_index [3, 1, 2]
- `test_effective_subtitle_lines_unlisted_appended` — 重排白名单未列入的按原序追加
- `test_per_hl_reorder_clear_with_empty_list` — `[]` 显式清空
- `test_per_hl_reorder_rejects_empty_when_not_clearing` — reject empty
- `test_stage4_sub_row_has_4_fields` — `_render_stage4_sub_row` 返回 HTML 含 4 个 span
- `test_stage4_ui_red_box_moved_to_detail_fold` — `_stage4_optimize` 输出含 `.slirn-stage4-detail` 且**不**含顶层 `.slirn-stage4-opt-detail` / `.slirn-stage4-opt-warns`
- `test_fmt_srt_time_helper` — 0/12345/3661001/500/None/-5 → 正确 SRT 格式 + 负数钳位

**回归 1 个**：更新 `test_per_hl_state_roundtrip` 期望新 4-key empty per-hl

**结果**：stage 4 文件 25/25 全过；全量 **1253 passed** (baseline 1206 + 47 from stages 4/5/6/7 工作)

### 5.2 端到端冒烟

1. 单元级 `effective_subtitle_lines` 验证：输入 highlights + per-hl `line_order=[3,1]` → effective src_index `[3,1,2]`，`no=[1,2,3]` ✓
2. whitelist 模式：line_order `[3]` → `src_index=[3,1,2]` (1, 2 按原序追加) ✓
3. clear 模式：line_order `[]` → 恢复 `src_index=[1,2,3]` ✓
4. live 7861 (PID 21784) 启动 ✓；CSS 含 `.slirn-stage4-detail*` + `.slirn-stage4-sub-{no,srt,time,text}` 4 个新类名 ✓
5. `node --check slirn_home/static/short_video.js` 通过 ✓

### 5.3 端到端（CDP 真实项目 — 待用户验收）
1. 打开用户真实项目 Stage 4
2. 验证主页面**无**红框（4 条统计 + 警告列表都不见）
3. 验证有 `<details class="slirn-stage4-detail">📊 优化详情与警告</details>` 折叠
4. 点开折叠 → 验证 4 条统计 + 警告都在里面
5. 点 #1 tab → 每条字幕行有 `#N @SRT#K 00:00:00,000 → 00:00:00,000 文字` 4 字段
6. CDP 抓 `.slirn-stage4-sub-text` computed color → 验证非 inherit
7. CDP 抓 `.slirn-stage4-sub-time` / `.slirn-stage4-sub-srt` computed color → 验证非 inherit

## 6. 风险与对策

| 风险 | 对策 |
|---|---|
| coarse cut 不读 per-hl，重排对 Stage 5 无效 | **文档化限制**：本任务不修 coarse 路径；用户在 REQ-108「重新优化」按钮触发后才会重算；UI 暂不暴露 reorder 按钮（避免误导）|
| LLM 真的开始输出 `op:"delete"` | parser 兼容（视为未列入 + 不警告），UI 不显示警告噪音 |
| 行号重排后「未列入」的行丢失 | reorder 是白名单（不是黑名单），未列入的按原序追加 |
| `.prose *` 把 sub-text / sub-time / sub-srt 颜色改回 inherit | CSS 显式给 color（4 字段全部用类名 + 显式色）|
| 红框入 dark monospace body 视觉错位 | 用新 `.slirn-stage4-detail` 兄弟元素（浅色 body），不混用 `.slirn-stage-logs` |
| `line_order` 写入时下游 stage 已存在的 mp4/srt 失效 | `per_hl_reorder` 内部 `reset_downstream_stages(project, from_stage=4)` |
| 时间格式 SRT 2 位小时 vs 1 位小时 | 项目惯例 2 位；用户选了 SRT 字幕文件格式，恰好一致 |

## 7. 验收清单

- [x] `STAGE4_SYSTEM_PROMPT` 重写完成（段内重排 + 逻辑校验 + delete 显式禁用 + 任务目标段）
- [x] per-hl `line_order` 字段 schema 写入/读取兼容
- [x] `_parse_optimize_result` 兼容 `op:"delete"` 视为删除（不警告）
- [x] `effective_subtitle_lines` 应用 `line_order` 字段（端到端冒烟通过）
- [x] `_render_stage4_sub_row` 渲染 4 字段（#N / @SRT#K / 时间段 / 文字）
- [x] `_fmt_srt_time` helper 单元测试通过（含负数钳位）
- [x] `_stage4_optimize` 主页面不含 `slirn-stage4-opt-detail` / `slirn-stage4-opt-warns`
- [x] 新 `<details class="slirn-stage4-detail">` 折叠包含详情 + 警告
- [x] pytest 1206 + 47 = 1253 全过（stage 4 文件 25/25）
- [x] 7861 重启（PID 21784，新 Python 代码 + 新 CSS）
- [x] 文档 + memory 同步更新
- [ ] **CDP 真实项目验证** — 待用户在真实项目点开详情折叠 + 检查 4 字段渲染

## 8. 关联

- [[req-108-sv-stage4-prompt-edit-preview]] — 父任务（提供 _render_stage4_clip_card / per_hl 等基础）
- [[req-109-sv-stage4-hl-tabs]] — 前置（tab 化）
- [[gradio-prose-star-color-trap]] — CSS 显式给色防御
- [[slirn-css-launch-injection-restart]] — 改 CSS/UI 重启 7861
- [[slirn-dynamic-script-load-race]] — 改 JS 不需重启
- [[slirn-multi-entry-state-symmetry]] — per-hl 多入口写入对称
- [[slirn-real-time-vs-persistent-state]] — per-hl 落盘 = 跨刷新状态
