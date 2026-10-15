# REQ-20261016-125 — Stage 2 提取字幕：执行时间/进度显示 + 提取崩溃根因修复

> 状态：✅ 已完成（2026-10-16）
> 关联：[REQ-20261013-111](REQ-20261013-111-sv-stage34-progress-card.md)（进度卡模式来源）
> 关联：[REQ-20261015-122](REQ-20261015-122-sv-stage2-remove-model-lang-and-hotwords-comma.md)

## 1. 需求（用户原话要点）

1. 「在提取字幕的时候需要有执行时间和进度情况」
2. 「另外如图所示还有执行异常，查一下什么原因」（截图 23.png：
   `funasr 退出码 1 · numpy ... Unable to allocate 2.00 GiB ... (268967223, 2) float32`）

## 2. 崩溃根因（实证）

真实项目 `sv_20260930_234159_86a2fa`（1.7h 立体声 44.1kHz 视频）连续 4 次
失败（10-15 23:21 起三次退出码 3221225477，10-16 07:03 退出码 1），同一根因：

```
extract_subtitle.py:142 → VideoClipper.video_recog
  videoclipper.py:248  video.audio.write_audiofile(wav)   ← moviepy 默认 44.1kHz 立体声
  videoclipper.py:249  librosa.load(wav, sr=16000)        ← 整段读入 float32
    → np.empty((268967223, 2), float32) = 2.0 GiB 一次性分配
    → 系统提交内存不足（当时空闲仅 ~5.7/50 GB）→ _ArrayMemoryError（退出码 1）
    → 内存临界时原生层直接 0xC0000005（退出码 3221225477，截图 22.png）
```

两类退出码是同一病：长视频整段音频的数 GB 级分配 + 双服务（7860/7861）
常驻占内存。该视频 10-04 曾成功过（内存宽裕时），佐证为环境性内存耗尽。

## 3. 修复

| 位置 | 改动 |
|---|---|
| `slirn-standalone skill/.../extract_subtitle.py`（commit `47b6c6a`） | 新增 `recog_video()`：ffmpeg 直抽 **16kHz 单声道** wav（内存/体积 ≈1/11）→ `librosa.load` → 直接 `clipper.recog`；单文件与批量路径均切换，不再走 `video_recog` 的 moviepy 立体声路径。无音频流 → `RuntimeError("No audio information found ...")`（批量逐文件 catch 不中断） |
| `slirn_home/short_video_extract.py` | `_diagnose_extract_error()`：stderr 含 `Unable to allocate/_ArrayMemoryError/MemoryError` 或退出码 3221225477 → error 追加可行动中文提示（关程序/重试）；Stage 2 与 Stage 5 clip ASR 两条失败路径接入 |
| `slirn` submodule | ref bump → `47b6c6a` |

识别结果与上游路径一致（同一 `clipper.recog`）；60s 立体声真实模型冒烟通过
（28 行 SRT，rtf 0.078 → 全片 ASR 约 8 分钟）。

## 4. 进度 + 执行时间（纯前端，仿 REQ-111）

| 文件 | 改动 |
|---|---|
| `short_video_ui.py::_stage2_extract` | 按钮加 `id="slirn-sv-stage2-run-btn"`；新增 `#slirn-sv-stage2-status` 状态卡（复用 `.slirn-rc-status-card`/`.slirn-progress`/`.slirn-progress-bar`，0 新 CSS） |
| `short_video.js::stage2Run(btn)` | 重写：防重入双保险（`window._slirnSvStage2Running` + `btn.disabled`）；**真实已耗时**（1s 节奏，复用 `_fmtElapsed`）；阶段文字（加载模型 → ffmpeg 提取音频 → 语音识别中）；进度条按已耗时爬升、90% 封顶等响应；完成绿 100% + `N 字 · 总耗时`，失败红 100% + 后端 error；路由 `sv-stage2-run` 传 btn |

## 5. 测试（+4，全量 1301 passed）

- `test_stage2_status_card_dom_exists` — 状态卡 + 按钮 id
- `test_stage2_js_progress_elapsed_and_guard` — 防重入/已耗时/阶段文字/终态/路由传 btn
- `test_diagnose_extract_error_oom_and_native_crash` — 两类崩溃翻译
- `test_extract_subtitle_script_bypasses_moviepy_video_recog` — skill 脚本不再走 `video_recog`、用 `-ac 1 -ar 16000`（submodule ref bump 后通过）

## 6. E2E

重启 7861 后在真实失败项目 `sv_20260930_234159_86a2fa` 重新提取：单声道
路径全程内存峰值 ≈390MB（原 2GiB），ASR 约 8-10 分钟，预期 status=done。

## 7. 已知边界

- 进度条为前端按时长估算（识别耗时随视频长度浮动），非后端真实百分比；
  完成时一次性跳 100%（与 Stage 3/4 同款取舍）。
- 阻塞式 POST 仍是现状（与历次 Stage 2 相同）；期间 uvicorn 单 worker
  不响应其他请求，属既有架构边界。
