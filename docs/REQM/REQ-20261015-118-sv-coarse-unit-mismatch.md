# REQ-20261015-118 — 短视频粗剪「segments 为空或全部无效」单位错位修复

> 日期：2026-10-15
> 状态：✅ 已完成（单条 clip E2E 验证通过）
> 类型：bug fix

## 1. 现象

用户反馈「粗剪合成失败」。项目 `20260924-001 / sv_20260930_234159_86a2fa`
（101 分钟源视频，Stage 1-4 均 done）点击「🎬 合成粗剪」后，**全部 4 条 clip**
报同一错误：

```
moviepy subclip/concat 失败: segments 为空或全部无效
```

## 2. 排查

| 检查项 | 结果 |
|---|---|
| 源视频存在 | ✅ 345MB / duration 6099.033s |
| highlights.json 时间戳 | ✅ hl#1 2168020→2357115ms，全部在时长内 |
| 上游阶段状态 | ✅ stage1-4 全 done |
| moviepy 独立复现 | ❌ `hl#1 segments kept after clamp: 0 / 15` |

夹取（clamp）循环本身把所有区间丢光了。

## 3. 根因

`slirn_home/short_video_coarse.py` `_coarse_one` 内（修复前 L244）：

```python
e_sec = min(vid_dur, e_ms) / 1000.0   # ← BUG
```

- `vid_dur = video.duration` 单位是**秒**（6099.033）
- `e_ms` 是字幕时间戳，单位是**毫秒**（2171820）
- `min()` 跨单位比较：秒赢 → `2171820` 被夹成 `6099.033`
- 再除 1000 → `e_sec = 6.099s`，而 `s_sec = 2168.02s`
- `e_sec <= s_sec` → **该区间被丢**；15 条字幕行全丢 → `raise ValueError`

规律：任何起点晚于 `vid_dur × 1000` 毫秒（本机 ≈6.1 秒）的区间必死。
即**只有片头 6 秒内的字幕能活**——长课程视频必然全军覆没。

同一错误写法在文件里出现**两次**（`_coarse_one` L244 + 连续预览路径 L762），
是 REQ-20261013-108/110（Stage 4 改版）重构时引入，与「粗剪 10-13 前正常、
之后全挂」的时间线吻合。

## 4. 修复

提取模块级纯函数（两处调用点共用，消除重复即消除二次踩坑面）：

```python
def _clamp_segments_ms(pairs_ms, vid_dur_sec: float) -> list[tuple[float, float]]:
    out = []
    for s_ms, e_ms in pairs_ms:
        s_sec = max(0, s_ms) / 1000.0
        e_sec = min(vid_dur_sec, e_ms / 1000.0)   # ← 先统一单位再 min
        if e_sec <= s_sec:
            continue
        out.append((s_sec, e_sec))
    return out
```

两个调用点改为 `seg_list = _clamp_segments_ms(segments, vid_dur)`，
错误信息（「segments 为空或全部无效」/「所有区间都被丢弃」）保持不变。

## 5. 测试

`tests/test_short_video_stage5_coarse_port.py` 新增 2 个：

1. `test_clamp_segments_ms_unit_regression` — 用**本次真实故障值**入测：
   `(2168020, 2171820) × vid_dur 6099.033` 必须存活为 `(2168.02, 2171.82)`；
   另覆盖尾部越界截断 / 超时长起点丢弃 / 负起点钳 0。
2. `test_coarse_clamp_no_unit_mismatch_left` — 源码守卫：旧写法字面量
   不得回归；`_clamp_segments_ms(` 出现 ≥3 次（定义 + 两调用点）。

全量 pytest：**1271 passed**（1269 + 2）。

## 6. E2E 验证（真实项目）

重启 7861 后，经 `/slirn/api/short_video_stage5_export`（`hl_index=1` 单条）：

- job：running → **done**（progress 100，≈30s）
- 产物：`stage4/coarse_01.mp4`（3.2MB）+ `coarse_01.srt`
- ffprobe：video+audio 双流，duration 29.76s ✅
- project.json：clip 1 `done dur=29.76`，无 error
  （clip 2-4 的 error 字段是修复前旧记录，重跑全量即覆盖）

## 7. 遗留

- 用户需在 UI 点一次全量「🎬 合成粗剪」补齐 clip 2-4。
- 同期未提交的 REQ-116（自动跳下一阶段开关）/ REQ-117（空列表提示分流）
  与本修复无文件交集，待用户验收后另行提交。
