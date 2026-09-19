# REQ-20260920-080 精剪·预览/导出最终视频 → 修复 BGM filter_complex label 拼接 bug

## 背景

用户在精剪面板点「生成预览」/「导出最终视频」，即便勾了「启用背景音乐」并上传了 mp3，输出视频**只有原说话人语音 + 视频原声轨，没有 BGM**。

排查路径：
- 后端 `_assemble_fine_filter` audio block 看似拼对了（amix inputs=2 + [bgm] 标签）
- ffmpeg 进程 stderr 末尾显示：`[AVFilterGraph @ ...] No such filter: ''` + `Error : Filter not found`
- 直接原因：bgm filter chain 拼成了 `[1:a]aloop=...,volume=0.40,[bgm]`，**`[bgm]` label 前面多了一个逗号** → ffmpeg 把 `,` 后的 `[bgm]` 当成 filter name（filter name 必须是字符串，不能以 `[` 开头） → 解析失败 → 整条 filter_complex 被拒绝

### 原代码片段（[slirn_home/app.py:2075-2086](slirn_home/app.py#L2075) 修复前）

```python
bgm_filters = [f"[{audio_idx}:a]aloop=loop=-1:size=2e9,volume={vol:.2f}"]
if fade_in > 0:
    bgm_filters.append(f"afade=t=in:st=0:d={fade_in:.2f}")
if fade_out > 0:
    bgm_filters.append(f"afade=t=out:st=0:d={fade_out:.2f}")
bgm_filters.append("[bgm]")
chain.append(",".join(bgm_filters))   # → "...,volume=0.40,[bgm]"  ← BUG
chain.append("[voice][bgm]amix=inputs=2:duration=first:normalize=0[aout]")
```

## 用户影响

| 现象 | 表现 |
|---|---|
| 预览（≤30s） | 直接返回 500/失败；用户看不到带 BGM 的预览 |
| 异步导出（1-3 小时） | ffmpeg 在解析阶段失败（≤5 秒），stderr 报 "No such filter: ''" → 立即 `state="failed"`，output_url 为空 |
| 没启用 BGM | 不触发此 bug（走 `else: chain.append("[voice]anull[aout]")`） |

**这次 BUG 的隐蔽性**：filter_complex 字符串能拼出来，ffmpeg 启动也不报错（只是语法解析阶段失败），所以 `cmd` 没崩，但 ffmpeg 子进程秒退。用户看到的现象就是「勾了 BGM 但没声音」或「导出失败」。

## 验收标准

| # | 描述 |
|---|---|
| AC-1 | 勾选 BGM + 上传 mp3 + 点预览 → 输出视频有 BGM 混音 |
| AC-2 | 勾选 BGM + 上传 mp3 + 点导出 → 最终视频有 BGM 混音 |
| AC-3 | 未勾 BGM → 不受影响（原 `[voice]anull[aout]` 路径） |
| AC-4 | filter_complex 中 `,[bgm]` 不再出现（label 前无逗号） |
| AC-5 | fade_in / fade_out 仍正常生效（filter chain 中间逗号仍合法） |
| AC-6 | 所有现有测试 + 1 个新测试全过（546 → 547+） |

## 修复方案

把 `list + ",".join` 改成直接拼字符串，**label 不进 join 列表**，最后才追加：

```python
bgm_chain = f"[{audio_idx}:a]aloop=loop=-1:size=2e9,volume={vol:.2f}"
if fade_in > 0:
    bgm_chain += f",afade=t=in:st=0:d={fade_in:.2f}"
if fade_out > 0:
    bgm_chain += f",afade=t=out:st=0:d={fade_out:.2f}"
bgm_chain += "[bgm]"   # label 紧接 filter chain 末尾，无逗号
chain.append(bgm_chain)
```

## 不做的事

- ❌ 不重构整条 audio chain（只改 BGM 这段；voice 段已正确）
- ❌ 不改 amix 参数（`duration=first:normalize=0` 是 REQ-20260919-061 已确认的策略）
- ❌ 不改 fc.audio 持久化格式
- ❌ 不改 fade_in/out 默认值

## 关联

- 上游：[REQ-20260919-061 精剪视频阶段](docs/REQM/)（audio 4 项的源头）
- 上游：[REQ-20260919-074 异步渲染](docs/REQM/)（导出异步化后此 bug 表现为「立即失败」）
- 上游：[REQ-20260920-077 inline 进度](docs/REQM/REQ-20260920-077-fine-export-inline-progress.md)（bug 在 inline 状态机里表现为「❌ 失败 · 重试」）
- 上游：[REQ-20260920-078 系统默认 BGM](docs/REQM/REQ-20260920-078-system-default-bgm.md)（选默认 BGM 后也会被此 bug 阻断）
- 上游：[REQ-20260920-079 超大图预缩](docs/REQM/REQ-20260920-079-prescale-oversized-images.md)（独立 REQ，但都在同次 5 阶段周期内提交）

## 失败案例（已记录）

| 字段 | 值 |
|---|---|
| 任务 ID | 20260918-022（用户实测） |
| fc.audio.enabled | True |
| 源 BGM | 上传的 mp3（1.7 MB） |
| 错误信号 | `state=failed`, `error="... [AVFilterGraph] No such filter: '' ... Error : Filter not found"` |
| 修复后 | filter_complex 含 `[bgm]` label 且无 `,` 前缀，ffmpeg 解析通过，amix 输出有声音 |