# DESIGN-20260918-049 — Pipeline 续跑 + Rigor 选择器 + 阶段状态回显

> REQ-20260918-049 — 在 REQ-047 v4（流程配置 + 自动执行）基础上的增强。
> v4 文档：[DESIGN-20260918-047-pipeline-auto-run.md](DESIGN-20260918-047-pipeline-auto-run.md)

## Context

REQ-047 v4（已完成）做了「全局停止阶段下拉」+「每阶段从本阶段起跑按钮」。
真机使用后用户反馈三个新需求：

> 在流程配置中，应该记录当前执行到哪个阶段。如果流程停止了，再次点击
> 运行流程时，应该从已经完成的阶段的下一阶段执行。另外，在流程配置的
> 字幕修订阶段，需要让用户选择分析严谨性级别。另外还有个问题是，运行
> 流程时能否看到当前运行阶段所执行的进度和时间吗？最好是能回显到页面
> 的对应阶段上。

### 用户已确认的设计决策

| 决策点 | 选 | 备 |
|---|---|---|
| 续跑按钮形态 | **智能单按钮（推荐）** | 双按钮 / 完全自动不变 |
| Rigor 选择器 | **复用现有 4 卡 picker** | 下拉 / 简单 radio |
| 状态回显形式 | **徽章 + 当前阶段百分比 + 已耗时（推荐）** | 仅徽章 / 仅百分比 |

## 设计

### 设计 1 — 续跑（智能单按钮）

#### 行为定义

`loadPanel(taskId)` 拿到 `r.history`（最近 10 条 summary），取最后一条：

| 情况 | 按钮文案 | since 参数 |
|---|---|---|
| 无 history / 首阶段就挂 | `▶ 从头跑` | `null` |
| 上次跑完部分（stages_done 是 5 阶段的前缀子集） | `▶ 续跑 (从「{nextStageLabel}」开始)` | `nextStageKey` |
| 全跑完（stages_done 含全部 5 个） | `▶ 从头跑` | `null` |
| 上次 status=error 且 stages_done 非空 | `▶ 续跑 (从「{出错阶段}」开始)` | 出错阶段 key |

**关键**：前端读 `history[-1].stages_done`，找 STAGE_KEYS 里第一个不在 done 集合里的 key = next。

#### 实现要点

1. **`computeNextSince(history)` 助手**：纯 JS 函数，与 STAGE_KEYS 强耦合
2. **`renderPanel` 内生成按钮 HTML**：根据 `data.history` 决定按钮文案/属性
3. **`loadPanel` 透传 history**：`renderPanel(taskId, {config, updated_at, history})`
4. **click 委托读 `data-since`**：智能按钮 `data-since` 在按钮上，「▶ 从本阶段开始」按钮是独立 `data-action="pipe-run-since"`

### 设计 2 — 字幕修订 Rigor 选择器

#### 复用现有 4 卡 picker

