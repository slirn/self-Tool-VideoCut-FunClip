# REQ-20260919-069 精剪预览·字幕跟随 preview_start 平移

## Context

精剪视频面板的「预览开始时间」（REQ-20260919-064/066）生效后，视频流确实
从源视频的指定时间点开始，但字幕仍从 SRT 第 1 条开始显示（与画面内容
错位）。用户：「预览开始时间设置后，视频能够从指定的时间开始，但是字幕
没有从指定的时间开始，而是从最开头开始的」。

## 根因

`_run_fine_render` 的 ffmpeg 命令用 `-ss preview_start -i video.mp4` 做**输入
端 seek**（output 端 PTS 从 0 起算，但内容对应源视频 N 秒后）。`subtitles`
滤镜按输出 PTS 匹配 SRT 绝对时间 → 一条 source-time [N, N+5] 的字幕
在输出时间 [N, N+5] 显示，但 [0, N] 这段输出帧对应的内容是 source-time
[N, 2N]，于是字幕过早出现并与画面错位。

## 验收标准

- AC-1：`preview_start = 0` 时，subtitles 滤镜直接用原 SRT 文件（不写 tmp）
- AC-2：`preview_start > 0` 时，subtitles 滤镜用临时偏移后的 SRT（路径在
  cmd 里可见）；预览偏移命令：-/preview_start/ s>e 全减 N；e<=s 整条丢弃；
  s<e<N 裁到 [0, e-N]
- AC-3：原 SRT 中所有 `end_ms <= preview_start*1000` 的条目不出现在 tmp SRT
- AC-4：tmp SRT 时间戳格式 `HH:MM:SS,mmm`（与原一致）
- AC-5：渲染成功后 tmp SRT 文件被删除（finally 兜底）
- AC-6：解析原 SRT 失败时（坏文件），回退到原 SRT（不报错，渲染仍可继续）

## 方案

在 `_run_fine_render` 字幕 burn 之前：

1. 计算 `preview_offset_ms = round(preview_start * 1000)`
2. `offset > 0`：用 `compose_service.parse_srt` + 自写偏移逻辑 → 写 tmp
   `slirn_fine_srt_<rand>.srt` → subtitles 滤镜用 tmp 路径
3. `offset == 0`：直接用原 SRT（无性能开销、无 tmp 文件）
4. `finally`：tmp 文件 `unlink(missing_ok=True)`（成功失败都删）

## 关键文件

- [slirn_home/app.py:1918-1965](slirn_home/app.py#L1918) — `_run_fine_render`
  字幕段加 `preview_offset_ms` + 偏移生成 tmp SRT
- [slirn_home/app.py:2047-2058](slirn_home/app.py#L2047) — `finally` 兜底删 tmp
- [tests/test_workbench.py:4283-4355](tests/test_workbench.py#L4283) — 新增
  `test_run_fine_render_shifts_srt_for_preview_start`（验证 AC-1/2/3/4）
- [tests/test_workbench.py:4357-4400](tests/test_workbench.py#L4357) — 新增
  `test_run_fine_render_zero_preview_start_uses_original_srt`（验证 AC-1）

## 不做的事

- ❌ 不改 `subtitles` 滤镜行为（ffmpeg 滤镜本身无法感知源时间偏移）
- ❌ 不改 `-ss` 位置（保留输入端 seek 的速度优势）
- ❌ 不在 commit 里写 tmp SRT（自动清理 + 不落仓库根）

## 验证

```
pytest tests/test_workbench.py -k preview_start -v
pytest tests/ -q                    # 510 通过
```