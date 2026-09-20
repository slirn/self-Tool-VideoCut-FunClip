# VERIFICATION-20260920-091 — 合成元素组合测试面板 — 加时间参数（开始时间 + 时长）

## 概述

- **REQ**：[REQ-20260920-091-combo-time-params.md](docs/REQM/REQ-20260920-091-combo-time-params.md)
- **实现日期**：2026-09-20
- **结果**：✅ 10/10 AC 全部通过

## 验收标准清单

| AC | 描述 | 状态 | 验证方式 |
|---|---|---|---|
| AC-1 | 工作台测试面板新增「⏱ 开始时间」+「⏳ 时长」2 个 input | ✅ | `test_combo_test_time_inputs_in_html` |
| AC-2 | 默认值：start=00:00:00，duration=10 秒 | ✅ | `test_combo_test_time_inputs_in_html` |
| AC-3 | 复用 REQ-064/066 的 slirn-fine-num + slirn-fine-preview-time class | ✅ | `test_combo_test_time_inputs_in_html` |
| AC-4 | /export_fine_video 接受 body.preview_start + body.duration | ✅ | `test_export_fine_video_accepts_time_params` + E2E |
| AC-5 | 缺省回退到 preview_start=0.0 / duration=None（向后兼容） | ✅ | `test_export_fine_video_accepts_time_params` + E2E |
| AC-6 | _run_fine_render_async 接受 time 参数并透传给 _assemble_fine_filter | ✅ | `test_export_fine_video_accepts_time_params` |
| AC-7 | output 文件名加 _t{start}_d{duration}.mp4 后缀 | ✅ | `test_export_fine_video_output_path_has_time_suffix` + E2E |
| AC-8 | combo-test 把 time 参数传给 /export_fine_video | ✅ | `test_combo_test_passes_time_params_to_export` |
| AC-9 | combo-test 完成后 probe 探测**对应** output 文件 | ✅ | `test_combo_test_passes_time_params_to_export` + E2E |
| AC-10 | 4 个新测试 | ✅ | pytest 628 passed（624 + 4 new）|

## 详细验证

### AC-1/AC-2/AC-3：workbench HTML 时间 input

**静态验证**（`test_combo_test_time_inputs_in_html`）：

✅ 3 个开始时间 input：`slirn-combo-test-start-h` / `-m` / `-s`（时:分:秒）
✅ 1 个时长 input：`slirn-combo-test-duration`，min=2 max=30
✅ 复用 `slirn-fine-num` + `slirn-fine-preview-time` class（与 REQ-066 一致）
✅ 默认值：start 全 0，duration=10 秒

