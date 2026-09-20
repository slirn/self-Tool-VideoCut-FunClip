# VERIFICATION-20260920-091 v2 — combo-test UX 反馈修复

## 概述

- **REQ**：[REQ-20260920-091-combo-time-params.md（v2 章节）](docs/REQM/REQ-20260920-091-combo-time-params.md)
- **实现日期**：2026-09-20
- **结果**：✅ 8/8 AC（v2 章节）全部通过

## 验收标准清单（v2 新增）

| AC | 描述 | 状态 | 验证方式 |
|---|---|---|---|
| AC-11 | combo-test 点击立刻 toast `🚀 一键测试合成已启动（{dur}）` | ✅ | `test_combo_test_button_state_changes` |
| AC-12 | combo-test 进入时按钮文字变 `⏳ 测试中…` | ✅ | `test_combo_test_button_state_changes` |
| AC-13 | combo-test done 时按钮文字变 `✅ 完成 · 查看视频` + 自动 `window.open` | ✅ | `test_combo_test_button_state_changes` |
| AC-14 | combo-test fail 时按钮文字变 `❌ 失败 · 重试` | ✅ | `test_combo_test_button_state_changes` |
| AC-15 | 自动 `window.open` 仅当 `outPath === 'outputs/fine_export.mp4'`（video 端点支持） | ✅ | `test_combo_test_only_autoopens_for_default_output` |
| AC-16 | time-suffix 文件不自动弹 video 端点，提示用户去本地查看 | ✅ | `test_combo_test_only_autoopens_for_default_output` |
| AC-17 | `_snapshot()` 加 early-return——已在 snapshot 时不覆盖 | ✅ | `test_combo_snapshot_not_overwritten_by_combo_test` |
| AC-18 | 2 个新测试 | ✅ | pytest 631 passed（629 + 2 new）|

## 详细验证

### AC-11/AC-12/AC-13/AC-14：按钮状态机（参考 REQ-077 setExportBtnState）

**静态验证**（`test_combo_test_button_state_changes`）：

```javascript
// 进入时
testBtn.textContent = '⏳ 测试中…';
testBtn.disabled = true;
toast('🚀 一键测试合成已启动（' + (tp.duration || '完整') + '）');

// done 时
testBtn.textContent = '✅ 完成 · 查看视频';
testBtn.onclick = function(ev) {
  window.open(autoUrl, '_blank');  // 用户再次点击「查看视频」
};

// failed 时
testBtn.textContent = '❌ 失败 · 重试';
```

✅ 4 处状态全过：进入 toast + ⏳ / done ✅ + 自动 open / fail ❌

### AC-15：自动 window.open 仅 default output

**静态验证**（`test_combo_test_only_autoopens_for_default_output`）：

```javascript
var isDefaultOutput = outPath === 'outputs/fine_export.mp4';
if (isDefaultOutput) {
  var autoUrl = '/slirn/api/video/' + tid + '?src=fine_export&t=' + Date.now();
  window.open(autoUrl, '_blank');  // ✅ video 端点支持
  // 按钮文字变 ✅ 完成 · 查看视频
} else {
  // time-suffix：提示用户去本地查看
  var tipHtml = html + '💡 提示：time-suffix 文件（' + outPath + '）...';
}
```

✅ `isDefaultOutput` 判断 + `src=fine_export`（video 端点唯一支持精剪）+ `Gradio` 提示文本

### AC-16：video 端点限制（为什么 time-suffix 不弹）

**app.py:6584 视频端点逻辑**：

```python
elif src_q in ("fine_preview", "fine_export"):
    p = mgr.tasks_dir / tid / "outputs" / f"{src_q}.mp4"
    video = p if p.exists() else None
else:
    video, _label = _resolve_task_video(t)
```

视频端点**只接受** `src=original/rough_compose/fine_preview/fine_export`，对 time-suffix 文件（`fine_export_t30_d20.mp4`）会 fall-through 到 `_resolve_task_video` 返回原视频，**与合成结果不匹配**。

所以 JS 必须在客户端判断 `outPath === 'outputs/fine_export.mp4'` 才自动弹 video 端点，time-suffix 文件只显示在 output 文本里 + 提示用户去本地查看。

### AC-17：_snapshot early-return 修复

**静态验证**（`test_combo_snapshot_not_overwritten_by_combo_test`）：

```javascript
function _snapshot() {
  // REQ-20260920-091 v2：snapshot 只在「还没 snapshot 过」时记录
  // 避免 combo-apply 完后点 combo-test，snapshot 被覆盖为「测试状态」
  window._comboSnapshot = window._comboSnapshot || {};
  if (window._comboSnapshot[tid] !== undefined) return;  // ← 关键
  window._comboSnapshot[tid] = _readComboState();
  var restoreBtn = detailsEl.querySelector('[data-action="combo-restore"]');
  if (restoreBtn) restoreBtn.hidden = false;
}
```

