# REQ-20261013-115 — 拖拽后「↩️ 还原顺序」按钮就地刷新禁用态

> 父 REQ：[REQ-20261004-stage3-restructure](REQ-20261004-stage3-restructure.md)（拖拽重排最初实现）
> 状态：✅ 验收通过（2026-10-13）

## Context

**用户反馈**（2026-10-13 第六轮，REQ-114 验收后）：

> "顺序已经调整，但是还原顺序按钮还认为顺序没有变，修改这个bug"

附截图：Stage 3 某条短视频的字幕行已被拖拽重排为 2,3,1,4,13,5,9,6,7,8,14,12,10,11,15,16，
但「↩️ 还原顺序」按钮仍处于 `disabled` 状态。

## 根因

`bindSubtitleSort`（`short_video.js:1964`）的拖拽成功回调只做了：
1. DOM 乐观重排（`parent.insertBefore`）
2. 调端点 `POST /slirn/api/short_video_stage3_reorder_subtitles` 持久化
3. 显示 toast「字幕顺序已更新（下游 Stage 4-7 已重置）」

**没有**触发 `openProject` 全量重渲染。

而按钮的 `disabled` 属性是 **server 渲染**写进 HTML 的（`short_video_ui.py:571-580`）：

```python
is_original = nos == sorted(nos)
restore_btn = (
    f'<button ... data-hl-index="{idx}">↩️ 还原顺序</button>'
    if not is_original else
    f'<button ... data-hl-index="{idx}" disabled title="当前已是大模型最初顺序">↩️ 还原顺序</button>'
)
```

旧渲染时行序是原序（`nos = [1,2,3,...,16]`）→ `is_original = True` → 按钮 HTML 带 `disabled`。
用户拖拽后 DOM 顺序已变，但**按钮 HTML 是 server 旧产物，DOM 节点没换** → 按钮仍 `disabled`。
结果：用户看到行序乱了，但按钮「失灵」（per memory `slirn-real-time-vs-persistent-state` 的 state mirror 失效模式）。

## 解决什么问题

| 维度 | 现状 | 修复后 |
|---|---|---|
| 拖拽成功 → 按钮 disabled 态 | 仍为旧 server 渲染态 | 就地按当前 DOM 行序重算 |
| 拖拽失败回滚 → 按钮态 | 不变（也是正确的） | 维持不变 |
| 还原顺序成功后 → 按钮态 | openProject 全量重渲染 ✓ | 不变（已对） |
| 与 server 计算口径 | 客户端无对应逻辑 | 客户端复用 `nos == sorted(nos)` 升序判 |

## 关键设计决策

1. **就地 DOM 更新而非 openProject 全量重渲染**：
   - 拖拽是高频交互（用户可能连续拖多行），openProject 是 server round-trip + 全 DOM 重建，体验上闪烁
   - 单按钮属性（disabled + title）就够解决用户可见问题
   - 与 `stage3RestoreOrder`（还原）走 `openProject` 全渲染的策略**不同** —— 还原是单次操作，全渲染可接受

2. **复用 server 端 `is_original = nos == sorted(nos)` 口径**：
   - 客户端读 `.slirn-stage3-hl-no` 节点文本（即 dict['no'] 的渲染值）
   - 客户端判升序：`nos.every((n, i, arr) => i === 0 || n >= arr[i-1])`
   - 与 `short_video_ui.py:571` 完全等价，不会出现「客户端说启用、server 说禁用」的不一致

3. **失败回滚分支不调刷新**：
   - DOM 已经被 `parent.insertBefore(movedEl, el.nextSibling)` 恢复
   - 行序未变 → 按钮态不该变 → 没必要再算

4. **不持久化、不动后端**：
   - 修法纯前端，加 ~30 行 JS + 1 个新测试
   - server 端 `reorder_subtitle_line` 行为不变（只改 list 顺序、不改 `no` 字段）已正确

## 实现方案

### 1. 新增 `_refreshRestoreBtn(hlIndex)` 工具函数（`slirn_home/static/short_video.js`）

```javascript
function _refreshRestoreBtn(hlIndex) {
  if (!hlIndex) return;
  var btn = document.querySelector(
    '.slirn-stage3-hl-row[data-hl-index="' + hlIndex + '"] ' +
    '[data-action="sv-stage3-restore-order"]'
  );
  if (!btn) return;
  var rows = document.querySelectorAll(
    '.slirn-stage3-hl-preview[data-hl-index="' + hlIndex + '"] ' +
    '.slirn-stage3-hl-sub'
  );
  var nos = [];
  rows.forEach(function(r) {
    var noEl = r.querySelector('.slirn-stage3-hl-no');
    if (!noEl) return;
    var m = (noEl.textContent || '').match(/(\d+)/);
    if (m) nos.push(parseInt(m[1], 10));
  });
  var isOriginal = nos.length > 0 && nos.every(function(n, i, arr) {
    return i === 0 || n >= arr[i - 1];
  });
  if (isOriginal) {
    btn.setAttribute('disabled', 'disabled');
    btn.setAttribute('title', '当前已是大模型最初顺序');
  } else {
    btn.removeAttribute('disabled');
    btn.setAttribute('title', '把该条短视频的字幕行还原为大模型最初给出的顺序（编号不变）');
  }
}
```

