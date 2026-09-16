# REQ-20260916-021 — 按有效字幕时间戳完成初检与粗剪合成

## 需求原话

> 使用这个验证过的方法，完成初检、合成阶段的工作。从原始视频中根据所提供的字幕文件的时间戳的时间段截取视频，并拼接合成粗剪视频

> （澄清）语音识别文字错误不是初检，是粗剪合成阶段。这里边也不涉及到两个候选字幕，所用的字幕就是在粗剪合成第一步中已经从上一阶段获取过来的有效的字幕信息……你只需要完成根据字幕时间戳的起始跟截止的值从原始视频中截取出来所需内容并拼接。

## 口径

- **字幕** = 粗剪合成面板同源「有效字幕」（REQ-019 预览口径：revision all_decided →
  cutlist 执行口径 → effective_keep_units → fine 未撤销热词替换 new_text），
  文字准确性由人工检查，本步只用时间戳起止值
- **源视频** = 任务工作视频（截取段，44.11 分钟，= 全管线一致时间轴 = SRT 时间轴）
- **方法** = REQ-20260916-020 已验证的 `compose_from_srt`（纯程序，无 LLM）
- **产物落 `work/`**（任务 outputs/ 只读，不覆盖已有 rough_compose.mp4）

## 执行（work/REQ-20260916-021-roughcut-by-srt/_run.py）

任务 `20260915-001`（status=SUBTITLE_REVIEWED）：

1. **有效字幕生成**：与 `compose_rough_preview_subs` 端点逐行同源 → 1681 行
   （74883 chars，热词替换 0 条），落 `effective_subs.srt` —— 与 REQ-019 面板
   预览行数一致（口径吻合）
2. **初检**：工作视频 44.11 分钟；净保留 34.9 分钟（去间隙 9.2 分钟）；
   重叠 0（相邻无缝自动合并）；越界 0；条目数=保留行数 1681 ✅
3. **合成**：`compose_from_srt(src=工作视频, effective_subs.srt, rough_cut_from_srt.mp4)`
   — 1681 条 → 相邻合并 997 段 → subclip 切片拼接 + 随片字幕平移
4. **验证**（全过）：
   - 成片时长 **2095.08s vs 预期 2094.07s（差 1.01s）** ✅
   - 随片字幕 1681 条 ≥ 合并段数 997 ✅
   - dropped = 0（无越界丢弃）✅
   - ffprobe 复核：时长 2095.08s、音轨在 ✅

## 产物（全部在 work/，gitignored）

| 文件 | 说明 |
|---|---|
| `rough_cut_from_srt.mp4` | 粗剪成片，2095.08s（34.92 分钟）/ 99.4 MB |
| `rough_cut_from_srt.srt` | 随片字幕（时间轴已平移到成片 0 点），1681 条 |
| `effective_subs.srt` | 送入合成的有效字幕（工作视频时间轴） |
| `_run.py` / `_run.log` | 执行脚本 / 全程日志 |

任务 `20260915-001/outputs/` 未做任何写入（rough_compose.mp4 保持原样）。

## 异常记录：WinError 1455（页面文件太小）

首次合成 18s 处失败：moviepy `VideoFileClip` 内 `sp.Popen` 启动 ffmpeg 时
`OSError: [WinError 1455] 页面文件太小` —— **Windows 提交内存（commit）耗尽**
（物理剩 9.6GB，但 commit 限额 47.7GB 只剩 1.6GB）。

| commit 大户 | 占用 | 处置 |
|---|---|---|
| VSCode（31 进程） | 10.4GB | 不动 |
| WSL2/Docker（12 个容器：网关/ASR/TTS/DB…） | 7.9GB | **不能动** |
| 上游 7860 服务 | 4.9GB | **合成期间暂停，完成后恢复** |
| slirn 7862 服务 | 1.7GB | 保持运行 |

上游服务 FunASR 模型是启动时急切加载，**重启不释放**（实测冷启动 40s 内又
commit 4.9GB）；合成是进程内直调 moviepy，不依赖 7860 服务 → 暂停后 commit
空闲 1.6GB→6.26GB，重跑一次成功。合成完成后 7860 已重新拉起。

## 其他发现（待人工决定，未处理）

- 任务 outputs/ 里有 `rough_compose_no0.mp4`（104MB，9-16 17:58）——早前服务端
  合成并发计数残留的未 rename 临时文件，异常已发现，按任务数据只读原则未动。

## 关联

- 方法前置：REQ-20260916-020（`compose_from_srt`，201 pytest + 逐帧拼接验证）
- 字幕口径：REQ-20260916-019（有效字幕预览端点）
- 本次为纯执行（无代码改动），故无 feat 提交