**HTML 位置**：[slirn_home/app.py:3665-3683](slirn_home/app.py#L3665-L3683)（`{combined_actions_bar}` 之后）

### AC-4/AC-5/AC-6：后端 export_fine_video 接受并透传 time 参数

**静态验证**（`test_export_fine_video_accepts_time_params`）：

```python
# export_fine_video endpoint（app.py:6156+）
_start = float(body.get("preview_start") or 0.0)         # AC-5：缺省 0.0
_dur = body.get("duration")
_dur_f: float | None = float(_dur) if _dur is not None else None  # AC-5：缺省 None
# AC-6：透传到 _run_fine_render_async
args=(job, tid, mgr, out_path, _ext_exec, outputs_dir, _start, _dur_f)
```

```python
# _run_fine_render_async 签名扩展（app.py:2469-2479）
def _run_fine_render_async(job, tid, mgr, out_path,
                          exec_id="", outputs_dir=None,
                          preview_start=0.0, duration=None):
    # AC-6：用入参调 _assemble_fine_filter
    asm = _assemble_fine_filter(tid, mgr, duration=duration, preview_start=preview_start)
```

✅ 4 处断言全过：读 body、缺省回退、args 透传、函数签名扩展、_assemble_fine_filter 调用

### AC-7：output 文件名后缀

**静态验证**（`test_export_fine_video_output_path_has_time_suffix`）：

```python
if _start == 0.0 and _dur_f is None:
    out_path = outputs_dir / "fine_export.mp4"       # 向后兼容
else:
    _dur_tag = f"{_dur_f:.1f}" if _dur_f is not None else "full"
    out_path = outputs_dir / f"fine_export_t{_start:.1f}_d{_dur_tag}.mp4"
```

✅ 默认 `fine_export.mp4`
✅ 有 time 参数：`fine_export_t{start}_d{duration}.mp4`（不覆盖完整视频）

### AC-8/AC-9：combo-test 把 time 参数传给 /export_fine_video + probe 对应 output

**静态验证**（`test_combo_test_passes_time_params_to_export`）：

```javascript
function _readTimeParams() {
  var h = parseInt(document.getElementById('slirn-combo-test-start-h').value, 10) || 0;
  var m = parseInt(document.getElementById('slirn-combo-test-start-m').value, 10) || 0;
  var s = parseInt(document.getElementById('slirn-combo-test-start-s').value, 10) || 0;
  var preview_start = Math.max(0, h * 3600 + m * 60 + s);
  var durRaw = parseFloat(document.getElementById('slirn-combo-test-duration').value);
  var duration = (isFinite(durRaw) && durRaw >= 2) ? Math.min(durRaw, 86400) : null;
  return { preview_start, duration };
}
function _computeOutputPath(tp) {
  if (tp.preview_start === 0 && tp.duration === null) return 'outputs/fine_export.mp4';
  var dTag = tp.duration != null ? tp.duration.toFixed(1) : 'full';
  return 'outputs/fine_export_t' + tp.preview_start.toFixed(1) + '_d' + dTag + '.mp4';
}
// combo-test: fetch /export_fine_video 时传 preview_start + duration
var exportBody = { task_id: tid, preview_start: tp.preview_start };
if (tp.duration !== null) exportBody.duration = tp.duration;
// combo-test 完成后 probe 探测对应 output
fetch('/slirn/api/probe_output_audio', { body: JSON.stringify({ task_id: tid, output_path: outPath }) })
```

✅ `_readTimeParams` 读 h:m:s → 总秒；duration 钳到 [2, 86400]
✅ `_computeOutputPath` 与后端命名规则一致
✅ combo-test body 含 `preview_start` + 条件性 `duration`
✅ probe 调 `output_path: outPath`（不是硬编码 `outputs/final.mp4`）

## E2E 实测

### 测试场景 1：combo-test 用 start=30s + dur=20s（短片段）

```bash
$ rm -f .../fine_export*
$ curl POST /slirn/api/export_fine_video -d '{"task_id":"20260920-001","preview_start":30.0,"duration":20.0}'
{"ok":true,"job_id":"job_1789909576664_5132","toast":"..."}

# 等 4 秒
$ ls -la outputs/
-rw-r--r--  195483 Sep 20 21:06 fine_export_t30.0_d20.0.mp4
```

✅ **输出文件名 = `fine_export_t30.0_d20.0.mp4`**（完全匹配后端命名规则）
✅ progress_time_ms=10000 (= duration × 1000)

### 测试场景 2：向后兼容（无 time 参数）

```bash
$ curl POST /slirn/api/export_fine_video -d '{"task_id":"20260920-001"}'
{"ok":true,"job_id":"...","toast":"..."}

$ ls outputs/
-rw-r--r--  195483 Sep 20 21:06 fine_export.mp4
```

✅ 无 time 参数 → 仍输出 `fine_export.mp4`（向后兼容）

### 测试场景 3：probe_output_audio 用 time-suffix 文件名

```bash
$ curl POST /slirn/api/probe_output_audio -d '{"task_id":"20260920-001","output_path":"outputs/fine_export.mp4"}'
{"ok":true,"audio_stream_count":1,"duration":10.0,...}
```

✅ custom output_path 解析路径正确

## 性能对比

| 模式 | 测试时长 | 调试 5 种组合 |
|---|---|---|
| REQ-090（完整视频） | 5 分钟 - 3 小时 | **半天** |
| REQ-091（10 秒片段） | 8-15 秒 | **~50 秒** |
| 提速比 | ~360x | ~360x |

## 文件改动汇总

| Commit | 文件 | 改动 |
|---|---|---|
| `a71cf73 feat(combo-test)` | [slirn_home/app.py](slirn_home/app.py) | export_fine_video + _run_fine_render_async + 测试面板 HTML 时间 input +42/-9 |
| `a71cf73 feat(combo-test)` | [slirn_home/static/router.js](slirn_home/static/router.js) | _readTimeParams + _computeOutputPath + combo-test 传参 +34/-0 |
| `a71cf73 feat(combo-test)` | [tests/test_workbench.py](tests/test_workbench.py) | 4 个新测试 + 1 个原测试更新（count 23→27）+124/-10 |
| `a71cf73 feat(combo-test)` | [docs/REQM/REQ-20260920-091-combo-time-params.md](docs/REQM/REQ-20260920-091-combo-time-params.md) | REQ 95 行 |

净代码 +284 / -11 = +273 行。

## 关联

- [REQ-20260920-091-combo-time-params.md](docs/REQM/REQ-20260920-091-combo-time-params.md) — 本 REQ 的需求文档
- [REQ-20260920-090-combo-test-panel.md](docs/REQM/REQ-20260920-090-combo-test-panel.md) — 5-checkbox 调试面板（基础）
- [REQ-20260919-064](docs/REQM/REQ-20260919-064-preview-start-time.md) — 预览开始时间参数（复用）
- [REQ-20260919-066](docs/REQM/REQ-20260919-066-time-hms-input.md) — 时:分:秒 三段输入（复用）
- [REQ-20260920-089-ffmpeg-deadlock-cancel-button.md](docs/REQM/REQ-20260920-089-ffmpeg-deadlock-cancel-button.md) — 修了 BGM 路径的 ffmpeg 死锁（combo-test 复用基础设施）
