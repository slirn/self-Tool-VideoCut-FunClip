# DESIGN-20260918-054 — 优化字幕阶段播放时字幕行高亮

## Context

REQ-054 用户原话：
> 优化字幕阶段，点击词频列表中的某一个词之后列出来了相关的 11 条字幕，这时点击一条字幕进行播放时，字幕应该进行高亮，这样方便跟踪正在播放字幕的位置

**现状**：
- 优化字幕阶段有独立视频播放器 `<video id="slirn-opt-player">` 与字幕列表 `#slirn-opt-list`（[slirn_home/app.py:1198-1227](slirn_home/app.py#L1198)）
- 已有交互 `playOptAt(tid, startMs)` [slirn_home/static/router.js:2276-2292](slirn_home/static/router.js#L2276)：点字幕行 → `currentTime = startMs/1000` + `play()`，但**没有任何 timeupdate 跟随**
- 字幕行 DOM 只带 `data-start-ms`、缺 `data-end-ms`（[slirn_home/app.py:1198-1199](slirn_home/app.py#L1198) 渲染漏写）
- 已有同款模式（最佳模板）：`bindRevPlayer` [slirn_home/static/router.js:2453-2509](slirn_home/static/router.js#L2453) 与 `bindCutPlayer` [slirn_home/static/router.js:2392-2451](slirn_home/static/router.js#L2392)

**采纳的设计决策**：
- ✅ **复用 `.active` class**（与 cut/rev 一致，跨阶段视觉统一）
- ✅ **直接抄 cut/rev 的 timeupdate 高亮跟随模式**（不含 cut/rev 的 ① 跳播链，opt 是单行点击语义）
- ✅ **Plan agent 的兜底**：当 `data-end-ms = 0` 或 ≤ start_ms 时，用下一行 `data-start-ms` 作为上界（最后一行 fallback `+Infinity`）
- ✅ **Plan agent 的 `seeked` 监听**：用户拖进度条后立即重算（不依赖 timeupdate 频率）
- ❌ **不**复制 Plan agent 的 `loadedmetadata` / `emptied` 监听（timeupdate 已经覆盖；cut/rev 也没加）

## 实施改动

### 1. 后端：补 `data-end-ms`

**文件**：[slirn_home/app.py:1196-1203](slirn_home/app.py#L1196)

`_line_html` 渲染 `slirn-opt-row` 补 `data-end-ms`：

```python
return (f'<div class="slirn-opt-row{has_occ}" data-id="{_esc(rid)}"'
        f' data-start-ms="{int(seg.get("start_ms", 0))}"'
        f' data-end-ms="{int(seg.get("end_ms", 0))}"'
        f' data-words="{_esc(chr(10).join(row_words))}">'
        ...)
```

### 2. 前端：新增 `optPlayerHighlight()` + 在 `playOptAt` / 初始化处触发

**文件**：[slirn_home/static/router.js:2276-2302](slirn_home/static/router.js#L2276)

**核心函数**（单一职责，按 currentTime 计算命中行 + setActive；DRY 避免重复）：

```javascript
function optPlayerHighlight(v) {  // REQ-20260918-054：按 currentTime 重算高亮
  var list = revVis('slirn-opt-list');
  if (!v || !list) return;
  var rows = Array.prototype.slice.call(
    list.querySelectorAll('.slirn-opt-row[data-start-ms]'));
  if (rows.length === 0) return;
  var tms = (v.currentTime || 0) * 1000, hit = -1;
  for (var i = 0; i < rows.length; i++) {
    var s0 = parseInt(rows[i].getAttribute('data-start-ms'), 10) || 0;
    var e0 = parseInt(rows[i].getAttribute('data-end-ms'), 10) || 0;
    // 兜底：end_ms 缺失或 ≤ start 时，用下一行 start_ms 作为上界（最后一行 +Infinity）
    var eEff = (e0 > s0) ? e0 :
      (i + 1 < rows.length) ? (parseInt(rows[i + 1].getAttribute('data-start-ms'), 10) || (s0 + 1)) :
      (s0 + 1);
    if (tms >= s0 && tms < eEff) { hit = i; break; }
    if (s0 > tms) break;
  }
  for (var j = 0; j < rows.length; j++) {
    rows[j].classList.toggle('active', j === hit);
  }
  if (hit >= 0 && rows[hit] && rows[hit].scrollIntoView) {
    try { rows[hit].scrollIntoView({block: 'nearest'}); } catch (err) {}
  }
}

function bindOptPlayerHighlight() {  // REQ-20260918-054：timeupdate + seeked 跟高亮（幂等）
  var v = revVis('slirn-opt-player');
  if (!v || v.dataset.optHLBound) return;
  v.dataset.optHLBound = '1';
  v.addEventListener('timeupdate', function() { optPlayerHighlight(v); });
  v.addEventListener('seeked',    function() { optPlayerHighlight(v); });
}
```

**`playOptAt` 内调用**（替换原 `goO` 中 currentTime 设置后立即调）：

```javascript
function playOptAt(tid, startMs) {
  var wrap = revVis('slirn-opt-player-wrap');
  var v = revVis('slirn-opt-player');
  if (!v) { toast('❌ 播放器未就绪', 'error'); return; }
  if (wrap) vfShow(wrap);
  if (!v.src) {
    v.src = SLIRN_API + '/video/' + encodeURIComponent(tid) + '?src=rough_compose';
    v.load();
  }
  bindOptPlayerHighlight();  // REQ-20260918-054：首次绑定（幂等）
  var goO = function() {
    try { v.currentTime = (startMs || 0) / 1000; } catch (err) {}
    optPlayerHighlight(v);  // REQ-20260918-054：跳转后立即按 currentTime 重算（不等 timeupdate）
    var p = v.play();
    if (p && p.catch) p.catch(function() {});
  };
  if (v.readyState >= 1) goO();
  else v.addEventListener('loadedmetadata', goO, {once: true});
}
```

### 3. CSS：`.slirn-opt-row.active` 样式

**文件**：[slirn_home/static/home.css:2023-2029](slirn_home/static/home.css#L2023) 紧接 target 样式后

```css
/* REQ-20260918-054：播放中字幕行高亮（与 cut/rev 同款 .active 语义）。
   视觉比 .slirn-opt-row-target（淡黄底）更强：青色左边粗条 + 渐变背景。
   active 后写以压过 target（target 也匹配 active 时视觉上是 active）。 */
.slirn-opt-row.active {
  background: linear-gradient(90deg, rgba(49, 120, 198, 0.16) 0%, rgba(49, 120, 198, 0.06) 100%);
  border-left: 4px solid var(--accent, #3178c6);
  padding-left: 6px;
  animation: slirn-opt-playing-pulse 1.6s ease-in-out infinite;
}
.slirn-opt-row.active .slirn-sub-idx {
  color: var(--accent, #3178c6);
  font-weight: 700;
}
.slirn-opt-row.active.slirn-opt-row-target {
  /* 当播放行同时是 target 时：复合选择器压过 target 单类（特异性 2 > 1） */
  border-left-color: var(--accent, #3178c6);
  background: linear-gradient(90deg, rgba(49, 120, 198, 0.20) 0%, rgba(49, 120, 198, 0.07) 100%);
}
@keyframes slirn-opt-playing-pulse {
  0%, 100% { box-shadow: inset 0 0 0 0 rgba(49, 120, 198, 0); }
  50%      { box-shadow: inset 0 0 0 2px rgba(49, 120, 198, 0.30); }
}
@media (prefers-reduced-motion: reduce) {
  .slirn-opt-row.active { animation: none; }
}
```

## 关键文件汇总

| 文件 | 改动 | 行数估算 |
|---|---|---|
| [slirn_home/app.py](slirn_home/app.py) | `_line_html` 加 `data-end-ms` | +1 |
| [slirn_home/static/router.js](slirn_home/static/router.js) | 新增 `optPlayerHighlight` + `bindOptPlayerHighlight`；`playOptAt` 内调用 | +30 |
| [slirn_home/static/home.css](slirn_home/static/home.css) | `.slirn-opt-row.active` 样式 | +25 |

## 验证

### 静态
- `node --check slirn_home/static/router.js`
- `python -c "import ast; ast.parse(open('slirn_home/app.py', encoding='utf-8').read())"`

### 回归
- `pytest tests/ -q`（既有 324+ tests 应全过；如有 `test_render_optimize_*` 检查 `data-start-ms` 用 contains 断言，新增 `data-end-ms` 不破坏）

### E2E（CDP）
`work/_archive/req054_e2e_highlight.py`（参考 `req_video_shortcut_probe.py`）：
1. 进任务 → 进优化字幕面板
2. 等 `.slirn-opt-row` 渲染
3. 断言每行有 `data-start-ms` 和 `data-end-ms`
4. 点一行 → 等 500ms
5. 断言：DOM 恰好 1 个 `.slirn-opt-row.active`，且 `data-start-ms` 等于被点行
6. 触发 `v.currentTime += 1` → 等下一帧
7. 断言：`active` 行更新
8. `v.pause()` → 断言：active 仍在
9. 计算样式断言：`border-left-color` 含 `#3178c6`

### 真机手测
1. 重启 slirn → 进优化字幕
2. 点词频某词 → 点 11 行中某行 → 视频播放 → 行立即高亮（蓝渐变 + 左色条 + pulse）
3. 视频继续播 → 高亮跟随
4. 拖进度条 → 高亮跳到对应行
5. 暂停 / 结束 → 高亮保留
6. 再点同一词清空过滤 → 全部行显示 → 当前播放行仍高亮
7. 同时是 target + playing → 蓝色视觉压过 target 橙色

## 风险与边界

| 风险 | 处理 |
|---|---|
| 切任务 video 元素被替换 | `v.dataset.optHLBound` 防重复绑；新元素 fresh state 重新绑定 |
| 最后一行 `end_ms=0` | JS 端用下一行 start_ms 兜底；optWordFilter 隐藏时下一行可能不在 DOM，用 `s0 + 1` 兜底 |
| `data-end-ms` 渲染漏写（未来回归） | E2E 断言强制校验 |
| 暂停 / 视频结束丢失高亮 | **不**挂 `pause`/`ended` 的 removeClass |
| 用户手动滚动导致高亮行出视口 | 不自动滚动跟随（MVP 范围之外；用户原话只要求"高亮"） |
| 与 REQ-20260918-052 target 高亮视觉冲突 | 复合选择器特异性 (0,2,0) > 单类 (0,1,0) 压过 |
| reduced-motion 用户 | pulse 动画自动关闭 |
| 行数 N>100 时 O(N) 线性扫 attr | 当前 ≤ 几十，无影响 |

## 不做的事

- ❌ 不加连续跳播链（opt 是单行点击语义）
- ❌ 不改 funclip/ 上游
- ❌ 不动 `bindRevPlayer` / `bindCutPlayer`
- ❌ 不改 playOptAt 视频 src 加载逻辑
- ❌ 不引入新依赖
- ❌ 不在 wb 重渲染时主动重绑（首次 `playOptAt` 调 `bindOptPlayerHighlight` 已覆盖场景；后续 timeupdate 持续生效）

## 实施顺序

1. ✅ REQ 文档（`docs/REQM/REQ-20260918-054-opt-subtitle-playback-highlight.md`）
2. ✅ DESIGN 文档（本文件）
3. ⏳ 实施：
   - [ ] app.py `_line_html` 加 `data-end-ms`
   - [ ] router.js 加 `optPlayerHighlight` + `bindOptPlayerHighlight`，`playOptAt` 内调用
   - [ ] home.css 加 `.slirn-opt-row.active` 样式
4. ⏳ 评审：node --check + python ast + 跑回归 tests
5. ⏳ 验证：CDP probe + 真机手测