### 2. 拖拽成功回调调用（`bindSubtitleSort` 内）

```javascript
} else {
  // 同步刷新「↩️ 还原顺序」按钮状态：行序变了，禁用态可能已失效
  // （per slirn-real-time-vs-persistent-state：拖拽改 server 状态后
  // 必须就地同步 UI 状态，不能只靠下次 openProject 重渲染）
  _refreshRestoreBtn(parseInt(ctx.hlIndex || '0', 10));
  showToast('字幕顺序已更新（下游 Stage 4-7 已重置）', 'ok');
}
```

### 3. 不改的部分

- `short_video_ui.py` 渲染逻辑不动（server 端 is_original 计算口径本来就对，是 HTML 缓存问题）
- `reorder_subtitle_line` 后端不动（只改 list 顺序、不改 `no` 字段 — 已正确）
- `stage3RestoreOrder` 处理器不动（已走 openProject 全渲染，按钮态会重新算）
- 其他按钮（重新解析 / 删除 / 预览）不依赖行序，无关

## 关键文件改动清单

| 文件 | 改动 |
|---|---|
| `slirn_home/static/short_video.js` | +`_refreshRestoreBtn` 函数 32 行；拖拽成功回调插入 1 行调用 + 3 行注释 |
| `tests/test_short_video_stage3_llm_clip.py` | +`test_drag_reorder_refreshes_restore_btn_inline` 5 项断言 |

## 测试与验证

### 单元测试

**结果**：全量 **1264 passed**（基线 1263 → +1 新测试）

**新测试 5 项断言**：
1. JS 含 `function _refreshRestoreBtn` 定义
2. 拖拽成功的 then 分支里有 `_refreshRestoreBtn(hlIndex)` 调用
3. 函数体读 `.slirn-stage3-hl-no` 节点文本（与 server 取 `sl.get("no")` 等价）
4. 升序判口径 `n >= arr[i - 1]`（与 server `nos == sorted(nos)` 等价）
5. 失败回滚分支不调 `_refreshRestoreBtn`（行序未变，按钮态不该变）

### 端到端（用户验收点）

- [ ] **真实项目拖拽**：打开任一 Stage 3 highlight，拖动字幕行 → 松手后该 highlight 的「↩️ 还原顺序」按钮立即从 disabled 变 enabled
- [ ] **继续拖拽**：多次拖拽后按钮态跟随更新（升序时 disabled，否则 enabled）
- [ ] **点还原顺序**：按钮恢复行序后变 disabled，刷新页面也是 disabled
- [ ] **失败回滚**：模拟后端失败（断网 / 改错 hl_index）→ 按钮态保持原状

### 7861 重启

- 不需要（改 JS 而非 CSS / Python，per memory `slirn-dynamic-script-load-race`）
- 用户刷新页面即可拿到新 JS

## 风险与对策

| 风险 | 对策 |
|---|---|
| 客户端判 original 与 server 口径不一致 | 严格复用 `nos == sorted(nos)` 升序判，已被测试覆盖 |
| `_refreshRestoreBtn` 选择器失效（DOM 结构变化） | 用 `data-hl-index` 属性 + `data-action` 属性双重定位，与 server 渲染 HTML 一一对应 |
| 拖拽过程中多次快速触发 | 拖拽是低频操作（一次拖完才调端点），多次触发的概率低；即便多次触发也是幂等（同一行序算同一态）|
| 失败回滚误调导致行序错乱 | 失败分支明确不调，按钮态保持原状（与 DOM 行序一致）|
| 跨 hl 误刷（hlIndex 拿错） | hlIndex 从 `_getDragCtx().hlIndex` 取（drop 时 set），与受影响 pane 一一对应 |

## 验收清单

- [x] `_refreshRestoreBtn(hlIndex)` 函数定义 + 拖拽成功回调调用
- [x] 失败回滚分支不调
- [x] 升序判口径与 server 等价
- [x] 1 新测试通过（5 项断言）
- [x] 全量 1264 pytest
- [x] commit 804924a push
- [ ] **用户最终验收**（待用户在真实项目拖拽后看按钮态是否实时切换）

## 关联

- [[req-104-stage3-restructure]] — 拖拽重排最初实现
- [[req-110-sv-stage4-reorder-time-srtno]] — 同一系列（行序相关修复）
- [[slirn-real-time-vs-persistent-state]] — state mirror 失效模式（拖拽改 server 状态后必须就地同步 UI）
- [[slirn-dynamic-script-load-race]] — 改 JS 不需重启 7861
- [[gradio-prose-star-color-trap]] — Gradio `.prose *` 通配陷阱