工作台（workbench）的 revise_zone 已渲染完整 4 卡 picker（`_render_rigor_picker`，[app.py:338-403](slirn_home/app.py#L338)）。
pipe-panel 里复用相同 HTML 结构 + 现有 `.slirn-rigor-card` 样式。

#### Pipe 通道 vs 工作台差异

- 工作台有完整 textarea 编辑自定义提示词（`slirn-rigor-custom` 块）
- Pipe 通道没有 textarea（pipe 用户选 custom 也没地方填提示词）
- **设计**：pipe 通道 4 卡 picker 也显示 custom 档（保持视觉一致），但**选 custom 时前端降级为 medium**，与后端 `handler_subtitle_review` 校验一致；desc 标注清楚避免误导

#### 配置 schema

```python
default_config()["subtitle_review"] = {
    "accept_all_suggestions": False,
    "skip_categories": [],
    "rigor": "medium",   # 新增（REQ-049）
}
```

#### 校验

- `validate_config()` 末尾加 rigor 合法性校验（脏数据 → 回落 medium）
- `handler_subtitle_review` 再校验一次（handler 层兜底）

#### 模板

3 套内置模板（`default_tpl`/`semi`/`full`）的 `subtitle_review` 段都加 `"rigor": "medium"`。

### 设计 3 — 阶段状态回显

#### 数据来源

后端 `pipeline_status` 端点已返回：
- `state`：running / done / error / stopped / idle
- `current_stage`：当前跑到的阶段 key
- `percent`：当前进度百分比（粗粒度 5 等分）
- `started_at`：Unix 时间戳
- `log`：最近 200 条日志条目（带 stage 字段）
- `summary.stages_done`：已完成阶段列表

**前端无需新加 API 字段**，从现有 status 推导即可。

#### 派生逻辑

1. **`deriveStagesDone(st)`**：
   - 优先读 `st.summary.stages_done`（已完成 run）
   - 否则从 `st.log` 找 `'✅'` + `'完成'` 字样 → 推算 done 列表
2. **`stageStateOf(stageKey, st, doneSet, currentStage)`**：
   - error + currentStage==stageKey → `'error'`
   - running + currentStage==stageKey → `'running'`
   - doneSet 含 stageKey → `'done'`
   - 其余 → `'pending'`
3. **`fmtElapsed(seconds)`**：X秒 / X分Y秒 / X时Y分
4. **`updateStageBadges(st)`**：
   - 遍历 STAGES 找到对应 `.slirn-pipe-section[data-pipe-section="<key>"]`
   - 设置 `data-pipe-state` 属性（CSS 据此变色 + pulse）
   - 更新 num 圆形字符（done=✓ / running=⏳ / 其他=数字）
   - 在 label 后插/更新徽章 span：
     - running：`⏳ 运行中 · X% · 已耗时 Y秒`
     - done：`✅ 已完成`
     - error：`❌ 出错`
     - pending：`⏸ 待执行`

#### 接入路径

| 时机 | 调用 | 触发源 |
|---|---|---|
| 面板渲染后 | `updateStageBadges(r.status)` | `loadPanel` 末尾 |
| running 中轮询 | `updateStageBadges(r)` | `pollStatus` 每次响应后 |
| idle 但有 history | `updateStageBadges({state: idle, summary: last})` | `loadPanel` history 兜底 |

#### CSS（home.css 新增）

- `.slirn-pipe-section-state`：徽章通用样式（圆角胶囊）
- 4 态颜色（`.slirn-pipe-section-state-{running,done,pending,error}`）
- `.slirn-pipe-section[data-pipe-state="running"] .slirn-pipe-section-num` 加 pulse 动画
- done 态 label 灰化（视觉降权）
- `.slirn-pipe-rigor`：pipe-panel 内嵌 rigor picker（2 列布局，复用 `.slirn-rigor-card` 全样式）
- `.slirn-pipe-subhead`：picker 标题样式

## 文件改动清单

| 文件 | 改动 |
|---|---|
| [pipeline_service.py](slirn_home/pipeline_service.py) | `default_config()` 加 `rigor: "medium"`；`validate_config()` 校验 rigor；`handler_subtitle_review()` 读 cfg.rigor |
| [pipeline.js](slirn_home/static/pipeline.js) | TEMPLATES 加 rigor；`renderStageForm` subtitle_review 嵌入 4 卡 picker；`renderPanel` 智能按钮；`readCurrentConfig` 读 rigor；新增 `computeNextSince`/`deriveStagesDone`/`stageStateOf`/`fmtElapsed`/`updateStageBadges`；`pollStatus`/`loadPanel` 接入徽章更新；click 委托读 `data-since`；暴露 `window.slirnPipelineUpdateStageBadges` 供 E2E |
| [home.css](slirn_home/static/home.css) | 新增 pipe-section 状态徽章样式 + pulse 动画 + pipe-rigor 容器样式 |

## 测试

### 单元测试（tests/test_pipeline_service.py，新增 14 个）

- `test_default_config_subtitle_review_has_rigor_medium`
- `test_validate_config_normalizes_invalid_rigor_to_medium`
- `test_validate_config_accepts_valid_rigor_values`
- `test_handler_subtitle_review_passes_rigor_from_cfg`
- `test_handler_subtitle_review_normalizes_invalid_rigor`
- `test_history_stages_done_persists_through_runs`
- `test_append_history_max_10_entries`（复用）
- `test_compute_next_since_empty_history`
- `test_compute_next_since_first_stage_done`
- `test_compute_next_since_two_stages_done`
- `test_compute_next_since_all_done`
- `test_compute_next_since_with_error_midway`
- `test_compute_next_since_unknown_stage_ignored`

**总计 41 passed**（含 26 个 v4 既有 + 1 个历史截断测试 + 14 个新）

### E2E（work/REQ-20260918-049/_e2e_049.py，16 checks）

UI 渲染、按钮文案自适应、4 卡 picker、落盘、徽章回显、running 态百分比+已耗时、样式复用。

**总计 16/16 通过**。

## 不做的事

- 不动 `funclip/` 上游
- 不改 Skill 源码
- 不引入新依赖（urllib 已够用）
- 不在 pipe-panel 加 custom rigor 的 textarea（pipe 通道不支持）
- 不加新按钮（智能单按钮）
- 不改 stage 服务端 per-stage 进度（仍是粗粒度 5 等分）
- pipe 通道对 custom 档降级 medium（不暴露自定义提示词）

---

# v2 — 状态条可拖动 + 收起后可恢复（2026-09-18 用户反馈）

## Context

真机使用后发现：
- "运行流程的状态条点击收起之后就无法显示了" — 收起按钮只是 hidden，但没有恢复入口
- "状态条不可以调整位置，应该可以调整，不然它挡住了其他功能按钮" — 状态条固定右上角，有时挡住按钮

## 设计

### 状态条拖动

- 拖手柄 = 状态条 head（除了 ▾ 按钮本身）
- `pointerdown/move/up` + `setPointerCapture`（保跨元素可靠追踪）
- 边界保护：`Math.max(0, Math.min(window.innerWidth-100, startLeft+dx))`
- 拖动结束保存到 localStorage（key = `slirn-pipe-status-pos-<taskId>`，多任务互不污染）
- CSS：`cursor: move`（手柄）+ `cursor: grabbing`（拖动中）+ `cursor: pointer`（折叠按钮）+ `user-select: none`（拖动时不选文字）

### 收起后恢复

- pipe-panel 头部新增「📊 状态」按钮（默认 visible，状态条收起时还可见）
- click 委托：`data-action="pipe-status-show"` → 恢复状态条 + 应用已设好的位置 + 重绑拖动 + 启动轮询
- 「📊 状态」按钮的可见性由 `refreshStatusToggleBtn()` 同步（hidden 状态条 ↔ 显示按钮）

### Bug fix

`_bindStatusDrag(box)` 原本把 `__dragBound=true` 写在 head 查询之前，导致：
- showStatus 时 head 还没渲染（renderStatusBar 异步），listener 跳过但标记已设
- renderStatusBar 写入 head 后再调 `_bindStatusDrag`，被 `__dragBound` 挡住，永远不绑

**修法**：先查 head，没 head 直接 return（不设标记），让 renderStatusBar 重绑。

## 测试

`_e2e_statusbar.py` 20 checks：拖动、localStorage 保存、收起/恢复、collapse 按钮不触发拖动。

---

# v3 — 流程跑完后「续跑」按钮自动刷新（2026-09-18 用户反馈）

## Context

> "字幕生成已经跑完了，显示成功，这时续跑的话，就不应该从字幕生成开始，而应该从下一阶段字幕修订开始。难道执行完的阶段成功了没有记录吗？"

### 根因

pipeline.json 里的 history.stages_done 确实记录了，但**按钮只在 loadPanel 时算一次**。
- 用户跑流程时面板已加载（按钮是「从头跑」）
- 流程跑完，stages_done 已落盘，但**按钮没刷新**
- 用户看徽章 ✅ 后点「仍带 since=null 的从头跑按钮」→ 从头跑（BUG！）

### 修复

在 `pollStatus` 检测到 `state !== 'running'`（终态）后调 `refreshRunBtnFromStatus(r)`：

```javascript
function refreshRunBtnFromStatus(r) {
  var btn = document.querySelector('#slirn-pipe-panel [data-action="pipe-run"]');
  if (!btn) return;
  var done = deriveStagesDone(r);  // 优先 summary.stages_done → 兜底 log 推算
  var sinceKey = computeNextSince([{stages_done: done, status: r.state}]);
  if (sinceKey) {
    btn.setAttribute('data-since', sinceKey);
    btn.textContent = '▶ 续跑 (从「' + (STAGE_LABELS[sinceKey] || sinceKey) + '」开始)';
    btn.setAttribute('title', '上次跑到了「' + (STAGE_LABELS[sinceKey] || sinceKey) + '」之前；点此从该处开始（跳过已完成阶段）');
  } else {
    btn.removeAttribute('data-since');
    btn.textContent = '▶ 从头跑';
    btn.setAttribute('title', '保存并按当前配置顺序执行所有阶段');
  }
}
```

### 测试

`_e2e_resume_after_run.py` 7 checks：
- 初始无 history → 「从头跑」
- 终态 done + stages_done=[subtitle_generation] → 「续跑(从「字幕修订」开始)」+ data-since='subtitle_review'
- 终态 error + stages_done=[subtitle_generation, subtitle_review] → 「续跑(从「切分修剪」开始)」+ data-since='rough_cut'
- 全跑完 → 「从头跑」（无 data-since）
