# REQ-20260917-023 — 长视频识别内存修复（ffmpeg 流式抽 16k 单声道）

## 需求原话

> ❌ Unable to allocate 2.68 GiB for an array with shape (359377956, 2) and data type float32 处理字幕生成时的这个异常

## 根因

任务 20260916-004（截取段 2.26 小时）字幕生成失败。上游链路
（`funclip/videoclipper.py video_recog`，不可改）：

1. `moviepy write_audiofile` 抽音轨 —— **保持源采样率 44.1kHz + 立体声**
2. `librosa.load(sr=16000)` —— soundfile 先整读 **float32 (N,2) 立体声大块**
   （8144s × 44100 × 2ch × 4B = **2.68GiB 单块连续分配** ← 炸点），
   再降混单声道、再重采样，峰值内存再翻倍

本机 commit 限额下长视频直接 `Unable to allocate`；5 小时上限视频此数组
达 5.7GiB，腾内存也救不了，必须改喂给上游的音频形态。

## 改动（slirn_home/asr_service.py，不动 funclip/）

- 新增 `_extract_mono_wav_16k(video, wav_path)`：`ffmpeg -vn -ac 1 -ar 16000
  -c:a pcm_s16le` **流式**抽 16k 单声道 wav（内存几十 MB，产物落系统临时目录）
- `_run_recognition` 重写音频获取：抽 16k 单声道 → `librosa.load`（直接得
  `(N,)` float32，**立体声大块分配消失**）→ 直调上游 `VideoClipper.recog`
  （同一入口：seaco-paraformer / sd_switch=no / 热词 / 断句完全同源；
  worker 只消费 `state["sentences"]`，绕过 video_recog 安全）
- 内存需求：**约 0.7GB/小时**（f32 波形 + recog 内 float64 转换）
- 内存不足时 job 报错附带可操作提示（关闭其他应用/上游服务后重试）
- 守卫：抽音失败/超时 → 清晰 RuntimeError（含 ffmpeg stderr 尾部）

## 验证

- `pytest tests/ -q` → **203 passed**（新增 2：16k 单声道 PCM16 真实抽轨
  校验[stdlib wave]；非视频输入清晰报错）；ruff clean
- 服务已带修复运行；任务 004 可直接重跑字幕生成（真长视频 E2E 由用户
  触发，识别约需较长时间）

## 关联

- 提交：`feat(asr): 长视频识别内存修复——ffmpeg 流式抽 16k 单声道 (REQ-20260917-023)`
- 内存环境约束：REQ-20260916-021（WinError 1455 处置）+ 记忆
  slirn-compose-memory-constraints（长任务前查 commit，不足先停 7860）