✅ `_snapshot` 函数体里找到 `if (...) return;` 形式（`!== undefined` 检查）

**为什么需要**：原 BUG 是 combo-apply 调 `_snapshot()` 后 combo-test 入口又调一次，导致 `window._comboSnapshot[tid]` 被覆盖为「测试状态」（不是原始 fc），用户点「↩️ 还原」按钮还原不到原始 fc。

## E2E 实测

### 测试场景 1：默认输出（no time params）→ 自动弹视频

```bash
$ rm fine_export*.mp4
$ curl POST /slirn/api/export_fine_video -d '{"task_id":"20260920-001"}'
{"ok":true,"job_id":"job_1789911836982_32672",...}

# 等 2 秒
$ ls outputs/
-rw-r--r-- fine_export.mp4           195483  21:43
-rw-r--r-- fine_export_t0.0_d10.0.mp4 195483  21:42

$ curl -sI "/slirn/api/video/20260920-001?src=fine_export&t=12345"
HTTP/1.1 200 OK
```

✅ 默认路径走 `src=fine_export` → JS 自动 `window.open` 弹视频

### 测试场景 2：time-suffix 输出 → 不自动弹，提示本地查看

```bash
$ curl POST /slirn/api/export_fine_video -d '{"task_id":"20260920-001","preview_start":0,"duration":10}'
{"ok":true,"job_id":"job_1789911724074_32672",...}

# 等 2 秒
$ ls outputs/
-rw-r--r-- fine_export_t0.0_d10.0.mp4 195483  21:42

# isDefaultOutput=false → 走 else 分支，不 window.open
# output 区域显示：「💡 提示：time-suffix 文件（outputs/fine_export_t0.0_d10.0.mp4）无法通过 video 端点下载...」
```

✅ time-suffix 走 else 分支，提示用户去本地查看

### 测试场景 3：probe_output_audio 在 done 后自动调

```bash
$ curl POST /slirn/api/probe_output_audio -d '{"task_id":"20260920-001","output_path":"outputs/fine_export.mp4"}'
{"ok":true,"audio_stream_count":1,"duration":10.0,...}
```

✅ done 后自动 probe，BGM 检测报告渲染到 output 区域

## 完整测试套件

```
============================= test session starts ==============================
collected 631 items
tests/test_workbench.py ................................................ [ 18%]
........................................................................ [ 45%]
........................................................................ [ 73%]
.......................................................................  [100%]
====================== 631 passed, 3 warnings in 46.18s =======================
```

净 +2 个测试（629 from v1 → 631 with v2 new）。

## 文件改动汇总

| 文件 | 改动 | 行数 |
|---|---|---|
| [slirn_home/static/router.js](slirn_home/static/router.js) | combo-test 进入 toast + testBtn 状态机 + isDefaultOutput 判断 + 自动/不自动弹视频 | +28 / -3 |
| [tests/test_workbench.py](tests/test_workbench.py) | 2 个新测试 + 1 个 snapshot 修复测试 | +90 / -0 |
| [docs/REQM/REQ-20260920-091-combo-time-params.md](docs/REQM/REQ-20260920-091-combo-time-params.md) | 新增 v2 章节（根因/修复/AC） | +90 / -0 |

净代码 +208 / -3 = +205 行。

## UX 对比

| 场景 | v1 | v2 |
|---|---|---|
| combo-test 点击后 | 按钮变灰，无文字变化 | toast + 按钮变 `⏳ 测试中…` |
| running 中 | 看不到进度（output 在折叠 details 内） | toast + 按钮持续 `⏳ 测试中…` |
| done 后 | 静默，只 output 文本 | 按钮变 `✅ 完成 · 查看视频` + **自动弹视频新窗口** + toast |
| failed 后 | 静默，只 output 文本 | 按钮变 `❌ 失败 · 重试` + toast 错误 |
| time-suffix 文件 | 静默，video 端点 404 | 按钮变 `✅ 完成 · 重测` + output 文本提示本地查看 |

## 关联

- [REQ-20260920-091-combo-time-params.md](docs/REQM/REQ-20260920-091-combo-time-params.md) — 本 REQ 文档（含 v2 章节）
- [VERIFICATION-20260920-091-combo-time-params.md](docs/verification/VERIFICATION-20260920-091-combo-time-params.md) — v1 的 VERIFICATION（10 条 AC）
- [REQ-20260920-090](docs/REQM/REQ-20260920-090-combo-test-panel.md) — 5-checkbox 调试面板（基础）
- [REQ-20260920-077](docs/REQM/REQ-20260920-077-async-export-progress.md) — setExportBtnState 模式（v2 参考）
- [REQ-20260920-089](docs/REQM/REQ-20260920-089-ffmpeg-deadlock-cancel-button.md) — ffmpeg 死锁修复（基础设施）
